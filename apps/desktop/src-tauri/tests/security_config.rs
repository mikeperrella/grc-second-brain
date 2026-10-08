//! The window's security configuration, pinned (M2 plan section 9, CLAUDE.md 3.2 and 7).
//!
//! These tests read `tauri.conf.json` and the capability files as JSON and assert exactly what the
//! window may do: which commands it can call, and what its Content Security Policy allows. They
//! pin CONTENTS. They do not prove the running app behaves that way: the capability under
//! `invoke()` is proven in M2d, and the CSP as delivered is checked in a debug build of the bundled
//! app (docs/adr/005-webview-csp-and-capabilities.md).

use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

use desktop_lib::app_commands::APP_COMMANDS;
use serde_json::Value;

type TestResult = Result<(), Box<dyn Error>>;

fn crate_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn read_json(path: &Path) -> Result<Value, Box<dyn Error>> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}

fn tauri_conf() -> Result<Value, Box<dyn Error>> {
    read_json(&crate_dir().join("tauri.conf.json"))
}

fn security(conf: &Value) -> Result<&serde_json::Map<String, Value>, Box<dyn Error>> {
    conf.pointer("/app/security")
        .and_then(Value::as_object)
        .ok_or_else(|| "tauri.conf.json has no app.security object".into())
}

fn string_list(value: &Value) -> Result<Vec<&str>, Box<dyn Error>> {
    value
        .as_array()
        .ok_or("expected a JSON array")?
        .iter()
        .map(|item| item.as_str().ok_or_else(|| "expected a string".into()))
        .collect()
}

/// The production CSP as `directive -> sources`, rejecting duplicates and empty directives.
fn csp_directives(conf: &Value) -> Result<BTreeMap<String, Vec<String>>, Box<dyn Error>> {
    let csp = security(conf)?
        .get("csp")
        .and_then(Value::as_str)
        .ok_or("app.security.csp is missing or not a string")?;
    let mut directives = BTreeMap::new();
    for part in csp
        .split(';')
        .map(str::trim)
        .filter(|part| !part.is_empty())
    {
        let mut words = part.split_whitespace();
        let name = words.next().ok_or("empty directive")?.to_owned();
        let sources: Vec<String> = words.map(str::to_owned).collect();
        if sources.is_empty() {
            return Err(format!("directive {name} has no sources").into());
        }
        if directives.insert(name.clone(), sources).is_some() {
            return Err(format!("directive {name} appears twice").into());
        }
    }
    Ok(directives)
}

#[test]
fn there_is_exactly_one_capability_file() -> TestResult {
    let mut names = Vec::new();
    for entry in fs::read_dir(crate_dir().join("capabilities"))? {
        names.push(entry?.file_name().to_string_lossy().into_owned());
    }
    assert_eq!(names, ["main-window.json"], "capability files: {names:?}");
    Ok(())
}

#[test]
fn the_capability_grants_exactly_the_health_command_to_the_main_window() -> TestResult {
    let capability = read_json(&crate_dir().join("capabilities/main-window.json"))?;
    let object = capability
        .as_object()
        .ok_or("capability is not an object")?;

    // No other keys: `remote`, `webviews`, `local` and friends would change who gets the permission.
    let keys: BTreeSet<&str> = object.keys().map(String::as_str).collect();
    let expected_keys: BTreeSet<&str> = [
        "$schema",
        "identifier",
        "description",
        "windows",
        "permissions",
    ]
    .into_iter()
    .collect();
    assert_eq!(keys, expected_keys);

    assert_eq!(object["identifier"], "main-window");
    assert_eq!(string_list(&object["windows"])?, ["main"]);
    assert_eq!(
        string_list(&object["permissions"])?,
        ["allow-sidecar-health"],
        "no core:default, no plugin permission, nothing else"
    );
    Ok(())
}

#[test]
fn the_app_manifest_commands_are_exactly_the_health_command() -> TestResult {
    assert_eq!(APP_COMMANDS, ["sidecar_health"]);
    Ok(())
}

#[test]
fn the_capability_covers_each_app_command_and_nothing_more() -> TestResult {
    // Tauri names the generated permission `allow-<command>` with dashes for underscores.
    let capability = read_json(&crate_dir().join("capabilities/main-window.json"))?;
    let granted: BTreeSet<String> = string_list(&capability["permissions"])?
        .into_iter()
        .map(str::to_owned)
        .collect();
    let derived: BTreeSet<String> = APP_COMMANDS
        .iter()
        .map(|command| format!("allow-{}", command.replace('_', "-")))
        .collect();
    assert_eq!(granted, derived);
    Ok(())
}

