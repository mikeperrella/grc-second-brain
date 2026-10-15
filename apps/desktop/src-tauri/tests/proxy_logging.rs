//! G29, Rust half (F34-5): the proxy and the HTTP libraries must not log request or reply content.
//!
//! This file is its own test binary, so it can install the process-wide `log` logger. The logger
//! keeps every record at the most detailed level (`Trace`, with the global maximum set to match).
//! A canary string is put in the file name the window sends and in the sidecar's replies (a 200
//! body, a 500 body and a status line). After the calls, no captured record may contain the canary
//! or the token. The token is read from the request the fake server recorded, because the token
//! type does not expose it outside the crate.
//!
//! What this can and cannot see: it captures the `log` facade. Whether hyper, reqwest and tokio emit
//! `log` records depends on their versions and features, so the test also requires at least one
//! record from the proxy itself (target `desktop_lib`), which proves the capture works and the
//! check is not vacuous. It would not see output from a library that uses another logging system.

mod proxy_support;

use std::error::Error;
use std::sync::Mutex;

use desktop_lib::proxy::{call, RouteRequest};
use desktop_lib::sidecar_client::{LoopbackEndpoint, SidecarClient};
use desktop_lib::token::AuthToken;
use proxy_support::{json_response, Server};

const CANARY: &str = "CANARY-0c9e41-do-not-leak";

struct Capture {
    records: Mutex<Vec<(String, String)>>,
}

impl log::Log for Capture {
    fn enabled(&self, _metadata: &log::Metadata<'_>) -> bool {
        true
    }

    fn log(&self, record: &log::Record<'_>) {
        if let Ok(mut records) = self.records.lock() {
            records.push((record.target().to_owned(), record.args().to_string()));
        }
    }

    fn flush(&self) {}
}

static CAPTURE: Capture = Capture {
    records: Mutex::new(Vec::new()),
};

fn run_call(
    token: &AuthToken,
    answer: Vec<u8>,
    json: &str,
) -> Result<(String, String), Box<dyn Error>> {
    let server = Server::start(answer)?;
    let client = SidecarClient::new(token)?;
    let endpoint = LoopbackEndpoint::new(server.port).ok_or("port 0")?;
    let request = RouteRequest::from_json(json).map_err(|_| "bad test request")?;
    let outcome = tauri::async_runtime::block_on(call(&client, &endpoint, &request));
    let requests = server.requests();
    let head = requests
        .first()
        .cloned()
        .ok_or("no request reached the server")?;
    Ok((format!("{outcome:?}"), head))
}

#[test]
fn no_log_record_at_the_most_detailed_level_contains_the_canary_or_the_token(
) -> Result<(), Box<dyn Error>> {
    log::set_logger(&CAPTURE).map_err(|_| "a logger was already installed")?;
    log::set_max_level(log::LevelFilter::Trace);

    let token = AuthToken::generate()?;
    let draft = format!(r#"{{"route":"draft_rows","file":"{CANARY}.xlsx"}}"#);
    let mut heads = Vec::new();
    for (answer, json) in [
        (
            json_response("200 OK", &format!(r#"{{"rows":[],"note":"{CANARY}"}}"#)),
            draft.clone(),
        ),
        (
            json_response(
                "500 Internal Server Error",
                &format!(r#"{{"detail":"{CANARY}"}}"#),
            ),
            draft.clone(),
        ),
        (
            json_response("409 Conflict", &format!(r#"{{"error":"{CANARY}"}}"#)),
            r#"{"route":"vault_index_start"}"#.to_owned(),
        ),
        (
            json_response("200 OK", CANARY),
            r#"{"route":"readiness"}"#.to_owned(),
        ),
    ] {
        let (_outcome, head) = run_call(&token, answer, &json)?;
        heads.push(head);
    }

    // The token as the sidecar saw it, from the first recorded request.
    let token_text = heads[0]
        .lines()
        .find_map(|line| {
            line.to_ascii_lowercase()
                .strip_prefix("x-grc-auth: ")
                .map(str::to_owned)
        })
        .ok_or("the request carried no token header")?;
    assert_eq!(token_text.len(), 64);
    // The canary really was sent in the request (so the check below is about logging, not about
    // the canary never having been in play).
    assert!(
        heads[0].contains(CANARY),
        "the canary was not in the request"
    );

    let records = CAPTURE.records.lock().map_err(|_| "poisoned")?;
    let mut targets: Vec<&str> = records.iter().map(|(target, _)| target.as_str()).collect();
    targets.sort_unstable();
    targets.dedup();
    eprintln!(
        "captured {} records from targets {targets:?}",
        records.len()
    );

    assert!(
        records
            .iter()
            .any(|(target, _)| target.starts_with("desktop_lib")),
        "no record from the proxy itself was captured: the capture is not working"
    );
    for (target, message) in records.iter() {
        assert!(
            !message.contains(CANARY),
            "canary in a record from {target}: {message}"
        );
        assert!(
            !message.to_ascii_lowercase().contains(&token_text),
            "token in a record from {target}"
        );
    }
    Ok(())
}
