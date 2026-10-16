use crate::*;
use radical_core::calibration::CalibrationCurve;
use radical_core::curve_view::{compare, to_csv, window};

fn small(v: &Value) -> CalibrationCurve {
    let cols = v.as_array().unwrap();
    CalibrationCurve::new("small", nums(&cols[0]), nums(&cols[1]), nums(&cols[2]))
}

/// Grid points and copied curve values exactly; the slope and the curves
/// read between grid points to `REL`; the CSV as text.
#[test]
fn curve_view() {
    let mut c = Closeness::default();
    for case in family("curve_view").cases {
        let i = &case.expanded_inputs();
        let name = case.name.as_str();
        let max_points = i["max_points"].as_u64().unwrap() as usize;
        if name.starts_with("window/") {
            let curve = radical_core::get_curve(i["curve"].as_str().unwrap()).unwrap();
            let got = window(curve, opt(&i["cal_from"]), opt(&i["cal_to"]), max_points).unwrap();
            let all = outputs(&case);
            let want = &all["window"];
            same(
                &format!("{name}/cal_from"),
                got.cal_from,
                num(&want["cal_from"]),
            );
            same(&format!("{name}/cal_to"), got.cal_to, num(&want["cal_to"]));
            same(
                &format!("{name}/curve_cal_min"),
                got.curve_cal_min,
                num(&want["curve_cal_min"]),
            );
            same(
                &format!("{name}/curve_cal_max"),
                got.curve_cal_max,
                num(&want["curve_cal_max"]),
            );
            assert_eq!(
                got.points_in_window as u64,
                want["points_in_window"].as_u64().unwrap(),
                "{name}"
            );
            assert_eq!(
                got.decimated,
                want["decimated"].as_bool().unwrap(),
                "{name}"
            );
            let points = want["points"].as_array().unwrap();
            assert_eq!(
                got.points.len() as u64,
                want["point_count"].as_u64().unwrap(),
                "{name}"
            );
            assert_eq!(got.points.len(), points.len(), "{name}");
            for (k, (g, w)) in got.points.iter().zip(points).enumerate() {
                let w = nums(w);
                for col in 0..3 {
                    same(&format!("{name}/points[{k}][{col}]"), g[col], w[col]);
                }
                c.check(&format!("{name}/points[{k}].slope"), g[3], w[3], REL);
            }
            assert_eq!(
                to_csv(&got, &curve.name),
                all["csv"].as_str().unwrap(),
                "{name}"
            );
            continue;
        }
        let (a, b) = if i.get("curve_a").is_some() {
            let curve = |k: &str| {
                radical_core::get_curve(i[k].as_str().unwrap())
                    .unwrap()
                    .clone()
            };
            (curve("curve_a"), curve("curve_b"))
        } else {
            (small(&i["a"]), small(&i["b"]))
        };
        let result = compare(&a, &b, opt(&i["cal_from"]), opt(&i["cal_to"]), max_points);
        if let Some(error) = &case.error {
            assert_eq!(
                result.unwrap_err().to_string(),
                error["message"].as_str().unwrap(),
                "{name}"
            );
            continue;
        }
        let got = result.unwrap();
        let want = outputs(&case);
        same(
            &format!("{name}/cal_from"),
            got.cal_from,
            num(&want["cal_from"]),
        );
        same(&format!("{name}/cal_to"), got.cal_to, num(&want["cal_to"]));
        same(
            &format!("{name}/overlap_cal_min"),
            got.overlap_cal_min,
            num(&want["overlap_cal_min"]),
        );
        same(
            &format!("{name}/overlap_cal_max"),
            got.overlap_cal_max,
            num(&want["overlap_cal_max"]),
        );
        assert_eq!(
            got.points_in_window as u64,
            want["points_in_window"].as_u64().unwrap(),
            "{name}"
        );
        assert_eq!(
            got.decimated,
            want["decimated"].as_bool().unwrap(),
            "{name}"
        );
        let points = want["points"].as_array().unwrap();
        assert_eq!(got.points.len(), points.len(), "{name}");
        for (k, (g, w)) in got.points.iter().zip(points).enumerate() {
            let w = nums(w);
            same(&format!("{name}/points[{k}].cal"), g[0], w[0]);
            for col in 1..7 {
                c.check(&format!("{name}/points[{k}][{col}]"), g[col], w[col], REL);
            }
        }
    }
    c.report("curve_view");
}
