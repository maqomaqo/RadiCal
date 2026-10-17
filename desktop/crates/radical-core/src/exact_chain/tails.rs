//! Dates outside every Boundary at the model's two ends: each held only by its
//! end Boundary, so the whole tail enters the chain as one weight on it.

use std::collections::HashMap;

use crate::Error;
use crate::exact_chain::Chain;
use crate::exact_chain::messages::{draw_dates, finite_peak};
use crate::exact_chain::ordered::OrderedGroup;
use crate::matrix::Matrix;
use crate::numeric::cumsum;
use crate::rng::Generator;

/// A chain's dates outside every Boundary on one grid: each one's integral
/// beyond its end Boundary ([`tail_integrals`]), and the weight the tail at
/// each end puts on that Boundary ([`tail_weight`]), as `end_weights` for the
/// message passing, with `log_scale` the log of what scaling the weights took
/// out. Empty for a chain without tails, whose solve it then leaves untouched.
pub struct Tails {
    first_id: String,
    last_id: String,
    m: usize,
    pub end_weights: HashMap<String, Vec<f64>>,
    pub log_scale: f64,
    // A tail of several blocks, ordered one after another, is an ordered
    // group with one end held at the grid's edge and no span factor, and its
    // weight on the end Boundary is a row or column of the group's.
    older_group: Option<OrderedGroup>,
    younger_group: Option<OrderedGroup>,
    older: Vec<(String, Vec<f64>)>,
    younger: Vec<(String, Vec<f64>)>,
    older_tail: Vec<String>,
    younger_tail: Vec<String>,
}

impl Tails {
    pub fn new(
        chain: &Chain,
        grid: &[f64],
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
    ) -> Self {
        let m = grid.len();
        let first_id = chain.boundary_ids[0].clone();
        let last_id = chain.boundary_ids[chain.boundary_ids.len() - 1].clone();
        let mut tails = Self {
            first_id: first_id.clone(),
            last_id: last_id.clone(),
            m,
            end_weights: HashMap::new(),
            log_scale: 0.0,
            older_group: None,
            younger_group: None,
            older: Vec::new(),
            younger: Vec::new(),
            older_tail: chain.older_tail.clone(),
            younger_tail: chain.younger_tail.clone(),
        };
        if chain.older_tail_blocks.len() > 1 {
            let group = OrderedGroup::new(&chain.older_tail_blocks, grid, cumulative, likelihood);
            tails.weigh(&first_id, &group.log_inner.column(m - 1));
            tails.older_group = Some(group);
        } else {
            tails.older = tail_integrals(grid, cumulative, likelihood, &chain.older_tail, true);
        }
        if chain.younger_tail_blocks.len() > 1 {
            let group = OrderedGroup::new(&chain.younger_tail_blocks, grid, cumulative, likelihood);
            tails.weigh(&last_id, group.log_inner.row(0));
            tails.younger_group = Some(group);
        } else {
            tails.younger =
                tail_integrals(grid, cumulative, likelihood, &chain.younger_tail, false);
        }
        for (bid, integrals) in [(&first_id, &tails.older), (&last_id, &tails.younger)] {
            if !integrals.is_empty() {
                let (weight, peak) = tail_weight(integrals);
                tails.end_weights.insert(bid.clone(), weight);
                tails.log_scale += peak;
            }
        }
        tails
    }

    fn weigh(&mut self, bid: &str, log_g: &[f64]) {
        let peak = finite_peak(log_g);
        self.end_weights.insert(
            bid.to_owned(),
            log_g
                .iter()
                .map(|&v| if v.is_finite() { (v - peak).exp() } else { 0.0 })
                .collect(),
        );
        self.log_scale += peak;
    }

