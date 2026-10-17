//! `hashlib.sha1(...).hexdigest()`.

use sha1::{Digest, Sha1};

pub fn sha1_hex(bytes: &[u8]) -> String {
    Sha1::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
