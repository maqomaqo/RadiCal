//! Corpus JSON to the model types: trees, dates, groups and relationships, as
//! the API's request bodies and the generator's hand-built models write them.

use radical_core::measurement::Measurement;
use radical_core::model_tree::{Element, Param, Params};
use radical_core::stratigraphy::{Group, Relationship};
use serde_json::Value;

use crate::num;

pub fn opt_num(v: &Value) -> Option<f64> {
    if v.is_null() { None } else { Some(num(v)) }
}

pub fn opt_str(v: &Value) -> Option<String> {
    v.as_str().map(str::to_owned)
}

fn param(v: &Value) -> Param {
    match v {
        Value::Null => Param::Null,
        Value::Bool(b) => Param::Bool(*b),
        Value::Number(n) => Param::Number(n.as_f64().unwrap()),
        Value::String(s) => Param::Text(s.clone()),
        other => panic!("not a param value: {other}"),
    }
}

pub fn element(v: &Value) -> Element {
    Element {
        id: v["id"].as_str().unwrap_or_default().to_owned(),
        element_type: v["element_type"].as_str().unwrap_or_default().to_owned(),
        name: opt_str(&v["name"]),
        tpq_age_bp: opt_num(&v["tpq_age_bp"]),
        taq_age_bp: opt_num(&v["taq_age_bp"]),
        params: v["params"].as_object().map(|p| {
            p.iter()
                .map(|(k, v)| (k.clone(), param(v)))
                .collect::<Params>()
        }),
        children: v["children"]
            .as_array()
            .map(|c| c.iter().map(element).collect())
            .unwrap_or_default(),
    }
}

pub fn tree(v: &Value) -> Option<Element> {
    (!v.is_null()).then(|| element(v))
}

pub fn measurement(v: &Value) -> Measurement {
    Measurement {
        id: v["id"].as_str().unwrap().to_owned(),
        sample_name: opt_str(&v["sample_name"]),
        age_bp: opt_num(&v["age_bp"]).unwrap_or(0.0),
        error_1sigma: opt_num(&v["error_1sigma"]).unwrap_or(0.0),
        curve_id: opt_str(&v["curve_id"]),
        delta_r: opt_num(&v["delta_r"]),
        delta_r_error: opt_num(&v["delta_r_error"]),
        tpq_age_bp: opt_num(&v["tpq_age_bp"]),
        taq_age_bp: opt_num(&v["taq_age_bp"]),
        element_id: opt_str(&v["element_id"]),
        group_id: opt_str(&v["group_id"]),
        outlier_prior: opt_num(&v["outlier_prior"]),
        outlier_model: opt_str(&v["outlier_model"]),
        inbuilt_years: opt_num(&v["inbuilt_years"]),
        inbuilt_shape: opt_str(&v["inbuilt_shape"]),
        ring_offset: opt_num(&v["ring_offset"]),
        ring_offset_error: opt_num(&v["ring_offset_error"]),
        depth_cm: opt_num(&v["depth_cm"]),
        latitude: opt_num(&v["latitude"]),
        longitude: opt_num(&v["longitude"]),
    }
}

pub fn measurements(v: &Value) -> Vec<Measurement> {
    v.as_array().unwrap().iter().map(measurement).collect()
}

pub fn group(v: &Value) -> Group {
    Group {
        id: v["id"].as_str().unwrap().to_owned(),
        name: opt_str(&v["name"]),
        tpq_age_bp: opt_num(&v["tpq_age_bp"]),
        taq_age_bp: opt_num(&v["taq_age_bp"]),
    }
}

pub fn relationship(v: &Value) -> Relationship {
    Relationship {
        from_sample_id: v["from_sample_id"].as_str().unwrap().to_owned(),
        to_sample_id: v["to_sample_id"].as_str().unwrap().to_owned(),
        // A missing key is Python's default, 'sample'; an explicit null is None.
        from_type: if v.get("from_type").is_some() {
            opt_str(&v["from_type"])
        } else {
            Some("sample".into())
        },
        to_type: if v.get("to_type").is_some() {
            opt_str(&v["to_type"])
        } else {
            Some("sample".into())
        },
        relationship: v["relationship"].as_str().unwrap().to_owned(),
    }
}

/// An element back to the JSON the generator wrote, for comparing trees.
pub fn element_json(e: &Element) -> Value {
    let mut map = serde_json::Map::new();
    map.insert("id".into(), Value::from(e.id.clone()));
    map.insert("element_type".into(), Value::from(e.element_type.clone()));
    map.insert(
        "name".into(),
        e.name.clone().map_or(Value::Null, Value::from),
    );
    map.insert(
        "tpq_age_bp".into(),
        e.tpq_age_bp.map_or(Value::Null, Value::from),
    );
    map.insert(
        "taq_age_bp".into(),
        e.taq_age_bp.map_or(Value::Null, Value::from),
    );
    map.insert(
        "children".into(),
        Value::Array(e.children.iter().map(element_json).collect()),
    );
    Value::Object(map)
}

pub fn strings(v: &Value) -> Vec<String> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_owned())
        .collect()
}
