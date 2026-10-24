//! Files going out of the app. The webview cannot be trusted to download a
//! blob URL (WebKit on macOS ignores one), so the frontend hands the bytes
//! here instead (`frontend/src/utils/saveFile.ts`), and the app asks where
//! to put them with the system's own save dialog.

use std::path::PathBuf;

use percent_encoding::percent_decode_str;
use tauri::ipc::{InvokeBody, Request};
use tauri_plugin_dialog::DialogExt;

/// Ask where to save the request's bytes, under the name the frontend
/// suggests (`X-Filename`, percent-encoded), and write them there. Answers
/// the path written, or `None` when the person cancels.
#[tauri::command]
pub async fn save_file(
    app: tauri::AppHandle,
    request: Request<'_>,
) -> Result<Option<String>, String> {
    let InvokeBody::Raw(bytes) = request.body() else {
        return Err("save_file takes the file's bytes as its body".into());
    };
    let name = request
        .headers()
        .get("x-filename")
        .and_then(|v| v.to_str().ok())
        .map(|v| percent_decode_str(v).decode_utf8_lossy().into_owned())
        .unwrap_or_else(|| "download".to_owned());
    // Blocking is safe here: an async command runs off the main thread,
    // which is the one the dialog needs.
    let Some(chosen) = app
        .dialog()
        .file()
        .set_file_name(file_name_only(&name))
        .blocking_save_file()
    else {
        return Ok(None);
    };
    let path: PathBuf = chosen.into_path().map_err(|e| e.to_string())?;
    std::fs::write(&path, bytes).map_err(|e| format!("could not write {}: {e}", path.display()))?;
    Ok(Some(path.display().to_string()))
}

/// The last component of a suggested name, so a name cannot steer the
/// dialog to another folder.
fn file_name_only(name: &str) -> String {
    name.rsplit(['/', '\\'])
        .next()
        .filter(|n| !n.is_empty() && *n != "." && *n != "..")
        .unwrap_or("download")
        .to_owned()
}

#[cfg(test)]
mod tests {
    use super::file_name_only;

    #[test]
    fn a_suggested_name_is_only_ever_a_file_name() {
        assert_eq!(file_name_only("project-site.zip"), "project-site.zip");
        assert_eq!(file_name_only("../../etc/passwd"), "passwd");
        assert_eq!(file_name_only(r"C:\Users\x\report.pdf"), "report.pdf");
        assert_eq!(file_name_only(".."), "download");
        assert_eq!(file_name_only("a/"), "download");
    }
}
