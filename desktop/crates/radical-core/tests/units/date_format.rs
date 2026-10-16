use crate::*;
use radical_core::HpdRange;
use radical_core::date_format::{format_age, format_hpd_ranges, format_range, normalise};

/// Text, so exact.
#[test]
fn date_format() {
    for case in family("date_format").cases {
        let i = case.expanded_inputs();
        let want = outputs(&case);
        let name = &case.name;
        let norm = normalise(i["format"].as_str());
        assert_eq!(norm, want["normalised"].as_str().unwrap(), "{name}");
        let ages: Vec<String> = nums(&i["ages"])
            .iter()
            .map(|&a| format_age(Some(a), norm))
            .collect();
        let want_ages: Vec<&str> = want["ages"]
            .as_array()
            .unwrap()
            .iter()
            .map(|a| a.as_str().unwrap())
            .collect();
        assert_eq!(ages, want_ages, "{name}");
        assert_eq!(
            format_range(Some(3270.0), Some(3100.0), norm),
            want["range"].as_str().unwrap(),
            "{name}"
        );
        let ranges: Vec<HpdRange> = i["ranges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| HpdRange {
                min: num(&r["min"]),
                max: num(&r["max"]),
                probability_percent: num(&r["probability_percent"]),
            })
            .collect();
        assert_eq!(
            format_hpd_ranges(&ranges, norm),
            want["ranges"].as_str().unwrap(),
            "{name}"
        );
    }
}
