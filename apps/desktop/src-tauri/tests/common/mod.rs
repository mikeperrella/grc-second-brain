//! Helpers shared by the process-level integration tests (M2 plan section 12).
//!
//! Rules these tests follow: no `unsafe` (the handle and process helpers they need are safe
//! functions in `desktop_lib::sidecar::job`), no sleeping (waits are on events, condition variables
//! and process handles, each with a bound), and helper functions return `Result` and use `?`
//! instead of `unwrap`/`expect`.

#![allow(
    dead_code,
    reason = "each integration test crate uses a different subset of these helpers"
)]

use std::error::Error;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use desktop_lib::child_env::build_child_env;
use desktop_lib::sidecar::job::{kill_process_by_pid, wait_for_process_exit, ProcessWait};
use desktop_lib::sidecar::spawn::system_windows_directory;
use desktop_lib::sidecar::supervisor::SidecarSpec;
use desktop_lib::token::AuthToken;

pub type TestResult = Result<(), Box<dyn Error>>;

/// Handshake timeout for every test that expects success or a specific failure. Generous on
/// purpose: no such test depends on a margin of less than several seconds. The fake's slowest mode
/// (`late-ready`) takes 1.5 s, which leaves 13.5 s.
pub const TEST_HANDSHAKE: Duration = Duration::from_secs(15);

/// Handshake timeout ONLY for tests that expect a `Timeout`, so they do not take 30 s.
pub const TIMEOUT_TEST_HANDSHAKE: Duration = Duration::from_secs(2);

/// Added to a handshake timeout to get the bound for waiting on the supervisor's state.
pub const SETTLE_SLACK: Duration = Duration::from_secs(15);

/// Bound for waiting on a process to exit after it was told to die.
pub const EXIT_BOUND: Duration = Duration::from_secs(15);

/// The port the fake sidecar announces.
pub const FAKE_PORT: u16 = 40123;

/// A scratch directory, removed when dropped.
///
/// It lives under `CARGO_TARGET_TMPDIR`, the private per-package directory Cargo provides for
/// integration tests (inside `target/`, not the system temp directory that other users and
/// programs share). The name ends in 64 random bits from the operating system's CSPRNG, and the
/// directory is created with `create_dir`, which FAILS if the path already exists, so a test can
/// never silently reuse or take over a directory it did not create.
pub struct Scratch(PathBuf);

impl Scratch {
    pub fn new(name: &str) -> Result<Self, Box<dyn Error>> {
        let mut random = [0u8; 8];
        getrandom::fill(&mut random).map_err(|_| "the operating system gave no random bytes")?;
        let suffix = u64::from_le_bytes(random);

        let base = Path::new(env!("CARGO_TARGET_TMPDIR"));
        // The parent may not exist yet; this is the only `create_dir_all`, and it is not the
        // scratch directory itself.
        fs::create_dir_all(base)?;
        let path = base.join(format!("grc_{name}_{suffix:016x}"));
        fs::create_dir(&path)?;
        Ok(Self(path))
    }

    pub fn path(&self) -> &Path {
        &self.0
    }

    pub fn file(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        // Best effort: a leftover temp directory must not fail the test that used it.
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// Kills the recorded process ids when dropped, so a failing test cannot leave an orphan behind.
/// Killing a process that is already gone is not an error.
#[derive(Default)]
pub struct Reaper(Vec<u32>);

impl Reaper {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add(&mut self, pid: u32) {
        self.0.push(pid);
    }
}

impl Drop for Reaper {
    fn drop(&mut self) {
        for pid in &self.0 {
            let _ = kill_process_by_pid(*pid);
        }
    }
}

pub fn fake_sidecar_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_fake_sidecar"))
}

/// A spec that runs the fake sidecar in `mode`, with the real environment allowlist, a fresh token
/// and `scratch` as both the working directory and the log directory.
pub fn fake_spec(
    scratch: &Scratch,
    mode: &str,
    file: Option<&Path>,
    handshake: Duration,
) -> Result<SidecarSpec, Box<dyn Error>> {
    let token = AuthToken::generate()?;
    let env = build_child_env(&system_windows_directory()?, scratch.path(), &token)?;
    let mut args = vec![OsString::from(mode)];
    if let Some(file) = file {
        args.push(file.as_os_str().to_owned());
    }
    Ok(
        SidecarSpec::new(fake_sidecar_path(), args, scratch.path().to_owned(), env)
            .with_handshake_timeout(handshake),
    )
}

pub fn read_pid(path: &Path) -> Result<u32, Box<dyn Error>> {
    Ok(fs::read_to_string(path)?.trim().parse()?)
}

/// Assert that the process with this id has exited (waiting up to `EXIT_BOUND`).
pub fn assert_exits(pid: u32, what: &str) -> TestResult {
    let outcome = wait_for_process_exit(pid, EXIT_BOUND)?;
    assert_eq!(
        outcome,
        ProcessWait::Exited,
        "{what} (pid {pid}) is still running"
    );
    Ok(())
}
