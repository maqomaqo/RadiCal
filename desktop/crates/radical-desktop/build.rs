fn main() {
    // The window's page comes from radical-server, which Tauri treats as a
    // remote origin: it may call the app's own commands only when they are
    // declared here and granted in capabilities/default.json.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["save_file", "take_opened_files"]),
    ))
    .expect("the Tauri build step runs");
}
