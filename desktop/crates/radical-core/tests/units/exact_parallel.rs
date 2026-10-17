use crate::exact_chain::{check_chain, options};
use crate::model::{measurements, strings, tree};
use crate::solved::solution;
use crate::*;
use radical_core::exact_parallel::{layout_with_reason, outside, solve, tied_cumsum, within};
use radical_core::matrix::Matrix;
use radical_core::measurement::Calibrator;

fn matrix(v: &Value) -> Matrix {
    let rows: Vec<Vec<f64>> = v.as_array().unwrap().iter().map(nums).collect();
    let (r, c) = (rows.len(), rows[0].len());
    Matrix::from_rows(r, c, rows.into_iter().flatten().collect())
}

#[test]
fn exact_parallel() {
    let mut c = Closeness::default();
    let cal = Calibrator::default();
    for case in family("exact_parallel").cases {
        let name = case.name.clone();
        let i = case.expanded_inputs();
        let want = outputs(&case);
        match name.split('/').next().unwrap() {
            "sums" => {
                let j = matrix(&i["matrix"]);
                for (field, got) in [
                    ("tied_cumsum_0", tied_cumsum(&j, 0)),
                    ("tied_cumsum_1", tied_cumsum(&j, 1)),
                    ("within", within(&j)),
                    ("outside", outside(&j)),
                ] {
                    c.check_all(
                        &format!("{name}/{field}"),
                        got.data(),
                        matrix(&want[field]).data(),
                        REL,
                    );
                }
            }
            "layout" => {
                let root = tree(&i["tree"]);
                let dates = measurements(&i["dates"]);
                let got = layout_with_reason(root.as_ref(), &dates);
                match (&got, &want["stages"]) {
                    (Ok(None), Value::Null) => assert!(want["reason"].is_null(), "{name}"),
                    (Err(reason), Value::Null) => {
                        assert_eq!(reason, want["reason"].as_str().unwrap(), "{name}")
                    }
                    (Ok(Some(stages)), w) if !w.is_null() => {
                        assert_eq!(stages.boundary_ids, strings(&w["boundary_ids"]), "{name}");
                        assert_eq!(stages.older_tail, strings(&w["older_tail"]), "{name}");
                        assert_eq!(stages.younger_tail, strings(&w["younger_tail"]), "{name}");
                        assert_eq!(
                            stages.all_boundary_ids(),
                            strings(&w["all_boundary_ids"]),
                            "{name}"
                        );
                        assert_eq!(
                            stages.num_groups() as u64,
                            w["num_groups"].as_u64().unwrap()
                        );
                        let ws = w["stages"].as_array().unwrap();
                        assert_eq!(stages.stages.len(), ws.len(), "{name}");
                        for (g, w) in stages.stages.iter().zip(ws) {
                            assert_eq!(g.older_id, w["older"], "{name}");
                            assert_eq!(g.younger_id, w["younger"], "{name}");
                            assert_eq!(g.dates, strings(&w["dates"]), "{name}");
                            let blocks: Vec<Vec<String>> = w["blocks"]
                                .as_array()
                                .unwrap()
                                .iter()
                                .map(strings)
                                .collect();
                            assert_eq!(g.blocks, blocks, "{name}");
                            let areas = w["areas"].as_array().unwrap();
                            assert_eq!(g.areas.len(), areas.len(), "{name}");
                            for (a, wa) in g.areas.iter().zip(areas) {
                                check_chain(&name, a, wa);
                            }
                        }
                    }
                    _ => panic!("{name}: {got:?} against {want}"),
                }
            }
            "solve" => {
                let root = tree(&i["tree"]).unwrap();
                let dates = measurements(&i["dates"]);
                let stages = layout_with_reason(Some(&root), &dates).unwrap().unwrap();
                let got = solve(&dates, &root, &stages, &options(&i["options"]), &cal).unwrap();
                solution(&mut c, &name, &got, &want);
            }
            other => panic!("unhandled case {other}"),
        }
    }
    c.report("exact_parallel");
}
