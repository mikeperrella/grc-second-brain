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
use crate::health::{HealthClient, HealthError, HealthReply, LoopbackEndpoint};
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

/// A started sidecar and the client for its `/health` endpoint.
pub struct SidecarHost {
    supervisor: Supervisor,
    client: HealthClient,
}

impl SidecarHost {
    /// Start `program` with `args` as the sidecar. `log_dir` becomes the child's `GRC_LOG_DIR` and
    /// working directory and is created if missing. Returns as soon as the process is running; the
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
        log_dir: &Path,
        handshake_timeout: Duration,
    ) -> Result<Self, FailureKind> {
        fs::create_dir_all(log_dir).map_err(|_| FailureKind::LogDirUnavailable)?;
        let system_root =
            system_windows_directory().map_err(|_| FailureKind::SystemRootUnavailable)?;
        let token = AuthToken::generate().map_err(|_| FailureKind::RandomUnavailable)?;
        let env =
            build_child_env(&system_root, log_dir, &token).map_err(|_| FailureKind::EnvRejected)?;
        let client = HealthClient::new(&token).map_err(|_| FailureKind::HttpClientFailed)?;
        let spec = SidecarSpec::new(program, args, log_dir.to_owned(), env)
            .with_handshake_timeout(handshake_timeout);
        let supervisor = Supervisor::start(spec).map_err(FailureKind::from)?;
        Ok(Self { supervisor, client })
    }

    /// Start the dev sidecar: the repository's venv interpreter (debug builds only; release builds
    /// report `NotConfigured` until M8) with the fixed isolated-module arguments.
    pub fn start_dev(log_dir: &Path) -> Result<Self, FailureKind> {
        let program = resolve_sidecar_program().map_err(FailureKind::from)?;
        let args = SIDECAR_ARGS.iter().map(OsString::from).collect();
        Self::start(
            program,
            args,
            log_dir,
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
