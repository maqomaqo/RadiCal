//! Files the app is asked to open: a double-clicked `.oxcal`, a file dropped
//! on the app's icon, or a path on the command line. They wait here until
//! the frontend takes them (`take_opened_files`), which it does as it
//! starts and whenever it is told more have come. Waiting matters for the
//! file that launched the app, which arrives before any page could listen.

use std::path::Path;
use std::sync::Mutex;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, Runtime};

/// What the frontend is told when a file has come.
pub const EVENT: &str = "radical://opened";

/// The kinds of file the importer reads.
const READABLE: &[&str] = &["oxcal", "csv", "tsv", "txt"];

/// Larger than any dates file; a guard against opening something else.
const MAX_BYTES: u64 = 20 * 1024 * 1024;

#[derive(Serialize, Clone)]
pub struct Opened {
    name: String,
    bytes: Vec<u8>,
}

#[derive(Default)]
pub struct Queue(Mutex<Vec<Opened>>);

/// Queue each readable file among `paths` and tell the frontend. Anything
/// else -- a flag, a folder, a file of another kind -- is passed over.
pub fn open<R: Runtime>(app: &AppHandle<R>, paths: impl IntoIterator<Item = impl AsRef<Path>>) {
    let read: Vec<Opened> = paths
        .into_iter()
        .filter_map(|p| {
            let path = p.as_ref();
            let extension = path.extension()?.to_str()?.to_ascii_lowercase();
            if !READABLE.contains(&extension.as_str())
                || std::fs::metadata(path).ok()?.len() > MAX_BYTES
            {
                return None;
            }
            Some(Opened {
                name: path.file_name()?.to_string_lossy().into_owned(),
                bytes: std::fs::read(path).ok()?,
            })
        })
        .collect();
    if read.is_empty() {
        return;
    }
    if let Some(queue) = app.try_state::<Queue>() {
        queue
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .extend(read);
    }
    let _ = app.emit_to("main", EVENT, ());
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// The files waiting, which are then no longer waiting.
#[tauri::command]
pub fn take_opened_files(queue: tauri::State<'_, Queue>) -> Vec<Opened> {
    std::mem::take(&mut *queue.0.lock().unwrap_or_else(|e| e.into_inner()))
}
