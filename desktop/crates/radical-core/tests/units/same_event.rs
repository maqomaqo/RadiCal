use crate::*;
use radical_core::Mode;
use radical_core::statistics::{Date, same_event};

#[test]
fn same_event_test() {
    let mut c = Closeness::default();
    for case in family("same_event").cases {
        let dates: Vec<Date> = case.inputs["measurements"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| Date {
                age_bp: num(&m["age_bp"]),
                error_1sigma: num(&m["error_1sigma"]),
                curve_id: m["curve_id"].as_str().map(str::to_owned),
                delta_r: opt(&m["delta_r"]),
                delta_r_error: opt(&m["delta_r_error"]),
                ..Date::default()
            })
            .collect();
        let got = same_event(&dates, radical_core::curves::builtin, Mode::Standard);
        let want = outputs(&case);
        let name = &case.name;
        let Some(got) = got else {
            assert!(want.is_null(), "{name}: no test, Python had one");
            continue;
        };
        assert_eq!(got.n as u64, want["n"].as_u64().unwrap(), "{name}");
        assert_eq!(got.df as u64, want["df"].as_u64().unwrap(), "{name}");
        same(&format!("{name}/level"), got.level, num(&want["level"]));
        assert_eq!(got.refused, want["refused"].as_str(), "{name}");
        assert_eq!(got.consistent, want["consistent"].as_bool(), "{name}");
        for (field, value) in [
            ("mean_bp", got.mean_bp),
            ("error_bp", got.error_bp),
            ("t", got.t),
            ("critical", got.critical),
        ] {
            let expected = opt(&want[field]);
            assert_eq!(value.is_some(), expected.is_some(), "{name}/{field}");
            if let (Some(g), Some(w)) = (value, expected) {
                c.check(&format!("{name}/{field}"), g, w, REL);
            }
        }
        match (&got.pooled_hpd_ranges, want["pooled_hpd_ranges"].as_array()) {
            (None, None) => {}
            (Some(levels), Some(expected)) => {
                assert_eq!(levels.len(), expected.len(), "{name}");
                for (level, w) in levels.iter().zip(expected) {
                    same(
                        &format!("{name}/confidence"),
                        level.confidence,
                        num(&w["confidence"]),
                    );
                    let label = format!("{name}/pooled/{}", level.confidence);
                    check_ranges(&mut c, &label, &level.ranges, &w["ranges"]);
                }
            }
            (g, w) => panic!("{name}: pooled ranges {g:?}, Python had {w:?}"),
        }
    }
    c.report("same_event");
}
