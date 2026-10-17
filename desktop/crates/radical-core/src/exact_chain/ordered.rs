//! Dates ordered inside one pair of Boundaries as blocks, one after another:
//! a small chain of its own over cuts at each block's oldest date.

use std::collections::HashMap;

use crate::Error;
use crate::exact_chain::messages::{
    BoundaryDraws, ChainProducts, EndFactorMessages, draw_dates, draw_in, exp_rescaled,
    finite_peak, messages_with_end_factor,
};
use crate::exact_chain::prior::{boundary_pair_term, logaddexp};
use crate::exact_chain::summary::straddling;
use crate::exact_chain::{BOUNDARY_TIE_WEIGHT, Chain};
use crate::matrix::Matrix;
use crate::numeric::{py_max, python_sum, sum};
use crate::rng::Generator;

/// F[y, o] = integral of one date's likelihood over [grid[y], grid[o]],
/// TRAPEZOIDALLY -- half weight on each endpoint cell.
///
/// The half-weights are the difference between right and wrong here, because
/// of the (span)^(-K) factor this gets multiplied by. A cumulative difference
/// alone sums cells y THROUGH o inclusive, so an interval spanning k*step
/// receives (k+1) cells of mass while the span prior uses k*step -- too large
/// by a factor of two PER DATE at the smallest span, 2^K over a group, landing
/// squarely on the near-coincident configurations the prior already favours.
///
/// Against a continuum reference with no quadrature at all (three Gaussian
/// likelihoods, so the integrals are exact error functions; truth 181.5 years):
///
/// ```text
/// grid step      cum[o] - cum[y-1]      trapezoidal
///    10.0                   126.4            186.3
///     5.0                   141.5            183.8
///     2.5                   155.2            182.6
///     1.0                   167.8            181.9
/// ```
///
/// The plain difference is biased low by 30% at a ten-year step and converges
/// only as the step vanishes; the trapezoidal form is within 3% at ten years
/// and makes the answer grid-independent.
pub fn interval_integral(cumulative: &[f64], density: &[f64], step: f64) -> Matrix {
    let m = cumulative.len();
    let half: Vec<f64> = density.iter().map(|d| 0.5 * step * d).collect();
    // (C[o] - h[o]) - (C[y-1] + h[y]): each end's half folded into its own
    // vector first, so the matrix is one subtraction rather than four passes.
    let upper: Vec<f64> = (0..m).map(|o| cumulative[o] - half[o]).collect();
    let lower: Vec<f64> = (0..m)
        .map(|y| (if y == 0 { 0.0 } else { cumulative[y - 1] }) + half[y])
        .collect();
    Matrix::from_fn(m, m, |y, o| (upper[o] - lower[y]).max(0.0))
}

/// [`interval_integral`] in the log: an empty interval is -inf.
pub fn log_interval_integral(cumulative: &[f64], density: &[f64], step: f64) -> Matrix {
    let mut f = interval_integral(cumulative, density, step);
    f.map_inplace(f64::ln);
    f
}

/// One date's factor in its group's transfer matrix, in the log, indexed
/// [younger, older]: its interval integral F where the boundaries are apart,
/// and its likelihood where they share a cell.
///
/// The second is the continuum's limit. A group of K dates enters as
/// span^(-K) prod F_j, and F_j / span tends to L_j(b) as the span closes, so
/// two boundaries meeting at b leave the dates' likelihoods at b -- times the
/// tie's own weight, which the pair term carries. Kept as one matrix per date
/// so a date's own factor can be divided back out of the transfer (the date
/// marginals) on and off the diagonal alike -- which needs it formed by this
/// one function both times, so a cell that is -inf in the factor is -inf in
/// the transfer too.
pub fn log_date_factor(cumulative: &[f64], density: &[f64], step: f64) -> Matrix {
    let mut f = log_interval_integral(cumulative, density, step);
    let diagonal: Vec<f64> = density.iter().map(|d| d.ln()).collect();
    f.set_diagonal(&diagonal);
    f
}

/// log of the sum of exp(log_weights) over the rows of a (rows x M) block,
/// one value per column: -inf where all are. The rows are added one after
/// another, as NumPy reduces the outer axis.
fn log_sum_columns(block: &[Vec<f64>]) -> Vec<f64> {
    let m = block[0].len();
    (0..m)
        .map(|c| {
            let peak = block.iter().map(|r| r[c]).fold(f64::NEG_INFINITY, f64::max);
            if !peak.is_finite() {
                return f64::NEG_INFINITY;
            }
            let mut s = 0.0;
            for r in block {
                s += (r[c] - peak).exp();
            }
            s.ln() + peak
        })
        .collect()
}

