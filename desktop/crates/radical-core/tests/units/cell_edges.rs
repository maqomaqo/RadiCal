use crate::*;

/// Midpoints and two end values: exact.
#[test]
fn cell_edges() {
    for case in family("cell_edges").cases {
        let ages = match case.inputs.get("curve") {
            Some(id) => radical_core::get_curve(id.as_str().unwrap())
                .unwrap()
                .cal_ages()
                .to_vec(),
            None => nums(&case.inputs["ages"]),
        };
        let got = radical_core::cell_edges(&ages);
        let want = nums(&outputs(&case)["edges"]);
        assert_eq!(got.len(), want.len(), "{}", case.name);
        for (k, (&g, &w)) in got.iter().zip(&want).enumerate() {
            same(&format!("{}[{k}]", case.name), g, w);
        }
    }
}
