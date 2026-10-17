//! Forward/backward message passing over a chain of Boundaries, the joint
//! draws it allows, and each date's draw given its Boundaries.

use std::collections::HashMap;

use crate::Error;
use crate::exact_chain::Chain;
use crate::matrix::Matrix;
use crate::numeric::{cumsum, searchsorted_left, searchsorted_right, sum};
use crate::rng::Generator;

/// The finite maximum [`exp_rescaled`] takes out, 0 when there is none.
pub fn finite_peak(log_weights: &[f64]) -> f64 {
    let mut best = f64::NEG_INFINITY;
    let mut any = false;
    for &v in log_weights {
        if v.is_finite() {
            any = true;
            if v > best {
                best = v;
            }
        }
    }
    if any { best } else { 0.0 }
}

/// Log weights shifted by their own finite maximum and exponentiated:
/// `exp(_rescaled(w))`, zero where the log is -inf.
///
/// Per-array rather than against a shared constant: dividing a joint weight by
/// one date's interval integral can raise it by hundreds of nats, which
/// overflows a shared shift.
pub fn exp_rescaled(log_weights: &[f64]) -> Vec<f64> {
    let peak = finite_peak(log_weights);
    log_weights
        .iter()
        .map(|&v| if v.is_finite() { (v - peak).exp() } else { 0.0 })
        .collect()
}

/// T_i: a log transfer matrix exponentiated, on demand rather than kept
/// alongside it.
pub fn exponentiated(log_t: &Matrix) -> Matrix {
    Matrix::from_rows(log_t.rows(), log_t.cols(), exp_rescaled(log_t.data()))
}

/// `count` inverse-CDF draws of an index into a 1-D weight array.
pub fn sample_indices(
    rng: &mut Generator,
    weights: &[f64],
    count: usize,
) -> Result<Vec<usize>, Error> {
    let total = sum(weights);
    if !total.is_finite() || total <= 0.0 {
        return Err(Error::Model(
            "a posterior came out empty; the model may be contradictory".into(),
        ));
    }
    let cdf = cumsum(weights);
    let last = weights.len() - 1;
    Ok(rng
        .random_n(count)
        .into_iter()
        .map(|u| searchsorted_left(&cdf, u * total).min(last))
        .collect())
}

/// One column index per draw, draw k an inverse-CDF draw from the weights of
/// row `keys[k]`, `row_for(key)` giving that row.
///
/// Draws that condition on the same row share its cumulative sum. There are
/// far fewer distinct rows than draws -- under 1% of the 20,000 joint draws
/// on a plain chain, and 5-44% on the span-prior path, where a row is a
/// (boundary, last boundary) pair.
///
/// Each row is searched against its own cumulative only, so rows on wildly
/// different scales cannot interfere. They do differ: a conditional's rows
/// are products of message entries that run from 1 down to 1e-60.
///
/// The uniforms are taken one per draw in draw order, and each row's weights
/// and cumulative are formed exactly as a row per draw would form them, so the
/// draws are exactly those of a row-per-draw loop on the same generator:
/// grouping changes the cost and nothing else.
pub fn draw_from_rows(
    rng: &mut Generator,
    row_for: impl Fn(usize) -> Vec<f64>,
    keys: &[usize],
) -> Result<Vec<usize>, Error> {
    let uniforms = rng.random_n(keys.len());
    let mut by_key: HashMap<usize, Vec<usize>> = HashMap::new();
    for (k, &key) in keys.iter().enumerate() {
        by_key.entry(key).or_default().push(k);
    }
    let mut distinct: Vec<usize> = by_key.keys().copied().collect();
    distinct.sort_unstable();
    let mut picked = vec![0usize; keys.len()];
    for key in distinct {
        let weights = row_for(key);
        let cumulative = cumsum(&weights);
        let total = cumulative[cumulative.len() - 1];
        if total <= 0.0 || !total.is_finite() {
            return Err(Error::Model(
                "a conditional came out empty while drawing from the joint posterior; the \
                 model may be contradictory"
                    .into(),
            ));
        }
        // u * total < total in exact arithmetic, but can round up to it; the
        // last column with weight is where such a draw belongs.
        let last_with_weight = weights
            .iter()
            .rposition(|&w| w > 0.0)
            .unwrap_or(weights.len() - 1);
        for &draw in &by_key[&key] {
            // The first column whose cumulative EXCEEDS the target, which
            // always carries weight: a zero cell repeats its predecessor's
            // cumulative, so it cannot be the first to exceed anything.
            let found = searchsorted_right(&cumulative, uniforms[draw] * total);
            picked[draw] = found.min(last_with_weight);
        }
    }
    Ok(picked)
}

