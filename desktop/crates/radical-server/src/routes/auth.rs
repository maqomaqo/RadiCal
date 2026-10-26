//! `api/auth.py`. On RadiCal Desktop, what is left of it: the sign-in page's
//! question, answered with `single_user` so the frontend goes straight in,
//! and who "you" are. On a server (accounts mode), all of it: registering,
//! signing in, passwords and their reset, and personal access tokens.

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};
use std::time::{Duration, Instant};

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, State as Extract};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use radical_store::Timestamp;
use radical_store::rows::{self, PersonalAccessToken, User};
use serde_json::{Value, json};

use crate::accounts::{self, Accounts, Caller, mail, tokens};
use crate::validate::{self, Bounds, Problems};
use crate::{ApiError, ApiResult, State, ok};

/// The one local user, as `_user_json` reports a user.
pub fn local_user() -> Value {
    json!({
        "id": "local",
        "email": "",
        "display_name": null,
        "is_first_account": true,
        "created_at": null,
    })
}

fn accounts_of(state: &State) -> ApiResult<&Accounts> {
    state
        .accounts
        .as_ref()
        .ok_or_else(|| ApiError::not_found("Not Found"))
}

fn created(data: Value) -> Response {
    (StatusCode::CREATED, ok(data)).into_response()
}

fn user_by_email(state: &State, email: &str) -> ApiResult<Option<User>> {
    Ok(state
        .store
        .read(|c| rows::select_where::<User>(c, "email = ?", [email], ""))?
        .into_iter()
        .next())
}

/// Hash off the request's thread: bcrypt is slow on purpose.
async fn hash(password: String) -> ApiResult<String> {
    tokio::task::spawn_blocking(move || tokens::hash_password(&password))
        .await
        .map_err(ApiError::internal)?
        .map_err(|e| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, e))
}

async fn verify(password: String, hash: String) -> bool {
    tokio::task::spawn_blocking(move || tokens::verify_password(&password, &hash))
        .await
        .unwrap_or(false)
}

/// The token and user a successful sign-in answers with.
fn signed_in(accounts: &Accounts, user: &User) -> Value {
    json!({
        "token": tokens::create_token(&accounts.secret, &user.id),
        "user": accounts::user_json(user),
    })
}

// --------------------------------------------------------------------------
// Signing in
// --------------------------------------------------------------------------

/// Create an account and sign it in. The first account adopts every project,
/// analysis and stored curve from before there were accounts, which would
/// otherwise be visible to nobody.
async fn register(Extract(state): Extract<State>, body: Bytes) -> ApiResult<Response> {
    let accounts = accounts_of(&state)?.clone();
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let (email, password, display_name) = {
        let mut f = validate::body(&input, "RegisterRequest", &mut problems);
        (
            f.email("email"),
            f.string("password", Some(8), Some(72)),
            f.opt_str("display_name", Some(200)),
        )
    };
    problems.finish()?;
    let (Some(email), Some(password)) = (email, password) else {
        return Err(ApiError::internal("validated fields missing"));
    };
    let email = tokens::normalise_email(&email);
    if user_by_email(&state, &email)?.is_some() {
        return Err(ApiError::conflict(
            "An account already exists for that address.",
        ));
    }
    let password_hash = hash(password).await?;
    let now = Timestamp::now();
    let user = User {
        id: radical_store::new_id(),
        email,
        password_hash,
        display_name: display_name
            .map(|n| n.trim().to_owned())
            .filter(|n| !n.is_empty()),
        is_first_account: false,
        created_at: Some(now),
        last_login_at: Some(now),
    };
    let (user, adopted) = state.store.write(|tx| {
        let first: i64 = tx.query_row("SELECT COUNT(*) FROM users", [], |r| r.get(0))?;
        let user = User {
            is_first_account: first == 0,
            ..user
        };
        rows::insert(tx, &user)?;
        let mut adopted = 0;
        if user.is_first_account {
            for sql in [
                "UPDATE projects SET owner_id = ?1 WHERE owner_id IS NULL",
                "UPDATE analyses SET owner_id = ?1 WHERE owner_id IS NULL",
                "UPDATE calibration_curves SET created_by = ?1 WHERE created_by IS NULL",
            ] {
                adopted += tx.execute(sql, [&user.id])?;
            }
        }
        Ok((user, adopted))
    })?;
    let mut data = signed_in(&accounts, &user);
    // On a first install after moving from the desktop, the number that
    // says the existing work is still there and now belongs to this account.
    data["adopted_existing_records"] = json!(adopted);
    Ok(created(data))
}