#[test]
fn there_is_exactly_one_window_and_it_is_labelled_main() -> TestResult {
    let conf = tauri_conf()?;
    let windows = conf
        .pointer("/app/windows")
        .and_then(Value::as_array)
        .ok_or("app.windows is missing")?;
    assert_eq!(windows.len(), 1);
    assert_eq!(windows[0]["label"], "main");
    Ok(())
}

#[test]
fn the_production_csp_has_exactly_the_expected_directives() -> TestResult {
    let directives = csp_directives(&tauri_conf()?)?;
    let expected: BTreeMap<&str, Vec<&str>> = BTreeMap::from([
        ("default-src", vec!["'self'"]),
        ("script-src", vec!["'self'"]),
        ("style-src", vec!["'self'"]),
        ("img-src", vec!["'self'"]),
        ("font-src", vec!["'self'"]),
        ("connect-src", vec!["ipc:", "http://ipc.localhost"]),
        ("object-src", vec!["'none'"]),
        ("base-uri", vec!["'none'"]),
        ("form-action", vec!["'none'"]),
        ("frame-ancestors", vec!["'none'"]),
    ]);
    let actual: BTreeMap<&str, Vec<&str>> = directives
        .iter()
        .map(|(name, sources)| (name.as_str(), sources.iter().map(String::as_str).collect()))
        .collect();
    assert_eq!(actual, expected);
    Ok(())
}

#[test]
fn csp_connect_src_is_ipc_only() -> TestResult {
    let directives = csp_directives(&tauri_conf()?)?;
    assert_eq!(directives["connect-src"], ["ipc:", "http://ipc.localhost"]);
    assert_eq!(directives["default-src"], ["'self'"]);
    Ok(())
}

#[test]
fn the_production_csp_allows_nothing_remote_inline_or_dynamic() -> TestResult {
    let directives = csp_directives(&tauri_conf()?)?;
    for (name, sources) in &directives {
        for source in sources {
            // The only host-like source anywhere is the Tauri IPC origin.
            let host_like = source.contains("://") || source.contains('.') || source.contains(':');
            if host_like {
                assert!(
                    source == "ipc:" || source == "http://ipc.localhost",
                    "{name} allows {source}"
                );
            }
            for forbidden in [
                "*",
                "data:",
                "blob:",
                "filesystem:",
                "unsafe-eval",
                "unsafe-inline",
                "unsafe-hashes",
                "wasm-unsafe-eval",
                "127.0.0.1",
                "localhost:",
                "ws:",
                "wss:",
            ] {
                assert!(
                    !source.contains(forbidden),
                    "{name} allows {source} (contains {forbidden})"
                );
            }
        }
    }
    Ok(())
}

#[test]
fn the_prototype_is_frozen() -> TestResult {
    assert_eq!(security(&tauri_conf()?)?["freezePrototype"], true);
    Ok(())
}

#[test]
fn there_is_no_dev_csp() -> TestResult {
    // `pnpm tauri dev` serves the UI from the Vite dev server, where Tauri injects no CSP (round 1
    // evidence in ADR-005), so a `devCsp` would be a policy that is never applied. Keeping it out
    // means the only CSP in the file is the one that ships.
    let conf = tauri_conf()?;
    assert!(security(&conf)?.get("devCsp").is_none());
    Ok(())
}

#[test]
fn the_security_section_has_only_the_keys_we_decided_on() -> TestResult {
    let conf = tauri_conf()?;
    let keys: BTreeSet<&str> = security(&conf)?.keys().map(String::as_str).collect();
    let expected: BTreeSet<&str> = ["csp", "freezePrototype"].into_iter().collect();
    assert_eq!(keys, expected);
    Ok(())
}

#[test]
fn no_global_tauri_object_and_no_plugin_configuration() -> TestResult {
    let conf = tauri_conf()?;
    // `withGlobalTauri` would put `window.__TAURI__` in every page.
    assert!(
        matches!(
            conf.pointer("/app/withGlobalTauri"),
            None | Some(Value::Bool(false))
        ),
        "app.withGlobalTauri must be absent or false"
    );
    let plugins_empty = match conf.get("plugins") {
        None => true,
        Some(Value::Object(map)) => map.is_empty(),
        Some(_) => false,
    };
    assert!(plugins_empty, "tauri.conf.json must not configure plugins");
    Ok(())
}
