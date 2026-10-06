// Test fixture for the Rust rules in .semgrep/grc-airgap.yml. Run with:
//   semgrep --test --config .semgrep/ .semgrep/fixtures
// This file is never compiled. It is excluded from the main scan, because it violates the
// rules on purpose. All data here is synthetic.

fn shell_commands() {
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
}

fn bind_addresses() {
    // ruleid: grc-rs-bind-all-interfaces
    let _a = TcpListener::bind("0.0.0.0:8080");
    // ruleid: grc-rs-bind-all-interfaces
    let _b = TcpListener::bind("[::]:8080");
    // ok: grc-rs-bind-all-interfaces
    let _c = TcpListener::bind("127.0.0.1:0");
}