/// Joint draws of a chain's Boundaries, as grid indices: one vector per
/// Boundary, oldest first, each holding every draw.
pub type BoundaryDraws = Vec<Vec<usize>>;

/// Forward/backward over the boundaries, the O(M^2) case.
pub struct PlainMessages {
    alpha: Vec<Vec<f64>>,
    beta: Vec<Vec<f64>>,
    pub log_mass: f64,
}

/// Which messages a chain was solved with: the plain O(M^2) recursion, or
/// matrices once a factor couples its two ends.
pub enum Messages {
    Plain(PlainMessages),
    EndFactor(EndFactorMessages),
}

impl Messages {
    /// Everything outside group k as a matrix indexed [younger boundary, older
    /// boundary].
    pub fn pair_weight(&self, k: usize) -> Matrix {
        match self {
            Self::Plain(p) => {
                let (a, b) = (&p.alpha[k], &p.beta[k + 1]);
                Matrix::from_fn(b.len(), a.len(), |y, o| a[o] * b[y])
            }
            Self::EndFactor(e) => e.pair_weight(k),
        }
    }

    /// Boundary i's unnormalised marginal.
    pub fn boundary_density(&self, i: usize) -> Vec<f64> {
        match self {
            Self::Plain(p) => p.alpha[i]
                .iter()
                .zip(&p.beta[i])
                .map(|(a, b)| a * b)
                .collect(),
            Self::EndFactor(e) => e.boundary_density(i),
        }
    }

    /// The log of the total weight of the chain as its transfers scale it,
    /// which the solve turns into the model's evidence.
    pub fn log_mass(&self) -> f64 {
        match self {
            Self::Plain(p) => p.log_mass,
            Self::EndFactor(e) => e.log_mass,
        }
    }

    /// `count` INDEPENDENT draws of every boundary at once, as grid indices,
    /// oldest boundary first. `log_t` are the chain's log transfers.
    pub fn draw_boundaries(
        &self,
        log_t: &[Matrix],
        rng: &mut Generator,
        count: usize,
    ) -> Result<BoundaryDraws, Error> {
        match self {
            Self::Plain(p) => p.draw_boundaries(log_t, rng, count),
            Self::EndFactor(e) => e.draw_boundaries(log_t, rng, count, None),
        }
    }
}

fn peak_of(v: &[f64]) -> f64 {
    let mut best = f64::NEG_INFINITY;
    for &x in v {
        if x.is_nan() {
            return x;
        }
        if x > best {
            best = x;
        }
    }
    best
}

fn allowed_weights(allowed: &[bool], weight: Option<&Vec<f64>>) -> Vec<f64> {
    allowed
        .iter()
        .enumerate()
        .map(|(i, &ok)| {
            let base = if ok { 1.0 } else { 0.0 };
            base * weight.map_or(1.0, |w| w[i])
        })
        .collect()
}

