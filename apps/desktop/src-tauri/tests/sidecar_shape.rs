//! The identity check on the sidecar's job (M2c): WHICH processes it holds, not only how many.
//!
//! `check_venv_sidecar_shape` (tests/common) is what tests/real_sidecar.rs applies to the real
//! Python sidecar. This file proves the check itself, without Python: synthetic process lists for
//! every accepted and rejected shape, and real jobs started with the fake sidecar, where the
//! processes are real but are not Python (so a missing interpreter and an unexpected extra process
//! are real cases, not made up).

mod common;

use common::{
    check_venv_sidecar_shape, fake_spec, Reaper, Scratch, ShapeError, TestResult, SETTLE_SLACK,
    TEST_HANDSHAKE,
};
use desktop_lib::sidecar::job::{describe_processes, ProcessInfo};
use desktop_lib::sidecar::supervisor::{SidecarState, Supervisor};

const LAUNCHER: u32 = 100;

fn process(pid: u32, parent_pid: u32, exe: &str) -> ProcessInfo {
    ProcessInfo {
        pid,
        parent_pid,
        exe: exe.to_owned(),
    }
}

fn launcher() -> ProcessInfo {
    process(LAUNCHER, 1, "python.exe")
}

/// The same processes, with the one `pid` presented as `python.exe`. The fake sidecar's image is
/// `fake_sidecar.exe`; presenting it as the venv launcher lets a real job exercise the rules that
/// come AFTER the launcher check (interpreter missing, unexpected extra process).
fn presented_as_launcher(described: &[ProcessInfo], pid: u32) -> Vec<ProcessInfo> {
    described
        .iter()
        .map(|info| {
            if info.pid == pid {
                ProcessInfo {
                    exe: "python.exe".to_owned(),
                    ..info.clone()
                }
            } else {
                info.clone()
            }
        })
        .collect()
}

#[test]
fn the_launcher_an_interpreter_and_a_console_host_are_accepted() {
    let processes = [
        launcher(),
        process(101, LAUNCHER, "python.exe"),
        process(102, LAUNCHER, "conhost.exe"),
    ];
    assert_eq!(check_venv_sidecar_shape(LAUNCHER, &processes), Ok(()));
}

#[test]
fn the_console_host_is_allowed_but_not_required() {
    let processes = [launcher(), process(101, LAUNCHER, "python.exe")];
    assert_eq!(check_venv_sidecar_shape(LAUNCHER, &processes), Ok(()));
}

#[test]
fn image_names_are_compared_without_regard_to_case() {
    let processes = [
        process(LAUNCHER, 1, "Python.EXE"),
        process(101, LAUNCHER, "PYTHON.exe"),
        process(102, LAUNCHER, "ConHost.exe"),
    ];
    assert_eq!(check_venv_sidecar_shape(LAUNCHER, &processes), Ok(()));
}

#[test]
fn a_job_with_only_the_launcher_is_missing_its_interpreter() {
    assert_eq!(
        check_venv_sidecar_shape(LAUNCHER, &[launcher()]),
        Err(ShapeError::InterpreterMissing)
    );
    // A console host alone does not stand in for the interpreter.
    let with_console = [launcher(), process(102, LAUNCHER, "conhost.exe")];
    assert_eq!(
        check_venv_sidecar_shape(LAUNCHER, &with_console),
        Err(ShapeError::InterpreterMissing)
    );
}

#[test]
fn an_unexpected_extra_process_is_rejected() {
    let processes = [
        launcher(),
        process(101, LAUNCHER, "python.exe"),
        process(102, LAUNCHER, "conhost.exe"),
        process(103, LAUNCHER, "cmd.exe"),
    ];
    assert_eq!(
        check_venv_sidecar_shape(LAUNCHER, &processes),
        Err(ShapeError::UnexpectedProcess {
            pid: 103,
            exe: "cmd.exe".to_owned()
        })
    );
}

#[test]
fn a_second_interpreter_or_a_second_console_host_is_rejected() {
    let two_interpreters = [
        launcher(),
        process(101, LAUNCHER, "python.exe"),
        process(102, LAUNCHER, "python.exe"),
    ];
    assert_eq!(
        check_venv_sidecar_shape(LAUNCHER, &two_interpreters),
        Err(ShapeError::TooManyInterpreters { count: 2 })
    );
    let two_consoles = [
        launcher(),
        process(101, LAUNCHER, "python.exe"),
        process(102, LAUNCHER, "conhost.exe"),
        process(103, LAUNCHER, "conhost.exe"),
    ];
    assert_eq!(
        check_venv_sidecar_shape(LAUNCHER, &two_consoles),
        Err(ShapeError::TooManyConsoleHosts { count: 2 })
    );
}

