//! The saved-folders file (ADR-020 decision 5): one small JSON file in the app config directory.
//!
//! It holds the five folders the user chose, as text, and nothing else:
//! `{"vault":"C:\\...","questionnaires":"...","exports":"...","imports":"...","ledger":"..."}`.
//! Every key is optional. The host is the only writer; the window never submits a path.
//!
//! The file is an input, not a trusted record (a same-user process can edit it, threat O1), so it
//! is read strictly: at most [`MAX_SAVED_BYTES`] bytes, valid JSON, only these keys, only strings
//! or `null`. Anything else is [`StoreError::Malformed`] and the whole file is ignored. Whether a
//! saved text is a usable folder is decided later, on every start (`folders::resolve`), not here.
//!
//! Writing uses only the standard library: the new content goes to a sibling temporary file, is
//! flushed to disk, and then `fs::rename` puts it in place. On Windows `rename` replaces an existing
//! file, so a reader sees the old file or the new one, never half of one.

use std::fs;
use std::io::{ErrorKind, Read, Write};
use std::path::Path;

use serde::{Deserialize, Serialize};

use super::FolderKind;

/// The largest saved file the host will read. Five paths of at most 32,767 UTF-16 units each could
/// not fit, but no real folder is that long; 16 KiB is far more than any plausible saved file.
pub const MAX_SAVED_BYTES: u64 = 16 * 1024;

/// Why the file could not be read or written. Unit variants: nothing here echoes a path or text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum StoreError {
    #[error("the saved folders file is over its size limit")]
    TooLarge,
    #[error("the saved folders file could not be read")]
    Unreadable,
    #[error("the saved folders file is not in the expected shape")]
    Malformed,
    #[error("the saved folders file could not be written")]
    WriteFailed,
}

/// The five saved folders, exactly as written in the file. Unknown keys and wrong types fail the
/// parse (`deny_unknown_fields`, no lenient conversion).
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SavedFolders {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    vault: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    questionnaires: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exports: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    imports: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    ledger: Option<String>,
}

impl SavedFolders {
    pub fn get(&self, kind: FolderKind) -> Option<&str> {
        match kind {
            FolderKind::Vault => self.vault.as_deref(),
            FolderKind::Questionnaires => self.questionnaires.as_deref(),
            FolderKind::Exports => self.exports.as_deref(),
            FolderKind::Imports => self.imports.as_deref(),
            FolderKind::Ledger => self.ledger.as_deref(),
        }
    }

    /// A copy with one folder replaced.
    pub fn with(&self, kind: FolderKind, value: Option<String>) -> Self {
        let mut copy = self.clone();
        match kind {
            FolderKind::Vault => copy.vault = value,
            FolderKind::Questionnaires => copy.questionnaires = value,
            FolderKind::Exports => copy.exports = value,
            FolderKind::Imports => copy.imports = value,
            FolderKind::Ledger => copy.ledger = value,
        }
        copy
    }
}

/// Read and parse the saved file. A file that does not exist is not an error (nothing saved yet).
pub fn read_saved(path: &Path) -> Result<SavedFolders, StoreError> {
    let file = match fs::File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(SavedFolders::default()),
        Err(_) => return Err(StoreError::Unreadable),
    };
    let metadata = file.metadata().map_err(|_| StoreError::Unreadable)?;
    if !metadata.is_file() {
        return Err(StoreError::Unreadable);
    }
    if metadata.len() > MAX_SAVED_BYTES {
        return Err(StoreError::TooLarge);
    }
    // The size above can be out of date by the time of the read (the file may grow), so the read
    // is capped too: one byte more than the limit is enough to know it is over.
    let mut bytes = Vec::new();
    file.take(MAX_SAVED_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| StoreError::Unreadable)?;
    if bytes.len() as u64 > MAX_SAVED_BYTES {
        return Err(StoreError::TooLarge);
    }
    parse(&bytes)
}

/// Parse the file's bytes. The first non-space byte must be `{`: serde fills a struct from a JSON
/// ARRAY as well (`["C:\\v"]` would become the vault), which is not the file's shape. A repeated
/// key is refused by serde itself.
fn parse(bytes: &[u8]) -> Result<SavedFolders, StoreError> {
    let first = bytes.iter().find(|byte| !byte.is_ascii_whitespace());
    if first != Some(&b'{') {
        return Err(StoreError::Malformed);
    }
    serde_json::from_slice(bytes).map_err(|_| StoreError::Malformed)
}

