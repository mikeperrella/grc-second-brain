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
//!
//! Verified against the real sidecar (M2 plan section 1): these three are sufficient, and without
//! `SYSTEMROOT` or `GRC_LOG_DIR` Python exits with a traceback.

use std::collections::BTreeMap;
use std::ffi::{OsStr, OsString};
use std::fmt;
use std::path::Path;

use crate::token::AuthToken;

pub const ENV_SYSTEM_ROOT: &str = "SYSTEMROOT";
pub const ENV_AUTH_TOKEN: &str = "GRC_AUTH_TOKEN";
pub const ENV_LOG_DIR: &str = "GRC_LOG_DIR";

/// The complete allowlist. [`build_child_env`] produces exactly these keys and no others.
pub const ALLOWED_ENV_KEYS: [&str; 3] = [ENV_SYSTEM_ROOT, ENV_AUTH_TOKEN, ENV_LOG_DIR];

/// Why the environment could not be built. Unit variants: nothing here echoes a path or a value.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum ChildEnvError {
    #[error("the system root must be an absolute path")]
    SystemRootNotAbsolute,
    #[error("the log directory must be an absolute path")]
    LogDirNotAbsolute,
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
    pub fn iter(&self) -> impl Iterator<Item = (&'static str, &OsStr)> {
        self.entries
            .iter()
            .map(|(key, value)| (*key, value.as_os_str()))
    }

    /// The key names only. Safe to log and to compare against [`ALLOWED_ENV_KEYS`].
    pub fn keys(&self) -> impl Iterator<Item = &'static str> + '_ {
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

/// Build the child environment from the Windows directory, the log directory and the token.
///
/// Both paths must be absolute (the sidecar rejects a relative `GRC_LOG_DIR`, and a relative
/// `SYSTEMROOT` would be resolved against whatever the working directory is), valid Unicode (the
/// sidecar reads them as text) and free of NUL characters.
pub fn build_child_env(
    system_root: &Path,
    log_dir: &Path,
    token: &AuthToken,
) -> Result<ChildEnv, ChildEnvError> {
    if !system_root.is_absolute() {
        return Err(ChildEnvError::SystemRootNotAbsolute);
    }
    if !log_dir.is_absolute() {
        return Err(ChildEnvError::LogDirNotAbsolute);
    }
    let system_root = checked_text(system_root)?;
    let log_dir = checked_text(log_dir)?;

    let mut entries = BTreeMap::new();
    entries.insert(ENV_SYSTEM_ROOT, OsString::from(system_root));
    entries.insert(ENV_AUTH_TOKEN, OsString::from(token.expose()));
    entries.insert(ENV_LOG_DIR, OsString::from(log_dir));
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

    #[test]
    fn child_env_contains_only_allowlist() {
        let (root, logs, token) = sample();
        let env = build_child_env(&root, &logs, &token).unwrap();

        let keys: BTreeSet<&str> = env.keys().collect();
        let allowed: BTreeSet<&str> = ALLOWED_ENV_KEYS.into_iter().collect();
        assert_eq!(
            keys, allowed,
            "the environment must hold exactly the allowlisted keys"
        );
        assert_eq!(env.iter().count(), ALLOWED_ENV_KEYS.len());
    }

    #[test]
    fn child_env_carries_the_token_and_the_two_paths() {
        let (root, logs, token) = sample();
        let env = build_child_env(&root, &logs, &token).unwrap();

        assert_eq!(env.get(ENV_AUTH_TOKEN), Some(OsStr::new(token.expose())));
        assert_eq!(env.get(ENV_SYSTEM_ROOT), Some(root.as_os_str()));
        assert_eq!(env.get(ENV_LOG_DIR), Some(logs.as_os_str()));
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
    fn relative_paths_are_rejected() {
        let (root, logs, token) = sample();
        assert_eq!(
            build_child_env(Path::new("Windows"), &logs, &token).unwrap_err(),
            ChildEnvError::SystemRootNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, Path::new(r"logs\sidecar"), &token).unwrap_err(),
            ChildEnvError::LogDirNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, Path::new(""), &token).unwrap_err(),
            ChildEnvError::LogDirNotAbsolute
        );
    }

    #[test]
    fn a_nul_character_in_a_path_is_rejected() {
        let (root, logs, token) = sample();
        let bad = PathBuf::from("C:\\logs\0evil");
        assert_eq!(
            build_child_env(&root, &bad, &token).unwrap_err(),
            ChildEnvError::PathContainsNul
        );
        let bad_root = PathBuf::from("C:\\Windows\0");
        assert_eq!(
            build_child_env(&bad_root, &logs, &token).unwrap_err(),
            ChildEnvError::PathContainsNul
        );
    }

    #[cfg(windows)]
    #[test]
    fn a_path_that_is_not_unicode_is_rejected() {
        use std::os::windows::ffi::OsStringExt;
        let (root, _logs, token) = sample();
        // 0xD800 is an unpaired UTF-16 surrogate: legal in a Windows path, not in UTF-8 text.
        let wide: Vec<u16> = "C:\\logs\\".encode_utf16().chain([0xD800]).collect();
        let bad = PathBuf::from(OsString::from_wide(&wide));
        assert_eq!(
            build_child_env(&root, &bad, &token).unwrap_err(),
            ChildEnvError::PathNotUnicode
        );
    }

    #[test]
    fn debug_output_lists_keys_but_never_values() {
        let (root, logs, token) = sample();
        let secret = token.expose().to_owned();
        let env = build_child_env(&root, &logs, &token).unwrap();

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
        let token = AuthToken::generate().unwrap();

        let env = build_child_env(&root, &logs, &token).unwrap();

        let keys: BTreeSet<&str> = env.keys().collect();
        let allowed: BTreeSet<&str> = ALLOWED_ENV_KEYS.into_iter().collect();
        assert_eq!(keys, allowed, "hostile values must not add or rename keys");
        assert_eq!(env.iter().count(), ALLOWED_ENV_KEYS.len());
        assert_eq!(env.get(ENV_SYSTEM_ROOT), Some(root.as_os_str()));
        assert_eq!(env.get(ENV_LOG_DIR), Some(logs.as_os_str()));
        assert_eq!(env.get(ENV_AUTH_TOKEN), Some(OsStr::new(token.expose())));
    }

    /// Every variant of `ChildEnvError`. The inner match has no wildcard, so adding a variant
    /// breaks compilation here until it is listed and therefore covered by the test below.
    fn every_child_env_error() -> Vec<ChildEnvError> {
        fn exhaustive(error: ChildEnvError) {
            match error {
                ChildEnvError::SystemRootNotAbsolute
                | ChildEnvError::LogDirNotAbsolute
                | ChildEnvError::PathNotUnicode
                | ChildEnvError::PathContainsNul => {}
            }
        }
        let all = vec![
            ChildEnvError::SystemRootNotAbsolute,
            ChildEnvError::LogDirNotAbsolute,
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
