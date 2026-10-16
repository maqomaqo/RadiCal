use crate::*;

#[test]
fn interpolation() {
    let mut c = Closeness::default();
    for case in family("interpolation").cases {
        let curve = radical_core::get_curve(case.inputs["curve"].as_str().unwrap()).unwrap();
        let at = nums(&case.inputs["cal_age"]);
        let (c14, err) = if case.name.starts_with("interpolate_cubic/") {
            curve.interpolate_cubic(&at).unwrap()
        } else {
            curve.interpolate(&at).unwrap()
        };
        let want = outputs(&case);
        c.check_all(
            &format!("{}/c14_age", case.name),
            &c14,
            &nums(&want["c14_age"]),
            REL,
        );
        c.check_all(
            &format!("{}/c14_error", case.name),
            &err,
            &nums(&want["c14_error"]),
            REL,
        );
    }
    c.report("interpolation");
}
