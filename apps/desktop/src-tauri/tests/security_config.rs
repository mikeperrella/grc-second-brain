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

/// The commands the window may call, exactly, in the order `app_commands.rs` lists them. A command
/// added, removed or renamed is a security decision and must change this list on purpose.
const EXPECTED_COMMANDS: [&str; 5] = [
    "sidecar_health",
    "sidecar_call",
    "folders_get",
    "folder_pick",
    "folder_list_files",
];

/// The permissions that match, exactly: Tauri names the generated permission `allow-<command>`.
const EXPECTED_PERMISSIONS: [&str; 5] = [
    "allow-sidecar-health",
    "allow-sidecar-call",
    "allow-folders-get",
    "allow-folder-pick",
    "allow-folder-list-files",
];

#[test]
fn the_capability_grants_exactly_the_app_commands_to_the_main_window() -> TestResult {
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
        EXPECTED_PERMISSIONS,
        "no core:default, no plugin permission, nothing else"
    );
    Ok(())
}

#[test]
fn the_app_manifest_commands_are_exactly_the_app_commands() -> TestResult {
    assert_eq!(APP_COMMANDS, EXPECTED_COMMANDS);
    Ok(())
}

/// Every capability file's permission strings (there is exactly one file, checked above).
fn all_permissions() -> Result<Vec<String>, Box<dyn Error>> {
    let mut permissions = Vec::new();
    for entry in fs::read_dir(crate_dir().join("capabilities"))? {
        let capability = read_json(&entry?.path())?;
        for permission in string_list(&capability["permissions"])? {
            permissions.push(permission.to_owned());
        }
    }
    Ok(permissions)
}

#[test]
fn the_window_is_granted_no_plugin_permission() -> TestResult {
    let permissions = all_permissions()?;
    assert!(
        !permissions.is_empty(),
        "the check below would pass over nothing"
    );
    for permission in &permissions {
        assert!(
            permission.starts_with("allow-"),
            "{permission} is not one of this app's own `allow-<command>` permissions"
        );
        assert!(
            !permission.contains(':'),
            "{permission} looks like a plugin or core permission (`plugin:name`)"
        );
        for forbidden in ["dialog", "core", "opener", "fs"] {
            assert!(
                !permission.starts_with(forbidden),
                "{permission} begins with {forbidden}"
            );
        }
        // The plugin names must not appear in any form: `allow-open` or `allow-save` would be the
        // dialog plugin's own command permissions, which the window must never hold.
        for forbidden in ["dialog", "allow-open", "allow-save", "allow-message", "fs:"] {
            assert!(
                !permission.contains(forbidden),
                "{permission} contains {forbidden}"
            );
        }
    }
    Ok(())
}

/// The non-comment lines of a file that contain `needle`.
fn lines_with(text: &str, needle: &str) -> Vec<String> {
    text.lines()
        .map(str::trim)
        .filter(|line| !line.starts_with("//") && !line.starts_with('#'))
        .filter(|line| line.contains(needle))
        .map(str::to_owned)
        .collect()
}

#[test]
fn exactly_one_plugin_crate_is_a_direct_dependency_and_it_is_pinned_exactly() -> TestResult {
    let manifest = fs::read_to_string(crate_dir().join("Cargo.toml"))?;
    let plugin_lines = lines_with(&manifest, "tauri-plugin-");
    assert_eq!(
        plugin_lines.len(),
        1,
        "any later plugin must be a visible test change: {plugin_lines:?}"
    );
    let line = &plugin_lines[0];
    assert!(
        line.starts_with("tauri-plugin-dialog"),
        "the only plugin is the dialog plugin: {line}"
    );
    // An exact pin: `= "=2.x.y"`, not a range, a caret, a tilde, a wildcard or a path or git source.
    let (_, version) = line
        .split_once('=')
        .ok_or("no version on the plugin line")?;
    assert!(
        version.trim().starts_with("\"=2."),
        "the dialog plugin must be pinned with `=`: {line}"
    );
    for forbidden in ["git", "path", "*", "^", "~", ">", "<", "registry"] {
        assert!(!version.contains(forbidden), "{line}");
    }
    Ok(())
}

