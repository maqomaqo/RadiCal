//! Areas inside an area, integrated exactly ([`radical_core::exact_parallel`]).
//!
//! Nothing recorded answers these models -- the corpus predates them -- so
//! they are held to two independent routes instead: the sampler, which never
//! forms a transfer matrix, on models nested two and three deep; and the
//! one-level solver on a model both can express, where the two must agree to
//! rounding because they sum the same terms in a different order.

use std::collections::HashMap;

use radical_core::exact_chain::{Posterior, Solution, SolveOptions};
use radical_core::exact_parallel::{Stages, layout_with_reason, solve};
use radical_core::gibbs::{self, SampleOptions};
use radical_core::measurement::{Calibrator, Measurement};
use radical_core::model_tree::Element;

/// How far apart two cumulative distributions may be, anywhere, for the exact
/// solve and the sampler to count as agreeing.
const SAMPLER_TOLERANCE: f64 = 0.03;

fn element(kind: &str, name: &str, children: Vec<Element>) -> Element {
    Element {
        id: name.to_owned(),
        element_type: kind.to_owned(),
        name: Some(name.to_owned()),
        children,
        ..Element::default()
    }
}

fn boundary(name: &str) -> Element {
    element("boundary", name, Vec::new())
}

fn phase(name: &str, children: Vec<Element>) -> Element {
    element("phase", name, children)
}

fn sequence(name: &str, children: Vec<Element>) -> Element {
    element("sequence", name, children)
}

/// Dates in the Phase `phase`, as (radiocarbon age, error).
fn dates_in(phase: &str, ages: &[(f64, f64)]) -> Vec<Measurement> {
    ages.iter()
        .enumerate()
        .map(|(index, &(age_bp, error_1sigma))| Measurement {
            id: format!("{phase}/{index}"),
            sample_name: Some(format!("{phase}/{index}")),
            age_bp,
            error_1sigma,
            element_id: Some(phase.to_owned()),
            ..Measurement::default()
        })
        .collect()
}

fn boundary_ids(element: &Element, found: &mut Vec<String>) {
    if element.is("boundary") {
        found.push(element.id.clone());
    }
    for child in &element.children {
        boundary_ids(child, found);
    }
}

fn laid_out(root: &Element, dates: &[Measurement]) -> Stages {
    layout_with_reason(Some(root), dates)
        .unwrap_or_else(|reason| panic!("declined: {reason}"))
        .expect("the model has areas")
}

/// Cumulative probability at each of `ages` for a reported distribution,
/// [cal BP, density]: each point's mass spread evenly over its own cell.
fn cumulative(distribution: &[[f64; 2]], ages: &[f64]) -> Vec<f64> {
    let mut points = distribution.to_vec();
    points.sort_by(|a, b| a[0].total_cmp(&b[0]));
    let step = points[1][0] - points[0][0];
    let total: f64 = points.iter().map(|p| p[1]).sum();
    ages.iter()
        .map(|&age| {
            points
                .iter()
                .map(|p| p[1] * ((age - (p[0] - step / 2.0)) / step).clamp(0.0, 1.0))
                .sum::<f64>()
                / total
        })
        .collect()
}

/// The largest difference between two reported distributions' cumulative
/// probabilities, taken every year across both.
fn cumulative_gap(a: &[[f64; 2]], b: &[[f64; 2]]) -> f64 {
    let ends = a.iter().chain(b).map(|p| p[0]);
    let low = ends.clone().fold(f64::INFINITY, f64::min).floor();
    let high = ends.fold(f64::NEG_INFINITY, f64::max).ceil();
    let ages: Vec<f64> = (0..=(high - low) as usize)
        .map(|i| low + i as f64)
        .collect();
    cumulative(a, &ages)
        .iter()
        .zip(cumulative(b, &ages))
        .map(|(x, y)| (x - y).abs())
        .fold(0.0, f64::max)
}

/// The largest difference between `draws`' empirical cumulative distribution
/// and a reported distribution's.
fn draws_gap(draws: &[f64], distribution: &[[f64; 2]]) -> f64 {
    let mut sorted = draws.to_vec();
    sorted.sort_by(f64::total_cmp);
    let ages: Vec<f64> = distribution.iter().map(|p| p[0]).collect();
    cumulative(distribution, &ages)
        .iter()
        .zip(&ages)
        .map(|(c, &age)| {
            let below = sorted.partition_point(|&d| d < age) as f64;
            let through = sorted.partition_point(|&d| d <= age) as f64;
            (c - (below + through) / 2.0 / sorted.len() as f64).abs()
        })
        .fold(0.0, f64::max)
}

