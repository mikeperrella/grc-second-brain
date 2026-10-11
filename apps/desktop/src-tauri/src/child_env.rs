//! The environment the sidecar process is started with (M2 plan section 5, CLAUDE.md 3.4/3.5).
//!
//! The host clears the child's environment (`env_clear()` in M2b) and then adds exactly the
//! variables built here. Nothing is inherited from the parent, including proxy variables, and
//! this function never reads the process environment: it is a pure function of its arguments.
//!
//! | Key              | Value                                   | Why                                        |
//! |------------------|-----------------------------------------|--------------------------------------------|
//! | `SYSTEMROOT`     | the Windows directory (from the OS API) | Winsock fails to start without it          |
//! | `GRC_AUTH_TOKEN` | the auth token                          | the contract; set only on this child       |
//! | `GRC_LOG_DIR`    | absolute log directory                  | the sidecar's default needs a home dir     |
//! | `GRC_INDEX_DIR`  | absolute index directory                | the sidecar has no home dir to default to  |
//! | `GRC_VAULT_DIR`  | absolute vault directory, if there is one | the knowledge vault (debug builds only until the folder picker, M6) |
//!
//! Verified against the real sidecar (M2 plan section 1): without `SYSTEMROOT` or `GRC_LOG_DIR`
//! Python exits with a traceback. `GRC_INDEX_DIR` and `GRC_VAULT_DIR` were added in M4b (ADR-009
//! note for M4): the allowlisted environment has no home directory, so without them the sidecar
//! would fall back to a home-based index folder that does not exist there and to no vault at all.
//! `GRC_VAULT_DIR` is optional: with no vault chosen the key is absent, and the sidecar reports
//! "no vault" instead of guessing one.

use std::collections::BTreeMap;
use std::ffi::{OsStr, OsString};
use std::fmt;
use std::path::Path;

use crate::token::AuthToken;

pub(crate) const ENV_SYSTEM_ROOT: &str = "SYSTEMROOT";
pub(crate) const ENV_AUTH_TOKEN: &str = "GRC_AUTH_TOKEN";
pub(crate) const ENV_LOG_DIR: &str = "GRC_LOG_DIR";
pub(crate) const ENV_INDEX_DIR: &str = "GRC_INDEX_DIR";
pub(crate) const ENV_VAULT_DIR: &str = "GRC_VAULT_DIR";

/// The complete allowlist. [`build_child_env`] produces keys from this list and no others (the
/// vault key only when a vault directory is given). `pub` because tests/supervisor_handshake.rs
/// compares the fake sidecar's reported variable names to it.
pub const ALLOWED_ENV_KEYS: [&str; 5] = [
    ENV_SYSTEM_ROOT,
    ENV_AUTH_TOKEN,
    ENV_LOG_DIR,
    ENV_INDEX_DIR,
    ENV_VAULT_DIR,
];

/// Why the environment could not be built. Unit variants: nothing here echoes a path or a value.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum ChildEnvError {
    #[error("the system root must be an absolute path")]
    SystemRootNotAbsolute,
    #[error("the log directory must be an absolute path")]
    LogDirNotAbsolute,
    #[error("the index directory must be an absolute path")]
    IndexDirNotAbsolute,
    #[error("the vault directory must be an absolute path")]
    VaultDirNotAbsolute,
    #[error("a path is not valid Unicode text")]
    PathNotUnicode,
    #[error("a path contains a NUL character")]
    PathContainsNul,
}

/// The environment to give the child. Its `Debug` output lists the KEY names only, because one of
/// the values is the auth token (`{:#?}` of a map would print it).
pub struct ChildEnv {
    entries: BTreeMap<&'static str, OsString>,
}

impl ChildEnv {
    /// The variables in key order, for `Command::env` (M2b).
    pub(crate) fn iter(&self) -> impl Iterator<Item = (&'static str, &OsStr)> {
        self.entries
            .iter()
            .map(|(key, value)| (*key, value.as_os_str()))
    }

    /// The key names only. Safe to log and to compare against [`ALLOWED_ENV_KEYS`].
    #[cfg(test)]
    pub(crate) fn keys(&self) -> impl Iterator<Item = &'static str> + '_ {
        self.entries.keys().copied()
    }

    #[cfg(test)]
    fn get(&self, key: &str) -> Option<&OsStr> {
        self.entries.get(key).map(OsString::as_os_str)
    }
}

impl fmt::Debug for ChildEnv {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ChildEnv")
            .field("keys", &self.entries.keys().collect::<Vec<_>>())
            .finish_non_exhaustive()
    }
}

