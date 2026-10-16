use crate::*;

#[test]
fn f14c() {
    let mut c = Closeness::default();
    for case in family("f14c").cases {
        let ages = nums(&case.inputs["age_bp"]);
        let got: Vec<f64> = ages.iter().map(|&a| radical_core::f14c(a)).collect();
        c.check_all(&case.name, &got, &nums(&outputs(&case)["f14c"]), REL);
    }
    c.report("f14c");
}
