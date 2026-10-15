//! `SidecarHost::call` against the fake sidecar (chunk 6d). The fake only announces a port; a
//! separate loopback server answers on it. The call must be `not_ready` with no network call while
//! the sidecar is starting or stopped, and must reach exactly the announced port once it is ready.

mod common;
mod proxy_support;

use std::ffi::OsString;

use common::{Reaper, Scratch, TestResult, SETTLE_SLACK, TEST_HANDSHAKE};
use desktop_lib::host::{SidecarDirs, SidecarHost};
use desktop_lib::proxy::{ProxyError, ProxyReply, RouteRequest};
use proxy_support::{json_response, Server};

fn start_with(
    scratch: &Scratch,
    args: Vec<OsString>,
) -> Result<SidecarHost, Box<dyn std::error::Error>> {
    let index_dir = scratch.file("index");
    let dirs = SidecarDirs {
        log_dir: scratch.path(),
        index_dir: &index_dir,
        vault_dir: None,
    };
    SidecarHost::start(common::fake_sidecar_path(), args, &dirs, TEST_HANDSHAKE)
        .map_err(|kind| format!("the host did not start: {kind:?}").into())
}

fn call(host: &SidecarHost, json: &str) -> Result<ProxyReply, ProxyError> {
    let request = RouteRequest::from_json(json)?;
    tauri::async_runtime::block_on(host.call(&request))
}

#[test]
fn a_call_is_not_ready_while_starting_and_after_stop_and_makes_no_connection() -> TestResult {
    let scratch = Scratch::new("host_proxy_not_ready")?;
    let server = Server::start(json_response("200 OK", r#"{"state":"ready"}"#))?;
    // `late-ready` waits 1.5 s before the ready line, so the state is `Starting` right after start.
    // The server below is never told to the host, so it must see no connection at all.
    let host = start_with(&scratch, vec![OsString::from("late-ready")])?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());

    assert_eq!(
        call(&host, r#"{"route":"readiness"}"#),
        Err(ProxyError::NotReady)
    );

    host.supervisor()
        .wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;
    host.supervisor().shutdown()?;
    assert_eq!(
        call(&host, r#"{"route":"readiness"}"#),
        Err(ProxyError::NotReady)
    );
    assert_eq!(server.accepted(), 0);
    Ok(())
}

#[test]
fn a_ready_sidecar_is_called_on_the_port_it_announced() -> TestResult {
    let scratch = Scratch::new("host_proxy_ready")?;
    let server = Server::start(json_response("200 OK", r#"{"state":"ready"}"#))?;
    let host = start_with(
        &scratch,
        vec![
            OsString::from("ready-on"),
            OsString::from(server.port.to_string()),
        ],
    )?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());
    host.supervisor()
        .wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    let reply = call(&host, r#"{"route":"readiness"}"#)?;
    assert_eq!(reply.status, 200);
    assert_eq!(reply.body, serde_json::json!({"state": "ready"}));

    let requests = server.requests();
    assert_eq!(requests.len(), 1);
    let head = requests[0].to_ascii_lowercase();
    assert!(head.starts_with("get /readiness http/1.1"), "{head}");
    let token = head
        .lines()
        .find_map(|line| line.strip_prefix("x-grc-auth: "))
        .ok_or("the call carried no X-GRC-Auth header")?;
    assert_eq!(token.len(), 64);

    host.supervisor().shutdown()?;
    Ok(())
}