/// log of the sum of exp(row) along one row, pairwise as NumPy reduces a
/// contiguous axis: -inf where all are.
fn log_sum_row(row: &[f64]) -> f64 {
    let peak = row.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if !peak.is_finite() {
        return f64::NEG_INFINITY;
    }
    let exps: Vec<f64> = row.iter().map(|v| (v - peak).exp()).collect();
    sum(&exps).ln() + peak
}

/// Dates in BLOCKS, oldest first, between an older end and a younger end on
/// one grid: Phases in a row with no Boundary between them, inside one pair of
/// Boundaries or beyond an end one. The dates of a block are unordered and
/// every one is older than every date of the next block.
///
/// Their integral over every placement is cut at v_i, the oldest date of block
/// i for i >= 2. Given the cuts the blocks are independent, so the group is a
/// small chain over the nodes (older end, v_2, ..., v_n, younger end), one
/// transfer per block, indexed [younger, older] as everywhere here:
///
/// ```text
/// block 1:    prod_m F_m(v_2, S)
/// block i>1:  T_i(l, u) = sum_k L_k(u) dt prod_(m != k) F_m(l, u)
/// ```
///
/// with F the trapezoidal interval integral, l the next cut or the younger
/// end, and T_i the derivative in u of block i's prod F: its dates in [l, u]
/// with the oldest AT u. Two neighbouring nodes in one cell count half, as two
/// Boundaries do -- which leaves only a block of one date there, since a
/// block's F over no width is nothing.
///
/// This is the trapezoid rule for the continuum integral, cut by cut, and it
/// converges as the step does. Following the sampler's grid rule instead --
/// half per tied ordering edge -- would need every block's count of dates at
/// each cell as state, and the cheaper imitations of it converge worse.
///
/// The chain's products are the chain solver's own ([`ChainProducts`]), so the
/// group's weight over its ends, its cuts' marginals given everything outside
/// it, and their draws come from the code that does the same for Boundaries.
/// The two ends in one cell -- every date there -- is the group's tie, which
/// the caller weighs ([`ordered_log_transfer`]) and hands back to
/// [`OrderedGroup::marginals`].
pub struct OrderedGroup {
    pub blocks: Vec<Vec<String>>,
    dt: f64,
    m: usize,
    /// One log transfer per block, the first block's product of F and every
    /// later block's T.
    log_links: Vec<Matrix>,
    products: Option<ChainProducts>,
    link_scale: f64,
    /// The group's log weight over its two ends, [younger end, older end].
    pub log_inner: Matrix,
    messages: Option<EndFactorMessages>,
    log_total: f64,
    log_tie: Option<Vec<f64>>,
}

impl OrderedGroup {
    pub fn new(
        blocks: &[Vec<String>],
        grid: &[f64],
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
    ) -> Self {
        let dt = grid[1] - grid[0];
        let m = grid.len();
        let log_f = |d: &String| log_interval_integral(&cumulative[d], &likelihood[d], dt);
        let mut log_links = Vec::new();
        // The first block: prod F.
        let mut first = Matrix::zeros(m, m);
        for date_id in &blocks[0] {
            first.zip_inplace(&log_f(date_id), |t, f| t + f);
        }
        log_links.push(first);
        // Every later block: log T, the derivative of its prod F in u, a half
        // where l = u (a block of one date only). The product rule forwards:
        // r the derivative so far, p the product.
        for block in &blocks[1..] {
            let mut p = Matrix::zeros(m, m);
            let mut out = Matrix::filled(m, m, f64::NEG_INFINITY);
            for date_id in block {
                let lf = log_f(date_id);
                let top = log_top(dt, &likelihood[date_id]);
                for l in 0..m {
                    let (pr, or, fr) = (p.row(l).to_vec(), out.row_mut(l), lf.row(l));
                    for u in 0..m {
                        or[u] = logaddexp(or[u] + fr[u], pr[u] + top[u]);
                    }
                }
                p.zip_inplace(&lf, |a, b| a + b);
            }
            let tie = BOUNDARY_TIE_WEIGHT.ln();
            for l in 0..m {
                for u in 0..l {
                    out.set(l, u, f64::NEG_INFINITY);
                }
                let d = out.get(l, l) + tie;
                out.set(l, l, d);
            }
            log_links.push(out);
        }
        let mut ids = vec!["_older".to_owned()];
        ids.extend((2..=blocks.len()).map(|i| format!("_cut{i}")));
        ids.push("_younger".to_owned());
        let chain = Chain::new(ids.clone(), vec![Vec::new(); blocks.len()], Vec::new());
        let allowed: HashMap<String, Vec<bool>> =
            ids.into_iter().map(|b| (b, vec![true; m])).collect();
        let products = ChainProducts::new(&chain, &allowed, &log_links);
        let link_scale = python_sum(log_links.iter().map(|l| finite_peak(l.data())));
        let offset = products.log_scale + link_scale;
        let log_inner = products.end_to_end().transpose().map(|v| v.ln() + offset);
        Self {
            blocks: blocks.to_vec(),
            dt,
            m,
            log_links,
            products: Some(products),
            link_scale,
            log_inner,
            messages: None,
            log_total: f64::NEG_INFINITY,
            log_tie: None,
        }
    }

