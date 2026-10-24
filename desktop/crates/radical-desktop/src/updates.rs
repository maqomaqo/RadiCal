//! Updates from GitHub Releases. The release workflow publishes, beside the
//! installers, a `latest.json` naming the newest version and a signature for
//! each platform's update, made with the project's private key; the app
//! holds the matching public key (`tauri.conf.json`) and installs nothing it
//! cannot verify against it.
//!
//! The app looks once at launch, quietly, and again whenever Help > Check for
//! Updates is chosen, when it also says if there is nothing new. It never
//! installs without asking.

use tauri::{AppHandle, Runtime};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

/// Whether this copy can update itself. On Linux only the AppImage can: a
/// .deb is installed, and so replaced, by the system's installer.
fn updatable() -> bool {
    !cfg!(target_os = "linux") || std::env::var_os("APPIMAGE").is_some()
}

fn tell<R: Runtime>(app: &AppHandle<R>, title: &str, text: String) {
    app.dialog()
        .message(text)
        .title(title)
        .kind(MessageDialogKind::Info)
        .blocking_show();
}

/// Look for a newer release, and offer to install it. `asked` is whether the
/// person chose to check, when "nothing new" and failures are said too; at
/// launch they are not, since nobody asked and nothing needs doing.
pub async fn check<R: Runtime>(app: AppHandle<R>, asked: bool) {
    if !updatable() {
        if asked {
            tell(
                &app,
                "Check for Updates",
                "This copy of RadiCal was installed from a .deb package. To update it, install \
                 the newest .deb from RadiCal's releases page."
                    .into(),
            );
        }
        return;
    }
    let found = match app.updater() {
        Ok(updater) => updater.check().await,
        Err(e) => Err(e),
    };
    let update = match found {
        Ok(Some(update)) => update,
        Ok(None) => {
            if asked {
                tell(
                    &app,
                    "Check for Updates",
                    format!(
                        "RadiCal {} is the latest version.",
                        app.package_info().version
                    ),
                );
            }
            return;
        }
        Err(e) => {
            if asked {
                tell(
                    &app,
                    "Check for Updates",
                    format!("Could not check for updates: {e}"),
                );
            }
            return;
        }
    };
    let install = app
        .dialog()
        .message(format!(
            "RadiCal {} is available; this is {}. Install it now? RadiCal restarts \
             to finish, and your work is kept.",
            update.version, update.current_version
        ))
        .title("Update Available")
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install and Restart".into(),
            "Later".into(),
        ))
        .blocking_show();
    if !install {
        return;
    }
    match update.download_and_install(|_, _| {}, || {}).await {
        Ok(()) => app.restart(),
        Err(e) => tell(
            &app,
            "Update Failed",
            format!("The update could not be installed: {e}"),
        ),
    }
}
