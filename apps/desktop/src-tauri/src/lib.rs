// The host of the GRC Second Brain desktop app: it starts the Python sidecar, keeps it confined in
// a Job Object, and answers the window's one question (`sidecar_health`). Windows only
// (sidecar/mod.rs refuses to compile elsewhere).
//
// The modules are `pub` because integration tests in tests/ use them; within them, only the items
// those tests need are `pub` (each says why), the rest are `pub(crate)`.
pub mod app_commands;
pub mod child_env;
mod commands;
pub mod health;
pub mod host;
pub mod ready;
pub mod sidecar;
pub mod token;

use tauri::{Manager, RunEvent, WindowEvent};

use crate::host::{HostState, SidecarHost};
use crate::sidecar::supervisor::FailureKind;

/// Build and run the app. Returns when the last window is closed.
///
/// Sidecar lifecycle: `setup` starts it on a background thread, so the window never waits for it;
/// closing the window and the app's exit both call `HostState::shutdown`. Behind those two sit
/// `Drop` of the supervisor and the Job Object's kill-on-close, so the sidecar dies with the host
/// even if the host is killed (ADR-004).
pub fn run() -> Result<(), tauri::Error> {
    let app = tauri::Builder::default()
        .manage(HostState::default())
        .invoke_handler(tauri::generate_handler![commands::sidecar_health])
        .setup(|app| {
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
    let spawned = std::thread::Builder::new()
        .name("sidecar-start".to_owned())
        .spawn(move || {
            let result = SidecarHost::start_dev(&log_dir);
            state_handle.state::<HostState>().finish_start(result);
        });
    if spawned.is_err() {
        handle
            .state::<HostState>()
            .finish_start(Err(FailureKind::Internal));
    }
}
