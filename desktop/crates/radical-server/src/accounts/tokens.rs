//! Passwords and bearer tokens (`services/auth.py`): the arithmetic, with no
//! HTTP in it.
//!
//! Made to interoperate with the backend, not only to resemble it: a server
//! moved from the backend keeps its `RADICAL_SECRET_KEY` and its database, so
//! a session token signed by the backend, a password hashed by it and a
//! personal access token it issued must all keep working here, and the other
//! way round. Hence HS256 JWTs laid out as PyJWT lays them out, bcrypt at the
//! same cost, and the same SHA-256 of an access token's secret.

use base64::Engine;
use base64::engine::general_purpose::{URL_SAFE, URL_SAFE_NO_PAD};
use hmac::{Hmac, Mac};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

/// How long a sign-in token lasts.
pub const TOKEN_TTL_SECONDS: i64 = 12 * 3600;
/// How long a password-reset link works.
pub const RESET_TTL_SECONDS: i64 = 3600;
const RESET_PURPOSE: &str = "password-reset";
/// bcrypt's work factor, as the backend's.
const COST: u32 = 12;

/// A token that cannot be trusted. Never says which of the reasons it was.
#[derive(Debug)]
pub struct AuthError;

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64)
}

// --------------------------------------------------------------------------
// Passwords
// --------------------------------------------------------------------------

/// A bcrypt hash of `password`, or why it cannot be one: bcrypt ignores
/// everything past 72 bytes, and a passphrase whose end is silently dropped is
/// weaker than it looks.
pub fn hash_password(password: &str) -> Result<String, String> {
    if password.len() > 72 {
        return Err(
            "That password is too long for bcrypt, which ignores anything past 72 bytes. \
                    Use a shorter one -- a long passphrase whose end is silently discarded is \
                    weaker than it looks."
                .to_owned(),
        );
    }
    bcrypt::hash(password, COST).map_err(|e| e.to_string())
}

/// Whether `password` matches the stored hash; false for a hash that cannot
/// be read, as for a wrong password.
pub fn verify_password(password: &str, hash: &str) -> bool {
    !hash.is_empty() && bcrypt::verify(password, hash).unwrap_or(false)
}

/// The stored form of an address: trimmed and case-folded.
pub fn normalise_email(email: &str) -> String {
    email.trim().to_lowercase()
}

// --------------------------------------------------------------------------
// HS256, as PyJWT writes and reads it
// --------------------------------------------------------------------------

fn sign(secret: &str, message: &[u8]) -> Vec<u8> {
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("any key length");
    mac.update(message);
    mac.finalize().into_bytes().to_vec()
}

