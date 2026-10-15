//! The user's folders, owned by the host (M6 chunk 6e, ADR-020 decisions 5 to 8, backlog G9).
//!
//! * [`store`]: the saved-folders file, read strictly.
//! * [`files`]: file NAMES from the window, checked before a request is forwarded.
//! * this file: the five [`FolderKind`]s, the overlap rules, [`resolve`] (what is passed to the
//!   sidecar), and [`FolderSettings`] (what the commands `folders_get`, `folder_pick` and
//!   `folder_list_files` answer).
//!
//! The rules, in one place:
//!
//! 1. The window names a KIND of folder (an enum), never a path. A picked folder reaches the host
//!    through the native dialog, which only Rust calls, and what comes back to the window is a
//!    display string and a typed reason.
//! 2. A folder is valid when it is on a drive letter, exists, is a directory, and does not overlap
//!    another (equal, inside or containing) where the sidecar's `Settings` forbids it. The rules
//!    here are the same pairs as `sidecar/src/grc_sidecar/config.py`, checked with `starts_with`
//!    on canonical paths: vault, index, questionnaire, export, import and ledger may not overlap
//!    each other, EXCEPT that the questionnaire and export folders may (the sidecar only checks
//!    each against the vault and the index).
//! 3. Validity is checked when a folder is set and again at EVERY start. An invalid saved folder is
//!    not passed (its key is absent, so the sidecar says "not configured") and a typed reason is
//!    kept for the screen.
//! 4. The index and log folders are the host's own choice and are never saved.
//! 5. A change applies at the next launch: the sidecar reads its settings once.

pub mod files;
pub mod store;

use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock, PoisonError};

use serde::{Deserialize, Serialize};

pub use self::store::{SavedFolders, StoreError};
use crate::dev_paths::existing_directory;

/// Which folder. The only thing about a folder the window may name.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FolderKind {
    Vault,
    Questionnaires,
    Exports,
    Imports,
    Ledger,
}

impl FolderKind {
    /// In the order folders are accepted: an earlier folder is never unseated by a later one.
    pub const ALL: [FolderKind; 5] = [
        FolderKind::Vault,
        FolderKind::Questionnaires,
        FolderKind::Exports,
        FolderKind::Imports,
        FolderKind::Ledger,
    ];

    fn index(self) -> usize {
        match self {
            FolderKind::Vault => 0,
            FolderKind::Questionnaires => 1,
            FolderKind::Exports => 2,
            FolderKind::Imports => 3,
            FolderKind::Ledger => 4,
        }
    }
}

/// The user folders as borrowed absolute paths, for the child environment. `None` means "not set",
/// and the matching key is absent.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct UserFolders<'a> {
    pub vault: Option<&'a Path>,
    pub questionnaire: Option<&'a Path>,
    pub export: Option<&'a Path>,
    pub import: Option<&'a Path>,
    pub ledger: Option<&'a Path>,
}

impl<'a> UserFolders<'a> {
    pub fn get(&self, kind: FolderKind) -> Option<&'a Path> {
        match kind {
            FolderKind::Vault => self.vault,
            FolderKind::Questionnaires => self.questionnaire,
            FolderKind::Exports => self.export,
            FolderKind::Imports => self.import,
            FolderKind::Ledger => self.ledger,
        }
    }
}

/// Where a folder sits in the overlap rules: the host's index folder or one of the user's.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Role {
    Index,
    Folder(FolderKind),
}

/// Whether the sidecar's `Settings` forbids these two roles to overlap. Everything is forbidden
/// except a folder with itself and the questionnaire and export folders with each other.
fn rule_applies(a: Role, b: Role) -> bool {
    let questionnaire_and_export = matches!(
        (a, b),
        (
            Role::Folder(FolderKind::Questionnaires),
            Role::Folder(FolderKind::Exports)
        ) | (
            Role::Folder(FolderKind::Exports),
            Role::Folder(FolderKind::Questionnaires)
        )
    );
    a != b && !questionnaire_and_export
}

/// Equal, inside or containing. Both paths are expected to be canonical, so `starts_with` compares
/// real folders; on a drive letter path it works on whole components, never on text prefixes
/// (`C:\vault2` is not inside `C:\vault`).
fn overlaps(a: &Path, b: &Path) -> bool {
    a.starts_with(b) || b.starts_with(a)
}

