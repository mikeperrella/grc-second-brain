//! The proxied call must ignore proxy environment variables, like the health probe (CLAUDE.md 3.2,
//! and `tests/health_proxy.rs`, which covers the shared client directly). This file is its own test
//! binary, so changing the process environment here cannot affect any other test.
//!
//! `NO_PROXY` is removed first, so a bypass list inherited from the machine cannot make the test
//! pass, then every proxy variable points at a loopback "proxy" that counts connections. A client
//! that honoured the environment would connect to the proxy instead of the real server.

mod proxy_support;

use std::error::Error;

use desktop_lib::proxy::{call, RouteRequest};
use desktop_lib::sidecar_client::{LoopbackEndpoint, SidecarClient};
use desktop_lib::token::AuthToken;
use proxy_support::{json_response, Server};

#[test]
fn a_proxied_call_ignores_proxy_env() -> Result<(), Box<dyn Error>> {
    let target = Server::start(json_response("200 OK", r#"{"state":"ready"}"#))?;
    let proxy = Server::start(json_response("502 Bad Gateway", "{}"))?;

    for name in ["NO_PROXY", "no_proxy"] {
        std::env::remove_var(name);
    }
    let proxy_url = format!("http://127.0.0.1:{}", proxy.port);
    for name in [
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ] {
        std::env::set_var(name, &proxy_url);
    }

    let token = AuthToken::generate()?;
    let client = SidecarClient::new(&token)?;
    let endpoint = LoopbackEndpoint::new(target.port).ok_or("port 0")?;
    let request = RouteRequest::from_json(r#"{"route":"readiness"}"#).map_err(|_| "bad request")?;
    let reply = tauri::async_runtime::block_on(call(&client, &endpoint, &request))
        .map_err(|_| "the call failed")?;

    assert_eq!(reply.status, 200);
    assert_eq!(
        target.accepted(),
        1,
        "the real server was not contacted directly"
    );
    assert_eq!(proxy.accepted(), 0, "the client sent traffic to the proxy");
    Ok(())
}
