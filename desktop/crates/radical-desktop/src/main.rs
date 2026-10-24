//! RadiCal Desktop: the API and the frontend the Docker image serves, in one
//! program and one window.
//!
//! On launch this opens the database in the OS app-data folder, starts
//! radical-server on loopback with a token made for this launch, and opens a
//! window on it. The frontend is the same build the Docker image serves; it
//! talks HTTP to the server exactly as it does there, and the window hands it
//! the token before any of its code runs (docs/RUST_DESKTOP_PLAN.md §2.2).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{Ipv4Addr, SocketAddr, TcpListener};
use std::path::PathBuf;

use radical_server::launch::{self, Launch};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

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
    tauri::Builder::default()
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
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(home))
                .title("RadiCal")
                .inner_size(1280.0, 840.0)
                .min_inner_size(900.0, 600.0)
                .initialization_script(&script)
                // The window shows RadiCal and nothing else.
                .on_navigation(move |url| url.origin().ascii_serialization() == origin)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("RadiCal Desktop failed to start");
}
