//! Orderly shutdown: it must end in `Stopped`, never in a failure, and never hang (M2 plan section
//! 6 and the state rule in `supervisor.rs`).

mod common;

use common::{
    assert_exits, fake_spec, Reaper, Scratch, TestResult, FAKE_PORT, SETTLE_SLACK, TEST_HANDSHAKE,
};
use desktop_lib::sidecar::supervisor::{SidecarState, Supervisor};

#[test]
fn shutting_down_a_ready_sidecar_ends_in_stopped_not_failed() -> TestResult {
    let scratch = Scratch::new("stop_ready")?;
    let supervisor = Supervisor::start(fake_spec(&scratch, "ready", None, TEST_HANDSHAKE)?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());
    let ready = supervisor.wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;
    assert!(
        matches!(ready, SidecarState::Ready { port } if port.get() == FAKE_PORT),
        "expected Ready, got {ready:?}"
    );

    supervisor.shutdown()?;

    // `shutdown` joins the supervision threads, so the exit event caused by the kill has already
    // been handled by now. If the state rule allowed it, this would be Failed(ExitedAfterReady).
    assert_eq!(supervisor.state()?, SidecarState::Stopped);
    assert_exits(supervisor.child_pid(), "the sidecar")?;
    assert_eq!(supervisor.job_process_ids()?, Vec::<u32>::new());
    Ok(())
}

#[test]
fn shutting_down_twice_is_harmless() -> TestResult {
    let scratch = Scratch::new("stop_twice")?;
    let supervisor = Supervisor::start(fake_spec(&scratch, "ready", None, TEST_HANDSHAKE)?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());
    supervisor.wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    supervisor.shutdown()?;
    supervisor.shutdown()?;

    assert_eq!(supervisor.state()?, SidecarState::Stopped);
    Ok(())
}

#[test]
fn shutting_down_while_still_starting_ends_in_stopped() -> TestResult {
    // `silent` never prints a ready line, so the state is certain to be `Starting` when shutdown
    // runs, with the full 15 s handshake as margin (no timing assumption). The case "a ready line
    // arrives after the stop" cannot be produced deterministically with a real process; the unit
    // tests in supervisor.rs drive that exact sequence through `handle_event`.
    let scratch = Scratch::new("stop_starting")?;
    let supervisor = Supervisor::start(fake_spec(&scratch, "silent", None, TEST_HANDSHAKE)?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());
    assert_eq!(supervisor.state()?, SidecarState::Starting);

    supervisor.shutdown()?;

    assert_eq!(supervisor.state()?, SidecarState::Stopped);
    assert_eq!(supervisor.job_process_ids()?, Vec::<u32>::new());
    assert_exits(supervisor.child_pid(), "the sidecar")?;
    Ok(())
}

#[test]
fn dropping_the_supervisor_stops_the_sidecar() -> TestResult {
    let scratch = Scratch::new("drop")?;
    let supervisor = Supervisor::start(fake_spec(&scratch, "ready", None, TEST_HANDSHAKE)?)?;
    let pid = supervisor.child_pid();
    let mut reaper = Reaper::new();
    reaper.add(pid);
    supervisor.wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    drop(supervisor);

    assert_exits(pid, "the sidecar after the supervisor was dropped")?;
    Ok(())
}
