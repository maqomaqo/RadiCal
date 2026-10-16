use crate::*;
use radical_core::reservoir::{ReservoirPoint, combine, haversine_km};

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
            })
            .collect();
        let got = combine(&points);
        let ints = |v: &Value| -> Vec<i64> {
            v.as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_i64().unwrap())
                .collect()
        };
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
    c.report("reservoir");
}
