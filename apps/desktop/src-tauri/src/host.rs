//! The sidecar as the app sees it: a supervised process plus the client that probes it
//! (M2 plan sections 6 and 7).
//!
//! * [`SidecarHost`] starts the sidecar (token, environment, health client, supervisor) and answers
//!   health questions.
//! * [`HostSlot`] is what the Tauri app keeps in managed state. It exists because the sidecar is
//!   started on a background thread while the window may already be closing: whichever of "start
//!   finished" and "shutdown requested" happens first, the sidecar ends up stopped exactly once and
//!   never left running.

use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use crate::child_env::build_child_env;
use crate::dev_paths::existing_directory;
use crate::folders::{files, UserFolders};
use crate::health::{HealthClient, HealthError, HealthReply, LoopbackEndpoint};
use crate::proxy::route::FileFolder;
use crate::proxy::{ProxyError, ProxyReply, RouteRequest};
use crate::sidecar::resolve::{resolve_sidecar_program, SIDECAR_ARGS};
use crate::sidecar::spawn::system_windows_directory;
use crate::sidecar::supervisor::{FailureKind, SidecarSpec, SidecarState, Supervisor};
use crate::token::AuthToken;

/// What to do for a given sidecar state. Pure, so it can be tested without a process or a socket.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Decision {
    /// Answer without touching the network.
    Reply(HealthReply),
    /// Ask the sidecar at this endpoint.
    Probe(LoopbackEndpoint),
}

pub(crate) fn decide(state: SidecarState) -> Decision {
    match state {
        // No port is known yet: never probe, never guess one.
        SidecarState::Starting => Decision::Reply(HealthReply::Starting),
        SidecarState::Ready { port } => Decision::Probe(LoopbackEndpoint::from_port(port)),
        SidecarState::Failed(reason) => Decision::Reply(HealthReply::Failed { reason }),
        SidecarState::Stopped => Decision::Reply(HealthReply::NotRunning),
    }
}

/// Where a proxied call may go for a given sidecar state. Pure, like [`decide`]: only `Ready` has a
/// port, and every other state is `not_ready` with no network call.
pub(crate) fn endpoint_for(state: SidecarState) -> Result<LoopbackEndpoint, ProxyError> {
    match state {
        SidecarState::Ready { port } => Ok(LoopbackEndpoint::from_port(port)),
        SidecarState::Starting | SidecarState::Failed(_) | SidecarState::Stopped => {
            Err(ProxyError::NotReady)
        }
    }
}

/// The directories the sidecar is told about. All must be absolute (the environment builder
/// checks). `folders` holds the user's five folders that are valid for this run (`folders::resolve`
/// decides); one that is `None` is not set, or was not valid, and its key is absent.
#[derive(Debug, Clone, Copy)]
pub struct SidecarDirs<'a> {
    pub log_dir: &'a Path,
    pub index_dir: &'a Path,
    pub folders: UserFolders<'a>,
}

/// The three folders a window-supplied file name is resolved in (CLAUDE.md 3.6, backlog G9).
/// Canonical paths, taken from the same folders the sidecar was started with, so the host and the
/// sidecar agree on where a name points. A folder that is `None` is not set: the sidecar then
/// answers the route with its own typed refusal.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct FileRoots {
    questionnaire: Option<PathBuf>,
    export: Option<PathBuf>,
    import: Option<PathBuf>,
}

impl FileRoots {
    pub(crate) fn from_folders(folders: &UserFolders<'_>) -> Self {
        let canonical = |dir: Option<&Path>| dir.and_then(|dir| existing_directory(dir.to_owned()));
        Self {
            questionnaire: canonical(folders.questionnaire),
            export: canonical(folders.export),
            import: canonical(folders.import),
        }
    }

    #[cfg(test)]
    pub(crate) fn new(
        questionnaire: Option<PathBuf>,
        export: Option<PathBuf>,
        import: Option<PathBuf>,
    ) -> Self {
        Self {
            questionnaire,
            export,
            import,
        }
    }
}