    /// Row slices of [l, u] small enough that a block's per-date terms for
    /// one of them fit in about 64 MB.
    fn chunks(&self, size: usize) -> Vec<std::ops::Range<usize>> {
        let step = ((64e6 / (16.0 * size.max(1) as f64 * self.m as f64)) as usize).max(1);
        (0..self.m)
            .step_by(step)
            .map(|start| start..(start + step).min(self.m))
            .collect()
    }

    /// Fold in `log_end`, [younger end, older end], the log weight of
    /// everything outside the group -- zero where the ends share a cell -- and
    /// `log_tie`, by cell, the whole weight of the ends and every date in that
    /// one cell. [`Self::marginals`] and [`Self::draw`] read the result.
    /// Consumes the products.
    pub fn solve(&mut self, log_end: &Matrix, log_tie: Option<Vec<f64>>) {
        let peak = finite_peak(log_end.data());
        let end = Matrix::from_rows(self.m, self.m, exp_rescaled(log_end.data()));
        let products = self
            .products
            .take()
            .expect("an ordered group is solved once");
        let messages = messages_with_end_factor(products, &end);
        self.log_total = messages.log_mass + peak + self.link_scale;
        self.messages = Some(messages);
        self.log_tie = log_tie;
    }

    /// {date id: unnormalised marginal}, given [`Self::solve`]'s weights: the
    /// group's placements with its ends apart, each date's normalised to their
    /// total, and the tie's mass in its cell.
    pub fn marginals(
        &self,
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
    ) -> Vec<(String, Vec<f64>)> {
        let messages = self.messages.as_ref().expect("solved");
        let (m, dt) = (self.m, self.dt);
        let mut out: Vec<(String, Vec<f64>)> = Vec::new();
        for (index, block) in self.blocks.iter().enumerate() {
            let mut log_q = messages.pair_weight(index); // [younger, older]
            log_q.map_inplace(f64::ln);
            let log_f: Vec<Matrix> = block
                .iter()
                .map(|d| log_interval_integral(&cumulative[d], &likelihood[d], dt))
                .collect();
            if index == 0 {
                log_q.zip_inplace(&self.log_links[0], |a, b| a + b);
                for (date_id, lf) in block.iter().zip(&log_f) {
                    let mut w = log_q.zip_map(lf, |a, b| {
                        let v = a - b;
                        if v.is_nan() { f64::NEG_INFINITY } else { v }
                    });
                    for i in 0..m {
                        w.set(i, i, f64::NEG_INFINITY);
                    }
                    let w = Matrix::from_rows(m, m, exp_rescaled(w.data()));
                    let (rows, cols) = (w.row_sums(), w.column_sums());
                    let s = straddling(&rows, &cols);
                    let density = likelihood[date_id]
                        .iter()
                        .enumerate()
                        .map(|(t, l)| l * (s[t] - 0.5 * (rows[t] + cols[t])).max(0.0))
                        .collect();
                    out.push((date_id.clone(), density));
                }
                continue;
            }
            // Per date, as log sums: the date at the block's oldest cell u (a
            // mass there, by u), and below u under its own trapezoid with
            // another date at u (by row l and by column u, for straddling).
            let k = block.len();
            let mut top = vec![vec![f64::NEG_INFINITY; m]; k];
            let mut rows_v = vec![vec![f64::NEG_INFINITY; m]; k];
            let mut cols_v = vec![vec![f64::NEG_INFINITY; m]; k];
            let tops: Vec<Vec<f64>> = block.iter().map(|d| log_top(dt, &likelihood[d])).collect();
            for rows in self.chunks(k) {
                for (j, others, rest) in leave_one_out(&log_f, &tops, rows.clone()) {
                    let mut at_top: Vec<Vec<f64>> = Vec::with_capacity(rows.len());
                    let mut v: Vec<Vec<f64>> = Vec::with_capacity(rows.len());
                    for (r, l) in rows.clone().enumerate() {
                        let q = log_q.row(l);
                        let mut a_row: Vec<f64> =
                            (0..m).map(|u| q[u] + tops[j][u] + others[r][u]).collect();
                        let mut v_row: Vec<f64> = (0..m).map(|u| q[u] + rest[r][u]).collect();
                        for u in 0..l {
                            a_row[u] = f64::NEG_INFINITY;
                            v_row[u] = f64::NEG_INFINITY;
                        }
                        if k == 1 {
                            a_row[l] += BOUNDARY_TIE_WEIGHT.ln();
                        } else {
                            a_row[l] = f64::NEG_INFINITY;
                        }
                        v_row[l] = f64::NEG_INFINITY;
                        rows_v[j][l] = log_sum_row(&v_row);
                        at_top.push(a_row);
                        v.push(v_row);
                    }
                    let chunk_top = log_sum_columns(&at_top);
                    let chunk_cols = log_sum_columns(&v);
                    for u in 0..m {
                        top[j][u] = logaddexp(top[j][u], chunk_top[u]);
                        cols_v[j][u] = logaddexp(cols_v[j][u], chunk_cols[u]);
                    }
                }
            }
            for (j, date_id) in block.iter().enumerate() {
                let peak = finite_peak(&top[j])
                    .max(finite_peak(&rows_v[j]))
                    .max(finite_peak(&cols_v[j]));
                let rows_j: Vec<f64> = rows_v[j].iter().map(|v| (v - peak).exp()).collect();
                let cols_j: Vec<f64> = cols_v[j].iter().map(|v| (v - peak).exp()).collect();
                let s = straddling(&rows_j, &cols_j);
                let density = (0..m)
                    .map(|t| {
                        let below = s[t] - 0.5 * (rows_j[t] + cols_j[t]);
                        likelihood[date_id][t] * below.max(0.0) + (top[j][t] - peak).exp() / dt
                    })
                    .collect();
                out.push((date_id.clone(), density));
            }
        }
        // Each date's density sums, times the step, to the weight of every
        // placement with the ends apart; the tie adds its own in one cell.
        let tie_peak = self
            .log_tie
            .as_ref()
            .map_or(f64::NEG_INFINITY, |t| finite_peak(t));
        let reference = py_max(self.log_total, tie_peak);
        let apart = if self.log_total.is_finite() {
            (self.log_total - reference).exp()
        } else {
            0.0
        };
        let tie: Vec<f64> = match &self.log_tie {
            Some(t) => t.iter().map(|v| (v - reference).exp() / dt).collect(),
            None => vec![0.0; m],
        };
        for (_, density) in &mut out {
            let mass = sum(density) * dt;
            for (d, t) in density.iter_mut().zip(&tie) {
                *d = if mass > 0.0 {
                    *d * (apart / mass)
                } else {
                    0.0 * *d
                } + t;
            }
        }
        out
    }

