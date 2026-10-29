//! `cblas_dgemm` from Apple's Accelerate framework, behind a safe function.
//!
//! On Apple Silicon Accelerate runs a matrix product on the matrix
//! coprocessor, which no Rust code can reach; for the exact solvers'
//! 2,000-point products it is about twice as fast as faer. The legacy LP64
//! symbol is used, present on every macOS RadiCal Desktop supports (11 and
//! later). On any other platform the crate is empty.

#[cfg(target_os = "macos")]
mod ffi {
    // CBLAS_ORDER and CBLAS_TRANSPOSE, from Accelerate's cblas.h.
    pub const ROW_MAJOR: i32 = 101;
    pub const NO_TRANS: i32 = 111;

    #[link(name = "Accelerate", kind = "framework")]
    #[allow(unsafe_code)]
    unsafe extern "C" {
        #[allow(clippy::too_many_arguments)]
        pub fn cblas_dgemm(
            order: i32,
            trans_a: i32,
            trans_b: i32,
            m: i32,
            n: i32,
            k: i32,
            alpha: f64,
            a: *const f64,
            lda: i32,
            b: *const f64,
            ldb: i32,
            beta: f64,
            c: *mut f64,
            ldc: i32,
        );
    }
}

/// `c = a @ b` for row-major `a` [m, k], `b` [k, n] and `c` [m, n].
///
/// Panics if a slice does not fit its shape, or a dimension does not fit the
/// 32-bit integers the LP64 interface takes.
#[cfg(target_os = "macos")]
pub fn dgemm(m: usize, k: usize, n: usize, a: &[f64], b: &[f64], c: &mut [f64]) {
    assert_eq!(a.len(), m * k, "a does not fit its shape");
    assert_eq!(b.len(), k * n, "b does not fit its shape");
    assert_eq!(c.len(), m * n, "c does not fit its shape");
    let dim = |d: usize| i32::try_from(d).expect("dimension exceeds the LP64 interface");
    let (mi, ki, ni) = (dim(m), dim(k), dim(n));
    if m == 0 || n == 0 {
        return;
    }
    // A leading dimension must be at least 1 even when that axis is empty.
    let (lda, ldb, ldc) = (ki.max(1), ni, ni);
    // SAFETY: the asserts above make each pointer address exactly the
    // row-major [rows, leading dimension] block cblas_dgemm reads or writes,
    // `c` is borrowed mutably so it aliases neither input, and beta = 0 means
    // c's previous contents are never read.
    #[allow(unsafe_code)]
    unsafe {
        ffi::cblas_dgemm(
            ffi::ROW_MAJOR,
            ffi::NO_TRANS,
            ffi::NO_TRANS,
            mi,
            ni,
            ki,
            1.0,
            a.as_ptr(),
            lda,
            b.as_ptr(),
            ldb,
            0.0,
            c.as_mut_ptr(),
            ldc,
        );
    }
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::dgemm;

    #[test]
    fn products_agree_with_their_definition() {
        let (m, k, n) = (37, 53, 29);
        let a: Vec<f64> = (0..m * k).map(|i| ((i * 7) % 11) as f64 - 4.0).collect();
        let b: Vec<f64> = (0..k * n).map(|i| ((i * 5) % 13) as f64 * 0.5).collect();
        let mut c = vec![f64::NAN; m * n];
        dgemm(m, k, n, &a, &b, &mut c);
        for r in 0..m {
            for col in 0..n {
                let want: f64 = (0..k).map(|p| a[r * k + p] * b[p * n + col]).sum();
                assert_eq!(c[r * n + col], want);
            }
        }
    }

    #[test]
    fn an_empty_inner_dimension_gives_zeros() {
        let mut c = vec![f64::NAN; 6];
        dgemm(2, 0, 3, &[], &[], &mut c);
        assert_eq!(c, vec![0.0; 6]);
    }
}
