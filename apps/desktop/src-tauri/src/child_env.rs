//! The environment the sidecar process is started with (M2 plan section 5, CLAUDE.md 3.4/3.5).
//!
//! The host clears the child's environment (`env_clear()` in M2b) and then adds exactly the
//! variables built here. Nothing is inherited from the parent, including proxy variables, and
//! this function never reads the process environment: it is a pure function of its arguments.
//!
//! | Key                     | Value                                   | Why                                        |
//! |-------------------------|-----------------------------------------|--------------------------------------------|
//! | `SYSTEMROOT`            | the Windows directory (from the OS API) | Winsock fails to start without it          |
//! | `GRC_AUTH_TOKEN`        | the auth token                          | the contract; set only on this child       |
//! | `GRC_LOG_DIR`           | absolute log directory                  | the sidecar's default needs a home dir     |
//! | `GRC_INDEX_DIR`         | absolute index directory                | the sidecar has no home dir to default to  |
//! | `GRC_VAULT_DIR`         | absolute vault folder, if there is one  | the knowledge vault                        |
//! | `GRC_QUESTIONNAIRE_DIR` | absolute questionnaire folder, if set   | where workbooks are read from              |
//! | `GRC_EXPORT_DIR`        | absolute export folder, if set          | where answers files are written            |
//! | `GRC_IMPORT_DIR`        | absolute import folder, if set          | where offline IT exports are read from     |
//! | `GRC_LEDGER_DIR`        | absolute ledger folder, if set          | where the promise ledger lives             |
//!
//! Nine keys (ADR-020 decision 6). The log and index keys are always present; the five user folders
//! are optional and a key is absent when its folder is not set or was not valid (`folders`).
//!
//! Verified against the real sidecar (M2 plan section 1): without `SYSTEMROOT` or `GRC_LOG_DIR`
//! Python exits with a traceback. `GRC_INDEX_DIR` and `GRC_VAULT_DIR` were added in M4b (ADR-009
//! note for M4): the allowlisted environment has no home directory, so without them the sidecar
//! would fall back to a home-based index folder that does not exist there and to no vault at all.
//! A user folder key is optional: with the folder not set the key is absent, and the sidecar
//! reports "not configured" instead of guessing one.
//!
//! As a last check the builder refuses a set of folders that overlap where the sidecar's
//! `Settings` would refuse them (the same rules, `folders::any_overlap`), so a caller bug cannot
//! make the sidecar exit 2 at startup.

use std::collections::BTreeMap;
use std::ffi::{OsStr, OsString};
use std::fmt;
use std::path::Path;

use crate::folders::{any_overlap, UserFolders};
use crate::token::AuthToken;

pub(crate) const ENV_SYSTEM_ROOT: &str = "SYSTEMROOT";
pub(crate) const ENV_AUTH_TOKEN: &str = "GRC_AUTH_TOKEN";
pub(crate) const ENV_LOG_DIR: &str = "GRC_LOG_DIR";
pub(crate) const ENV_INDEX_DIR: &str = "GRC_INDEX_DIR";
pub(crate) const ENV_VAULT_DIR: &str = "GRC_VAULT_DIR";
pub(crate) const ENV_QUESTIONNAIRE_DIR: &str = "GRC_QUESTIONNAIRE_DIR";
pub(crate) const ENV_EXPORT_DIR: &str = "GRC_EXPORT_DIR";
pub(crate) const ENV_IMPORT_DIR: &str = "GRC_IMPORT_DIR";
pub(crate) const ENV_LEDGER_DIR: &str = "GRC_LEDGER_DIR";

