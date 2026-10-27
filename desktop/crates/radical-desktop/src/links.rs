//! Links that lead out of the app. The window shows RadiCal and nothing
//! else, so a link to anywhere else -- a reference in the manual, a map's
//! attribution -- opens in the system's browser instead, whether it would
//! have replaced the page or opened a new window.

use tauri::webview::{NewWindowFeatures, NewWindowResponse};
use tauri::{AppHandle, Manager, Runtime, Url};

/// Whether the window may go to `url`: only RadiCal's own pages. A link to
/// anywhere else goes to the system's browser.
pub fn navigate(origin: &str, url: &Url) -> bool {
    if is_own(origin, url) {
        return true;
    }
    open_outside(url);
    false
}

/// A link that asked for a new window. RadiCal's own pages open in its one
/// window; anything else in the system's browser.
pub fn new_window<R: Runtime>(
    app: &AppHandle<R>,
    origin: &str,
    url: &Url,
    _features: NewWindowFeatures,
) -> NewWindowResponse<R> {
    if is_own(origin, url) {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.navigate(url.clone());
        }
    } else {
        open_outside(url);
    }
    NewWindowResponse::Deny
}

fn is_own(origin: &str, url: &Url) -> bool {
    url.origin().ascii_serialization() == origin
}

/// Web and mail links only: anything else a page might name (a file, a
/// custom scheme) is not the system's to open on the page's say-so.
fn open_outside(url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        let _ = open::that_detached(url.as_str());
    }
}

#[cfg(test)]
mod tests {
    use super::is_own;

    #[test]
    fn only_the_apps_own_origin_is_its_own() {
        let origin = "http://127.0.0.1:47318";
        let own = |u: &str| is_own(origin, &u.parse().unwrap());
        assert!(own("http://127.0.0.1:47318/help#same-event-test"));
        assert!(!own("http://127.0.0.1:47319/"));
        assert!(!own("https://openfreemap.org/"));
    }
}