/// Forward/backward over the boundaries, the O(M^2) case.
///
/// `end_weights`, {boundary id: weight over the grid}, multiplies the first or
/// last boundary's allowed window: the weight dates outside every Boundary put
/// on it.
///
/// Each message is scaled to a peak of one as it is formed, and the scales
/// kept in the log, so a long chain can neither underflow nor overflow and the
/// total survives the rescaling.
pub fn messages(
    chain: &Chain,
    allowed: &HashMap<String, Vec<bool>>,
    log_t: &[Matrix],
    end_weights: &HashMap<String, Vec<f64>>,
) -> Messages {
    let first = &chain.boundary_ids[0];
    let last = &chain.boundary_ids[chain.boundary_ids.len() - 1];
    let mut log_scale = 0.0;
    let mut alpha = vec![allowed_weights(&allowed[first], end_weights.get(first))];
    for (i, log) in log_t.iter().enumerate().take(chain.groups.len()) {
        let mut message = exponentiated(log).mat_vec(&alpha[i]);
        let peak = peak_of(&message);
        if peak > 0.0 {
            for m in &mut message {
                *m /= peak;
            }
            log_scale += peak.ln();
        }
        alpha.push(message);
    }
    let n = chain.boundary_ids.len();
    let mut beta = vec![Vec::new(); n];
    beta[n - 1] = allowed_weights(&allowed[last], end_weights.get(last));
    for i in (0..chain.groups.len()).rev() {
        let mut message = Matrix::vec_mat(&beta[i + 1], &exponentiated(&log_t[i]));
        let peak = peak_of(&message);
        if peak > 0.0 {
            for m in &mut message {
                *m /= peak;
            }
        }
        beta[i] = message;
    }
    let product: Vec<f64> = alpha[n - 1]
        .iter()
        .zip(&beta[n - 1])
        .map(|(a, b)| a * b)
        .collect();
    let total = sum(&product);
    let log_mass = if total > 0.0 {
        total.ln() + log_scale
    } else {
        f64::NEG_INFINITY
    };
    Messages::Plain(PlainMessages {
        alpha,
        beta,
        log_mass,
    })
}

impl PlainMessages {
    /// Forward-filter/backward-sample: the messages are the forward filter and
    /// the chain is Markov in the boundaries, so drawing the youngest from its
    /// own marginal and each earlier one from alpha_i(b_i) T_i(b_i, b_(i+1))
    /// given the one after it is an exact draw from the joint. No MCMC and no
    /// burn-in.
    ///
    /// This is what the durations need and the marginals cannot supply: the
    /// two boundaries either side of a phase are strongly correlated, so a
    /// duration built by subtracting their marginals would be far too wide.
    fn draw_boundaries(
        &self,
        log_t: &[Matrix],
        rng: &mut Generator,
        count: usize,
    ) -> Result<BoundaryDraws, Error> {
        let num_groups = self.alpha.len() - 1;
        let mut indices = vec![Vec::new(); num_groups + 1];
        let last: Vec<f64> = self.alpha[num_groups]
            .iter()
            .zip(&self.beta[num_groups])
            .map(|(a, b)| a * b)
            .collect();
        indices[num_groups] = sample_indices(rng, &last, count)?;
        for i in (0..num_groups).rev() {
            let matrix = exponentiated(&log_t[i]); // [younger, older]
            let a = &self.alpha[i];
            indices[i] = draw_from_rows(
                rng,
                |y| matrix.row(y).iter().zip(a).map(|(m, a)| m * a).collect(),
                &indices[i + 1],
            )?;
        }
        Ok(indices)
    }
}

/// Scale a matrix by its own peak: (scaled, log of the peak), unchanged with
/// a log of 0 when the peak is not positive.
pub fn scaled_by_peak(mut matrix: Matrix) -> (Matrix, f64) {
    let peak = matrix.max();
    if peak > 0.0 {
        matrix.map_inplace(|v| v / peak);
        (matrix, peak.ln())
    } else {
        (matrix, 0.0)
    }
}

/// The part of [`messages_with_span_prior`] that does not depend on the factor
/// coupling a chain's two ends: the prefix products A and suffix products D
/// over its transfer matrices.
///
/// Formed once per chain. [`crate::exact_parallel`] needs a chain's end-to-end
/// product before it knows the chain's end factor, which there is the weight
/// of everything outside the chain and depends on that product through the
/// rest of the model; [`messages_with_end_factor`] folds the factor in
/// afterwards and consumes these.
pub struct ChainProducts {
    pub first_ok: Vec<f64>,
    pub last_ok: Vec<f64>,
    /// `A[k]`, indexed [first boundary, boundary k]: from the first boundary to
    /// boundary k. `A[0]` is the identity on the allowed first boundaries, so
    /// that a chain of one group reduces to the plain case.
    pub a: Vec<Matrix>,
    /// `D[k]`, indexed [boundary k, last boundary]: from boundary k to the last.
    pub d: Vec<Matrix>,
    pub log_scale: f64,
}

