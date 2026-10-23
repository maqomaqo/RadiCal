use crate::*;
use radical_core::reservoir::{
    Combination, LookupOptions, ReservoirPoint, combine, haversine_km, lookup, nearest,
};

fn ints(v: &Value) -> Vec<i64> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_i64().unwrap())
        .collect()
}

fn check_combination(c: &mut Closeness, name: &str, got: &Combination, want: &Value) {
    assert_eq!(got.n as u64, want["n"].as_u64().unwrap(), "{name}");
    assert_eq!(got.map_nos, ints(&want["map_nos"]), "{name}");
    assert_eq!(
        got.excluded_no_error,
        ints(&want["excluded_no_error"]),
        "{name}"
    );
    for (field, value) in [
        ("delta_r", got.delta_r),
        ("delta_r_error", got.delta_r_error),
        ("internal", got.internal),
        ("scatter", got.scatter),
    ] {
        let expected = opt(&want[field]);
        assert_eq!(value.is_some(), expected.is_some(), "{name}/{field}");
        if let (Some(g), Some(w)) = (value, expected) {
            c.check(&format!("{name}/{field}"), g, w, REL);
        }
    }
}

/// The made-up coast the lookup cases share.
fn coast(v: &Value) -> Vec<ReservoirPoint> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|r| ReservoirPoint {
            map_no: r[0].as_i64().unwrap(),
            latitude: num(&r[1]),
            longitude: num(&r[2]),
            delta_r: opt(&r[3]),
            delta_r_error: opt(&r[4]),
            feeding: r[5].as_str().map(str::to_owned),
            feeding_raw: r[6].as_str().map(str::to_owned),
            locality: Some(format!("Point {}", r[0])),
            reference: Some("Made up".to_owned()),
            distance_km: None,
        })
        .collect()
}

fn check_lookup(c: &mut Closeness, name: &str, inputs: &Value, want: &Value) {
    let points = coast(&inputs["points"]);
    let at = nums(&inputs["at"]);
    let o = &inputs["options"];
    let options = LookupOptions {
        n: o["n"].as_u64().unwrap() as usize,
        snapshot_id: Some("snap-1".to_owned()),
        max_km: opt(&o["max_km"]),
        exclude_map_nos: o.get("exclude_map_nos").map(ints).unwrap_or_default(),
        suspension_only: o["suspension_only"].as_bool().unwrap_or(false),
    };
    let got = lookup(at[0], at[1], &points, &options);
    if let Some(refused) = want["refused"].as_str() {
        assert_eq!(got.err().as_deref(), Some(refused), "{name}");
        return;
    }
    let got = got.unwrap_or_else(|e| panic!("{name}: refused: {e}"));
    let w = &want["lookup"];
    assert_eq!(got.n as u64, w["n"].as_u64().unwrap(), "{name}");
    for (field, value) in [
        ("delta_r", got.delta_r),
        ("delta_r_error", got.delta_r_error),
        ("nearest_km", got.nearest_km),
        ("farthest_km", got.farthest_km),
        ("feeding_shift", got.feeding_shift()),
    ] {
        let expected = opt(&w[field]);
        assert_eq!(value.is_some(), expected.is_some(), "{name}/{field}");
        if let (Some(g), Some(e)) = (value, expected) {
            c.check(&format!("{name}/{field}"), g, e, REL);
        }
    }
    assert_eq!(
        got.feeding_shift_matters(),
        w["feeding_shift_matters"].as_bool().unwrap(),
        "{name}"
    );
    assert_eq!(
        got.excluded_no_error,
        ints(&w["excluded_no_error"]),
        "{name}"
    );
    assert_eq!(got.excluded_by_user, ints(&w["excluded_by_user"]), "{name}");
    let counts: Vec<(String, u64)> = w["feeding"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.as_u64().unwrap()))
        .collect();
    let mut got_counts: Vec<(String, u64)> = got
        .feeding_counts
        .iter()
        .map(|(k, v)| (k.clone(), *v as u64))
        .collect();
    got_counts.sort();
    assert_eq!(got_counts, counts, "{name}: feeding");
    let raw: Vec<(String, String)> = w["feeding_raw"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_owned()))
        .collect();
    let mut got_raw: Vec<(String, String)> = got
        .feeding_raw
        .iter()
        .map(|(k, v)| (k.to_string(), v.clone()))
        .collect();
    got_raw.sort();
    assert_eq!(got_raw, raw, "{name}: feeding_raw");
    check_combination(
        c,
        &format!("{name}/all"),
        &got.included_all,
        &w["included_all"],
    );
    match (&got.suspension_equivalent, &w["suspension_equivalent"]) {
        (None, Value::Null) => {}
        (Some(s), want) if !want.is_null() => {
            check_combination(c, &format!("{name}/suspension"), s, want);
        }
        (g, w) => panic!("{name}: suspension {g:?} against {w}"),
    }
    let points = w["points"].as_array().unwrap();
    assert_eq!(got.points.len(), points.len(), "{name}: points");
    for (g, p) in got.points.iter().zip(points) {
        assert_eq!(g.map_no, p["map_no"].as_i64().unwrap(), "{name}");
        c.check(
            &format!("{name}/point {}", g.map_no),
            g.distance_km.unwrap(),
            num(&p["distance_km"]),
            REL,
        );
    }
    assert_eq!(
        got.included_all.map_nos,
        ints(&want["provenance"]["map_nos"]),
        "{name}: provenance"
    );
}

#[test]
fn reservoir() {
    let mut c = Closeness::default();
    for case in family("reservoir").cases {
        let want = outputs(&case);
        let name = &case.name;
        if name == "haversine_km" {
            let got: Vec<f64> = case.inputs["pairs"]
                .as_array()
                .unwrap()
                .iter()
                .map(|p| {
                    let p = nums(p);
                    haversine_km(p[0], p[1], p[2], p[3])
                })
                .collect();
            c.check_all(name, &got, &nums(&want["km"]), REL);
            continue;
        }
        if name == "nearest" {
            let at = nums(&case.inputs["at"]);
            let n = case.inputs["n"].as_u64().unwrap() as usize;
            let got = nearest(at[0], at[1], &coast(&case.inputs["points"]), n, None);
            let want = want.as_array().unwrap();
            assert_eq!(got.len(), want.len(), "{name}");
            for (g, w) in got.iter().zip(want) {
                assert_eq!(g.map_no, w[0].as_i64().unwrap(), "{name}");
                c.check(name, g.distance_km.unwrap(), num(&w[1]), REL);
            }
            continue;
        }
        if name.starts_with("lookup/") {
            check_lookup(&mut c, name, &case.inputs, &want);
            continue;
        }
        let points: Vec<ReservoirPoint> = case.inputs["points"]
            .as_array()
            .unwrap()
            .iter()
            .map(|row| ReservoirPoint {
                map_no: row[0].as_i64().unwrap(),
                latitude: 0.0,
                longitude: 0.0,
                delta_r: opt(&row[1]),
                delta_r_error: opt(&row[2]),
                ..ReservoirPoint::default()
            })
            .collect();
        let got = combine(&points);
        check_combination(&mut c, name, &got, &want);
    }
    c.report("reservoir");
}
