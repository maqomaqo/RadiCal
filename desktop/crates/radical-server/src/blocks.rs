//! The analysis-level blocks a run stores, as the backend writes them:
//! durations and differences (`Analysis.intervals`), with their age-depth,
//! phase-activity and dispersal sections, and kernel density estimates
//! (`Analysis.summaries`).

use radical_core::calibration::HpdRange;
use radical_core::dispersal::{OriginProfile, Proposal, Residual, Section, SiteSpec, Trend, Wave};
use radical_core::intervals::{
    self, AgeDepth, Block, CalendarBand, Difference, Draws, Duration, NamedDate, PhaseActivity,
    YearsSummary,
};
use radical_core::kde::{self, PhaseSummary, Spread, Summary};
use radical_core::measurement::Curves;
use radical_core::numeric::median;
use radical_core::rng::Generator;
use radical_core::{Error, Mode};
use radical_store::rows::RadiocarbonSample;
use serde_json::{Value, json};

use crate::State;

fn ranges(r: &[HpdRange]) -> Value {
    Value::Array(r.iter().map(|r| json!({"min": r.min, "max": r.max, "probability_percent": r.probability_percent})).collect())
}

/// A quantity in years (`intervals.summarise`).
pub fn years(y: &YearsSummary) -> Value {
    json!({
        "mean": y.mean, "median": y.median, "std": y.std, "hpd_68": y.hpd_68, "hpd_95": y.hpd_95,
        "hpd_68_ranges": ranges(&y.hpd_68_ranges), "hpd_95_ranges": ranges(&y.hpd_95_ranges),
        "step": y.step, "distribution": y.distribution,
    })
}

/// A calendar age from joint draws (`intervals.calendar_band`).
pub fn band(b: &CalendarBand) -> Value {
    json!({"median": b.median, "mean": b.mean, "band_68": b.band_68, "band_95": b.band_95})
}

fn duration(d: &Duration) -> Value {
    json!({
        "kind": d.kind.as_str(), "id": d.id, "name": d.name, "element_id": d.element_id,
        "dates": d.dates, "years": years(&d.years),
    })
}

fn difference(d: &Difference) -> Value {
    let mut out = json!({
        "kind": d.kind.as_str(), "id": d.id, "from_id": d.from_id, "from_name": d.from_name,
        "to_id": d.to_id, "to_name": d.to_name, "years": years(&d.years), "overlap": d.overlap,
        "severity": d.severity,
    });
    if let Some(p) = d.older_probability {
        out["older_probability"] = json!(p);
    }
    if let Some(n) = d.from_dates {
        out["from_dates"] = json!(n);
    }
    if let Some(n) = d.to_dates {
        out["to_dates"] = json!(n);
    }
    out
}

fn age_depth(a: &AgeDepth) -> Value {
    json!({
        "element_id": a.element_id,
        "name": a.name,
        "depth_units": a.depth_units,
        "levels": a.levels.iter().map(|l| {
            let mut out = json!({"id": l.id, "name": l.name, "depth": l.depth, "dated": l.dated});
            for (k, v) in band(&l.band).as_object().unwrap() {
                out[k] = v.clone();
            }
            out
        }).collect::<Vec<_>>(),
        "accumulation": a.accumulation.iter().map(|x| json!({
            "from_depth": x.from_depth, "to_depth": x.to_depth, "from_name": x.from_name,
            "to_name": x.to_name, "depth_gap": x.depth_gap, "years": years(&x.years),
            "rate_median": x.rate_median, "rate_band_68": x.rate_band_68,
        })).collect::<Vec<_>>(),
    })
}

fn phase_activity(p: &PhaseActivity) -> Value {
    json!({
        "start": p.start,
        "step": p.step,
        "rows": p.rows.iter().map(|r| {
            let mut out = json!({"id": r.id, "name": r.name});
            if r.relative {
                out["relative"] = json!(true);
            }
            if let Some(n) = r.dates {
                out["dates"] = json!(n);
            }
            out["values"] = json!(r.values);
            out
        }).collect::<Vec<_>>(),
    })
}

