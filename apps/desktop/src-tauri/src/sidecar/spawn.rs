//! Starting the sidecar inside the job (M2 plan sections 3 and 5, ADR-004).
//!
//! The order matters. The process is created SUSPENDED, assigned to the job while no thread of it
//! has run, and only then resumed. A process created and assigned afterwards could already have
//! started its own children (the venv launcher starts the real interpreter in its first
//! milliseconds), and a child created before the assignment is not retroactively in the job.
//!
//! Resuming: std does not hand back the initial thread, so this module does not try to identify a
//! "main" thread. It takes a snapshot of the process's threads, resumes EVERY one, and judges the
//! previous suspend counts with [`check_resume_counts`]. Anything unexpected fails closed: the job
//! is terminated and the start fails.
//!
//! One of only two modules allowed to use `unsafe` (the other is `job.rs`).

#![allow(
    unsafe_code,
    reason = "Win32 thread and system directory calls; every unsafe block carries a SAFETY comment (ADR-004)"
)]

use std::ffi::OsString;
use std::mem::size_of;
use std::os::windows::ffi::OsStringExt;
use std::os::windows::io::{AsHandle, AsRawHandle};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};

use windows::Win32::Foundation::HANDLE;
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows::Win32::System::SystemInformation::GetSystemWindowsDirectoryW;
use windows::Win32::System::Threading::{
    GetProcessIdOfThread, OpenThread, ResumeThread, CREATE_NO_WINDOW, CREATE_SUSPENDED,
    THREAD_QUERY_LIMITED_INFORMATION, THREAD_SUSPEND_RESUME,
};

use super::job::{own_handle, Job, EMPTY_WAIT_BOUND};
use super::supervisor::SidecarSpec;

/// Why the Windows directory could not be determined.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum SpawnError {
    #[error("the Windows directory could not be determined")]
    SystemRootUnavailable,
}

/// Why the sidecar could not be started. Unit variants only.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum StartError {
    #[error("the sidecar process could not be created")]
    SpawnFailed,
    #[error("the job object could not be created")]
    JobFailed,
    #[error("the sidecar process could not be assigned to the job object")]
    JobAssignFailed,
    #[error("the sidecar's threads were not in the expected suspended state")]
    UnexpectedThreadState,
    #[error("the sidecar's threads or supervision threads could not be started")]
    ThreadStartFailed,
}

/// The suspend counts reported by `ResumeThread` broke the rule in [`check_resume_counts`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
#[error("the sidecar's threads were not in the expected suspended state")]
pub(crate) struct UnexpectedThreadState;

/// `ResumeThread` returns this when it fails. It is also what this module reports for a thread that
/// could not be opened or that does not belong to the child.
const RESUME_FAILED: u32 = u32::MAX;

