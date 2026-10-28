//! Calibration against OxCal's own likelihoods on the Hillside dates.
//!
//! Only the UNMODELLED path, deliberately: it needs no solver, so it can be
//! asserted tightly, and it is the foundation everything else stands on --
//! curve loading, interpolation, the grid weighting and the multi-range HPD
//! search all have to be right before a modelled range can mean anything.
//!
//! Every one of these eight dates is bimodal on SHCal20 -- the wiggle around
//! AD 1350 splits each likelihood into roughly AD 1300-1360 and AD 1380-1410
//! -- so this exercises the multi-range machinery rather than a single peak.

use radical_core::calibration::{HpdRange, hpd_ranges};
use radical_core::model_tree::Element;

use crate::export::{
    Raw, Table, as_ad, envelope, fixture_path, hpd_from_distribution, key, overlap,
};
use crate::fixture::{HILLSIDE, cal, hillside, model};

const LEVELS: [(usize, f64); 2] = [(0, 0.683), (1, 0.954)];

fn ours(name: &str) -> (Vec<f64>, Vec<f64>, Vec<HpdRange>, Vec<HpdRange>) {
    let date = model(hillside("ordered")).date(name);
    let (ages, masses) = cal().calibrate(date).unwrap();
    let r68 = hpd_ranges(ages, &masses, 0.683).unwrap();
    let r95 = hpd_ranges(ages, &masses, 0.954).unwrap();
    (ages.to_vec(), masses, r68, r95)
}

#[test]
fn each_likelihood_matches_oxcals() {
    // 5-year export bins and a different interpolation of the same curve keep
    // this short of 1.0; well below ~0.95 would mean a real disagreement
    // about the calibration itself.
    let raw = Raw::read(hillside("ordered"));
    for name in model(hillside("ordered")).names() {
        let (ages, masses, ..) = ours(&name);
        let theirs = raw.likelihood(&name);
        let o = overlap(&ages, &masses, &theirs.cal, &theirs.prob);
        assert!(o > 0.95, "{name}: overlap {o}");
    }
}

#[test]
fn each_reported_range_matches_oxcals() {
    // OxCal's table reports the ENVELOPE of the HPD region, so compare
    // envelope to envelope. Five years is a little over its export resolution.
    let table = Table::read(hillside("ordered"));
    for name in model(hillside("ordered")).names() {
        let (_, _, r68, r95) = ours(&name);
        for ((level, _), ranges) in LEVELS.iter().zip([&r68, &r95]) {
            let want = table.get(&name).unmodelled.0[*level][0];
            let got = as_ad(envelope(
                &ranges.iter().map(|r| (r.min, r.max)).collect::<Vec<_>>(),
            ));
            assert!(
                (got.0 - want.0).abs() <= 5.0 && (got.1 - want.1).abs() <= 5.0,
                "{name} at level {level}: {got:?} against OxCal's {want:?}"
            );
        }
    }
}

#[test]
fn each_date_splits_into_as_many_lobes_as_oxcals() {
    // Counted over ranges carrying at least 1% of the probability: the curve's
    // own grid is finer than OxCal's 5-year bins near AD 1300, so it can
    // resolve a narrow dip OxCal smooths over -- see the sliver test below.
    let raw = Raw::read(hillside("ordered"));
    for name in model(hillside("ordered")).names() {
        let (_, _, _, r95) = ours(&name);
        let theirs = raw.likelihood(&name);
        let want = hpd_from_distribution(&theirs.cal, &theirs.prob, 0.954).len();
        let got = r95.iter().filter(|r| r.probability_percent >= 1.0).count();
        assert_eq!(got, want, "{name}");
    }
}

/// One of these dates comes back with a THIRD 95.4% range carrying under 1%
/// of the probability: LA-10001's is AD 1306-1308, 1313-1360, 1381-1408.
///
/// Not a calibration error: the curve's own grid is finer than OxCal's 5-year
/// bins, so it resolves a one-point dip OxCal smooths across. But a three-year
/// range at 0.6% is not meaningful output, and OxCal does not report it.
/// Pinned so that suppressing slivers shows up here as a deliberate change.
#[test]
fn sub_resolution_slivers_are_reported() {
    let mut found = Vec::new();
    for name in model(hillside("ordered")).names() {
        let (_, _, _, r95) = ours(&name);
        let slivers: Vec<_> = r95.iter().filter(|r| r.probability_percent < 1.0).collect();
        for s in &slivers {
            assert!(
                s.max - s.min <= 5.0 && s.probability_percent < 1.0,
                "{name}: {s:?}"
            );
        }
        if !slivers.is_empty() {
            found.push(name);
        }
    }
    assert_eq!(found, ["LA10001"]);
}

