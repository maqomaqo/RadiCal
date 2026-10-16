//! The special functions the ported services need from SciPy: the chi-squared
//! quantile (`scipy.stats.chi2.ppf`) for the same-event test, and what it is
//! built from.

/// `ln Γ(x)` for `x > 0`, by Lanczos' approximation (g = 7, nine terms),
/// good to about one part in 10^15.
pub fn ln_gamma(x: f64) -> f64 {
    const G: f64 = 7.0;
    const COEFFICIENTS: [f64; 9] = [
        0.999_999_999_999_809_9,
        676.520_368_121_885_1,
        -1_259.139_216_722_402_8,
        771.323_428_777_653_1,
        -176.615_029_162_140_6,
        12.507_343_278_686_905,
        -0.138_571_095_265_720_12,
        9.984_369_578_019_572e-6,
        1.505_632_735_149_311_6e-7,
    ];
    if x < 0.5 {
        // Reflection: Γ(x) Γ(1 - x) = π / sin(πx).
        let pi = std::f64::consts::PI;
        return (pi / (pi * x).sin()).ln() - ln_gamma(1.0 - x);
    }
    let x = x - 1.0;
    let mut a = COEFFICIENTS[0];
    let t = x + G + 0.5;
    for (i, &c) in COEFFICIENTS.iter().enumerate().skip(1) {
        a += c / (x + i as f64);
    }
    0.5 * (2.0 * std::f64::consts::PI).ln() + (x + 0.5) * t.ln() - t + a.ln()
}

/// `x^a e^-x / Γ(a)`, the factor both incomplete gamma expansions share.
fn gamma_prefactor(a: f64, x: f64) -> f64 {
    (a * x.ln() - x - ln_gamma(a)).exp()
}

/// The regularized incomplete gamma functions `(P(a, x), Q(a, x))`, each
/// computed directly where it is small so neither loses precision to `1 - `:
/// the power series below `x = a + 1`, Lentz's continued fraction above.
pub fn gamma_pq(a: f64, x: f64) -> (f64, f64) {
    if x <= 0.0 {
        return (0.0, 1.0);
    }
    if x.is_infinite() {
        return (1.0, 0.0);
    }
    if x < a + 1.0 {
        let (mut term, mut total, mut ap) = (1.0 / a, 1.0 / a, a);
        for _ in 0..10_000 {
            ap += 1.0;
            term *= x / ap;
            total += term;
            if term.abs() < total.abs() * f64::EPSILON {
                break;
            }
        }
        let p = total * gamma_prefactor(a, x);
        (p, 1.0 - p)
    } else {
        const TINY: f64 = 1e-300;
        let mut b = x + 1.0 - a;
        let mut c = 1.0 / TINY;
        let mut d = 1.0 / b;
        let mut h = d;
        for i in 1..10_000 {
            let an = -f64::from(i) * (f64::from(i) - a);
            b += 2.0;
            d = an * d + b;
            if d.abs() < TINY {
                d = TINY;
            }
            c = b + an / c;
            if c.abs() < TINY {
                c = TINY;
            }
            d = 1.0 / d;
            let step = d * c;
            h *= step;
            if (step - 1.0).abs() < f64::EPSILON {
                break;
            }
        }
        let q = gamma_prefactor(a, x) * h;
        (1.0 - q, q)
    }
}

/// The `x` with `P(a, x) = p`: Newton's method on whichever of P and Q is the
/// smaller tail, kept inside a bracket that bisection falls back on.
pub fn gamma_p_inverse(a: f64, p: f64) -> f64 {
    if p <= 0.0 {
        return 0.0;
    }
    if p >= 1.0 {
        return f64::INFINITY;
    }
    let upper_tail = p > 0.5;
    let target = if upper_tail { 1.0 - p } else { p };
    // f(x) = tail(x) - target, increasing in x for P, decreasing for Q.
    let excess = |x: f64| {
        let (lower, upper) = gamma_pq(a, x);
        if upper_tail {
            target - upper
        } else {
            lower - target
        }
    };
    let (mut lo, mut hi) = (0.0, a.max(1.0));
    while excess(hi) < 0.0 {
        lo = hi;
        hi *= 2.0;
    }
    let mut x = 0.5 * (lo + hi);
    for _ in 0..200 {
        let f = excess(x);
        if f == 0.0 {
            return x;
        }
        if f < 0.0 {
            lo = x;
        } else {
            hi = x;
        }
        let density = gamma_prefactor(a, x) / x;
        let mut next = x - f / density;
        if !(next > lo && next < hi) {
            next = 0.5 * (lo + hi);
        }
        if (next - x).abs() <= 4.0 * f64::EPSILON * x {
            return next;
        }
        x = next;
    }
    x
}

/// `scipy.stats.chi2.ppf(p, df)`: the value a chi-squared variable with `df`
/// degrees of freedom falls below with probability `p`.
pub fn chi2_ppf(p: f64, df: f64) -> f64 {
    2.0 * gamma_p_inverse(df / 2.0, p)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ln_gamma_at_known_points() {
        assert!(ln_gamma(1.0).abs() < 1e-15);
        assert!(ln_gamma(2.0).abs() < 1e-15);
        let half = (std::f64::consts::PI.sqrt()).ln();
        assert!((ln_gamma(0.5) - half).abs() < 1e-15);
        // ln(10!) = ln Γ(11)
        assert!((ln_gamma(11.0) - 3_628_800_f64.ln()).abs() < 1e-13);
    }

    #[test]
    fn chi2_quantiles_match_scipy() {
        // scipy.stats.chi2.ppf(0.95, df) under SciPy 1.18.1.
        let scipy = [
            (1.0, 3.841_458_820_694_120_5),
            (2.0, 5.991_464_547_107_98),
            (3.0, 7.814_727_903_251_176_5),
            (4.0, 9.487_729_036_781_154),
            (5.0, 11.070_497_693_516_351),
            (7.0, 14.067_140_449_340_167),
            (10.0, 18.307_038_053_275_146),
            (25.0, 37.652_484_133_482_766),
            (50.0, 67.504_806_549_541_2),
            (100.0, 124.342_113_404_004_08),
            (250.0, 287.881_500_521_830_8),
            (1000.0, 1_074.679_448_803_441),
        ];
        for (df, want) in scipy {
            let got = chi2_ppf(0.95, df);
            assert!(
                ((got - want) / want).abs() < 1e-13,
                "df={df}: {got} != {want}"
            );
        }
        // And off the 95% level, below and above a half.
        assert!((chi2_ppf(0.3, 3.0) - 1.423_652_243_035_279_6).abs() < 1e-13);
        assert!((chi2_ppf(0.999, 1.0) - 10.827_566_170_662_733).abs() < 1e-12);
    }
}
