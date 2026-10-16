//! SciPy's `CubicSpline(x, y)` with its default not-a-knot ends, for
//! `CalibrationCurve::interpolate_cubic`.
//!
//! Built as SciPy builds it: the first derivative at each point from a
//! tridiagonal system (solved with partial pivoting, as LAPACK's `dgtsv`,
//! which SciPy calls for it), then each interval's cubic in powers of the
//! distance from its left end, evaluated term by term as SciPy's `PPoly`
//! does. Two and three points are SciPy's special cases: a straight line, and
//! the parabola through all three.

use crate::Error;
use crate::numeric::diff;

#[derive(Debug, Clone)]
pub struct CubicSpline {
    x: Vec<f64>,
    /// Per interval, the coefficients of `s^3, s^2, s, 1` with `s = t - x[i]`.
    c: Vec<[f64; 4]>,
}

impl CubicSpline {
    /// The not-a-knot cubic spline through `(x, y)`; `x` strictly ascending,
    /// at least two points.
    pub fn not_a_knot(x: &[f64], y: &[f64]) -> Result<Self, Error> {
        let n = x.len();
        if n < 2 || y.len() != n {
            return Err(Error::Spline("`x` must contain at least 2 elements."));
        }
        let dx = diff(x);
        if dx.iter().any(|&d| d.is_nan() || d <= 0.0) {
            return Err(Error::Spline("`x` must be strictly increasing sequence."));
        }
        let slope: Vec<f64> = diff(y).iter().zip(&dx).map(|(dy, d)| dy / d).collect();

        let s = match n {
            // The end conditions become first derivatives equal to the one
            // slope there is: a straight line.
            2 => vec![slope[0], slope[0]],
            // Both not-a-knot conditions are the same condition here, so SciPy
            // fits the parabola through the three points instead.
            3 => solve3(
                [
                    [1.0, 1.0, 0.0],
                    [dx[1], 2.0 * (dx[0] + dx[1]), dx[0]],
                    [0.0, 1.0, 1.0],
                ],
                [
                    2.0 * slope[0],
                    3.0 * (dx[0] * slope[1] + dx[1] * slope[0]),
                    2.0 * slope[1],
                ],
            )?,
            _ => {
                // Row i of the system, for 0 < i < n-1:
                //   dx[i] s[i-1] + 2 (dx[i-1] + dx[i]) s[i] + dx[i-1] s[i+1]
                //     = 3 (dx[i] slope[i-1] + dx[i-1] slope[i])
                let mut lower = vec![0.0; n - 1];
                let mut diag = vec![0.0; n];
                let mut upper = vec![0.0; n - 1];
                let mut b = vec![0.0; n];
                for i in 1..n - 1 {
                    diag[i] = 2.0 * (dx[i - 1] + dx[i]);
                    upper[i] = dx[i - 1];
                    lower[i - 1] = dx[i];
                    b[i] = 3.0 * (dx[i] * slope[i - 1] + dx[i - 1] * slope[i]);
                }
                // Not-a-knot: the third derivative is continuous across the
                // second point and the second-to-last.
                let d = x[2] - x[0];
                diag[0] = dx[1];
                upper[0] = d;
                b[0] = ((dx[0] + 2.0 * d) * dx[1] * slope[0] + dx[0] * dx[0] * slope[1]) / d;
                let d = x[n - 1] - x[n - 3];
                diag[n - 1] = dx[n - 3];
                lower[n - 2] = d;
                b[n - 1] = (dx[n - 2] * dx[n - 2] * slope[n - 3]
                    + (2.0 * d + dx[n - 2]) * dx[n - 3] * slope[n - 2])
                    / d;
                gtsv(lower, diag, upper, b)?
            }
        };

        let c = (0..n - 1)
            .map(|i| {
                let t = (s[i] + s[i + 1] - 2.0 * slope[i]) / dx[i];
                [t / dx[i], (slope[i] - s[i]) / dx[i] - t, s[i], y[i]]
            })
            .collect();
        Ok(Self { x: x.to_vec(), c })
    }

    /// The spline at `t`, extrapolating the end cubics beyond the knots.
    pub fn eval(&self, t: f64) -> f64 {
        let i = self.interval(t);
        let s = t - self.x[i];
        let [c0, c1, c2, c3] = self.c[i];
        // PPoly's order: ascending powers, each power built by repeated
        // multiplication.
        let mut res = 0.0 + c3;
        let mut z = s;
        res += c2 * z;
        z *= s;
        res += c1 * z;
        z *= s;
        res += c0 * z;
        res
    }

    /// The interval `i` with `x[i] <= t < x[i+1]`, closed on the right at the
    /// last knot, and the end intervals beyond the ends.
    fn interval(&self, t: f64) -> usize {
        let n = self.x.len();
        if t.is_nan() || t <= self.x[0] {
            return 0;
        }
        if t >= self.x[n - 1] {
            return n - 2;
        }
        self.x.partition_point(|&v| v <= t) - 1
    }
}

