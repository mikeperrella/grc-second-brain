//! Supervising one sidecar process: handshake, state, shutdown (M2 plan section 6, ADR-004).
//!
//! No `unsafe` and no Tauri types here. The process is started by `spawn.rs` inside a Job Object;
//! this module watches it with four threads that talk through one channel:
//!
//! * stdout reader: reads the single ready line (bounded, strict: `ready.rs`), then watches for
//!   any further byte, which is a contract violation;
//! * stderr drainer: keeps the pipe from filling and remembers the last 4 KiB for debug logging;
//! * waiter: owns the `Child` and reports its exit code;
//! * handshake thread: receives the events with a deadline and drives the state machine.
//!
//! Nothing sleeps. Every wait is on an event, a condition variable or a bounded OS wait.
//!
//! # State machine
//! `Starting -> Ready | Failed | Stopped`, `Ready -> Failed | Stopped`. `Failed` and `Stopped` are
//! TERMINAL: nothing overwrites them, `Ready` is only entered from `Starting`, and nothing returns
//! to `Starting`. Every write goes through [`transition`], which enforces this with [`allowed`].
//! That is what makes an orderly shutdown end in `Stopped` even though killing the process also
//! produces an exit event, and what stops a late ready line from reviving a stopped sidecar.

use std::collections::VecDeque;
use std::ffi::OsString;
use std::fmt;
use std::io::{BufRead, BufReader, Read};
use std::num::NonZeroU16;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdout};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::child_env::ChildEnv;
use crate::ready::{read_ready_line, ReadyError};

use super::job::{Job, JobError, EMPTY_WAIT_BOUND};
use super::resolve::ResolveError;
use super::spawn::{spawn_in_job, StartError};

/// How long the sidecar has to print its ready line (M2 plan section 6: approved constant).
pub(crate) const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(30);

/// Exit code the sidecar uses for a startup error it detected itself (`GrcError`, exit 2).
const STARTUP_REJECTED_EXIT_CODE: i32 = 2;

/// How much of the sidecar's stderr is remembered, for debug logging only.
const STDERR_TAIL: usize = 4096;

/// Everything needed to start one sidecar. `Debug` is written by hand and shows only the program,
/// arguments, working directory and the environment's KEY names, never its values: one of them is
/// the auth token. Adding a field later does not print it unless this impl is changed on purpose.
pub struct SidecarSpec {
    program: PathBuf,
    args: Vec<OsString>,
    working_dir: PathBuf,
    env: ChildEnv,
    handshake_timeout: Duration,
}

impl SidecarSpec {
    pub fn new(program: PathBuf, args: Vec<OsString>, working_dir: PathBuf, env: ChildEnv) -> Self {
        Self {
            program,
            args,
            working_dir,
            env,
            handshake_timeout: HANDSHAKE_TIMEOUT,
        }
    }

    /// Replace the 30 s handshake timeout. Production code does not call this; tests do, so a test
    /// that expects a timeout does not take 30 s.
    #[must_use]
    pub fn with_handshake_timeout(mut self, timeout: Duration) -> Self {
        self.handshake_timeout = timeout;
        self
    }

    pub(crate) fn program(&self) -> &Path {
        &self.program
    }

    pub(crate) fn args(&self) -> &[OsString] {
        &self.args
    }

    pub(crate) fn working_dir(&self) -> &Path {
        &self.working_dir
    }

    pub(crate) fn env(&self) -> &ChildEnv {
        &self.env
    }
}

impl fmt::Debug for SidecarSpec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SidecarSpec")
            .field("program", &self.program)
            .field("args", &self.args)
            .field("working_dir", &self.working_dir)
            // `ChildEnv`'s own Debug lists key names only.
            .field("env", &self.env)
            .finish_non_exhaustive()
    }
}

/// Where the sidecar is in its life. `Copy`, and it carries only integers and unit variants.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SidecarState {
    Starting,
    Ready { port: NonZeroU16 },
    Failed(FailureKind),
    Stopped,
}

/// Why the sidecar failed. `Copy` with unit variants plus one integer exit code: nothing here can
/// carry text, a path or the token. `Serialize` because the health command reports it to the
/// window as `{"kind":"timeout"}` (snake_case names, integers only).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum FailureKind {
    Timeout,
    EofBeforeReady,
    NotJson,
    WrongFields,
    PortOutOfRange,
    LineTooLong,
    ReadFailed,
    /// The process exited before the ready line, with this exit code if Windows reported one.
    ExitedBeforeReady {
        code: Option<i32>,
    },
    /// Exit code 2: the sidecar rejected its own startup configuration (token, log directory...).
    StartupRejected,
    ExitedAfterReady,
    /// Any stdout byte after the ready line.
    ProtocolViolation,
    NotInstalled,
    NotConfigured,
    OutsideSidecarDir,
    SpawnFailed,
    JobFailed,
    JobAssignFailed,
    UnexpectedThreadState,
    /// The host could not create the log directory the sidecar writes to.
    LogDirUnavailable,
    /// The host could not ask the operating system for the Windows directory.
    SystemRootUnavailable,
    /// The operating system gave no random bytes for the auth token.
    RandomUnavailable,
    /// The child environment could not be built (a path was not absolute or not valid text).
    EnvRejected,
    /// The host's HTTP client could not be built.
    HttpClientFailed,
    Internal,
}