/// The Windows directory from the operating system (`GetSystemWindowsDirectoryW`), never from the
/// parent's environment, so an attacker-influenced parent environment cannot change `SYSTEMROOT`.
pub fn system_windows_directory() -> Result<PathBuf, SpawnError> {
    let mut buffer = vec![0u16; 260];
    for _ in 0..2 {
        // SAFETY: the binding passes the slice's pointer and length together, `buffer` is writable
        // and exclusively borrowed for the call, and the function writes at most `len` UTF-16 units.
        let written = unsafe { GetSystemWindowsDirectoryW(Some(&mut buffer)) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- GetSystemWindowsDirectoryW; SAFETY above, ADR-004
        let written = usize::try_from(written).map_err(|_| SpawnError::SystemRootUnavailable)?;
        if written == 0 {
            return Err(SpawnError::SystemRootUnavailable);
        }
        if written >= buffer.len() {
            // Too small: `written` is the size needed, terminator included. Retry once with it.
            buffer = vec![0u16; written + 1];
            continue;
        }
        let text = buffer
            .get(..written)
            .ok_or(SpawnError::SystemRootUnavailable)?;
        return Ok(PathBuf::from(OsString::from_wide(text)));
    }
    Err(SpawnError::SystemRootUnavailable)
}

/// The rule for the previous suspend counts of every resumed thread: each must be 0 or 1 (a
/// security product may inject a thread that is already running, count 0), and at least one must be
/// exactly 1 (the thread this host created suspended). No threads, an error value, a count of 2 or
/// more, or "nobody was suspended" all fail.
pub(crate) fn check_resume_counts(counts: &[u32]) -> Result<(), UnexpectedThreadState> {
    let all_plausible = counts.iter().all(|count| *count <= 1);
    let one_was_suspended = counts.contains(&1);
    if all_plausible && one_was_suspended {
        Ok(())
    } else {
        Err(UnexpectedThreadState)
    }
}

/// Resume EVERY thread in `ids` (never stopping at the first bad one), collecting each previous
/// suspend count, and only then apply [`check_resume_counts`]. `resume` returns the previous count,
/// or `u32::MAX` on failure. Generic so tests can inject a fake and prove that every thread was
/// resumed.
pub(crate) fn resume_threads_with(
    ids: &[u32],
    mut resume: impl FnMut(u32) -> u32,
) -> Result<(), UnexpectedThreadState> {
    let counts: Vec<u32> = ids.iter().map(|id| resume(*id)).collect();
    check_resume_counts(&counts)
}

/// Start the sidecar suspended, put it in `job`, resume it, and return the child.
///
/// The `Command` is built and spawned inside this one function and never stored anywhere, so no
/// struct in the crate can be accidentally Debug-printed with the token in its environment
/// (ADR-004 review rule). Failure handling:
/// * assignment fails: the child is still suspended and not in a job, so `Child::kill` is the only
///   possible mechanism; it never runs unconfined;
/// * resuming breaks the rule: the whole job is terminated and waited for.
pub fn spawn_in_job(spec: &SidecarSpec, job: &Job) -> Result<Child, StartError> {
    let mut command = Command::new(spec.program());
    command.env_clear();
    for (key, value) in spec.env().iter() {
        command.env(key, value);
    }
    command
        .args(spec.args())
        .current_dir(spec.working_dir())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(CREATE_SUSPENDED.0 | CREATE_NO_WINDOW.0);

    let mut child = command.spawn().map_err(|_| StartError::SpawnFailed)?;

    if job.assign(child.as_handle()).is_err() {
        // Best effort: the child is suspended and outside any job, so there is nothing else to do.
        let _ = child.kill();
        let _ = child.wait();
        return Err(StartError::JobAssignFailed);
    }

    if let Err(error) = resume_all_threads(child.id()) {
        let _ = job.terminate();
        let _ = job.wait_until_empty(EMPTY_WAIT_BOUND);
        let _ = child.wait();
        return Err(error);
    }
    Ok(child)
}

fn resume_all_threads(pid: u32) -> Result<(), StartError> {
    let ids = thread_ids_of(pid).ok_or(StartError::ThreadStartFailed)?;
    resume_threads_with(&ids, |thread_id| resume_one(pid, thread_id))
        .map_err(|UnexpectedThreadState| StartError::UnexpectedThreadState)
}

/// Ids of every thread whose owner is `pid`, from a thread snapshot. `None` if the snapshot could
/// not be taken. A list that comes back empty is rejected later by [`check_resume_counts`].
fn thread_ids_of(pid: u32) -> Option<Vec<u32>> {
    // SAFETY: no pointer is involved; the arguments are a flag and 0 ("all processes"), which is
    // what TH32CS_SNAPTHREAD requires.
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- CreateToolhelp32Snapshot; SAFETY above, ADR-004
    let snapshot = own_handle(snapshot.ok()?)?;
    let handle = HANDLE(snapshot.as_raw_handle());

    let entry_size = u32::try_from(size_of::<THREADENTRY32>()).ok()?;
    let mut entry = THREADENTRY32 {
        dwSize: entry_size,
        ..THREADENTRY32::default()
    };
    let mut ids = Vec::new();

    // SAFETY: `handle` stays valid while `snapshot` lives (it is not moved or dropped until the end
    // of this function). `entry` is a live, writable `THREADENTRY32` whose `dwSize` is set to its
    // size, as the API requires before the first call.
    let mut more = unsafe { Thread32First(handle, &mut entry) }.is_ok(); // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- Thread32First; SAFETY above, ADR-004
    while more {
        if entry.th32OwnerProcessID == pid {
            ids.push(entry.th32ThreadID);
        }
        entry.dwSize = entry_size;
        // SAFETY: same as above; `dwSize` was reset to the structure size for this call.
        more = unsafe { Thread32Next(handle, &mut entry) }.is_ok(); // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- Thread32Next; SAFETY above, ADR-004
    }
    Some(ids)
}

/// Resume one thread and return its previous suspend count, or `RESUME_FAILED`.
fn resume_one(pid: u32, thread_id: u32) -> u32 {
    let rights = THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION;
    // SAFETY: no pointer is involved; the arguments are an access mask, a flag and a thread id.
    let opened = unsafe { OpenThread(rights, false, thread_id) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- OpenThread; SAFETY above, ADR-004
    let Ok(opened) = opened else {
        return RESUME_FAILED;
    };
    let Some(thread) = own_handle(opened) else {
        return RESUME_FAILED;
    };
    let handle = HANDLE(thread.as_raw_handle());

    // SAFETY: `handle` is an owned, valid thread handle opened with QUERY_LIMITED_INFORMATION and
    // alive for the call (`thread` is dropped at the end of the function).
    let owner = unsafe { GetProcessIdOfThread(handle) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- GetProcessIdOfThread; SAFETY above, ADR-004
    if owner != pid {
        // A thread id from the snapshot was reused by another process before it was opened.
        // Fail closed rather than resume somebody else's thread.
        return RESUME_FAILED;
    }
    // SAFETY: `handle` is an owned, valid thread handle opened with SUSPEND_RESUME access, alive for
    // the call, and was just verified to belong to the child. `u32::MAX` means failure, which
    // `check_resume_counts` rejects.
    unsafe { ResumeThread(handle) } // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- ResumeThread; SAFETY above, ADR-004
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plausible_suspend_counts_are_accepted() {
        for counts in [&[1][..], &[1, 0], &[0, 1], &[1, 1], &[0, 0, 1]] {
            assert_eq!(check_resume_counts(counts), Ok(()), "{counts:?}");
        }
    }

    #[test]
    fn implausible_suspend_counts_are_rejected() {
        for counts in [
            &[][..],
            &[0],
            &[0, 0],
            &[2],
            &[1, 2],
            &[u32::MAX],
            &[1, u32::MAX],
            &[3, 1],
        ] {
            assert_eq!(
                check_resume_counts(counts),
                Err(UnexpectedThreadState),
                "{counts:?}"
            );
        }
    }

    #[test]
    fn every_thread_is_resumed_even_when_an_earlier_count_is_bad() {
        let mut resumed = Vec::new();
        // The first thread reports 2 (invalid), the others 1 and 0. All three must be resumed.
        let result = resume_threads_with(&[10, 11, 12], |id| {
            resumed.push(id);
            match id {
                10 => 2,
                11 => 1,
                _ => 0,
            }
        });
        assert_eq!(result, Err(UnexpectedThreadState));
        assert_eq!(resumed, vec![10, 11, 12]);
    }

    #[test]
    fn every_thread_is_resumed_on_success_too() {
        let mut resumed = Vec::new();
        let result = resume_threads_with(&[5, 6], |id| {
            resumed.push(id);
            u32::from(id == 5)
        });
        assert_eq!(result, Ok(()));
        assert_eq!(resumed, vec![5, 6]);
    }

    #[test]
    fn no_threads_at_all_is_rejected_without_calling_resume() {
        let result = resume_threads_with(&[], |_| unreachable!("no thread to resume"));
        assert_eq!(result, Err(UnexpectedThreadState));
    }

    #[test]
    fn the_windows_directory_comes_from_the_operating_system() {
        let directory = system_windows_directory().unwrap();
        assert!(directory.is_absolute());
        assert!(directory.is_dir());
        assert!(directory.join("System32").is_dir());
    }

    #[test]
    fn errors_have_fixed_messages_without_detail() {
        for text in [
            StartError::SpawnFailed.to_string(),
            StartError::JobFailed.to_string(),
            StartError::JobAssignFailed.to_string(),
            StartError::UnexpectedThreadState.to_string(),
            StartError::ThreadStartFailed.to_string(),
            SpawnError::SystemRootUnavailable.to_string(),
            UnexpectedThreadState.to_string(),
        ] {
            assert!(!text.is_empty());
            assert!(!text.contains("C:\\"), "a path leaked into: {text}");
        }
    }
}