/// Claims in the order given, as PyJWT keeps a dict's order (a JSON map here
/// would sort them).
fn encode(secret: &str, claims: &[(&str, Value)]) -> String {
    // PyJWT's header and compact JSON, so a token here is byte for byte the
    // token the backend would have made.
    let header = URL_SAFE_NO_PAD.encode(br#"{"alg":"HS256","typ":"JWT"}"#);
    let body: Vec<String> = claims
        .iter()
        .map(|(k, v)| format!("{}:{v}", Value::from(*k)))
        .collect();
    let payload = URL_SAFE_NO_PAD.encode(format!("{{{}}}", body.join(",")));
    let signing = format!("{header}.{payload}");
    let signature = URL_SAFE_NO_PAD.encode(sign(secret, signing.as_bytes()));
    format!("{signing}.{signature}")
}

fn b64(part: &str) -> Result<Vec<u8>, AuthError> {
    let trimmed = part.trim_end_matches('=');
    URL_SAFE_NO_PAD
        .decode(trimmed)
        .or_else(|_| URL_SAFE.decode(part))
        .map_err(|_| AuthError)
}

/// The claims of an HS256 token signed with `secret`. Only HS256 is accepted,
/// whatever the token's header names: a decoder that trusts the header takes
/// `alg: none`. Expiry and issue time are checked as PyJWT checks them.
fn decode(secret: &str, token: &str) -> Result<serde_json::Map<String, Value>, AuthError> {
    let mut parts = token.split('.');
    let (Some(header), Some(payload), Some(signature), None) =
        (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return Err(AuthError);
    };
    let head: Value = serde_json::from_slice(&b64(header)?).map_err(|_| AuthError)?;
    if head["alg"] != "HS256" {
        return Err(AuthError);
    }
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("any key length");
    mac.update(format!("{header}.{payload}").as_bytes());
    mac.verify_slice(&b64(signature)?).map_err(|_| AuthError)?;
    let claims: Value = serde_json::from_slice(&b64(payload)?).map_err(|_| AuthError)?;
    let Value::Object(claims) = claims else {
        return Err(AuthError);
    };
    let at = now();
    let number = |key: &str| -> Result<Option<i64>, AuthError> {
        match claims.get(key) {
            None => Ok(None),
            Some(v) => v.as_f64().map(|x| Some(x as i64)).ok_or(AuthError),
        }
    };
    if number("exp")?.is_some_and(|exp| exp <= at) {
        return Err(AuthError);
    }
    if number("nbf")?.is_some_and(|nbf| nbf > at) {
        return Err(AuthError);
    }
    if number("iat")?.is_some_and(|iat| iat > at) {
        return Err(AuthError);
    }
    Ok(claims)
}

fn subject(claims: &serde_json::Map<String, Value>) -> Result<String, AuthError> {
    claims
        .get("sub")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .ok_or(AuthError)
}

/// A sign-in token for `user_id`.
pub fn create_token(secret: &str, user_id: &str) -> String {
    let issued = now();
    encode(
        secret,
        &[
            ("sub", json!(user_id)),
            ("iat", json!(issued)),
            ("exp", json!(issued + TOKEN_TTL_SECONDS)),
        ],
    )
}

/// The user a sign-in token is for. A reset link is signed with the same key
/// and names the same user, so one is refused here: it is not a session.
pub fn read_token(secret: &str, token: &str) -> Result<String, AuthError> {
    let claims = decode(secret, token)?;
    if claims.contains_key("purpose") {
        return Err(AuthError);
    }
    subject(&claims)
}

/// What a reset token remembers of the password it was issued against: it
/// stops working once the password changes, with no table of issued links.
fn fingerprint(password_hash: &str) -> String {
    hex(&Sha256::digest(password_hash.as_bytes()))[..32].to_owned()
}

/// A one-use, hour-long token for setting a new password.
pub fn create_reset_token(secret: &str, user_id: &str, password_hash: &str) -> String {
    let issued = now();
    encode(
        secret,
        &[
            ("sub", json!(user_id)),
            ("purpose", json!(RESET_PURPOSE)),
            ("pwd", json!(fingerprint(password_hash))),
            ("iat", json!(issued)),
            ("exp", json!(issued + RESET_TTL_SECONDS)),
        ],
    )
}

/// The user a reset token is for, while their password is still the one it
/// was issued against.
pub fn read_reset_token(
    secret: &str,
    token: &str,
    password_hash_of: impl FnOnce(&str) -> Option<String>,
) -> Result<String, AuthError> {
    let claims = decode(secret, token)?;
    if claims.get("purpose").and_then(Value::as_str) != Some(RESET_PURPOSE) {
        return Err(AuthError);
    }
    let user = subject(&claims)?;
    let current = password_hash_of(&user).ok_or(AuthError)?;
    let given = claims.get("pwd").and_then(Value::as_str).unwrap_or("");
    if !same(given.as_bytes(), fingerprint(&current).as_bytes()) {
        return Err(AuthError);
    }
    Ok(user)
}

fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

// --------------------------------------------------------------------------
// Personal access tokens
// --------------------------------------------------------------------------

/// What marks a personal access token, in a script, a log, or a secret
/// scanner's rules.
pub const ACCESS_TOKEN_PREFIX: &str = "rct_";
/// The lifetimes offered, in days.
pub const ACCESS_TOKEN_LIFETIMES: [i64; 4] = [7, 30, 90, 365];
pub const ACCESS_TOKEN_DEFAULT_LIFETIME: i64 = 90;
/// Characters of the secret kept to identify a token in the list.
const SHOWN_PREFIX: usize = 12;

/// A fresh secret: the prefix and 256 random bits, as `secrets.token_urlsafe(32)`.
pub fn new_access_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("the system's random source");
    format!("{ACCESS_TOKEN_PREFIX}{}", URL_SAFE_NO_PAD.encode(bytes))
}

pub fn is_access_token(token: &str) -> bool {
    token.starts_with(ACCESS_TOKEN_PREFIX)
}

/// What is stored and looked up: a fast hash is enough for a random secret.
pub fn access_token_hash(token: &str) -> String {
    hex(&Sha256::digest(token.as_bytes()))
}