/// Every date and every Boundary of `root` in `solution`, by id.
fn posteriors<'a>(
    root: &Element,
    dates: &[Measurement],
    solution: &'a Solution,
) -> Vec<(String, &'a Posterior)> {
    let by_date: HashMap<&str, &Posterior> = solution
        .measurements
        .iter()
        .map(|(id, p)| (id.as_str(), p))
        .collect();
    let by_boundary: HashMap<&str, &Posterior> = solution
        .boundaries
        .iter()
        .map(|(id, b)| (id.as_str(), &b.posterior))
        .collect();
    let mut wanted = Vec::new();
    boundary_ids(root, &mut wanted);
    let mut found: Vec<(String, &Posterior)> = dates
        .iter()
        .map(|m| {
            let p = by_date
                .get(m.id.as_str())
                .unwrap_or_else(|| panic!("no marginal for date {}", m.id));
            (m.id.clone(), *p)
        })
        .collect();
    for id in wanted {
        let p = by_boundary
            .get(id.as_str())
            .unwrap_or_else(|| panic!("no marginal for Boundary {id}"));
        found.push((id, *p));
    }
    found
}

/// `(older, younger)` for everything the tree orders directly: a Sequence's
/// children one after another, and whatever a Phase between two Boundaries
/// holds inside them.
fn orderings(element: &Element, dates: &[Measurement], found: &mut Vec<(String, String)>) {
    // (oldest parameters, youngest parameters) of an element.
    fn ends(element: &Element, dates: &[Measurement]) -> (Vec<String>, Vec<String>) {
        if element.is("boundary") {
            return (vec![element.id.clone()], vec![element.id.clone()]);
        }
        let own: Vec<String> = dates
            .iter()
            .filter(|m| m.element_id.as_deref() == Some(element.id.as_str()))
            .map(|m| m.id.clone())
            .collect();
        let parts: Vec<(Vec<String>, Vec<String>)> = element
            .children
            .iter()
            .map(|c| ends(c, dates))
            .filter(|(o, _)| !o.is_empty())
            .collect();
        if element.is("sequence") && !parts.is_empty() {
            return (parts[0].0.clone(), parts[parts.len() - 1].1.clone());
        }
        let mut oldest = own.clone();
        let mut youngest = own;
        for (o, y) in parts {
            oldest.extend(o);
            youngest.extend(y);
        }
        (oldest, youngest)
    }
    if element.is("sequence") {
        let parts: Vec<(Vec<String>, Vec<String>)> = element
            .children
            .iter()
            .map(|c| ends(c, dates))
            .filter(|(o, _)| !o.is_empty())
            .collect();
        for pair in parts.windows(2) {
            for older in &pair[0].1 {
                for younger in &pair[1].0 {
                    found.push((older.clone(), younger.clone()));
                }
            }
        }
    }
    for child in &element.children {
        orderings(child, dates, found);
    }
}

/// The exact solve of a nested model, checked for what any solve owes: a
/// marginal for every date and Boundary, and joint draws that hold every
/// parameter, keep the model's order in every state, and follow the
/// marginals.
fn solved(root: &Element, dates: &[Measurement]) -> Solution {
    let stages = laid_out(root, dates);
    assert!(
        stages
            .stages
            .iter()
            .flat_map(|s| &s.areas)
            .any(|a| a.nested()),
        "the model has no area inside an area"
    );
    let cal = Calibrator::default();
    let solution = solve(dates, root, &stages, &SolveOptions::default(), &cal).unwrap();
    let mut all_boundaries = Vec::new();
    boundary_ids(root, &mut all_boundaries);
    assert_eq!(stages.all_boundary_ids(), all_boundaries);
    assert_eq!(solution.measurements.len(), dates.len());
    assert_eq!(solution.boundaries.len(), all_boundaries.len());

    let mut draws: HashMap<&str, &Vec<f64>> = HashMap::new();
    for (id, values) in solution
        .joint_draws
        .iter()
        .chain(&solution.joint_boundaries)
    {
        draws.insert(id, values);
    }
    for (id, posterior) in posteriors(root, dates, &solution) {
        let drawn = draws
            .get(id.as_str())
            .unwrap_or_else(|| panic!("no joint draws for {id}"));
        let gap = draws_gap(drawn, &posterior.probability_distribution);
        assert!(
            gap < 0.04,
            "{id}: its joint draws are {gap:.4} from its marginal"
        );
    }
    let mut ordered = Vec::new();
    orderings(root, dates, &mut ordered);
    assert!(!ordered.is_empty());
    for (older, younger) in ordered {
        let (o, y) = (draws[older.as_str()], draws[younger.as_str()]);
        assert!(
            o.iter().zip(y.iter()).all(|(o, y)| o >= y),
            "{older} is drawn younger than {younger}"
        );
    }
    solution
}