/// Exchange an address and password for a token. One answer for "no such
/// account" and "wrong password", and the bcrypt cost paid either way, so the
/// two cannot be told apart by what comes back or how long it took.
async fn login(Extract(state): Extract<State>, body: Bytes) -> ApiResult {
    let accounts = accounts_of(&state)?.clone();
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let (email, password) = {
        let mut f = validate::body(&input, "LoginRequest", &mut problems);
        (f.email("email"), f.string("password", None, None))
    };
    problems.finish()?;
    let (Some(email), Some(password)) = (email, password) else {
        return Err(ApiError::internal("validated fields missing"));
    };
    let user = user_by_email(&state, &tokens::normalise_email(&email))?;
    let reference = match &user {
        Some(u) => u.password_hash.clone(),
        None => hash("a dummy password".to_owned()).await?,
    };
    let matches = verify(password, reference).await;
    let Some(user) = user.filter(|_| matches) else {
        return Err(ApiError::new(
            StatusCode::UNAUTHORIZED,
            "That email or password is not right.",
        ));
    };
    let user = User {
        last_login_at: Some(Timestamp::now()),
        ..user
    };
    state
        .store
        .write(|tx| rows::update(tx, &user).map(|_| ()))?;
    Ok(ok(signed_in(&accounts, &user)))
}

/// Who the caller is.
async fn me(Extract(state): Extract<State>, caller: Caller) -> ApiResult {
    let Some(id) = caller.owner() else {
        return Ok(ok(local_user()));
    };
    let user: Option<User> = state.store.read(|c| rows::get(c, id))?;
    let user =
        user.ok_or_else(|| ApiError::new(StatusCode::UNAUTHORIZED, "Sign in to use this."))?;
    Ok(ok(accounts::user_json(&user)))
}

/// Change your own password, proving you know the current one. 403 rather
/// than 401 for a wrong one: the browser signs out on any 401, which would
/// end the session of someone who only mistyped.
async fn change_password(
    Extract(state): Extract<State>,
    caller: Caller,
    body: Bytes,
) -> ApiResult<Response> {
    accounts_of(&state)?;
    let id = caller.browser()?.to_owned();
    let input = validate::json_body(&body)?;
    let Some(fields) = input.as_object() else {
        return Err(ApiError::invalid(vec![validate::problem(
            "dict_type",
            &[json!("body")],
            "Input should be a valid dictionary",
            input.clone(),
            None,
        )]));
    };
    let text = |k: &str| match fields.get(k) {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    };
    let (current, new) = (text("current_password"), text("new_password"));
    let user: User = state
        .store
        .read(|c| rows::get(c, &id))?
        .ok_or_else(|| ApiError::new(StatusCode::UNAUTHORIZED, "Sign in to use this."))?;
    if !verify(current, user.password_hash.clone()).await {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "That current password is not right.",
        ));
    }
    if new.chars().count() < 8 {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "A password needs at least 8 characters.",
        ));
    }
    let user = User {
        password_hash: hash(new).await?,
        ..user
    };
    state
        .store
        .write(|tx| rows::update(tx, &user).map(|_| ()))?;
    Ok(axum::Json(json!({"success": true})).into_response())
}

// --------------------------------------------------------------------------
// A forgotten password
// --------------------------------------------------------------------------

