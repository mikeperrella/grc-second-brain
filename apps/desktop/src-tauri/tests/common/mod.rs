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
use std::thread;
use std::time::{Duration, Instant};

use desktop_lib::child_env::build_child_env;
use desktop_lib::sidecar::job::{
    kill_process_by_pid, wait_for_process_exit, ProcessInfo, ProcessWait,
};
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
    fake_spec_with_vault(scratch, mode, file, handshake, None)
}

/// Like [`fake_spec`], with an optional vault directory. The index directory is `scratch`'s
/// `index` folder (the spec does not create it; the fake never touches it).
pub fn fake_spec_with_vault(
    scratch: &Scratch,
    mode: &str,
    file: Option<&Path>,
    handshake: Duration,
    vault_dir: Option<&Path>,
) -> Result<SidecarSpec, Box<dyn Error>> {
    let token = AuthToken::generate()?;
    let env = build_child_env(
        &system_windows_directory()?,
        scratch.path(),
        &scratch.file("index"),
        vault_dir,
        &token,
    )?;
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

/// Why a set of processes is not the shape the dev (venv) sidecar is expected to have.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ShapeError {
    /// The process the host started is not among the processes described.
    LauncherMissing,
    /// The process the host started is not `python.exe` (the venv launcher).
    LauncherNotPython { exe: String },
    /// A process other than the launcher whose parent is not the launcher.
    NotAChildOfTheLauncher { pid: u32, parent_pid: u32 },
    /// A child of the launcher that is neither `python.exe` nor `conhost.exe`.
    UnexpectedProcess { pid: u32, exe: String },
    /// No `python.exe` child: the real interpreter is not in the job.
    InterpreterMissing,
    /// More than one `python.exe` child.
    TooManyInterpreters { count: usize },
    /// More than one `conhost.exe` child.
    TooManyConsoleHosts { count: usize },
}

fn is_image(process: &ProcessInfo, name: &str) -> bool {
    process.exe.eq_ignore_ascii_case(name)
}

/// The shape of the DEV sidecar's job on Windows (ADR-004): exactly the launcher (`python.exe`, the
/// process the host started), exactly ONE `python.exe` that is its child (the real interpreter), and
/// AT MOST one `conhost.exe` that is its child. The console host is allowed but not required: it
/// appears because `CREATE_NO_WINDOW` still allocates a hidden console, and a different runner may
/// not start one. Anything else, or a process whose parent is not the launcher, fails.
///
/// This describes the venv launcher setup only. The packaged sidecar (M8) has another shape and
/// gets its own check.
pub fn check_venv_sidecar_shape(
    launcher_pid: u32,
    processes: &[ProcessInfo],
) -> Result<(), ShapeError> {
    let launcher = processes
        .iter()
        .find(|process| process.pid == launcher_pid)
        .ok_or(ShapeError::LauncherMissing)?;
    if !is_image(launcher, "python.exe") {
        return Err(ShapeError::LauncherNotPython {
            exe: launcher.exe.clone(),
        });
    }

    let (mut interpreters, mut console_hosts) = (0usize, 0usize);
    for process in processes
        .iter()
        .filter(|process| process.pid != launcher_pid)
    {
        if process.parent_pid != launcher_pid {
            return Err(ShapeError::NotAChildOfTheLauncher {
                pid: process.pid,
                parent_pid: process.parent_pid,
            });
        }
        if is_image(process, "python.exe") {
            interpreters += 1;
        } else if is_image(process, "conhost.exe") {
            console_hosts += 1;
        } else {
            return Err(ShapeError::UnexpectedProcess {
                pid: process.pid,
                exe: process.exe.clone(),
            });
        }
    }
    match (interpreters, console_hosts) {
        (0, _) => Err(ShapeError::InterpreterMissing),
        (1, 0 | 1) => Ok(()),
        (count, _) if count > 1 => Err(ShapeError::TooManyInterpreters { count }),
        (_, count) => Err(ShapeError::TooManyConsoleHosts { count }),
    }
}

/// How long the sidecar's job may take to settle into the expected shape. The real sidecar's
/// interpreter starts a short-lived `cmd.exe` through the standard library's `platform` module
/// while uvicorn starts (ADR-004), and a slow runner can still have it alive when the ready line
/// arrives. A process that is still there after this bound is not transient.
pub const SHAPE_SETTLE_BOUND: Duration = Duration::from_secs(10);

/// Pause between two reads of the job. This paces a bounded poll on a real condition (there is no
/// event to wait on for "this child has exited"); it is not a guess about timing.
const SHAPE_POLL_PAUSE: Duration = Duration::from_millis(25);

/// The sidecar's processes never took the expected shape within the bound.
#[derive(Debug)]
pub enum ShapeWaitError {
    /// The job or the process snapshot could not be read at all. Not retried.
    ReadFailed(String),
    /// Still the wrong shape at the end of the bound. Carries the LAST list, with every process's
    /// id, parent and image name, so a failure can be read without a second run.
    NeverSettled {
        attempts: usize,
        last_error: ShapeError,
        last_processes: Vec<ProcessInfo>,
    },
}

impl std::fmt::Display for ShapeWaitError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ReadFailed(reason) => {
                write!(f, "could not read the sidecar's processes: {reason}")
            }
            Self::NeverSettled {
                attempts,
                last_error,
                last_processes,
            } => {
                write!(
                    f,
                    "the sidecar's processes were not the expected shape after {attempts} reads \
                     ({last_error:?}); last list:"
                )?;
                for process in last_processes {
                    write!(
                        f,
                        " [pid {} {} parent {}]",
                        process.pid, process.exe, process.parent_pid
                    )?;
                }
                Ok(())
            }
        }
    }
}

impl std::error::Error for ShapeWaitError {}

/// Read the job's processes with `read` until [`check_venv_sidecar_shape`] accepts them or `bound`
/// has passed, and return how many reads that took. At least one read always happens. A read that
/// fails is an error at once; a read that gives the wrong shape is retried, so a short-lived extra
/// process is tolerated, while one that is still there at the end of the bound fails.
pub fn wait_for_venv_sidecar_shape(
    launcher_pid: u32,
    bound: Duration,
    mut read: impl FnMut() -> Result<Vec<ProcessInfo>, Box<dyn Error>>,
) -> Result<usize, ShapeWaitError> {
    let deadline = Instant::now() + bound;
    let mut attempts = 0;
    loop {
        let processes = read().map_err(|error| ShapeWaitError::ReadFailed(error.to_string()))?;
        attempts += 1;
        match check_venv_sidecar_shape(launcher_pid, &processes) {
            Ok(()) => return Ok(attempts),
            Err(last_error) => {
                if Instant::now() >= deadline {
                    return Err(ShapeWaitError::NeverSettled {
                        attempts,
                        last_error,
                        last_processes: processes,
                    });
                }
            }
        }
        thread::sleep(SHAPE_POLL_PAUSE);
    }
}
