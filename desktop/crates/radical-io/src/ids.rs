//! Element and date ids: a prefix and twelve hex digits, as the Python's
//! `f"{prefix}-{uuid.uuid4().hex[:12]}"` makes them. Behind a trait so tests
//! can see which id is which.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};

pub trait Ids {
    /// The next id, `prefix-` and twelve lowercase hex digits.
    fn next(&mut self, prefix: &str) -> String;
}

/// Random ids: 48 bits from a randomly keyed hash of a counter, which is all
/// a single user's draft needs to keep its elements apart.
#[derive(Debug, Default)]
pub struct RandomIds {
    state: RandomState,
    counter: u64,
}

impl Ids for RandomIds {
    fn next(&mut self, prefix: &str) -> String {
        self.counter += 1;
        let mut h = self.state.build_hasher();
        h.write_u64(self.counter);
        format!("{prefix}-{:012x}", h.finish() & 0xffff_ffff_ffff)
    }
}

/// Sequential ids, `prefix-000000000001` onwards: deterministic, for tests.
#[derive(Debug, Default)]
pub struct CountingIds(u64);

impl Ids for CountingIds {
    fn next(&mut self, prefix: &str) -> String {
        self.0 += 1;
        format!("{prefix}-{:012x}", self.0)
    }
}