/// What the sign-in page can offer. On the desktop, that there is no signing
/// in at all.
async fn options(Extract(state): Extract<State>) -> axum::Json<Value> {
    if state.accounts.is_none() {
        return ok(json!({"password_reset_email": false, "single_user": true}));
    }
    ok(json!({"password_reset_email": mail::is_configured()}))
}

/// One email per address per this long, so the form cannot fill an inbox.
const RESET_EMAIL_INTERVAL: Duration = Duration::from_secs(300);
static LAST_RESET_EMAIL: LazyLock<Mutex<HashMap<String, Instant>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

fn reset_email_allowed(email: &str) -> bool {
    let now = Instant::now();
    let mut sent = LAST_RESET_EMAIL.lock().unwrap_or_else(|e| e.into_inner());
    sent.retain(|_, at| now.duration_since(*at) < RESET_EMAIL_INTERVAL);
    if sent.contains_key(email) {
        return false;
    }
    sent.insert(email.to_owned(), now);
    true
}

/// Email a one-hour reset link, if the address has an account; the same
/// answer whether or not it does.
async fn forgot_password(Extract(state): Extract<State>, body: Bytes) -> ApiResult {
    let accounts = accounts_of(&state)?.clone();
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let email = validate::body(&input, "ForgotPasswordRequest", &mut problems).email("email");
    problems.finish()?;
    let Some(config) = mail::settings() else {
        return Err(ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "This installation cannot send email. Ask whoever runs it to reset your password.",
        ));
    };
    let email = tokens::normalise_email(&email.unwrap_or_default());
    if let Some(user) = user_by_email(&state, &email)?
        && reset_email_allowed(&email)
    {
        let token = tokens::create_reset_token(&accounts.secret, &user.id, &user.password_hash);
        // After the answer, so it takes no longer when the account exists. A
        // failure can only be logged: the person was told an email is coming
        // if there is an account.
        tokio::task::spawn_blocking(move || {
            if let Err(e) = mail::send_reset_link(&config, &user.email, &token) {
                eprintln!("Could not send a password-reset email: {e}");
            }
        });
    }
    Ok(axum::Json(json!({"success": true})))
}

/// Set a new password with a reset link's token, and sign in.
async fn reset_password(Extract(state): Extract<State>, body: Bytes) -> ApiResult {
    let accounts = accounts_of(&state)?.clone();
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let (token, new_password) = {
        let mut f = validate::body(&input, "ResetPasswordRequest", &mut problems);
        (
            f.string("token", None, None),
            f.string("new_password", Some(8), Some(72)),
        )
    };
    problems.finish()?;
    let (Some(token), Some(new_password)) = (token, new_password) else {
        return Err(ApiError::internal("validated fields missing"));
    };
    let user_id = tokens::read_reset_token(&accounts.secret, &token, |id| {
        state
            .store
            .read(|c| rows::get::<User>(c, id))
            .ok()
            .flatten()
            .map(|u| u.password_hash)
    })
    .map_err(|_| {
        ApiError::bad_request(
            "This reset link has expired or has already been used. Ask for a new one.",
        )
    })?;
    let user: User = state
        .store
        .read(|c| rows::get(c, &user_id))?
        .ok_or_else(|| ApiError::internal("reset for no account"))?;
    let user = User {
        password_hash: hash(new_password).await?,
        last_login_at: Some(Timestamp::now()),
        ..user
    };
    state
        .store
        .write(|tx| rows::update(tx, &user).map(|_| ()))?;
    Ok(ok(signed_in(&accounts, &user)))
}

// --------------------------------------------------------------------------
// Personal access tokens
// --------------------------------------------------------------------------

/// Enough for every script a person runs; a cap so a loop cannot fill the table.
const MAX_ACCESS_TOKENS: i64 = 50;

/// A token as the list shows it: never the secret, which is not stored.
fn access_token_json(row: &PersonalAccessToken) -> Value {
    json!({
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "scope": row.scope,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "last_used_at": row.last_used_at.map(|t| t.isoformat()),
        "expired": row.expires_at <= Timestamp::now(),
    })
}

