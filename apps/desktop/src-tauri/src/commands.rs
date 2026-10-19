//! The commands the window can call. Every name here must also be in `app_commands.rs` (which makes
//! it permission-gated) and in `capabilities/main-window.json`; tests/security_config.rs checks
//! that the list is exactly the five below and that no command takes or returns a path the window
//! could submit back as authority.
//!
//! * `sidecar_health`: the sidecar's health. No arguments.
//! * `sidecar_call`: one proxied sidecar route (ADR-020 decision 1). One tagged argument, the
//!   `RouteRequest`, taken as JSON so that a malformed one is the fixed word `bad_request` and not
//!   Tauri's own error text, which can quote the input.
//! * `folders_get`: both folder columns, display strings and typed reasons.
//! * `folder_pick`: ask the user for a folder of a NAMED KIND (an enum) in the native dialog, check
//!   it, save it for the next launch. The window never supplies a path, and gets back a display
//!   string.
//! * `folder_list_files`: the file names in an in-use folder of a named kind (workbooks; `.csv` for
//!   the import folder).

use serde_json::Value;
use tauri::{AppHandle, State};
use tauri_plugin_dialog::DialogExt;

use crate::folders::{FileList, FolderError, FolderKind, FolderSettings, FoldersReply, PickReply};
use crate::health::{HealthError, HealthReply};
use crate::host::HostState;
use crate::proxy::{ProxyError, ProxyReply, RouteRequest};

/// The sidecar's health, as the host sees it. Takes no arguments, so the window cannot choose a
/// host, a port or a path; it only gets one of the secret-free replies of `health.rs`.
#[tauri::command]
pub(crate) async fn sidecar_health(
    state: State<'_, HostState>,
) -> Result<HealthReply, HealthError> {
    state.health().await
}

/// One call to a route in the closed table. The window sends the route name and that route's typed
/// arguments; everything else (URL, method, headers, port, token) is the host's.
#[tauri::command]
pub(crate) async fn sidecar_call(
    state: State<'_, HostState>,
    request: Value,
) -> Result<ProxyReply, ProxyError> {
    let request = RouteRequest::from_value(request)?;
    state.call(&request).await
}

/// Both columns for every folder: what is in use now and what is saved for the next launch.
#[tauri::command]
pub(crate) async fn folders_get(
    folders: State<'_, FolderSettings>,
) -> Result<FoldersReply, FolderError> {
    Ok(folders.view())
}

/// Open the native folder dialog for `kind`, validate the choice and save it. `cancelled` if the
/// user closed the dialog. The dialog is called from here, in Rust: the window has no dialog
/// permission.
#[tauri::command]
pub(crate) async fn folder_pick(
    app: AppHandle,
    folders: State<'_, FolderSettings>,
    kind: FolderKind,
) -> Result<PickReply, FolderError> {
    let handle = app.clone();
    // The dialog blocks until the user answers, so it runs on a blocking thread, not on the
    // async executor and not on the main thread.
    let chosen = tauri::async_runtime::spawn_blocking(move || {
        handle
            .dialog()
            .file()
            .set_title("Choose a folder for ALIBI")
            .blocking_pick_folder()
    })
    .await
    .map_err(|_| FolderError::Unavailable)?;
    let Some(chosen) = chosen else {
        return Ok(PickReply::Cancelled);
    };
    let path = chosen.into_path().map_err(|_| FolderError::Unavailable)?;
    folders.save_picked(kind, path)
}

/// The file names in the in-use questionnaire, export or import folder (workbooks, or `.csv` for
/// the import folder).
#[tauri::command]
pub(crate) async fn folder_list_files(
    folders: State<'_, FolderSettings>,
    kind: FolderKind,
) -> Result<FileList, FolderError> {
    folders.list_files(kind)
}