/// Check the file name a request carries BEFORE anything is sent: one plain path component, and the
/// file it names in the right folder must be a regular file inside that folder (every link
/// followed). Every failure is `bad_request`. A request with no file name passes. With the folder
/// not set the containment step is skipped and the sidecar refuses the route itself; the
/// one-component check still runs.
pub(crate) fn check_request_file(
    request: &RouteRequest,
    roots: &FileRoots,
) -> Result<(), ProxyError> {
    let Some((folder, name)) = request.file() else {
        return Ok(());
    };
    if !files::is_one_component(name) {
        return Err(ProxyError::BadRequest);
    }
    let root = match folder {
        FileFolder::Questionnaires => roots.questionnaire.as_deref(),
        FileFolder::Exports => roots.export.as_deref(),
        FileFolder::Imports => roots.import.as_deref(),
    };
    match root {
        Some(root) => files::resolve_file(root, name)
            .map(|_| ())
            .map_err(|_| ProxyError::BadRequest),
        None => Ok(()),
    }
}

/// A started sidecar and the client for its `/health` endpoint.
pub struct SidecarHost {
    supervisor: Supervisor,
    client: HealthClient,
    roots: FileRoots,
}

impl SidecarHost {
    /// Start `program` with `args` as the sidecar. `log_dir` becomes the child's `GRC_LOG_DIR` and
    /// working directory and is created if missing. `index_dir` becomes `GRC_INDEX_DIR` and is
    /// created if missing; each user folder that is given becomes its own key (the caller has
    /// already validated them, `folders::resolve`). Returns as soon as the process is running; the
    /// handshake continues on supervision threads (read it with `supervisor().state()`).
    ///
    /// `pub` because integration tests start the fake sidecar through it; the app's only caller is
    /// [`SidecarHost::start_dev`], which passes the resolved dev interpreter.
    ///
    /// The token lives only in the child's environment and the client's sensitive header: this
    /// function's own copy is dropped when it returns.
    pub fn start(
        program: PathBuf,
        args: Vec<OsString>,
        dirs: &SidecarDirs<'_>,
        handshake_timeout: Duration,
    ) -> Result<Self, FailureKind> {
        let log_dir = dirs.log_dir;
        fs::create_dir_all(log_dir).map_err(|_| FailureKind::LogDirUnavailable)?;
        // Its own failure kind (G8, ADR-011): "could not prepare the index folder" is not the same
        // as "the environment could not be built".
        fs::create_dir_all(dirs.index_dir).map_err(|_| FailureKind::IndexDirUnavailable)?;
        let system_root =
            system_windows_directory().map_err(|_| FailureKind::SystemRootUnavailable)?;
        let token = AuthToken::generate().map_err(|_| FailureKind::RandomUnavailable)?;
        let env = build_child_env(&system_root, log_dir, dirs.index_dir, &dirs.folders, &token)
            .map_err(|_| FailureKind::EnvRejected)?;
        let client = HealthClient::new(&token).map_err(|_| FailureKind::HttpClientFailed)?;
        let spec = SidecarSpec::new(program, args, log_dir.to_owned(), env)
            .with_handshake_timeout(handshake_timeout);
        let supervisor = Supervisor::start(spec).map_err(FailureKind::from)?;
        Ok(Self {
            supervisor,
            client,
            roots: FileRoots::from_folders(&dirs.folders),
        })
    }

    /// Start the dev sidecar: the repository's venv interpreter (debug builds only; release builds
    /// report `NotConfigured` until M8) with the fixed isolated-module arguments.
    pub fn start_dev(dirs: &SidecarDirs<'_>) -> Result<Self, FailureKind> {
        let program = resolve_sidecar_program().map_err(FailureKind::from)?;
        let args = SIDECAR_ARGS.iter().map(OsString::from).collect();
        Self::start(
            program,
            args,
            dirs,
            crate::sidecar::supervisor::HANDSHAKE_TIMEOUT,
        )
    }

    /// The health of the sidecar right now. `Starting`, `Failed` and `NotRunning` are answered from
    /// the supervisor's state with no network call; `Ready` probes `/health`.
    pub async fn health(&self) -> Result<HealthReply, HealthError> {
        let state = self
            .supervisor
            .state()
            .unwrap_or(SidecarState::Failed(FailureKind::Internal));
        match decide(state) {
            Decision::Reply(reply) => Ok(reply),
            Decision::Probe(endpoint) => self.client.check(&endpoint).await,
        }
    }

