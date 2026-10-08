//! The Job Object flow when the HOST is already inside another job, as it is under a terminal, an
//! IDE or a CI runner (M2 plan section 3, "Nested jobs").
//!
//! This is its own file, and therefore its own test process, on purpose: placing the current
//! process in a job lasts until the process exits, and it must not affect any other test. There is
//! exactly one test here for the same reason.

mod common;

use common::{
    assert_exits, fake_spec, read_pid, Reaper, Scratch, TestResult, SETTLE_SLACK, TEST_HANDSHAKE,
};
use desktop_lib::sidecar::job::{current_process_is_in_a_job, place_current_process_in_plain_job};
use desktop_lib::sidecar::supervisor::{SidecarState, Supervisor};

#[test]
fn the_whole_flow_works_when_the_host_is_already_in_a_job() -> TestResult {
    let _outer = place_current_process_in_plain_job()?;
    assert!(
        current_process_is_in_a_job()?,
        "setup failed: this process is not in a job"
    );

    let scratch = Scratch::new("nested")?;
    let pid_file = scratch.file("grandchild.pid");
    let supervisor = Supervisor::start(fake_spec(
        &scratch,
        "grandchild",
        Some(&pid_file),
        TEST_HANDSHAKE,
    )?)?;
    let child = supervisor.child_pid();
    let mut reaper = Reaper::new();
    reaper.add(child);

    let state = supervisor.wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;
    assert!(matches!(state, SidecarState::Ready { .. }), "got {state:?}");
    let grandchild = read_pid(&pid_file)?;
    reaper.add(grandchild);
    let in_job = supervisor.job_process_ids()?;
    assert!(
        in_job.contains(&child) && in_job.contains(&grandchild),
        "{in_job:?}"
    );

    supervisor.shutdown()?;

    assert_exits(grandchild, "the grandchild")?;
    assert_exits(child, "the child")?;
    assert_eq!(supervisor.state()?, SidecarState::Stopped);
    Ok(())
}
