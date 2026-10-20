//! The spatial models held to the Python. Their per-draw fits are a dot
//! product NumPy hands to BLAS, whose summation order nothing here can
//! reproduce, so numbers are held to [`TOL`] of their magnitude; text,
//! counts, orders and the shape of every answer are held exactly.

use std::collections::HashMap;

use crate::*;
use radical_core::calibration::HpdRange;
use radical_core::dispersal::{
    Draws, ORIGIN_GRID_DEG, OriginProfile, Proposal, Residual, SiteSpec, Trend, Wave, derive_sites,
    derive_sites_from_dates, fit_trend, fit_wave, lon_extent, profile_origin, propose_sequence,
    round_step, unproject,
};
use radical_core::intervals::{CalendarBand, YearsSummary};
use radical_core::rng::Generator;
use serde_json::json;

/// How close a number must come, relative to max(|value|, 1). The worst
/// seen is 2e-12, a trend residual: NumPy solves the plane through an SVD
/// and this through the normal equations.
const TOL: f64 = 1e-10;

fn close(label: &str, got: &Value, want: &Value) {
    match (want, got) {
        (Value::Number(_), Value::Number(_)) => {
            let (w, g) = (want.as_f64().unwrap(), got.as_f64().unwrap());
            assert!(
                (g - w).abs() <= TOL * w.abs().max(1.0),
                "{label}: {g} vs {w}"
            );
        }
        (Value::Array(w), Value::Array(g)) => {
            assert_eq!(g.len(), w.len(), "{label}: lengths");
            for (i, (g, w)) in g.iter().zip(w).enumerate() {
                close(&format!("{label}[{i}]"), g, w);
            }
        }
        (Value::Object(w), Value::Object(g)) => {
            let (mut wk, mut gk): (Vec<_>, Vec<_>) = (w.keys().collect(), g.keys().collect());
            wk.sort();
            gk.sort();
            assert_eq!(gk, wk, "{label}: keys");
            for (k, wv) in w {
                close(&format!("{label}.{k}"), &g[k], wv);
            }
        }
        _ => assert_eq!(got, want, "{label}"),
    }
}

fn opt<T>(v: &Option<T>, f: impl Fn(&T) -> Value) -> Value {
    v.as_ref().map_or(Value::Null, f)
}

fn site_json(s: &SiteSpec) -> Value {
    json!({
        "element_id": s.element_id, "name": s.name, "parameter_id": s.parameter_id,
        "basis": s.basis, "latitude": s.latitude, "longitude": s.longitude,
        "date_ids": s.date_ids, "scatter_km": s.scatter_km, "refusal": s.refusal,
    })
}

fn ranges(r: &[HpdRange]) -> Value {
    Value::Array(r.iter().map(|r| json!({"min": r.min, "max": r.max, "probability_percent": r.probability_percent})).collect())
}

fn years(y: &YearsSummary) -> Value {
    json!({
        "mean": y.mean, "median": y.median, "std": y.std, "hpd_68": y.hpd_68, "hpd_95": y.hpd_95,
        "hpd_68_ranges": ranges(&y.hpd_68_ranges), "hpd_95_ranges": ranges(&y.hpd_95_ranges),
        "step": y.step, "distribution": y.distribution,
    })
}

fn band(b: &CalendarBand) -> Value {
    json!({"median": b.median, "mean": b.mean, "band_68": b.band_68, "band_95": b.band_95})
}

fn residual(r: &Residual) -> Value {
    let mut out = json!({
        "element_id": r.element_id, "name": r.name,
        "residual_years": r.residual_years, "residual_sd": r.residual_sd,
    });
    if let Some(d) = r.distance_km {
        out["distance_km"] = json!(d);
    }
    out
}

