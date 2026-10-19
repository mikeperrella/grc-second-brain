//! File names from the window (CLAUDE.md 3.6, ADR-020 decision 7, backlog G9).
//!
//! The window never sends a path, only a file NAME for the questionnaire, export or import folder. Before a
//! request that carries one is forwarded to the sidecar, the host checks it in two steps:
//!
//! 1. [`is_one_component`]: the name is one path component. No separator, no `..`, no drive or
//!    stream colon, no NUL or other control character, no wildcard, no reserved device name, no
//!    trailing dot or space, at most 260 characters.
//! 2. [`resolve_file`]: `canonicalize(root.join(name))` (every link followed) must start with the
//!    canonical root and must be a regular file.
//!
//! The sidecar repeats its own `resolve_within` (defence in depth). Windows lets an unprivileged
//! process create a hard link to a file but not a symlink, so a link that points outside the folder
//! is covered by a test only where the machine allows creating one; a hard link inside the folder
//! is a real file in it and cannot be told apart.
//!
//! [`list_workbooks`] lists the `.xlsx` and `.xlsm` names the window may offer, and [`list_csv`] the
//! `.csv` names of the Imports folder, both bounded in count.

use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};

use crate::dev_paths::{existing_directory, existing_file};

/// The sidecar's own limit on a file name (`MAX_FILE_NAME_CHARS`), mirrored.
pub const MAX_NAME_CHARS: usize = 260;
/// The most names one listing returns.
pub const MAX_LISTED_FILES: usize = 500;
/// The most directory entries one listing reads, so a folder of millions of files cannot stall it.
pub const MAX_SCANNED_ENTRIES: usize = 20_000;

const RESERVED_DEVICE_NAMES: [&str; 6] = ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"];

/// Why a name was refused. Callers map all of them to `bad_request`; nothing is echoed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum NameError {
    #[error("the name is not one plain file name")]
    NotOneComponent,
    #[error("the file does not exist in the folder or is not a regular file")]
    NotAFile,
    #[error("the file resolves to somewhere outside the folder")]
    Escapes,
}

/// `COM0` to `COM9`, `LPT0` to `LPT9` and their superscript forms (`COM¹`), in any case.
fn is_numbered_device(stem: &str) -> bool {
    let upper = stem.to_ascii_uppercase();
    let Some(rest) = upper
        .strip_prefix("COM")
        .or_else(|| upper.strip_prefix("LPT"))
    else {
        return false;
    };
    let mut chars = rest.chars();
    matches!(
        (chars.next(), chars.next()),
        (Some('0'..='9' | '¹' | '²' | '³'), None)
    )
}

/// A reserved Windows device name, with or without an extension (`CON`, `nul.xlsx`, `COM1 .txt`).
/// Windows ignores the extension and trailing spaces of the part before the first dot.
fn is_reserved_device(name: &str) -> bool {
    let stem = name.split('.').next().unwrap_or("").trim_end_matches(' ');
    RESERVED_DEVICE_NAMES
        .iter()
        .any(|reserved| stem.eq_ignore_ascii_case(reserved))
        || is_numbered_device(stem)
}

/// Whether `name` is one plain file name that cannot mean anything but a file directly in a folder.
pub fn is_one_component(name: &str) -> bool {
    if !(1..=MAX_NAME_CHARS).contains(&name.chars().count()) {
        return false;
    }
    if name == "." || name == ".." || name.ends_with('.') || name.ends_with(' ') {
        return false;
    }
    let forbidden = |c: char| {
        c.is_control() || matches!(c, '/' | '\\' | ':' | '<' | '>' | '"' | '|' | '?' | '*')
    };
    if name.chars().any(forbidden) {
        return false;
    }
    !is_reserved_device(name)
}

/// The canonical path of the regular file `name` directly named in `root`, or why not.
///
/// `root` is made canonical here again, so the containment test is a comparison of two canonical
/// paths whatever the caller passed. A link inside `root` that points outside it resolves to a path
/// that does not start with `root` and is refused.
pub fn resolve_file(root: &Path, name: &str) -> Result<PathBuf, NameError> {
    if !is_one_component(name) {
        return Err(NameError::NotOneComponent);
    }
    let root = existing_directory(root.to_owned()).ok_or(NameError::NotAFile)?;
    let canonical = existing_file(root.join(name)).ok_or(NameError::NotAFile)?;
    if !canonical.starts_with(&root) {
        return Err(NameError::Escapes);
    }
    Ok(canonical)
}

/// The names offered to the window.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Listing {
    pub files: Vec<String>,
    /// More matching files exist than were returned (the count cap or the scan cap was reached).
    pub truncated: bool,
}

fn is_workbook_name(name: &str) -> bool {
    let extension = Path::new(name).extension().and_then(OsStr::to_str);
    let workbook = extension
        .is_some_and(|ext| ext.eq_ignore_ascii_case("xlsx") || ext.eq_ignore_ascii_case("xlsm"));
    // `~$name.xlsx` is the lock file Excel leaves next to an open workbook, not a workbook.
    workbook && !name.starts_with("~$") && is_one_component(name)
}

