// M2a: pure logic for the host/sidecar channel. These modules are `pub` only until M2c wires
// them into the app; the ones that no integration test needs go back to `pub(crate)` then.
pub mod child_env;
pub mod ready;
pub mod token;

// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
// `expect`, not `allow`: once M2c removes the `.expect(..)` below, rustc reports an unfulfilled
// lint expectation (an error under `-D warnings`), so this attribute cannot be left behind.
#[expect(
    clippy::expect_used,
    reason = "template code; M2c replaces run() and removes this attribute"
)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![greet])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