/// Build the child environment from the Windows directory, the log, index and (optional) vault
/// directories, and the token.
///
/// Every path must be absolute (the sidecar rejects a relative directory setting, and a relative
/// `SYSTEMROOT` would be resolved against whatever the working directory is), valid Unicode (the
/// sidecar reads them as text) and free of NUL characters. With `vault_dir` of `None` the
/// `GRC_VAULT_DIR` key is simply absent.
pub fn build_child_env(
    system_root: &Path,
    log_dir: &Path,
    index_dir: &Path,
    vault_dir: Option<&Path>,
    token: &AuthToken,
) -> Result<ChildEnv, ChildEnvError> {
    if !system_root.is_absolute() {
        return Err(ChildEnvError::SystemRootNotAbsolute);
    }
    if !log_dir.is_absolute() {
        return Err(ChildEnvError::LogDirNotAbsolute);
    }
    if !index_dir.is_absolute() {
        return Err(ChildEnvError::IndexDirNotAbsolute);
    }
    if vault_dir.is_some_and(|dir| !dir.is_absolute()) {
        return Err(ChildEnvError::VaultDirNotAbsolute);
    }
    let system_root = checked_text(system_root)?;
    let log_dir = checked_text(log_dir)?;
    let index_dir = checked_text(index_dir)?;
    let vault_dir = vault_dir.map(checked_text).transpose()?;

    let mut entries = BTreeMap::new();
    entries.insert(ENV_SYSTEM_ROOT, OsString::from(system_root));
    entries.insert(ENV_AUTH_TOKEN, OsString::from(token.expose()));
    entries.insert(ENV_LOG_DIR, OsString::from(log_dir));
    entries.insert(ENV_INDEX_DIR, OsString::from(index_dir));
    if let Some(vault_dir) = vault_dir {
        entries.insert(ENV_VAULT_DIR, OsString::from(vault_dir));
    }
    Ok(ChildEnv { entries })
}

