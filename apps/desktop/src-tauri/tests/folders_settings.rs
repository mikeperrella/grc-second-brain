//! The saved-folders file, the overlap rules, the start-time re-validation and the picked-folder
//! save, against real folders on disk (chunk 6e, ADR-020 decisions 5 to 7, threat F35-1, F35-2,
//! F35-5). Everything is synthetic and lives under the per-package scratch directory.

mod common;

use std::fs;
use std::path::{Path, PathBuf};

use common::{Scratch, TestResult};
use desktop_lib::folders::store::{read_saved, write_saved, MAX_SAVED_BYTES};
use desktop_lib::folders::{
    resolve, FolderError, FolderKind, FolderProblem, FolderSettings, PickReply, SavedFileState,
    SavedFolders, StoreError,
};

/// A scratch directory laid out like an app: a config file, an index folder, and room for the
/// user's folders.
struct App {
    scratch: Scratch,
    config_file: PathBuf,
    index: PathBuf,
}

fn app(name: &str) -> Result<App, Box<dyn std::error::Error>> {
    let scratch = Scratch::new(name)?;
    let index = scratch.file("app-index");
    fs::create_dir(&index)?;
    let config_file = scratch.file("config").join("folders.json");
    Ok(App {
        scratch,
        config_file,
        index,
    })
}

impl App {
    /// A new, empty user folder.
    fn folder(&self, name: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
        let path = self.scratch.file(name);
        fs::create_dir_all(&path)?;
        Ok(path)
    }

    /// The index folder as `resolve` takes it.
    fn idx(&self) -> Option<&Path> {
        Some(self.index.as_path())
    }

    fn settings(&self) -> FolderSettings {
        FolderSettings::new(Some(self.config_file.clone()), Some(self.index.clone()))
    }

    fn write_raw(&self, bytes: &[u8]) -> TestResult {
        fs::create_dir_all(self.config_file.parent().ok_or("no parent")?)?;
        fs::write(&self.config_file, bytes)?;
        Ok(())
    }
}

fn saved(json: &str) -> Result<SavedFolders, serde_json::Error> {
    serde_json::from_str(json)
}

/// JSON text for a path (backslashes escaped).
fn quoted(path: &Path) -> String {
    serde_json::Value::String(path.to_string_lossy().into_owned()).to_string()
}

fn same_folder(left: &Path, right: &Path) -> bool {
    match (fs::canonicalize(left), fs::canonicalize(right)) {
        (Ok(left), Ok(right)) => left == right,
        _ => false,
    }
}

// ---- the saved file ------------------------------------------------------------------------

#[test]
fn the_saved_file_round_trips_and_leaves_no_temporary_file() -> TestResult {
    let app = app("store_round_trip")?;
    let mut original = SavedFolders::default();
    for kind in FolderKind::ALL {
        original = original.with(kind, Some(format!(r"C:\synthetic\{kind:?}")));
    }

    // The config folder does not exist yet: writing creates it.
    assert!(!app.config_file.parent().ok_or("no parent")?.exists());
    write_saved(&app.config_file, &original)?;

    assert_eq!(read_saved(&app.config_file)?, original);
    assert!(!app.config_file.with_extension("json.tmp").exists());
    // And a second write replaces the first, whole.
    let replaced = SavedFolders::default().with(FolderKind::Ledger, Some(r"C:\synthetic\l".into()));
    write_saved(&app.config_file, &replaced)?;
    assert_eq!(read_saved(&app.config_file)?, replaced);
    Ok(())
}

#[test]
fn no_saved_file_means_nothing_saved() -> TestResult {
    let app = app("store_missing")?;
    assert_eq!(read_saved(&app.config_file)?, SavedFolders::default());
    Ok(())
}