    /// One proxied call (ADR-020), the body of the `sidecar_call` command. A file name in the
    /// request is checked first (`check_request_file`), so a bad name is `bad_request` and opens no
    /// connection. Then `not_ready` unless the sidecar has announced its port and is running;
    /// otherwise the call goes to that port only.
    pub async fn call(&self, request: &RouteRequest) -> Result<ProxyReply, ProxyError> {
        check_request_file(request, &self.roots)?;
        let state = self
            .supervisor
            .state()
            .unwrap_or(SidecarState::Failed(FailureKind::Internal));
        let endpoint = endpoint_for(state)?;
        crate::proxy::call(&self.client, &endpoint, request).await
    }

    pub fn supervisor(&self) -> &Supervisor {
        &self.supervisor
    }
}

/// Anything that can be stopped. A trait so the slot's logic can be tested with a counting fake.
pub(crate) trait Stop {
    /// Stop it. Must be safe to call more than once.
    fn stop(&self);
}

impl Stop for SidecarHost {
    fn stop(&self) {
        if let Err(error) = self.supervisor.shutdown() {
            log::warn!("sidecar shutdown reported an error: {error}");
        }
    }
}

enum Slot<H> {
    /// Nothing has finished yet.
    Starting,
    Running(Arc<H>),
    StartFailed(FailureKind),
    /// Shutdown was requested. Terminal: a host that finishes starting later is stopped at once.
    ShutDown,
}

/// What the slot holds, handed out without exposing the lock.
pub(crate) enum Current<H> {
    Starting,
    Running(Arc<H>),
    Failed(FailureKind),
    Stopped,
}

/// The managed state of the app: at most one host, started once, stopped once.
pub(crate) struct HostSlot<H> {
    slot: Mutex<Slot<H>>,
}

impl<H> Default for HostSlot<H> {
    fn default() -> Self {
        Self {
            slot: Mutex::new(Slot::Starting),
        }
    }
}

impl<H: Stop> HostSlot<H> {
    /// Lock the slot. A poisoned lock is recovered (the data is a plain enum, valid in any state)
    /// so that shutdown can still run: leaving a process running is the worse outcome.
    fn lock(&self) -> MutexGuard<'_, Slot<H>> {
        self.slot.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Record the outcome of starting. If shutdown was requested while the sidecar was starting,
    /// the new host is stopped right away instead of being kept.
    pub(crate) fn finish_start(&self, result: Result<H, FailureKind>) {
        let late: Option<H> = {
            let mut slot = self.lock();
            match (&*slot, result) {
                (Slot::Starting, Ok(host)) => {
                    *slot = Slot::Running(Arc::new(host));
                    None
                }
                (Slot::Starting, Err(kind)) => {
                    *slot = Slot::StartFailed(kind);
                    None
                }
                // Shut down already, or started twice (a bug): do not keep this host.
                (_, Ok(host)) => Some(host),
                (_, Err(_)) => None,
            }
        };
        // Outside the lock: stopping waits for the processes to exit.
        if let Some(host) = late {
            host.stop();
        }
    }

    /// Stop the sidecar if there is one, and remember that shutdown happened. Idempotent.
    pub(crate) fn shutdown(&self) {
        let previous = std::mem::replace(&mut *self.lock(), Slot::ShutDown);
        if let Slot::Running(host) = previous {
            host.stop();
        }
    }

    pub(crate) fn current(&self) -> Current<H> {
        match &*self.lock() {
            Slot::Starting => Current::Starting,
            Slot::Running(host) => Current::Running(Arc::clone(host)),
            Slot::StartFailed(kind) => Current::Failed(*kind),
            Slot::ShutDown => Current::Stopped,
        }
    }
}