/// LAPACK `dgtsv` for one right-hand side: Gaussian elimination with partial
/// pivoting on a tridiagonal system, then back substitution. `lower[i]` is
/// the element below `diag[i]`, `upper[i]` the one to its right.
fn gtsv(
    mut dl: Vec<f64>,
    mut d: Vec<f64>,
    mut du: Vec<f64>,
    mut b: Vec<f64>,
) -> Result<Vec<f64>, Error> {
    let n = d.len();
    let singular = Error::Spline("singular matrix");
    for i in 0..n - 1 {
        if d[i].abs() >= dl[i].abs() {
            if d[i] == 0.0 {
                return Err(singular);
            }
            let fact = dl[i] / d[i];
            d[i + 1] -= fact * du[i];
            b[i + 1] -= fact * b[i];
            dl[i] = 0.0;
        } else {
            // Rows i and i+1 swap; dl[i] then holds the fill-in two to the
            // right of the diagonal.
            let fact = d[i] / dl[i];
            d[i] = dl[i];
            let temp = d[i + 1];
            d[i + 1] = du[i] - fact * temp;
            if i + 1 < n - 1 {
                dl[i] = du[i + 1];
                du[i + 1] = -fact * dl[i];
            }
            du[i] = temp;
            let temp = b[i];
            b[i] = b[i + 1];
            b[i + 1] = temp - fact * b[i + 1];
        }
    }
    if d[n - 1] == 0.0 {
        return Err(singular);
    }
    b[n - 1] /= d[n - 1];
    if n > 1 {
        b[n - 2] = (b[n - 2] - du[n - 2] * b[n - 1]) / d[n - 2];
    }
    for i in (0..n.saturating_sub(2)).rev() {
        b[i] = (b[i] - du[i] * b[i + 1] - dl[i] * b[i + 2]) / d[i];
    }
    Ok(b)
}

/// A 3x3 system by Gaussian elimination with partial pivoting, as LAPACK's
/// `dgesv` solves SciPy's three-point case.
fn solve3(mut a: [[f64; 3]; 3], mut b: [f64; 3]) -> Result<Vec<f64>, Error> {
    for k in 0..3 {
        let p = (k..3)
            .max_by(|&i, &j| a[i][k].abs().total_cmp(&a[j][k].abs()).then(j.cmp(&i)))
            .unwrap();
        if a[p][k] == 0.0 {
            return Err(Error::Spline("singular matrix"));
        }
        a.swap(k, p);
        b.swap(k, p);
        let pivot = a[k];
        for i in k + 1..3 {
            let f = a[i][k] / pivot[k];
            for (aij, akj) in a[i][k..].iter_mut().zip(&pivot[k..]) {
                *aij -= f * akj;
            }
            b[i] -= f * b[k];
        }
    }
    let mut s = [0.0; 3];
    for i in (0..3).rev() {
        let mut v = b[i];
        for j in i + 1..3 {
            v -= a[i][j] * s[j];
        }
        s[i] = v / a[i][i];
    }
    Ok(s.to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reproduces_a_cubic_exactly() {
        // Not-a-knot reproduces any cubic, so the spline through samples of
        // one is that cubic, between the samples and beyond them.
        let f = |t: f64| 2.0 * t * t * t - 3.0 * t * t + t - 5.0;
        let x = [0.0, 0.5, 2.0, 3.0, 4.5, 7.0];
        let y: Vec<f64> = x.iter().map(|&t| f(t)).collect();
        let spline = CubicSpline::not_a_knot(&x, &y).unwrap();
        for t in [0.0, 0.1, 1.3, 2.0, 3.7, 6.9, 7.0, 8.0, -1.0] {
            assert!((spline.eval(t) - f(t)).abs() < 1e-9, "{t}");
        }
    }

    #[test]
    fn three_points_make_a_parabola_and_two_a_line() {
        let x = [0.0, 1.0, 3.0];
        let y = [1.0, 2.0, 10.0]; // 1 + 0 t + t^2
        let spline = CubicSpline::not_a_knot(&x, &y).unwrap();
        assert!((spline.eval(2.0) - 5.0).abs() < 1e-12);
        let line = CubicSpline::not_a_knot(&[0.0, 2.0], &[1.0, 5.0]).unwrap();
        assert_eq!(line.eval(1.0), 3.0);
    }

    #[test]
    fn refuses_what_scipy_refuses() {
        assert!(CubicSpline::not_a_knot(&[1.0], &[1.0]).is_err());
        assert!(CubicSpline::not_a_knot(&[0.0, 1.0, 1.0], &[0.0; 3]).is_err());
    }
}