// The fixture itself. If these fail, the comparisons here and elsewhere are
// measuring nothing.

#[test]
fn every_date_is_in_both_exports() {
    let (raw, table) = (
        Raw::read(hillside("ordered")),
        Table::read(hillside("ordered")),
    );
    let names = model(hillside("ordered")).names();
    assert_eq!(names.len(), 8);
    for name in names {
        let p = raw.get(&name);
        assert!(p.likelihood.is_some() && p.posterior.is_some(), "{name}");
        assert_eq!(table.get(&name).op, "R_Date");
    }
}

#[test]
fn a_boundary_has_a_posterior_and_no_likelihood() {
    let (raw, table) = (
        Raw::read(hillside("ordered")),
        Table::read(hillside("ordered")),
    );
    for name in ["Start Hillside", "End Hillside"] {
        let p = raw.get(name);
        assert!(p.posterior.is_some() && p.likelihood.is_none(), "{name}");
        assert_eq!(table.get(name).op, "Boundary");
        assert!(table.get(name).unmodelled.0[1].is_empty(), "{name}");
    }
}

/// LD10006 and LA10003 sit right on the 68.3% cut: their late lobe (around AD
/// 1380-1395) is just inside it by this HPD search and just outside by
/// OxCal's. The older edge still agrees, and both agree at 95.4%, so this is a
/// tie at the threshold. Listed rather than tolerated generally, so a third
/// case appearing would fail.
const BORDERLINE_68: [&str; 2] = ["LD10006", "LA10003"];

/// OxCal's table and its raw distributions have to agree with each other
/// through an OxCal-style HPD, or the reading of one of them is wrong. This
/// is what established that the table reports envelopes.
#[test]
fn the_table_reproduces_the_raw_export() {
    let (raw, table) = (
        Raw::read(hillside("ordered")),
        Table::read(hillside("ordered")),
    );
    let mut checked = 0;
    for (name, p) in &raw.parameters {
        for (dist, modelled) in [(&p.likelihood, false), (&p.posterior, true)] {
            let Some(dist) = dist else { continue };
            let row = table.get(name);
            let ranges = if modelled {
                &row.modelled
            } else {
                &row.unmodelled
            };
            for (level, confidence) in LEVELS {
                let Some(&want) = ranges.0[level].first() else {
                    continue;
                };
                if level == 0 && modelled && BORDERLINE_68.contains(&name.as_str()) {
                    continue;
                }
                let got = as_ad(envelope(&hpd_from_distribution(
                    &dist.cal, &dist.prob, confidence,
                )));
                assert!(
                    (got.0 - want.0).abs() <= 3.0 && (got.1 - want.1).abs() <= 3.0,
                    "{name} ({}, level {level}): {got:?} against {want:?}",
                    if modelled { "posterior" } else { "likelihood" }
                );
                checked += 1;
            }
        }
    }
    assert_eq!(
        checked, 34,
        "fewer comparisons than expected -- fixture changed?"
    );
}

#[test]
fn the_borderline_68_cases_still_agree_at_95() {
    let (raw, table) = (
        Raw::read(hillside("ordered")),
        Table::read(hillside("ordered")),
    );
    for name in BORDERLINE_68 {
        let dist = raw.posterior(name);
        let row = &table.get(name).modelled;
        let at95 = as_ad(envelope(&hpd_from_distribution(
            &dist.cal, &dist.prob, 0.954,
        )));
        let want = row.0[1][0];
        assert!(
            (at95.0 - want.0).abs() <= 3.0 && (at95.1 - want.1).abs() <= 3.0,
            "{name}"
        );
        // ...and the older edge of the 68.3% range agrees even there.
        let at68 = as_ad(envelope(&hpd_from_distribution(
            &dist.cal, &dist.prob, 0.683,
        )));
        assert!((at68.0 - row.0[0][0].0).abs() <= 3.0, "{name}");
    }
}

