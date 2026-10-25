//! Accounts, for RadiCal on a server (`api/auth.py`). RadiCal Desktop runs
//! without them: one person, no sign-in, every row theirs.
//!
//! In accounts mode every `/api/` route but the few that come before signing
//! in asks for a bearer token -- a sign-in token, or a personal access token
//! (`rct_...`) -- checked once, in [`require_caller`], before the route runs.
//! A route learns who is asking from its [`Caller`], and asks it which rows it
//! may see rather than checking a token itself.
//!
//! The rules the rest of the API depends on are the backend's:
//!
//! - **Someone else's row is a 404, not a 403**, so an id cannot be used to
//!   find out whether something exists.
//! - **Every failure to sign in is the same 401**, worded the same: what was
//!   wrong with a token is useful to an attacker and to nobody else.
//! - **A read-only access token may only read**, on every route, and no
//!   access token may make tokens or change the password.

pub mod mail;
pub mod tokens;

use axum::extract::{FromRequestParts, Request, State as Extract};
use axum::http::request::Parts;
use axum::http::{Method, StatusCode, header};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use radical_store::Timestamp;
use radical_store::rows::{self, PersonalAccessToken, User};
use serde_json::{Value, json};

use crate::{ApiError, State};

/// What signs every token, from `RADICAL_SECRET_KEY`.
#[derive(Clone)]
pub struct Accounts {
    pub secret: String,
}

/// The key `RADICAL_SECRET_KEY` takes when nobody has set one, named so that
/// it is obvious in a log, a token or a search that nobody has.
const DEVELOPMENT_SECRET: &str = "radical-development-secret-do-not-use-in-a-deployment";

impl Accounts {
    /// The configuration from the environment. A deployment (`RADICAL_ENV`
    /// other than development) must set its own key: the development one is
    /// in the source, and anyone with the source could sign a token with it.
    pub fn from_env() -> Result<Self, String> {
        let configured = std::env::var("RADICAL_SECRET_KEY").unwrap_or_default();
        let configured = configured.trim();
        if !configured.is_empty() {
            return Ok(Self {
                secret: configured.to_owned(),
            });
        }
        let env = std::env::var("RADICAL_ENV").unwrap_or_else(|_| "development".to_owned());
        if !["development", "dev", "test"].contains(&env.to_lowercase().as_str()) {
            return Err(
                "RADICAL_SECRET_KEY is not set. It signs every sign-in token, so a \
                        deployment without one is a deployment where anyone holding this \
                        source can mint a token for any account. Set it to a long random \
                        string, and keep it out of version control."
                    .to_owned(),
            );
        }
        eprintln!(
            "⚠ RADICAL_SECRET_KEY is not set, so sign-in tokens are signed with the published \
             development key. Fine on a laptop; never on a server."
        );
        Ok(Self {
            secret: DEVELOPMENT_SECRET.to_owned(),
        })
    }
}

/// Who is asking.
#[derive(Debug, Clone, PartialEq)]
pub enum Caller {
    /// RadiCal Desktop's one person, who owns everything.
    Local,
    /// A signed-in account; `access_token` is the personal access token it
    /// came with, when it did not come with a sign-in token.
    User {
        id: String,
        access_token: Option<String>,
    },
}

impl Caller {
    /// The account whose rows these are, or None on the desktop.
    pub fn owner(&self) -> Option<&str> {
        match self {
            Self::Local => None,
            Self::User { id, .. } => Some(id),
        }
    }

    /// For the routes that manage tokens and the password: refused to a
    /// personal access token, so a leaked one cannot make itself a successor
    /// or lock its owner out.
    pub fn browser(&self) -> Result<&str, ApiError> {
        match self {
            Self::User {
                id,
                access_token: None,
            } => Ok(id),
            Self::User { .. } => Err(ApiError::new(
                StatusCode::FORBIDDEN,
                "Access tokens and the password are managed from a signed-in browser, \
                 not with an access token.",
            )),
            Self::Local => Err(ApiError::not_found("Not Found")),
        }
    }
}

