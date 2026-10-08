// Test fixture for the Rust rules in .semgrep/grc-airgap.yml. Run with:
//   semgrep --test --config .semgrep/ .semgrep/fixtures
// This file is never compiled. It is excluded from the main scan, because it violates the
// rules on purpose. All data here is synthetic.

fn shell_commands() {
    // This function is about the shell rule. The env_clear() statement below keeps the separate
    // env_clear rule quiet for every Command in it (that rule has its own fixtures further down).
    let mut _clean = Command::new("true");
    _clean.env_clear();
    // ruleid: grc-rs-shell-command
    let _a = Command::new("sh");
    // ruleid: grc-rs-shell-command
    let _b = Command::new("bash");
    // ruleid: grc-rs-shell-command
    let _c = Command::new("cmd.exe");
    // ruleid: grc-rs-shell-command
    let _d = Command::new("PowerShell");
    // ok: grc-rs-shell-command
    let _e = Command::new("git");
    // ruleid: grc-rs-shell-command
    let _f = std::process::Command::new("cmd");
    // ok: grc-rs-shell-command
    let _g = std::process::Command::new("git");
}

fn bind_addresses() {
    // ruleid: grc-rs-bind-all-interfaces
    let _a = TcpListener::bind("0.0.0.0:8080");
    // ruleid: grc-rs-bind-all-interfaces
    let _b = TcpListener::bind("[::]:8080");
    // ok: grc-rs-bind-all-interfaces
    let _c = TcpListener::bind("127.0.0.1:0");
}

// ruleid: grc-rs-tauri-shell-plugin
use tauri_plugin_shell::ShellExt;

fn tauri_plugins() {
    // ruleid: grc-rs-tauri-shell-plugin
    let _a = tauri_plugin_shell::init();
    // ok: grc-rs-tauri-shell-plugin
    let _b = tauri_plugin_opener::init();
}

fn command_with_no_env_clear_at_all() {
    // ruleid: grc-rs-command-without-env-clear
    let mut c = Command::new("git");
    c.arg("status");
}

fn command_with_full_path_and_no_env_clear() {
    // ruleid: grc-rs-command-without-env-clear
    let mut c = std::process::Command::new("git");
    c.arg("status");
}

fn env_clear_in_another_function_does_not_count() {
    // ruleid: grc-rs-command-without-env-clear
    let mut c = Command::new("git");
    c.env("KEY", "value");
}

fn some_other_function_that_clears() {
    // ok: grc-rs-command-without-env-clear
    let mut c = Command::new("git");
    c.env_clear();
}

fn env_clear_as_a_statement() {
    // ok: grc-rs-command-without-env-clear
    let mut c = Command::new("git");
    c.env_clear();
    c.env("KEY", "value");
}

fn env_clear_inside_a_match_arm(mode: &str) {
    match mode {
        "run" => {
            // ok: grc-rs-command-without-env-clear
            let mut c = Command::new("git");
            c.env_clear();
            c.arg("status");
        }
        _ => {}
    }
}

fn env_clear_inside_an_if_block(enabled: bool) {
    if enabled {
        // ok: grc-rs-command-without-env-clear
        let mut c = Command::new("git");
        c.env_clear();
    }
}

fn nested_command_without_env_clear_is_still_flagged(enabled: bool) {
    if enabled {
        // ruleid: grc-rs-command-without-env-clear
        let mut c = Command::new("git");
        c.arg("status");
    }
}

fn env_clear_in_the_chain() {
    // ok: grc-rs-command-without-env-clear
    let _c = Command::new("git").env_clear().output();
}

fn env_clear_in_the_chain_after_other_calls() {
    // ok: grc-rs-command-without-env-clear
    let _c = std::process::Command::new("git")
        .arg("status")
        .env_clear()
        .output();
}

fn debug_macro() {
    // ruleid: grc-rs-dbg-macro
    let _a = dbg!(1 + 1);
    // ruleid: grc-rs-dbg-macro
    dbg!("anything");
    // ok: grc-rs-dbg-macro
    println!("{}", 2);
}
