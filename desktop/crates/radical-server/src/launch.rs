//! What the desktop app adds around the API: a token that only its own
//! window holds, and the frontend itself, served from the same origin so
//! the frontend's bare `fetch('/api/...')` calls reach the API unchanged.
//!
//! The server listens on loopback only, but any process on the machine can
//! reach loopback. The token is what keeps them out: made fresh each launch,
//! handed to the window and nowhere else, and asked for on every `/api/`
//! request. It travels as a bearer token, the header the frontend's fetch
//! wrapper already sends.

use std::path::{Component, Path, PathBuf};

use axum::extract::{Request, State as Extract};
use axum::http::{Method, StatusCode, header};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};

use crate::{ApiError, State};

/// How this server is being run.
#[derive(Clone, Default)]
pub struct Launch {
    /// The bearer token every `/api/` request must carry, or `None` to ask
    /// for none (development, and the replay).
    pub token: Option<String>,
    /// The built frontend (`frontend/dist`), served at `/` with every path
    /// that is not a file falling back to `index.html`, as the frontend's
    /// router expects.
    pub frontend: Option<PathBuf>,
}

/// A token for one launch: 244 random bits, as two version-4 UUIDs.
pub fn new_token() -> String {
    format!("{}{}", radical_store::new_id(), radical_store::new_id()).replace('-', "")
}

/// Equal without saying, by how long it took, how much of a guess was right.
fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

/// Refuse an `/api/` request without this launch's token.
pub(crate) async fn require_token(
    Extract(token): Extract<String>,
    request: Request,
    next: Next,
) -> Response {
    if !request.uri().path().starts_with("/api/") {
        return next.run(request).await;
    }
    let given = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "));
    match given {
        Some(given) if same(given.as_bytes(), token.as_bytes()) => next.run(request).await,
        _ => ApiError::new(StatusCode::UNAUTHORIZED, "Not authenticated").into_response(),
    }
}

/// The file under `root` a request path names, refusing any path that would
/// climb out of it.
fn file_for(root: &Path, path: &str) -> Option<PathBuf> {
    let relative = Path::new(path.trim_start_matches('/'));
    if !relative
        .components()
        .all(|c| matches!(c, Component::Normal(_)))
    {
        return None;
    }
    let file = root.join(relative);
    file.is_file().then_some(file)
}

fn content_type(file: &Path) -> &'static str {
    match file.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" | "map" => "application/json",
        "geojson" => "application/geo+json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "ico" => "image/x-icon",
        "woff2" => "font/woff2",
        "woff" => "font/woff",
        "wasm" => "application/wasm",
        "txt" => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

/// A frontend file, or `index.html` for a route the frontend draws itself.
pub(crate) async fn frontend(root: PathBuf, method: Method, uri: axum::http::Uri) -> Response {
    let path = uri.path();
    if path.starts_with("/api/") || path == "/api" || !matches!(method, Method::GET | Method::HEAD)
    {
        return ApiError::not_found("Not Found").into_response();
    }
    let (file, shell) = match file_for(&root, path) {
        Some(file) => (file, false),
        None => match file_for(&root, "index.html") {
            Some(file) => (file, true),
            None => return ApiError::not_found("Not Found").into_response(),
        },
    };
    match tokio::fs::read(&file).await {
        // As the backend serves the published image's frontend: the hashed
        // bundles never change under a name, so they are cached for good;
        // public/ files keep their names across releases, so a day; and the
        // shell, which names the current bundles, is asked for afresh.
        Ok(bytes) => {
            let cache = if !shell && file.parent() == Some(&root.join("assets")) {
                "public, max-age=31536000, immutable"
            } else if !shell {
                "public, max-age=86400"
            } else {
                "no-cache"
            };
            (
                [
                    (header::CONTENT_TYPE, content_type(&file)),
                    (header::CACHE_CONTROL, cache),
                ],
                bytes,
            )
                .into_response()
        }
        Err(e) => ApiError::internal(e).into_response(),
    }
}

/// The API as the desktop app runs it.
pub fn app(state: State, launch: Launch) -> axum::Router {
    let mut router = crate::routes::router();
    router = match launch.frontend {
        Some(root) => router.fallback(move |method: Method, uri: axum::http::Uri| {
            frontend(root.clone(), method, uri)
        }),
        // No frontend: "/" is the API's own front page, as the backend's is
        // in its development stack.
        None => router
            .route(
                "/",
                axum::routing::get(|| async {
                    axum::Json(serde_json::json!({
                        "message": "RadiCal API", "docs": "/api/docs", "health": "/api/health",
                    }))
                }),
            )
            .fallback(|| async { ApiError::new(StatusCode::NOT_FOUND, "Not Found") }),
    };
    let router = router
        .route_layer(axum::middleware::from_fn_with_state(
            state.clone(),
            crate::accounts::require_caller,
        ))
        .with_state(state);
    match launch.token {
        Some(token) => router.layer(axum::middleware::from_fn_with_state(token, require_token)),
        None => router,
    }
}

/// Serve on `listener` until the process ends.
pub async fn serve(
    listener: tokio::net::TcpListener,
    state: State,
    launch: Launch,
) -> std::io::Result<()> {
    axum::serve(listener, app(state, launch)).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_path_cannot_climb_out_of_the_frontend() {
        let root = std::env::temp_dir().join(format!("radical-front-{}", radical_store::new_id()));
        std::fs::create_dir_all(root.join("assets")).unwrap();
        std::fs::write(root.join("index.html"), "<html>").unwrap();
        std::fs::write(root.join("assets/app.js"), "1").unwrap();
        assert_eq!(
            file_for(&root, "/assets/app.js"),
            Some(root.join("assets/app.js"))
        );
        assert_eq!(file_for(&root, "/../secret"), None);
        assert_eq!(file_for(&root, "/assets/../../secret"), None);
        assert_eq!(file_for(&root, "/projects/abc"), None);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn tokens_are_compared_whole() {
        let t = new_token();
        assert_eq!(t.len(), 64);
        assert!(same(t.as_bytes(), t.clone().as_bytes()));
        assert!(!same(t.as_bytes(), &t.as_bytes()[..63]));
        assert!(!same(b"abc", b"abd"));
    }
}