/// A durations-and-differences block, as `Analysis.intervals` stores it.
pub fn block(b: &Block) -> Value {
    json!({
        "durations": b.durations.iter().map(duration).collect::<Vec<_>>(),
        "differences": b.differences.iter().map(difference).collect::<Vec<_>>(),
        "age_depth": b.age_depth.iter().map(age_depth).collect::<Vec<_>>(),
        "phase_activity": b.phase_activity.as_ref().map(phase_activity),
        "dispersal": b.dispersal.as_ref().map(dispersal),
        "draws": b.draws,
        "independent": b.independent,
        "note": b.note,
    })
}

fn spread(s: &Spread) -> Value {
    json!({"median": s.median, "mean": s.mean, "range_68": s.range_68})
}

/// A kernel density estimate (`kde.summarise_draws`).
pub fn kde_summary(s: &Summary) -> Value {
    json!({
        "method": "kde_plot", "dates": s.dates, "draws": s.draws, "reflected": s.reflected,
        "distribution": s.distribution, "bandwidth": spread(&s.bandwidth),
        "silverman": spread(&s.silverman), "factor": spread(&s.factor),
    })
}

/// One phase's estimate, named.
pub fn phase_kde(p: &PhaseSummary) -> Value {
    let mut out = json!({"element_id": p.element_id, "name": p.name});
    for (k, v) in kde_summary(&p.summary).as_object().unwrap() {
        out[k] = v.clone();
    }
    out
}

// --------------------------------------------------------------------------
// A group calibration's blocks
// --------------------------------------------------------------------------

/// How far apart every pair of a group calibration's dates is, from each
/// date's own posterior (`_date_difference_block`).
pub fn date_differences(state: &State, samples: &[RadiocarbonSample]) -> Result<Value, Error> {
    let mut rng = Generator::new(42);
    let mut draws = Draws::new();
    let mut dates: Vec<(NamedDate, f64)> = Vec::new();
    for s in samples {
        let curve = state
            .curves
            .curve(s.curve_id.as_deref().unwrap_or("intcal20"))?;
        let probs = curve.calibrate(
            s.age_bp,
            s.error_1sigma,
            s.delta_r,
            s.delta_r_error,
            Mode::Standard,
        )?;
        let drawn = intervals::draws_from_distribution(
            curve.cal_ages(),
            &probs,
            &mut rng,
            intervals::DEFAULT_DRAWS,
        )?;
        dates.push((
            NamedDate {
                id: s.id.clone(),
                name: s.sample_name.clone(),
            },
            median(&drawn),
        ));
        draws.insert(s.id.clone(), drawn);
    }
    // Oldest first, ties in the order given.
    dates.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    let named: Vec<NamedDate> = dates.into_iter().map(|(d, _)| d).collect();
    Ok(block(&intervals::report_dates(&named, &draws, None)))
}

/// A group calibration's kernel density estimate, of every date at once
/// (`_summary_block`); None below the fewest dates one is offered for.
pub fn date_kde(state: &State, samples: &[RadiocarbonSample]) -> Result<Option<Value>, Error> {
    let mut calibrated: Vec<(&[f64], Vec<f64>)> = Vec::new();
    for s in samples {
        let curve = state
            .curves
            .curve(s.curve_id.as_deref().unwrap_or("intcal20"))?;
        let probs = curve.calibrate(
            s.age_bp,
            s.error_1sigma,
            s.delta_r,
            s.delta_r_error,
            Mode::Standard,
        )?;
        calibrated.push((curve.cal_ages(), probs));
    }
    let pairs: Vec<(&[f64], &[f64])> = calibrated.iter().map(|(a, p)| (*a, p.as_slice())).collect();
    Ok(kde::summarise(&pairs, kde::DEFAULT_DRAWS, 42)?.map(|s| {
        let mut record = json!({"element_id": null, "name": null});
        for (k, v) in kde_summary(&s).as_object().unwrap() {
            record[k] = v.clone();
        }
        json!({"kde": [record]})
    }))
}

// --------------------------------------------------------------------------
// The dispersal section
// --------------------------------------------------------------------------

pub fn opt<T>(v: &Option<T>, f: impl Fn(&T) -> Value) -> Value {
    v.as_ref().map_or(Value::Null, f)
}

