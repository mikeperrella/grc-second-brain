// Registers the app's own commands as permission-gated: the build generates `allow-<command>` for
// each name in APP_COMMANDS, and a window can call a command only if its capability lists that
// permission (capabilities/main-window.json). Without the manifest, app commands are callable by
// every window with no permission at all.
include!("src/app_commands.rs");

fn main() {
    let attributes = tauri_build::Attributes::new()
        .app_manifest(tauri_build::AppManifest::new().commands(APP_COMMANDS));
    if let Err(error) = tauri_build::try_build(attributes) {
        // A build script reports failure by exiting non-zero; `expect` is denied crate-wide.
        eprintln!("tauri build configuration failed: {error:#}");
        std::process::exit(1);
    }
}