impl HostSlot<SidecarHost> {
    /// The reply for the `sidecar_health` command. The lock is released before any network call.
    pub(crate) async fn health(&self) -> Result<HealthReply, HealthError> {
        match self.current() {
            Current::Starting => Ok(HealthReply::Starting),
            Current::Failed(reason) => Ok(HealthReply::Failed { reason }),
            Current::Stopped => Ok(HealthReply::NotRunning),
            Current::Running(host) => host.health().await,
        }
    }

    /// The reply for the `sidecar_call` command. Anything but a running host is `not_ready`, with no
    /// network call. The lock is released before the call.
    pub(crate) async fn call(&self, request: &RouteRequest) -> Result<ProxyReply, ProxyError> {
        match self.current() {
            Current::Running(host) => host.call(request).await,
            Current::Starting | Current::Failed(_) | Current::Stopped => Err(ProxyError::NotReady),
        }
    }
}

/// What the Tauri app keeps in managed state.
pub(crate) type HostState = HostSlot<SidecarHost>;

#[cfg(test)]
mod tests {
    use super::*;
    use std::num::NonZeroU16;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread;

    /// Counts how many times it was stopped.
    struct Counting(Arc<AtomicUsize>);

    impl Stop for Counting {
        fn stop(&self) {
            self.0.fetch_add(1, Ordering::SeqCst);
        }
    }

    fn counting() -> (Counting, Arc<AtomicUsize>) {
        let count = Arc::new(AtomicUsize::new(0));
        (Counting(Arc::clone(&count)), count)
    }

    fn port(value: u16) -> NonZeroU16 {
        NonZeroU16::new(value).unwrap()
    }

    #[test]
    fn decide_never_probes_before_the_sidecar_is_ready() {
        assert_eq!(
            decide(SidecarState::Starting),
            Decision::Reply(HealthReply::Starting)
        );
        assert_eq!(
            decide(SidecarState::Stopped),
            Decision::Reply(HealthReply::NotRunning)
        );
        assert_eq!(
            decide(SidecarState::Failed(FailureKind::Timeout)),
            Decision::Reply(HealthReply::Failed {
                reason: FailureKind::Timeout
            })
        );
        assert_eq!(
            decide(SidecarState::Ready { port: port(5000) }),
            Decision::Probe(LoopbackEndpoint::new(5000).unwrap())
        );
    }

    #[test]
    fn a_proxied_call_has_an_endpoint_only_when_the_sidecar_is_ready() {
        for state in [
            SidecarState::Starting,
            SidecarState::Stopped,
            SidecarState::Failed(FailureKind::Timeout),
        ] {
            assert_eq!(endpoint_for(state), Err(ProxyError::NotReady), "{state:?}");
        }
        assert_eq!(
            endpoint_for(SidecarState::Ready { port: port(5000) }),
            Ok(LoopbackEndpoint::new(5000).unwrap())
        );
    }

    fn file_request(route: &str, file: &str) -> RouteRequest {
        RouteRequest::from_value(serde_json::json!({"route": route, "file": file})).unwrap()
    }

    #[test]
    fn a_request_without_a_file_name_needs_no_file_check() {
        let roots = FileRoots::default();
        for json in [
            r#"{"route":"readiness"}"#,
            r#"{"route":"vault_index_start"}"#,
            r#"{"route":"questionnaire_status","run_id":"0123456789abcdef"}"#,
            r#"{"route":"ledger_customers"}"#,
            r#"{"route":"ledger_promises","include_voided":false,"page":1,"page_size":100}"#,
            r#"{"route":"ledger_cited_paths"}"#,
            r#"{"route":"ledger_recall"}"#,
            r#"{"route":"ledger_void","promise_id":"01234567-89ab-cdef-0123-456789abcdef","reason":"other"}"#,
            r#"{"route":"ledger_acknowledge","promise_id":"01234567-89ab-cdef-0123-456789abcdef","seen_recall_event_id":1}"#,
            r#"{"route":"ledger_blast_radius","path":"a.md","delete_file":true}"#,
            r#"{"route":"reality_claims"}"#,
            r#"{"route":"contradictions_start"}"#,
            r#"{"route":"contradictions_status","run_id":"0123456789abcdef"}"#,
            r#"{"route":"vault_graph"}"#,
        ] {
            let request = RouteRequest::from_json(json).unwrap();
            assert_eq!(check_request_file(&request, &roots), Ok(()), "{json}");
        }
    }

