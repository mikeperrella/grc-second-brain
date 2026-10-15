//! File names from the window against real folders (chunk 6e, CLAUDE.md 3.6, backlog G9, threat
//! F35-3). `folders::files` unit tests cover the pure name rules; these create files and links on
//! disk and check that a name can only ever reach a regular file directly inside its folder.

mod common;

use std::fs;
use std::path::{Path, PathBuf};

use common::{Scratch, TestResult};
use desktop_lib::folders::files::{
    is_one_component, list_workbooks, resolve_file, NameError, MAX_LISTED_FILES,
};

/// A scratch directory with a `root` folder (the questionnaire folder) and a file OUTSIDE it.
struct Fixture {
    scratch: Scratch,
    root: PathBuf,
    outside_file: PathBuf,
}

fn fixture(name: &str) -> Result<Fixture, Box<dyn std::error::Error>> {
    let scratch = Scratch::new(name)?;
    let root = scratch.file("root");
    fs::create_dir(&root)?;
    let outside_file = scratch.file("outside.xlsx");
    fs::write(&outside_file, b"synthetic")?;
    Ok(Fixture {
        scratch,
        root,
        outside_file,
    })
}

fn canonical(path: &Path) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let text = fs::canonicalize(path)?.to_string_lossy().into_owned();
    Ok(PathBuf::from(
        text.strip_prefix(r"\\?\").unwrap_or(&text).to_owned(),
    ))
}

#[test]
fn a_regular_file_in_the_folder_resolves_to_a_path_inside_it() -> TestResult {
    let fixture = fixture("files_ok")?;
    fs::write(fixture.root.join("q.xlsx"), b"synthetic")?;

    let found = resolve_file(&fixture.root, "q.xlsx")?;

    assert!(found.starts_with(canonical(&fixture.root)?));
    assert!(found.is_file());
    Ok(())
}

#[test]
fn a_name_with_a_different_case_resolves_to_the_real_file() -> TestResult {
    // Windows file names are case-insensitive; the answer is the real, canonical spelling.
    let fixture = fixture("files_case")?;
    fs::write(fixture.root.join("Quarterly Report.xlsx"), b"synthetic")?;

    let found = resolve_file(&fixture.root, "QUARTERLY REPORT.XLSX")?;

    assert_eq!(
        found.file_name().and_then(|name| name.to_str()),
        Some("Quarterly Report.xlsx")
    );
    Ok(())
}