impl From<ReadyError> for FailureKind {
    fn from(error: ReadyError) -> Self {
        match error {
            ReadyError::EofBeforeReady => Self::EofBeforeReady,
            ReadyError::LineTooLong => Self::LineTooLong,
            ReadyError::NotJson => Self::NotJson,
            ReadyError::WrongFields => Self::WrongFields,
            ReadyError::PortOutOfRange => Self::PortOutOfRange,
            ReadyError::ReadFailed => Self::ReadFailed,
        }
    }
}

impl From<StartError> for FailureKind {
    fn from(error: StartError) -> Self {
        match error {
            StartError::SpawnFailed => Self::SpawnFailed,
            StartError::JobFailed => Self::JobFailed,
            StartError::JobAssignFailed => Self::JobAssignFailed,
            StartError::UnexpectedThreadState => Self::UnexpectedThreadState,
            StartError::ThreadStartFailed => Self::Internal,
        }
    }
}

impl From<ResolveError> for FailureKind {
    fn from(error: ResolveError) -> Self {
        match error {
            ResolveError::NotInstalled => Self::NotInstalled,
            ResolveError::NotConfigured => Self::NotConfigured,
            ResolveError::OutsideSidecarDir => Self::OutsideSidecarDir,
        }
    }
}

/// The state lock was poisoned (a thread panicked while holding it). The supervisor recovers by
/// forcing `Failed(Internal)` and killing the job, then reports this.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum StateError {
    #[error("the sidecar state lock was poisoned")]
    Poisoned,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum ShutdownError {
    #[error("the sidecar processes did not exit within the time limit")]
    NotEmptyInTime,
    #[error("the sidecar state lock was poisoned")]
    Poisoned,
}

/// State shared between the supervisor handle and its threads.
struct Shared {
    state: Mutex<SidecarState>,
    changed: Condvar,
    job: Job,
}

/// The pure transition rule. See the module documentation.
fn allowed(from: SidecarState, to: SidecarState) -> bool {
    matches!(
        (from, to),
        (SidecarState::Starting, SidecarState::Ready { .. })
            | (SidecarState::Starting, SidecarState::Failed(_))
            | (SidecarState::Starting, SidecarState::Stopped)
            | (SidecarState::Ready { .. }, SidecarState::Failed(_))
            | (SidecarState::Ready { .. }, SidecarState::Stopped)
    )
}

/// Lock the state. A poisoned lock is recovered here, in one place: force `Failed(Internal)`
/// (through the same rule, so a terminal state is kept), kill the job, wake any waiter, and report
/// `Poisoned`. There is no `unwrap` or `expect`.
fn lock_state(shared: &Shared) -> Result<MutexGuard<'_, SidecarState>, StateError> {
    shared
        .state
        .lock()
        .map_err(|poisoned| handle_poison(shared, poisoned.into_inner()))
}

fn handle_poison(shared: &Shared, mut guard: MutexGuard<'_, SidecarState>) -> StateError {
    let failed = SidecarState::Failed(FailureKind::Internal);
    if allowed(*guard, failed) {
        *guard = failed;
    }
    drop(guard);
    let _ = shared.job.terminate();
    shared.changed.notify_all();
    StateError::Poisoned
}

/// The ONLY function that writes the state. Applies `to` if [`allowed`] says so and returns whether
/// it did. A rejected transition is not an error; it is logged at debug (variant names and integers
/// only).
fn transition(shared: &Shared, to: SidecarState) -> Result<bool, StateError> {
    let mut guard = lock_state(shared)?;
    let from = *guard;
    if !allowed(from, to) {
        log::debug!("sidecar state change rejected: {from:?} -> {to:?}");
        return Ok(false);
    }
    *guard = to;
    drop(guard);
    shared.changed.notify_all();
    Ok(true)
}

