use crate::model::{measurements, opt_num, strings, tree};
use crate::*;
use radical_core::intervals::{
    AgeDepth, Block, CalendarBand, Difference, DifferenceSpec, Draws, Duration, NamedDate,
    PhaseActivity, Side, YearsSummary, calendar_band, plan_age_depth, plan_date_differences,
    plan_durations, plan_phase_activity, plan_phase_differences, report, report_dates, severity,
    step_for, summarise,
};
use radical_core::kde::report_phases;

pub fn draws(v: &Value) -> Draws {
    v.as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), nums(v)))
        .collect()
}

fn pair(v: &Value) -> [f64; 2] {
    [num(&v[0]), num(&v[1])]
}

pub fn check_years(c: &mut Closeness, label: &str, got: &YearsSummary, want: &Value, rel: f64) {
    c.check(&format!("{label}.mean"), got.mean, num(&want["mean"]), rel);
    c.check(
        &format!("{label}.median"),
        got.median,
        num(&want["median"]),
        rel,
    );
    c.check(&format!("{label}.std"), got.std, num(&want["std"]), rel);
    same(&format!("{label}.step"), got.step, num(&want["step"]));
    for (field, g) in [("hpd_68", got.hpd_68), ("hpd_95", got.hpd_95)] {
        match g {
            None => assert!(want[field].is_null(), "{label}.{field}"),
            Some(g) => {
                let w = pair(&want[field]);
                same(&format!("{label}.{field}[0]"), g[0], w[0]);
                same(&format!("{label}.{field}[1]"), g[1], w[1]);
            }
        }
    }
    check_ranges(
        c,
        &format!("{label}.hpd_68_ranges"),
        &got.hpd_68_ranges,
        &want["hpd_68_ranges"],
    );
    check_ranges(
        c,
        &format!("{label}.hpd_95_ranges"),
        &got.hpd_95_ranges,
        &want["hpd_95_ranges"],
    );
    let series = want["distribution"].as_array().unwrap();
    assert_eq!(
        got.distribution.len(),
        series.len(),
        "{label}: distribution"
    );
    for (k, (g, w)) in got.distribution.iter().zip(series).enumerate() {
        same(&format!("{label}.distribution[{k}].x"), g[0], num(&w[0]));
        c.check(
            &format!("{label}.distribution[{k}].y"),
            g[1],
            num(&w[1]),
            rel,
        );
    }
}

fn check_duration(c: &mut Closeness, label: &str, got: &Duration, want: &Value, rel: f64) {
    assert_eq!(got.kind.as_str(), want["kind"], "{label}");
    assert_eq!(got.id, want["id"], "{label}");
    assert_eq!(got.name, want["name"], "{label}");
    assert_eq!(
        got.element_id.as_deref(),
        want["element_id"].as_str(),
        "{label}"
    );
    assert_eq!(
        got.dates.map(|d| d as u64),
        want["dates"].as_u64(),
        "{label}"
    );
    check_years(
        c,
        &format!("{label}/{}", got.id),
        &got.years,
        &want["years"],
        rel,
    );
}

fn check_difference(c: &mut Closeness, label: &str, got: &Difference, want: &Value, rel: f64) {
    let label = format!("{label}/{}", got.id);
    assert_eq!(got.kind.as_str(), want["kind"], "{label}");
    assert_eq!(got.id, want["id"], "{label}");
    assert_eq!(got.from_id, want["from_id"], "{label}");
    assert_eq!(got.from_name, want["from_name"], "{label}");
    assert_eq!(got.to_id, want["to_id"], "{label}");
    assert_eq!(got.to_name, want["to_name"], "{label}");
    c.check(
        &format!("{label}.overlap"),
        got.overlap,
        num(&want["overlap"]),
        rel,
    );
    assert_eq!(got.severity, want["severity"], "{label}");
    match got.older_probability {
        None => assert!(want.get("older_probability").is_none(), "{label}"),
        Some(p) => c.check(
            &format!("{label}.older_probability"),
            p,
            num(&want["older_probability"]),
            rel,
        ),
    }
    assert_eq!(
        got.from_dates.map(|d| d as u64),
        want.get("from_dates").and_then(Value::as_u64),
        "{label}"
    );
    assert_eq!(
        got.to_dates.map(|d| d as u64),
        want.get("to_dates").and_then(Value::as_u64),
        "{label}"
    );
    check_years(c, &label, &got.years, &want["years"], rel);
}