fn wave_json(w: &Wave) -> Value {
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

fn trend_json(t: &Trend) -> Value {
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

fn profile_json(p: &OriginProfile) -> Value {
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

fn proposal_json(p: &Proposal) -> Value {
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

/// The generator's `_recipe_draws`, replayed on NumPy's generator.
fn recipe_draws(r: &Value) -> Draws {
    if r.is_null() {
        return HashMap::new();
    }
    let mut rng = Generator::new(r["seed"].as_u64().unwrap());
    let j = r["j"].as_u64().unwrap() as usize;
    let common_sd = num(&r["common_sd"]);
    let common: Vec<f64> = (0..j).map(|_| rng.normal(0.0, common_sd)).collect();
    r["params"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| {
            let (mean, sd) = (num(&p[1]), num(&p[2]));
            let values = common
                .iter()
                .map(|c| (mean + c) + rng.normal(0.0, sd))
                .collect();
            (p[0].as_str().unwrap().to_owned(), values)
        })
        .collect()
}

fn site(v: &Value) -> SiteSpec {
    SiteSpec {
        element_id: v["element_id"].as_str().unwrap().to_owned(),
        name: v["name"].as_str().unwrap().to_owned(),
        parameter_id: v["parameter_id"].as_str().unwrap().to_owned(),
        basis: if v["basis"] == "boundary" {
            "boundary"
        } else {
            "earliest_dated_event"
        },
        latitude: v["latitude"].as_f64(),
        longitude: v["longitude"].as_f64(),
        date_ids: v["date_ids"]
            .as_array()
            .map(|a| a.iter().map(|d| d.as_str().unwrap().to_owned()).collect())
            .unwrap_or_default(),
        scatter_km: v["scatter_km"].as_f64(),
        refusal: v["refusal"].as_str().map(str::to_owned),
    }
}

fn origin(v: &Value) -> Option<(f64, f64)> {
    v.as_array().map(|o| (num(&o[0]), num(&o[1])))
}

#[test]
fn dispersal() {
    for case in family("dispersal").cases {
        let i = case.expanded_inputs();
        let name = case.name.as_str();
        let want = outputs(&case);
        let sites =
            || -> Vec<SiteSpec> { i["sites"].as_array().unwrap().iter().map(site).collect() };
        let got = match name.split('/').next().unwrap() {
            "fit_wave" => wave_json(&fit_wave(
                &sites(),
                &recipe_draws(&i["recipe"]),
                origin(&i["origin"]).unwrap(),
            )),
            "fit_trend" => trend_json(&fit_trend(&sites(), &recipe_draws(&i["recipe"]))),
            "profile_origin" => profile_json(&profile_origin(
                &sites(),
                &recipe_draws(&i["recipe"]),
                ORIGIN_GRID_DEG,
            )),
            "derive_sites_from_dates" => {
                let ms: Vec<_> = i["measurements"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(crate::model::measurement)
                    .collect();
                json!({"sites": derive_sites_from_dates(&ms, &recipe_draws(&i["recipe"])).iter().map(site_json).collect::<Vec<_>>()})
            }
            "derive_sites" => {
                let root = crate::model::tree(&i["root"]);
                let ms: Vec<_> = i["measurements"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(crate::model::measurement)
                    .collect();
                json!({"sites": derive_sites(root.as_ref(), &ms).iter().map(site_json).collect::<Vec<_>>()})
            }
            "propose_sequence" => proposal_json(&propose_sequence(&sites(), origin(&i["origin"]))),
            "lon_extent" => {
                let lons: Vec<f64> = i["lons"].as_array().unwrap().iter().map(num).collect();
                let (w, e) = lon_extent(&lons);
                json!({"extent": [w, e]})
            }
            "unproject" => {
                let (lat, lon) =
                    unproject(num(&i["x"]), num(&i["y"]), origin(&i["centre"]).unwrap());
                json!({"point": [lat, lon]})
            }
            "round_step" => json!({"step": round_step(num(&i["raw"]))}),
            other => panic!("unhandled case kind {other}"),
        };
        close(name, &got, &want);
    }
}
