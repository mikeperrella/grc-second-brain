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

fn bind_addresses_as_types() {
    // ruleid: grc-rs-bind-all-interfaces
    let _a = Ipv4Addr::UNSPECIFIED;
    // ruleid: grc-rs-bind-all-interfaces
    let _b = std::net::Ipv4Addr::UNSPECIFIED;
    // ruleid: grc-rs-bind-all-interfaces
    let _c = Ipv4Addr::new(0, 0, 0, 0);
    // ruleid: grc-rs-bind-all-interfaces
    let _d = std::net::Ipv4Addr::new(0, 0, 0, 0);
    // ruleid: grc-rs-bind-all-interfaces
    let _e = Ipv6Addr::UNSPECIFIED;
    // ruleid: grc-rs-bind-all-interfaces
    let _f = std::net::Ipv6Addr::UNSPECIFIED;
    // ruleid: grc-rs-bind-all-interfaces
    let _g = IpAddr::V4(Ipv4Addr::UNSPECIFIED);
    // ruleid: grc-rs-bind-all-interfaces
    let _h = SocketAddr::from(([0, 0, 0, 0], 8080));
    // ruleid: grc-rs-bind-all-interfaces
    let _i = std::net::SocketAddr::from(([0, 0, 0, 0], 0));
    // ok: grc-rs-bind-all-interfaces
    let _j = Ipv4Addr::LOCALHOST;
    // ok: grc-rs-bind-all-interfaces
    let _k = Ipv4Addr::new(127, 0, 0, 1);
    // ok: grc-rs-bind-all-interfaces
    let _l = Ipv6Addr::LOCALHOST;
    // ok: grc-rs-bind-all-interfaces
    let _m = SocketAddr::from(([127, 0, 0, 1], 0));
}

// ruleid: grc-rs-reqwest-without-no-proxy
use reqwest::blocking::Client as BlockingClient;

fn reqwest_clients() {
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _a = reqwest::Client::builder().build();
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _b = reqwest::Client::builder()
        .redirect(Policy::none())
        .timeout(Duration::from_secs(5))
        .build();
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _c = Client::builder().timeout(Duration::from_secs(5)).build();
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _d = reqwest::ClientBuilder::new().build();
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _e = ClientBuilder::new().connect_timeout(Duration::from_secs(2)).build();
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _f = reqwest::Client::new();
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _g = reqwest::Client::default();
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _h = reqwest::get("http://127.0.0.1:1/health");
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _i = reqwest::blocking::Client::new();
    // ruleid: grc-rs-reqwest-without-no-proxy
    let _i2 = reqwest::blocking::get("http://127.0.0.1:1/health");
    // ok: grc-rs-reqwest-without-no-proxy
    let _j = reqwest::Client::builder().no_proxy().build();
    // ok: grc-rs-reqwest-without-no-proxy
    let _k = reqwest::Client::builder()
        .no_proxy()
        .redirect(Policy::none())
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(5))
        .build();
    // ok: grc-rs-reqwest-without-no-proxy
    let _l = Client::builder()
        .redirect(Policy::none())
        .no_proxy()
        .build();
    // ok: grc-rs-reqwest-without-no-proxy
    let _m = reqwest::ClientBuilder::new().no_proxy().build();
    // ok: grc-rs-reqwest-without-no-proxy
    let _n = ClientBuilder::new().no_proxy().build();
}

fn network_urls() {
    // ruleid: grc-rs-non-loopback-url
    let _a = "https://example.com/health";
    // ruleid: grc-rs-non-loopback-url
    let _b = "http://localhost:8080/health";
    // ruleid: grc-rs-non-loopback-url
    let _c = "http://192.168.1.5:8080/";
    // ruleid: grc-rs-non-loopback-url
    let _d = "ws://example.com/socket";
    // ruleid: grc-rs-non-loopback-url
    let _e = "https://127.0.0.1.example.com/";
    // ruleid: grc-rs-non-loopback-url
    let _f = "http://127.0.0.10:80/";
    // ruleid: grc-rs-non-loopback-url
    let _g = format!("http://api.example.com:{}/v1", 80);
    // ok: grc-rs-non-loopback-url
    let _h = "http://127.0.0.1:11434";
    // ok: grc-rs-non-loopback-url
    let _i = "http://127.0.0.1/health";
    // ok: grc-rs-non-loopback-url
    let _j = "http://[::1]:8080/health";
    // ok: grc-rs-non-loopback-url
    let _k = format!("http://127.0.0.1:{}/health", 8080);
    // ok: grc-rs-non-loopback-url
    let _l = "ipc://not-a-network-url";
    // ok: grc-rs-non-loopback-url
    let _m = "just text that mentions http but is not a url";
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
