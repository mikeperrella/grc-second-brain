//! `SidecarHost` against the fake sidecar: the health answers for each stage of its life
//! (M2 plan section 12). The real Python sidecar is covered by tests/real_sidecar.rs.
//!
//! The fake only announces a port; it never listens. The probe test picks a port that was free a
//! moment ago (bind to port 0, note the port, close the listener) and has the fake announce it, so
//! nothing on the machine can be answering there. A refused probe proves the host really probes once
//! the state is `Ready`, and that a failed probe is reported as `Unreachable`, not as healthy.

mod common;

use std::ffi::OsString;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::thread::JoinHandle;

use common::{
    assert_exits, fake_sidecar_path, Reaper, Scratch, TestResult, SETTLE_SLACK, TEST_HANDSHAKE,
};
use desktop_lib::folders::UserFolders;
use desktop_lib::health::{HealthError, HealthReply};
use desktop_lib::host::{SidecarDirs, SidecarHost};
use desktop_lib::sidecar::supervisor::{FailureKind, SidecarState};

fn start(scratch: &Scratch, mode: &str) -> Result<SidecarHost, Box<dyn std::error::Error>> {
    start_with(scratch, vec![OsString::from(mode)])
}

fn start_with(
    scratch: &Scratch,
    args: Vec<OsString>,
) -> Result<SidecarHost, Box<dyn std::error::Error>> {
    let index_dir = scratch.file("index");
    let dirs = SidecarDirs {
        log_dir: scratch.path(),
        index_dir: &index_dir,
        folders: UserFolders::default(),
    };
    let host = SidecarHost::start(fake_sidecar_path(), args, &dirs, TEST_HANDSHAKE)
        .map_err(|kind| format!("the host did not start: {kind:?}"))?;
    Ok(host)
}

fn health(host: &SidecarHost) -> Result<HealthReply, HealthError> {
    tauri::async_runtime::block_on(host.health())
}

fn settle(host: &SidecarHost) -> Result<SidecarState, Box<dyn std::error::Error>> {
    Ok(host
        .supervisor()
        .wait_until_terminal(TEST_HANDSHAKE + SETTLE_SLACK)?)
}

#[test]
fn health_is_starting_without_a_network_call_until_the_ready_line_arrives() -> TestResult {
    let scratch = Scratch::new("host_starting")?;
    // `late-ready` waits 1.5 s before the ready line, so the state is `Starting` right after start.
    let host = start(&scratch, "late-ready")?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());

    assert_eq!(host.supervisor().state()?, SidecarState::Starting);
    assert_eq!(health(&host), Ok(HealthReply::Starting));

    host.supervisor().shutdown()?;
    Ok(())
}

#[test]
fn a_ready_sidecar_is_probed_and_a_refused_probe_is_unreachable() -> TestResult {
    let scratch = Scratch::new("host_ready")?;
    // A port that was free a moment ago: bind to 0, note the port the OS chose, close the listener.
    let free_port = {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        listener.local_addr()?.port()
    };
    let host = start_with(
        &scratch,
        vec![
            OsString::from("ready-on"),
            OsString::from(free_port.to_string()),
        ],
    )?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());

    let state = host
        .supervisor()
        .wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;
    assert!(
        matches!(state, SidecarState::Ready { port } if port.get() == free_port),
        "{state:?}"
    );

    assert_eq!(health(&host), Err(HealthError::Unreachable));

    host.supervisor().shutdown()?;
    Ok(())
}

/// Answers ONE connection on a free loopback port with a valid health response and hands back the
/// request it received. If nothing ever connects, joining the handle would hang, so tests only join
/// it after the host has already answered.
fn one_shot_health_server() -> Result<(u16, JoinHandle<String>), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    let handle = std::thread::spawn(move || {
        let Ok((mut stream, _)) = listener.accept() else {
            return String::new();
        };
        let mut request = Vec::new();
        let mut buffer = [0u8; 1024];
        while !request.windows(4).any(|window| window == b"\r\n\r\n") && request.len() < 16 * 1024 {
            match stream.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(count) => request.extend_from_slice(&buffer[..count]),
            }
        }
        let body = br#"{"status":"ok"}"#;
        let head = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
            body.len()
        );
        let _ = stream.write_all(head.as_bytes());
        let _ = stream.write_all(body);
        String::from_utf8_lossy(&request).into_owned()
    });
    Ok((port, handle))
}

#[test]
fn a_ready_sidecar_is_probed_on_the_port_it_announced() -> TestResult {
    let scratch = Scratch::new("host_probe_port")?;
    let (port, server) = one_shot_health_server()?;
    let host = start_with(
        &scratch,
        vec![OsString::from("ready-on"), OsString::from(port.to_string())],
    )?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());
    host.supervisor()
        .wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    // `Healthy` can only come from the responder above: probing any other port is refused.
    assert_eq!(health(&host), Ok(HealthReply::Healthy));

    let request = server
        .join()
        .map_err(|_| "the responder thread panicked")?
        .to_ascii_lowercase();
    assert!(request.starts_with("get /health http/1.1"), "{request}");
    let token_header = request
        .lines()
        .find_map(|line| line.strip_prefix("x-grc-auth: "))
        .ok_or("the probe carried no X-GRC-Auth header")?;
    assert_eq!(
        token_header.len(),
        64,
        "the header is not a 64-character token"
    );
    assert!(token_header.bytes().all(|b| b.is_ascii_hexdigit()));

    host.supervisor().shutdown()?;
    Ok(())
}

#[test]
fn a_rejected_startup_is_reported_with_its_reason() -> TestResult {
    let scratch = Scratch::new("host_exit2")?;
    let host = start(&scratch, "exit2")?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());

    let state = settle(&host)?;
    assert_eq!(state, SidecarState::Failed(FailureKind::StartupRejected));

    assert_eq!(
        health(&host),
        Ok(HealthReply::Failed {
            reason: FailureKind::StartupRejected
        })
    );
    assert_exits(host.supervisor().child_pid(), "the sidecar")?;
    Ok(())
}

#[test]
fn a_stopped_sidecar_is_not_running() -> TestResult {
    let scratch = Scratch::new("host_stopped")?;
    let host = start(&scratch, "ready")?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());
    host.supervisor()
        .wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    host.supervisor().shutdown()?;

    assert_eq!(health(&host), Ok(HealthReply::NotRunning));
    assert_exits(host.supervisor().child_pid(), "the sidecar")?;
    Ok(())
}

#[test]
fn a_program_that_does_not_exist_is_a_spawn_failure_not_a_panic() -> TestResult {
    let scratch = Scratch::new("host_missing")?;
    let index_dir = scratch.file("index");
    let dirs = SidecarDirs {
        log_dir: scratch.path(),
        index_dir: &index_dir,
        folders: UserFolders::default(),
    };
    let result = SidecarHost::start(
        scratch.file("no_such_program.exe"),
        Vec::new(),
        &dirs,
        TEST_HANDSHAKE,
    );
    assert_eq!(result.err(), Some(FailureKind::SpawnFailed));
    Ok(())
}
