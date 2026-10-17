//! The NumPy operations the ported services lean on, reproduced in NumPy's own
//! order of operations.
//!
//! Matching the order is what lets a port agree with the Python code to the
//! last bit wherever the platform's `exp` and `ln` agree, rather than only to
//! a tolerance. It matters most where a result is a *choice* rather than a
//! number: an HPD set takes whole grid points until a cumulative sum crosses a
//! level, so a sum that differs in its last bit can take one point more or
//! less, and a sort that orders tied values differently can take a different
//! one of two equal points, miles apart on the calendar axis.
//!
//! Each function names the NumPy call it stands for and was checked against
//! NumPy 2.5 (the version `backend/requirements.txt` pins) on random and
//! tie-heavy inputs.

use std::cmp::Ordering;

/// `numpy.sum` of a contiguous float64 array: pairwise summation, in blocks of
/// eight partial sums, splitting at a multiple of eight above 128 elements.
/// Not the plain left-to-right sum; the two differ in the last bits.
pub fn sum(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = 0.0;
        for &x in a {
            res += x;
        }
        res
    } else if n <= 128 {
        let mut r = [0.0; 8];
        r.copy_from_slice(&a[..8]);
        let mut i = 8;
        while i < n - n % 8 {
            for (j, acc) in r.iter_mut().enumerate() {
                *acc += a[i + j];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for &x in &a[i..] {
            res += x;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        sum(&a[..n2]) + sum(&a[n2..])
    }
}

/// Python's builtin `sum()` over floats, which since Python 3.12 is
/// compensated (Neumaier's improvement on Kahan's algorithm): a running sum and
/// a running correction, the correction added once at the end if it is finite.
/// Not the same as `numpy.sum`, and not the plain running sum.
pub fn python_sum(items: impl IntoIterator<Item = f64>) -> f64 {
    let (mut total, mut c) = (0.0_f64, 0.0_f64);
    for x in items {
        let t = total + x;
        if total.abs() >= x.abs() {
            c += (total - t) + x;
        } else {
            c += (x - t) + total;
        }
        total = t;
    }
    if c != 0.0 && c.is_finite() {
        total += c;
    }
    total
}

/// `numpy.cumsum`: a plain running sum, unlike `numpy.sum`.
pub fn cumsum(a: &[f64]) -> Vec<f64> {
    let mut total = 0.0;
    a.iter()
        .map(|&x| {
            total += x;
            total
        })
        .collect()
}

/// `numpy.diff` of a 1-D array.
pub fn diff(a: &[f64]) -> Vec<f64> {
    a.windows(2).map(|w| w[1] - w[0]).collect()
}

/// `numpy.linspace(start, stop, n)`: `i * step + start` with
/// `step = (stop - start) / (n - 1)`, and the last value forced to `stop`.
pub fn linspace(start: f64, stop: f64, n: usize) -> Vec<f64> {
    match n {
        0 => Vec::new(),
        1 => vec![start],
        _ => {
            let step = (stop - start) / (n - 1) as f64;
            let mut out: Vec<f64> = if step == 0.0 {
                // NumPy divides first and multiplies by the span when the
                // step underflows to zero.
                let delta = stop - start;
                (0..n)
                    .map(|i| i as f64 / (n - 1) as f64 * delta + start)
                    .collect()
            } else {
                (0..n).map(|i| i as f64 * step + start).collect()
            };
            out[n - 1] = stop;
            out
        }
    }
}

/// `numpy.arange(start, stop, step)` for floats: `ceil((stop - start) / step)`
/// values, the second being `start + step` and the rest `start + i * delta`
/// where `delta` is the difference of the first two -- which is how NumPy
/// fills the array, and not quite `start + i * step`.
pub fn arange(start: f64, stop: f64, step: f64) -> Vec<f64> {
    let length = ((stop - start) / step).ceil();
    if length.is_nan() || length <= 0.0 {
        return Vec::new();
    }
    let length = length as usize;
    let mut out = Vec::with_capacity(length);
    out.push(start);
    if length > 1 {
        let next = start + step;
        out.push(next);
        let delta = next - start;
        out.extend((2..length).map(|i| start + i as f64 * delta));
    }
    out
}

/// The index of the first element `>= key` in an ascending slice:
/// `numpy.searchsorted(a, key, side='left')`.
pub fn searchsorted_left(a: &[f64], key: f64) -> usize {
    a.partition_point(|&v| v < key)
}

/// The index of the first element `> key` in an ascending slice:
/// `numpy.searchsorted(a, key, side='right')`.
pub fn searchsorted_right(a: &[f64], key: f64) -> usize {
    a.partition_point(|&v| v <= key)
}

/// `numpy.interp(x, xp, fp)` at one point: clamped to `fp`'s end values
/// outside `xp`, exact at a grid point, and otherwise
/// `slope * (x - xp[j]) + fp[j]`, retried from the right-hand point if that is
/// NaN. `xp` must be ascending and non-empty.
pub fn interp_one(x: f64, xp: &[f64], fp: &[f64]) -> f64 {
    let n = xp.len();
    if x.is_nan() {
        return x;
    }
    // The last j with xp[j] <= x, as NumPy's binary search finds it.
    let above = searchsorted_right(xp, x);
    if above == 0 {
        return fp[0];
    }
    let j = above - 1;
    if j == n - 1 {
        // At the last point exactly, or beyond it.
        return fp[n - 1];
    }
    if xp[j] == x {
        return fp[j];
    }
    let slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
    let mut value = slope * (x - xp[j]) + fp[j];
    if value.is_nan() {
        value = slope * (x - xp[j + 1]) + fp[j + 1];
        if value.is_nan() && fp[j] == fp[j + 1] {
            value = fp[j];
        }
    }
    value
}

/// `numpy.interp` over many points.
pub fn interp(x: &[f64], xp: &[f64], fp: &[f64]) -> Vec<f64> {
    x.iter().map(|&v| interp_one(v, xp, fp)).collect()
}

/// NumPy's ordering of floats for sorting: NaN after everything else.
fn less(a: f64, b: f64) -> bool {
    a < b || (b.is_nan() && !a.is_nan())
}

/// `numpy.argmax`: the first index of the largest value (NaN counts as
/// largest), or None for an empty slice.
pub fn argmax(a: &[f64]) -> Option<usize> {
    if a.is_empty() {
        return None;
    }
    if let Some(nan) = a.iter().position(|v| v.is_nan()) {
        return Some(nan);
    }
    let mut best = 0;
    for (i, &v) in a.iter().enumerate().skip(1) {
        if v > a[best] {
            best = i;
        }
    }
    Some(best)
}

/// `numpy.argsort(v, kind='stable')`: ties keep their order.
pub fn argsort_stable(v: &[f64]) -> Vec<usize> {
    let mut order: Vec<usize> = (0..v.len()).collect();
    order.sort_by(|&a, &b| {
        if less(v[a], v[b]) {
            Ordering::Less
        } else if less(v[b], v[a]) {
            Ordering::Greater
        } else {
            Ordering::Equal
        }
    });
    order
}

/// Partitions at or below this length are finished by insertion sort.
const SMALL_QUICKSORT: usize = 15;

/// `numpy.argsort(v)` with the default kind: NumPy's introsort (median-of-three
/// quicksort, insertion sort for short partitions, heapsort past a depth
/// limit), step for step.
///
/// The default kind is not stable, and the order it leaves TIED values in is
/// part of the answer wherever the first few of a sorted list are taken -- the
/// expected-resolution HPD set is one, and a one-year grid on a curve given in
/// whole years is full of exact ties. So this reproduces NumPy's tie order,
/// which a stable sort, or any other quicksort, does not.
pub fn argsort_quicksort(v: &[f64]) -> Vec<usize> {
    let num = v.len();
    let mut t: Vec<usize> = (0..num).collect();
    if num < 2 {
        return t;
    }
    let mut stack: Vec<(usize, usize, i64)> = Vec::new();
    let (mut pl, mut pr) = (0usize, num - 1);
    let mut cdepth = 2 * i64::from(usize::BITS - 1 - num.leading_zeros());
    loop {
        if cdepth < 0 {
            heapsort_indices(v, &mut t[pl..=pr]);
        } else {
            while pr - pl > SMALL_QUICKSORT {
                let pm = pl + ((pr - pl) >> 1);
                if less(v[t[pm]], v[t[pl]]) {
                    t.swap(pm, pl);
                }
                if less(v[t[pr]], v[t[pm]]) {
                    t.swap(pr, pm);
                }
                if less(v[t[pm]], v[t[pl]]) {
                    t.swap(pm, pl);
                }
                let vp = v[t[pm]];
                let mut pi = pl;
                let mut pj = pr - 1;
                t.swap(pm, pj);
                loop {
                    pi += 1;
                    while less(v[t[pi]], vp) {
                        pi += 1;
                    }
                    pj -= 1;
                    while less(vp, v[t[pj]]) {
                        pj -= 1;
                    }
                    if pi >= pj {
                        break;
                    }
                    t.swap(pi, pj);
                }
                t.swap(pi, pr - 1);
                // The larger partition waits on the stack.
                cdepth -= 1;
                if pi - pl < pr - pi {
                    stack.push((pi + 1, pr, cdepth));
                    pr = pi - 1;
                } else {
                    stack.push((pl, pi - 1, cdepth));
                    pl = pi + 1;
                }
            }
            for pi in pl + 1..=pr {
                let vi = t[pi];
                let vp = v[vi];
                let mut pj = pi;
                while pj > pl && less(vp, v[t[pj - 1]]) {
                    t[pj] = t[pj - 1];
                    pj -= 1;
                }
                t[pj] = vi;
            }
        }
        match stack.pop() {
            Some((l, r, d)) => {
                pl = l;
                pr = r;
                cdepth = d;
            }
            None => break,
        }
    }
    t
}

/// NumPy's `aheapsort`, the introsort's fallback, on a sub-slice of indices.
fn heapsort_indices(v: &[f64], t: &mut [usize]) {
    // One-based, as NumPy indexes its heap.
    let mut n = t.len();
    let at = |k: usize| k - 1;
    let mut l = n >> 1;
    while l > 0 {
        let tmp = t[at(l)];
        let (mut i, mut j) = (l, l << 1);
        while j <= n {
            if j < n && less(v[t[at(j)]], v[t[at(j + 1)]]) {
                j += 1;
            }
            if less(v[tmp], v[t[at(j)]]) {
                t[at(i)] = t[at(j)];
                i = j;
                j += j;
            } else {
                break;
            }
        }
        t[at(i)] = tmp;
        l -= 1;
    }
    while n > 1 {
        let tmp = t[at(n)];
        t[at(n)] = t[at(1)];
        n -= 1;
        let (mut i, mut j) = (1, 2);
        while j <= n {
            if j < n && less(v[t[at(j)]], v[t[at(j + 1)]]) {
                j += 1;
            }
            if less(v[tmp], v[t[at(j)]]) {
                t[at(i)] = t[at(j)];
                i = j;
                j += j;
            } else {
                break;
            }
        }
        t[at(i)] = tmp;
    }
}

/// `numpy.mean` of a 1-D array: the pairwise [`sum`] divided by the count.
pub fn mean(a: &[f64]) -> f64 {
    sum(a) / a.len() as f64
}

/// `numpy.std(a, ddof=ddof)`: the mean as [`mean`], the squared deviations
/// summed pairwise, divided by `n - ddof`, square-rooted.
pub fn std(a: &[f64], ddof: usize) -> f64 {
    let centre = mean(a);
    let squares: Vec<f64> = a
        .iter()
        .map(|&x| {
            let d = x - centre;
            d * d
        })
        .collect();
    (sum(&squares) / (a.len() - ddof) as f64).sqrt()
}

/// `numpy.median`: the middle value, or the mean of the two middle values as
/// `(a + b) / 2`.
pub fn median(a: &[f64]) -> f64 {
    let mut sorted = a.to_vec();
    sorted.sort_by(f64::total_cmp);
    let n = sorted.len();
    if n % 2 == 1 {
        sorted[n / 2]
    } else {
        (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0
    }
}

/// `numpy.percentile(a, q)` with the default (linear) method: [`quantile`] at
/// `q / 100`, the division done first as NumPy does it.
pub fn percentile(a: &[f64], q: f64) -> f64 {
    quantile(a, q / 100.0)
}

/// `numpy.quantile(a, q)` with the default (linear) method: the virtual
/// index `(n - 1) * q`, and NumPy's `_lerp` between its neighbours --
/// `a + d * t` below the midpoint, `b - d * (1 - t)` from it on, so that the
/// result is exact at both ends.
pub fn quantile(a: &[f64], q: f64) -> f64 {
    let mut sorted = a.to_vec();
    sorted.sort_by(f64::total_cmp);
    quantile_sorted(&sorted, q)
}

/// [`quantile`] of an already ascending slice.
pub fn quantile_sorted(sorted: &[f64], q: f64) -> f64 {
    let n = sorted.len();
    let virtual_index = (n - 1) as f64 * q;
    if virtual_index >= (n - 1) as f64 {
        return sorted[n - 1];
    }
    if virtual_index < 0.0 {
        return sorted[0];
    }
    let previous = virtual_index.floor();
    let gamma = virtual_index - previous;
    let (lo, hi) = (sorted[previous as usize], sorted[previous as usize + 1]);
    let difference = hi - lo;
    if gamma >= 0.5 {
        hi - difference * (1.0 - gamma)
    } else {
        lo + difference * gamma
    }
}

/// `numpy.interp(x, xp, fp, left=left, right=right)`: [`interp_one`] inside
/// `xp`, and the given values beyond either end.
pub fn interp_bounded(x: &[f64], xp: &[f64], fp: &[f64], left: f64, right: f64) -> Vec<f64> {
    let (first, last) = (xp[0], xp[xp.len() - 1]);
    x.iter()
        .map(|&v| {
            if v < first {
                left
            } else if v > last {
                right
            } else {
                interp_one(v, xp, fp)
            }
        })
        .collect()
}

/// `numpy.convolve(a, v, mode='same')` for an odd-length `v` no longer than
/// `a`: the full convolution's central `a.len()` values. Each is summed in
/// index order; NumPy hands the same products to BLAS's `ddot`, whose order
/// is the library's own, so the two agree to rounding rather than to the bit.
pub fn convolve_same(a: &[f64], v: &[f64]) -> Vec<f64> {
    debug_assert!(v.len() % 2 == 1 && v.len() <= a.len());
    let half = v.len() / 2;
    (0..a.len())
        .map(|k| {
            // out[k] = sum_i a[i] * v[k + half - i], over the i that index both.
            let m = k + half;
            let i_lo = m.saturating_sub(v.len() - 1);
            let i_hi = m.min(a.len() - 1);
            let mut total = 0.0;
            for i in i_lo..=i_hi {
                total += a[i] * v[m - i];
            }
            total
        })
        .collect()
}

/// Python's `max(a, b)`: the first unless the second is larger.
pub fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Python's `min(a, b)`: the first unless the second is smaller.
pub fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// `numpy.histogram(values, bins=edges)` with explicit ascending edges: the
/// count in each `[e_k, e_(k+1))`, the last bin closed on the right too, and
/// values outside the edges left out -- the cumulative-search form NumPy uses
/// for edges it is given.
pub fn histogram(values: &[f64], edges: &[f64]) -> Vec<usize> {
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    let n = edges.len();
    let cumulative: Vec<usize> = edges
        .iter()
        .enumerate()
        .map(|(i, &e)| {
            if i + 1 < n {
                searchsorted_left(&sorted, e)
            } else {
                searchsorted_right(&sorted, e)
            }
        })
        .collect();
    cumulative.windows(2).map(|w| w[1] - w[0]).collect()
}

/// `numpy.unique` of a float array: its distinct values, ascending.
pub fn unique(values: &[f64]) -> Vec<f64> {
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    sorted.dedup_by(|a, b| a == b);
    sorted
}

/// Python's `round(x, ndigits)` for a float: the exact binary value rounded
/// to `ndigits` decimals, ties to even, and read back.
pub fn round_ndigits(x: f64, ndigits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{x:.ndigits$}")
        .parse()
        .expect("a formatted float parses")
}

/// `scipy.signal.find_peaks(x, prominence=minimum)`: the indices of the local
/// maxima whose prominence is at least `minimum`, in order.
///
/// A plateau counts as one peak at its middle sample (rounding down), and
/// neither end of `x` can be one -- SciPy's `_local_maxima_1d`. A peak's
/// prominence is its height above the higher of the lowest points reached
/// walking out either side until something higher than it, or the end
/// (`_peak_prominences` with no window).
pub fn find_peaks(x: &[f64], minimum: f64) -> Vec<usize> {
    let n = x.len();
    let mut peaks = Vec::new();
    if n < 3 {
        return peaks;
    }
    let mut i = 1;
    let i_max = n - 1;
    while i < i_max {
        if x[i - 1] < x[i] {
            let mut ahead = i + 1;
            while ahead < i_max && x[ahead] == x[i] {
                ahead += 1;
            }
            if x[ahead] < x[i] {
                peaks.push((i + ahead - 1) / 2);
                i = ahead;
            }
        }
        i += 1;
    }
    peaks.retain(|&peak| {
        let height = x[peak];
        let mut left_min = height;
        let mut j = peak as isize;
        while j >= 0 && x[j as usize] <= height {
            left_min = left_min.min(x[j as usize]);
            j -= 1;
        }
        let mut right_min = height;
        let mut j = peak;
        while j < n && x[j] <= height {
            right_min = right_min.min(x[j]);
            j += 1;
        }
        minimum <= height - left_min.max(right_min)
    });
    peaks
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pairwise_sum_splits_where_numpy_does() {
        // 0.1 summed 1000 times: the naive running sum drifts, NumPy's
        // pairwise one is the value it prints.
        let a = vec![0.1; 1000];
        assert_eq!(sum(&a), 100.00000000000001);
        let naive: f64 = a.iter().sum();
        assert_ne!(sum(&a), naive);
        // And Python's own sum() compensates, so it lands on 100 exactly.
        assert_eq!(python_sum(a.iter().copied()), 100.0);
        assert_eq!(sum(&[]), 0.0);
        assert_eq!(sum(&[1.0, 2.0, 3.0]), 6.0);
    }

    #[test]
    fn interp_clamps_and_hits_grid_points() {
        let xp = [0.0, 1.0, 3.0];
        let fp = [10.0, 20.0, 40.0];
        assert_eq!(
            interp(&[-1.0, 0.0, 0.5, 1.0, 2.0, 3.0, 9.0], &xp, &fp),
            [10.0, 10.0, 15.0, 20.0, 30.0, 40.0, 40.0]
        );
        assert!(interp_one(f64::NAN, &xp, &fp).is_nan());
    }

    #[test]
    fn arange_counts_like_numpy() {
        assert_eq!(arange(0.0, 3.0, 1.0), [0.0, 1.0, 2.0]);
        assert_eq!(arange(0.0, 3.5, 1.0), [0.0, 1.0, 2.0, 3.0]);
        assert_eq!(arange(0.0, 1.0, 0.5), [0.0, 0.5]);
        assert!(arange(3.0, 1.0, 1.0).is_empty());
    }

    #[test]
    fn quicksort_tie_order_is_numpys() {
        // np.argsort of this array; ties are not left in index order.
        let x: Vec<f64> = (0..36).map(|i| f64::from(i % 2)).collect();
        assert_eq!(
            argsort_quicksort(&x),
            [
                0, 20, 18, 34, 16, 26, 14, 12, 24, 10, 28, 8, 30, 6, 4, 32, 2, 22, 27, 31, 25, 33,
                29, 17, 21, 19, 15, 13, 11, 9, 7, 5, 3, 1, 23, 35
            ]
        );
        // Short partitions are insertion-sorted, which is stable.
        let short: Vec<f64> = (0..16).map(|i| f64::from(i % 2)).collect();
        assert_eq!(argsort_quicksort(&short), argsort_stable(&short));
    }

    #[test]
    fn heapsort_sorts() {
        let v = [5.0, 1.0, 4.0, 1.0, 3.0, f64::NAN, 2.0];
        let mut t: Vec<usize> = (0..v.len()).collect();
        heapsort_indices(&v, &mut t);
        let sorted: Vec<f64> = t.iter().map(|&i| v[i]).collect();
        assert_eq!(&sorted[..6], [1.0, 1.0, 2.0, 3.0, 4.0, 5.0]);
        assert!(sorted[6].is_nan());
    }

    #[test]
    fn histogram_closes_the_last_bin() {
        let edges = [0.0, 1.0, 2.0, 3.0];
        assert_eq!(
            histogram(&[-1.0, 0.0, 0.5, 1.0, 2.999, 3.0, 3.5], &edges),
            [2, 1, 2]
        );
        assert_eq!(round_ndigits(0.0625, 3), 0.062);
        assert_eq!(round_ndigits(2.675, 2), 2.67);
        assert_eq!(unique(&[3.0, 1.0, 3.0, 2.0]), [1.0, 2.0, 3.0]);
    }

    #[test]
    fn argmax_takes_the_first_peak() {
        assert_eq!(argmax(&[1.0, 3.0, 3.0, 2.0]), Some(1));
        assert_eq!(argmax(&[]), None);
        assert_eq!(argmax(&[1.0, f64::NAN, 5.0]), Some(1));
    }
}