fn check_band(c: &mut Closeness, label: &str, got: &CalendarBand, want: &Value, rel: f64) {
    c.check(
        &format!("{label}.median"),
        got.median,
        num(&want["median"]),
        rel,
    );
    c.check(&format!("{label}.mean"), got.mean, num(&want["mean"]), rel);
    for (field, g) in [("band_68", got.band_68), ("band_95", got.band_95)] {
        let w = pair(&want[field]);
        c.check(&format!("{label}.{field}[0]"), g[0], w[0], rel);
        c.check(&format!("{label}.{field}[1]"), g[1], w[1], rel);
    }
}

fn check_age_depth(c: &mut Closeness, label: &str, got: &[AgeDepth], want: &Value, rel: f64) {
    let want = want.as_array().unwrap();
    assert_eq!(got.len(), want.len(), "{label}: age-depth records");
    for (g, w) in got.iter().zip(want) {
        assert_eq!(g.element_id, w["element_id"], "{label}");
        assert_eq!(g.name, w["name"], "{label}");
        assert_eq!(g.depth_units, w["depth_units"], "{label}");
        let levels = w["levels"].as_array().unwrap();
        assert_eq!(g.levels.len(), levels.len(), "{label}");
        for (gl, wl) in g.levels.iter().zip(levels) {
            assert_eq!(gl.id, wl["id"], "{label}");
            assert_eq!(gl.name, wl["name"], "{label}");
            same(label, gl.depth, num(&wl["depth"]));
            assert_eq!(gl.dated, wl["dated"].as_bool().unwrap(), "{label}");
            check_band(c, &format!("{label}/{}", gl.id), &gl.band, wl, rel);
        }
        let acc = w["accumulation"].as_array().unwrap();
        assert_eq!(g.accumulation.len(), acc.len(), "{label}");
        for (ga, wa) in g.accumulation.iter().zip(acc) {
            same(label, ga.from_depth, num(&wa["from_depth"]));
            same(label, ga.to_depth, num(&wa["to_depth"]));
            assert_eq!(ga.from_name, wa["from_name"], "{label}");
            assert_eq!(ga.to_name, wa["to_name"], "{label}");
            same(label, ga.depth_gap, num(&wa["depth_gap"]));
            check_years(
                c,
                &format!("{label}/accumulation"),
                &ga.years,
                &wa["years"],
                rel,
            );
            c.check(
                &format!("{label}.rate_median"),
                ga.rate_median,
                num(&wa["rate_median"]),
                rel,
            );
            let band = pair(&wa["rate_band_68"]);
            c.check(
                &format!("{label}.rate_band_68[0]"),
                ga.rate_band_68[0],
                band[0],
                rel,
            );
            c.check(
                &format!("{label}.rate_band_68[1]"),
                ga.rate_band_68[1],
                band[1],
                rel,
            );
        }
    }
}

/// Phase activity's values are rounded to three decimals, so they match
/// exactly or, where a smoothed count sits on a rounding edge and the
/// kernel's sum is formed in another order, by 0.001.
fn check_activity(label: &str, got: &Option<PhaseActivity>, want: &Value) {
    let Some(got) = got else {
        assert!(want.is_null(), "{label}: expected phase activity");
        return;
    };
    same(&format!("{label}.start"), got.start, num(&want["start"]));
    same(&format!("{label}.step"), got.step, num(&want["step"]));
    let rows = want["rows"].as_array().unwrap();
    assert_eq!(got.rows.len(), rows.len(), "{label}: activity rows");
    for (g, w) in got.rows.iter().zip(rows) {
        assert_eq!(g.id, w["id"], "{label}");
        assert_eq!(g.name, w["name"], "{label}");
        assert_eq!(g.relative, w.get("relative").is_some(), "{label}");
        assert_eq!(
            g.dates.map(|d| d as u64),
            w.get("dates").and_then(Value::as_u64)
        );
        let values = nums(&w["values"]);
        assert_eq!(g.values.len(), values.len(), "{label}: columns");
        for (k, (a, b)) in g.values.iter().zip(&values).enumerate() {
            assert!(
                (a - b).abs() <= 1.0000001e-3,
                "{label}/{}[{k}]: {a} against {b}",
                g.id
            );
        }
    }
}

