use crate::*;
use radical_core::calibration::{DISPLAY_MASS_THRESHOLD, DISPLAY_MAX_POINTS};
use radical_core::{Mode, REPORTING_CONFIDENCE_LEVELS, hpd_ranges, point_estimate};
use radical_corpus::Window;

/// The posterior inside its stored window, the mass outside it, the HPD
/// ranges at each reported level, the point estimates and the display
/// series. The masses and densities are held to REL_LIKELIHOOD, being the
/// likelihood normalised; see there for why.
#[test]
fn calibrate() {
    let mut c = Closeness::default();
    for case in family("calibrate").cases {
        let i = &case.inputs;
        let curve = radical_core::get_curve(i["curve"].as_str().unwrap()).unwrap();
        let result = curve.calibrate(
            num(&i["age_bp"]),
            num(&i["error_1sigma"]),
            opt(&i["delta_r"]),
            opt(&i["delta_r_error"]),
            Mode::Standard,
        );
        let name = &case.name;
        if let Some(error) = &case.error {
            assert_eq!(
                result.unwrap_err().to_string(),
                error["message"].as_str().unwrap(),
                "{name}"
            );
            continue;
        }
        let probs = result.unwrap();
        let want = outputs(&case);
        let cal = curve.cal_ages();

        let window: Window = serde_json::from_value(want["posterior"].clone()).unwrap();
        assert_eq!(window.length, probs.len(), "{name}");
        for (k, i) in window.range().enumerate() {
            same(
                &format!("{name}/posterior.cal_ages[{k}]"),
                cal[i],
                window.cal_ages[k],
            );
        }
        c.check_all(
            &format!("{name}/posterior"),
            &probs[window.range()],
            &window.values,
            REL_LIKELIHOOD,
        );
        let outside: f64 = probs[..window.index_from].iter().sum::<f64>()
            + probs[window.range().end..].iter().sum::<f64>();
        assert!(
            (outside - window.mass_outside).abs() <= 1e-12,
            "{name}: {outside:e} outside the window, Python had {:e}",
            window.mass_outside
        );

        for level in REPORTING_CONFIDENCE_LEVELS {
            let key = format!("{level:.10}");
            let got = hpd_ranges(cal, &probs, level).unwrap();
            check_ranges(
                &mut c,
                &format!("{name}/hpd/{key}"),
                &got,
                &want["hpd"][&key],
            );
        }

        let estimate = point_estimate(cal, &probs).unwrap();
        let pe = &want["point_estimate"];
        c.check(
            &format!("{name}/mean"),
            estimate.mean,
            num(&pe["mean"]),
            REL,
        );
        c.check(
            &format!("{name}/median"),
            estimate.median,
            num(&pe["median"]),
            REL,
        );
        same(&format!("{name}/mode"), estimate.mode, num(&pe["mode"]));
        c.check(&format!("{name}/std"), estimate.std, num(&pe["std"]), REL);

        let display =
            curve.display_distribution(cal, &probs, DISPLAY_MAX_POINTS, DISPLAY_MASS_THRESHOLD);
        let series = want["display"].as_array().unwrap();
        assert_eq!(display.len(), series.len(), "{name}/display");
        for (k, ([x, y], pair)) in display.iter().zip(series).enumerate() {
            same(&format!("{name}/display[{k}].x"), *x, num(&pair[0]));
            c.check(
                &format!("{name}/display[{k}].y"),
                *y,
                num(&pair[1]),
                REL_LIKELIHOOD,
            );
        }
    }
    c.report("calibrate");
}