/// Whether any pair of folders that the rules keep apart overlaps. Used by the environment builder
/// as a last check on what it is about to hand the sidecar.
pub fn any_overlap(index_dir: &Path, folders: &UserFolders<'_>) -> bool {
    let mut roles = vec![(Role::Index, index_dir)];
    for kind in FolderKind::ALL {
        if let Some(path) = folders.get(kind) {
            roles.push((Role::Folder(kind), path));
        }
    }
    roles.iter().enumerate().any(|(position, (role, path))| {
        roles[position + 1..]
            .iter()
            .any(|(other_role, other)| rule_applies(*role, *other_role) && overlaps(path, other))
    })
}

/// Why a saved folder is not used. Unit variants: a reason is a word from a fixed list, never text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FolderProblem {
    /// Missing, not a directory, not on a drive letter (UNC and device paths) or not openable.
    Unavailable,
    /// The saved text is a relative path.
    NotAbsolute,
    /// It is equal to, inside or containing another folder the rules keep apart.
    Overlap,
}

/// Why a command could not answer. Unit variants, serialized `{"error":"unavailable"}`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, thiserror::Error)]
#[serde(tag = "error", rename_all = "snake_case")]
pub enum FolderError {
    #[error("the folder is not available")]
    Unavailable,
    #[error("the folder overlaps another folder")]
    Overlap,
    #[error("that folder is not set for this run")]
    NotConfigured,
    #[error("that kind of folder has no file list")]
    NotListable,
    #[error("the choice could not be saved")]
    SaveFailed,
}

impl From<FolderProblem> for FolderError {
    fn from(problem: FolderProblem) -> Self {
        match problem {
            FolderProblem::Unavailable | FolderProblem::NotAbsolute => FolderError::Unavailable,
            FolderProblem::Overlap => FolderError::Overlap,
        }
    }
}

/// The folders to pass to the sidecar, and the typed reason for each one that was not passed.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Resolved {
    in_use: [Option<PathBuf>; 5],
    problems: [Option<FolderProblem>; 5],
}

impl Resolved {
    pub fn get(&self, kind: FolderKind) -> Option<&Path> {
        self.in_use[kind.index()].as_deref()
    }

    pub fn problem(&self, kind: FolderKind) -> Option<FolderProblem> {
        self.problems[kind.index()]
    }

    /// The valid folders, ready for `SidecarDirs`.
    pub fn user_folders(&self) -> UserFolders<'_> {
        UserFolders {
            vault: self.get(FolderKind::Vault),
            questionnaire: self.get(FolderKind::Questionnaires),
            export: self.get(FolderKind::Exports),
            import: self.get(FolderKind::Imports),
            ledger: self.get(FolderKind::Ledger),
        }
    }
}

fn check_saved(text: &str) -> Result<PathBuf, FolderProblem> {
    let path = PathBuf::from(text);
    if !path.is_absolute() {
        return Err(FolderProblem::NotAbsolute);
    }
    // Refuses a UNC or device path before opening it, and a missing or non-directory path.
    existing_directory(path).ok_or(FolderProblem::Unavailable)
}

/// Decide which saved folders are valid right now.
///
/// Each folder is checked in [`FolderKind::ALL`] order: canonical, a directory, and not overlapping
/// the folders ALREADY accepted (the host's index folder first). A folder that fails is absent from
/// the result with its reason; an earlier folder is never removed because of a later one.
///
/// `dev_vault` is the debug-only vault of the host's environment (`dev_paths`). It is used ONLY
/// when no vault is saved at all: a saved vault wins, and a saved vault that is invalid is not
/// replaced by it. The caller passes `None` in a release build. It goes through the same overlap
/// check as a saved vault.
///
/// `index_dir` is the host's own index folder (`None` only if the host could not choose one).
pub fn resolve(
    saved: &SavedFolders,
    index_dir: Option<&Path>,
    dev_vault: Option<PathBuf>,
) -> Resolved {
    let mut accepted: Vec<(Role, PathBuf)> = Vec::new();
    if let Some(index_dir) = index_dir {
        // The index folder is created by the host before this runs; if it cannot be opened the
        // rules still apply to the path as written.
        let index =
            existing_directory(index_dir.to_owned()).unwrap_or_else(|| index_dir.to_owned());
        accepted.push((Role::Index, index));
    }
    let mut resolved = Resolved::default();
    for kind in FolderKind::ALL {
        let checked = match saved.get(kind) {
            Some(text) => check_saved(text),
            None if kind == FolderKind::Vault => match dev_vault.clone() {
                Some(path) => Ok(path),
                None => continue,
            },
            None => continue,
        };
        let outcome = checked.and_then(|path| {
            let clash = accepted.iter().any(|(role, other)| {
                rule_applies(Role::Folder(kind), *role) && overlaps(&path, other)
            });
            if clash {
                Err(FolderProblem::Overlap)
            } else {
                Ok(path)
            }
        });
        match outcome {
            Ok(path) => {
                accepted.push((Role::Folder(kind), path.clone()));
                resolved.in_use[kind.index()] = Some(path);
            }
            Err(problem) => resolved.problems[kind.index()] = Some(problem),
        }
    }
    resolved
}