    /// An ordered tail's marginals: its end Boundary's own, with the tail's
    /// weight on it divided out, as the weight of everything else.
    fn solve_group(
        group: &mut OrderedGroup,
        weight: &[f64],
        density: &[f64],
        older_end: bool,
        m: usize,
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
    ) -> Vec<(String, Vec<f64>)> {
        let outside: Vec<f64> = weight
            .iter()
            .zip(density)
            .map(|(&w, &d)| {
                if w > 0.0 {
                    d.ln() - w.ln()
                } else {
                    f64::NEG_INFINITY
                }
            })
            .collect();
        let mut log_end = Matrix::filled(m, m, f64::NEG_INFINITY); // [younger, older]
        for (i, &v) in outside.iter().enumerate() {
            if older_end {
                log_end.set(i, m - 1, v);
            } else {
                log_end.set(0, i, v);
            }
        }
        group.solve(&log_end, None);
        group.marginals(cumulative, likelihood)
    }

    /// Every tail date's marginal, from its end Boundary's
    /// ([`tail_marginals`]).
    pub fn marginals(
        &mut self,
        first_density: &[f64],
        last_density: &[f64],
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
    ) -> Vec<(String, Vec<f64>)> {
        let mut out = tail_marginals(first_density, &self.older, likelihood, true);
        out.extend(tail_marginals(
            last_density,
            &self.younger,
            likelihood,
            false,
        ));
        let m = self.m;
        if let Some(group) = self.older_group.as_mut() {
            out.extend(Self::solve_group(
                group,
                &self.end_weights[&self.first_id],
                first_density,
                true,
                m,
                cumulative,
                likelihood,
            ));
        }
        if let Some(group) = self.younger_group.as_mut() {
            out.extend(Self::solve_group(
                group,
                &self.end_weights[&self.last_id],
                last_density,
                false,
                m,
                cumulative,
                likelihood,
            ));
        }
        out
    }

    /// Every tail date's draws, given its end Boundary's ([`draw_tail`]).
    pub fn draw(
        &self,
        rng: &mut Generator,
        first_draws: &[usize],
        last_draws: &[usize],
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
        step: f64,
    ) -> Result<HashMap<String, Vec<usize>>, Error> {
        let mut out = HashMap::new();
        let n = first_draws.len();
        if let Some(group) = &self.older_group {
            out.extend(group.draw(
                rng,
                &vec![self.m - 1; n],
                first_draws,
                cumulative,
                likelihood,
            )?);
        } else if !self.older_tail.is_empty() {
            out.extend(draw_tail(
                rng,
                &self.older_tail,
                first_draws,
                true,
                self.m,
                cumulative,
                likelihood,
                step,
            ));
        }
        if let Some(group) = &self.younger_group {
            out.extend(group.draw(rng, last_draws, &vec![0; n], cumulative, likelihood)?);
        } else if !self.younger_tail.is_empty() {
            out.extend(draw_tail(
                rng,
                &self.younger_tail,
                last_draws,
                false,
                self.m,
                cumulative,
                likelihood,
                step,
            ));
        }
        Ok(out)
    }
}

/// {date id: G(F)} for dates outside every Boundary: each one's trapezoidal
/// integral from an end Boundary at F out to the grid's edge -- beyond F for
/// an older tail, below it for a younger. The entries of the interval integral
/// with one end pinned at the edge, formed the same way and so equal to them.
pub fn tail_integrals(
    grid: &[f64],
    cumulative: &HashMap<String, Vec<f64>>,
    likelihood: &HashMap<String, Vec<f64>>,
    date_ids: &[String],
    older: bool,
) -> Vec<(String, Vec<f64>)> {
    let dt = grid[1] - grid[0];
    let m = grid.len();
    date_ids
        .iter()
        .map(|date_id| {
            let cum = &cumulative[date_id];
            let half: Vec<f64> = likelihood[date_id].iter().map(|l| 0.5 * dt * l).collect();
            let below = |f: usize| if f == 0 { 0.0 } else { cum[f - 1] };
            let mut g: Vec<f64> = (0..m)
                .map(|f| {
                    let v = if older {
                        // [F, top]: (C[top] - h[top]) - (C[F-1] + h[F])
                        (cum[m - 1] - half[m - 1]) - (below(f) + half[f])
                    } else {
                        // [bottom, F]: (C[F] - h[F]) - (C[-1] + h[0])
                        (cum[f] - half[f]) - (0.0 + half[0])
                    };
                    v.max(0.0)
                })
                .collect();
            // With F on the grid's edge the interval has no width. The
            // difference above leaves rounding there instead of zero, and the
            // marginals divide by G, so a residue of 1e-16 would hand that cell
            // a weight it does not have.
            if older {
                g[m - 1] = 0.0;
            } else {
                g[0] = 0.0;
            }
            (date_id.clone(), g)
        })
        .collect()
}

