// The commands the frontend may call. This file is included by build.rs (to generate the
// permission `allow-<name>` for each, with dashes) and compiled into the crate (so a test can pin the
// list). Use `//` comments only: `include!` cannot take `//!` inner doc comments.
//
// Adding a command here is a security decision: it widens what the window can ask the host to do.
// tests/security_config.rs fails until the capability file and that test are updated on purpose.

pub const APP_COMMANDS: &[&str] = &["sidecar_health"];
