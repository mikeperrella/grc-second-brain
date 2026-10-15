//! Where the sidecar's index and vault directories come from (M4b, ADR-009 note for M4).
//!
//! * **Index directory:** under the app's local data directory (`app_local_data_dir()/index`),
//!   chosen by the host, never by the environment or the window.
//! * **Vault directory:** the user's saved vault wins (`folders`, chunk 6e). In a DEBUG build a
//!   developer may still point at a vault with the `GRC_VAULT_DIR` environment variable of the host
//!   process, but it is used ONLY when no vault is saved. In a RELEASE build the host never reads
//!   that variable, so a release app cannot be handed an arbitrary path through its environment.
//!
//! This file also holds the path primitives the folder code reuses: a path must be on a drive
//! letter, exist, and is passed on in canonical form without Windows' `\\?\` prefix (the sidecar
//! refuses UNC and device paths). The folder and file-name checks of CLAUDE.md 3.6 are in
//! `folders`.

use std::env;
use std::ffi::OsString;
use std::fs;
use std::path::{Component, Path, PathBuf, Prefix};

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

/// Whether `path` starts with a drive letter (`C:\x` or `\\?\C:\x`). A UNC share (`\\server\share`),
/// a device path (`\\.\COM1`) or a path with no prefix is not. Lexical only: nothing is opened.
///
/// Checked BEFORE `fs::canonicalize`, because canonicalizing a UNC path asks the network for it
/// (CLAUDE.md 3.2: no external network). A mapped network drive keeps a drive letter and cannot be
/// told apart here; that is a known limit (see the 6e report).
pub(crate) fn is_local_drive_path(path: &Path) -> bool {
    matches!(
        path.components().next(),
        Some(Component::Prefix(prefix))
            if matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_))
    )
}

/// The existing directory behind `path`, canonical and without the verbatim prefix, or `None`.
/// A path that is not on a drive letter is refused without being opened.
pub(crate) fn existing_directory(path: PathBuf) -> Option<PathBuf> {
    if !is_local_drive_path(&path) {
        return None;
    }
    let canonical = without_verbatim_prefix(fs::canonicalize(path).ok()?)?;
    canonical.is_dir().then_some(canonical)
}

/// The existing regular file behind `path`, canonical and without the verbatim prefix, or `None`.
/// Every link on the way is followed, so the answer is where the bytes really are.
pub(crate) fn existing_file(path: PathBuf) -> Option<PathBuf> {
    if !is_local_drive_path(&path) {
        return None;
    }
    let canonical = without_verbatim_prefix(fs::canonicalize(path).ok()?)?;
    canonical.is_file().then_some(canonical)
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

    // These tests read only what the crate already has on disk: its own manifest directory (an
    // existing directory) and `Cargo.toml` inside it (an existing file). They create nothing, so
    // there is no temporary directory to name or clean up.
    fn manifest_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
    }

    #[test]
    fn an_existing_directory_is_returned_canonical_and_without_the_prefix() {
        let found = existing_directory(manifest_dir()).unwrap();
        assert!(found.is_absolute());
        assert!(found.is_dir());
        assert!(!found.to_string_lossy().starts_with(VERBATIM_PREFIX));
    }

    #[test]
    fn a_missing_path_and_a_file_are_not_directories() {
        assert_eq!(
            existing_directory(manifest_dir().join("no-such-folder")),
            None
        );
        let file = manifest_dir().join("Cargo.toml");
        assert!(file.is_file(), "the test needs the manifest to exist");
        assert_eq!(existing_directory(file), None);
    }

    #[test]
    fn only_a_drive_letter_path_is_local() {
        for path in [r"C:\vault", r"c:\vault", r"\\?\C:\vault", r"C:\"] {
            assert!(is_local_drive_path(Path::new(path)), "{path}");
        }
        // UNC and device forms would make `canonicalize` reach the network or a device. A path with
        // no prefix has no drive to be on.
        for path in [
            r"\\server\share\x",
            r"\\?\UNC\server\share",
            r"\\.\COM1",
            r"\\?\GLOBALROOT\Device\x",
            r"\vault",
            r"vault",
            "",
        ] {
            assert!(!is_local_drive_path(Path::new(path)), "{path}");
        }
    }

    #[test]
    fn a_unc_or_device_path_is_refused_before_it_is_opened() {
        for path in [r"\\server\share\x", r"\\?\UNC\server\share", r"\\.\NUL"] {
            assert_eq!(existing_directory(PathBuf::from(path)), None, "{path}");
            assert_eq!(existing_file(PathBuf::from(path)), None, "{path}");
        }
    }

    #[test]
    fn an_existing_file_is_returned_canonical_and_a_directory_is_not_a_file() {
        let found = existing_file(manifest_dir().join("Cargo.toml")).unwrap();
        assert!(found.is_file());
        assert!(!found.to_string_lossy().starts_with(VERBATIM_PREFIX));
        assert_eq!(existing_file(manifest_dir()), None);
        assert_eq!(existing_file(manifest_dir().join("no-such-file")), None);
    }
}