/// The weight a tail of dates outside every Boundary puts on its end
/// Boundary: the product of their integrals beyond it, scaled to a peak of
/// one, with the log of that peak.
///
/// No span factor: a date outside every Boundary has a flat prior, as in
/// OxCal, so given the end Boundary the dates are independent of each other
/// and of the rest of the model, and this one vector is all of them the rest
/// of the model sees. A date ON the Boundary counts half, as everywhere.
pub fn tail_weight(integrals: &[(String, Vec<f64>)]) -> (Vec<f64>, f64) {
    let m = integrals[0].1.len();
    let mut log_g = vec![0.0; m];
    for (_, g) in integrals {
        for (s, v) in log_g.iter_mut().zip(g) {
            *s += v.ln();
        }
    }
    let peak = finite_peak(&log_g);
    (log_g.iter().map(|v| (v - peak).exp()).collect(), peak)
}

/// {date id: unnormalised marginal} for a tail, from its end Boundary's
/// marginal P(F), which already carries every tail date's integral.
///
/// Given F, date j sits at t with weight L_j(t) h(t) / G_j(F) over the tail's
/// side of F, h a half at F and at the grid's edge, as G_j integrates it. So
/// p_j(t) = L_j(t) * sum over F on the near side of t of h P(F) / G_j(F): one
/// cumulative sum.
pub fn tail_marginals(
    end_density: &[f64],
    integrals: &[(String, Vec<f64>)],
    likelihood: &HashMap<String, Vec<f64>>,
    older: bool,
) -> Vec<(String, Vec<f64>)> {
    integrals
        .iter()
        .map(|(date_id, g)| {
            let per_end: Vec<f64> = end_density
                .iter()
                .zip(g)
                .map(|(&p, &gv)| if gv > 0.0 { p / gv } else { 0.0 })
                .collect();
            let reach: Vec<f64> = if older {
                // t >= F
                cumsum(&per_end)
                    .iter()
                    .zip(&per_end)
                    .map(|(c, p)| c - 0.5 * p)
                    .collect()
            } else {
                // t <= F
                let mut reversed: Vec<f64> = per_end.iter().rev().copied().collect();
                reversed = cumsum(&reversed);
                reversed.reverse();
                reversed
                    .iter()
                    .zip(&per_end)
                    .map(|(c, p)| c - 0.5 * p)
                    .collect()
            };
            let mut density: Vec<f64> = likelihood[date_id]
                .iter()
                .zip(&reach)
                .map(|(l, r)| l * r.max(0.0))
                .collect();
            let edge = if older { density.len() - 1 } else { 0 };
            density[edge] *= 0.5;
            (date_id.clone(), density)
        })
        .collect()
}

/// A grid index per tail date per draw, given its end Boundary's drawn cell:
/// its likelihood over the tail's side of that cell, half at each end, as a
/// date between two Boundaries is drawn -- here one of them is the grid's edge.
#[allow(clippy::too_many_arguments)]
pub fn draw_tail(
    rng: &mut Generator,
    date_ids: &[String],
    end_draws: &[usize],
    older: bool,
    m: usize,
    cumulative: &HashMap<String, Vec<f64>>,
    likelihood: &HashMap<String, Vec<f64>>,
    step: f64,
) -> HashMap<String, Vec<usize>> {
    let edge = vec![if older { m - 1 } else { 0 }; end_draws.len()];
    let pair = if older {
        vec![edge, end_draws.to_vec()]
    } else {
        vec![end_draws.to_vec(), edge]
    };
    draw_dates(
        rng,
        &[date_ids.to_vec()],
        cumulative,
        likelihood,
        &pair,
        step,
    )
}