fn checked_text(path: &Path) -> Result<&str, ChildEnvError> {
    let text = path.to_str().ok_or(ChildEnvError::PathNotUnicode)?;
    if text.contains('\0') {
        return Err(ChildEnvError::PathContainsNul);
    }
    Ok(text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;
    use std::path::PathBuf;

    fn sample() -> (PathBuf, PathBuf, AuthToken) {
        (
            PathBuf::from(r"C:\Windows"),
            PathBuf::from(r"C:\Users\someone\AppData\Local\app\logs"),
            AuthToken::generate().unwrap(),
        )
    }

    fn index_dir() -> PathBuf {
        PathBuf::from(r"C:\Users\someone\AppData\Local\app\index")
    }

    fn vault_dir() -> PathBuf {
        PathBuf::from(r"C:\Users\someone\Documents\vault")
    }

    #[test]
    fn child_env_contains_only_allowlist() {
        let (root, logs, token) = sample();
        let env = build_child_env(&root, &logs, &index_dir(), Some(&vault_dir()), &token).unwrap();

        let keys: BTreeSet<&str> = env.keys().collect();
        let allowed: BTreeSet<&str> = ALLOWED_ENV_KEYS.into_iter().collect();
        assert_eq!(
            keys, allowed,
            "the environment must hold exactly the allowlisted keys"
        );
        assert_eq!(env.iter().count(), ALLOWED_ENV_KEYS.len());
    }

    #[test]
    fn with_no_vault_the_vault_key_is_absent_and_nothing_else_is() {
        let (root, logs, token) = sample();
        let env = build_child_env(&root, &logs, &index_dir(), None, &token).unwrap();

        let keys: BTreeSet<&str> = env.keys().collect();
        let expected: BTreeSet<&str> = ALLOWED_ENV_KEYS
            .into_iter()
            .filter(|key| *key != ENV_VAULT_DIR)
            .collect();
        assert_eq!(keys, expected);
        assert_eq!(env.get(ENV_VAULT_DIR), None);
    }

    #[test]
    fn child_env_carries_the_token_and_every_path_verbatim() {
        let (root, logs, token) = sample();
        let (index, vault) = (index_dir(), vault_dir());
        let env = build_child_env(&root, &logs, &index, Some(&vault), &token).unwrap();

        assert_eq!(env.get(ENV_AUTH_TOKEN), Some(OsStr::new(token.expose())));
        assert_eq!(env.get(ENV_SYSTEM_ROOT), Some(root.as_os_str()));
        assert_eq!(env.get(ENV_LOG_DIR), Some(logs.as_os_str()));
        assert_eq!(env.get(ENV_INDEX_DIR), Some(index.as_os_str()));
        assert_eq!(env.get(ENV_VAULT_DIR), Some(vault.as_os_str()));
    }

    #[test]
    fn the_token_value_appears_only_under_its_own_key() {
        let (root, logs, token) = sample();
        let env = build_child_env(&root, &logs, &index_dir(), Some(&vault_dir()), &token).unwrap();

        for (key, value) in env.iter() {
            let is_token_key = key == ENV_AUTH_TOKEN;
            let holds_token = value.to_string_lossy().contains(token.expose());
            assert_eq!(
                holds_token, is_token_key,
                "the token must be in {ENV_AUTH_TOKEN} and nowhere else (found under {key})"
            );
        }
    }

    #[test]
    fn allowlist_never_contains_inherited_or_proxy_variables() {
        // Documents the decision in the plan: nothing here may ever be added by accident.
        for forbidden in [
            "PATH",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "HOME",
            "APPDATA",
            "PYTHONPATH",
            "PYTHONHOME",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
        ] {
            assert!(
                !ALLOWED_ENV_KEYS.contains(&forbidden),
                "{forbidden} must not be allowed"
            );
        }
    }

    #[test]
    fn the_allowlist_is_exactly_these_five_keys() {
        // A pin: adding or removing a key must change this test, and so be seen in review.
        let allowed: BTreeSet<&str> = ALLOWED_ENV_KEYS.into_iter().collect();
        let expected: BTreeSet<&str> = [
            "SYSTEMROOT",
            "GRC_AUTH_TOKEN",
            "GRC_LOG_DIR",
            "GRC_INDEX_DIR",
            "GRC_VAULT_DIR",
        ]
        .into_iter()
        .collect();
        assert_eq!(allowed, expected);
        assert_eq!(ALLOWED_ENV_KEYS.len(), expected.len(), "no duplicate keys");
    }

    #[test]
    fn relative_paths_are_rejected() {
        let (root, logs, token) = sample();
        let (index, vault) = (index_dir(), vault_dir());
        assert_eq!(
            build_child_env(Path::new("Windows"), &logs, &index, None, &token).unwrap_err(),
            ChildEnvError::SystemRootNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, Path::new(r"logs\sidecar"), &index, None, &token).unwrap_err(),
            ChildEnvError::LogDirNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, Path::new(""), &index, None, &token).unwrap_err(),
            ChildEnvError::LogDirNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, &logs, Path::new(r"index"), None, &token).unwrap_err(),
            ChildEnvError::IndexDirNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, &logs, Path::new(""), Some(&vault), &token).unwrap_err(),
            ChildEnvError::IndexDirNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, &logs, &index, Some(Path::new(r"..\vault")), &token)
                .unwrap_err(),
            ChildEnvError::VaultDirNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, &logs, &index, Some(Path::new("")), &token).unwrap_err(),
            ChildEnvError::VaultDirNotAbsolute
        );
    }

    #[test]
    fn a_nul_character_in_a_path_is_rejected() {
        let (root, logs, token) = sample();
        let (index, vault) = (index_dir(), vault_dir());
        let bad = PathBuf::from("C:\\logs\0evil");
        assert_eq!(
            build_child_env(&root, &bad, &index, None, &token).unwrap_err(),
            ChildEnvError::PathContainsNul
        );
        let bad_root = PathBuf::from("C:\\Windows\0");
        assert_eq!(
            build_child_env(&bad_root, &logs, &index, None, &token).unwrap_err(),
            ChildEnvError::PathContainsNul
        );
        let bad_index = PathBuf::from("C:\\index\0evil");
        assert_eq!(
            build_child_env(&root, &logs, &bad_index, Some(&vault), &token).unwrap_err(),
            ChildEnvError::PathContainsNul
        );
        let bad_vault = PathBuf::from("C:\\vault\0evil");
        assert_eq!(
            build_child_env(&root, &logs, &index, Some(&bad_vault), &token).unwrap_err(),
            ChildEnvError::PathContainsNul
        );
    }

    #[cfg(windows)]
    #[test]
    fn a_path_that_is_not_unicode_is_rejected() {
        use std::os::windows::ffi::OsStringExt;
        let (root, logs, token) = sample();
        // 0xD800 is an unpaired UTF-16 surrogate: legal in a Windows path, not in UTF-8 text.
        let wide: Vec<u16> = "C:\\logs\\".encode_utf16().chain([0xD800]).collect();
        let bad = PathBuf::from(OsString::from_wide(&wide));
        assert_eq!(
            build_child_env(&root, &bad, &index_dir(), None, &token).unwrap_err(),
            ChildEnvError::PathNotUnicode
        );
        assert_eq!(
            build_child_env(&root, &logs, &bad, None, &token).unwrap_err(),
            ChildEnvError::PathNotUnicode
        );
        assert_eq!(
            build_child_env(&root, &logs, &index_dir(), Some(&bad), &token).unwrap_err(),
            ChildEnvError::PathNotUnicode
        );
    }

    #[test]
    fn debug_output_lists_keys_but_never_values() {
        let (root, logs, token) = sample();
        let secret = token.expose().to_owned();
        let env = build_child_env(&root, &logs, &index_dir(), Some(&vault_dir()), &token).unwrap();

        for output in [format!("{env:?}"), format!("{env:#?}")] {
            assert!(
                !output.contains(&secret),
                "token value leaked into: {output}"
            );
            assert!(
                !output.contains("AppData"),
                "a path value leaked into: {output}"
            );
            for key in ALLOWED_ENV_KEYS {
                assert!(output.contains(key), "key {key} missing from: {output}");
            }
        }
    }

    #[test]
    fn hostile_looking_values_are_carried_verbatim_and_never_add_keys() {
        // Strings a shell or the Windows API could treat specially: a PATH-style separator, an
        // environment-variable reference, command substitution, quotes, `=`, a newline and a
        // flag-like segment. Here they are plain data: not expanded, not split, not turned into
        // extra variables.
        let root = PathBuf::from(r"C:\Windows;C:\evil");
        let logs = PathBuf::from("C:\\logs\\%PATH%\\$(whoami)\\\"quoted\"\\a=b\\new\nline\\--flag");
        let index = PathBuf::from("C:\\index;C:\\evil\\%HOME%\\a=b");
        let vault = PathBuf::from("C:\\vault\\$(whoami)\\GRC_AUTH_TOKEN=x\\--flag");
        let token = AuthToken::generate().unwrap();

        let env = build_child_env(&root, &logs, &index, Some(&vault), &token).unwrap();

        let keys: BTreeSet<&str> = env.keys().collect();
        let allowed: BTreeSet<&str> = ALLOWED_ENV_KEYS.into_iter().collect();
        assert_eq!(keys, allowed, "hostile values must not add or rename keys");
        assert_eq!(env.iter().count(), ALLOWED_ENV_KEYS.len());
        assert_eq!(env.get(ENV_SYSTEM_ROOT), Some(root.as_os_str()));
        assert_eq!(env.get(ENV_LOG_DIR), Some(logs.as_os_str()));
        assert_eq!(env.get(ENV_INDEX_DIR), Some(index.as_os_str()));
        assert_eq!(env.get(ENV_VAULT_DIR), Some(vault.as_os_str()));
        assert_eq!(env.get(ENV_AUTH_TOKEN), Some(OsStr::new(token.expose())));
    }

    /// Every variant of `ChildEnvError`. The inner match has no wildcard, so adding a variant
    /// breaks compilation here until it is listed and therefore covered by the test below.
    fn every_child_env_error() -> Vec<ChildEnvError> {
        fn exhaustive(error: ChildEnvError) {
            match error {
                ChildEnvError::SystemRootNotAbsolute
                | ChildEnvError::LogDirNotAbsolute
                | ChildEnvError::IndexDirNotAbsolute
                | ChildEnvError::VaultDirNotAbsolute
                | ChildEnvError::PathNotUnicode
                | ChildEnvError::PathContainsNul => {}
            }
        }
        let all = vec![
            ChildEnvError::SystemRootNotAbsolute,
            ChildEnvError::LogDirNotAbsolute,
            ChildEnvError::IndexDirNotAbsolute,
            ChildEnvError::VaultDirNotAbsolute,
            ChildEnvError::PathNotUnicode,
            ChildEnvError::PathContainsNul,
        ];
        for error in &all {
            exhaustive(*error);
        }
        all
    }

    #[test]
    fn every_error_variant_formats_without_a_token_or_path() {
        // `ChildEnvError` does not implement `Serialize`. If that ever changes, add a
        // `serde_json::to_string` of every variant to the loop below.
        let token = AuthToken::generate().unwrap();
        for error in every_child_env_error() {
            for text in [
                format!("{error}"),
                format!("{error:?}"),
                format!("{error:#?}"),
            ] {
                assert!(!text.is_empty());
                assert!(!text.contains(token.expose()), "token leaked into: {text}");
                assert!(!text.contains("C:\\"), "a path leaked into: {text}");
            }
        }
    }

    #[test]
    fn error_type_has_no_payload_so_it_cannot_carry_a_token_or_path() {
        // Unit variants only: a data-less enum with a handful of variants fits in one byte.
        // Adding a `String` field to any variant makes it larger and this test goes red.
        assert!(std::mem::size_of::<ChildEnvError>() <= 1);
    }
}