#[test]
fn a_hostile_saved_file_is_refused_as_a_whole() -> TestResult {
    let app = app("store_hostile")?;
    let wide = "x".repeat(2 * MAX_SAVED_BYTES as usize);
    let padded_valid = format!("{{}}{}", " ".repeat(MAX_SAVED_BYTES as usize));
    let cases: Vec<(&str, Vec<u8>, StoreError)> = vec![
        (
            "an extra key",
            br#"{"vault":"C:\\v","extra":"x"}"#.to_vec(),
            StoreError::Malformed,
        ),
        (
            "the index folder",
            br#"{"index":"C:\\i"}"#.to_vec(),
            StoreError::Malformed,
        ),
        (
            "the log folder",
            br#"{"log":"C:\\l"}"#.to_vec(),
            StoreError::Malformed,
        ),
        (
            "a number",
            br#"{"vault":5}"#.to_vec(),
            StoreError::Malformed,
        ),
        (
            "a list",
            br#"{"vault":["C:\\v"]}"#.to_vec(),
            StoreError::Malformed,
        ),
        (
            "an object",
            br#"{"vault":{"path":"C:\\v"}}"#.to_vec(),
            StoreError::Malformed,
        ),
        (
            "a top-level array",
            br#"["C:\\v"]"#.to_vec(),
            StoreError::Malformed,
        ),
        (
            "a top-level string",
            br#""C:\\v""#.to_vec(),
            StoreError::Malformed,
        ),
        ("two objects", br#"{}{}"#.to_vec(), StoreError::Malformed),
        ("empty", Vec::new(), StoreError::Malformed),
        (
            "not UTF-8",
            vec![0xff, 0xfe, b'{', b'}'],
            StoreError::Malformed,
        ),
        ("NUL bytes", vec![0, 0, 0, 0], StoreError::Malformed),
        ("not JSON", b"vault=C:\\v".to_vec(), StoreError::Malformed),
        (
            "one byte over the limit",
            vec![b' '; MAX_SAVED_BYTES as usize + 1],
            StoreError::TooLarge,
        ),
        ("huge", wide.into_bytes(), StoreError::TooLarge),
        (
            "a megabyte of padding",
            vec![b' '; 1024 * 1024],
            StoreError::TooLarge,
        ),
    ];
    for (name, bytes, expected) in cases {
        app.write_raw(&bytes)?;
        assert_eq!(read_saved(&app.config_file), Err(expected), "{name}");
    }
    // Exactly at the limit, valid: accepted.
    app.write_raw(padded_valid.as_bytes())?;
    assert_eq!(
        padded_valid.len() as u64,
        MAX_SAVED_BYTES + 2,
        "the test's own arithmetic"
    );
    assert_eq!(read_saved(&app.config_file), Err(StoreError::TooLarge));
    app.write_raw(format!("{{}}{}", " ".repeat(MAX_SAVED_BYTES as usize - 2)).as_bytes())?;
    assert_eq!(read_saved(&app.config_file)?, SavedFolders::default());
    // A folder where the file should be cannot be read.
    fs::remove_file(&app.config_file)?;
    fs::create_dir(&app.config_file)?;
    assert_eq!(read_saved(&app.config_file), Err(StoreError::Unreadable));
    Ok(())
}

// ---- which saved folders are valid ---------------------------------------------------------

#[test]
fn a_saved_folder_that_is_not_usable_is_absent_with_a_typed_reason() -> TestResult {
    let app = app("resolve_unusable")?;
    let good = app.folder("good")?;
    let a_file = app.scratch.file("a-file.txt");
    fs::write(&a_file, b"synthetic")?;
    let json = format!(
        r#"{{"vault":{},"questionnaires":{},"exports":{},"imports":{},"ledger":{}}}"#,
        quoted(&good),
        quoted(&app.scratch.file("missing")),
        quoted(&a_file),
        quoted(Path::new(r"..\relative")),
        quoted(Path::new(r"\\server\share\ledger")),
    );

    let resolved = resolve(&saved(&json)?, app.idx(), None);

    assert!(same_folder(
        resolved
            .get(FolderKind::Vault)
            .ok_or("the good folder was dropped")?,
        &good
    ));
    for (kind, problem) in [
        (FolderKind::Questionnaires, FolderProblem::Unavailable),
        (FolderKind::Exports, FolderProblem::Unavailable),
        (FolderKind::Imports, FolderProblem::NotAbsolute),
        (FolderKind::Ledger, FolderProblem::Unavailable),
    ] {
        assert_eq!(resolved.get(kind), None, "{kind:?} must not be passed");
        assert_eq!(resolved.problem(kind), Some(problem), "{kind:?}");
    }
    assert_eq!(resolved.problem(FolderKind::Vault), None);
    Ok(())
}

#[test]
fn unc_device_and_nul_texts_are_never_opened_or_passed() -> TestResult {
    let app = app("resolve_hostile_text")?;
    for text in [
        r"\\server\share",
        r"\\?\UNC\server\share\x",
        r"\\.\NUL",
        r"\\.\COM1",
        r"\\?\GLOBALROOT\Device\HarddiskVolume1",
        "C:\\x\0y",
        "",
        "   ",
        "C:",
        r"\",
    ] {
        let json = serde_json::json!({ "vault": text }).to_string();
        let resolved = resolve(&saved(&json)?, app.idx(), None);
        assert_eq!(resolved.get(FolderKind::Vault), None, "{text:?}");
        assert!(resolved.problem(FolderKind::Vault).is_some(), "{text:?}");
    }
    Ok(())
}

#[test]
fn a_tampered_file_is_checked_again_at_the_next_start() -> TestResult {
    let app = app("resolve_tampered")?;
    let vault = app.folder("vault")?;
    let ledger = app.folder("ledger")?;
    write_saved(
        &app.config_file,
        &SavedFolders::default()
            .with(
                FolderKind::Vault,
                Some(vault.to_string_lossy().into_owned()),
            )
            .with(
                FolderKind::Ledger,
                Some(ledger.to_string_lossy().into_owned()),
            ),
    )?;

    let first = app.settings().startup(None);
    assert!(first.get(FolderKind::Vault).is_some());
    assert!(first.get(FolderKind::Ledger).is_some());

    // Between launches the ledger folder disappears and the file is edited to point the vault at the
    // index folder.
    fs::remove_dir(&ledger)?;
    app.write_raw(format!(r#"{{"vault":{}}}"#, quoted(&app.index)).as_bytes())?;
    let second_launch = app.settings();
    let second = second_launch.startup(None);

    assert_eq!(second.get(FolderKind::Vault), None);
    assert_eq!(
        second.problem(FolderKind::Vault),
        Some(FolderProblem::Overlap)
    );
    let view = second_launch.view();
    assert!(view.ready);
    let vault_view = &view.folders[0];
    assert_eq!(vault_view.kind, FolderKind::Vault);
    assert_eq!(vault_view.in_use, None);
    assert_eq!(vault_view.start_problem, Some(FolderProblem::Overlap));
    assert_eq!(vault_view.problem, Some(FolderProblem::Overlap));
    Ok(())
}

#[test]
fn a_damaged_saved_file_at_start_passes_no_folder_and_says_so() -> TestResult {
    let app = app("resolve_damaged")?;
    app.write_raw(br#"{"vault":"C:\\v","surprise":1}"#)?;
    let settings = app.settings();

    let resolved = settings.startup(None);

    for kind in FolderKind::ALL {
        assert_eq!(resolved.get(kind), None, "{kind:?}");
    }
    let view = settings.view();
    assert_eq!(view.saved_file, SavedFileState::Invalid);
    assert_eq!(view.saved_file_at_start, Some(SavedFileState::Invalid));
    Ok(())
}

#[test]
fn before_the_start_decision_nothing_is_ready() -> TestResult {
    let app = app("resolve_not_ready")?;
    let settings = app.settings();
    let view = settings.view();
    assert!(!view.ready);
    assert_eq!(view.saved_file_at_start, None);
    assert!(view.folders.iter().all(|folder| folder.in_use.is_none()));
    assert_eq!(view.folders.len(), 5);
    Ok(())
}

// ---- the overlap rules ---------------------------------------------------------------------

#[test]
fn overlap_cases_follow_the_sidecar_rules() -> TestResult {
    let app = app("overlap")?;
    let vault = app.folder("vault")?;
    let inside_vault = app.folder(r"vault\inner")?;
    let vault_sibling = app.folder("vault2")?;
    let questionnaires = app.folder("questionnaires")?;
    let inside_questionnaires = app.folder(r"questionnaires\answers")?;
    let imports = app.folder("imports")?;
    let index_child = app.folder(r"app-index\child")?;
    let scratch_root = app.scratch.path().to_owned();

    // (name, the saved folders, the kinds expected to be dropped as overlapping)
    type Case<'a> = (&'a str, Vec<(FolderKind, &'a Path)>, Vec<FolderKind>);
    use FolderKind::{Exports, Imports, Ledger, Questionnaires, Vault};
    let cases: Vec<Case<'_>> = vec![
        (
            "ledger inside the vault",
            vec![(Vault, &vault), (Ledger, &inside_vault)],
            vec![Ledger],
        ),
        (
            "ledger equal to the vault",
            vec![(Vault, &vault), (Ledger, &vault)],
            vec![Ledger],
        ),
        (
            "ledger containing the vault",
            vec![(Vault, &inside_vault), (Ledger, &vault)],
            vec![Ledger],
        ),
        (
            "import inside the questionnaires",
            vec![
                (Questionnaires, &questionnaires),
                (Imports, &inside_questionnaires),
            ],
            vec![Imports],
        ),
        (
            "ledger equal to the imports",
            vec![(Imports, &imports), (Ledger, &imports)],
            vec![Ledger],
        ),
        (
            "export inside the vault",
            vec![(Vault, &vault), (Exports, &inside_vault)],
            vec![Exports],
        ),
        (
            "questionnaires inside the vault",
            vec![(Vault, &vault), (Questionnaires, &inside_vault)],
            vec![Questionnaires],
        ),
        (
            "vault containing the index",
            vec![(Vault, &scratch_root)],
            vec![Vault],
        ),
        (
            "questionnaires inside the index",
            vec![(Questionnaires, &index_child)],
            vec![Questionnaires],
        ),
        (
            "ledger equal to the index",
            vec![(Ledger, &app.index)],
            vec![Ledger],
        ),
        // Allowed by the sidecar: these must NOT be dropped.
        (
            "export inside the questionnaires",
            vec![
                (Questionnaires, &questionnaires),
                (Exports, &inside_questionnaires),
            ],
            vec![],
        ),
        (
            "questionnaires equal to the exports",
            vec![
                (Questionnaires, &questionnaires),
                (Exports, &questionnaires),
            ],
            vec![],
        ),
        (
            "a folder whose name starts like another",
            vec![(Vault, &vault), (Ledger, &vault_sibling)],
            vec![],
        ),
    ];
    for (name, folders, expected_dropped) in cases {
        let mut set = SavedFolders::default();
        for (kind, path) in &folders {
            set = set.with(*kind, Some(path.to_string_lossy().into_owned()));
        }
        let resolved = resolve(&set, app.idx(), None);
        let dropped: Vec<FolderKind> = FolderKind::ALL
            .into_iter()
            .filter(|kind| resolved.problem(*kind) == Some(FolderProblem::Overlap))
            .collect();
        assert_eq!(dropped, expected_dropped, "{name}");
        for kind in &expected_dropped {
            assert_eq!(
                resolved.get(*kind),
                None,
                "{name}: {kind:?} must not be passed"
            );
        }
        for (kind, _) in &folders {
            if !expected_dropped.contains(kind) {
                assert!(
                    resolved.get(*kind).is_some(),
                    "{name}: {kind:?} was dropped"
                );
            }
        }
    }
    Ok(())
}

#[test]
fn the_same_folder_spelled_with_another_case_is_still_an_overlap() -> TestResult {
    let app = app("overlap_case")?;
    let vault = app.folder("MixedCaseVault")?;
    let shouting = PathBuf::from(vault.to_string_lossy().to_uppercase());
    let set = SavedFolders::default()
        .with(
            FolderKind::Vault,
            Some(vault.to_string_lossy().into_owned()),
        )
        .with(
            FolderKind::Ledger,
            Some(shouting.to_string_lossy().into_owned()),
        );

    let resolved = resolve(&set, app.idx(), None);

    assert!(resolved.get(FolderKind::Vault).is_some());
    assert_eq!(
        resolved.problem(FolderKind::Ledger),
        Some(FolderProblem::Overlap)
    );
    Ok(())
}

#[test]
fn an_earlier_folder_is_never_unseated_by_a_later_one() -> TestResult {
    let app = app("overlap_order")?;
    let vault = app.folder("vault")?;
    let ledger_inside = app.folder(r"vault\ledger")?;
    let set = SavedFolders::default()
        .with(
            FolderKind::Ledger,
            Some(ledger_inside.to_string_lossy().into_owned()),
        )
        .with(
            FolderKind::Vault,
            Some(vault.to_string_lossy().into_owned()),
        );

    let resolved = resolve(&set, app.idx(), None);

    assert!(resolved.get(FolderKind::Vault).is_some());
    assert_eq!(resolved.get(FolderKind::Ledger), None);
    Ok(())
}

// ---- the debug-only vault fallback ---------------------------------------------------------

#[test]
fn the_debug_vault_is_used_only_when_no_vault_is_saved() -> TestResult {
    let app = app("fallback")?;
    let saved_vault = app.folder("saved-vault")?;
    let dev_vault = app.folder("dev-vault")?;
    let text = |path: &Path| path.to_string_lossy().into_owned();

    // A saved vault wins.
    let with_saved = SavedFolders::default().with(FolderKind::Vault, Some(text(&saved_vault)));
    let resolved = resolve(&with_saved, app.idx(), Some(dev_vault.clone()));
    assert!(same_folder(
        resolved.get(FolderKind::Vault).ok_or("no vault")?,
        &saved_vault
    ));

    // No saved vault: the debug one is used.
    let resolved = resolve(&SavedFolders::default(), app.idx(), Some(dev_vault.clone()));
    assert!(same_folder(
        resolved.get(FolderKind::Vault).ok_or("no vault")?,
        &dev_vault
    ));

    // No saved vault and no debug one (a release build passes none): no vault.
    let resolved = resolve(&SavedFolders::default(), app.idx(), None);
    assert_eq!(resolved.get(FolderKind::Vault), None);

    // A saved vault that is not valid is NOT replaced by the debug one: the key stays absent and the
    // reason is recorded.
    let broken = SavedFolders::default().with(
        FolderKind::Vault,
        Some(text(&app.scratch.file("deleted-vault"))),
    );
    let resolved = resolve(&broken, app.idx(), Some(dev_vault.clone()));
    assert_eq!(resolved.get(FolderKind::Vault), None);
    assert_eq!(
        resolved.problem(FolderKind::Vault),
        Some(FolderProblem::Unavailable)
    );

    // The debug vault goes through the same overlap check as a saved one.
    let ledger_over_dev = SavedFolders::default().with(FolderKind::Ledger, Some(text(&dev_vault)));
    let resolved = resolve(&ledger_over_dev, app.idx(), Some(dev_vault));
    assert!(resolved.get(FolderKind::Vault).is_some());
    assert_eq!(
        resolved.problem(FolderKind::Ledger),
        Some(FolderProblem::Overlap)
    );
    Ok(())
}

// ---- saving a picked folder ----------------------------------------------------------------

fn saved_display(reply: PickReply) -> Result<String, Box<dyn std::error::Error>> {
    match reply {
        PickReply::Saved { display, .. } => Ok(display),
        PickReply::Cancelled => Err("expected a saved folder".into()),
    }
}

#[test]
fn a_picked_folder_is_saved_for_the_next_launch_and_does_not_change_this_run() -> TestResult {
    let app = app("pick_saved")?;
    let settings = app.settings();
    let first = app.folder("first")?;
    let second = app.folder("second")?;
    settings.startup(None);

    let shown = saved_display(settings.save_picked(FolderKind::Ledger, first.clone())?)?;
    assert!(same_folder(Path::new(&shown), &first));
    settings.save_picked(FolderKind::Ledger, second.clone())?;

    let view = settings.view();
    let ledger = view
        .folders
        .iter()
        .find(|folder| folder.kind == FolderKind::Ledger)
        .ok_or("no ledger row")?;
    // Saved for the next launch, but not in use now: there is no restart path.
    assert_eq!(ledger.in_use, None);
    let saved_now = ledger.saved.as_deref().ok_or("nothing saved")?;
    assert!(same_folder(Path::new(saved_now), &second));
    assert_eq!(ledger.problem, None);
    // And the next launch passes it.
    let next_launch = app.settings().startup(None);
    assert!(same_folder(
        next_launch.get(FolderKind::Ledger).ok_or("not passed")?,
        &second
    ));
    Ok(())
}

#[test]
fn a_pick_that_fails_a_check_saves_nothing() -> TestResult {
    let app = app("pick_refused")?;
    let settings = app.settings();
    let vault = app.folder("vault")?;
    let inside = app.folder(r"vault\inner")?;
    let a_file = app.scratch.file("file.txt");
    fs::write(&a_file, b"synthetic")?;
    settings.save_picked(FolderKind::Vault, vault.clone())?;
    let before = fs::read(&app.config_file)?;

    let refused: Vec<(PathBuf, FolderError)> = vec![
        (inside.clone(), FolderError::Overlap),
        (vault.clone(), FolderError::Overlap),
        (app.index.clone(), FolderError::Overlap),
        (app.scratch.path().to_owned(), FolderError::Overlap),
        (app.scratch.file("missing"), FolderError::Unavailable),
        (a_file, FolderError::Unavailable),
        (PathBuf::from(r"\\server\share"), FolderError::Unavailable),
        (
            PathBuf::from(r"\\?\UNC\server\share"),
            FolderError::Unavailable,
        ),
        (PathBuf::from("relative"), FolderError::Unavailable),
    ];
    for (picked, expected) in refused {
        assert_eq!(
            settings.save_picked(FolderKind::Ledger, picked.clone()),
            Err(expected),
            "{picked:?}"
        );
    }
    assert_eq!(
        fs::read(&app.config_file)?,
        before,
        "the saved file must not change"
    );
    Ok(())
}

#[test]
fn a_pick_that_would_invalidate_another_saved_folder_is_refused() -> TestResult {
    let app = app("pick_breaks_another")?;
    let settings = app.settings();
    let parent = app.folder("parent")?;
    let child = app.folder(r"parent\child")?;
    settings.save_picked(FolderKind::Ledger, child)?;
    let before = fs::read(&app.config_file)?;

    // The vault would contain the saved ledger folder: the vault itself looks fine, but the ledger
    // would silently stop being valid, so the pick is refused.
    assert_eq!(
        settings.save_picked(FolderKind::Vault, parent),
        Err(FolderError::Overlap)
    );
    assert_eq!(fs::read(&app.config_file)?, before);
    Ok(())
}

#[test]
fn a_pick_replaces_a_damaged_saved_file() -> TestResult {
    let app = app("pick_recovers")?;
    app.write_raw(b"{ not json")?;
    let settings = app.settings();
    let folder = app.folder("ledger")?;

    settings.save_picked(FolderKind::Ledger, folder)?;

    assert_eq!(settings.view().saved_file, SavedFileState::Ok);
    Ok(())
}

#[test]
fn without_a_config_folder_nothing_can_be_saved() -> TestResult {
    let app = app("pick_no_config")?;
    let settings = FolderSettings::new(None, Some(app.index.clone()));
    let folder = app.folder("ledger")?;
    assert_eq!(
        settings.save_picked(FolderKind::Ledger, folder),
        Err(FolderError::SaveFailed)
    );
    Ok(())
}

// ---- the file list -------------------------------------------------------------------------

#[test]
fn files_are_listed_from_the_folder_in_use_workbooks_for_two_kinds_csv_for_imports() -> TestResult {
    // Renamed and changed in chunk 65d: the Imports folder now has a list (`.csv` only); the vault
    // and ledger folders still have none. Was `..._and_only_for_workbook_folders`.
    let app = app("list")?;
    let questionnaires = app.folder("questionnaires")?;
    fs::write(questionnaires.join("a.xlsx"), b"synthetic")?;
    fs::write(questionnaires.join("notes.txt"), b"synthetic")?;
    fs::write(questionnaires.join("users.csv"), b"synthetic")?;
    let imports = app.folder("imports")?;
    fs::write(imports.join("Users.CSV"), b"synthetic")?;
    fs::write(imports.join("a.xlsx"), b"synthetic")?;
    fs::write(imports.join("notes.txt"), b"synthetic")?;
    let later = app.folder("later")?;
    fs::write(later.join("b.xlsx"), b"synthetic")?;
    write_saved(
        &app.config_file,
        &SavedFolders::default()
            .with(
                FolderKind::Questionnaires,
                Some(questionnaires.to_string_lossy().into_owned()),
            )
            .with(
                FolderKind::Imports,
                Some(imports.to_string_lossy().into_owned()),
            ),
    )?;
    let settings = app.settings();

    // Before the start decision there is no folder in use.
    assert_eq!(
        settings.list_files(FolderKind::Questionnaires),
        Err(FolderError::NotConfigured)
    );
    assert_eq!(
        settings.list_files(FolderKind::Imports),
        Err(FolderError::NotConfigured)
    );
    settings.startup(None);
    let listed = settings.list_files(FolderKind::Questionnaires)?;
    assert_eq!(listed.files, ["a.xlsx"]);
    assert!(!listed.truncated);
    // The import folder offers its `.csv` files and nothing else, as the kind it was asked for.
    let imported = settings.list_files(FolderKind::Imports)?;
    assert_eq!(imported.kind, FolderKind::Imports);
    assert_eq!(imported.files, ["Users.CSV"]);
    assert!(!imported.truncated);

    // Changing the saved folder does not change what this run lists.
    settings.save_picked(FolderKind::Questionnaires, later)?;
    assert_eq!(
        settings.list_files(FolderKind::Questionnaires)?.files,
        ["a.xlsx"]
    );

    // No export folder is in use; the other kinds have no file list at all.
    assert_eq!(
        settings.list_files(FolderKind::Exports),
        Err(FolderError::NotConfigured)
    );
    for kind in [FolderKind::Vault, FolderKind::Ledger] {
        assert_eq!(
            settings.list_files(kind),
            Err(FolderError::NotListable),
            "{kind:?}"
        );
    }
    Ok(())
}

#[test]
fn a_reply_never_carries_a_path_field() -> TestResult {
    let app = app("reply_shape")?;
    let folder = app.folder("vault")?;
    let settings = app.settings();
    settings.save_picked(FolderKind::Vault, folder)?;
    settings.startup(None);

    let view = serde_json::to_string(&settings.view())?;

    assert!(!view.contains("\"path\""), "{view}");
    for key in ["kind", "in_use", "saved", "problem", "start_problem"] {
        assert!(
            view.contains(&format!("\"{key}\"")),
            "{key} missing: {view}"
        );
    }
    Ok(())
}