async fn list_access_tokens(Extract(state): Extract<State>, caller: Caller) -> ApiResult {
    accounts_of(&state)?;
    let id = caller.browser()?.to_owned();
    let rows: Vec<PersonalAccessToken> = state
        .store
        .read(|c| rows::select_where(c, "user_id = ?", [&id], "created_at DESC"))?;
    Ok(axum::Json(json!({
        "success": true,
        "data": rows.iter().map(access_token_json).collect::<Vec<_>>(),
        "lifetimes": tokens::ACCESS_TOKEN_LIFETIMES,
    })))
}

/// Make a personal access token. The secret is in this answer and nowhere
/// else: it is not stored, so it cannot be shown again.
async fn create_access_token(
    Extract(state): Extract<State>,
    caller: Caller,
    body: Bytes,
) -> ApiResult<Response> {
    accounts_of(&state)?;
    let id = caller.browser()?.to_owned();
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let (name, days, scope) = {
        let mut f = validate::body(&input, "AccessTokenRequest", &mut problems);
        (
            f.string("name", Some(1), Some(100)),
            f.int_or("days", tokens::ACCESS_TOKEN_DEFAULT_LIFETIME, Bounds::NONE),
            f.literal_or("scope", &["read", "full"], "read"),
        )
    };
    problems.finish()?;
    let (Some(name), Some(days), Some(scope)) = (name, days, scope) else {
        return Err(ApiError::internal("validated fields missing"));
    };
    if !tokens::ACCESS_TOKEN_LIFETIMES.contains(&days) {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "A token lasts 7, 30, 90 or 365 days.",
        ));
    }
    let count: i64 = state.store.read(|c| {
        Ok(c.query_row(
            "SELECT COUNT(*) FROM personal_access_tokens WHERE user_id = ?",
            [&id],
            |r| r.get(0),
        )?)
    })?;
    if count >= MAX_ACCESS_TOKENS {
        return Err(ApiError::conflict(format!(
            "You have {MAX_ACCESS_TOKENS} access tokens. Revoke one you no longer use first."
        )));
    }
    let secret = tokens::new_access_token();
    let now = Timestamp::now();
    let row = PersonalAccessToken {
        id: radical_store::new_id(),
        user_id: id,
        name: name.trim().to_owned(),
        token_hash: tokens::access_token_hash(&secret),
        prefix: tokens::access_token_prefix(&secret),
        scope,
        created_at: now,
        expires_at: Timestamp {
            micros: now.micros + days * 86_400_000_000,
        },
        last_used_at: None,
    };
    state.store.write(|tx| rows::insert(tx, &row))?;
    let mut data = access_token_json(&row);
    data["token"] = json!(secret);
    Ok(created(data))
}

/// Revoke a token: it stops working at once, and its row goes with it.
async fn revoke_access_token(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(token_id): Path<String>,
) -> ApiResult {
    accounts_of(&state)?;
    let id = caller.browser()?.to_owned();
    let row: Option<PersonalAccessToken> = state.store.read(|c| rows::get(c, &token_id))?;
    if row.is_none_or(|r| r.user_id != id) {
        return Err(ApiError::not_found("That token was not found."));
    }
    state.store.write(|tx| {
        rows::delete_where::<PersonalAccessToken>(tx, "id = ?", [&token_id]).map(|_| ())
    })?;
    Ok(axum::Json(json!({"success": true})))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/auth/options", get(options))
        .route("/api/auth/me", get(me))
        .route("/api/auth/register", post(register))
        .route("/api/auth/login", post(login))
        .route("/api/auth/password", post(change_password))
        .route("/api/auth/forgot-password", post(forgot_password))
        .route("/api/auth/reset-password", post(reset_password))
        .route(
            "/api/auth/tokens",
            get(list_access_tokens).post(create_access_token),
        )
        .route("/api/auth/tokens/{token_id}", delete(revoke_access_token))
}