/// An export the reality check can read: `name.csv` in any case. Not a lock or temporary file of
/// a spreadsheet program (`~$`), and one plain component.
fn is_csv_name(name: &str) -> bool {
    let csv = Path::new(name)
        .extension()
        .and_then(OsStr::to_str)
        .is_some_and(|ext| ext.eq_ignore_ascii_case("csv"));
    csv && !name.starts_with("~$") && is_one_component(name)
}

/// The `.xlsx` and `.xlsm` files directly in `root`, sorted without regard to case. Directories and
/// links are not listed (a link is not followed here; [`resolve_file`] decides at use time). `None`
/// if the folder cannot be read.
pub fn list_workbooks(root: &Path) -> Option<Listing> {
    list_matching(root, is_workbook_name)
}

/// The `.csv` files directly in `root` (the Imports folder), with the same rules, caps and order as
/// [`list_workbooks`].
pub fn list_csv(root: &Path) -> Option<Listing> {
    list_matching(root, is_csv_name)
}

fn list_matching(root: &Path, wanted: fn(&str) -> bool) -> Option<Listing> {
    let mut files = Vec::new();
    let mut truncated = false;
    for (scanned, entry) in fs::read_dir(root).ok()?.enumerate() {
        if scanned >= MAX_SCANNED_ENTRIES {
            truncated = true;
            break;
        }
        let Ok(entry) = entry else { continue };
        let is_regular = entry.file_type().is_ok_and(|kind| kind.is_file());
        let name = entry.file_name();
        let Some(name) = name.to_str() else { continue };
        if is_regular && wanted(name) {
            files.push(name.to_owned());
        }
    }
    files.sort_by_cached_key(|name| name.to_lowercase());
    if files.len() > MAX_LISTED_FILES {
        files.truncate(MAX_LISTED_FILES);
        truncated = true;
    }
    Some(Listing { files, truncated })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_names_are_one_component() {
        for name in [
            "q.xlsx",
            "SIG Lite 2024.xlsx",
            "a-b_c (1).xlsm",
            "résumé.xlsx",
            ".hidden",
            "a.b.c.xlsx",
            "console.xlsx",
            "COM10.xlsx",
            "communication.xlsx",
            &"x".repeat(260),
        ] {
            assert!(is_one_component(name), "{name:?} should be accepted");
        }
    }

    #[test]
    fn separators_dots_drives_streams_and_controls_are_refused() {
        for name in [
            "",
            ".",
            "..",
            "...",
            "a/b.xlsx",
            r"a\b.xlsx",
            "../q.xlsx",
            r"..\q.xlsx",
            "/q.xlsx",
            r"\q.xlsx",
            r"C:\q.xlsx",
            "C:q.xlsx",
            "q.xlsx:stream",
            r"\\server\share\q.xlsx",
            r"\\?\C:\q.xlsx",
            "q\0.xlsx",
            "q\n.xlsx",
            "q\t.xlsx",
            "q\u{7f}.xlsx",
            "q*.xlsx",
            "q?.xlsx",
            "q<.xlsx",
            "q>.xlsx",
            "q|.xlsx",
            "q\".xlsx",
            "q.xlsx.",
            "q.xlsx ",
            "q ",
            "q.",
        ] {
            assert!(!is_one_component(name), "{name:?} must be refused");
        }
    }

    #[test]
    fn reserved_device_names_are_refused_in_any_form() {
        for name in [
            "CON",
            "con",
            "Con.xlsx",
            "PRN",
            "AUX.txt",
            "NUL",
            "nul.xlsx",
            "NUL.tar.gz",
            "CONIN$",
            "CONOUT$",
            "COM0",
            "COM1",
            "com9.xlsx",
            "LPT1",
            "lpt0.txt",
            "COM\u{b9}",
            "LPT\u{b2}.xlsx",
            "COM\u{b3}",
            "CON .xlsx",
            "COM1 .txt",
        ] {
            assert!(!is_one_component(name), "{name:?} must be refused");
        }
    }

    #[test]
    fn a_name_is_limited_to_260_characters_counted_as_characters() {
        assert!(is_one_component(&"x".repeat(260)));
        assert!(!is_one_component(&"x".repeat(261)));
        // 260 two-byte characters are 520 bytes but 260 characters: accepted, like the sidecar.
        assert!(is_one_component(&"é".repeat(260)));
        assert!(!is_one_component(&"é".repeat(261)));
    }

    #[test]
    fn workbook_names_need_the_right_extension_and_are_not_lock_files() {
        for name in ["a.xlsx", "A.XLSX", "b.xlsm", "c.XlsM", "x y.xlsx"] {
            assert!(is_workbook_name(name), "{name}");
        }
        for name in [
            "a.xls",
            "a.xlsb",
            "a.csv",
            "a",
            "xlsx",
            ".xlsx",
            "~$a.xlsx",
            "a.xlsx.txt",
            "CON.xlsx",
        ] {
            assert!(!is_workbook_name(name), "{name}");
        }
    }

    #[test]
    fn no_name_error_can_hold_text() {
        for error in [
            NameError::NotOneComponent,
            NameError::NotAFile,
            NameError::Escapes,
        ] {
            assert!(!format!("{error}").contains('\\'));
        }
        assert!(std::mem::size_of::<NameError>() <= 1);
    }
}