/// The exact solve held to the sampler: every date's and Boundary's
/// cumulative distribution within [`SAMPLER_TOLERANCE`].
fn agrees_with_the_sampler(root: &Element, dates: &[Measurement]) {
    let exact = solved(root, dates);
    let cal = Calibrator::default();
    let options = SampleOptions {
        chains: 4,
        ..SampleOptions::default()
    };
    let sampled = gibbs::solve(dates, root, &options, &cal, None)
        .unwrap()
        .unwrap_or_else(|_| panic!("the run was cancelled"));
    let mut by_id: HashMap<&str, &Posterior> = HashMap::new();
    for (id, p) in &sampled.measurements {
        by_id.insert(id, p);
    }
    for (id, _, p) in &sampled.boundaries {
        by_id.insert(id, p);
    }
    let mut worst: (f64, String) = (0.0, String::new());
    for (id, posterior) in posteriors(root, dates, &exact) {
        let other = by_id
            .get(id.as_str())
            .unwrap_or_else(|| panic!("the sampler has no {id}"));
        let gap = cumulative_gap(
            &posterior.probability_distribution,
            &other.probability_distribution,
        );
        eprintln!(
            "{id}: {gap:.4} (median {:.1} exact, {:.1} sampled)",
            posterior.posterior_median, other.posterior_median
        );
        if gap > worst.0 {
            worst = (gap, id);
        }
    }
    assert!(
        worst.0 < SAMPLER_TOLERANCE,
        "{} is {:.4} from the sampler in cumulative probability",
        worst.1,
        worst.0
    );
}

/// A site whose one trench has a phase dug as two cuts, each its own
/// sequence, beside a second trench: areas two deep.
fn two_deep() -> (Element, Vec<Measurement>) {
    let root = sequence(
        "Site",
        vec![
            boundary("Start"),
            phase(
                "Trenches",
                vec![
                    sequence(
                        "Trench 1",
                        vec![
                            boundary("T1 start"),
                            phase("T1 early", Vec::new()),
                            boundary("T1 middle"),
                            phase(
                                "T1 cuts",
                                vec![
                                    sequence(
                                        "Cut A",
                                        vec![
                                            boundary("A start"),
                                            phase("A fill", Vec::new()),
                                            boundary("A end"),
                                        ],
                                    ),
                                    sequence(
                                        "Cut B",
                                        vec![
                                            boundary("B start"),
                                            phase("B lower", Vec::new()),
                                            boundary("B middle"),
                                            phase("B upper", Vec::new()),
                                            boundary("B end"),
                                        ],
                                    ),
                                ],
                            ),
                            boundary("T1 end"),
                        ],
                    ),
                    sequence(
                        "Trench 2",
                        vec![
                            boundary("T2 start"),
                            phase("T2 use", Vec::new()),
                            boundary("T2 end"),
                        ],
                    ),
                ],
            ),
            boundary("End"),
        ],
    );
    let mut dates = Vec::new();
    dates.extend(dates_in(
        "T1 early",
        &[(2540.0, 30.0), (2510.0, 30.0), (2495.0, 25.0)],
    ));
    dates.extend(dates_in(
        "A fill",
        &[(2450.0, 30.0), (2435.0, 30.0), (2420.0, 25.0)],
    ));
    dates.extend(dates_in("B lower", &[(2470.0, 30.0), (2455.0, 30.0)]));
    dates.extend(dates_in("B upper", &[(2390.0, 30.0), (2350.0, 30.0)]));
    dates.extend(dates_in(
        "T2 use",
        &[(2480.0, 30.0), (2440.0, 30.0), (2400.0, 30.0)],
    ));
    (root, dates)
}