/// Replace the saved file with `saved`: write a sibling temporary file, flush it, rename it over the
/// real one. The parent directory is created if missing.
pub fn write_saved(path: &Path, saved: &SavedFolders) -> Result<(), StoreError> {
    let mut text = serde_json::to_string_pretty(saved).map_err(|_| StoreError::WriteFailed)?;
    text.push('\n');
    if text.len() as u64 > MAX_SAVED_BYTES {
        // Never write a file the reader would refuse.
        return Err(StoreError::TooLarge);
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|_| StoreError::WriteFailed)?;
    }
    let temporary = path.with_extension("json.tmp");
    let written =
        write_and_flush(&temporary, text.as_bytes()).and_then(|()| fs::rename(&temporary, path));
    if written.is_err() {
        // Best effort: a leftover temporary file must not be mistaken for the saved file, and is
        // overwritten by the next write anyway.
        let _ = fs::remove_file(&temporary);
        return Err(StoreError::WriteFailed);
    }
    Ok(())
}

fn write_and_flush(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let mut file = fs::File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(text: &str) -> Result<SavedFolders, StoreError> {
        super::parse(text.as_bytes())
    }

    #[test]
    fn an_empty_object_and_missing_keys_mean_nothing_saved() {
        assert_eq!(parse("{}").unwrap(), SavedFolders::default());
        let one = parse(r#"{"vault":"C:\\v"}"#).unwrap();
        assert_eq!(one.get(FolderKind::Vault), Some(r"C:\v"));
        assert_eq!(one.get(FolderKind::Ledger), None);
        assert_eq!(parse(r#"{"vault":null}"#).unwrap(), SavedFolders::default());
    }

    #[test]
    fn unknown_keys_and_wrong_types_are_refused() {
        for text in [
            r#"{"vault":"C:\\v","extra":"x"}"#,
            r#"{"index":"C:\\i"}"#,
            r#"{"log":"C:\\l"}"#,
            r#"{"vault":5}"#,
            r#"{"vault":["C:\\v"]}"#,
            r#"{"vault":{"path":"C:\\v"}}"#,
            r#"{"vault":true}"#,
            r#"["C:\\v"]"#,
            r#""C:\\v""#,
            "null",
            "not json",
            "",
            r#"{"vault":"C:\\v"}{"vault":"C:\\w"}"#,
            // a repeated key: the later one must not silently win
            r#"{"vault":"C:\\a","vault":"C:\\b"}"#,
            r#" ["C:\\v"]"#,
        ] {
            assert!(parse(text).is_err(), "{text:?} must be refused");
        }
    }

    #[test]
    fn with_replaces_one_folder_and_leaves_the_others() {
        let base = parse(r#"{"vault":"C:\\v","ledger":"C:\\l"}"#).unwrap();
        let next = base.with(FolderKind::Ledger, Some(r"C:\m".to_owned()));
        assert_eq!(next.get(FolderKind::Vault), Some(r"C:\v"));
        assert_eq!(next.get(FolderKind::Ledger), Some(r"C:\m"));
        assert_eq!(base.get(FolderKind::Ledger), Some(r"C:\l"));
        assert_eq!(
            next.with(FolderKind::Vault, None).get(FolderKind::Vault),
            None
        );
    }

    #[test]
    fn serialized_keys_are_exactly_the_kind_names() {
        let mut saved = SavedFolders::default();
        for kind in FolderKind::ALL {
            saved = saved.with(kind, Some(r"C:\x".to_owned()));
        }
        let value = serde_json::to_value(&saved).unwrap();
        let mut keys: Vec<&str> = value
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            ["exports", "imports", "ledger", "questionnaires", "vault"]
        );
    }

    #[test]
    fn no_store_error_can_hold_text() {
        for error in [
            StoreError::TooLarge,
            StoreError::Unreadable,
            StoreError::Malformed,
            StoreError::WriteFailed,
        ] {
            assert!(!format!("{error}").contains("C:\\"));
        }
        assert!(std::mem::size_of::<StoreError>() <= 1);
    }
}