fn names_in_order(e: &Element, out: &mut Vec<String>) {
    out.push(e.name.clone().unwrap_or_default());
    for child in &e.children {
        names_in_order(child, out);
    }
}

/// A .oxcal file with its // comments stripped: the files carry a header
/// describing the variant, which names elements.
fn model_body(stem: &str) -> String {
    let text = std::fs::read_to_string(fixture_path(&format!("{stem}.oxcal"))).unwrap();
    text.lines()
        .map(|line| line.split("//").next().unwrap())
        .collect::<Vec<_>>()
        .join("\n")
}

/// The model each run is solved as must be the one in its .oxcal file: the
/// same elements in the same order, which is what decides what is older than
/// what, and every date under the phase the file puts it in.
#[test]
fn each_model_is_the_one_in_its_oxcal_file() {
    for (run, stem) in HILLSIDE {
        let expected: &[&str] = match run {
            "ordered" => &[
                "Hillside",
                "Start Hillside",
                "North",
                "Fire_Feature_J",
                "South",
                "Fires",
                "Fire_Feature_K",
                "Fire_Feature_A",
                "End Hillside",
            ],
            "unordered" => &[
                "Hillside",
                "Start Hillside",
                "Sites",
                "North",
                "Fire_Feature_J",
                "South",
                "Fires",
                "Fire_Feature_K",
                "Fire_Feature_A",
                "End Hillside",
            ],
            _ => &[
                "Hillside",
                "Start Hillside",
                "North",
                "Fire_Feature_J",
                "North to South",
                "South",
                "Fires",
                "Fire_Feature_K",
                "Fire_Feature_A",
                "End Hillside",
            ],
        };
        let m = model(stem);
        let mut names = Vec::new();
        names_in_order(&m.root, &mut names);
        assert_eq!(names, expected, "{run}");

        let text = model_body(stem);
        let positions: Vec<usize> = expected
            .iter()
            .map(|name| {
                text.find(&format!("\"{name}\""))
                    .unwrap_or_else(|| panic!("{name} is not in {run}.oxcal"))
            })
            .collect();
        assert!(
            positions[1..].is_sorted(),
            "{run}: tree order is not file order"
        );

        let phase_of = |name: &str| {
            let id = m.date(name).element_id.clone().unwrap();
            let mut found = None;
            fn walk<'a>(e: &'a Element, id: &str, found: &mut Option<&'a str>) {
                if e.id == id {
                    *found = e.name.as_deref();
                }
                for c in &e.children {
                    walk(c, id, found);
                }
            }
            walk(&m.root, &id, &mut found);
            found.unwrap().to_owned()
        };
        for name in crate::fixture::NORTH {
            assert_eq!(phase_of(name), "Fire_Feature_J", "{run}: {name}");
        }
        for name in ["LD10006", "LD10007", "LD10008"] {
            assert_eq!(phase_of(name), "Fire_Feature_K", "{run}: {name}");
        }
        for name in ["LA10003", "LA10001"] {
            assert_eq!(phase_of(name), "Fire_Feature_A", "{run}: {name}");
        }
    }
}

/// All three runs must be the same eight dates on the same curve, or the
/// comparisons between them are meaningless; so the unmodelled ranges are
/// identical across runs too.
#[test]
fn the_runs_differ_only_in_the_branch_relationship() {
    let baseline = Table::read(hillside("ordered"));
    for (run, stem) in HILLSIDE {
        let text = model_body(stem);
        assert!(text.contains("SHCal20"), "{run}");
        let m = model(stem);
        assert_eq!(m.dates.len(), 8, "{run}");
        for d in &m.dates {
            let name = d.sample_name.as_deref().unwrap();
            assert!(
                text.contains(&format!(
                    "\"{name}\", {:.0}, {:.0}",
                    d.age_bp, d.error_1sigma
                )),
                "{run}: {name}"
            );
            assert_eq!(d.curve_id.as_deref(), Some("shcal20"), "{run}: {name}");
        }
        let table = Table::read(stem);
        for name in m.names() {
            assert_eq!(
                table.get(&name).unmodelled,
                baseline.get(&key(&name)).unmodelled,
                "{run}: {name}"
            );
        }
    }
}
