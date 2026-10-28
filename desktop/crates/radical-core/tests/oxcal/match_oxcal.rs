//! "Match OxCal 4.4": reproducing OxCal rather than solving the model as
//! stated. On one Phase of eight dates between two Boundaries (IntCal20, 1480
//! to 1410 BP in steps of ten, +/-25), OxCal's span is 26.26 years against
//! 28.4 solved as stated.

use radical_core::calibration::Mode;
use radical_core::gibbs::{self, SampleOptions, Sampled};
use radical_core::measurement::{Builtins, Calibrator, Measurement};
use radical_core::model_tree::Element;

const OXCAL_SPAN_EIGHT_DATES: f64 = 26.26;

fn element(id: &str, element_type: &str, name: &str, children: Vec<Element>) -> Element {
    Element {
        id: id.to_owned(),
        element_type: element_type.to_owned(),
        name: Some(name.to_owned()),
        tpq_age_bp: None,
        taq_age_bp: None,
        params: None,
        children,
    }
}

fn eight_dates() -> (Element, Vec<Measurement>) {
    let root = element(
        "seq",
        "sequence",
        "Site",
        vec![
            element("b1", "boundary", "Start", vec![]),
            element("p", "phase", "Phase", vec![]),
            element("b2", "boundary", "End", vec![]),
        ],
    );
    let dates = (0..8)
        .map(|i| Measurement {
            id: format!("u{i}"),
            sample_name: Some(format!("U{i}")),
            age_bp: f64::from(1480 - 10 * i),
            error_1sigma: 25.0,
            curve_id: Some("intcal20".to_owned()),
            element_id: Some("p".to_owned()),
            ..Measurement::default()
        })
        .collect();
    (root, dates)
}

fn span(sampled: &Sampled) -> f64 {
    sampled.intervals.as_ref().unwrap().durations[0].years.mean
}

/// One chain, the default seed. Solved as stated the span is about 28.4;
/// matched it lands within a couple of years of OxCal's 26.26, the rest being
/// the run's own Monte Carlo variation (26.2-27.6 across seeds and chain
/// counts).
#[test]
fn matching_narrows_a_phase_towards_oxcals() {
    let (root, dates) = eight_dates();
    let options = SampleOptions {
        chains: 1,
        estimate_durations: true,
        num_samples: 500,
        ..SampleOptions::default()
    };
    let matched = gibbs::solve(
        &dates,
        &root,
        &SampleOptions {
            match_oxcal: true,
            ..options.clone()
        },
        &Calibrator::new(&Builtins, Mode::MatchOxcal),
        None,
    )
    .unwrap()
    .unwrap();
    let plain = gibbs::solve(
        &dates,
        &root,
        &SampleOptions {
            temperatures: 1,
            ..options
        },
        &Calibrator::default(),
        None,
    )
    .unwrap()
    .unwrap();
    assert!(matched.limits_shrunk && !plain.limits_shrunk);
    assert!(matched.lobe_probe.is_none());
    assert!(
        span(&matched) < span(&plain) - 1.0,
        "{} against {}",
        span(&matched),
        span(&plain)
    );
    assert!(
        (span(&matched) - OXCAL_SPAN_EIGHT_DATES).abs() <= 2.0,
        "{}",
        span(&matched)
    );
}
