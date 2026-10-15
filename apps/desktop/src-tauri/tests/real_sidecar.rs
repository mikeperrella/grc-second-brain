//! The real Python sidecar, started through the same code the app uses (M2 plan sections 12 and 18).
//!
//! Everything else in this crate is tested against the fake sidecar. This is the one test that runs
//! `sidecar/.venv/Scripts/python.exe -I -m grc_sidecar` through `SidecarHost::start_dev`, which
//! includes the path form the resolver produces: `canonicalize` on Windows returns an extended-length
//! `\\?\C:\...` path, and whether the venv launcher starts correctly from it was untested until this
//! test (ADR-004). It is `#[ignore]` because it needs the dev venv; CI creates one and runs it with
//! `--ignored` (rust.yml), and so does the developer: `cargo test --test real_sidecar -- --ignored`.

mod common;

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::thread;
use std::time::{Duration, Instant};

use common::{
    assert_exits, wait_for_venv_sidecar_shape, Reaper, Scratch, TestResult, SHAPE_SETTLE_BOUND,
};
use desktop_lib::folders::UserFolders;
use desktop_lib::health::HealthReply;
use desktop_lib::host::{SidecarDirs, SidecarHost};
use desktop_lib::sidecar::job::{describe_processes, ProcessInfo};
use desktop_lib::sidecar::supervisor::SidecarState;

/// The sidecar's own handshake bound is 30 s; this adds slack for the wait on the state.
const READY_BOUND: Duration = Duration::from_secs(45);

/// How long to wait for the sidecar's listening port to stop accepting connections after it was
/// stopped. On Windows a listening socket can outlive the killed process for a moment (the M1.5
/// flaky-test fix), so this polls the real condition, bounded, instead of assuming it is instant.
const PORT_CLOSED_BOUND: Duration = Duration::from_secs(15);

/// Send `GET /health` WITHOUT the auth header and return the raw response bytes.
fn unauthenticated_health_request(port: u16) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(5))?;
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.write_all(
        format!("GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n")
            .as_bytes(),
    )?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response)?;
    Ok(response)
}

/// True once connecting to the port is refused (or times out): nothing is listening any more.
fn port_is_closed(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&address, Duration::from_millis(500)).is_err()
}

#[test]
#[ignore = "needs sidecar/.venv"]
fn the_real_sidecar_starts_answers_health_and_stops() -> TestResult {
    let scratch = Scratch::new("real_sidecar")?;
    let index_dir = scratch.file("index");
    let dirs = SidecarDirs {
        log_dir: scratch.path(),
        index_dir: &index_dir,
        folders: UserFolders::default(),
    };
    let host = SidecarHost::start_dev(&dirs)
        .map_err(|kind| format!("the real sidecar did not start: {kind:?}"))?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());

    // 1. It reaches Ready. A failure here prints the exact `FailureKind`.
    let state = host.supervisor().wait_until_settled(READY_BOUND)?;
    let SidecarState::Ready { port } = state else {
        return Err(format!("expected Ready, got {state:?}").into());
    };

    // 2. The job holds exactly: the launcher (the process the host started), ONE `python.exe` child
    //    of it (the real interpreter) and AT MOST one `conhost.exe` child (the hidden console that
    //    `CREATE_NO_WINDOW` still allocates; allowed, not required). Identity, not a bare count.
    //
    //    The job is read repeatedly for up to SHAPE_SETTLE_BOUND: while uvicorn starts, the
    //    interpreter runs `cmd.exe /c ver` through the standard library's `platform` module
    //    (ADR-004), and on a slow runner that short-lived child can still be alive when the ready
    //    line arrives (it was, on the first CI run). Anything still wrong at the end of the bound
    //    fails, and the failure lists every process with its parent and image name. Every pid seen
    //    in any read is remembered, so step 5 checks that all of them exited.
    let launcher = host.supervisor().child_pid();
    let mut seen: Vec<u32> = Vec::new();
    wait_for_venv_sidecar_shape(launcher, SHAPE_SETTLE_BOUND, || {
        let in_job = host.supervisor().job_process_ids()?;
        for pid in &in_job {
            if !seen.contains(pid) {
                seen.push(*pid);
                reaper.add(*pid);
            }
        }
        let mut described = describe_processes(&in_job)?;
        // A process that is in the job but missing from the snapshot (it exited between the two
        // calls) is kept in the list under a placeholder name, so it cannot hide from the check.
        for pid in &in_job {
            if !described.iter().any(|process| process.pid == *pid) {
                described.push(ProcessInfo {
                    pid: *pid,
                    parent_pid: 0,
                    exe: "<not in snapshot>".to_owned(),
                });
            }
        }
        Ok(described)
    })?;

    // 3. The host's client, with the token it generated, gets a valid health answer.
    let reply = tauri::async_runtime::block_on(host.health())?;
    assert_eq!(reply, HealthReply::Healthy);

    // 4. Negative: the same endpoint without the header is refused with 401 and no body.
    let response = unauthenticated_health_request(port.get())?;
    let text = String::from_utf8_lossy(&response);
    assert!(
        text.starts_with("HTTP/1.1 401"),
        "expected 401, got: {text}"
    );
    let body_start = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map(|index| index + 4)
        .ok_or("the 401 response had no header terminator")?;
    assert!(
        response[body_start..].is_empty(),
        "a 401 must carry no body: {text}"
    );

    // 5. Orderly stop: Stopped, both processes gone, the port closed.
    host.supervisor().shutdown()?;
    assert_eq!(host.supervisor().state()?, SidecarState::Stopped);
    assert_eq!(host.supervisor().job_process_ids()?, Vec::<u32>::new());
    for pid in &seen {
        assert_exits(*pid, "a sidecar process")?;
    }
    let deadline = Instant::now() + PORT_CLOSED_BOUND;
    let mut closed = port_is_closed(port.get());
    while !closed && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(50)); // pacing of a bounded poll on a real condition
        closed = port_is_closed(port.get());
    }
    assert!(closed, "the sidecar's port still accepts connections");
    Ok(())
}
