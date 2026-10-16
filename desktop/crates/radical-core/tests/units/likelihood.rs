use crate::*;
use radical_core::Mode;

#[test]
fn likelihood() {
    let mut c = Closeness::default();
    for case in family("likelihood").cases {
        let i = case.expanded_inputs();
        let curve = radical_core::get_curve(i["curve"].as_str().unwrap()).unwrap();
        let mode = if i["match_oxcal"].as_bool().unwrap() {
            Mode::MatchOxcal
        } else {
            Mode::Standard
        };
        let got = curve
            .likelihood(
                &nums(&i["cal_ages"]),
                num(&i["age_bp"]),
                num(&i["error_1sigma"]),
                opt(&i["delta_r"]),
                opt(&i["delta_r_error"]),
                mode,
            )
            .unwrap();
        c.check_all(
            &case.name,
            &got,
            &nums(&outputs(&case)["likelihood"]),
            REL_LIKELIHOOD,
        );
    }
    c.report("likelihood");
}
