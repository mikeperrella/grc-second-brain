//! The handshake against the fake sidecar: what a good start looks like, and that every way of
//! going wrong ends with the right failure AND no process left running (M2 plan section 12).

mod common;

use std::collections::BTreeSet;
use std::fs;
use std::num::NonZeroU16;

use common::{
    assert_exits, fake_spec, Reaper, Scratch, TestResult, FAKE_PORT, SETTLE_SLACK, TEST_HANDSHAKE,
    TIMEOUT_TEST_HANDSHAKE,
};
use desktop_lib::child_env::ALLOWED_ENV_KEYS;
use desktop_lib::sidecar::supervisor::{FailureKind, SidecarState, Supervisor};

fn fake_port() -> Result<NonZeroU16, &'static str> {
    NonZeroU16::new(FAKE_PORT).ok_or("the fake port is zero")
}

/// Start the fake in `mode`, wait for a terminal state and check that the sidecar processes are
/// gone. `Failed` is only recorded after the job is empty, so the job must already be empty here.
fn expect_failure(name: &str, mode: &str, expected: FailureKind) -> TestResult {
    let scratch = Scratch::new(name)?;
    let supervisor = Supervisor::start(fake_spec(&scratch, mode, None, TEST_HANDSHAKE)?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());

    let state = supervisor.wait_until_terminal(TEST_HANDSHAKE + SETTLE_SLACK)?;

    assert_eq!(state, SidecarState::Failed(expected), "mode {mode}");
    assert_eq!(
        supervisor.job_process_ids()?,
        Vec::<u32>::new(),
        "job not empty"
    );
    assert_exits(supervisor.child_pid(), "the sidecar")?;
    Ok(())
}

#[test]
fn a_good_ready_line_gives_ready_with_the_port() -> TestResult {
    let scratch = Scratch::new("ready")?;
    let supervisor = Supervisor::start(fake_spec(&scratch, "ready", None, TEST_HANDSHAKE)?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());

    let state = supervisor.wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    assert_eq!(state, SidecarState::Ready { port: fake_port()? });
    supervisor.shutdown()?;
    Ok(())
}

#[test]
fn a_slow_sidecar_that_answers_in_time_is_ready() -> TestResult {
    let scratch = Scratch::new("late_ready")?;
    let supervisor = Supervisor::start(fake_spec(&scratch, "late-ready", None, TEST_HANDSHAKE)?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());

    let state = supervisor.wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    assert_eq!(state, SidecarState::Ready { port: fake_port()? });
    supervisor.shutdown()?;
    Ok(())
}

#[test]
fn silent_child_is_killed_on_timeout() -> TestResult {
    // The only test that uses the short timeout: it expects the handshake to time out.
    let scratch = Scratch::new("silent")?;
    let supervisor =
        Supervisor::start(fake_spec(&scratch, "silent", None, TIMEOUT_TEST_HANDSHAKE)?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());

    let state = supervisor.wait_until_terminal(TIMEOUT_TEST_HANDSHAKE + SETTLE_SLACK)?;

    assert_eq!(state, SidecarState::Failed(FailureKind::Timeout));
    assert_eq!(
        supervisor.job_process_ids()?,
        Vec::<u32>::new(),
        "job not empty"
    );
    assert_exits(supervisor.child_pid(), "the silent sidecar")?;
    Ok(())
}

#[test]
fn exit_code_two_is_a_rejected_startup() -> TestResult {
    expect_failure("exit2", "exit2", FailureKind::StartupRejected)
}

#[test]
fn exit_code_three_keeps_its_code() -> TestResult {
    expect_failure(
        "exit3",
        "exit3",
        FailureKind::ExitedBeforeReady { code: Some(3) },
    )
}

#[test]
fn a_line_that_is_not_json_fails_and_kills() -> TestResult {
    expect_failure("garbage", "garbage", FailureKind::NotJson)
}

#[test]
fn an_oversized_line_fails_and_kills() -> TestResult {
    expect_failure("oversized", "oversized", FailureKind::LineTooLong)
}

#[test]
fn two_objects_on_one_line_fail_and_kill() -> TestResult {
    expect_failure("two_objects", "two-objects", FailureKind::NotJson)
}

#[test]
fn output_after_the_ready_line_fails_closed() -> TestResult {
    expect_failure(
        "ready_then_output",
        "ready-then-output",
        FailureKind::ProtocolViolation,
    )
}

#[test]
fn the_child_environment_is_exactly_the_allowlist() -> TestResult {
    // The fake writes the NAMES of the variables it finds, which proves that `env_clear()` took
    // effect and that Windows injected nothing else.
    let scratch = Scratch::new("env_report")?;
    let report = scratch.file("env.txt");
    let supervisor = Supervisor::start(fake_spec(
        &scratch,
        "env-report",
        Some(&report),
        TEST_HANDSHAKE,
    )?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());

    let state = supervisor.wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;
    assert_eq!(state, SidecarState::Ready { port: fake_port()? });

    let text = fs::read_to_string(&report)?;
    let found: BTreeSet<&str> = text.lines().collect();
    let allowed: BTreeSet<&str> = ALLOWED_ENV_KEYS.into_iter().collect();
    assert_eq!(found, allowed, "the child saw a different environment");
    supervisor.shutdown()?;
    Ok(())
}