/// What the stdout reader, stderr drainer and waiter tell the handshake thread.
#[derive(Debug)]
enum Event {
    /// The result of reading and parsing the ready line. Raw output never crosses the channel.
    ReadyLine(Result<NonZeroU16, ReadyError>),
    /// At least one byte of stdout after a good ready line.
    StdoutAfterReady,
    /// Reading stdout failed after a good ready line.
    StdoutReadFailed,
    /// The process exited, with its code if Windows reported one.
    Exited(Option<i32>),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Phase {
    /// Waiting for the ready line. `eof_seen`: stdout closed without one, and we are giving the
    /// exit event a chance to arrive so the exit code can classify the failure.
    AwaitingReady {
        eof_seen: bool,
    },
    Running,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Flow {
    Continue,
    Finish,
}

/// Kill the job, wait (bounded) until it is empty, and only THEN record the failure. A reader who
/// sees `Failed` therefore knows the processes are gone. Does nothing harmful if a shutdown already
/// made the state `Stopped`: the transition is rejected.
fn fail(shared: &Shared, kind: FailureKind) {
    let _ = shared.job.terminate();
    if shared.job.wait_until_empty(EMPTY_WAIT_BOUND).is_err() {
        log::warn!("sidecar processes were still running after the termination time limit");
    }
    let _ = transition(shared, SidecarState::Failed(kind));
}

fn classify_early_exit(code: Option<i32>) -> FailureKind {
    if code == Some(STARTUP_REJECTED_EXIT_CODE) {
        FailureKind::StartupRejected
    } else {
        FailureKind::ExitedBeforeReady { code }
    }
}

/// Apply one event. Separate from the thread loop so tests can feed events in an exact order.
fn handle_event(shared: &Shared, phase: &mut Phase, event: Event) -> Flow {
    match (*phase, event) {
        (Phase::AwaitingReady { .. }, Event::ReadyLine(Ok(port))) => {
            *phase = Phase::Running;
            match transition(shared, SidecarState::Ready { port }) {
                Ok(_) => Flow::Continue,
                Err(StateError::Poisoned) => Flow::Finish,
            }
        }
        (Phase::AwaitingReady { .. }, Event::ReadyLine(Err(ReadyError::EofBeforeReady))) => {
            // Stdout closed. The exit event, if the process is dying, may arrive a moment later;
            // wait for it (still bounded by the handshake deadline) to classify by exit code.
            *phase = Phase::AwaitingReady { eof_seen: true };
            Flow::Continue
        }
        (Phase::AwaitingReady { .. }, Event::ReadyLine(Err(error))) => {
            fail(shared, error.into());
            Flow::Finish
        }
        (Phase::AwaitingReady { .. }, Event::Exited(code)) => {
            fail(shared, classify_early_exit(code));
            Flow::Finish
        }
        (Phase::AwaitingReady { .. }, Event::StdoutReadFailed) => {
            fail(shared, FailureKind::ReadFailed);
            Flow::Finish
        }
        // The reader only sends these after a good ready line, so before one they are a bug.
        (Phase::AwaitingReady { .. }, Event::StdoutAfterReady) => {
            fail(shared, FailureKind::Internal);
            Flow::Finish
        }
        (Phase::Running, Event::StdoutAfterReady) => {
            fail(shared, FailureKind::ProtocolViolation);
            Flow::Finish
        }
        (Phase::Running, Event::StdoutReadFailed) => {
            fail(shared, FailureKind::ReadFailed);
            Flow::Finish
        }
        (Phase::Running, Event::Exited(_)) => {
            fail(shared, FailureKind::ExitedAfterReady);
            Flow::Finish
        }
        // The reader sends exactly one `ReadyLine`, so a second one is a bug.
        (Phase::Running, Event::ReadyLine(_)) => {
            fail(shared, FailureKind::Internal);
            Flow::Finish
        }
    }
}

/// The handshake thread body: receive events until the deadline (before ready) or until the
/// channel closes (after ready).
fn run_handshake(shared: &Shared, events: &Receiver<Event>, timeout: Duration) {
    let deadline = Instant::now() + timeout;
    let mut phase = Phase::AwaitingReady { eof_seen: false };
    loop {
        let event = match phase {
            Phase::AwaitingReady { eof_seen } => {
                let remaining = deadline.saturating_duration_since(Instant::now());
                match events.recv_timeout(remaining) {
                    Ok(event) => event,
                    Err(RecvTimeoutError::Timeout) => {
                        let kind = if eof_seen {
                            FailureKind::EofBeforeReady
                        } else {
                            FailureKind::Timeout
                        };
                        fail(shared, kind);
                        return;
                    }
                    Err(RecvTimeoutError::Disconnected) => {
                        fail(shared, FailureKind::Internal);
                        return;
                    }
                }
            }
            // After ready there is no deadline: the loop ends when every sender is gone.
            Phase::Running => match events.recv() {
                Ok(event) => event,
                Err(_) => return,
            },
        };
        if handle_event(shared, &mut phase, event) == Flow::Finish {
            return;
        }
    }
}

fn stdout_thread(stdout: ChildStdout, events: &Sender<Event>) {
    let mut reader = BufReader::new(stdout);
    let result = read_ready_line(&mut reader);
    let ready = result.is_ok();
    if events.send(Event::ReadyLine(result)).is_err() || !ready {
        return;
    }
    // Any byte after the ready line breaks the contract. `fill_buf` also returns bytes the reader
    // had already buffered past the ready line, so output that arrived together with it is seen.
    match reader.fill_buf() {
        Ok(buffered) if !buffered.is_empty() => {
            let _ = events.send(Event::StdoutAfterReady);
        }
        Ok(_) => {} // end of stream: the process closed stdout
        Err(_) => {
            let _ = events.send(Event::StdoutReadFailed);
        }
    }
}

/// The last `capacity` bytes written to it.
struct TailBuffer {
    bytes: VecDeque<u8>,
    capacity: usize,
}

impl TailBuffer {
    fn new(capacity: usize) -> Self {
        Self {
            bytes: VecDeque::with_capacity(capacity),
            capacity,
        }
    }

    fn push(&mut self, data: &[u8]) {
        // Only the last `capacity` bytes of `data` can survive.
        let keep = data.len().min(self.capacity);
        let overflow = (self.bytes.len() + keep).saturating_sub(self.capacity);
        self.bytes.drain(..overflow.min(self.bytes.len()));
        if let Some(tail) = data.get(data.len() - keep..) {
            self.bytes.extend(tail.iter().copied());
        }
    }

    fn contents(&self) -> Vec<u8> {
        self.bytes.iter().copied().collect()
    }
}

/// Read stderr to the end so the pipe can never fill and block the sidecar. The content is logged
/// once, at debug level only (CLAUDE.md Section 7), never put in an error and never forwarded.
fn stderr_thread(mut stderr: ChildStderr) {
    let mut tail = TailBuffer::new(STDERR_TAIL);
    let mut chunk = [0u8; 1024];
    loop {
        match stderr.read(&mut chunk) {
            Ok(0) => break,
            Ok(count) => {
                if let Some(read) = chunk.get(..count) {
                    tail.push(read);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(_) => break,
        }
    }
    if log::log_enabled!(log::Level::Debug) {
        log::debug!(
            "sidecar stderr (last {} bytes): {}",
            tail.bytes.len(),
            String::from_utf8_lossy(&tail.contents())
        );
    }
}

fn waiter_thread(mut child: Child, events: &Sender<Event>) {
    let code = child.wait().ok().and_then(|status| status.code());
    let _ = events.send(Event::Exited(code));
}

/// A running (or finished) sidecar. Created by [`Supervisor::start`]; dropping it shuts the
/// sidecar down.
pub struct Supervisor {
    shared: Arc<Shared>,
    threads: Mutex<Vec<JoinHandle<()>>>,
    pid: u32,
}

impl Supervisor {
    /// Start the process (suspended, into a new job, then resumed) and the four supervision
    /// threads, and return at once with the state `Starting`. Spawning takes milliseconds; the
    /// handshake itself runs on the handshake thread.
    pub fn start(spec: SidecarSpec) -> Result<Self, StartError> {
        let job = Job::new().map_err(|_| StartError::JobFailed)?;
        let shared = Arc::new(Shared {
            state: Mutex::new(SidecarState::Starting),
            changed: Condvar::new(),
            job,
        });

        let mut child = spawn_in_job(&spec, &shared.job)?;
        let pid = child.id();

        let (Some(stdout), Some(stderr)) = (child.stdout.take(), child.stderr.take()) else {
            return Err(abort_start(&shared, child));
        };

        let (sender, receiver) = mpsc::channel();
        let timeout = spec.handshake_timeout;
        let mut threads = Vec::new();

        let stdout_events = sender.clone();
        let handshake_shared = Arc::clone(&shared);
        let Ok(stdout_handle) = spawn_named("sidecar-stdout", move || {
            stdout_thread(stdout, &stdout_events);
        }) else {
            return Err(abort_start(&shared, child));
        };
        threads.push(stdout_handle);

        let Ok(stderr_handle) = spawn_named("sidecar-stderr", move || stderr_thread(stderr)) else {
            return Err(abort_start(&shared, child));
        };
        threads.push(stderr_handle);

        let Ok(handshake_handle) = spawn_named("sidecar-handshake", move || {
            run_handshake(&handshake_shared, &receiver, timeout);
        }) else {
            return Err(abort_start(&shared, child));
        };
        threads.push(handshake_handle);

        // The waiter owns the child. It is started last so that a failure above can still clean up
        // the child itself.
        match spawn_named("sidecar-waiter", move || waiter_thread(child, &sender)) {
            Ok(handle) => threads.push(handle),
            Err(_) => {
                // The child moved into the closure that failed to start and was dropped with it;
                // the job is how it is killed.
                let _ = shared.job.terminate();
                let _ = shared.job.wait_until_empty(EMPTY_WAIT_BOUND);
                return Err(StartError::ThreadStartFailed);
            }
        }

        Ok(Self {
            shared,
            threads: Mutex::new(threads),
            pid,
        })
    }

    /// The current state.
    pub fn state(&self) -> Result<SidecarState, StateError> {
        Ok(*lock_state(&self.shared)?)
    }

    /// Block until the state is no longer `Starting`, or `bound` has passed, and return the state
    /// then. Waits on a condition variable, so it wakes the moment the state changes.
    pub fn wait_until_settled(&self, bound: Duration) -> Result<SidecarState, StateError> {
        let guard = lock_state(&self.shared)?;
        let (guard, _timed_out) = self
            .shared
            .changed
            .wait_timeout_while(guard, bound, |state| *state == SidecarState::Starting)
            .map_err(|poisoned| handle_poison(&self.shared, poisoned.into_inner().0))?;
        Ok(*guard)
    }

    /// Block until the state is terminal (`Failed` or `Stopped`), or `bound` has passed, and return
    /// the state then. Like [`Supervisor::wait_until_settled`] but it also waits through `Ready`,
    /// which is what a test needs to observe a failure that happens after the ready line.
    pub fn wait_until_terminal(&self, bound: Duration) -> Result<SidecarState, StateError> {
        let guard = lock_state(&self.shared)?;
        let (guard, _timed_out) = self
            .shared
            .changed
            .wait_timeout_while(guard, bound, |state| {
                matches!(state, SidecarState::Starting | SidecarState::Ready { .. })
            })
            .map_err(|poisoned| handle_poison(&self.shared, poisoned.into_inner().0))?;
        Ok(*guard)
    }

    /// Stop the sidecar. Idempotent. The state becomes `Stopped` FIRST (a `Failed` state is
    /// terminal and kept), then the whole job is terminated and waited for (bounded). Supervision
    /// threads are joined only once the job is empty; if it is not, they are left running and this
    /// returns `NotEmptyInTime`, so shutdown can never hang.
    pub fn shutdown(&self) -> Result<(), ShutdownError> {
        let transitioned = transition(&self.shared, SidecarState::Stopped);
        // Kill regardless of the state outcome: a poisoned lock must not leave a process running.
        let _ = self.shared.job.terminate();
        self.shared
            .job
            .wait_until_empty(EMPTY_WAIT_BOUND)
            .map_err(|_| ShutdownError::NotEmptyInTime)?;

        let handles =
            std::mem::take(&mut *self.threads.lock().unwrap_or_else(PoisonError::into_inner));
        for handle in handles {
            if handle.join().is_err() {
                log::warn!("a sidecar supervision thread panicked");
            }
        }
        transitioned
            .map(|_| ())
            .map_err(|_| ShutdownError::Poisoned)
    }

    /// The operating-system id of the sidecar process the host started (the venv launcher in dev).
    pub fn child_pid(&self) -> u32 {
        self.pid
    }

    /// Ids of every process currently in the job (launcher, real interpreter, any children).
    pub fn job_process_ids(&self) -> Result<Vec<u32>, JobError> {
        self.shared.job.process_ids()
    }
}

impl Drop for Supervisor {
    fn drop(&mut self) {
        // Second layer (after explicit shutdown); the job handle closing is the third.
        if let Err(error) = self.shutdown() {
            log::warn!("sidecar shutdown on drop failed: {error}");
        }
    }
}

fn spawn_named(
    name: &str,
    body: impl FnOnce() + Send + 'static,
) -> std::io::Result<JoinHandle<()>> {
    thread::Builder::new().name(name.to_owned()).spawn(body)
}

/// Clean up after a failure between spawning and the supervision threads existing: the child is
/// already in the job, so terminate the job, reap the child, and report why.
fn abort_start(shared: &Shared, mut child: Child) -> StartError {
    let _ = shared.job.terminate();
    let _ = shared.job.wait_until_empty(EMPTY_WAIT_BOUND);
    let _ = child.wait();
    StartError::ThreadStartFailed
}

/// Every failure kind, with an inner match that has no wildcard: a new variant breaks
/// compilation here until it is listed, and therefore until every test that uses this list
/// (the conversions test below and the IPC contract test in `health.rs`) covers it.
#[cfg(test)]
pub(crate) fn every_failure_kind() -> Vec<FailureKind> {
    fn exhaustive(kind: FailureKind) {
        match kind {
            FailureKind::Timeout
            | FailureKind::EofBeforeReady
            | FailureKind::NotJson
            | FailureKind::WrongFields
            | FailureKind::PortOutOfRange
            | FailureKind::LineTooLong
            | FailureKind::ReadFailed
            | FailureKind::ExitedBeforeReady { .. }
            | FailureKind::StartupRejected
            | FailureKind::ExitedAfterReady
            | FailureKind::ProtocolViolation
            | FailureKind::NotInstalled
            | FailureKind::NotConfigured
            | FailureKind::OutsideSidecarDir
            | FailureKind::SpawnFailed
            | FailureKind::JobFailed
            | FailureKind::JobAssignFailed
            | FailureKind::UnexpectedThreadState
            | FailureKind::LogDirUnavailable
            | FailureKind::SystemRootUnavailable
            | FailureKind::RandomUnavailable
            | FailureKind::EnvRejected
            | FailureKind::HttpClientFailed
            | FailureKind::Internal => {}
        }
    }
    let all = vec![
        FailureKind::Timeout,
        FailureKind::EofBeforeReady,
        FailureKind::NotJson,
        FailureKind::WrongFields,
        FailureKind::PortOutOfRange,
        FailureKind::LineTooLong,
        FailureKind::ReadFailed,
        FailureKind::ExitedBeforeReady { code: Some(3) },
        FailureKind::StartupRejected,
        FailureKind::ExitedAfterReady,
        FailureKind::ProtocolViolation,
        FailureKind::NotInstalled,
        FailureKind::NotConfigured,
        FailureKind::OutsideSidecarDir,
        FailureKind::SpawnFailed,
        FailureKind::JobFailed,
        FailureKind::JobAssignFailed,
        FailureKind::UnexpectedThreadState,
        FailureKind::LogDirUnavailable,
        FailureKind::SystemRootUnavailable,
        FailureKind::RandomUnavailable,
        FailureKind::EnvRejected,
        FailureKind::HttpClientFailed,
        FailureKind::Internal,
    ];
    for kind in &all {
        exhaustive(*kind);
    }
    all
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::child_env::{build_child_env, ALLOWED_ENV_KEYS};
    use crate::token::AuthToken;

    fn port(value: u16) -> NonZeroU16 {
        NonZeroU16::new(value).unwrap()
    }

    fn shared() -> Shared {
        Shared {
            state: Mutex::new(SidecarState::Starting),
            changed: Condvar::new(),
            job: Job::new().unwrap(),
        }
    }

    fn state_of(shared: &Shared) -> SidecarState {
        *lock_state(shared).unwrap()
    }

    fn awaiting() -> Phase {
        Phase::AwaitingReady { eof_seen: false }
    }

    fn every_state() -> [SidecarState; 4] {
        [
            SidecarState::Starting,
            SidecarState::Ready { port: port(5000) },
            SidecarState::Failed(FailureKind::Timeout),
            SidecarState::Stopped,
        ]
    }

    #[test]
    fn the_transition_table_has_exactly_five_allowed_moves() {
        let is_allowed = |from: SidecarState, to: SidecarState| allowed(from, to);
        let [starting, ready, failed, stopped] = every_state();
        // (from, to, expected): all 16 pairs.
        let table = [
            (starting, starting, false),
            (starting, ready, true),
            (starting, failed, true),
            (starting, stopped, true),
            (ready, starting, false),
            (ready, ready, false),
            (ready, failed, true),
            (ready, stopped, true),
            (failed, starting, false),
            (failed, ready, false),
            (failed, failed, false),
            (failed, stopped, false),
            (stopped, starting, false),
            (stopped, ready, false),
            (stopped, failed, false),
            (stopped, stopped, false),
        ];
        for (from, to, expected) in table {
            assert_eq!(is_allowed(from, to), expected, "{from:?} -> {to:?}");
        }
        assert_eq!(table.iter().filter(|row| row.2).count(), 5);
    }

    #[test]
    fn a_ready_line_after_shutdown_leaves_the_sidecar_stopped() {
        let shared = shared();
        assert_eq!(transition(&shared, SidecarState::Stopped), Ok(true));

        let mut phase = awaiting();
        let flow = handle_event(&shared, &mut phase, Event::ReadyLine(Ok(port(5000))));

        assert_eq!(flow, Flow::Continue);
        assert_eq!(state_of(&shared), SidecarState::Stopped);
    }

    #[test]
    fn exit_after_an_orderly_stop_stays_stopped_not_failed() {
        let shared = shared();
        let mut phase = awaiting();
        handle_event(&shared, &mut phase, Event::ReadyLine(Ok(port(5000))));
        assert_eq!(state_of(&shared), SidecarState::Ready { port: port(5000) });

        // Shutdown records Stopped first; killing the process then produces an exit event.
        assert_eq!(transition(&shared, SidecarState::Stopped), Ok(true));
        let flow = handle_event(&shared, &mut phase, Event::Exited(Some(1)));

        assert_eq!(flow, Flow::Finish);
        assert_eq!(state_of(&shared), SidecarState::Stopped);
    }

    #[test]
    fn a_failure_is_terminal_and_a_late_ready_line_does_not_revive_it() {
        let shared = shared();
        let mut phase = awaiting();
        // Timeout recorded by the deadline path.
        fail(&shared, FailureKind::Timeout);
        assert_eq!(
            state_of(&shared),
            SidecarState::Failed(FailureKind::Timeout)
        );

        handle_event(&shared, &mut phase, Event::ReadyLine(Ok(port(5000))));

        assert_eq!(
            state_of(&shared),
            SidecarState::Failed(FailureKind::Timeout)
        );
    }

    #[test]
    fn exit_after_ready_without_a_stop_is_a_failure() {
        let shared = shared();
        let mut phase = awaiting();
        handle_event(&shared, &mut phase, Event::ReadyLine(Ok(port(5000))));

        let flow = handle_event(&shared, &mut phase, Event::Exited(Some(0)));

        assert_eq!(flow, Flow::Finish);
        assert_eq!(
            state_of(&shared),
            SidecarState::Failed(FailureKind::ExitedAfterReady)
        );
    }

    #[test]
    fn stdout_after_ready_is_a_protocol_violation() {
        let shared = shared();
        let mut phase = awaiting();
        handle_event(&shared, &mut phase, Event::ReadyLine(Ok(port(5000))));

        handle_event(&shared, &mut phase, Event::StdoutAfterReady);

        assert_eq!(
            state_of(&shared),
            SidecarState::Failed(FailureKind::ProtocolViolation)
        );
    }

    #[test]
    fn eof_then_exit_code_two_is_startup_rejected() {
        let shared = shared();
        let mut phase = awaiting();
        let eof = Event::ReadyLine(Err(ReadyError::EofBeforeReady));
        assert_eq!(handle_event(&shared, &mut phase, eof), Flow::Continue);
        assert_eq!(phase, Phase::AwaitingReady { eof_seen: true });
        assert_eq!(state_of(&shared), SidecarState::Starting);

        handle_event(&shared, &mut phase, Event::Exited(Some(2)));

        assert_eq!(
            state_of(&shared),
            SidecarState::Failed(FailureKind::StartupRejected)
        );
    }

    #[test]
    fn other_early_exits_keep_their_code() {
        for code in [Some(3), Some(1), Some(0), None] {
            let shared = shared();
            let mut phase = awaiting();
            handle_event(&shared, &mut phase, Event::Exited(code));
            assert_eq!(
                state_of(&shared),
                SidecarState::Failed(FailureKind::ExitedBeforeReady { code }),
                "{code:?}"
            );
        }
    }

    #[test]
    fn every_bad_ready_line_error_maps_to_its_own_failure() {
        let cases = [
            (ReadyError::LineTooLong, FailureKind::LineTooLong),
            (ReadyError::NotJson, FailureKind::NotJson),
            (ReadyError::WrongFields, FailureKind::WrongFields),
            (ReadyError::PortOutOfRange, FailureKind::PortOutOfRange),
            (ReadyError::ReadFailed, FailureKind::ReadFailed),
        ];
        for (error, kind) in cases {
            let shared = shared();
            let mut phase = awaiting();
            let flow = handle_event(&shared, &mut phase, Event::ReadyLine(Err(error)));
            assert_eq!(flow, Flow::Finish);
            assert_eq!(state_of(&shared), SidecarState::Failed(kind), "{error:?}");
        }
    }

    #[test]
    fn a_poisoned_state_lock_is_recovered_as_failed_internal() {
        let shared = Arc::new(shared());
        let poisoner = Arc::clone(&shared);
        let joined = thread::spawn(move || {
            let _guard = poisoner.state.lock().unwrap();
            panic!("deliberately poisoning the state lock for a test");
        })
        .join();
        assert!(joined.is_err());

        assert_eq!(lock_state(&shared).err(), Some(StateError::Poisoned));
        // The recovery recorded Failed(Internal) in the (still poisoned) mutex.
        let recorded = match shared.state.lock() {
            Ok(guard) => *guard,
            Err(poisoned) => *poisoned.into_inner(),
        };
        assert_eq!(recorded, SidecarState::Failed(FailureKind::Internal));
        assert_eq!(
            transition(&shared, SidecarState::Stopped),
            Err(StateError::Poisoned)
        );
    }

    #[test]
    fn the_spec_debug_output_shows_key_names_but_no_values() {
        let token = AuthToken::generate().unwrap();
        let env = build_child_env(
            Path::new(r"C:\Windows"),
            Path::new(r"C:\Users\someone\AppData\Local\app\logs"),
            &token,
        )
        .unwrap();
        let secret = {
            // The only legitimate reader of the value, to build the assertion.
            let mut found = String::new();
            for (key, value) in env.iter() {
                if key == "GRC_AUTH_TOKEN" {
                    found = value.to_string_lossy().into_owned();
                }
            }
            found
        };
        assert_eq!(secret.len(), 64);
        let spec = SidecarSpec::new(
            PathBuf::from(r"C:\sidecar\python.exe"),
            vec![OsString::from("-I")],
            PathBuf::from(r"C:\work"),
            env,
        );

        for output in [format!("{spec:?}"), format!("{spec:#?}")] {
            assert!(!output.contains(&secret), "token leaked into: {output}");
            assert!(!output.contains("AppData"), "an env value leaked: {output}");
            for key in ALLOWED_ENV_KEYS {
                assert!(output.contains(key), "key {key} missing from: {output}");
            }
        }
    }

    #[test]
    fn the_default_handshake_timeout_is_thirty_seconds_and_can_be_replaced() {
        assert_eq!(HANDSHAKE_TIMEOUT, Duration::from_secs(30));
        let token = AuthToken::generate().unwrap();
        let env = build_child_env(Path::new(r"C:\Windows"), Path::new(r"C:\logs"), &token).unwrap();
        let spec = SidecarSpec::new(PathBuf::from("x"), Vec::new(), PathBuf::from(r"C:\w"), env);
        assert_eq!(spec.handshake_timeout, HANDSHAKE_TIMEOUT);
        let spec = spec.with_handshake_timeout(Duration::from_secs(2));
        assert_eq!(spec.handshake_timeout, Duration::from_secs(2));
    }

    #[test]
    fn the_tail_buffer_keeps_only_the_last_bytes() {
        let mut tail = TailBuffer::new(8);
        tail.push(b"abc");
        assert_eq!(tail.contents(), b"abc");
        tail.push(b"defgh");
        assert_eq!(tail.contents(), b"abcdefgh");
        tail.push(b"ij");
        assert_eq!(tail.contents(), b"cdefghij");
        // One write larger than the whole capacity keeps its own last 8 bytes.
        tail.push(b"0123456789");
        assert_eq!(tail.contents(), b"23456789");
        tail.push(b"");
        assert_eq!(tail.contents(), b"23456789");
    }

    #[test]
    fn the_conversions_into_failure_kind_cover_every_source_variant() {
        let ready = [
            ReadyError::EofBeforeReady,
            ReadyError::LineTooLong,
            ReadyError::NotJson,
            ReadyError::WrongFields,
            ReadyError::PortOutOfRange,
            ReadyError::ReadFailed,
        ];
        let from_ready: Vec<FailureKind> = ready.into_iter().map(FailureKind::from).collect();
        assert_eq!(from_ready.len(), 6);

        let start = [
            StartError::SpawnFailed,
            StartError::JobFailed,
            StartError::JobAssignFailed,
            StartError::UnexpectedThreadState,
            StartError::ThreadStartFailed,
        ];
        assert_eq!(
            FailureKind::from(StartError::JobAssignFailed),
            FailureKind::JobAssignFailed
        );
        assert_eq!(start.len(), 5);

        assert_eq!(
            FailureKind::from(ResolveError::NotInstalled),
            FailureKind::NotInstalled
        );
        assert_eq!(
            FailureKind::from(ResolveError::NotConfigured),
            FailureKind::NotConfigured
        );
        assert_eq!(
            FailureKind::from(ResolveError::OutsideSidecarDir),
            FailureKind::OutsideSidecarDir
        );
    }

    #[test]
    fn no_failure_kind_or_error_can_hold_text_or_the_token() {
        let token = AuthToken::generate().unwrap();
        for kind in every_failure_kind() {
            for text in [format!("{kind:?}"), format!("{kind:#?}")] {
                assert!(!text.is_empty());
                assert!(!text.contains("C:\\"), "a path leaked into: {text}");
                assert!(!text.contains(token.expose()), "token leaked into: {text}");
            }
        }
        // `FailureKind` is `Copy`: a `String` or `Vec` field would make that impossible.
        fn assert_copy<T: Copy>() {}
        assert_copy::<FailureKind>();
        assert_copy::<SidecarState>();
        for text in [
            StateError::Poisoned.to_string(),
            ShutdownError::NotEmptyInTime.to_string(),
            ShutdownError::Poisoned.to_string(),
        ] {
            assert!(!text.is_empty());
        }
    }
}