impl ChainProducts {
    pub fn new(chain: &Chain, allowed: &HashMap<String, Vec<bool>>, log_t: &[Matrix]) -> Self {
        let ok = |id: &String| -> Vec<f64> {
            allowed[id]
                .iter()
                .map(|&a| if a { 1.0 } else { 0.0 })
                .collect()
        };
        let first_ok = ok(&chain.boundary_ids[0]);
        let last_ok = ok(&chain.boundary_ids[chain.boundary_ids.len() - 1]);
        let num_groups = chain.groups.len();
        let mut log_scale = 0.0;
        let mut a = vec![Matrix::diag(&first_ok)];
        for (i, log) in log_t.iter().enumerate().take(num_groups) {
            let (product, factor) = scaled_by_peak(a[i].matmul(&exponentiated(log).transpose()));
            a.push(product);
            log_scale += factor;
        }
        let mut d = vec![Matrix::zeros(0, 0); num_groups + 1];
        d[num_groups] = Matrix::diag(&last_ok);
        for i in (0..num_groups).rev() {
            d[i] = scaled_by_peak(exponentiated(&log_t[i]).transpose().matmul(&d[i + 1])).0;
        }
        Self {
            first_ok,
            last_ok,
            a,
            d,
            log_scale,
        }
    }

    /// `A[n]`, indexed [first boundary, last boundary]: the chain's weight with
    /// both ends held, scaled down by exp(log_scale).
    pub fn end_to_end(&self) -> &Matrix {
        &self.a[self.a.len() - 1]
    }
}

/// The same as [`messages`], once the span correction couples the first
/// boundary to the last. That breaks the forward/backward recursion -- a
/// message can no longer be a vector, because everything downstream still
/// depends on where b_first was -- so each message becomes a MATRIX and each
/// step a matrix product.
///
/// With S_i[older, younger] = T_i:
///
/// ```text
/// A_k[b_first, b_k]  = (S_0 ... S_(k-1))     everything before group k
/// D_k[b_k, b_last]   = (S_k ... S_(n-2))     from b_k to the end
/// ```
///
/// and the weight outside group k, with the correction C[b_last, b_first]:
///
/// ```text
/// P_k[b_k, b_(k+1)] = A_k^T C^T D_(k+1)^T
/// ```
///
/// O(M^3) per group instead of O(M^2), which is why it is only built when the
/// correction is actually going to change something. Both P_k and the boundary
/// marginals share the left factor A_k^T C, so it is formed once per boundary
/// and reused; the marginal is then the diagonal of (A_i^T C) D_i^T, a
/// row-wise dot product that costs O(M^2) rather than the O(M^3) of forming
/// the product and discarding all but its diagonal.
pub fn messages_with_span_prior(
    chain: &Chain,
    allowed: &HashMap<String, Vec<bool>>,
    log_t: &[Matrix],
    correction: Matrix,
    end_weights: &HashMap<String, Vec<f64>>,
) -> Messages {
    let mut correction = correction;
    if !end_weights.is_empty() {
        // Into the end factor, which couples the two ends anyway, and only
        // there: the products already carry each end's 0/1 window, and a
        // weight there as well would count it twice.
        let first = end_weights.get(&chain.boundary_ids[0]);
        let last = end_weights.get(&chain.boundary_ids[chain.boundary_ids.len() - 1]);
        let size = correction.rows();
        for l in 0..size {
            for f in 0..size {
                let v =
                    correction.get(l, f) * last.map_or(1.0, |w| w[l]) * first.map_or(1.0, |w| w[f]);
                correction.set(l, f, v);
            }
        }
    }
    Messages::EndFactor(messages_with_end_factor(
        ChainProducts::new(chain, allowed, log_t),
        &correction,
    ))
}

