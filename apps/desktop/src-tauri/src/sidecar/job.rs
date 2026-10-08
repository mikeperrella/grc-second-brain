//! The Windows Job Object that ties the sidecar's life to the host's (M2 plan section 3, ADR-004).
//!
//! A job is configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and NOTHING else. Closing the last
//! handle to the job therefore terminates every process in it, even if the host is force-killed
//! (the operating system closes its handles at process exit). With neither breakaway flag set, no
//! descendant can leave the job, so a sidecar that starts its own children (the venv launcher and
//! the real interpreter, a PyInstaller bootstrap) cannot orphan them.
//!
//! "Is the job empty" is answered by `QueryInformationJobObject(JobObjectBasicAccountingInformation)`
//! and nothing else. Microsoft documents that completion-port messages are not guaranteed to be
//! delivered, so the port is used only as a way to wake up early; the query decides.
//!
//! This is one of only two modules allowed to use `unsafe` (the other is `spawn.rs`). Every Win32
//! handle is wrapped in a `std::os::windows::io::OwnedHandle` the moment it is created, so closing
//! is automatic, no handle is closed by hand, and `Job` is `Send + Sync` without any `unsafe impl`.

#![allow(
    unsafe_code,
    reason = "Win32 Job Object calls; every unsafe block carries a SAFETY comment (ADR-004)"
)]

use std::mem::size_of;
use std::os::windows::io::{AsRawHandle, BorrowedHandle, FromRawHandle, OwnedHandle};
use std::ptr;
use std::time::{Duration, Instant};

use windows::core::{BOOL, PCWSTR};
use windows::Win32::Foundation::{
    ERROR_INVALID_PARAMETER, HANDLE, INVALID_HANDLE_VALUE, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob,
    JobObjectAssociateCompletionPortInformation, JobObjectBasicAccountingInformation,
    JobObjectBasicProcessIdList, JobObjectBasicUIRestrictions, JobObjectExtendedLimitInformation,
    QueryInformationJobObject, SetInformationJobObject, TerminateJobObject, JOBOBJECTINFOCLASS,
    JOBOBJECT_ASSOCIATE_COMPLETION_PORT, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
    JOBOBJECT_BASIC_UI_RESTRICTIONS, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{
    GetCurrentProcess, OpenProcess, TerminateProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE,
    PROCESS_TERMINATE,
};
use windows::Win32::System::IO::{CreateIoCompletionPort, GetQueuedCompletionStatus, OVERLAPPED};

/// How long [`Job::wait_until_empty`] is given by the supervisor after a termination. `pub`:
/// tests/job_object.rs uses it as the bound for its own waits.
pub const EMPTY_WAIT_BOUND: Duration = Duration::from_secs(5);

/// Longest single wait on the completion port. The port only wakes the loop early; the
/// authoritative query runs after every wake-up and every slice. This is pacing, not a sleep: the
/// wait returns as soon as the port has a message.
const PORT_SLICE: Duration = Duration::from_millis(100);

/// Why a job operation failed. Unit variants: no OS error text, no handle values, nothing that
/// could carry a secret. `pub` because it appears in the signatures of the `pub` items below.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum JobError {
    #[error("the job object could not be created")]
    CreateFailed,
    #[error("the job object could not be configured")]
    ConfigureFailed,
    #[error("the process could not be assigned to the job object")]
    AssignFailed,
    #[error("the job object could not be terminated")]
    TerminateFailed,
    #[error("the job object could not be queried")]
    QueryFailed,
    #[error("the job object still held processes after the time limit")]
    NotEmptyInTime,
}

/// Information classes this module sets or queries. The associated constant ties each struct to
/// the class Windows expects for it, so a class and a buffer type can never be mismatched.
trait JobInfo: Default {
    const CLASS: JOBOBJECTINFOCLASS;
}