/// Areas three deep, with dates lying directly in the Phases that hold areas,
/// beside an ordinary stage of the outer Sequence: every kind of term at once,
/// and the span correction coupling the outer chain's ends rather than folded
/// into one stage.
fn three_deep() -> (Element, Vec<Measurement>) {
    let root = sequence(
        "Site",
        vec![
            boundary("Start"),
            phase("Before", Vec::new()),
            boundary("Transition"),
            phase(
                "Areas",
                vec![
                    sequence(
                        "North",
                        vec![
                            boundary("N start"),
                            phase(
                                "N buildings",
                                vec![
                                    sequence(
                                        "House",
                                        vec![
                                            boundary("H start"),
                                            phase(
                                                "H rooms",
                                                vec![
                                                    sequence(
                                                        "Room 1",
                                                        vec![
                                                            boundary("R1 start"),
                                                            phase("R1 floor", Vec::new()),
                                                            boundary("R1 end"),
                                                        ],
                                                    ),
                                                    sequence(
                                                        "Room 2",
                                                        vec![
                                                            boundary("R2 start"),
                                                            phase("R2 floor", Vec::new()),
                                                            boundary("R2 end"),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            boundary("H end"),
                                            phase("H collapse", Vec::new()),
                                            boundary("H sealed"),
                                        ],
                                    ),
                                    sequence(
                                        "Yard",
                                        vec![
                                            boundary("Y start"),
                                            phase("Y surface", Vec::new()),
                                            boundary("Y end"),
                                        ],
                                    ),
                                ],
                            ),
                            boundary("N end"),
                        ],
                    ),
                    sequence(
                        "South",
                        vec![
                            boundary("S start"),
                            phase("S use", Vec::new()),
                            boundary("S end"),
                        ],
                    ),
                ],
            ),
            boundary("End"),
        ],
    );
    let mut dates = Vec::new();
    dates.extend(dates_in("Before", &[(2620.0, 30.0), (2580.0, 30.0)]));
    dates.extend(dates_in("Areas", &[(2460.0, 35.0)]));
    dates.extend(dates_in("N buildings", &[(2475.0, 30.0)]));
    dates.extend(dates_in("H rooms", &[(2465.0, 30.0)]));
    dates.extend(dates_in("R1 floor", &[(2490.0, 30.0), (2470.0, 30.0)]));
    dates.extend(dates_in("R2 floor", &[(2455.0, 30.0), (2445.0, 25.0)]));
    dates.extend(dates_in("H collapse", &[(2410.0, 30.0), (2380.0, 30.0)]));
    dates.extend(dates_in("Y surface", &[(2500.0, 30.0), (2430.0, 30.0)]));
    dates.extend(dates_in(
        "S use",
        &[(2520.0, 30.0), (2450.0, 30.0), (2360.0, 30.0)],
    ));
    (root, dates)
}

#[test]
fn two_deep_agrees_with_the_sampler() {
    let (root, dates) = two_deep();
    agrees_with_the_sampler(&root, &dates);
}

#[test]
fn three_deep_agrees_with_the_sampler() {
    let (root, dates) = three_deep();
    agrees_with_the_sampler(&root, &dates);
}

/// An area holding nothing but one area is, to the one-level solver, a single
/// line with a Boundary either side of the inner one's and no dates between:
/// the same Boundaries in the same order with the same ties, so the same
/// model. Nested, the inner area enters through Z and is solved given the
/// outer one's pair weight; as a line, the two empty groups are transfer
/// matrices like any other. The same terms, summed along different routes.
#[test]
fn nested_matches_the_one_level_solver() {
    let cut = vec![
        boundary("Cut start"),
        phase("Cut lower", Vec::new()),
        boundary("Cut middle"),
        phase("Cut upper", Vec::new()),
        boundary("Cut end"),
    ];
    let site = |trench: Vec<Element>| {
        sequence(
            "Site",
            vec![
                boundary("Start"),
                phase(
                    "Trenches",
                    vec![
                        sequence("Trench 1", trench),
                        sequence(
                            "Trench 2",
                            vec![
                                boundary("T2 start"),
                                phase("T2 use", Vec::new()),
                                boundary("T2 end"),
                            ],
                        ),
                    ],
                ),
                boundary("End"),
            ],
        )
    };
    let nested = site(vec![
        boundary("T1 start"),
        phase("T1 cuts", vec![sequence("Cut", cut.clone())]),
        boundary("T1 end"),
    ]);
    let mut line = vec![boundary("T1 start")];
    line.extend(cut);
    line.push(boundary("T1 end"));
    let flat = site(line);

    let mut dates = Vec::new();
    dates.extend(dates_in(
        "Cut lower",
        &[(2500.0, 30.0), (2470.0, 30.0), (2455.0, 25.0)],
    ));
    dates.extend(dates_in("Cut upper", &[(2420.0, 30.0), (2380.0, 30.0)]));
    dates.extend(dates_in(
        "T2 use",
        &[(2480.0, 30.0), (2440.0, 30.0), (2400.0, 30.0)],
    ));

    let flat_stages = laid_out(&flat, &dates);
    assert!(
        flat_stages
            .stages
            .iter()
            .flat_map(|s| &s.areas)
            .all(|a| !a.nested()),
        "the line is laid out as areas inside an area"
    );
    let cal = Calibrator::default();
    let one_level = solve(&dates, &flat, &flat_stages, &SolveOptions::default(), &cal).unwrap();
    let two_level = solved(&nested, &dates);
    assert_eq!(one_level.grid, two_level.grid);

    let theirs: HashMap<String, &Posterior> =
        posteriors(&flat, &dates, &one_level).into_iter().collect();
    for (id, ours) in posteriors(&nested, &dates, &two_level) {
        let other = theirs[&id];
        assert_eq!(
            ours.probability_distribution.len(),
            other.probability_distribution.len(),
            "{id}"
        );
        let peak = other
            .probability_distribution
            .iter()
            .map(|p| p[1])
            .fold(0.0, f64::max);
        for (a, b) in ours
            .probability_distribution
            .iter()
            .zip(&other.probability_distribution)
        {
            assert_eq!(a[0], b[0], "{id}");
            assert!(
                (a[1] - b[1]).abs() <= 1e-9 * peak,
                "{id} at {}: {} nested, {} as a line",
                a[0],
                a[1],
                b[1]
            );
        }
        for (name, a, b) in [
            ("mean", ours.posterior_mean, other.posterior_mean),
            ("median", ours.posterior_median, other.posterior_median),
            ("sd", ours.posterior_std, other.posterior_std),
        ] {
            assert!((a - b).abs() <= 1e-6, "{id} {name}: {a} nested, {b} a line");
        }
    }
    assert!(
        (one_level.log_evidence - two_level.log_evidence).abs() <= 1e-8,
        "evidence: {} as a line, {} nested",
        one_level.log_evidence,
        two_level.log_evidence
    );
}

/// What is still declined says why, naming the area.
#[test]
fn shapes_not_covered_are_declined_with_a_reason() {
    // Dates in an area outside its own first Boundary, beside areas in it.
    let (mut root, mut dates) = two_deep();
    let trench = &mut root.children[1].children[0];
    trench.children.insert(0, phase("T1 loose", Vec::new()));
    dates.extend(dates_in("T1 loose", &[(2560.0, 30.0)]));
    let reason = layout_with_reason(Some(&root), &dates).unwrap_err();
    assert!(
        reason.starts_with("in 'Trench 1', 1 date(s) sit outside every Boundary"),
        "{reason}"
    );

    // An area holding areas with no Boundary of its own around them.
    let (mut root, dates) = two_deep();
    let trench = &mut root.children[1].children[0];
    trench.children.retain(|c| c.id != "T1 end");
    trench.children.retain(|c| c.id != "T1 start");
    trench.children.retain(|c| c.id != "T1 early");
    let dates: Vec<Measurement> = dates
        .into_iter()
        .filter(|m| m.element_id.as_deref() != Some("T1 early"))
        .collect();
    let reason = layout_with_reason(Some(&root), &dates).unwrap_err();
    assert!(
        reason.starts_with("in 'Trench 1', no Boundary pair encloses"),
        "{reason}"
    );

    // Two Phases in a row inside an area, areas among them.
    let (mut root, mut dates) = two_deep();
    let trench = &mut root.children[1].children[0];
    trench.children.insert(4, phase("T1 late", Vec::new()));
    dates.extend(dates_in("T1 late", &[(2380.0, 30.0)]));
    let reason = layout_with_reason(Some(&root), &dates).unwrap_err();
    assert!(
        reason.starts_with("in 'Trench 1', 'T1 cuts' and 'T1 late' sit one after another"),
        "{reason}"
    );

    // A Sequence with no Boundaries of its own inside a cut: not an area.
    let (mut root, dates) = two_deep();
    let cuts = &mut root.children[1].children[0].children[3];
    cuts.children[0].children[1] = phase(
        "A fill",
        vec![sequence(
            "Loose",
            vec![phase("L1", Vec::new()), phase("L2", Vec::new())],
        )],
    );
    let mut dates = dates;
    dates.extend(dates_in("L1", &[(2440.0, 30.0)]));
    dates.extend(dates_in("L2", &[(2430.0, 30.0)]));
    let reason = layout_with_reason(Some(&root), &dates).unwrap_err();
    assert!(
        reason.starts_with("in 'Loose', no pair of Boundaries encloses any date"),
        "{reason}"
    );
}
