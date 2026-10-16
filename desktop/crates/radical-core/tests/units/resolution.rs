use crate::*;
use radical_core::resolution::{profile, uncertainty_at};

#[test]
fn resolution() {
    let mut c = Closeness::default();
    for case in family("resolution").cases {
        let i = &case.inputs;
        let want = outputs(&case);
        let name = &case.name;
        if name == "uncertainty_at" {
            let got: Vec<f64> = nums(&i["c14_age"])
                .iter()
                .map(|&a| uncertainty_at(a, num(&i["u0"])))
                .collect();
            c.check_all(name, &got, &nums(&want["uncertainty"]), REL);
            continue;
        }
        let curve = radical_core::get_curve(i["curve"].as_str().unwrap()).unwrap();
        let got = profile(
            curve,
            opt(&i["cal_from"]),
            opt(&i["cal_to"]),
            num(&i["u0"]),
            i["stride"].as_u64().unwrap() as usize,
        )
        .unwrap();
        same(&format!("{name}/u0"), got.u0, num(&want["u0"]));
        let points = want["points"].as_array().unwrap();
        assert_eq!(got.cal_ages.len(), points.len(), "{name}");
        for (k, point) in points.iter().enumerate() {
            let p = nums(point);
            let label = format!("{name}/points[{k}]");
            same(&format!("{label}.cal"), got.cal_ages[k], p[0]);
            c.check(&format!("{label}.mean"), got.mean[k], p[1], REL);
            same(&format!("{label}.p05"), got.p05[k], p[2]);
            same(&format!("{label}.p95"), got.p95[k], p[3]);
            c.check(
                &format!("{label}.uncertainty"),
                got.uncertainty[k],
                p[4],
                REL,
            );
        }
    }
    c.report("resolution");
}
