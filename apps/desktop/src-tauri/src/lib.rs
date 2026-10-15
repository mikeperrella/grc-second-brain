// The host of the GRC Second Brain desktop app: it starts the Python sidecar, keeps it confined in
// a Job Object, and answers the window's one question (`sidecar_health`). Windows only
// (sidecar/mod.rs refuses to compile elsewhere).
//
// The modules are `pub` because integration tests in tests/ use them; within them, only the items
// those tests need are `pub` (each says why), the rest are `pub(crate)`.
pub mod app_commands;
pub mod child_env;
mod commands;
mod dev_paths;
pub mod folders;
pub mod health;
pub mod host;
pub mod proxy;
pub mod ready;
pub mod sidecar;
pub mod sidecar_client;
pub mod token;

use std::fs;

use tauri::{Manager, RunEvent, WindowEvent};

use crate::dev_paths::vault_dir_for_this_build;
use crate::folders::FolderSettings;
use crate::host::{HostState, SidecarDirs, SidecarHost};
use crate::sidecar::supervisor::FailureKind;

/// The saved-folders file inside the app config directory (ADR-020 decision 5).
const SAVED_FOLDERS_FILE: &str = "folders.json";

/// Build and run the app. Returns when the last window is closed.
///
/// Sidecar lifecycle: `setup` starts it on a background thread, so the window never waits for it;
/// closing the window and the app's exit both call `HostState::shutdown`. Behind those two sit
/// `Drop` of the supervisor and the Job Object's kill-on-close, so the sidecar dies with the host
/// even if the host is killed (ADR-004).
pub fn run() -> Result<(), tauri::Error> {
    let app = tauri::Builder::default()
        // The native folder dialog, called from Rust only (commands.rs). The window is granted no
        // dialog permission (tests/security_config.rs), and the fs plugin that it depends on is
        // never registered.
        .plugin(tauri_plugin_dialog::init())
        .manage(HostState::default())
        .invoke_handler(tauri::generate_handler![
            commands::sidecar_health,
            commands::sidecar_call,
            commands::folders_get,
            commands::folder_pick,
            commands::folder_list_files,
        ])
        .setup(|app| {
            // The index folder is the host's own choice; the saved-folders file lives in the app
            // config directory. Neither is ever a path from the window.
            let index_dir = app
                .path()
                .app_local_data_dir()
                .ok()
                .map(|dir| dir.join("index"));
            let config_file = app
                .path()
                .app_config_dir()
                .ok()
                .map(|dir| dir.join(SAVED_FOLDERS_FILE));
            app.manage(FolderSettings::new(config_file, index_dir));
            start_sidecar_in_background(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::Destroyed = event {
                window.app_handle().state::<HostState>().shutdown();
            }
        })
        .build(tauri::generate_context!())?;

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            handle.state::<HostState>().shutdown();
        }
    });
    Ok(())
}

/// Start the sidecar on its own thread and record the outcome in the managed state. Every failure,
/// including "no log directory" and "could not start a thread", becomes a state the health command
/// reports; none of them stops the window from opening.
fn start_sidecar_in_background(handle: &tauri::AppHandle) {
    let state_handle = handle.clone();
    let log_dir = match handle.path().app_log_dir() {
        Ok(dir) => dir,
        Err(_) => {
            handle
                .state::<HostState>()
                .finish_start(Err(FailureKind::LogDirUnavailable));
            return;
        }
    };
    // The index lives under the app's local data directory, chosen here by the host. If there is
    // no such directory, or it cannot be created, the start fails with its own kind (G8).
    let index_dir = match handle.path().app_local_data_dir() {
        Ok(dir) => dir.join("index"),
        Err(_) => {
            handle
                .state::<HostState>()
                .finish_start(Err(FailureKind::IndexDirUnavailable));
            return;
        }
    };
    let spawned = std::thread::Builder::new()
        .name("sidecar-start".to_owned())
        .spawn(move || {
            // The index folder must exist before the folders are checked against it. Canonicalizing
            // the user's folders can touch slow drives, which is why this runs here and not in
            // `setup`, where it would delay the window.
            if fs::create_dir_all(&index_dir).is_err() {
                state_handle
                    .state::<HostState>()
                    .finish_start(Err(FailureKind::IndexDirUnavailable));
                return;
            }
            // The saved folders, validated afresh at every start. A saved vault wins; the debug-only
            // host variable is used only when no vault is saved and never in a release build.
            let resolved = state_handle
                .state::<FolderSettings>()
                .startup(vault_dir_for_this_build());
            let dirs = SidecarDirs {
                log_dir: &log_dir,
                index_dir: &index_dir,
                folders: resolved.user_folders(),
            };
            let result = SidecarHost::start_dev(&dirs);
            state_handle.state::<HostState>().finish_start(result);
        });
    if spawned.is_err() {
        handle
            .state::<HostState>()
            .finish_start(Err(FailureKind::Internal));
    }
}
