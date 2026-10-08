//! The commands the window can call. Every name here must also be in `app_commands.rs` (which makes
//! it permission-gated) and in `capabilities/main-window.json`; tests/security_config.rs checks
//! that the list is exactly `["sidecar_health"]`.

use tauri::State;

use crate::health::{HealthError, HealthReply};
use crate::host::HostState;

/// The sidecar's health, as the host sees it. Takes no arguments, so the window cannot choose a
/// host, a port or a path; it only gets one of the secret-free replies of `health.rs`.
#[tauri::command]
pub(crate) async fn sidecar_health(
    state: State<'_, HostState>,
) -> Result<HealthReply, HealthError> {
    state.health().await
}