/// Every Rust source file under `src/`, as (relative name, text).
fn source_files() -> Result<Vec<(String, String)>, Box<dyn Error>> {
    fn walk(dir: &Path, out: &mut Vec<(String, String)>) -> Result<(), Box<dyn Error>> {
        for entry in fs::read_dir(dir)? {
            let path = entry?.path();
            if path.is_dir() {
                walk(&path, out)?;
            } else if path.extension().is_some_and(|extension| extension == "rs") {
                out.push((
                    path.to_string_lossy().into_owned(),
                    fs::read_to_string(&path)?,
                ));
            }
        }
        Ok(())
    }
    let mut files = Vec::new();
    walk(&crate_dir().join("src"), &mut files)?;
    Ok(files)
}

#[test]
fn the_fs_plugin_is_never_registered_and_no_fs_permission_is_granted() -> TestResult {
    // `tauri-plugin-fs` is in the lockfile only because the dialog plugin depends on it. Registering
    // it would give the window file-system commands to ask permissions for.
    let files = source_files()?;
    assert!(
        files.len() > 10,
        "the walk found too few files to mean anything"
    );
    for (name, text) in &files {
        assert!(
            lines_with(text, "tauri_plugin_fs").is_empty(),
            "{name} refers to the fs plugin"
        );
        assert!(
            lines_with(text, "tauri_plugin_dialog").is_empty()
                || name.ends_with("lib.rs")
                || name.ends_with("commands.rs"),
            "{name} uses the dialog plugin; only lib.rs registers it and commands.rs calls it"
        );
    }
    let lib = files
        .iter()
        .find(|(name, _)| name.ends_with("lib.rs"))
        .ok_or("src/lib.rs not found")?;
    assert_eq!(
        lines_with(&lib.1, ".plugin(").len(),
        1,
        "exactly one plugin is registered"
    );
    assert!(!lines_with(&lib.1, "tauri_plugin_dialog::init()").is_empty());
    let manifest = fs::read_to_string(crate_dir().join("Cargo.toml"))?;
    assert!(lines_with(&manifest, "tauri-plugin-fs").is_empty());
    for permission in all_permissions()? {
        assert!(
            !permission.starts_with("fs")
                && !permission.contains("fs:")
                && !permission.contains("-fs-"),
            "{permission} is a file-system permission"
        );
    }
    Ok(())
}

/// The text of the item that starts at `header` (for example `pub struct FolderView`) up to its
/// closing brace at the start of a line.
fn item_block<'a>(source: &'a str, header: &str) -> Result<&'a str, Box<dyn Error>> {
    let start = source
        .find(header)
        .ok_or_else(|| format!("{header} not found"))?;
    let end = source[start..]
        .find("\n}")
        .ok_or_else(|| format!("{header} has no closing brace"))?;
    Ok(&source[start..start + end + 2])
}

/// Split `text` at top-level commas (not inside `<...>`).
fn top_level_split(text: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let (mut depth, mut current) = (0usize, String::new());
    for character in text.chars() {
        match character {
            '<' | '(' => depth += 1,
            '>' | ')' => depth = depth.saturating_sub(1),
            ',' if depth == 0 => {
                parts.push(std::mem::take(&mut current));
                continue;
            }
            _ => {}
        }
        current.push(character);
    }
    parts.push(current);
    parts
        .into_iter()
        .map(|part| part.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|part| !part.is_empty())
        .collect()
}