/// The complete allowlist. [`build_child_env`] produces keys from this list and no others (a user
/// folder key only when that folder is given). `pub` because tests/supervisor_handshake.rs compares
/// the fake sidecar's reported variable names to it.
pub const ALLOWED_ENV_KEYS: [&str; 9] = [
    ENV_SYSTEM_ROOT,
    ENV_AUTH_TOKEN,
    ENV_LOG_DIR,
    ENV_INDEX_DIR,
    ENV_VAULT_DIR,
    ENV_QUESTIONNAIRE_DIR,
    ENV_EXPORT_DIR,
    ENV_IMPORT_DIR,
    ENV_LEDGER_DIR,
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
    #[error("the questionnaire directory must be an absolute path")]
    QuestionnaireDirNotAbsolute,
    #[error("the export directory must be an absolute path")]
    ExportDirNotAbsolute,
    #[error("the import directory must be an absolute path")]
    ImportDirNotAbsolute,
    #[error("the ledger directory must be an absolute path")]
    LedgerDirNotAbsolute,
    #[error("two directories overlap where they must not")]
    FoldersOverlap,
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

/// Build the child environment from the Windows directory, the log and index directories, the
/// (optional) user folders, and the token.
///
/// Every path must be absolute (the sidecar rejects a relative directory setting, and a relative
/// `SYSTEMROOT` would be resolved against whatever the working directory is), valid Unicode (the
/// sidecar reads them as text) and free of NUL characters. A user folder of `None` leaves its key
/// absent. The index and user folders must not overlap where the sidecar's `Settings` forbids it.
pub fn build_child_env(
    system_root: &Path,
    log_dir: &Path,
    index_dir: &Path,
    folders: &UserFolders<'_>,
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
    let optional = [
        (
            ENV_VAULT_DIR,
            folders.vault,
            ChildEnvError::VaultDirNotAbsolute,
        ),
        (
            ENV_QUESTIONNAIRE_DIR,
            folders.questionnaire,
            ChildEnvError::QuestionnaireDirNotAbsolute,
        ),
        (
            ENV_EXPORT_DIR,
            folders.export,
            ChildEnvError::ExportDirNotAbsolute,
        ),
        (
            ENV_IMPORT_DIR,
            folders.import,
            ChildEnvError::ImportDirNotAbsolute,
        ),
        (
            ENV_LEDGER_DIR,
            folders.ledger,
            ChildEnvError::LedgerDirNotAbsolute,
        ),
    ];
    for (_key, dir, not_absolute) in optional {
        if dir.is_some_and(|dir| !dir.is_absolute()) {
            return Err(not_absolute);
        }
    }
    let system_root = checked_text(system_root)?;
    let log_dir = checked_text(log_dir)?;
    let index_text = checked_text(index_dir)?;
    let mut entries = BTreeMap::new();
    for (key, dir, _not_absolute) in optional {
        if let Some(dir) = dir {
            entries.insert(key, OsString::from(checked_text(dir)?));
        }
    }
    if any_overlap(index_dir, folders) {
        return Err(ChildEnvError::FoldersOverlap);
    }

    entries.insert(ENV_SYSTEM_ROOT, OsString::from(system_root));
    entries.insert(ENV_AUTH_TOKEN, OsString::from(token.expose()));
    entries.insert(ENV_LOG_DIR, OsString::from(log_dir));
    entries.insert(ENV_INDEX_DIR, OsString::from(index_text));
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

    /// The five user folders, owned, so a test can borrow them as `UserFolders`. None overlaps
    /// another or the index folder.
    struct Dirs {
        vault: PathBuf,
        questionnaire: PathBuf,
        export: PathBuf,
        import: PathBuf,
        ledger: PathBuf,
    }

    impl Dirs {
        fn new() -> Self {
            Self {
                vault: PathBuf::from(r"C:\Users\someone\Documents\vault"),
                questionnaire: PathBuf::from(r"C:\Users\someone\Documents\questionnaires"),
                export: PathBuf::from(r"C:\Users\someone\Documents\exports"),
                import: PathBuf::from(r"C:\Users\someone\Documents\imports"),
                ledger: PathBuf::from(r"C:\Users\someone\Documents\ledger"),
            }
        }

        fn folders(&self) -> UserFolders<'_> {
            UserFolders {
                vault: Some(&self.vault),
                questionnaire: Some(&self.questionnaire),
                export: Some(&self.export),
                import: Some(&self.import),
                ledger: Some(&self.ledger),
            }
        }
    }

    const USER_FOLDER_KEYS: [&str; 5] = [
        ENV_VAULT_DIR,
        ENV_QUESTIONNAIRE_DIR,
        ENV_EXPORT_DIR,
        ENV_IMPORT_DIR,
        ENV_LEDGER_DIR,
    ];

    #[test]
    fn child_env_contains_only_allowlist() {
        let (root, logs, token) = sample();
        let dirs = Dirs::new();
        let env = build_child_env(&root, &logs, &index_dir(), &dirs.folders(), &token).unwrap();

        let keys: BTreeSet<&str> = env.keys().collect();
        let allowed: BTreeSet<&str> = ALLOWED_ENV_KEYS.into_iter().collect();
        assert_eq!(
            keys, allowed,
            "the environment must hold exactly the allowlisted keys"
        );
        assert_eq!(env.iter().count(), ALLOWED_ENV_KEYS.len());
    }

    #[test]
    fn with_no_user_folders_only_those_five_keys_are_absent() {
        let (root, logs, token) = sample();
        let env =
            build_child_env(&root, &logs, &index_dir(), &UserFolders::default(), &token).unwrap();

        let keys: BTreeSet<&str> = env.keys().collect();
        let expected: BTreeSet<&str> = ALLOWED_ENV_KEYS
            .into_iter()
            .filter(|key| !USER_FOLDER_KEYS.contains(key))
            .collect();
        assert_eq!(keys, expected);
        assert_eq!(
            keys,
            BTreeSet::from([ENV_SYSTEM_ROOT, ENV_AUTH_TOKEN, ENV_LOG_DIR, ENV_INDEX_DIR])
        );
        for key in USER_FOLDER_KEYS {
            assert_eq!(env.get(key), None, "{key}");
        }
    }

    #[test]
    fn each_user_folder_key_is_present_exactly_when_its_folder_is_given() {
        let (root, logs, token) = sample();
        let dirs = Dirs::new();
        let full = dirs.folders();
        let cases = [
            (
                ENV_VAULT_DIR,
                UserFolders {
                    vault: None,
                    ..full
                },
            ),
            (
                ENV_QUESTIONNAIRE_DIR,
                UserFolders {
                    questionnaire: None,
                    ..full
                },
            ),
            (
                ENV_EXPORT_DIR,
                UserFolders {
                    export: None,
                    ..full
                },
            ),
            (
                ENV_IMPORT_DIR,
                UserFolders {
                    import: None,
                    ..full
                },
            ),
            (
                ENV_LEDGER_DIR,
                UserFolders {
                    ledger: None,
                    ..full
                },
            ),
        ];
        for (missing_key, folders) in cases {
            let env = build_child_env(&root, &logs, &index_dir(), &folders, &token).unwrap();
            let keys: BTreeSet<&str> = env.keys().collect();
            let expected: BTreeSet<&str> = ALLOWED_ENV_KEYS
                .into_iter()
                .filter(|key| *key != missing_key)
                .collect();
            assert_eq!(keys, expected, "without {missing_key}");
        }
    }

    #[test]
    fn child_env_carries_the_token_and_every_path_verbatim() {
        let (root, logs, token) = sample();
        let (index, dirs) = (index_dir(), Dirs::new());
        let env = build_child_env(&root, &logs, &index, &dirs.folders(), &token).unwrap();

        assert_eq!(env.get(ENV_AUTH_TOKEN), Some(OsStr::new(token.expose())));
        assert_eq!(env.get(ENV_SYSTEM_ROOT), Some(root.as_os_str()));
        assert_eq!(env.get(ENV_LOG_DIR), Some(logs.as_os_str()));
        assert_eq!(env.get(ENV_INDEX_DIR), Some(index.as_os_str()));
        assert_eq!(env.get(ENV_VAULT_DIR), Some(dirs.vault.as_os_str()));
        assert_eq!(
            env.get(ENV_QUESTIONNAIRE_DIR),
            Some(dirs.questionnaire.as_os_str())
        );
        assert_eq!(env.get(ENV_EXPORT_DIR), Some(dirs.export.as_os_str()));
        assert_eq!(env.get(ENV_IMPORT_DIR), Some(dirs.import.as_os_str()));
        assert_eq!(env.get(ENV_LEDGER_DIR), Some(dirs.ledger.as_os_str()));
    }

    #[test]
    fn the_token_value_appears_only_under_its_own_key() {
        let (root, logs, token) = sample();
        let dirs = Dirs::new();
        let env = build_child_env(&root, &logs, &index_dir(), &dirs.folders(), &token).unwrap();

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
    fn the_allowlist_is_exactly_these_nine_keys() {
        // A pin: adding or removing a key must change this test, and so be seen in review.
        let allowed: BTreeSet<&str> = ALLOWED_ENV_KEYS.into_iter().collect();
        let expected: BTreeSet<&str> = [
            "SYSTEMROOT",
            "GRC_AUTH_TOKEN",
            "GRC_LOG_DIR",
            "GRC_INDEX_DIR",
            "GRC_VAULT_DIR",
            "GRC_QUESTIONNAIRE_DIR",
            "GRC_EXPORT_DIR",
            "GRC_IMPORT_DIR",
            "GRC_LEDGER_DIR",
        ]
        .into_iter()
        .collect();
        assert_eq!(allowed, expected);
        assert_eq!(ALLOWED_ENV_KEYS.len(), expected.len(), "no duplicate keys");
        assert_eq!(expected.len(), 9);
    }

    #[test]
    fn relative_paths_are_rejected() {
        let (root, logs, token) = sample();
        let (index, dirs) = (index_dir(), Dirs::new());
        let none = UserFolders::default();
        assert_eq!(
            build_child_env(Path::new("Windows"), &logs, &index, &none, &token).unwrap_err(),
            ChildEnvError::SystemRootNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, Path::new(r"logs\sidecar"), &index, &none, &token).unwrap_err(),
            ChildEnvError::LogDirNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, Path::new(""), &index, &none, &token).unwrap_err(),
            ChildEnvError::LogDirNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, &logs, Path::new(r"index"), &none, &token).unwrap_err(),
            ChildEnvError::IndexDirNotAbsolute
        );
        assert_eq!(
            build_child_env(&root, &logs, Path::new(""), &dirs.folders(), &token).unwrap_err(),
            ChildEnvError::IndexDirNotAbsolute
        );
        // Each user folder: a relative and an empty value are refused, each with its own error.
        for relative in [Path::new(r"..\folder"), Path::new(""), Path::new("folder")] {
            let full = dirs.folders();
            let cases = [
                (
                    UserFolders {
                        vault: Some(relative),
                        ..full
                    },
                    ChildEnvError::VaultDirNotAbsolute,
                ),
                (
                    UserFolders {
                        questionnaire: Some(relative),
                        ..full
                    },
                    ChildEnvError::QuestionnaireDirNotAbsolute,
                ),
                (
                    UserFolders {
                        export: Some(relative),
                        ..full
                    },
                    ChildEnvError::ExportDirNotAbsolute,
                ),
                (
                    UserFolders {
                        import: Some(relative),
                        ..full
                    },
                    ChildEnvError::ImportDirNotAbsolute,
                ),
                (
                    UserFolders {
                        ledger: Some(relative),
                        ..full
                    },
                    ChildEnvError::LedgerDirNotAbsolute,
                ),
            ];
            for (folders, expected) in cases {
                assert_eq!(
                    build_child_env(&root, &logs, &index, &folders, &token).unwrap_err(),
                    expected,
                    "{relative:?}"
                );
            }
        }
    }

    #[test]
    fn overlapping_folders_are_refused_where_the_sidecar_would_refuse_them() {
        let (root, logs, token) = sample();
        let (index, dirs) = (index_dir(), Dirs::new());
        let full = dirs.folders();
        // A ledger inside the vault, an import equal to an export, a vault containing the index.
        let ledger_inside_vault = dirs.vault.join("ledger");
        let vault_over_index = PathBuf::from(r"C:\Users\someone\AppData");
        let cases = [
            UserFolders {
                ledger: Some(&ledger_inside_vault),
                ..full
            },
            UserFolders {
                import: Some(&dirs.export),
                ..full
            },
            UserFolders {
                vault: Some(&vault_over_index),
                ..full
            },
            UserFolders {
                questionnaire: Some(&index),
                ..UserFolders::default()
            },
        ];
        for folders in cases {
            assert_eq!(
                build_child_env(&root, &logs, &index, &folders, &token).unwrap_err(),
                ChildEnvError::FoldersOverlap,
                "{folders:?}"
            );
        }
        // The one pair the sidecar allows to overlap: questionnaire and export.
        let export_inside_questionnaire = dirs.questionnaire.join("answers");
        let allowed = UserFolders {
            export: Some(&export_inside_questionnaire),
            ..full
        };
        assert!(build_child_env(&root, &logs, &index, &allowed, &token).is_ok());
    }

    #[test]
    fn a_nul_character_in_a_path_is_rejected() {
        let (root, logs, token) = sample();
        let (index, dirs) = (index_dir(), Dirs::new());
        let none = UserFolders::default();
        let bad = PathBuf::from("C:\\logs\0evil");
        assert_eq!(
            build_child_env(&root, &bad, &index, &none, &token).unwrap_err(),
            ChildEnvError::PathContainsNul
        );
        let bad_root = PathBuf::from("C:\\Windows\0");
        assert_eq!(
            build_child_env(&bad_root, &logs, &index, &none, &token).unwrap_err(),
            ChildEnvError::PathContainsNul
        );
        let bad_index = PathBuf::from("C:\\index\0evil");
        assert_eq!(
            build_child_env(&root, &logs, &bad_index, &dirs.folders(), &token).unwrap_err(),
            ChildEnvError::PathContainsNul
        );
        let bad_folder = PathBuf::from("C:\\folder\0evil");
        let full = dirs.folders();
        for folders in [
            UserFolders {
                vault: Some(&bad_folder),
                ..full
            },
            UserFolders {
                questionnaire: Some(&bad_folder),
                ..full
            },
            UserFolders {
                export: Some(&bad_folder),
                ..full
            },
            UserFolders {
                import: Some(&bad_folder),
                ..full
            },
            UserFolders {
                ledger: Some(&bad_folder),
                ..full
            },
        ] {
            assert_eq!(
                build_child_env(&root, &logs, &index, &folders, &token).unwrap_err(),
                ChildEnvError::PathContainsNul
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn a_path_that_is_not_unicode_is_rejected() {
        use std::os::windows::ffi::OsStringExt;
        let (root, logs, token) = sample();
        let none = UserFolders::default();
        // 0xD800 is an unpaired UTF-16 surrogate: legal in a Windows path, not in UTF-8 text.
        let wide: Vec<u16> = "C:\\logs\\".encode_utf16().chain([0xD800]).collect();
        let bad = PathBuf::from(OsString::from_wide(&wide));
        assert_eq!(
            build_child_env(&root, &bad, &index_dir(), &none, &token).unwrap_err(),
            ChildEnvError::PathNotUnicode
        );
        assert_eq!(
            build_child_env(&root, &logs, &bad, &none, &token).unwrap_err(),
            ChildEnvError::PathNotUnicode
        );
        let dirs = Dirs::new();
        let full = dirs.folders();
        for folders in [
            UserFolders {
                vault: Some(&bad),
                ..full
            },
            UserFolders {
                questionnaire: Some(&bad),
                ..full
            },
            UserFolders {
                export: Some(&bad),
                ..full
            },
            UserFolders {
                import: Some(&bad),
                ..full
            },
            UserFolders {
                ledger: Some(&bad),
                ..full
            },
        ] {
            assert_eq!(
                build_child_env(&root, &logs, &index_dir(), &folders, &token).unwrap_err(),
                ChildEnvError::PathNotUnicode
            );
        }
    }

    #[test]
    fn debug_output_lists_keys_but_never_values() {
        let (root, logs, token) = sample();
        let secret = token.expose().to_owned();
        let dirs = Dirs::new();
        let env = build_child_env(&root, &logs, &index_dir(), &dirs.folders(), &token).unwrap();

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
        let questionnaire = PathBuf::from("C:\\q\\HTTPS_PROXY=http://127.0.0.1:9\\%PATH%");
        let export = PathBuf::from("C:\\e\\GRC_LEDGER_DIR=C:\\evil\\\"quoted\"");
        let import = PathBuf::from("C:\\i\\new\nline\\;C:\\evil");
        let ledger = PathBuf::from("C:\\l\\$(whoami)\\SYSTEMROOT=C:\\evil");
        let folders = UserFolders {
            vault: Some(&vault),
            questionnaire: Some(&questionnaire),
            export: Some(&export),
            import: Some(&import),
            ledger: Some(&ledger),
        };
        let token = AuthToken::generate().unwrap();

        let env = build_child_env(&root, &logs, &index, &folders, &token).unwrap();

        let keys: BTreeSet<&str> = env.keys().collect();
        let allowed: BTreeSet<&str> = ALLOWED_ENV_KEYS.into_iter().collect();
        assert_eq!(keys, allowed, "hostile values must not add or rename keys");
        assert_eq!(env.iter().count(), ALLOWED_ENV_KEYS.len());
        assert_eq!(env.get(ENV_SYSTEM_ROOT), Some(root.as_os_str()));
        assert_eq!(env.get(ENV_LOG_DIR), Some(logs.as_os_str()));
        assert_eq!(env.get(ENV_INDEX_DIR), Some(index.as_os_str()));
        assert_eq!(env.get(ENV_VAULT_DIR), Some(vault.as_os_str()));
        assert_eq!(
            env.get(ENV_QUESTIONNAIRE_DIR),
            Some(questionnaire.as_os_str())
        );
        assert_eq!(env.get(ENV_EXPORT_DIR), Some(export.as_os_str()));
        assert_eq!(env.get(ENV_IMPORT_DIR), Some(import.as_os_str()));
        assert_eq!(env.get(ENV_LEDGER_DIR), Some(ledger.as_os_str()));
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
                | ChildEnvError::QuestionnaireDirNotAbsolute
                | ChildEnvError::ExportDirNotAbsolute
                | ChildEnvError::ImportDirNotAbsolute
                | ChildEnvError::LedgerDirNotAbsolute
                | ChildEnvError::FoldersOverlap
                | ChildEnvError::PathNotUnicode
                | ChildEnvError::PathContainsNul => {}
            }
        }
        let all = vec![
            ChildEnvError::SystemRootNotAbsolute,
            ChildEnvError::LogDirNotAbsolute,
            ChildEnvError::IndexDirNotAbsolute,
            ChildEnvError::VaultDirNotAbsolute,
            ChildEnvError::QuestionnaireDirNotAbsolute,
            ChildEnvError::ExportDirNotAbsolute,
            ChildEnvError::ImportDirNotAbsolute,
            ChildEnvError::LedgerDirNotAbsolute,
            ChildEnvError::FoldersOverlap,
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