/// The messages of a chain whose two ends a factor couples: the span
/// correction on a chain, the weight of everything outside an area in
/// [`crate::exact_parallel`].
pub struct EndFactorMessages {
    /// C, indexed [first, last], restricted to allowed ends and scaled.
    c: Matrix,
    a_last: Matrix,
    d: Vec<Matrix>,
    /// `L[k] = A[k]^T C`, indexed [b_k, b_last]: everything before boundary k
    /// with the end-to-end factor folded in.
    l: Vec<Matrix>,
    pub log_mass: f64,
}

/// [`messages_with_span_prior`]'s results from a chain's products and the
/// factor coupling its ends, `correction`, indexed [last boundary, first
/// boundary]. Consumes `products`.
pub fn messages_with_end_factor(products: ChainProducts, correction: &Matrix) -> EndFactorMessages {
    let ChainProducts {
        first_ok,
        last_ok,
        a,
        d,
        log_scale: products_scale,
    } = products;
    let size = correction.rows();
    // C indexed [oldest, youngest], restricted to allowed ends.
    let restricted = Matrix::from_fn(size, size, |f, l| {
        correction.get(l, f) * first_ok[f] * last_ok[l]
    });
    let (c, scale) = scaled_by_peak(restricted);
    let log_scale = scale + products_scale;
    let a_last = a[a.len() - 1].clone();
    let l: Vec<Matrix> = a.iter().map(|ak| ak.transpose().matmul(&c)).collect();
    drop(a);
    let total = c.zip_map(&a_last, |x, y| x * y).sum();
    let log_mass = if total > 0.0 {
        total.ln() + log_scale
    } else {
        f64::NEG_INFINITY
    };
    EndFactorMessages {
        c,
        a_last,
        d,
        l,
        log_mass,
    }
}

impl EndFactorMessages {
    /// [younger, older], formed that way round rather than transposed after.
    pub fn pair_weight(&self, k: usize) -> Matrix {
        self.d[k + 1].matmul(&self.l[k].transpose())
    }

    /// `diag(L[i] @ D[i].T)` without forming the product.
    pub fn boundary_density(&self, i: usize) -> Vec<f64> {
        (0..self.l[i].rows())
            .map(|r| {
                let mut s = 0.0;
                for (x, y) in self.l[i].row(r).iter().zip(self.d[i].row(r)) {
                    s += x * y;
                }
                s
            })
            .collect()
    }

    /// Joint draws, as in the plain case, but the end factor makes the chain
    /// non-Markov so the sampling order changes.
    ///
    /// C couples b_first to b_last, so there is no backward pass over single
    /// boundaries. CONDITIONAL on both ends, though, C is a constant and the
    /// rest is an ordinary chain again: the two ends are drawn together from
    /// their joint `C * A[n]`, and the interior filled in forwards, each boundary
    /// from T_i(b_i, b_(i+1)) D_(i+1)(b_(i+1), b_last) given the one before it
    /// and the far end.
    ///
    /// `ends`, as (first, last) grid indices one per draw, skips the first
    /// step: [`crate::exact_parallel`] draws an area's ends from the rest of
    /// the model and has only its interior filled in here.
    ///
    /// The last step needs no special case: `D[n]` is diagonal on the allowed
    /// last boundary, so the final draw can only land on the b_last already
    /// drawn, which is the consistency check falling out of the maths rather
    /// than being imposed on it.
    pub fn draw_boundaries(
        &self,
        log_t: &[Matrix],
        rng: &mut Generator,
        count: usize,
        ends: Option<(Vec<usize>, Vec<usize>)>,
    ) -> Result<BoundaryDraws, Error> {
        let num_groups = self.d.len() - 1;
        let mut indices = vec![Vec::new(); num_groups + 1];
        match ends {
            None => {
                let joint = self.c.zip_map(&self.a_last, |x, y| x * y); // [b_first, b_last]
                let width = joint.cols();
                let flat = sample_indices(rng, joint.data(), count)?;
                indices[0] = flat.iter().map(|f| f / width).collect();
                indices[num_groups] = flat.iter().map(|f| f % width).collect();
            }
            Some((first, last)) => {
                indices[0] = first;
                indices[num_groups] = last;
            }
        }
        for i in 0..num_groups.saturating_sub(1) {
            let older_to_younger = exponentiated(&log_t[i]).transpose(); // [older, younger]
            let reach = self.d[i + 1].transpose(); // [b_last, b_(i+1)]
            let width = reach.rows();
            let keys: Vec<usize> = indices[i]
                .iter()
                .zip(&indices[num_groups])
                .map(|(b, l)| b * width + l)
                .collect();
            indices[i + 1] = draw_from_rows(
                rng,
                |pair| {
                    older_to_younger
                        .row(pair / width)
                        .iter()
                        .zip(reach.row(pair % width))
                        .map(|(a, r)| a * r)
                        .collect()
                },
                &keys,
            )?;
        }
        Ok(indices)
    }
}

