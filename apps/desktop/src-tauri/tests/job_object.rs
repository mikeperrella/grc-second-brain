//! The Job Object guarantee (M2 plan section 3): the sidecar's whole process tree dies with the job,
//! whichever way the job ends, and the job is configured with exactly one limit.

mod common;

use std::io::BufReader;

use common::{
    assert_exits, fake_spec, read_pid, Reaper, Scratch, TestResult, SETTLE_SLACK, TEST_HANDSHAKE,
};
use desktop_lib::ready::read_ready_line;
use desktop_lib::sidecar::job::{Job, EMPTY_WAIT_BOUND};
use desktop_lib::sidecar::spawn::spawn_in_job;
use desktop_lib::sidecar::supervisor::{SidecarState, Supervisor};

/// `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, written as a literal on purpose: the test must not share
/// a constant with the code it checks.
const KILL_ON_JOB_CLOSE_FLAG: u32 = 0x2000;

#[test]
fn the_job_has_exactly_the_kill_on_close_limit_and_no_ui_limits() -> TestResult {
    let job = Job::new()?;
    // Exactly 0x2000: KILL_ON_JOB_CLOSE set, BREAKAWAY_OK (0x800) and SILENT_BREAKAWAY_OK (0x1000)
    // clear, and no other limit.
    assert_eq!(job.limit_flags()?, KILL_ON_JOB_CLOSE_FLAG);
    assert_eq!(job.ui_restrictions()?, 0);
    assert_eq!(job.active_processes()?, 0);
    Ok(())
}

#[test]
fn the_grandchild_dies_when_the_supervisor_shuts_the_job_down() -> TestResult {
    let scratch = Scratch::new("grandchild_shutdown")?;
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

    // The fake wrote the grandchild's PID before it printed ready, so the file is there now.
    let grandchild = read_pid(&pid_file)?;
    reaper.add(grandchild);
    let in_job = supervisor.job_process_ids()?;
    assert!(
        in_job.contains(&child),
        "the child is not in the job: {in_job:?}"
    );
    assert!(
        in_job.contains(&grandchild),
        "the grandchild is not in the job: {in_job:?}"
    );

    supervisor.shutdown()?;

    assert_exits(grandchild, "the grandchild")?;
    assert_exits(child, "the child")?;
    Ok(())
}

#[test]
fn the_grandchild_dies_when_the_job_handle_is_dropped_without_terminate() -> TestResult {
    // The host-was-killed backstop: nobody calls `terminate`. The operating system closes the job
    // handle and KILL_ON_JOB_CLOSE must kill the tree.
    let scratch = Scratch::new("grandchild_drop")?;
    let pid_file = scratch.file("grandchild.pid");
    let spec = fake_spec(&scratch, "grandchild", Some(&pid_file), TEST_HANDSHAKE)?;
    let job = Job::new()?;
    let mut child = spawn_in_job(&spec, &job)?;
    let child_pid = child.id();
    let mut reaper = Reaper::new();
    reaper.add(child_pid);

    // Wait for the ready line, which the fake prints after writing the grandchild's PID.
    let stdout = child.stdout.take().ok_or("the child has no stdout pipe")?;
    read_ready_line(&mut BufReader::new(stdout))?;
    let grandchild = read_pid(&pid_file)?;
    reaper.add(grandchild);
    let in_job = job.process_ids()?;
    assert!(
        in_job.contains(&child_pid) && in_job.contains(&grandchild),
        "{in_job:?}"
    );

    drop(job);

    assert_exits(grandchild, "the grandchild")?;
    assert_exits(child_pid, "the child")?;
    child.wait()?;
    Ok(())
}

#[test]
fn emptiness_is_decided_by_the_query_even_if_no_port_message_ever_arrives() -> TestResult {
    // With the port never associated to the job, no message can be delivered. The wait must still
    // finish, because the authoritative query (not the port) decides that the job is empty.
    let scratch = Scratch::new("no_port")?;
    let spec = fake_spec(&scratch, "hold", None, TEST_HANDSHAKE)?;
    let job = Job::new_without_port_association()?;
    let mut child = spawn_in_job(&spec, &job)?;
    let mut reaper = Reaper::new();
    reaper.add(child.id());
    assert!(job.process_ids()?.contains(&child.id()));

    job.terminate()?;

    job.wait_until_empty(EMPTY_WAIT_BOUND)?;
    assert_eq!(job.active_processes()?, 0);
    child.wait()?;
    assert_exits(child.id(), "the child")?;
    Ok(())
}

#[test]
fn waiting_on_a_job_that_is_never_terminated_gives_up_at_the_bound() -> TestResult {
    let scratch = Scratch::new("never_empty")?;
    let spec = fake_spec(&scratch, "hold", None, TEST_HANDSHAKE)?;
    let job = Job::new()?;
    let mut child = spawn_in_job(&spec, &job)?;
    let mut reaper = Reaper::new();
    reaper.add(child.id());

    let result = job.wait_until_empty(std::time::Duration::from_millis(300));

    assert_eq!(
        result,
        Err(desktop_lib::sidecar::job::JobError::NotEmptyInTime)
    );
    job.terminate()?;
    child.wait()?;
    Ok(())
}