pub fn check_block(c: &mut Closeness, label: &str, got: &Block, want: &Value, rel: f64) {
    let durations = want["durations"].as_array().unwrap();
    assert_eq!(got.durations.len(), durations.len(), "{label}: durations");
    for (g, w) in got.durations.iter().zip(durations) {
        check_duration(c, label, g, w, rel);
    }
    let differences = want["differences"].as_array().unwrap();
    assert_eq!(
        got.differences.len(),
        differences.len(),
        "{label}: differences"
    );
    for (g, w) in got.differences.iter().zip(differences) {
        check_difference(c, label, g, w, rel);
    }
    check_age_depth(c, label, &got.age_depth, &want["age_depth"], rel);
    check_activity(label, &got.phase_activity, &want["phase_activity"]);
    assert_eq!(got.draws as u64, want["draws"].as_u64().unwrap(), "{label}");
    assert_eq!(got.independent, want["independent"].as_bool().unwrap());
    assert_eq!(got.note.as_deref(), want["note"].as_str(), "{label}");
    assert!(
        want["dispersal"].is_null(),
        "{label}: dispersal is not ported"
    );
}

fn side(prefix: &str, spec: &Value) -> Option<Side> {
    if let Some(dates) = spec.get(format!("{prefix}_dates")) {
        return Some(Side::Dates(strings(dates)));
    }
    spec.get(format!("{prefix}_boundaries"))
        .map(|b| Side::Boundaries(b[0].as_str().unwrap().into(), b[1].as_str().unwrap().into()))
}

fn check_difference_specs(label: &str, got: &[DifferenceSpec], want: &Value) {
    let want = want.as_array().unwrap();
    assert_eq!(got.len(), want.len(), "{label}: difference specs");
    for (g, w) in got.iter().zip(want) {
        assert_eq!(g.kind.as_str(), w["kind"], "{label}");
        assert_eq!(g.id, w["id"], "{label}");
        assert_eq!(g.from_id, w["from_id"], "{label}");
        assert_eq!(g.from_name, w["from_name"], "{label}");
        assert_eq!(g.to_id, w["to_id"], "{label}");
        assert_eq!(g.to_name, w["to_name"], "{label}");
        assert_eq!(g.from, side("from", w), "{label}");
        assert_eq!(g.to, side("to", w), "{label}");
    }
}