    /// A grid index per date per draw, given the group's two ends drawn:
    /// every date in the ends' cell where they share one.
    pub fn draw(
        &self,
        rng: &mut Generator,
        older: &[usize],
        younger: &[usize],
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
    ) -> Result<HashMap<String, Vec<usize>>, Error> {
        let apart: Vec<usize> = (0..older.len())
            .filter(|&k| older[k] != younger[k])
            .collect();
        let mut out: HashMap<String, Vec<usize>> = self
            .blocks
            .iter()
            .flatten()
            .map(|d| (d.clone(), older.to_vec()))
            .collect();
        if apart.is_empty() {
            return Ok(out);
        }
        let messages = self.messages.as_ref().expect("solved");
        let cuts: BoundaryDraws = messages.draw_boundaries(
            &self.log_links,
            rng,
            apart.len(),
            Some((
                apart.iter().map(|&k| older[k]).collect(),
                apart.iter().map(|&k| younger[k]).collect(),
            )),
        )?;
        let mut drawn = draw_dates(
            rng,
            std::slice::from_ref(&self.blocks[0]),
            cumulative,
            likelihood,
            &cuts[..2],
            self.dt,
        );
        for index in 1..self.blocks.len() {
            drawn.extend(self.draw_block(
                rng,
                &self.blocks[index],
                &cuts[index],
                &cuts[index + 1],
                cumulative,
                likelihood,
            ));
        }
        for (date_id, values) in drawn {
            let column = out.get_mut(&date_id).expect("a date of the group");
            for (&k, v) in apart.iter().zip(values) {
                column[k] = v;
            }
        }
        Ok(out)
    }

