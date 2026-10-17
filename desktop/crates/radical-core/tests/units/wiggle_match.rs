use crate::model::{measurements, opt_num, tree};
use crate::solved::{REL_SOLVE, posterior};
use crate::*;
use radical_core::measurement::Calibrator;
use radical_core::model_tree::plan_model;
use radical_core::wiggle_match::{gap_kernel, solve};

#[test]
fn wiggle_match() {
    let mut c = Closeness::default();
    let cal = Calibrator::default();
    for case in family("wiggle_match").cases {
        let name = case.name.clone();
        let i = case.expanded_inputs();
        if let Some(rest) = name.strip_prefix("gap_kernel/") {
            let (step, sigma) = rest.split_once('/').unwrap();
            let got = gap_kernel(step.parse().unwrap(), sigma.parse().unwrap());
            c.check_all(&name, &got, &nums(&outputs(&case)["kernel"]), REL);
            continue;
        }
        let root = tree(&i["tree"]).unwrap();
        let dates = measurements(&i["dates"]);
        let group = plan_model(&root, &dates).wiggle_groups.remove(0);
        let got = solve(&dates, &group, Some(&root), 40, 42, 5.0, &cal);
        if let Some(error) = &case.error {
            assert_eq!(
                got.unwrap_err().to_string(),
                error["message"].as_str().unwrap(),
                "{name}"
            );
            continue;
        }
        let got = got.unwrap();
        let want = outputs(&case);
        posterior(
            &mut c,
            &format!("{name}/reference"),
            &got.reference,
            &want["reference"],
            REL_SOLVE,
        );
        let results = want["measurements"].as_array().unwrap();
        assert_eq!(got.measurements.len(), results.len(), "{name}");
        for ((id, g), w) in got.measurements.iter().zip(results) {
            assert_eq!(id, w[0].as_str().unwrap(), "{name}");
            posterior(&mut c, &format!("{name}/{id}"), g, &w[1], REL_SOLVE);
            let index = &w[1]["agreement_index"];
            match g.agreement_index {
                None => assert!(index.is_null(), "{name}"),
                Some(a) => c.check(&format!("{name}/{id}.agreement"), a, num(index), REL_SOLVE),
            }
        }
        match got.combination_agreement {
            None => assert!(want["combination_agreement"].is_null()),
            Some(a) => c.check(&name, a, num(&want["combination_agreement"]), REL_SOLVE),
        }
        same(
            &name,
            got.combination_threshold,
            num(&want["combination_threshold"]),
        );
        same(&name, got.series_span, num(&want["series_span"]));
        assert_eq!(got.gap_units, want["gap_units"].as_str().unwrap());
        assert_eq!(
            got.reference_sample_id.as_deref(),
            want["reference_sample_id"].as_str()
        );
        let grid = &want["grid"];
        assert_eq!(
            got.grid,
            (num(&grid["lower"]), num(&grid["upper"]), num(&grid["step"])),
            "{name}"
        );
        let members = want["members"].as_array().unwrap();
        for (g, w) in got.members.iter().zip(members) {
            assert_eq!(g.id, w["id"].as_str().unwrap(), "{name}");
            assert_eq!(g.name, w["name"].as_str().unwrap(), "{name}");
            same(&name, g.ring_offset, num(&w["ring_offset"]));
            assert_eq!(
                g.ring_offset_error,
                opt_num(&w["ring_offset_error"]),
                "{name}"
            );
        }
    }
    c.report("wiggle_match");
}