pub fn site_json(s: &SiteSpec) -> Value {
    json!({
        "element_id": s.element_id, "name": s.name, "parameter_id": s.parameter_id,
        "basis": s.basis, "latitude": s.latitude, "longitude": s.longitude,
        "date_ids": s.date_ids, "scatter_km": s.scatter_km, "refusal": s.refusal,
    })
}

pub fn residual(r: &Residual) -> Value {
    let mut out = json!({
        "element_id": r.element_id, "name": r.name,
        "residual_years": r.residual_years, "residual_sd": r.residual_sd,
    });
    if let Some(d) = r.distance_km {
        out["distance_km"] = json!(d);
    }
    out
}

pub fn wave_json(w: &Wave) -> Value {
    let mut out = json!({
        "origin": {"lat": w.origin.0, "lon": w.origin.1},
        "sites": w.sites.iter().map(site_json).collect::<Vec<_>>(),
        "n_sites": w.n_sites, "min_sites": 6, "refused": w.refused,
        "speed": opt(&w.speed, years), "t0": opt(&w.t0, band),
        "residuals": w.residuals.iter().map(residual).collect::<Vec<_>>(), "basis": w.basis,
    });
    if w.refused.is_none() {
        out["distances"] = json!(w.distances);
        out["draws"] = json!(w.draws);
        out["degenerate_draws"] = json!(w.degenerate_draws);
    }
    out
}

pub fn trend_json(t: &Trend) -> Value {
    let mut out = json!({
        "sites": t.sites.iter().map(site_json).collect::<Vec<_>>(),
        "n_sites": t.n_sites, "min_sites": 6, "refused": t.refused,
        "speed": opt(&t.speed, years),
        "azimuth": opt(&t.azimuth, |a| json!({
            "mean_deg": a.mean_deg, "hpd_95_deg": a.hpd_95_deg, "width_deg": a.width_deg,
            "concentration": a.concentration,
        })),
        "centre": opt(&t.centre, |c| json!({"lat": c.0, "lon": c.1})),
        "isochrons": t.isochrons.iter().map(|i| json!({
            "age": i.age,
            "points": i.points.iter().map(|p| json!({"latitude": p.0, "longitude": p.1})).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
        "residuals": t.residuals.iter().map(residual).collect::<Vec<_>>(), "basis": t.basis,
    });
    if t.refused.is_none() {
        out["draws"] = json!(t.draws);
        out["degenerate_draws"] = json!(t.degenerate_draws);
    }
    out
}

pub fn profile_json(p: &OriginProfile) -> Value {
    let mut out = json!({
        "n_sites": p.n_sites, "min_sites": 6, "refused": p.refused, "step_deg": p.step_deg,
        "reach_km": p.reach_km, "candidates": p.candidates, "candidates_ahead": p.candidates_ahead,
        "peak": opt(&p.peak, |c| json!({"lat": c.0, "lon": c.1})),
        "region": p.region.iter().map(|r| json!({"lat": r.lat, "lon": r.lon, "support": r.support})).collect::<Vec<_>>(),
        "cutoff": 0.125,
    });
    if let Some(at) = p.peak_at_reach {
        out["peak_at_reach"] = json!(at);
        out["share_inside"] = json!(p.share_inside);
    }
    out
}

pub fn proposal_json(p: &Proposal) -> Value {
    json!({
        "order": p.order.iter().map(|(id, name, lat, lon)| json!({
            "element_id": id, "name": name, "latitude": lat, "longitude": lon,
        })).collect::<Vec<_>>(),
        "edges": p.edges.iter().map(|e| json!({
            "from": e.from, "to": e.to,
            "from_point": {"latitude": e.from_point.0, "longitude": e.from_point.1},
            "to_point": {"latitude": e.to_point.0, "longitude": e.to_point.1},
            "km": e.km,
        })).collect::<Vec<_>>(),
        "refused": p.refused, "n_sites": p.n_sites, "total_km": p.total_km,
        "rooted_at": p.rooted_at,
        "origin": opt(&p.origin, |o| json!({"lat": o.0, "lon": o.1})),
    })
}

/// The spatial models, side by side.
pub fn dispersal(s: &Section) -> Value {
    json!({
        "wave": s.wave.as_ref().map(wave_json),
        "trend": trend_json(&s.trend),
        "origin_profile": profile_json(&s.origin_profile),
    })
}
