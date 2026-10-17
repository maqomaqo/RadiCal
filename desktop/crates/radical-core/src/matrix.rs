//! A dense row-major matrix of doubles, and the few operations the exact
//! solvers take from NumPy: products, transposes, diagonals and reductions.
//!
//! The solvers' matrices are indexed [younger boundary, older boundary] on a
//! calendar grid of up to 2,000 points, so one is 32 MB and a product is
//! 8 x 10^9 multiply-adds. NumPy hands those products to BLAS; here they are
//! a cache-blocked loop split across threads by rows of the result. Neither
//! adds the products in a fixed order, so a product agrees with NumPy's to
//! rounding rather than to the bit -- and every number that comes out of the
//! message passing with it. Elementwise work and reductions follow NumPy's
//! order exactly ([`crate::numeric`]).

use crate::numeric;

#[derive(Debug, Clone, PartialEq)]
pub struct Matrix {
    rows: usize,
    cols: usize,
    data: Vec<f64>,
}

impl Matrix {
    pub fn filled(rows: usize, cols: usize, value: f64) -> Self {
        Self {
            rows,
            cols,
            data: vec![value; rows * cols],
        }
    }

    pub fn zeros(rows: usize, cols: usize) -> Self {
        Self::filled(rows, cols, 0.0)
    }

    pub fn from_fn(rows: usize, cols: usize, mut f: impl FnMut(usize, usize) -> f64) -> Self {
        let mut data = Vec::with_capacity(rows * cols);
        for r in 0..rows {
            for c in 0..cols {
                data.push(f(r, c));
            }
        }
        Self { rows, cols, data }
    }

    pub fn from_rows(rows: usize, cols: usize, data: Vec<f64>) -> Self {
        assert_eq!(
            data.len(),
            rows * cols,
            "matrix data does not fit its shape"
        );
        Self { rows, cols, data }
    }

    /// `numpy.diag(v)`.
    pub fn diag(v: &[f64]) -> Self {
        let n = v.len();
        let mut m = Self::zeros(n, n);
        for (i, &x) in v.iter().enumerate() {
            m.data[i * n + i] = x;
        }
        m
    }

    pub fn rows(&self) -> usize {
        self.rows
    }

    pub fn cols(&self) -> usize {
        self.cols
    }

    /// The elements, row after row.
    pub fn data(&self) -> &[f64] {
        &self.data
    }

    pub fn data_mut(&mut self) -> &mut [f64] {
        &mut self.data
    }

    pub fn into_data(self) -> Vec<f64> {
        self.data
    }

    pub fn get(&self, r: usize, c: usize) -> f64 {
        self.data[r * self.cols + c]
    }

    pub fn set(&mut self, r: usize, c: usize, value: f64) {
        self.data[r * self.cols + c] = value;
    }

    pub fn row(&self, r: usize) -> &[f64] {
        &self.data[r * self.cols..(r + 1) * self.cols]
    }

    pub fn row_mut(&mut self, r: usize) -> &mut [f64] {
        &mut self.data[r * self.cols..(r + 1) * self.cols]
    }

    /// Column `c` as a new vector.
    pub fn column(&self, c: usize) -> Vec<f64> {
        (0..self.rows).map(|r| self.get(r, c)).collect()
    }

    pub fn transpose(&self) -> Self {
        let mut out = Self::zeros(self.cols, self.rows);
        const BLOCK: usize = 64;
        for r0 in (0..self.rows).step_by(BLOCK) {
            for c0 in (0..self.cols).step_by(BLOCK) {
                for r in r0..(r0 + BLOCK).min(self.rows) {
                    for c in c0..(c0 + BLOCK).min(self.cols) {
                        out.data[c * self.rows + r] = self.data[r * self.cols + c];
                    }
                }
            }
        }
        out
    }

    /// The main diagonal, `numpy.diagonal`.
    pub fn diagonal(&self) -> Vec<f64> {
        (0..self.rows.min(self.cols))
            .map(|i| self.get(i, i))
            .collect()
    }

    /// `numpy.fill_diagonal` with one value per cell.
    pub fn set_diagonal(&mut self, values: &[f64]) {
        for (i, &v) in values.iter().enumerate().take(self.rows.min(self.cols)) {
            self.set(i, i, v);
        }
    }

    pub fn map(&self, f: impl Fn(f64) -> f64) -> Self {
        Self {
            rows: self.rows,
            cols: self.cols,
            data: self.data.iter().map(|&x| f(x)).collect(),
        }
    }

    pub fn map_inplace(&mut self, f: impl Fn(f64) -> f64) {
        for x in &mut self.data {
            *x = f(*x);
        }
    }

    /// Elementwise with another matrix of the same shape.
    pub fn zip_map(&self, other: &Self, f: impl Fn(f64, f64) -> f64) -> Self {
        assert_eq!((self.rows, self.cols), (other.rows, other.cols));
        Self {
            rows: self.rows,
            cols: self.cols,
            data: self
                .data
                .iter()
                .zip(&other.data)
                .map(|(&a, &b)| f(a, b))
                .collect(),
        }
    }