#[test]
fn names_that_try_to_leave_the_folder_are_refused_before_the_disk_is_touched() -> TestResult {
    let fixture = fixture("files_escape")?;
    assert!(fixture.outside_file.is_file(), "the test needs the file");
    let outside = fixture.outside_file.to_string_lossy().into_owned();
    let outside_unc = format!(
        r"\\?\{}",
        fixture
            .outside_file
            .to_string_lossy()
            .trim_start_matches(r"\\?\")
    );
    for name in [
        r"..\outside.xlsx".to_owned(),
        "../outside.xlsx".to_owned(),
        r"..\..\outside.xlsx".to_owned(),
        r"sub\..\..\outside.xlsx".to_owned(),
        outside,
        outside_unc,
        r"\outside.xlsx".to_owned(),
        r"C:outside.xlsx".to_owned(),
        r"\\server\share\q.xlsx".to_owned(),
        r"\\.\NUL".to_owned(),
        "outside.xlsx:stream".to_owned(),
        "NUL".to_owned(),
        "nul.xlsx".to_owned(),
        "q.xlsx.".to_owned(),
        "q.xlsx ".to_owned(),
        "q\0.xlsx".to_owned(),
        "x".repeat(261),
        String::new(),
        "..".to_owned(),
        ".".to_owned(),
    ] {
        assert_eq!(
            resolve_file(&fixture.root, &name),
            Err(NameError::NotOneComponent),
            "{name:?}"
        );
        assert!(!is_one_component(&name), "{name:?}");
    }
    Ok(())
}

#[test]
fn a_missing_file_and_a_folder_are_not_a_file() -> TestResult {
    let fixture = fixture("files_missing")?;
    fs::create_dir(fixture.root.join("folder.xlsx"))?;

    assert_eq!(
        resolve_file(&fixture.root, "nope.xlsx"),
        Err(NameError::NotAFile)
    );
    assert_eq!(
        resolve_file(&fixture.root, "folder.xlsx"),
        Err(NameError::NotAFile)
    );
    // A root that does not exist resolves nothing.
    assert_eq!(
        resolve_file(&fixture.scratch.file("no-such-root"), "q.xlsx"),
        Err(NameError::NotAFile)
    );
    Ok(())
}

#[test]
fn a_link_to_a_file_outside_the_folder_is_refused_where_windows_lets_one_be_created() -> TestResult
{
    let fixture = fixture("files_symlink")?;
    let link = fixture.root.join("link.xlsx");
    // A symbolic link needs a privilege (or Developer Mode) on Windows, so creating one can fail
    // for a reason that has nothing to do with the code under test. A junction cannot be made
    // with the standard library; the Python scanner test of chunk 6c covers that case.
    if let Err(error) = std::os::windows::fs::symlink_file(&fixture.outside_file, &link) {
        eprintln!(
            "SKIPPED the symlink part: this account cannot create a symbolic link ({error}); \
             the escape case is NOT exercised on this machine"
        );
        return Ok(());
    }

    assert_eq!(
        resolve_file(&fixture.root, "link.xlsx"),
        Err(NameError::Escapes)
    );
    // A link is not offered in the listing at all.
    let listing = list_workbooks(&fixture.root).ok_or("the folder could not be listed")?;
    assert!(
        !listing.files.contains(&"link.xlsx".to_owned()),
        "{listing:?}"
    );

    // A link that stays inside the folder points at a real file in it: allowed.
    fs::write(fixture.root.join("real.xlsx"), b"synthetic")?;
    std::os::windows::fs::symlink_file(
        fixture.root.join("real.xlsx"),
        fixture.root.join("inner.xlsx"),
    )?;
    let inner = resolve_file(&fixture.root, "inner.xlsx")?;
    assert!(inner.starts_with(canonical(&fixture.root)?));
    Ok(())
}

#[test]
fn a_hard_link_is_a_real_file_in_the_folder_and_that_is_a_known_limit() -> TestResult {
    // Windows lets any account hard-link a file on the same volume. The link IS a directory entry
    // of the folder, so no path check can tell it from a copy. This test pins the behavior so that
    // the limit is visible; the sidecar's own checks and the threat model (same-user, O1) own it.
    let fixture = fixture("files_hardlink")?;
    fs::hard_link(&fixture.outside_file, fixture.root.join("hard.xlsx"))?;

    let found = resolve_file(&fixture.root, "hard.xlsx")?;

    assert!(found.starts_with(canonical(&fixture.root)?));
    Ok(())
}

#[test]
fn the_listing_offers_only_workbooks_sorted_without_regard_to_case() -> TestResult {
    let fixture = fixture("files_list")?;
    for name in [
        "a.xlsx",
        "B.XLSM",
        "c.xls",
        "d.txt",
        "~$a.xlsx",
        "e.XLSX",
        "noextension",
        ".xlsx",
    ] {
        fs::write(fixture.root.join(name), b"synthetic")?;
    }
    fs::create_dir(fixture.root.join("folder.xlsx"))?;

    let listing = list_workbooks(&fixture.root).ok_or("the folder could not be listed")?;

    assert_eq!(listing.files, ["a.xlsx", "B.XLSM", "e.XLSX"]);
    assert!(!listing.truncated);
    Ok(())
}

#[test]
fn a_listing_is_capped_and_says_so() -> TestResult {
    let fixture = fixture("files_cap")?;
    for number in 0..MAX_LISTED_FILES + 5 {
        fs::write(fixture.root.join(format!("f{number:03}.xlsx")), b"")?;
    }

    let listing = list_workbooks(&fixture.root).ok_or("the folder could not be listed")?;

    assert_eq!(listing.files.len(), MAX_LISTED_FILES);
    assert!(listing.truncated);
    assert_eq!(listing.files.first().map(String::as_str), Some("f000.xlsx"));
    assert_eq!(listing.files.last().map(String::as_str), Some("f499.xlsx"));
    Ok(())
}

#[test]
fn a_folder_that_cannot_be_read_has_no_listing() -> TestResult {
    let fixture = fixture("files_unreadable")?;
    assert!(list_workbooks(&fixture.scratch.file("no-such-folder")).is_none());
    // A file where a folder is expected.
    assert!(list_workbooks(&fixture.outside_file).is_none());
    Ok(())
}