/// (cell, empty) per draw: an inverse-CDF draw at `uniform` from `density`
/// over cells low..high, half weight at `low`, and at `high` too with
/// `high_half`; `empty` where the interval holds none.
pub fn pick_in(
    cum: &[f64],
    density: &[f64],
    low: usize,
    high: usize,
    step: f64,
    uniform: f64,
    high_half: bool,
) -> (usize, bool) {
    let low_end = 0.5 * step * density[low];
    let mut high_end = (if high_half { 0.5 } else { 1.0 }) * step * density[high];
    if !high_half && high <= low {
        high_end = 0.0;
    }
    // Interior cells low+1 .. high-1, whole.
    let interior = if high > low + 1 {
        cum[high.saturating_sub(1)] - cum[low]
    } else {
        0.0
    };
    let total = low_end + interior + high_end;
    let target = uniform * total;
    let picked = if target < low_end {
        low
    } else if target < low_end + interior {
        let inside = searchsorted_right(cum, cum[low] + (target - low_end));
        inside.clamp(low + 1, (high.saturating_sub(1)).max(low + 1))
    } else {
        high
    };
    (picked, total <= 0.0)
}

/// [`pick_in`] with its own uniforms, and a cell inside the interval where it
/// holds no likelihood, as [`draw_dates`] does.
pub fn draw_in(
    rng: &mut Generator,
    cum: &[f64],
    density: &[f64],
    low: &[usize],
    high: &[usize],
    step: f64,
    high_half: bool,
) -> Vec<usize> {
    let uniforms = rng.random_n(low.len());
    (0..low.len())
        .map(|k| {
            let (lo, hi, u) = (low[k], high[k], uniforms[k]);
            let (mut picked, empty) = pick_in(cum, density, lo, hi, step, u, high_half);
            if empty {
                picked = (lo as f64 + u * (hi - lo + 1) as f64) as usize;
            }
            picked.clamp(lo, hi)
        })
        .collect()
}

/// A calendar index for every date, one per joint boundary draw.
///
/// Conditional on its enclosing boundaries a date is its own likelihood
/// truncated to that interval -- that IS the phase model -- and a group's dates
/// are then conditionally independent, so each is an inverse-CDF draw on the
/// interval. The two end cells, where the date sits on a boundary, count half,
/// as in the interval integral the solver's weights use and in the date
/// marginals, so the draws come from the same joint posterior the marginals
/// describe.
///
/// An interval holding no likelihood at all (a date whose support lies
/// entirely outside where its boundaries landed) still has to yield a value
/// inside it rather than an out-of-range index.
pub fn draw_dates(
    rng: &mut Generator,
    groups: &[Vec<String>],
    cumulative: &HashMap<String, Vec<f64>>,
    likelihood: &HashMap<String, Vec<f64>>,
    boundary_draws: &[Vec<usize>],
    step: f64,
) -> HashMap<String, Vec<usize>> {
    let mut draws = HashMap::new();
    for (index, date_ids) in groups.iter().enumerate() {
        let older = &boundary_draws[index];
        let younger = &boundary_draws[index + 1];
        let low: Vec<usize> = older.iter().zip(younger).map(|(&o, &y)| o.min(y)).collect();
        let high: Vec<usize> = older.iter().zip(younger).map(|(&o, &y)| o.max(y)).collect();
        for date_id in date_ids {
            let picked = draw_in(
                rng,
                &cumulative[date_id],
                &likelihood[date_id],
                &low,
                &high,
                step,
                true,
            );
            draws.insert(date_id.clone(), picked);
        }
    }
    draws
}