impl FromRequestParts<State> for Caller {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, state: &State) -> Result<Self, ApiError> {
        if let Some(caller) = parts.extensions.get::<Self>() {
            return Ok(caller.clone());
        }
        if state.accounts.is_none() {
            return Ok(Self::Local);
        }
        // A route outside require_caller that asked anyway.
        Err(unauthorised(SIGN_IN))
    }
}

/// The routes that come before signing in.
const OPEN: &[&str] = &[
    "/api/health",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/options",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
];

const SIGN_IN: &str = "Sign in to use this.";
const EXPIRED: &str = "Your session has expired. Sign in again.";
const BAD_ACCESS_TOKEN: &str =
    "That access token is not valid: it is wrong, has expired, or was revoked.";
/// last_used_at is a guide, not an audit log: written at most this often.
const LAST_USED_RESOLUTION_MICROS: i64 = 60 * 1_000_000;

/// A 401, with the header that says a bearer token is what is wanted.
fn unauthorised(detail: &str) -> ApiError {
    ApiError::new(StatusCode::UNAUTHORIZED, detail)
}

fn with_challenge(error: ApiError) -> Response {
    let mut response = error.into_response();
    if response.status() == StatusCode::UNAUTHORIZED {
        response
            .headers_mut()
            .insert(header::WWW_AUTHENTICATE, "Bearer".parse().expect("ascii"));
    }
    response
}

/// The account a bearer token names, as the backend's `current_user`.
fn identify(
    state: &State,
    accounts: &Accounts,
    method: &Method,
    header: &str,
) -> Result<Caller, ApiError> {
    let (scheme, token) = header.split_once(' ').unwrap_or((header, ""));
    let token = token.trim();
    if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() {
        return Err(unauthorised(SIGN_IN));
    }
    if tokens::is_access_token(token) {
        return by_access_token(state, method, token);
    }
    let user_id = tokens::read_token(&accounts.secret, token).map_err(|_| unauthorised(EXPIRED))?;
    let user: Option<User> = state.store.read(|c| rows::get(c, &user_id))?;
    match user {
        Some(user) => Ok(Caller::User {
            id: user.id,
            access_token: None,
        }),
        None => Err(unauthorised(EXPIRED)),
    }
}

fn by_access_token(state: &State, method: &Method, token: &str) -> Result<Caller, ApiError> {
    let hash = tokens::access_token_hash(token);
    let now = Timestamp::now();
    let row: Option<PersonalAccessToken> = state
        .store
        .read(|c| rows::select_where(c, "token_hash = ?", [&hash], ""))?
        .into_iter()
        .next();
    let Some(row) = row.filter(|r| r.expires_at > now) else {
        return Err(unauthorised(BAD_ACCESS_TOKEN));
    };
    let user: Option<User> = state.store.read(|c| rows::get(c, &row.user_id))?;
    let Some(user) = user else {
        return Err(unauthorised(BAD_ACCESS_TOKEN));
    };
    // A read-only token is refused anything but a read, whatever the route.
    if row.scope != "full" && !matches!(*method, Method::GET | Method::HEAD | Method::OPTIONS) {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "This access token is read-only.",
        ));
    }
    if row
        .last_used_at
        .is_none_or(|t| now.micros - t.micros >= LAST_USED_RESOLUTION_MICROS)
    {
        state.store.write(|tx| {
            tx.execute(
                "UPDATE personal_access_tokens SET last_used_at = ? WHERE id = ?",
                radical_store::rusqlite::params![now, row.id],
            )?;
            Ok(())
        })?;
    }
    Ok(Caller::User {
        id: user.id,
        access_token: Some(row.id),
    })
}

/// In accounts mode, who is asking, for every `/api/` route but the open
/// ones: refused with a 401 before the route runs, or handed to it as a
/// [`Caller`].
pub async fn require_caller(
    Extract(state): Extract<State>,
    mut request: Request,
    next: Next,
) -> Response {
    let Some(accounts) = state.accounts.clone() else {
        return next.run(request).await;
    };
    let path = request.uri().path();
    if !path.starts_with("/api/") || OPEN.contains(&path) {
        return next.run(request).await;
    }
    let header = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let method = request.method().clone();
    let identified =
        tokio::task::spawn_blocking(move || identify(&state, &accounts, &method, &header))
            .await
            .unwrap_or_else(|e| Err(ApiError::internal(e)));
    match identified {
        Ok(caller) => {
            request.extensions_mut().insert(caller);
            next.run(request).await
        }
        Err(error) => with_challenge(error),
    }
}