    pub fn zip_inplace(&mut self, other: &Self, f: impl Fn(f64, f64) -> f64) {
        assert_eq!((self.rows, self.cols), (other.rows, other.cols));
        for (a, &b) in self.data.iter_mut().zip(&other.data) {
            *a = f(*a, b);
        }
    }

    /// `numpy.max` over every element: NaN if any is NaN.
    pub fn max(&self) -> f64 {
        let mut best = f64::NEG_INFINITY;
        for &x in &self.data {
            if x.is_nan() {
                return x;
            }
            if x > best {
                best = x;
            }
        }
        best
    }

    /// `numpy.sum` over every element of a contiguous array: pairwise.
    pub fn sum(&self) -> f64 {
        numeric::sum(&self.data)
    }

    /// `m.sum(axis=1)`: each row summed pairwise, as NumPy reduces a
    /// contiguous axis.
    pub fn row_sums(&self) -> Vec<f64> {
        (0..self.rows).map(|r| numeric::sum(self.row(r))).collect()
    }

    /// `m.sum(axis=0)`: the rows added one after another, as NumPy reduces
    /// the outer axis.
    pub fn column_sums(&self) -> Vec<f64> {
        let mut out = vec![0.0; self.cols];
        for r in 0..self.rows {
            for (o, &x) in out.iter_mut().zip(self.row(r)) {
                *o += x;
            }
        }
        out
    }

    /// `self @ v`.
    pub fn mat_vec(&self, v: &[f64]) -> Vec<f64> {
        assert_eq!(self.cols, v.len());
        (0..self.rows)
            .map(|r| {
                let mut s = 0.0;
                for (&a, &b) in self.row(r).iter().zip(v) {
                    s += a * b;
                }
                s
            })
            .collect()
    }

    /// `v @ self`.
    pub fn vec_mat(v: &[f64], m: &Self) -> Vec<f64> {
        assert_eq!(m.rows, v.len());
        let mut out = vec![0.0; m.cols];
        for (r, &a) in v.iter().enumerate() {
            if a == 0.0 {
                continue;
            }
            for (o, &b) in out.iter_mut().zip(m.row(r)) {
                *o += a * b;
            }
        }
        out
    }

    /// `self @ other`.
    pub fn matmul(&self, other: &Self) -> Self {
        assert_eq!(self.cols, other.rows, "matmul shapes do not align");
        let (m, k, n) = (self.rows, self.cols, other.cols);
        let mut out = Self::zeros(m, n);
        if m == 0 || n == 0 || k == 0 {
            return out;
        }
        let threads = std::thread::available_parallelism()
            .map_or(1, usize::from)
            .min(m.div_ceil(16))
            .max(1);
        let rows_per = m.div_ceil(threads);
        std::thread::scope(|scope| {
            for (chunk, c) in out.data.chunks_mut(rows_per * n).enumerate() {
                let first = chunk * rows_per;
                scope.spawn(move || gemm_rows(self, other, c, first, k, n));
            }
        });
        out
    }
}

/// Rows `first..` of `a @ b` into `c`, blocked over the inner dimension so a
/// band of `b` stays in cache while every row of the chunk passes over it.
/// A zero in `a` is skipped: the solvers' matrices are triangular, and every
/// value they hold is finite, so the product it would add is an exact zero.
fn gemm_rows(a: &Matrix, b: &Matrix, c: &mut [f64], first: usize, k: usize, n: usize) {
    const KC: usize = 128;
    let rows = c.len() / n;
    for p0 in (0..k).step_by(KC) {
        let p1 = (p0 + KC).min(k);
        for i in 0..rows {
            let a_row = &a.row(first + i)[p0..p1];
            let c_row = &mut c[i * n..(i + 1) * n];
            for (p, &aip) in (p0..p1).zip(a_row) {
                if aip == 0.0 {
                    continue;
                }
                let b_row = b.row(p);
                for (cj, &bj) in c_row.iter_mut().zip(b_row) {
                    *cj += aip * bj;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn products_and_transposes_agree_with_their_definitions() {
        let a = Matrix::from_fn(37, 53, |r, c| ((r * 7 + c * 3) % 11) as f64 - 4.0);
        let b = Matrix::from_fn(53, 29, |r, c| ((r * 5 + c) % 13) as f64 * 0.5);
        let c = a.matmul(&b);
        for r in 0..37 {
            for col in 0..29 {
                let want: f64 = (0..53).map(|p| a.get(r, p) * b.get(p, col)).sum();
                assert_eq!(c.get(r, col), want);
            }
        }
        assert_eq!(a.transpose().transpose(), a);
        let v: Vec<f64> = (0..53).map(|i| i as f64).collect();
        let w: Vec<f64> = (0..37).map(|i| (i % 3) as f64).collect();
        assert_eq!(
            a.mat_vec(&v),
            a.matmul(&Matrix::from_rows(53, 1, v.clone())).into_data()
        );
        assert_eq!(
            Matrix::vec_mat(&w, &a),
            Matrix::from_rows(1, 37, w.clone()).matmul(&a).into_data()
        );
    }
}
