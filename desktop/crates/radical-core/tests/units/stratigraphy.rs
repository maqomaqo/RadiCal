use crate::model::{element_json, group, measurements, opt_num, relationship, strings};
use crate::*;
use radical_core::model_tree::migrate_from_relationships;
use radical_core::stratigraphy::{
    effective_bounds, expand_relationships, find_cycle, group_members,
};

fn pairs(v: &Value) -> Vec<(String, String)> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|p| (p[0].as_str().unwrap().into(), p[1].as_str().unwrap().into()))
        .collect()
}

#[test]
fn stratigraphy() {
    for case in family("stratigraphy").cases {
        let name = case.name.clone();
        let inputs = case.expanded_inputs();
        let want = outputs(&case);
        match name.as_str() {
            "group_members" => {
                let got = group_members(
                    &measurements(&inputs["dates"]),
                    &inputs["groups"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(group)
                        .collect::<Vec<_>>(),
                );
                let expected: Vec<(String, Vec<String>)> = want["members"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|m| (m[0].as_str().unwrap().into(), strings(&m[1])))
                    .collect();
                assert_eq!(got, expected);
            }
            "effective_bounds" => {
                for (c, w) in inputs["cases"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .zip(want["bounds"].as_array().unwrap())
                {
                    let g = (!c[2].is_null()).then(|| (opt_num(&c[2][1]), opt_num(&c[2][0])));
                    let got = effective_bounds((opt_num(&c[1]), opt_num(&c[0])), g);
                    assert_eq!(got, (opt_num(&w[0]), opt_num(&w[1])), "{c}");
                }
            }
            _ => {
                let dates = measurements(&inputs["dates"]);
                let groups: Vec<_> = inputs["groups"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(group)
                    .collect();
                let rels: Vec<_> = inputs["relationships"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(relationship)
                    .collect();
                let (older, equal) = expand_relationships(&dates, &groups, &rels);
                assert_eq!(older, pairs(&want["older_younger"]), "{name}");
                assert_eq!(equal, pairs(&want["equal"]), "{name}");
                assert_eq!(
                    find_cycle(&older),
                    (!want["cycle"].is_null()).then(|| strings(&want["cycle"])),
                    "{name}"
                );
                let mut counter = 0;
                let migrated = migrate_from_relationships(&dates, &groups, &rels, || {
                    counter += 1;
                    format!("gen{}", counter - 1)
                });
                let w = &want["migrated"];
                assert_eq!(element_json(&migrated.root), w["root"], "{name}: tree");
                assert_eq!(
                    migrated.linearized,
                    w["linearized"].as_bool().unwrap(),
                    "{name}"
                );
                let boundaries: Vec<(String, (String, String))> = w["group_boundaries"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|b| {
                        (
                            b[0].as_str().unwrap().into(),
                            (
                                b[1][0].as_str().unwrap().into(),
                                b[1][1].as_str().unwrap().into(),
                            ),
                        )
                    })
                    .collect();
                assert_eq!(migrated.group_boundaries, boundaries, "{name}");
            }
        }
    }
}