fn display(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

/// Whether the saved file could be read at the last look. Not found counts as fine (nothing saved).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SavedFileState {
    Ok,
    /// Unreadable, over its size limit, or not the expected shape: ignored as a whole.
    Invalid,
}

/// One folder as the window sees it. Display strings and words only; nothing here is accepted back
/// as authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FolderView {
    pub kind: FolderKind,
    /// The folder the sidecar was started with (the "In use now" column), or `None`.
    pub in_use: Option<String>,
    /// What is saved for the next launch (the "Saved for next launch" column), or `None`.
    pub saved: Option<String>,
    /// Why the saved folder would not be used if ALIBI started now.
    pub problem: Option<FolderProblem>,
    /// Why the saved folder was not passed at this start.
    pub start_problem: Option<FolderProblem>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FoldersReply {
    /// False until the host has decided the folders for this run (it does so as the sidecar starts).
    pub ready: bool,
    /// The saved file as it is now.
    pub saved_file: SavedFileState,
    /// The saved file as it was when this run started (`None` until `ready`). `invalid` here means
    /// every saved folder was ignored at this start.
    pub saved_file_at_start: Option<SavedFileState>,
    pub folders: Vec<FolderView>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum PickReply {
    Saved { kind: FolderKind, display: String },
    Cancelled,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FileList {
    pub kind: FolderKind,
    pub files: Vec<String>,
    pub truncated: bool,
}

#[derive(Debug, Clone)]
struct Startup {
    resolved: Resolved,
    saved_file: SavedFileState,
}

/// What the Tauri app keeps for the folder commands. The only mutable thing is the saved file; the
/// folders "in use" are decided once, when the sidecar starts, and never change after (there is no
/// restart path).
pub struct FolderSettings {
    config_file: Option<PathBuf>,
    index_dir: Option<PathBuf>,
    startup: OnceLock<Startup>,
    /// One writer at a time, so two picks cannot interleave their read-check-write.
    write_lock: Mutex<()>,
}

impl FolderSettings {
    /// `config_file` is the saved file's path (`None` if the host has no config directory; nothing
    /// can then be saved). `index_dir` is the host's index folder.
    pub fn new(config_file: Option<PathBuf>, index_dir: Option<PathBuf>) -> Self {
        Self {
            config_file,
            index_dir,
            startup: OnceLock::new(),
            write_lock: Mutex::new(()),
        }
    }

    fn load(&self) -> (SavedFolders, SavedFileState) {
        let Some(file) = &self.config_file else {
            return (SavedFolders::default(), SavedFileState::Ok);
        };
        match store::read_saved(file) {
            Ok(saved) => (saved, SavedFileState::Ok),
            Err(_) => (SavedFolders::default(), SavedFileState::Invalid),
        }
    }

    /// Decide the folders for this run: read the saved file, validate every folder, remember the
    /// outcome for `folders_get`, and return what to pass to the sidecar. Called once per run.
    pub fn startup(&self, dev_vault: Option<PathBuf>) -> Resolved {
        let (saved, saved_file) = self.load();
        let resolved = resolve(&saved, self.index_dir.as_deref(), dev_vault);
        // A second call keeps the first outcome: the sidecar was started with that one.
        let _ = self.startup.set(Startup {
            resolved: resolved.clone(),
            saved_file,
        });
        resolved
    }

    /// Both columns for every folder, read fresh from the saved file.
    pub fn view(&self) -> FoldersReply {
        let (saved, saved_file) = self.load();
        let now = resolve(&saved, self.index_dir.as_deref(), None);
        let started = self.startup.get();
        let folders = FolderKind::ALL
            .iter()
            .map(|&kind| FolderView {
                kind,
                in_use: started
                    .and_then(|start| start.resolved.get(kind))
                    .map(display),
                saved: saved
                    .get(kind)
                    .map(|text| now.get(kind).map_or_else(|| text.to_owned(), display)),
                problem: now.problem(kind),
                start_problem: started.and_then(|start| start.resolved.problem(kind)),
            })
            .collect();
        FoldersReply {
            ready: started.is_some(),
            saved_file,
            saved_file_at_start: started.map(|start| start.saved_file),
            folders,
        }
    }

    /// Validate a folder chosen in the native dialog and save it for the next launch.
    ///
    /// The folder must be valid on its own AND must not make any other saved folder invalid that
    /// was valid before (the new one is checked against the others, and the others against it).
    /// Nothing is written unless both hold. If the saved file itself is invalid it is replaced by
    /// a file holding only this folder.
    pub fn save_picked(&self, kind: FolderKind, picked: PathBuf) -> Result<PickReply, FolderError> {
        let file = self.config_file.as_ref().ok_or(FolderError::SaveFailed)?;
        let canonical = existing_directory(picked).ok_or(FolderError::Unavailable)?;
        let _writer = self
            .write_lock
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let (saved, _) = self.load();
        let before = resolve(&saved, self.index_dir.as_deref(), None);
        let candidate = saved.with(kind, Some(display(&canonical)));
        let after = resolve(&candidate, self.index_dir.as_deref(), None);
        if let Some(problem) = after.problem(kind) {
            return Err(problem.into());
        }
        let breaks_another = FolderKind::ALL.iter().any(|&other| {
            other != kind && after.problem(other).is_some() && before.problem(other).is_none()
        });
        if breaks_another {
            return Err(FolderError::Overlap);
        }
        store::write_saved(file, &candidate).map_err(|_| FolderError::SaveFailed)?;
        Ok(PickReply::Saved {
            kind,
            display: display(&canonical),
        })
    }

    /// The workbook names in the folder that is IN USE for this run (the one the sidecar can reach).
    pub fn list_files(&self, kind: FolderKind) -> Result<FileList, FolderError> {
        if !matches!(kind, FolderKind::Questionnaires | FolderKind::Exports) {
            return Err(FolderError::NotListable);
        }
        let root = self
            .startup
            .get()
            .and_then(|start| start.resolved.get(kind))
            .ok_or(FolderError::NotConfigured)?;
        let listing = files::list_workbooks(root).ok_or(FolderError::Unavailable)?;
        Ok(FileList {
            kind,
            files: listing.files,
            truncated: listing.truncated,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(text: &str) -> &Path {
        Path::new(text)
    }

    #[test]
    fn the_rules_forbid_every_pair_except_questionnaire_with_export() {
        use FolderKind::{Exports, Imports, Ledger, Questionnaires, Vault};
        let roles = [
            Role::Index,
            Role::Folder(Vault),
            Role::Folder(Questionnaires),
            Role::Folder(Exports),
            Role::Folder(Imports),
            Role::Folder(Ledger),
        ];
        for a in roles {
            for b in roles {
                let expected = a != b
                    && !matches!(
                        (a, b),
                        (Role::Folder(Questionnaires), Role::Folder(Exports))
                            | (Role::Folder(Exports), Role::Folder(Questionnaires))
                    );
                assert_eq!(rule_applies(a, b), expected, "{a:?} {b:?}");
                assert_eq!(
                    rule_applies(a, b),
                    rule_applies(b, a),
                    "symmetry {a:?} {b:?}"
                );
            }
        }
    }

    #[test]
    fn overlap_is_equal_inside_or_containing_on_whole_components() {
        assert!(overlaps(p(r"C:\a"), p(r"C:\a")));
        assert!(overlaps(p(r"C:\a\b"), p(r"C:\a")));
        assert!(overlaps(p(r"C:\a"), p(r"C:\a\b")));
        assert!(overlaps(p(r"C:\a\b\c"), p(r"C:\a")));
        assert!(!overlaps(p(r"C:\a"), p(r"C:\b")));
        assert!(!overlaps(p(r"C:\vault2"), p(r"C:\vault")));
        assert!(!overlaps(p(r"C:\vault"), p(r"C:\vault2")));
        assert!(!overlaps(p(r"C:\a"), p(r"D:\a")));
    }

    #[test]
    fn any_overlap_finds_a_forbidden_pair_and_allows_questionnaire_with_export() {
        let index = p(r"C:\app\index");
        let clean = UserFolders {
            vault: Some(p(r"C:\v")),
            questionnaire: Some(p(r"C:\q")),
            export: Some(p(r"C:\e")),
            import: Some(p(r"C:\i")),
            ledger: Some(p(r"C:\l")),
        };
        assert!(!any_overlap(index, &clean));
        assert!(!any_overlap(index, &UserFolders::default()));
        // The one allowed overlap.
        assert!(!any_overlap(
            index,
            &UserFolders {
                questionnaire: Some(p(r"C:\both")),
                export: Some(p(r"C:\both\sub")),
                ..UserFolders::default()
            }
        ));
        let broken = [
            UserFolders {
                vault: Some(p(r"C:\app")),
                ..clean
            },
            UserFolders {
                vault: Some(p(r"C:\app\index\v")),
                ..clean
            },
            UserFolders {
                questionnaire: Some(p(r"C:\v\q")),
                ..clean
            },
            UserFolders {
                export: Some(p(r"C:\v")),
                ..clean
            },
            UserFolders {
                import: Some(p(r"C:\q")),
                ..clean
            },
            UserFolders {
                import: Some(p(r"C:\e\i")),
                ..clean
            },
            UserFolders {
                ledger: Some(p(r"C:\i")),
                ..clean
            },
            UserFolders {
                ledger: Some(p(r"C:\app\index")),
                ..clean
            },
            UserFolders {
                ledger: Some(p(r"C:\")),
                ..clean
            },
        ];
        for folders in broken {
            assert!(any_overlap(index, &folders), "{folders:?}");
        }
    }

    #[test]
    fn a_relative_saved_text_is_not_absolute_and_a_unc_text_is_unavailable() {
        let relative = [r"vault", r"..\vault", r".\vault", r"\vault", "C:vault", ""];
        for text in relative {
            assert_eq!(
                check_saved(text),
                Err(FolderProblem::NotAbsolute),
                "{text:?}"
            );
        }
        for text in [r"\\server\share", r"\\?\UNC\server\share", r"\\.\NUL"] {
            assert_eq!(
                check_saved(text),
                Err(FolderProblem::Unavailable),
                "{text:?}"
            );
        }
        assert_eq!(
            check_saved("C:\\x\0y"),
            Err(FolderProblem::Unavailable),
            "NUL"
        );
    }

    #[test]
    fn replies_carry_display_strings_and_words_never_a_path_field() {
        let reply = FoldersReply {
            ready: true,
            saved_file: SavedFileState::Invalid,
            saved_file_at_start: Some(SavedFileState::Ok),
            folders: vec![FolderView {
                kind: FolderKind::Ledger,
                in_use: Some(r"C:\l".to_owned()),
                saved: Some(r"C:\m".to_owned()),
                problem: Some(FolderProblem::Overlap),
                start_problem: Some(FolderProblem::NotAbsolute),
            }],
        };
        let json = serde_json::to_string(&reply).unwrap();
        assert_eq!(
            json,
            r#"{"ready":true,"saved_file":"invalid","saved_file_at_start":"ok","folders":[{"kind":"ledger","in_use":"C:\\l","saved":"C:\\m","problem":"overlap","start_problem":"not_absolute"}]}"#
        );
        let pick = serde_json::to_string(&PickReply::Saved {
            kind: FolderKind::Vault,
            display: r"C:\v".to_owned(),
        })
        .unwrap();
        assert_eq!(
            pick,
            r#"{"outcome":"saved","kind":"vault","display":"C:\\v"}"#
        );
        assert_eq!(
            serde_json::to_string(&PickReply::Cancelled).unwrap(),
            r#"{"outcome":"cancelled"}"#
        );
        for text in [json, pick] {
            assert!(!text.contains("\"path\""), "{text}");
        }
    }

    #[test]
    fn folder_errors_are_words_and_the_kind_is_a_closed_set() {
        for (error, word) in [
            (FolderError::Unavailable, "unavailable"),
            (FolderError::Overlap, "overlap"),
            (FolderError::NotConfigured, "not_configured"),
            (FolderError::NotListable, "not_listable"),
            (FolderError::SaveFailed, "save_failed"),
        ] {
            assert_eq!(
                serde_json::to_string(&error).unwrap(),
                format!(r#"{{"error":"{word}"}}"#)
            );
        }
        assert!(std::mem::size_of::<FolderError>() <= 1);
        for text in ["vault", "questionnaires", "exports", "imports", "ledger"] {
            assert!(serde_json::from_str::<FolderKind>(&format!("\"{text}\"")).is_ok());
        }
        for text in [
            "\"index\"",
            "\"log\"",
            "\"Vault\"",
            "\"C:\\\\x\"",
            "\"\"",
            "5",
        ] {
            assert!(serde_json::from_str::<FolderKind>(text).is_err(), "{text}");
        }
    }
}