    /// A block's dates given its oldest cell `top` and its lower end `low`:
    /// which date sits at the top, by its term in T, then the rest from
    /// [low, top] under their trapezoids -- exactly the block's conditional,
    /// with no M x M table. Where top = low the block is one date, there.
    fn draw_block(
        &self,
        rng: &mut Generator,
        block: &[String],
        top: &[usize],
        low: &[usize],
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
    ) -> HashMap<String, Vec<usize>> {
        let dt = self.dt;
        let n = top.len();
        let k = block.len();
        let mut log_f: Vec<Vec<f64>> = Vec::with_capacity(k);
        let mut log_l: Vec<Vec<f64>> = Vec::with_capacity(k);
        for date_id in block {
            let (cum, l) = (&cumulative[date_id], &likelihood[date_id]);
            let padded = |i: usize| if i == 0 { 0.0 } else { cum[i - 1] };
            log_f.push(
                (0..n)
                    .map(|d| {
                        let f = (padded(top[d] + 1) - 0.5 * dt * l[top[d]])
                            - (padded(low[d]) + 0.5 * dt * l[low[d]]);
                        if top[d] > low[d] {
                            f.max(0.0).ln()
                        } else {
                            f64::NEG_INFINITY
                        }
                    })
                    .collect(),
            );
            log_l.push((0..n).map(|d| (dt * l[top[d]]).ln()).collect());
        }
        let mut chosen = vec![0usize; n];
        let mut weights = vec![vec![0.0; n]; k];
        for d in 0..n {
            let mut total = 0.0;
            let mut zeros = 0;
            for row in &log_f {
                if row[d].is_finite() {
                    total += row[d];
                } else {
                    zeros += 1;
                }
            }
            for j in 0..k {
                let others = if log_f[j][d].is_finite() {
                    if zeros == 0 {
                        total - log_f[j][d]
                    } else {
                        f64::NEG_INFINITY
                    }
                } else if zeros == 1 {
                    total
                } else {
                    f64::NEG_INFINITY
                };
                weights[j][d] = log_l[j][d] + others;
            }
            let peak = (0..k)
                .map(|j| weights[j][d])
                .fold(f64::NEG_INFINITY, f64::max);
            for row in &mut weights {
                let v = (row[d] - peak).exp();
                row[d] = if v.is_nan() { 0.0 } else { v };
            }
        }
        // cdf over k, per draw.
        let mut cdf = weights.clone();
        for j in 1..k {
            for d in 0..n {
                cdf[j][d] = cdf[j - 1][d] + weights[j][d];
            }
        }
        let uniforms = rng.random_n(n);
        for d in 0..n {
            let target = uniforms[d] * cdf[k - 1][d];
            let below = (0..k).filter(|&j| cdf[j][d] < target).count();
            chosen[d] = below.min(k - 1);
        }
        let mut out = HashMap::new();
        for (j, date_id) in block.iter().enumerate() {
            let within = draw_in(
                rng,
                &cumulative[date_id],
                &likelihood[date_id],
                low,
                top,
                dt,
                true,
            );
            let values = (0..n)
                .map(|d| if chosen[d] == j { top[d] } else { within[d] })
                .collect();
            out.insert(date_id.clone(), values);
        }
        out
    }
}

/// log of L(u) dt, by column u.
fn log_top(dt: f64, likelihood: &[f64]) -> Vec<f64> {
    likelihood.iter().map(|l| (dt * l).ln()).collect()
}

/// (j, log prod over the others of F, log T over the others), by row.
type LeftOut = (usize, Vec<Vec<f64>>, Vec<Vec<f64>>);