impl JobInfo for JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
    const CLASS: JOBOBJECTINFOCLASS = JobObjectExtendedLimitInformation;
}
impl JobInfo for JOBOBJECT_BASIC_ACCOUNTING_INFORMATION {
    const CLASS: JOBOBJECTINFOCLASS = JobObjectBasicAccountingInformation;
}
impl JobInfo for JOBOBJECT_BASIC_UI_RESTRICTIONS {
    const CLASS: JOBOBJECTINFOCLASS = JobObjectBasicUIRestrictions;
}
impl JobInfo for JOBOBJECT_ASSOCIATE_COMPLETION_PORT {
    const CLASS: JOBOBJECTINFOCLASS = JobObjectAssociateCompletionPortInformation;
}

/// Take ownership of a handle a Win32 call just returned. `None` for a null or invalid handle.
pub(crate) fn own_handle(handle: HANDLE) -> Option<OwnedHandle> {
    if handle.is_invalid() {
        return None;
    }
    // SAFETY: `handle` was just returned by a successful Win32 call and was checked above to be
    // neither null nor INVALID_HANDLE_VALUE. Nothing else owns it or will close it, so
    // `OwnedHandle` may take sole ownership and close it exactly once, when dropped.
    Some(unsafe { OwnedHandle::from_raw_handle(handle.0) }) // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- OwnedHandle::from_raw_handle; SAFETY above, ADR-004
}

/// The `windows` crate's view of a handle owned by an `OwnedHandle`. Borrowing only: the returned
/// value must not outlive the owner, which every caller guarantees by using it immediately.
fn raw(handle: &OwnedHandle) -> HANDLE {
    HANDLE(handle.as_raw_handle())
}

fn millis(duration: Duration) -> u32 {
    u32::try_from(duration.as_millis()).unwrap_or(u32::MAX)
}

/// A Job Object plus the completion port used to wake [`Job::wait_until_empty`] early. `pub`:
/// tests/job_object.rs builds jobs directly; the app only reaches one through the supervisor.
pub struct Job {
    job: OwnedHandle,
    port: OwnedHandle,
}

impl Job {
    /// An unnamed job with `KILL_ON_JOB_CLOSE` as its only limit, and a completion port associated
    /// with it BEFORE any process is assigned (Microsoft recommends associating while inactive).
    pub fn new() -> Result<Self, JobError> {
        Self::create(true)
    }

    /// TEST ONLY. Like [`Job::new`] but the port is created and never associated, so no message
    /// ever arrives. A test uses it to prove that the authoritative query, not the port, decides
    /// that the job is empty.
    #[doc(hidden)]
    pub fn new_without_port_association() -> Result<Self, JobError> {
        Self::create(false)
    }

