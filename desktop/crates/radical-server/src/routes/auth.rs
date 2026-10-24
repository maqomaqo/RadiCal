//! What is left of `api/auth.py` for one person on their own machine: the
//! sign-in page's question, answered with `single_user` so the frontend goes
//! straight in, and who "you" are.

use axum::Router;
use axum::routing::get;
use serde_json::json;

use crate::{State, ok};

/// The one local user, as `_user_json` reports a user.
pub fn local_user() -> serde_json::Value {
    json!({
        "id": "local",
        "email": "",
        "display_name": null,
        "is_first_account": true,
        "created_at": null,
    })
}

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/api/auth/options",
            get(|| async { ok(json!({"password_reset_email": false, "single_user": true})) }),
        )
        .route("/api/auth/me", get(|| async { ok(local_user()) }))
}
