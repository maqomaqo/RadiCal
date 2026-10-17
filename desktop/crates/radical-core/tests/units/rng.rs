use crate::*;
use radical_core::rng::{Generator, Seed};

fn seed(v: &Value) -> Seed {
    let parse = |s: &Value| s.as_str().unwrap().parse::<u128>().unwrap();
    match v {
        Value::Array(items) => Seed::Many(items.iter().map(parse).collect()),
        one => Seed::One(parse(one)),
    }
}

/// The generator reproduces NumPy's bit for bit: its seeded state, and every
/// draw taken from it, compared as exact doubles.
#[test]
fn rng() {
    for case in family("rng").cases {
        let i = &case.expanded_inputs();
        let want = outputs(&case);
        let name = &case.name;
        if name.starts_with("random/") {
            let mut rng = Generator::new(seed(&i["seed"]));
            let (state, inc) = rng.bit_generator().state();
            assert_eq!(
                state.to_string(),
                want["state"]["state"].as_str().unwrap(),
                "{name}"
            );
            assert_eq!(
                inc.to_string(),
                want["state"]["inc"].as_str().unwrap(),
                "{name}"
            );
            for (k, w) in nums(&want["random"]).into_iter().enumerate() {
                same(&format!("{name}[{k}]"), rng.random(), w);
            }
        } else if name == "uniform" {
            let mut rng = Generator::new(seed(&Value::String(i["seed"].to_string())));
            for (k, (pair, w)) in i["scalars"]
                .as_array()
                .unwrap()
                .iter()
                .zip(nums(&want["scalars"]))
                .enumerate()
            {
                let p = nums(pair);
                same(&format!("{name}/scalars[{k}]"), rng.uniform(p[0], p[1]), w);
            }
            let spec = nums(&i["array"]);
            for (k, w) in nums(&want["array"]).into_iter().enumerate() {
                same(
                    &format!("{name}/array[{k}]"),
                    rng.uniform(spec[0], spec[1]),
                    w,
                );
            }
            for (k, w) in nums(&want["random_after"]).into_iter().enumerate() {
                same(&format!("{name}/random_after[{k}]"), rng.random(), w);
            }
        } else if name == "choice" {
            let mut rng = Generator::new(seed(&Value::String(i["seed"].to_string())));
            let p = nums(&i["p"]);
            let got: Vec<u64> = (0..i["count"].as_u64().unwrap())
                .map(|_| rng.choice(&p) as u64)
                .collect();
            let want: Vec<u64> = want["choices"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_u64().unwrap())
                .collect();
            assert_eq!(got, want, "{name}");
        } else if name == "interleaved" {
            let mut rng = Generator::new(i["seed"].as_u64().unwrap());
            let want = want["values"].as_array().unwrap();
            let int = |v: &Value| v.as_str().unwrap().parse::<i64>().unwrap();
            for (k, (op, w)) in i["script"].as_array().unwrap().iter().zip(want).enumerate() {
                let label = format!("{name}[{k}]");
                match op[0].as_str().unwrap() {
                    "random" => same(&label, rng.random(), num(w)),
                    "integers" => {
                        assert_eq!(rng.integers(int(&op[1]), int(&op[2])), int(w), "{label}")
                    }
                    "normal" => same(&label, rng.normal(num(&op[1]), num(&op[2])), num(w)),
                    other => panic!("{label}: unknown draw {other}"),
                }
            }
        } else if name == "standard_normal" {
            let mut rng = Generator::new(i["seed"].as_u64().unwrap());
            let draws: Vec<f64> = (0..i["count"].as_u64().unwrap())
                .map(|_| rng.standard_normal())
                .collect();
            for (k, w) in nums(&want["first"]).into_iter().enumerate() {
                same(&format!("{name}[{k}]"), draws[k], w);
            }
            let bytes: Vec<u8> = draws.iter().flat_map(|d| d.to_le_bytes()).collect();
            assert_eq!(
                radical_corpus::sha256_hex(&bytes),
                want["sha256_le"].as_str().unwrap(),
                "{name}: a draw differs"
            );
            let tail = draws
                .iter()
                .filter(|d| d.abs() > 3.654_152_885_361_008_8)
                .count() as u64;
            assert_eq!(tail, want["tail_draws"].as_u64().unwrap(), "{name}");
        } else {
            panic!("{name}: no comparison for this case");
        }
    }
}
