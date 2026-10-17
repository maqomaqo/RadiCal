use crate::model::{element_json, measurements, opt_num, strings, tree};
use crate::*;
use radical_core::model_tree::{
    FlatRow, ModelPlan, Param, TermKind, element_label, element_params, flatten_structure,
    inherited_bounds, iter_elements, plan_model, standalone_wiggle_match, validate_ordinates,
    validate_tree,
};

fn terms(v: &Value) -> Vec<(TermKind, String)> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|t| {
            let kind = match t[0].as_str().unwrap() {
                "date" => TermKind::Date,
                "boundary" => TermKind::Boundary,
                other => panic!("unknown term kind {other}"),
            };
            (kind, t[1].as_str().unwrap().to_owned())
        })
        .collect()
}

fn check_plan(name: &str, got: &ModelPlan, want: &Value) {
    let ordering: Vec<_> = want["ordering"]
        .as_array()
        .unwrap()
        .iter()
        .map(|o| (terms(&o[0]), terms(&o[1])))
        .collect();
    assert_eq!(got.ordering, ordering, "{name}: ordering");
    let groups = want["span_groups"].as_array().unwrap();
    assert_eq!(got.span_groups.len(), groups.len(), "{name}: span groups");
    for (g, w) in got.span_groups.iter().zip(groups) {
        assert_eq!(g.older_boundary_id, w["older"], "{name}");
        assert_eq!(g.younger_boundary_id, w["younger"], "{name}");
        assert_eq!(g.date_ids, strings(&w["dates"]), "{name}");
        assert_eq!(g.contained_ids, strings(&w["contained"]), "{name}");
    }
    assert_eq!(
        got.marginalized_boundaries
            .iter()
            .cloned()
            .collect::<Vec<_>>(),
        strings(&want["marginalized"]),
        "{name}: marginalized"
    );
    assert_eq!(got.sampled_boundaries, strings(&want["sampled"]), "{name}");
    let names: Vec<(String, String)> = want["boundary_names"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| (p[0].as_str().unwrap().into(), p[1].as_str().unwrap().into()))
        .collect();
    assert_eq!(got.boundary_names, names, "{name}: boundary names");

    let wiggles = want["wiggle_groups"].as_array().unwrap();
    assert_eq!(got.wiggle_groups.len(), wiggles.len(), "{name}");
    for (g, w) in got.wiggle_groups.iter().zip(wiggles) {
        assert_eq!(g.element_id, w["element_id"], "{name}");
        assert_eq!(g.name, w["name"], "{name}");
        assert_eq!(g.gap_units, w["gap_units"], "{name}");
        assert_eq!(
            g.reference_sample_id.as_deref(),
            w["reference_sample_id"].as_str()
        );
        let members: Vec<(String, f64, f64)> = w["members"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| (m[0].as_str().unwrap().into(), num(&m[1]), num(&m[2])))
            .collect();
        assert_eq!(g.members, members, "{name}: members");
    }
    let deposits = want["deposition_groups"].as_array().unwrap();
    assert_eq!(got.deposition_groups.len(), deposits.len(), "{name}");
    for (g, w) in got.deposition_groups.iter().zip(deposits) {
        assert_eq!(g.element_id, w["element_id"], "{name}");
        assert_eq!(g.name, w["name"], "{name}");
        assert_eq!(g.k, opt_num(&w["k"]), "{name}");
        assert_eq!(g.k_variable, w["k_variable"].as_bool().unwrap(), "{name}");
        assert_eq!(g.log_k_min, num(&w["log_k_min"]), "{name}");
        assert_eq!(g.log_k_max, num(&w["log_k_max"]), "{name}");
        assert_eq!(g.depth_units, w["depth_units"], "{name}");
        let levels: Vec<(String, f64, bool)> = w["levels"]
            .as_array()
            .unwrap()
            .iter()
            .map(|l| {
                (
                    l[0].as_str().unwrap().into(),
                    num(&l[1]),
                    l[2].as_bool().unwrap(),
                )
            })
            .collect();
        assert_eq!(g.levels, levels, "{name}: levels");
        let names = w["level_names"].as_object().unwrap();
        assert_eq!(g.level_names.len(), names.len(), "{name}");
        for (k, v) in names {
            assert_eq!(g.level_names[k], v.as_str().unwrap(), "{name}");
        }
    }
}