    #[test]
    fn a_name_that_is_not_one_component_is_bad_request_even_with_no_folder_set() {
        // No root at all: the one-component step has nothing to resolve against and still refuses.
        let roots = FileRoots::new(None, None, None);
        for route in ["questionnaire_inspect", "draft_rows", "reality_inspect"] {
            for name in [
                "..\\x.xlsx",
                "a/b.xlsx",
                "C:\\x.xlsx",
                "NUL.xlsx",
                "x.xlsx.",
                "q:s",
            ] {
                assert_eq!(
                    check_request_file(&file_request(route, name), &roots),
                    Err(ProxyError::BadRequest),
                    "{route} {name}"
                );
            }
            // A plain name with no folder set goes on to the sidecar's own typed refusal.
            assert_eq!(
                check_request_file(&file_request(route, "x.xlsx"), &roots),
                Ok(())
            );
        }
    }

    #[test]
    fn a_missing_file_in_a_set_folder_is_bad_request() {
        // The crate's own source folder exists, and holds no `nope.xlsx`.
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let roots = FileRoots::new(Some(dir.clone()), Some(dir.clone()), Some(dir));
        for route in ["questionnaire_inspect", "draft_rows", "reality_inspect"] {
            assert_eq!(
                check_request_file(&file_request(route, "nope.xlsx"), &roots),
                Err(ProxyError::BadRequest)
            );
        }
        // A real regular file there passes (the check is about the name, not the extension).
        assert_eq!(
            check_request_file(&file_request("draft_rows", "Cargo.toml"), &roots),
            Ok(())
        );
    }

    #[test]
    fn a_started_host_is_stopped_once_however_often_shutdown_runs() {
        let slot = HostSlot::default();
        let (host, stops) = counting();
        slot.finish_start(Ok(host));
        assert!(matches!(slot.current(), Current::Running(_)));

        slot.shutdown();
        slot.shutdown();

        assert_eq!(stops.load(Ordering::SeqCst), 1);
        assert!(matches!(slot.current(), Current::Stopped));
    }

    #[test]
    fn a_host_that_finishes_starting_after_shutdown_is_stopped_not_kept() {
        let slot = HostSlot::default();
        slot.shutdown();
        let (host, stops) = counting();

        slot.finish_start(Ok(host));

        assert_eq!(stops.load(Ordering::SeqCst), 1);
        assert!(matches!(slot.current(), Current::Stopped));
    }

    #[test]
    fn a_failed_start_is_remembered_and_shutdown_still_works() {
        let slot: HostSlot<Counting> = HostSlot::default();
        slot.finish_start(Err(FailureKind::NotInstalled));
        assert!(matches!(
            slot.current(),
            Current::Failed(FailureKind::NotInstalled)
        ));

        slot.shutdown();
        assert!(matches!(slot.current(), Current::Stopped));
    }

    #[test]
    fn starting_twice_keeps_the_first_host_and_stops_the_second() {
        let slot = HostSlot::default();
        let (first, first_stops) = counting();
        let (second, second_stops) = counting();

        slot.finish_start(Ok(first));
        slot.finish_start(Ok(second));

        assert_eq!(second_stops.load(Ordering::SeqCst), 1);
        assert_eq!(first_stops.load(Ordering::SeqCst), 0);
        assert!(matches!(slot.current(), Current::Running(_)));
    }

    #[test]
    fn a_poisoned_lock_does_not_stop_shutdown_from_running() {
        let slot = Arc::new(HostSlot::default());
        let (host, stops) = counting();
        slot.finish_start(Ok(host));

        let poisoner = Arc::clone(&slot);
        let joined = thread::spawn(move || {
            let _guard = poisoner.slot.lock().unwrap();
            panic!("deliberately poisoning the slot lock for a test");
        })
        .join();
        assert!(joined.is_err());

        slot.shutdown();

        assert_eq!(stops.load(Ordering::SeqCst), 1);
        assert!(matches!(slot.current(), Current::Stopped));
    }
}