/// A user as the API reports one. Never the hash.
pub fn user_json(user: &User) -> Value {
    json!({
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_first_account": user.is_first_account,
        "created_at": user.created_at.map(|t| t.isoformat()),
    })
}

/// Set `email`'s password from the server itself (`app/reset_password.py`),
/// for an installation that cannot send email: there is no admin account,
/// and access to the server is the authority, since it holds the database.
pub fn reset_password(
    store: &radical_store::Store,
    email: &str,
    password: &str,
) -> Result<(), String> {
    if password.chars().count() < 8 {
        return Err("A password needs at least 8 characters.".to_owned());
    }
    let hash = tokens::hash_password(password)?;
    let email = tokens::normalise_email(email);
    let changed = store
        .write(|tx| {
            Ok(tx.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?",
                [&hash, &email],
            )?)
        })
        .map_err(|e| e.to_string())?;
    if changed == 0 {
        return Err(format!("There is no account for {email}."));
    }
    Ok(())
}

// --------------------------------------------------------------------------
// Email addresses, as Pydantic's EmailStr checks them
// --------------------------------------------------------------------------

/// Names no address may use (email-validator's special-use domains).
const SPECIAL_USE: &[&str] = &["arpa", "invalid", "local", "localhost", "onion", "test"];

/// Why `email` is not an address, in email-validator's words, or None when it
/// is one.
pub fn email_problem(email: &str) -> Option<&'static str> {
    let ats = email.matches('@').count();
    if ats == 0 {
        return Some("An email address must have an @-sign.");
    }
    let (local, domain) = email.rsplit_once('@')?;
    if local.is_empty() {
        return Some("There must be something before the @-sign.");
    }
    if domain.is_empty() {
        return Some("There must be something after the @-sign.");
    }
    if ats > 1 {
        return Some("The email address is not valid. It must have exactly one @-sign.");
    }
    if local.starts_with('.') {
        return Some("An email address cannot start with a period.");
    }
    if local.ends_with('.') {
        return Some("An email address cannot have a period immediately before the @-sign.");
    }
    if local.contains("..") || domain.contains("..") {
        return Some("An email address cannot have two periods in a row.");
    }
    if local
        .chars()
        .any(|c| c.is_whitespace() || "\"(),:;<>[\\]".contains(c))
    {
        return Some("The email address contains invalid characters before the @-sign.");
    }
    if domain.starts_with('.') {
        return Some("An email address cannot have a period immediately after the @-sign.");
    }
    if domain.ends_with('.') {
        return Some("An email address cannot end with a period.");
    }
    if domain
        .chars()
        .any(|c| !(c.is_alphanumeric() || c == '-' || c == '.'))
    {
        return Some("The part after the @-sign contains invalid characters.");
    }
    if !domain.contains('.') {
        return Some("The part after the @-sign is not valid. It should have a period.");
    }
    let last = domain.rsplit('.').next().unwrap_or("").to_lowercase();
    if SPECIAL_USE.contains(&last.as_str()) {
        return Some(
            "The part after the @-sign is a special-use or reserved name that cannot be used \
             with email.",
        );
    }
    None
}

#[cfg(test)]
mod tests {
    use super::email_problem;

    #[test]
    fn addresses_are_judged_as_pydantic_judges_them() {
        assert_eq!(email_problem("someone@radical.example.com"), None);
        assert_eq!(
            email_problem("not-an-address"),
            Some("An email address must have an @-sign.")
        );
        assert!(email_problem("@x.org").is_some());
        assert!(email_problem("a@x").is_some());
        assert!(email_problem("a@b@x.org").is_some());
        assert!(email_problem("a@host.test").is_some());
    }
}