#[test]
fn a_process_that_is_not_a_child_of_the_launcher_is_rejected() {
    // A `python.exe` in the job whose parent is something else (a grandchild, or a stranger).
    let processes = [launcher(), process(101, 555, "python.exe")];
    assert_eq!(
        check_venv_sidecar_shape(LAUNCHER, &processes),
        Err(ShapeError::NotAChildOfTheLauncher {
            pid: 101,
            parent_pid: 555
        })
    );
}

#[test]
fn a_missing_or_non_python_launcher_is_rejected() {
    let only_children = [process(101, LAUNCHER, "python.exe")];
    assert_eq!(
        check_venv_sidecar_shape(LAUNCHER, &only_children),
        Err(ShapeError::LauncherMissing)
    );
    let wrong_launcher = [
        process(LAUNCHER, 1, "fake_sidecar.exe"),
        process(101, LAUNCHER, "python.exe"),
    ];
    assert_eq!(
        check_venv_sidecar_shape(LAUNCHER, &wrong_launcher),
        Err(ShapeError::LauncherNotPython {
            exe: "fake_sidecar.exe".to_owned()
        })
    );
}

#[test]
fn describe_processes_reports_this_process_with_its_parent_and_image_name() -> TestResult {
    let me = std::process::id();
    let found = describe_processes(&[me])?;
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].pid, me);
    assert_ne!(found[0].parent_pid, 0);
    assert!(
        found[0].exe.to_ascii_lowercase().ends_with(".exe"),
        "{:?}",
        found[0].exe
    );
    // An id that cannot exist (the System Idle pseudo-process aside, 0xFFFF_FFFC is never a pid).
    assert!(describe_processes(&[0xFFFF_FFFC])?.is_empty());
    Ok(())
}

/// A real job holding ONE real process (the fake sidecar, image `fake_sidecar.exe`).
#[test]
fn a_real_job_holding_only_the_launcher_has_no_interpreter() -> TestResult {
    let scratch = Scratch::new("shape_launcher_only")?;
    let supervisor = Supervisor::start(fake_spec(&scratch, "ready", None, TEST_HANDSHAKE)?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());
    supervisor.wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    let in_job = supervisor.job_process_ids()?;
    let described = describe_processes(&in_job)?;
    assert_eq!(described.len(), in_job.len(), "{described:?}");
    assert_eq!(described[0].exe.to_ascii_lowercase(), "fake_sidecar.exe");

    // As it is, the launcher check fails first: the fake is not `python.exe`.
    assert!(matches!(
        check_venv_sidecar_shape(supervisor.child_pid(), &described),
        Err(ShapeError::LauncherNotPython { .. })
    ));
    // Presented as the launcher, the job holds nothing that is a `python.exe` child (at most a
    // console host), so the only possible verdict is "interpreter missing".
    let relabelled = presented_as_launcher(&described, supervisor.child_pid());
    assert_eq!(
        check_venv_sidecar_shape(supervisor.child_pid(), &relabelled),
        Err(ShapeError::InterpreterMissing)
    );
    supervisor.shutdown()?;
    Ok(())
}

/// A real job holding two real processes: the fake and a second copy of itself it started.
#[test]
fn a_real_job_with_an_unexpected_extra_process_is_rejected() -> TestResult {
    let scratch = Scratch::new("shape_extra")?;
    let pid_file = scratch.file("grandchild.pid");
    let supervisor = Supervisor::start(fake_spec(
        &scratch,
        "grandchild",
        Some(&pid_file),
        TEST_HANDSHAKE,
    )?)?;
    let mut reaper = Reaper::new();
    reaper.add(supervisor.child_pid());
    let state = supervisor.wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;
    assert!(matches!(state, SidecarState::Ready { .. }), "{state:?}");
    reaper.add(common::read_pid(&pid_file)?);

    // The fake, its copy, and (like every console child started with CREATE_NO_WINDOW) a
    // `conhost.exe`: at least the first two, and every one of them described.
    let in_job = supervisor.job_process_ids()?;
    let described = describe_processes(&in_job)?;
    assert!(in_job.len() >= 2, "{in_job:?}");
    assert_eq!(described.len(), in_job.len(), "{described:?}");

    // Judge it as if the first process were the launcher by treating the fake's image name as
    // `python.exe`: the extra process is then a child with the wrong image, and must be rejected.
    let relabelled = presented_as_launcher(&described, supervisor.child_pid());
    let verdict = check_venv_sidecar_shape(supervisor.child_pid(), &relabelled);
    assert!(
        matches!(&verdict, Err(ShapeError::UnexpectedProcess { exe, .. }) if exe.eq_ignore_ascii_case("fake_sidecar.exe")),
        "{verdict:?}"
    );
    supervisor.shutdown()?;
    Ok(())
}