/// For rows `rows` of [l, u], each member j in turn as (j, log prod over the
/// others of F, log T over the others) -- T the derivative in u of prod F,
/// sum_k L_k(u) dt prod_(m != k) F_m. From one backward and one forward pass
/// of the product rule in the log, so no F is ever divided out and a zero one
/// stays a zero.
fn leave_one_out(
    log_f: &[Matrix],
    tops: &[Vec<f64>],
    rows: std::ops::Range<usize>,
) -> Vec<LeftOut> {
    let k = log_f.len();
    let m = tops[0].len();
    let a: Vec<Vec<Vec<f64>>> = log_f
        .iter()
        .map(|f| rows.clone().map(|r| f.row(r).to_vec()).collect())
        .collect();
    let n = rows.len();
    let zeros = vec![vec![0.0; m]; n];
    let infs = vec![vec![f64::NEG_INFINITY; m]; n];
    let combine = |x: &Vec<Vec<f64>>, xa: &Vec<Vec<f64>>, y: &Vec<Vec<f64>>, yb: &Vec<f64>| {
        // logaddexp(x + xa, y + yb), yb broadcast over rows.
        (0..n)
            .map(|r| {
                (0..m)
                    .map(|u| logaddexp(x[r][u] + xa[r][u], y[r][u] + yb[u]))
                    .collect()
            })
            .collect::<Vec<Vec<f64>>>()
    };
    let add = |x: &Vec<Vec<f64>>, y: &Vec<Vec<f64>>| {
        (0..n)
            .map(|r| (0..m).map(|u| x[r][u] + y[r][u]).collect())
            .collect::<Vec<Vec<f64>>>()
    };
    let mut big_q: Vec<Vec<Vec<f64>>> = vec![Vec::new(); k];
    let mut big_u: Vec<Vec<Vec<f64>>> = vec![Vec::new(); k];
    let mut q = zeros.clone();
    let mut u = infs.clone();
    for j in (0..k).rev() {
        big_q[j] = q.clone();
        big_u[j] = u.clone();
        u = combine(&u, &a[j], &q, &tops[j]);
        q = add(&q, &a[j]);
    }
    let mut out = Vec::with_capacity(k);
    let mut p = zeros;
    let mut r = infs;
    for j in 0..k {
        let others = add(&p, &big_q[j]);
        // logaddexp(r + Q[j], p + U[j])
        let rest: Vec<Vec<f64>> = (0..n)
            .map(|row| {
                (0..m)
                    .map(|c| logaddexp(r[row][c] + big_q[j][row][c], p[row][c] + big_u[j][row][c]))
                    .collect()
            })
            .collect();
        out.push((j, others, rest));
        r = combine(&r, &a[j], &p, &tops[j]);
        p = add(&p, &a[j]);
    }
    out
}

/// An ordered group's T over its two Boundaries, in the log and indexed
/// [younger, older]: the span factor over all its K dates times the sum over
/// its cuts. Where the two Boundaries share a cell every date is in it: the
/// tie's half times the dates' likelihoods, as for any group, and a half more
/// for each pair of dates in neighbouring blocks, a tied ordering edge each,
/// as the sampler weighs them.
pub fn ordered_log_transfer(
    group: &OrderedGroup,
    grid: &[f64],
    older_ok: &[bool],
    younger_ok: &[bool],
    likelihood: &HashMap<String, Vec<f64>>,
) -> Matrix {
    let sizes: Vec<usize> = group.blocks.iter().map(Vec::len).collect();
    let ties: usize = sizes.windows(2).map(|w| w[0] * w[1]).sum();
    let mut term = boundary_pair_term(grid, sizes.iter().sum());
    term.zip_inplace(&group.log_inner, |a, b| a + b);
    let m = grid.len();
    let mut logs = vec![0.0; m];
    for date_id in group.blocks.iter().flatten() {
        for (s, l) in logs.iter_mut().zip(&likelihood[date_id]) {
            *s += l.ln();
        }
    }
    let constant = BOUNDARY_TIE_WEIGHT.ln() - ties as f64 * 2.0_f64.ln();
    let tie: Vec<f64> = logs.iter().map(|s| constant + s).collect();
    term.set_diagonal(&tie);
    mask_allowed(&mut term, older_ok, younger_ok);
    term
}

/// -inf wherever either boundary may not sit.
pub(crate) fn mask_allowed(term: &mut Matrix, older_ok: &[bool], younger_ok: &[bool]) {
    if older_ok.iter().all(|&o| o) && younger_ok.iter().all(|&y| y) {
        return;
    }
    for (y, &yok) in younger_ok.iter().enumerate() {
        for (o, &ook) in older_ok.iter().enumerate() {
            if !(yok && ook) {
                term.set(y, o, f64::NEG_INFINITY);
            }
        }
    }
}
