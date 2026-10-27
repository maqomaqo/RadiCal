//! RadiCal Desktop: the API and the frontend the Docker image serves, in one
//! program and one window.
//!
//! On launch this opens the database in the OS app-data folder, starts
//! radical-server on loopback with a token made for this launch, and opens a
//! window on it. The frontend is the same build the Docker image serves; it
//! talks HTTP to the server exactly as it does there, and the window hands it
//! the token before any of its code runs.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{Ipv4Addr, SocketAddr, TcpListener};
use std::path::PathBuf;

use radical_server::launch::{self, Launch};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

mod files;
mod menu;
mod opened;
mod updates;

/// The port the server asks for first. The frontend keeps its preferences
/// (date format, columns, basemap) in localStorage, which belongs to an
/// origin, port included: a new port each launch would forget them all. A
/// random one is taken only when this one is busy.
const PREFERRED_PORT: u16 = 47318;

fn listen() -> std::io::Result<TcpListener> {
    TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, PREFERRED_PORT)))
        .or_else(|_| TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, 0))))
}

/// The built frontend: bundled beside the app as a resource, or, running
/// from the source tree, `frontend/dist` (`npm run build`).
fn frontend_dir(app: &tauri::App) -> Option<PathBuf> {
    let bundled = app.path().resource_dir().ok().map(|d| d.join("frontend"));
    let source = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../frontend/dist");
    std::env::var_os("RADICAL_FRONTEND")
        .map(PathBuf::from)
        .into_iter()
        .chain(bundled)
        .chain([source])
        .find(|d| d.join("index.html").is_file())
}

/// The database: in the OS app-data folder, or wherever `RADICAL_DB` says
/// (a scratch copy, for trying something out).
fn database() -> Result<PathBuf, Box<dyn std::error::Error>> {
    if let Some(path) = std::env::var_os("RADICAL_DB") {
        return Ok(PathBuf::from(path));
    }
    let folder = radical_store::default_dir().ok_or("no home folder to keep the database in")?;
    std::fs::create_dir_all(&folder)?;
    Ok(folder.join(radical_store::FILE_NAME))
}

/// Start the server on its own runtime, answering at the returned address.
fn start_server(
    frontend: PathBuf,
    token: String,
) -> Result<SocketAddr, Box<dyn std::error::Error>> {
    let state = radical_server::State::open(database()?, radical_jobs::default_workers())?;
    let listener = listen()?;
    listener.set_nonblocking(true)?;
    let address = listener.local_addr()?;
    let runtime = tokio::runtime::Runtime::new()?;
    std::thread::Builder::new()
        .name("radical-server".into())
        .spawn(move || {
            runtime.block_on(async move {
                let listener =
                    tokio::net::TcpListener::from_std(listener).expect("the listener is usable");
                let launch = Launch {
                    token: Some(token),
                    frontend: Some(frontend),
                };
                if let Err(e) = launch::serve(listener, state, launch).await {
                    eprintln!("the server stopped: {e}");
                }
            });
        })?;
    Ok(address)
}

fn main() {
    let app = tauri::Builder::default()
        // First, so a second launch hands over to the first before it opens
        // a database the first already has open. What it was asked to open
        // comes along: on Windows and Linux, a double-clicked file is a
        // command-line argument.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
            opened::open(app, args.iter().skip(1));
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(opened::Queue::default())
        .invoke_handler(tauri::generate_handler![
            files::save_file,
            opened::take_opened_files
        ])
        .menu(menu::build)
        .on_menu_event(|app, event| menu::chosen(app, event.id().as_ref()))
        .setup(|app| {
            let frontend = frontend_dir(app).ok_or(
                "the frontend is not built: run `npm run build` in frontend/, \
                 or set RADICAL_FRONTEND to its dist folder",
            )?;
            let token = launch::new_token();
            let address = start_server(frontend, token.clone())?;
            let origin = format!("http://{address}");
            // The token goes to this origin's pages only: the script runs on
            // every page the webview loads, so it checks where it is first.
            let script = format!(
                "if (window.location.origin === {origin:?}) {{ window.__RADICAL_LAUNCH_TOKEN__ = {token}; }}",
                token = serde_json::to_string(&token)?,
            );
            let home: tauri::Url = format!("{origin}/").parse()?;
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(home))
                .title("RadiCal")
                .inner_size(1280.0, 840.0)
                .min_inner_size(900.0, 600.0)
                .initialization_script(&script)
                // The import dialogs take a dropped file themselves; left on,
                // the window would swallow the drop before the page saw it.
                .disable_drag_drop_handler()
                // The window shows RadiCal and nothing else.
                .on_navigation(move |url| url.origin().ascii_serialization() == origin);
            // On macOS the app's own bar is the title bar: the page runs to
            // the window's top edge and the window's buttons sit inside the
            // bar, centred on its 64px height. The frontend leaves room for
            // them and makes the bar draggable (MAC_TITLE_BAR in
            // frontend/src/desktop/shell.ts).
            #[cfg(target_os = "macos")]
            let window = window
                .title_bar_style(tauri::TitleBarStyle::Overlay)
                .hidden_title(true)
                .traffic_light_position(tauri::LogicalPosition::new(20.0, 33.0));
            window.build()?;
            opened::open(app.handle(), std::env::args_os().skip(1));
            // A build from the source tree has no release to compare with.
            if !cfg!(debug_assertions) {
                tauri::async_runtime::spawn(updates::check(app.handle().clone(), false));
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("RadiCal Desktop failed to start");
    app.run(|_app, _event| {
        // macOS hands a double-clicked file to the running app as an event,
        // not an argument.
        #[cfg(target_os = "macos")]
        if let tauri::RunEvent::Opened { urls } = _event {
            opened::open(_app, urls.iter().filter_map(|u| u.to_file_path().ok()));
        }
    });
}
