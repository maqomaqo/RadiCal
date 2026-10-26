//! The API's own documentation, as the backend serves it: the OpenAPI
//! document at `/api/openapi.json`, Swagger UI at `/api/docs` and ReDoc at
//! `/api/redoc`, with the old root paths redirecting there. Scripts that use
//! personal access tokens are written against these.
//!
//! The document is the one FastAPI generated, as recorded in the corpus; a
//! test holds the two the same, so it describes exactly the routes the
//! replay checks this server against.

use axum::Router;
use axum::http::header;
use axum::response::{Html, IntoResponse, Redirect};
use axum::routing::get;

use crate::State;

pub const OPENAPI: &str = include_str!("../../api/openapi.json");

/// FastAPI's Swagger page, less its OAuth2 redirect: sign-in here is a
/// bearer token, pasted under Authorize.
const SWAGGER: &str = r#"<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link type="text/css" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
<title>RadiCal API - Swagger UI</title>
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
const ui = SwaggerUIBundle({
  url: '/api/openapi.json',
  dom_id: '#swagger-ui',
  layout: 'BaseLayout',
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
})
</script>
</body>
</html>
"#;

const REDOC: &str = r#"<!DOCTYPE html>
<html>
<head>
<title>RadiCal API - ReDoc</title>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
<style>body { margin: 0; padding: 0; }</style>
</head>
<body>
<noscript>ReDoc requires Javascript to function. Please enable it to browse the documentation.</noscript>
<redoc spec-url="/api/openapi.json"></redoc>
<script src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"></script>
</body>
</html>
"#;

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/api/openapi.json",
            get(|| async {
                ([(header::CONTENT_TYPE, "application/json")], OPENAPI).into_response()
            }),
        )
        .route("/api/docs", get(|| async { Html(SWAGGER) }))
        .route("/api/redoc", get(|| async { Html(REDOC) }))
        .route("/docs", get(|| async { Redirect::permanent("/api/docs") }))
        .route(
            "/redoc",
            get(|| async { Redirect::permanent("/api/redoc") }),
        )
        .route(
            "/openapi.json",
            get(|| async { Redirect::permanent("/api/openapi.json") }),
        )
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_document_is_the_one_recorded() {
        let family = radical_corpus::load_family("api/exchanges").unwrap();
        let recorded = family
            .cases
            .iter()
            .find(|c| c.inputs["url"] == "/api/openapi.json")
            .and_then(|c| c.expanded_outputs())
            .expect("the recording has the OpenAPI document");
        let served: serde_json::Value = serde_json::from_str(super::OPENAPI).unwrap();
        assert_eq!(served, recorded["response_json"]);
    }
}
