//! The menu bar. RadiCal's own items are handed to the frontend
//! (`frontend/src/desktop/DesktopBridge.tsx`), which opens the page or dialog
//! it already has for each; the rest are the system's standard items. The
//! Edit menu matters more than it looks: on macOS, copy and paste in a text
//! field are menu commands, and without them neither works.

use tauri::menu::{Menu, MenuItemBuilder, SubmenuBuilder};
use tauri::{AppHandle, Emitter, Runtime};

/// What the frontend is told when one of RadiCal's own items is chosen.
pub const EVENT: &str = "radical://menu";

/// The ids the frontend answers to.
const OWN: &[&str] = &["new-analysis", "import-dates", "import-project", "help"];

pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let item = |id: &str, text: &str, keys: Option<&str>| {
        let builder = MenuItemBuilder::with_id(id, text);
        match keys {
            Some(k) => builder.accelerator(k).build(app),
            None => builder.build(app),
        }
    };
    let file = SubmenuBuilder::new(app, "File")
        .item(&item("new-analysis", "New Analysis", Some("CmdOrCtrl+N"))?)
        .separator()
        .item(&item("import-dates", "Import Dates…", Some("CmdOrCtrl+O"))?)
        .item(&item(
            "import-project",
            "Import Project…",
            Some("CmdOrCtrl+Shift+O"),
        )?)
        .separator()
        .close_window()
        .build()?;
    let edit = SubmenuBuilder::new(app, "Edit")
        .undo()
        .redo()
        .separator()
        .cut()
        .copy()
        .paste()
        .select_all()
        .build()?;
    let view = SubmenuBuilder::new(app, "View").fullscreen().build()?;
    let window = SubmenuBuilder::new(app, "Window")
        .minimize()
        .maximize()
        .build()?;
    let help = SubmenuBuilder::new(app, "Help")
        .item(&item("help", "RadiCal Help", None)?)
        .build()?;
    let menu = Menu::new(app)?;
    // macOS puts the first submenu under the app's name, whatever it is
    // called; elsewhere there is no such menu, and Quit lives under File.
    #[cfg(target_os = "macos")]
    menu.append(
        &SubmenuBuilder::new(app, "RadiCal")
            .about(None)
            .separator()
            .services()
            .separator()
            .hide()
            .hide_others()
            .show_all()
            .separator()
            .quit()
            .build()?,
    )?;
    menu.append_items(&[&file, &edit, &view, &window, &help])?;
    #[cfg(not(target_os = "macos"))]
    file.append_items(&[
        &tauri::menu::PredefinedMenuItem::separator(app)?,
        &tauri::menu::PredefinedMenuItem::quit(app, None)?,
    ])?;
    Ok(menu)
}

/// Hand one of RadiCal's own items to the frontend.
pub fn chosen<R: Runtime>(app: &AppHandle<R>, id: &str) {
    if OWN.contains(&id) {
        let _ = app.emit_to("main", EVENT, id);
    }
}