#[test]
fn intervals() {
    let mut c = Closeness::default();
    for case in family("intervals").cases {
        let name = case.name.clone();
        let inputs = case.expanded_inputs();
        let want = outputs(&case);
        if let Some(rest) = name.strip_prefix("report/") {
            let root = tree(&inputs["tree"]);
            let dates = measurements(&inputs["dates"]);
            let joint = draws(&inputs["draws"]);
            let step = opt_num(&inputs["step"]);
            if rest == "deposition/no-durations" {
                let got = report(root.as_ref(), &dates, &joint, false, false, None, step);
                check_block(&mut c, &name, &got, &want, REL);
                continue;
            }
            let specs = plan_durations(root.as_ref(), &dates);
            let plan = want["durations_plan"].as_array().unwrap();
            assert_eq!(specs.len(), plan.len(), "{name}: duration specs");
            for (g, w) in specs.iter().zip(plan) {
                assert_eq!(g.kind.as_str(), w["kind"], "{name}");
                assert_eq!(g.id, w["id"], "{name}");
                assert_eq!(g.name, w["name"], "{name}");
                assert_eq!(g.element_id.as_deref(), w["element_id"].as_str(), "{name}");
                let boundaries = strings(&w["boundaries"]);
                assert_eq!(
                    g.boundaries
                        .clone()
                        .map(|(a, b)| vec![a, b])
                        .unwrap_or_default(),
                    boundaries,
                    "{name}: {}",
                    g.id
                );
                assert_eq!(g.dates, strings(&w["dates"]), "{name}");
            }
            let (differences, note) = plan_phase_differences(root.as_ref(), &dates);
            check_difference_specs(&name, &differences, &want["differences_plan"]);
            assert_eq!(note.as_deref(), want["note"].as_str(), "{name}");
            let rows = plan_phase_activity(root.as_ref(), &dates);
            let want_rows = want["activity_plan"].as_array().unwrap();
            assert_eq!(rows.len(), want_rows.len(), "{name}: activity rows");
            for (g, w) in rows.iter().zip(want_rows) {
                assert_eq!(g.id, w["id"], "{name}");
                assert_eq!(g.name, w["name"], "{name}");
                assert_eq!(Some(g.side.clone()), side("", &json_side(w)), "{name}");
            }
            let age_depth = plan_age_depth(root.as_ref(), &dates);
            let want_ad = want["age_depth_plan"].as_array().unwrap();
            assert_eq!(age_depth.len(), want_ad.len(), "{name}");
            for (g, w) in age_depth.iter().zip(want_ad) {
                assert_eq!(g.element_id, w["element_id"], "{name}");
                let levels = w["levels"].as_array().unwrap();
                for (gl, wl) in g.levels.iter().zip(levels) {
                    assert_eq!(gl.id, wl["id"], "{name}");
                    assert_eq!(gl.name, wl["name"], "{name}");
                    assert_eq!(gl.dated, wl["dated"].as_bool().unwrap(), "{name}");
                }
            }

            let got = report(root.as_ref(), &dates, &joint, true, true, None, step);
            check_block(&mut c, &name, &got, &want["report"], REL);

            let phases =
                report_phases(root.as_ref(), &dates, &joint, num(&inputs["grid_step"]), 42);
            let want_phases = want["phases"].as_array().unwrap();
            assert_eq!(phases.len(), want_phases.len(), "{name}: phase KDEs");
            for (g, w) in phases.iter().zip(want_phases) {
                assert_eq!(g.element_id.as_deref(), w["element_id"].as_str(), "{name}");
                assert_eq!(g.name, w["name"], "{name}");
                crate::kde::check_summary(
                    &mut c,
                    &format!("{name}/kde/{}", g.name),
                    Some(g.summary.clone()),
                    w,
                );
            }
        } else if name.starts_with("summarise/") {
            let values = nums(&inputs["values"]);
            let base = opt_num(&inputs["base"]);
            match (summarise(&values, base), want["summary"].is_null()) {
                (None, true) => {}
                (Some(got), false) => check_years(&mut c, &name, &got, &want["summary"], REL),
                (got, _) => panic!("{name}: {got:?} against {}", want["summary"]),
            }
            if let Some(step) = opt_num(&want["step"]) {
                let finite: Vec<f64> = values.into_iter().filter(|v| v.is_finite()).collect();
                same(&name, step_for(&finite, base), step);
            }
        } else if name == "severity" {
            let words: Vec<&str> = nums(&inputs["overlaps"])
                .into_iter()
                .map(severity)
                .collect();
            assert_eq!(words, strings(&want["words"]));
        } else if name == "report_dates" {
            let named: Vec<NamedDate> = inputs["dates"]
                .as_array()
                .unwrap()
                .iter()
                .map(|d| NamedDate {
                    id: d["id"].as_str().unwrap().into(),
                    name: d["name"].as_str().unwrap().into(),
                })
                .collect();
            let got = report_dates(&named, &draws(&inputs["draws"]), None);
            check_block(&mut c, &name, &got, &want, REL);
        } else if name == "plan_date_differences/many" {
            let named: Vec<NamedDate> = inputs["dates"]
                .as_array()
                .unwrap()
                .iter()
                .map(|d| NamedDate {
                    id: d["id"].as_str().unwrap().into(),
                    name: d["name"].as_str().unwrap().into(),
                })
                .collect();
            let (specs, note) = plan_date_differences(&named);
            assert_eq!(specs.len(), want["specs"].as_array().unwrap().len());
            assert_eq!(note.as_deref(), want["note"].as_str());
        } else if name == "calendar_band" {
            check_band(
                &mut c,
                &name,
                &calendar_band(&nums(&inputs["values"])),
                &want,
                REL,
            );
        } else {
            panic!("unhandled case {name}");
        }
    }
    c.report("intervals");
}

/// An activity row's side under the empty prefix [`side`] reads.
fn json_side(row: &Value) -> Value {
    let mut out = serde_json::Map::new();
    if let Some(d) = row.get("dates") {
        out.insert("_dates".into(), d.clone());
    }
    if let Some(b) = row.get("boundaries") {
        out.insert("_boundaries".into(), b.clone());
    }
    Value::Object(out)
}
