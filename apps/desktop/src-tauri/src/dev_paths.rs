//! Where the sidecar's index and vault directories come from (M4b, ADR-009 note for M4).
//!
//! * **Index directory:** under the app's local data directory (`app_local_data_dir()/index`),
//!   chosen by the host, never by the environment or the window.
//! * **Vault directory:** until the folder picker (M6) there is no way for a user to choose one.
//!   In a DEBUG build a developer may point at a vault with the `GRC_VAULT_DIR` environment
//!   variable of the host process. In a RELEASE build the host never reads that variable, so a
//!   release app cannot be handed an arbitrary path through its environment.
//!
//! No Tauri command takes a path in M4, so the Rust-side `canonicalize` + `starts_with` check of
//! CLAUDE.md 3.6 (for paths from the window) is still owned by the folder-picker chunk. What is
//! checked here is only the developer's debug variable: it must be absolute, must exist, must be a
//! directory, and is passed on in canonical form without Windows' `\\?\` prefix (the sidecar
//! refuses UNC and device paths).

use std::env;
use std::ffi::OsString;
use std::fs;
use std::path::PathBuf;

/// The environment variable of the HOST process that debug builds read.
pub(crate) const DEV_VAULT_ENV: &str = "GRC_VAULT_DIR";

const VERBATIM_PREFIX: &str = r"\\?\";
const VERBATIM_UNC_PREFIX: &str = r"UNC\";

/// The vault directory a debug build was given, or `None`.
///
/// Pure: `debug` is passed in (the caller passes `cfg!(debug_assertions)`), so a test can prove
/// that a release build ignores the variable. Returns `None` unless `debug` is true and the value
/// is an absolute path.
pub(crate) fn dev_vault_dir(value: Option<OsString>, debug: bool) -> Option<PathBuf> {
    if !debug {
        return None;
    }
    let path = PathBuf::from(value?);
    path.is_absolute().then_some(path)
}

/// `\\?\C:\x` -> `C:\x`. A verbatim UNC path (`\\?\UNC\server\share`) or a device path is refused
/// (`None`): those can reach the network or raw devices. A path without the prefix is unchanged.
fn without_verbatim_prefix(path: PathBuf) -> Option<PathBuf> {
    let text = path.to_str()?;
    let Some(rest) = text.strip_prefix(VERBATIM_PREFIX) else {
        return Some(path);
    };
    let is_drive_path =
        rest.as_bytes().get(1) == Some(&b':') && !rest.starts_with(VERBATIM_UNC_PREFIX);
    is_drive_path.then(|| PathBuf::from(rest))
}

/// The existing directory behind `path`, canonical and without the verbatim prefix, or `None`.
pub(crate) fn existing_directory(path: PathBuf) -> Option<PathBuf> {
    let canonical = without_verbatim_prefix(fs::canonicalize(path).ok()?)?;
    canonical.is_dir().then_some(canonical)
}

/// The debug-only vault directory for this process, ready to pass to the sidecar. Always `None`
/// in a release build, without reading the environment.
pub(crate) fn vault_dir_for_this_build() -> Option<PathBuf> {
    let debug = cfg!(debug_assertions);
    let value = if debug {
        env::var_os(DEV_VAULT_ENV)
    } else {
        None
    };
    existing_directory(dev_vault_dir(value, debug)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn absolute() -> OsString {
        OsString::from(r"C:\Users\someone\Documents\vault")
    }

    #[test]
    fn dev_vault_dir_is_none_in_release() {
        // An otherwise valid absolute path: only the `debug` flag can explain the `None`.
        assert_eq!(dev_vault_dir(Some(absolute()), false), None);
    }

    #[test]
    fn dev_vault_dir_passes_an_absolute_path_in_debug() {
        assert_eq!(
            dev_vault_dir(Some(absolute()), true),
            Some(PathBuf::from(absolute()))
        );
    }

    #[test]
    fn dev_vault_dir_is_none_without_a_value() {
        assert_eq!(dev_vault_dir(None, true), None);
        assert_eq!(dev_vault_dir(None, false), None);
    }

    #[test]
    fn dev_vault_dir_rejects_relative_and_empty_values() {
        for value in ["", "vault", r"..\vault", r".\vault", r"\vault"] {
            assert_eq!(
                dev_vault_dir(Some(OsString::from(value)), true),
                None,
                "{value:?} must not be accepted"
            );
        }
    }

    #[test]
    fn the_verbatim_prefix_is_removed_from_drive_paths_only() {
        assert_eq!(
            without_verbatim_prefix(PathBuf::from(r"\\?\C:\vault")),
            Some(PathBuf::from(r"C:\vault"))
        );
        assert_eq!(
            without_verbatim_prefix(PathBuf::from(r"C:\vault")),
            Some(PathBuf::from(r"C:\vault"))
        );
    }

    #[test]
    fn verbatim_unc_and_device_paths_are_refused() {
        for path in [
            r"\\?\UNC\server\share",
            r"\\?\GLOBALROOT\Device\x",
            r"\\?\Volume{1234}\x",
            r"\\?\pipe\x",
        ] {
            assert_eq!(
                without_verbatim_prefix(PathBuf::from(path)),
                None,
                "{path} must be refused"
            );
        }
    }

    /// A unique directory under the system temp directory, removed on drop.
    struct TempDir(PathBuf);

    impl TempDir {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = env::temp_dir().join(format!(
                "grc_dev_paths_{}_{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::SeqCst)
            ));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn an_existing_directory_is_returned_canonical_and_without_the_prefix() {
        let temp = TempDir::new();
        let found = existing_directory(temp.0.clone()).unwrap();
        assert!(found.is_absolute());
        assert!(found.is_dir());
        assert!(!found.to_string_lossy().starts_with(VERBATIM_PREFIX));
    }

    #[test]
    fn a_missing_path_and_a_file_are_not_directories() {
        let temp = TempDir::new();
        assert_eq!(existing_directory(temp.0.join("missing")), None);
        let file = temp.0.join("file.txt");
        fs::write(&file, "x").unwrap();
        assert_eq!(existing_directory(file), None);
    }
}
