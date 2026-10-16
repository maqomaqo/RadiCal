use crate::*;

/// numpy.linspace, bit for bit: the display series and the resolution
/// profile both sample with it.
#[test]
fn linspace() {
    for case in family("linspace").cases {
        let i = &case.inputs;
        let got = radical_core::numeric::linspace(
            num(&i["start"]),
            num(&i["stop"]),
            i["n"].as_u64().unwrap() as usize,
        );
        let want = nums(&outputs(&case)["values"]);
        assert_eq!(got.len(), want.len(), "{}", case.name);
        for (k, (&g, &w)) in got.iter().zip(&want).enumerate() {
            same(&format!("{}[{k}]", case.name), g, w);
        }
    }
}