fn param_json(p: &Param) -> Value {
    match p {
        Param::Null => Value::Null,
        Param::Bool(b) => Value::from(*b),
        Param::Number(v) => Value::from(*v),
        Param::Text(s) => Value::from(s.clone()),
    }
}

fn check_flattened(name: &str, got: &[FlatRow], want: &Value) {
    let want = want.as_array().unwrap();
    assert_eq!(got.len(), want.len(), "{name}: rows");
    for (g, w) in got.iter().zip(want) {
        assert_eq!(g.kind, w["kind"], "{name}");
        assert_eq!(g.element_id.as_deref(), w["element_id"].as_str(), "{name}");
        assert_eq!(g.name, w["name"], "{name}");
        assert_eq!(g.depth as u64, w["depth"].as_u64().unwrap(), "{name}");
        assert_eq!(g.path, w["path"], "{name}");
        assert_eq!(g.tpq_age_bp, opt_num(&w["tpq_age_bp"]), "{name}");
        assert_eq!(g.taq_age_bp, opt_num(&w["taq_age_bp"]), "{name}");
        assert_eq!(g.sample.map(|s| s as u64), w["sample"].as_u64(), "{name}");
    }
}

fn expect_error(name: &str, got: Result<(), radical_core::Error>, case: &radical_corpus::Case) {
    match (&case.error, got) {
        (Some(error), Err(e)) => assert_eq!(e.to_string(), error["message"], "{name}"),
        (None, Ok(())) => {}
        (Some(error), Ok(())) => panic!("{name}: Python refused it: {}", error["message"]),
        (None, Err(e)) => panic!("{name}: refused, Python did not: {e}"),
    }
}

#[test]
fn model_tree() {
    for case in family("model_tree").cases {
        let name = case.name.clone();
        let inputs = case.expanded_inputs();
        let root = tree(&inputs["tree"]);
        let dates = measurements(&inputs["dates"]);
        if let Some(kind) = name.strip_prefix("validate_tree/") {
            expect_error(kind, validate_tree(root.as_ref(), &dates), &case);
            continue;
        }
        if let Some(kind) = name.strip_prefix("validate_ordinates/") {
            expect_error(kind, validate_ordinates(root.as_ref(), &dates), &case);
            continue;
        }
        let want = outputs(&case);
        if name == "flatten/orphans" {
            check_flattened(
                &name,
                &flatten_structure(root.as_ref(), &dates),
                &want["flattened"],
            );
            continue;
        }
        let root = root.unwrap();
        validate_tree(Some(&root), &dates).unwrap();
        validate_ordinates(Some(&root), &dates).unwrap();
        check_plan(&name, &plan_model(&root, &dates), &want["plan"]);

        let elements = iter_elements(&root);
        let ids: Vec<&str> = elements.iter().map(|e| e.id.as_str()).collect();
        assert_eq!(ids, strings(&want["elements"]), "{name}");
        let labels: Vec<String> = elements.iter().map(|e| element_label(e)).collect();
        assert_eq!(labels, strings(&want["labels"]), "{name}");
        for (e, w) in elements.iter().zip(want["params"].as_array().unwrap()) {
            let got: serde_json::Map<String, Value> = element_params(e)
                .iter()
                .map(|(k, v)| (k.clone(), param_json(v)))
                .collect();
            assert_eq!(&Value::Object(got), w, "{name}: params of {}", e.id);
        }
        let bounds = inherited_bounds(&root);
        for (e, w) in elements
            .iter()
            .zip(want["inherited_bounds"].as_array().unwrap())
        {
            assert_eq!(
                bounds[&e.id],
                (opt_num(&w[0]), opt_num(&w[1])),
                "{name}: bounds of {}",
                e.id
            );
        }
        assert_eq!(
            standalone_wiggle_match(Some(&root), &dates).map(|e| e.id.as_str()),
            want["standalone_wiggle_match"].as_str(),
            "{name}"
        );
        check_flattened(
            &name,
            &flatten_structure(Some(&root), &dates),
            &want["flattened"],
        );
        // The converters round-trip, so the trees above are the generator's.
        assert_eq!(element_json(&root)["id"], inputs["tree"]["id"]);
    }
}
