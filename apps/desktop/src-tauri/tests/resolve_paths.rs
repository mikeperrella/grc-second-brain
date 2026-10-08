//! The resolver's path containment check against real files (CLAUDE.md 3.6: canonicalize, then
//! require the real path to be inside the approved root, so `..` segments and symlinks cannot
//! escape).
//!
//! These tests are an integration test, not unit tests in `src/`, so they can use Cargo's private
//! test directory (`CARGO_TARGET_TMPDIR`, via `common::Scratch`) instead of the shared system temp
//! directory. `checked_inside` exists only in debug builds, hence the `cfg` below; the release
//! behaviour (`NotConfigured`) is tested by `release_tests` inside `resolve.rs`.

#![cfg(debug_assertions)]

mod common;

use std::fs;

use common::{Scratch, TestResult};
use desktop_lib::sidecar::resolve::{checked_inside, ResolveError};

#[test]
fn a_file_inside_the_root_is_accepted() -> TestResult {
    let scratch = Scratch::new("inside")?;
    let root = scratch.path().join("sidecar");
    fs::create_dir_all(root.join(".venv"))?;
    let program = root.join(".venv").join("python.exe");
    fs::write(&program, b"synthetic")?;

    let resolved = checked_inside(&program, &root)?;

    assert!(resolved.starts_with(root.canonicalize()?));
    assert!(resolved.is_file());
    Ok(())
}

#[test]
fn a_missing_file_is_not_installed() -> TestResult {
    let scratch = Scratch::new("missing")?;
    let root = scratch.path().join("sidecar");
    fs::create_dir_all(&root)?;

    assert_eq!(
        checked_inside(&root.join(".venv").join("python.exe"), &root),
        Err(ResolveError::NotInstalled)
    );
    Ok(())
}

#[test]
fn a_missing_root_is_not_installed() -> TestResult {
    let scratch = Scratch::new("noroot")?;
    let program = scratch.path().join("python.exe");
    fs::write(&program, b"synthetic")?;

    assert_eq!(
        checked_inside(&program, &scratch.path().join("no_such_root")),
        Err(ResolveError::NotInstalled)
    );
    Ok(())
}

#[test]
fn a_directory_is_not_a_program() -> TestResult {
    let scratch = Scratch::new("directory")?;
    let root = scratch.path().join("sidecar");
    fs::create_dir_all(root.join("python.exe"))?;

    assert_eq!(
        checked_inside(&root.join("python.exe"), &root),
        Err(ResolveError::NotInstalled)
    );
    Ok(())
}

#[test]
fn a_dot_dot_escape_is_rejected() -> TestResult {
    let scratch = Scratch::new("dotdot")?;
    let root = scratch.path().join("sidecar");
    fs::create_dir_all(&root)?;
    let outside = scratch.path().join("outside");
    fs::create_dir_all(&outside)?;
    fs::write(outside.join("python.exe"), b"synthetic")?;

    let escaping = root.join("..").join("outside").join("python.exe");

    assert_eq!(
        checked_inside(&escaping, &root),
        Err(ResolveError::OutsideSidecarDir)
    );
    Ok(())
}

#[test]
fn a_symlink_escape_is_rejected() -> TestResult {
    // Creating the symlink must work for this test to mean anything, so a failure to create it
    // FAILS the test (the `?` propagates). There is no skip and no early return. It works on this
    // machine (Developer Mode); the first CI run confirms it on the hosted runner.
    let scratch = Scratch::new("symlink")?;
    let root = scratch.path().join("sidecar");
    fs::create_dir_all(&root)?;
    let outside = scratch.path().join("outside");
    fs::create_dir_all(&outside)?;
    let real = outside.join("python.exe");
    fs::write(&real, b"synthetic")?;

    let link = root.join("python.exe");
    std::os::windows::fs::symlink_file(&real, &link)?;

    assert_eq!(
        checked_inside(&link, &root),
        Err(ResolveError::OutsideSidecarDir)
    );
    Ok(())
}