/// The part shown in the list, which names a token but cannot be used as one.
pub fn access_token_prefix(token: &str) -> String {
    token.chars().take(SHOWN_PREFIX).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const SECRET: &str = "radical-development-secret-do-not-use-in-a-deployment";

    #[test]
    fn a_sign_in_token_reads_back_and_a_tampered_one_does_not() {
        let token = create_token(SECRET, "u1");
        assert_eq!(read_token(SECRET, &token).unwrap(), "u1");
        assert!(read_token("another key", &token).is_err());
        let mut forged = token.clone();
        forged.push('x');
        assert!(read_token(SECRET, &forged).is_err());
        // alg: none is refused, whatever the signature.
        let none = format!(
            "{}.{}.",
            URL_SAFE_NO_PAD.encode(br#"{"alg":"none","typ":"JWT"}"#),
            URL_SAFE_NO_PAD.encode(br#"{"sub":"u1"}"#)
        );
        assert!(read_token(SECRET, &none).is_err());
    }

    #[test]
    fn an_expired_token_and_a_reset_token_are_not_sessions() {
        let expired = encode(
            SECRET,
            &[("sub", json!("u1")), ("iat", json!(1)), ("exp", json!(2))],
        );
        assert!(read_token(SECRET, &expired).is_err());
        let reset = create_reset_token(SECRET, "u1", "$2b$12$hash");
        assert!(read_token(SECRET, &reset).is_err());
    }

    #[test]
    fn a_reset_token_works_only_against_the_password_it_was_issued_for() {
        let reset = create_reset_token(SECRET, "u1", "$2b$12$old");
        assert_eq!(
            read_reset_token(SECRET, &reset, |_| Some("$2b$12$old".into())).unwrap(),
            "u1"
        );
        assert!(read_reset_token(SECRET, &reset, |_| Some("$2b$12$new".into())).is_err());
        assert!(read_reset_token(SECRET, &reset, |_| None).is_err());
        let session = create_token(SECRET, "u1");
        assert!(read_reset_token(SECRET, &session, |_| Some("x".into())).is_err());
    }

    /// A token and a password hash the backend made (PyJWT and bcrypt, the
    /// development secret), read here: what keeps sessions and passwords
    /// working when a server moves from the backend.
    #[test]
    fn what_the_backend_signed_and_hashed_is_read_here() {
        // jwt.encode({'sub': 'backend-user', 'iat': 1792713440, 'exp': 4102444800}, SECRET, 'HS256')
        let token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.\
            eyJzdWIiOiJiYWNrZW5kLXVzZXIiLCJpYXQiOjE3OTI3MTM0NDAsImV4cCI6NDEwMjQ0NDgwMH0.\
            BLp7PTY_Rpx66a3FhnhZvF55zQatcm347AyS0j7h-cQ";
        assert_eq!(read_token(SECRET, token).unwrap(), "backend-user");
        // And this side writes it byte for byte as PyJWT does.
        assert_eq!(
            encode(
                SECRET,
                &[
                    ("sub", json!("backend-user")),
                    ("iat", json!(1792713440)),
                    ("exp", json!(4102444800_i64)),
                ]
            ),
            token
        );
        // bcrypt.hashpw(b'a good enough password', bcrypt.gensalt(rounds=12))
        let hash = "$2b$12$6pk.1hCzY7nDZtpBDM73H.sHNYF17ijrpTuGGV9yE0VrzyLZt0QxO";
        assert!(verify_password("a good enough password", hash));
        assert!(!verify_password("another password", hash));
    }

    #[test]
    fn passwords_hash_as_bcrypt_and_refuse_past_72_bytes() {
        let hash = hash_password("a good enough password").unwrap();
        assert!(hash.starts_with("$2b$12$"), "{hash}");
        assert!(verify_password("a good enough password", &hash));
        assert!(!verify_password("a wrong password", &hash));
        assert!(!verify_password("anything", "not a hash"));
        assert!(hash_password(&"é".repeat(37)).is_err());
    }

    #[test]
    fn access_tokens_look_and_hash_as_the_backends() {
        let t = new_access_token();
        assert!(t.starts_with("rct_") && t.len() == 4 + 43, "{t}");
        assert_ne!(t, new_access_token());
        assert_eq!(access_token_prefix(&t).len(), 12);
        // hashlib.sha256(b'rct_abc').hexdigest()
        assert_eq!(
            access_token_hash("rct_abc"),
            "c3fc937d8e9fcfe31196479c7bcede2ff13a3aedcb4b9f0e7d591702d9f312f5"
        );
    }
}