#[test]
fn no_command_hands_the_window_a_path_it_can_submit_back() -> TestResult {
    let source = fs::read_to_string(crate_dir().join("src/commands.rs"))?;
    let allowed_arguments = [
        "state: State<'_, HostState>",
        "folders: State<'_, FolderSettings>",
        "app: AppHandle",
        "kind: FolderKind",
        "request: Value",
    ];
    let allowed_ok = [
        "HealthReply",
        "ProxyReply",
        "FoldersReply",
        "PickReply",
        "FileList",
    ];
    let allowed_err = ["HealthError", "ProxyError", "FolderError"];

    let mut found = Vec::new();
    for chunk in source.split("#[tauri::command]").skip(1) {
        let after_fn = chunk
            .split_once("fn ")
            .ok_or("a #[tauri::command] with no function")?
            .1;
        let name = after_fn
            .split('(')
            .next()
            .ok_or("no name")?
            .trim()
            .to_owned();
        let open = after_fn.find('(').ok_or("no parameter list")?;
        let close = after_fn.find(") ->").ok_or("no return type")?;
        let arguments = top_level_split(&after_fn[open + 1..close]);
        let returns_text = after_fn[close + 4..]
            .split('{')
            .next()
            .ok_or("no body")?
            .trim()
            .to_owned();

        for argument in &arguments {
            assert!(
                allowed_arguments.contains(&argument.as_str()),
                "{name} takes `{argument}`; only a state, the app handle, an enum kind or the typed \
                 route request may be arguments, never a path or a string"
            );
            for forbidden in [
                "Path", "PathBuf", "String", "str", "OsStr", "OsString", "Vec",
            ] {
                assert!(
                    !argument
                        .split(|c: char| !c.is_alphanumeric())
                        .any(|word| word == forbidden),
                    "{name} takes `{argument}`"
                );
            }
        }
        let inner = returns_text
            .strip_prefix("Result<")
            .and_then(|rest| rest.strip_suffix('>'))
            .ok_or_else(|| format!("{name} must return a Result"))?;
        let pair = top_level_split(inner);
        assert_eq!(pair.len(), 2, "{name}: {returns_text}");
        assert!(
            allowed_ok.contains(&pair[0].as_str()),
            "{name} returns {}",
            pair[0]
        );
        assert!(
            allowed_err.contains(&pair[1].as_str()),
            "{name} errors with {}",
            pair[1]
        );
        found.push((name, arguments));
    }

    let names: Vec<&str> = found.iter().map(|(name, _)| name.as_str()).collect();
    assert_eq!(names, EXPECTED_COMMANDS, "the commands in commands.rs");
    for (name, arguments) in &found {
        let has_kind = arguments
            .iter()
            .any(|argument| argument == "kind: FolderKind");
        // The two folder commands that name a folder do it with the enum, and only with it.
        assert_eq!(
            has_kind,
            name == "folder_pick" || name == "folder_list_files",
            "{name}"
        );
    }

    // The reply types are display values and words: no field of any of them can hold a path.
    let folders = fs::read_to_string(crate_dir().join("src/folders/mod.rs"))?;
    for header in [
        "pub struct FoldersReply",
        "pub struct FolderView",
        "pub struct FileList",
        "pub enum PickReply",
        "pub enum FolderError",
        "pub enum FolderProblem",
        "pub enum FolderKind",
    ] {
        let block = item_block(&folders, header)?;
        for forbidden in ["Path", "PathBuf", "OsString", "OsStr"] {
            assert!(
                !block.contains(forbidden),
                "{header} holds a {forbidden}; a reply may carry only display strings and words"
            );
        }
    }
    Ok(())
}

#[test]
fn the_dialog_plugin_is_called_only_from_rust_and_never_with_a_window_argument_path() -> TestResult
{
    // `folder_pick` is the only place that opens the dialog, and it takes no path.
    let source = fs::read_to_string(crate_dir().join("src/commands.rs"))?;
    assert_eq!(lines_with(&source, ".blocking_pick_folder()").len(), 1);
    assert!(!lines_with(&source, ".dialog()").is_empty());
    let others: Vec<String> = source_files()?
        .into_iter()
        .filter(|(name, _)| !name.ends_with("commands.rs"))
        .flat_map(|(_, text)| lines_with(&text, ".dialog()"))
        .collect();
    assert!(
        others.is_empty(),
        "the dialog is opened elsewhere: {others:?}"
    );
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
fn the_window_is_resizable_with_the_decided_default_and_minimum_sizes() -> TestResult {
    // docs/DESIGN.md section 2 and M6_PLAN.md E.1 item 10: resizable, 1100 by 720, minimum 900 by 600.
    let conf = tauri_conf()?;
    let windows = conf
        .pointer("/app/windows")
        .and_then(Value::as_array)
        .ok_or("app.windows is missing")?;
    assert_eq!(windows.len(), 1);
    let window = &windows[0];
    assert_eq!(window["resizable"], true);
    assert_eq!(window["width"], 1100);
    assert_eq!(window["height"], 720);
    assert_eq!(window["minWidth"], 900);
    assert_eq!(window["minHeight"], 600);
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
