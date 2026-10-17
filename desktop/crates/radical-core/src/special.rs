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

/// cephes' `polevl`: a polynomial with coefficients highest power first,
/// each step one fused multiply-add as the compiler contracts it in SciPy's
/// build.
fn polevl(x: f64, coef: &[f64]) -> f64 {
    let mut ans = coef[0];
    for &c in &coef[1..] {
        ans = ans.mul_add(x, c);
    }
    ans
}

/// cephes' `p1evl`: as [`polevl`] with a leading coefficient of 1 left out.
fn p1evl(x: f64, coef: &[f64]) -> f64 {
    let mut ans = x + coef[0];
    for &c in &coef[1..] {
        ans = ans.mul_add(x, c);
    }
    ans
}

/// `scipy.special.gammaln(x)` for `x > 0`: cephes' `lgam`, step for step --
/// a rational approximation after shifting the argument into [2, 3) below
/// 13, Stirling's series above. Where [`ln_gamma`] is good to about one part
/// in 10^15, this is the value SciPy returns, which the exact solver's span
/// correction sums and differences at arguments in the thousands.
///
/// SciPy's C++ is compiled with floating-point contraction on, so each
/// `a * b + c` in it is one fused multiply-add; so it is here. Checked bit for
/// bit against SciPy 1.18 at 4,000 random arguments either side of 13.
pub fn gammaln(x: f64) -> f64 {
    const A: [f64; 5] = [
        8.116_141_674_705_085e-4,
        -5.950_619_042_843_014e-4,
        7.936_503_404_577_169e-4,
        -2.777_777_777_300_997e-3,
        8.333_333_333_333_319e-2,
    ];
    const B: [f64; 6] = [
        -1.378_251_525_691_208_6e3,
        -3.880_163_151_346_378_4e4,
        -3.316_129_927_388_712e5,
        -1.162_370_974_927_623e6,
        -1.721_737_008_208_396_6e6,
        -8.535_556_642_457_654e5,
    ];
    const C: [f64; 6] = [
        -3.518_157_014_365_234_5e2,
        -1.706_421_066_518_811_5e4,
        -2.205_285_905_538_544_5e5,
        -1.139_334_443_679_825_1e6,
        -2.532_523_071_775_829_4e6,
        -2.018_891_414_335_327_7e6,
    ];
    const LS2PI: f64 = 0.918_938_533_204_672_7;
    if x.is_nan() || x <= 0.0 || x.is_infinite() {
        return if x.is_nan() { x } else { f64::INFINITY };
    }
    if x < 13.0 {
        let mut z = 1.0;
        let mut p = 0.0;
        let mut u = x;
        while u >= 3.0 {
            p -= 1.0;
            u = x + p;
            z *= u;
        }
        while u < 2.0 {
            z /= u;
            p += 1.0;
            u = x + p;
        }
        if u == 2.0 {
            return z.ln();
        }
        p -= 2.0;
        let x = x + p;
        let p = x * polevl(x, &B) / p1evl(x, &C);
        return z.ln() + p;
    }
    let mut q = (x - 0.5).mul_add(x.ln(), -x) + LS2PI;
    if x > 1.0e8 {
        return q;
    }
    let p = 1.0 / (x * x);
    if x >= 1000.0 {
        q += 7.936_507_936_507_937e-4f64
            .mul_add(p, -2.777_777_777_777_778e-3)
            .mul_add(p, 0.083_333_333_333_333_33)
            / x;
    } else {
        q += polevl(p, &A) / x;
    }
    q
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