    fn create(associate_port: bool) -> Result<Self, JobError> {
        // SAFETY: both arguments are documented as optional. `None` selects default security
        // attributes and a null name creates an unnamed job, which no other process can open. No
        // pointer is read by the call.
        let created = unsafe { CreateJobObjectW(None, PCWSTR::null()) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- CreateJobObjectW; SAFETY above, ADR-004
        let job = created
            .ok()
            .and_then(own_handle)
            .ok_or(JobError::CreateFailed)?;

        // The only limit: kill everything when the last handle closes. No breakaway flag.
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

        // SAFETY: `INVALID_HANDLE_VALUE` with no existing port is the documented way to create a
        // new completion port, and the other arguments are plain integers. No pointer is read.
        let port = unsafe { CreateIoCompletionPort(INVALID_HANDLE_VALUE, None, 0, 1) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- CreateIoCompletionPort; SAFETY above, ADR-004
        let port = port
            .ok()
            .and_then(own_handle)
            .ok_or(JobError::CreateFailed)?;

        let this = Self { job, port };
        this.set(&limits)?;
        if associate_port {
            this.set(&JOBOBJECT_ASSOCIATE_COMPLETION_PORT {
                CompletionKey: ptr::null_mut(),
                CompletionPort: raw(&this.port),
            })?;
        }
        Ok(this)
    }

    fn set<T: JobInfo>(&self, info: &T) -> Result<(), JobError> {
        let length = u32::try_from(size_of::<T>()).map_err(|_| JobError::ConfigureFailed)?;
        let job = raw(&self.job);
        let buffer: *const core::ffi::c_void = ptr::from_ref(info).cast();
        // SAFETY: `job` is a valid job handle for the whole call because `self` is borrowed.
        // `buffer` points to the live, fully initialised `T` behind `info`, which outlives the
        // call, `T::CLASS` is the class that goes with `T` (the `JobInfo` impls tie them
        // together), and `length` is exactly `size_of::<T>()`. The call only reads from the buffer.
        // For the port association the port handle inside `info` belongs to this same `Job` and
        // stays open as long as the job does.
        let result = unsafe { SetInformationJobObject(job, T::CLASS, buffer, length) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- SetInformationJobObject; SAFETY above, ADR-004
        result.map_err(|_| JobError::ConfigureFailed)
    }

    fn query<T: JobInfo>(&self) -> Result<T, JobError> {
        let mut out = T::default();
        let length = u32::try_from(size_of::<T>()).map_err(|_| JobError::QueryFailed)?;
        let job = Some(raw(&self.job));
        let buffer: *mut core::ffi::c_void = ptr::from_mut(&mut out).cast();
        // SAFETY: `job` is a valid job handle for the whole call. `buffer` points to `out`, an
        // initialised `T` of exactly `length` bytes that is writable for the call and not touched
        // until it returns, and `T::CLASS` matches `T`. The optional return-length pointer is `None`.
        let result = unsafe { QueryInformationJobObject(job, T::CLASS, buffer, length, None) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- QueryInformationJobObject; SAFETY above, ADR-004
        result.map_err(|_| JobError::QueryFailed)?;
        Ok(out)
    }

    /// Put a process into the job. Only `spawn.rs` calls this, while the process is still suspended.
    pub(crate) fn assign(&self, process: BorrowedHandle<'_>) -> Result<(), JobError> {
        // SAFETY: `self.job` is owned by `self` and valid for the call. `process` is a
        // `BorrowedHandle`, so the caller keeps the process handle open for at least this call.
        // This function closes neither handle.
        let result =
            unsafe { AssignProcessToJobObject(raw(&self.job), HANDLE(process.as_raw_handle())) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- AssignProcessToJobObject; SAFETY above, ADR-004
        result.map_err(|_| JobError::AssignFailed)
    }

    /// Terminate every process in the job (exit code 1). Succeeds on an empty job. `pub`:
    /// tests/job_object.rs terminates a job it built itself.
    pub fn terminate(&self) -> Result<(), JobError> {
        // SAFETY: `self.job` is a valid job handle owned by `self`; the other argument is a plain
        // exit code. No pointer is involved.
        let result = unsafe { TerminateJobObject(raw(&self.job), 1) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- TerminateJobObject; SAFETY above, ADR-004
        result.map_err(|_| JobError::TerminateFailed)
    }

    /// Number of processes currently in the job (the authoritative "is it empty" answer). `pub`:
    /// tests/job_object.rs reads it directly.
    pub fn active_processes(&self) -> Result<u32, JobError> {
        let info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = self.query()?;
        Ok(info.ActiveProcesses)
    }

    /// Wait until the job holds no process, for at most `bound`.
    ///
    /// Every pass asks the operating system how many processes are active. If any are left, the
    /// loop waits on the completion port for a message, but never longer than [`PORT_SLICE`] or
    /// the time that remains, and asks again whatever the wait returned: a message, a timeout and
    /// an error all mean "ask again". A lost message therefore costs one slice, never a hang or a
    /// wrong answer, and there is no `sleep`. `pub`: tests/job_object.rs waits on its own jobs.
    pub fn wait_until_empty(&self, bound: Duration) -> Result<(), JobError> {
        let deadline = Instant::now() + bound;
        loop {
            if self.active_processes()? == 0 {
                return Ok(());
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(JobError::NotEmptyInTime);
            }
            self.wait_on_port(remaining.min(PORT_SLICE));
        }
    }

    fn wait_on_port(&self, slice: Duration) {
        let port = raw(&self.port);
        let ms = millis(slice);
        let mut n = 0u32;
        let mut key = 0usize;
        let mut ov: *mut OVERLAPPED = ptr::null_mut();
        // SAFETY: `port` is a valid completion port handle for the call. All three out parameters
        // (`n`, `key`, `ov`) point to live locals that the call may write. For job notifications
        // `ov` receives a process id cast to a pointer, NOT a real pointer, and it is never
        // dereferenced here. The result and the values are ignored on purpose: a message, a
        // timeout and an error all mean "ask again" (see `wait_until_empty`).
        let result = unsafe { GetQueuedCompletionStatus(port, &mut n, &mut key, &mut ov, ms) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- GetQueuedCompletionStatus; SAFETY above, ADR-004
        let _ = result;
    }

    /// Process ids currently in the job. `pub`: the supervisor exposes it and the tests (fake
    /// sidecar, and tests/real_sidecar.rs for the "launcher plus real interpreter" check) read it.
    pub fn process_ids(&self) -> Result<Vec<u32>, JobError> {
        // Layout of JOBOBJECT_BASIC_PROCESS_ID_LIST on 64-bit Windows: two u32 counts (8 bytes)
        // followed by the ids as pointer-sized values. A `Vec<usize>` is correctly aligned for it.
        // `sidecar/mod.rs` refuses to compile for 32-bit targets, where the layout differs.
        const HEADER_WORDS: usize = 1;
        let mut capacity = 16usize;
        for _ in 0..7 {
            let mut words = vec![0usize; HEADER_WORDS + capacity];
            let byte_length = u32::try_from(words.len() * size_of::<usize>())
                .map_err(|_| JobError::QueryFailed)?;
            let job = Some(raw(&self.job));
            let class = JobObjectBasicProcessIdList;
            let buffer: *mut core::ffi::c_void = words.as_mut_ptr().cast();
            // SAFETY: `job` is a valid job handle for the call. `buffer` points to `words`, a
            // writable buffer of exactly `byte_length` bytes, aligned for the id list structure
            // (pointer-sized words), which stays alive and unmoved during the call.
            // `JobObjectBasicProcessIdList` expects exactly such a header-plus-array buffer. The
            // return-length pointer is `None`.
            let result =
                unsafe { QueryInformationJobObject(job, class, buffer, byte_length, None) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- QueryInformationJobObject (process id list); SAFETY above, ADR-004
            if result.is_err() {
                // ERROR_MORE_DATA: the job holds more ids than the buffer. Try a bigger one.
                capacity *= 2;
                continue;
            }
            let counts = words.first().copied().ok_or(JobError::QueryFailed)?;
            // The high half of the first word is `NumberOfProcessIdsInList` (little-endian, 64-bit).
            let listed = counts >> 32;
            let ids = words
                .get(HEADER_WORDS..HEADER_WORDS + listed.min(capacity))
                .ok_or(JobError::QueryFailed)?;
            return ids
                .iter()
                .map(|id| u32::try_from(*id).map_err(|_| JobError::QueryFailed))
                .collect();
        }
        Err(JobError::QueryFailed)
    }

    /// The job's `LimitFlags`, for the test that pins them to exactly `KILL_ON_JOB_CLOSE`.
    pub fn limit_flags(&self) -> Result<u32, JobError> {
        let info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = self.query()?;
        Ok(info.BasicLimitInformation.LimitFlags.0)
    }

    /// The job's UI restriction flags (expected 0: none are set).
    pub fn ui_restrictions(&self) -> Result<u32, JobError> {
        let info: JOBOBJECT_BASIC_UI_RESTRICTIONS = self.query()?;
        Ok(info.UIRestrictionsClass.0)
    }
}

// ---------------------------------------------------------------------------------------------
// Safe helpers for tests. Integration tests contain no `unsafe` (M2 plan section 2), so the few
// process and handle operations they need live here as safe functions.
// ---------------------------------------------------------------------------------------------

/// Result of [`wait_for_process_exit`]. `pub` because that test helper returns it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProcessWait {
    Exited,
    StillRunning,
}

/// Open a process by id, or report that no such process exists any more.
fn open_process(
    access: windows::Win32::System::Threading::PROCESS_ACCESS_RIGHTS,
    pid: u32,
) -> Result<Option<OwnedHandle>, JobError> {
    // SAFETY: no pointer is involved; the arguments are an access mask, a flag and a process id.
    let opened = unsafe { OpenProcess(access, false, pid) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- OpenProcess; SAFETY above, ADR-004
    match opened {
        Ok(handle) => own_handle(handle).map(Some).ok_or(JobError::QueryFailed),
        // ERROR_INVALID_PARAMETER is what Windows returns for a process id that does not exist.
        Err(error) if error.code() == ERROR_INVALID_PARAMETER.to_hresult() => Ok(None),
        Err(_) => Err(JobError::QueryFailed),
    }
}

/// Wait for the process with this id to exit, for at most `bound`. A process that no longer exists
/// counts as exited. Test helper. Caveat: a process id can be reused after exit, so call it only
/// for processes started by the test, shortly after they were killed.
pub fn wait_for_process_exit(pid: u32, bound: Duration) -> Result<ProcessWait, JobError> {
    let Some(handle) = open_process(PROCESS_SYNCHRONIZE, pid)? else {
        return Ok(ProcessWait::Exited);
    };
    // SAFETY: `handle` is an owned, valid process handle opened with SYNCHRONIZE access, alive for
    // the whole call, and the wait is bounded by the timeout. No pointer is involved.
    let outcome = unsafe { WaitForSingleObject(raw(&handle), millis(bound)) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- WaitForSingleObject; SAFETY above, ADR-004
    if outcome == WAIT_OBJECT_0 {
        Ok(ProcessWait::Exited)
    } else if outcome == WAIT_TIMEOUT {
        Ok(ProcessWait::StillRunning)
    } else {
        Err(JobError::QueryFailed)
    }
}

/// Terminate a process by id. Test cleanup guards use it so a failing test cannot leave an orphan.
/// A process that no longer exists is not an error.
pub fn kill_process_by_pid(pid: u32) -> Result<(), JobError> {
    let Some(handle) = open_process(PROCESS_TERMINATE, pid)? else {
        return Ok(());
    };
    // SAFETY: `handle` is an owned, valid process handle opened with TERMINATE access and alive for
    // the call; the exit code is a plain integer. No pointer is involved.
    let result = unsafe { TerminateProcess(raw(&handle), 1) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- TerminateProcess; SAFETY above, ADR-004
    result.map_err(|_| JobError::TerminateFailed)
}

/// A plain job with no limits that the current process has been placed in. Dropping it does not
/// kill anything (no KILL_ON_JOB_CLOSE), so it is harmless. Used by the nested-job test.
pub struct OuterJob(#[allow(dead_code, reason = "held so the handle stays open")] OwnedHandle);

/// Put the CURRENT process into a new job without limits, to reproduce running inside a terminal,
/// IDE or CI job. The effect lasts until the process exits.
pub fn place_current_process_in_plain_job() -> Result<OuterJob, JobError> {
    // SAFETY: same as in `Job::create`: optional arguments, default security, unnamed job.
    let created = unsafe { CreateJobObjectW(None, PCWSTR::null()) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- CreateJobObjectW (test helper); SAFETY above, ADR-004
    let job = created
        .ok()
        .and_then(own_handle)
        .ok_or(JobError::CreateFailed)?;
    // SAFETY: `GetCurrentProcess` returns a pseudo-handle that is always valid in this process and
    // needs no closing. `job` is an owned, valid job handle.
    let result = unsafe { AssignProcessToJobObject(raw(&job), GetCurrentProcess()) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- AssignProcessToJobObject with GetCurrentProcess; SAFETY above, ADR-004
    result.map_err(|_| JobError::AssignFailed)?;
    Ok(OuterJob(job))
}

/// Whether the current process is in any job.
pub fn current_process_is_in_a_job() -> Result<bool, JobError> {
    let mut in_job = BOOL(0);
    // SAFETY: `GetCurrentProcess` returns an always-valid pseudo-handle. `None` for the job means
    // "any job". `in_job` is a live local that the call writes.
    let result = unsafe { IsProcessInJob(GetCurrentProcess(), None, &mut in_job) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- IsProcessInJob; SAFETY above, ADR-004
    result.map_err(|_| JobError::QueryFailed)?;
    Ok(in_job.as_bool())
}

/// What a process snapshot knows about one process. `pub` because [`describe_processes`] returns
/// it. The image name is the short file name only (`python.exe`), never a path, so it is safe to
/// print in a test failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProcessInfo {
    pub pid: u32,
    pub parent_pid: u32,
    pub exe: String,
}

/// The text of a null-terminated UTF-16 buffer such as `PROCESSENTRY32W::szExeFile`. Without a
/// terminator the whole buffer is used.
fn wide_to_string(units: &[u16]) -> String {
    let end = units
        .iter()
        .position(|unit| *unit == 0)
        .unwrap_or(units.len());
    String::from_utf16_lossy(units.get(..end).unwrap_or(units))
}

/// Id, parent id and image name of each process in `pids`, from one process snapshot. A process
/// that is not in the snapshot (it exited, or the enumeration ended early) is simply absent, so a
/// caller that needs every one compares the lengths. `pub`: tests/real_sidecar.rs and
/// tests/sidecar_shape.rs use it to check WHICH processes the sidecar's job holds, not only how
/// many.
pub fn describe_processes(pids: &[u32]) -> Result<Vec<ProcessInfo>, JobError> {
    // SAFETY: no pointer is involved; the arguments are a flag and 0 ("all processes"), which is
    // what TH32CS_SNAPPROCESS requires.
    let created = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- CreateToolhelp32Snapshot (processes); SAFETY above, ADR-004
    let snapshot = created
        .ok()
        .and_then(own_handle)
        .ok_or(JobError::QueryFailed)?;
    let handle = raw(&snapshot);

    let entry_size =
        u32::try_from(size_of::<PROCESSENTRY32W>()).map_err(|_| JobError::QueryFailed)?;
    let mut entry = PROCESSENTRY32W {
        dwSize: entry_size,
        ..PROCESSENTRY32W::default()
    };
    let mut found = Vec::new();

    // SAFETY: `handle` stays valid while `snapshot` lives (neither moved nor dropped before the end
    // of this function). `entry` is a live, writable `PROCESSENTRY32W` whose `dwSize` is set to its
    // size, as the API requires before the first call.
    let mut more = unsafe { Process32FirstW(handle, &mut entry) }.is_ok(); // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- Process32FirstW; SAFETY above, ADR-004
    while more {
        if pids.contains(&entry.th32ProcessID) {
            found.push(ProcessInfo {
                pid: entry.th32ProcessID,
                parent_pid: entry.th32ParentProcessID,
                exe: wide_to_string(&entry.szExeFile),
            });
        }
        entry.dwSize = entry_size;
        // SAFETY: same as above; `dwSize` was reset to the structure size for this call.
        more = unsafe { Process32NextW(handle, &mut entry) }.is_ok(); // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- Process32NextW; SAFETY above, ADR-004
    }
    Ok(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_wide_buffer_is_read_up_to_its_terminator() {
        let mut buffer = [0u16; 16];
        for (slot, unit) in buffer.iter_mut().zip("python.exe".encode_utf16()) {
            *slot = unit;
        }
        assert_eq!(wide_to_string(&buffer), "python.exe");
        // Text after the first terminator is ignored.
        buffer[11] = u16::from(b'x');
        assert_eq!(wide_to_string(&buffer), "python.exe");
    }

    #[test]
    fn a_buffer_without_a_terminator_is_read_whole_and_an_empty_one_is_empty() {
        let units: Vec<u16> = "conhost.exe".encode_utf16().collect();
        assert_eq!(wide_to_string(&units), "conhost.exe");
        assert_eq!(wide_to_string(&[]), "");
        assert_eq!(wide_to_string(&[0, 0x61]), "");
    }
}
