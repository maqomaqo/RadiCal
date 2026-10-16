use crate::*;
use radical_core::curve_mix::mix;

/// Every `step`-th point of each mixed curve, as the corpus keeps them.
#[test]
fn curve_mix() {
    let mut c = Closeness::default();
    for case in family("curve_mix").cases {
        let i = &case.inputs;
        let curve = |key: &str| radical_core::get_curve(i[key].as_str().unwrap()).unwrap();
        let result = mix(
            curve("curve_a"),
            curve("curve_b"),
            num(&i["percent_a"]),
            num(&i["percent_a_error"]),
            "Mixed",
        );
        let name = &case.name;
        if let Some(error) = &case.error {
            assert_eq!(
                result.unwrap_err().to_string(),
                error["message"].as_str().unwrap(),
                "{name}"
            );
            continue;
        }
        let mixed = result.unwrap();
        let want = outputs(&case);
        assert_eq!(
            mixed.len() as u64,
            want["length"].as_u64().unwrap(),
            "{name}"
        );
        let step = want["step"].as_u64().unwrap() as usize;
        let every = |v: &[f64]| v.iter().step_by(step).copied().collect::<Vec<_>>();
        for (k, (&g, &w)) in every(mixed.cal_ages())
            .iter()
            .zip(&nums(&want["cal_ages"]))
            .enumerate()
        {
            same(&format!("{name}/cal_ages[{k}]"), g, w);
        }
        c.check_all(
            &format!("{name}/c14_ages"),
            &every(mixed.c14_ages()),
            &nums(&want["c14_ages"]),
            REL,
        );
        c.check_all(
            &format!("{name}/c14_errors"),
            &every(mixed.c14_errors()),
            &nums(&want["c14_errors"]),
            REL,
        );
    }
    c.report("curve_mix");
}
