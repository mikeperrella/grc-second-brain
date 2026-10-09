//! The host's health check of the sidecar (M2 plan section 7, CLAUDE.md 3.2 and 3.4).
//!
//! One GET to `http://127.0.0.1:{port}/health` with the `X-GRC-Auth` header, and a strict reading of
//! the answer. Everything is built so the result cannot carry a secret or free text to the window:
//!
//! * [`LoopbackEndpoint`] is the only way to name a URL. It takes a port and nothing else, so no
//!   caller can point the client at another host.
//! * The client has no proxy (`.no_proxy()`), no redirects, short timeouts and a 1 KiB body cap.
//! * The token is put in a header marked sensitive and is never part of an error.
//! * [`HealthReply`] and [`HealthError`] hold only unit variants (plus the already secret-free
//!   [`FailureKind`]); the reqwest error is dropped, because it can carry the URL.

use std::fmt;
use std::num::NonZeroU16;
use std::time::Duration;

use reqwest::header::{HeaderName, HeaderValue};
use reqwest::redirect::Policy;
use reqwest::StatusCode;
use serde::Serialize;

use crate::sidecar::supervisor::FailureKind;
use crate::token::AuthToken;

/// The header the sidecar's auth middleware checks (CLAUDE.md 3.4). `from_static` requires lowercase;
/// HTTP header names are case-insensitive.
const AUTH_HEADER: HeaderName = HeaderName::from_static("x-grc-auth");

/// The real `/health` body is 15 bytes. Anything larger than this is not our sidecar.
const MAX_BODY: usize = 1024;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(5);

/// The only address the host ever contacts: `http://127.0.0.1:{port}/health`. There is deliberately
/// no constructor that takes a host or a string.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LoopbackEndpoint(NonZeroU16);

impl LoopbackEndpoint {
    /// `None` for port 0 (never a real listening port; the sidecar's ready line rejects it too).
    pub fn new(port: u16) -> Option<Self> {
        NonZeroU16::new(port).map(Self)
    }

    pub(crate) fn from_port(port: NonZeroU16) -> Self {
        Self(port)
    }
}

impl fmt::Display for LoopbackEndpoint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "http://127.0.0.1:{}/health", self.0)
    }
}

/// What the window is told about the sidecar. Serialized as `{"status":"healthy"}`,
/// `{"status":"failed","reason":{"kind":"timeout"}}` and so on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum HealthReply {
    /// The sidecar has not announced its port yet. No network call was made.
    Starting,
    /// `/health` answered 200 with exactly `{"status":"ok"}`.
    Healthy,
    /// The sidecar was stopped on purpose.
    NotRunning,
    /// The sidecar failed (or never started); `reason` says how, without any detail text.
    Failed { reason: FailureKind },
}

/// Why a health probe of a sidecar that claims to be running did not succeed. Unit variants only.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, thiserror::Error)]
#[serde(tag = "error", rename_all = "snake_case")]
pub enum HealthError {
    #[error("the sidecar rejected the host's credentials")]
    Unauthorized,
    #[error("the sidecar did not accept a connection")]
    Unreachable,
    #[error("the sidecar did not answer in time")]
    Timeout,
    #[error("the sidecar's answer was not the expected health response")]
    BadResponse,
    #[error("the host could not build its health request")]
    Internal,
}

/// Parse a complete response body: a JSON OBJECT with exactly one key, `status`, whose value is
/// the string `"ok"`. Trailing whitespace is accepted (JSON allows it); anything else after the
/// value is an error.
///
/// This reads into a `Value` instead of a derived `#[derive(Deserialize)]` struct on purpose: serde
/// also lets a struct be filled from a JSON ARRAY (`["ok"]`), which is not the contract.
fn parse_body(body: &[u8]) -> Result<(), HealthError> {
    let value: serde_json::Value =
        serde_json::from_slice(body).map_err(|_| HealthError::BadResponse)?;
    let serde_json::Value::Object(fields) = value else {
        return Err(HealthError::BadResponse);
    };
    match (fields.len(), fields.get("status")) {
        (1, Some(serde_json::Value::String(word))) if word == "ok" => Ok(()),
        _ => Err(HealthError::BadResponse),
    }
}

fn classify(error: &reqwest::Error) -> HealthError {
    // `is_connect` first: a connect that times out is still "could not connect". On Windows a
    // refused connection takes about 2 s to be reported, which is our connect timeout, so checking
    // `is_timeout` first would call a closed port a timeout.
    if error.is_connect() {
        HealthError::Unreachable
    } else if error.is_timeout() {
        HealthError::Timeout
    } else {
        HealthError::BadResponse
    }
}

/// The host's HTTP client for the sidecar. Build one when the sidecar is ready and reuse it.
pub struct HealthClient {
    client: reqwest::Client,
    auth: HeaderValue,
}

impl HealthClient {
    pub fn new(token: &AuthToken) -> Result<Self, HealthError> {
        Self::build(token, CONNECT_TIMEOUT, REQUEST_TIMEOUT)
    }

    /// Separate from `new` so tests can use short timeouts. Every client in the crate is built
    /// here (the Semgrep rule `grc-rs-reqwest-without-no-proxy` checks the chain).
    fn build(
        token: &AuthToken,
        connect_timeout: Duration,
        timeout: Duration,
    ) -> Result<Self, HealthError> {
        // The second of the token's two intended readers (the first is the child environment).
        let mut auth = HeaderValue::from_str(token.expose()).map_err(|_| HealthError::Internal)?;
        auth.set_sensitive(true);
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(Policy::none())
            .connect_timeout(connect_timeout)
            .timeout(timeout)
            .build()
            .map_err(|_| HealthError::Internal)?;
        Ok(Self { client, auth })
    }

    /// Ask the sidecar if it is healthy. `Ok(Healthy)` only for a 200 with exactly
    /// `{"status":"ok"}`.
    pub async fn check(&self, endpoint: &LoopbackEndpoint) -> Result<HealthReply, HealthError> {
        let mut response = self
            .client
            .get(endpoint.to_string())
            .header(AUTH_HEADER, self.auth.clone())
            .send()
            .await
            .map_err(|error| classify(&error))?;

        let status = response.status();
        if status == StatusCode::UNAUTHORIZED {
            // The body is never read: the sidecar sends none, and nothing in it is ours to use.
            return Err(HealthError::Unauthorized);
        }
        if status != StatusCode::OK {
            return Err(HealthError::BadResponse);
        }
        // A declared length over the cap is refused up front; the loop below enforces the cap for
        // responses with no length (chunked) or a wrong one.
        if response
            .content_length()
            .is_some_and(|length| length > MAX_BODY as u64)
        {
            return Err(HealthError::BadResponse);
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|error| classify(&error))? {
            if body.len() + chunk.len() > MAX_BODY {
                return Err(HealthError::BadResponse);
            }
            body.extend_from_slice(&chunk);
        }
        parse_body(&body)?;
        Ok(HealthReply::Healthy)
    }
}

impl fmt::Debug for HealthClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Neither field is printed: one holds the token.
        f.debug_struct("HealthClient").finish_non_exhaustive()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex};
    use std::thread::JoinHandle;

    fn run<F: std::future::Future>(future: F) -> F::Output {
        tauri::async_runtime::block_on(future)
    }

    /// What the fake server does after reading a request.
    enum Reply {
        /// Write these bytes and close.
        Bytes(Vec<u8>),
        /// Say nothing and keep the connection open until the client closes it.
        HoldOpen,
    }

    /// A one-thread HTTP server on 127.0.0.1 that records every request it receives.
    struct FakeServer {
        port: u16,
        requests: Arc<Mutex<Vec<String>>>,
        accepted: Arc<AtomicUsize>,
        stop: Arc<AtomicBool>,
        thread: Option<JoinHandle<()>>,
    }

    impl FakeServer {
        fn start(reply: Reply) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let port = listener.local_addr().unwrap().port();
            let requests = Arc::new(Mutex::new(Vec::new()));
            let accepted = Arc::new(AtomicUsize::new(0));
            let stop = Arc::new(AtomicBool::new(false));
            let (r, a, s) = (requests.clone(), accepted.clone(), stop.clone());
            let thread = std::thread::spawn(move || {
                while let Ok((stream, _)) = listener.accept() {
                    if s.load(Ordering::SeqCst) {
                        break;
                    }
                    a.fetch_add(1, Ordering::SeqCst);
                    serve(stream, &reply, &r);
                }
            });
            Self {
                port,
                requests,
                accepted,
                stop,
                thread: Some(thread),
            }
        }

        fn endpoint(&self) -> LoopbackEndpoint {
            LoopbackEndpoint::new(self.port).unwrap()
        }

        fn requests(&self) -> Vec<String> {
            self.requests.lock().unwrap().clone()
        }

        fn accepted(&self) -> usize {
            self.accepted.load(Ordering::SeqCst)
        }
    }

    impl Drop for FakeServer {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::SeqCst);
            // Wake the blocked `accept` so the thread can see the flag and end.
            let _ = TcpStream::connect(("127.0.0.1", self.port));
            if let Some(thread) = self.thread.take() {
                let _ = thread.join();
            }
        }
    }

    fn serve(mut stream: TcpStream, reply: &Reply, requests: &Mutex<Vec<String>>) {
        let mut request = Vec::new();
        let mut buffer = [0u8; 1024];
        while !request.windows(4).any(|w| w == b"\r\n\r\n") && request.len() < 16 * 1024 {
            match stream.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(count) => request.extend_from_slice(&buffer[..count]),
            }
        }
        requests
            .lock()
            .unwrap()
            .push(String::from_utf8_lossy(&request).into_owned());
        match reply {
            Reply::Bytes(bytes) => {
                let _ = stream.write_all(bytes);
                let _ = stream.flush();
            }
            Reply::HoldOpen => while matches!(stream.read(&mut buffer), Ok(n) if n > 0) {},
        }
    }

    fn response(status_line: &str, extra_headers: &[&str], body: &[u8]) -> Reply {
        let mut bytes = format!("HTTP/1.1 {status_line}\r\n").into_bytes();
        for header in extra_headers {
            bytes.extend_from_slice(format!("{header}\r\n").as_bytes());
        }
        bytes.extend_from_slice(format!("Content-Length: {}\r\n\r\n", body.len()).as_bytes());
        bytes.extend_from_slice(body);
        Reply::Bytes(bytes)
    }

    fn chunked(body: &[u8]) -> Reply {
        let mut bytes = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n".to_vec();
        for piece in body.chunks(100) {
            bytes.extend_from_slice(format!("{:x}\r\n", piece.len()).as_bytes());
            bytes.extend_from_slice(piece);
            bytes.extend_from_slice(b"\r\n");
        }
        bytes.extend_from_slice(b"0\r\n\r\n");
        Reply::Bytes(bytes)
    }

    fn check(server: &FakeServer, token: &AuthToken) -> Result<HealthReply, HealthError> {
        let client = HealthClient::new(token).unwrap();
        run(client.check(&server.endpoint()))
    }

    const OK_BODY: &[u8] = br#"{"status":"ok"}"#;

    #[test]
    fn a_valid_answer_is_healthy_and_the_request_carries_the_token() {
        let token = AuthToken::generate().unwrap();
        let server = FakeServer::start(response(
            "200 OK",
            &["Content-Type: application/json"],
            OK_BODY,
        ));

        assert_eq!(check(&server, &token), Ok(HealthReply::Healthy));

        let requests = server.requests();
        assert_eq!(requests.len(), 1);
        let request = requests[0].to_ascii_lowercase();
        assert!(request.starts_with("get /health http/1.1"), "{request}");
        assert!(
            request.contains(&format!("host: 127.0.0.1:{}", server.port)),
            "{request}"
        );
        assert!(
            request.contains(&format!("x-grc-auth: {}", token.expose())),
            "the token header is missing: {request}"
        );
    }

    #[test]
    fn wrong_bodies_are_bad_responses() {
        let cases: [&[u8]; 8] = [
            br#"{"status":"ok","extra":1}"#,
            br#"{"status":"degraded"}"#,
            br#"{"status":1}"#,
            br#"{}"#,
            br#"{"status":"ok"}{"status":"ok"}"#,
            br#"["ok"]"#,
            b"not json",
            b"",
        ];
        for body in cases {
            let server = FakeServer::start(response("200 OK", &[], body));
            let token = AuthToken::generate().unwrap();
            assert_eq!(
                check(&server, &token),
                Err(HealthError::BadResponse),
                "{}",
                String::from_utf8_lossy(body)
            );
        }
    }

    #[test]
    fn a_status_other_than_200_is_a_bad_response() {
        for status in [
            "500 Internal Server Error",
            "204 No Content",
            "404 Not Found",
        ] {
            let server = FakeServer::start(response(status, &[], OK_BODY));
            let token = AuthToken::generate().unwrap();
            assert_eq!(
                check(&server, &token),
                Err(HealthError::BadResponse),
                "{status}"
            );
        }
    }

    #[test]
    fn a_401_is_unauthorized_even_with_a_huge_body() {
        // A body over the cap would be a BadResponse if it were read; Unauthorized proves it is not.
        let body = vec![b'x'; 5000];
        let server = FakeServer::start(response("401 Unauthorized", &[], &body));
        let token = AuthToken::generate().unwrap();
        assert_eq!(check(&server, &token), Err(HealthError::Unauthorized));
    }

    #[test]
    fn a_declared_length_over_the_cap_is_refused() {
        let mut body = OK_BODY.to_vec();
        body.resize(2048, b' '); // still valid JSON: only the size is wrong
        let server = FakeServer::start(response("200 OK", &[], &body));
        let token = AuthToken::generate().unwrap();
        assert_eq!(check(&server, &token), Err(HealthError::BadResponse));
    }

    #[test]
    fn a_chunked_body_over_the_cap_is_refused() {
        // No Content-Length, so only the read loop can stop it. The padding is whitespace after a
        // valid object: without the cap this would parse and be reported Healthy.
        let mut body = OK_BODY.to_vec();
        body.resize(4000, b' ');
        let server = FakeServer::start(chunked(&body));
        let token = AuthToken::generate().unwrap();
        assert_eq!(check(&server, &token), Err(HealthError::BadResponse));
    }

    #[test]
    fn a_chunked_body_within_the_cap_is_healthy() {
        // The control for the test above: the same transfer coding, small enough.
        let server = FakeServer::start(chunked(OK_BODY));
        let token = AuthToken::generate().unwrap();
        assert_eq!(check(&server, &token), Ok(HealthReply::Healthy));
    }

    #[test]
    fn a_redirect_is_not_followed() {
        let target = FakeServer::start(response("200 OK", &[], OK_BODY));
        let location = format!("Location: http://127.0.0.1:{}/health", target.port);
        let origin = FakeServer::start(response("302 Found", &[&location], b""));
        let token = AuthToken::generate().unwrap();

        assert_eq!(check(&origin, &token), Err(HealthError::BadResponse));
        assert_eq!(origin.accepted(), 1);
        assert_eq!(target.accepted(), 0, "the redirect target was contacted");
    }

    #[test]
    fn a_server_that_never_answers_times_out() {
        let server = FakeServer::start(Reply::HoldOpen);
        let token = AuthToken::generate().unwrap();
        let client =
            HealthClient::build(&token, Duration::from_secs(2), Duration::from_millis(300))
                .unwrap();
        assert_eq!(
            run(client.check(&server.endpoint())),
            Err(HealthError::Timeout)
        );
    }

    #[test]
    fn a_closed_port_is_unreachable() {
        let port = {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.local_addr().unwrap().port()
        };
        let token = AuthToken::generate().unwrap();
        let client = HealthClient::new(&token).unwrap();
        let endpoint = LoopbackEndpoint::new(port).unwrap();
        assert_eq!(run(client.check(&endpoint)), Err(HealthError::Unreachable));
    }

    #[test]
    fn the_endpoint_is_loopback_only_and_rejects_port_zero() {
        assert_eq!(LoopbackEndpoint::new(0), None);
        let endpoint = LoopbackEndpoint::new(8080).unwrap();
        assert_eq!(endpoint.to_string(), "http://127.0.0.1:8080/health");
        let max = LoopbackEndpoint::new(u16::MAX).unwrap();
        assert_eq!(max.to_string(), "http://127.0.0.1:65535/health");
    }

    #[test]
    fn every_reply_and_error_serializes_to_fixed_secret_free_json() {
        let token = AuthToken::generate().unwrap();
        let replies = [
            (HealthReply::Starting, r#"{"status":"starting"}"#),
            (HealthReply::Healthy, r#"{"status":"healthy"}"#),
            (HealthReply::NotRunning, r#"{"status":"not_running"}"#),
            (
                HealthReply::Failed {
                    reason: FailureKind::Timeout,
                },
                r#"{"status":"failed","reason":{"kind":"timeout"}}"#,
            ),
            (
                HealthReply::Failed {
                    reason: FailureKind::ExitedBeforeReady { code: Some(3) },
                },
                r#"{"status":"failed","reason":{"kind":"exited_before_ready","code":3}}"#,
            ),
        ];
        for (reply, expected) in replies {
            assert_eq!(serde_json::to_string(&reply).unwrap(), expected);
        }
        let errors = [
            (HealthError::Unauthorized, r#"{"error":"unauthorized"}"#),
            (HealthError::Unreachable, r#"{"error":"unreachable"}"#),
            (HealthError::Timeout, r#"{"error":"timeout"}"#),
            (HealthError::BadResponse, r#"{"error":"bad_response"}"#),
            (HealthError::Internal, r#"{"error":"internal"}"#),
        ];
        for (error, expected) in errors {
            let json = serde_json::to_string(&error).unwrap();
            assert_eq!(json, expected);
            for text in [json, error.to_string(), format!("{error:?}")] {
                assert!(!text.contains(token.expose()), "token leaked into: {text}");
            }
        }
        // Unit variants only: a `String` field (a place for detail or the token) makes this larger.
        assert!(std::mem::size_of::<HealthError>() <= 1);
    }

    /// Every reply, with no wildcard match: a new `HealthReply` variant breaks compilation here.
    /// `Failed` is expanded in `contract_json` over every failure kind.
    fn every_reply_without_reason() -> Vec<HealthReply> {
        fn exhaustive(reply: HealthReply) {
            match reply {
                HealthReply::Starting
                | HealthReply::Healthy
                | HealthReply::NotRunning
                | HealthReply::Failed { .. } => {}
            }
        }
        let all = vec![
            HealthReply::Starting,
            HealthReply::Healthy,
            HealthReply::NotRunning,
        ];
        exhaustive(HealthReply::Healthy);
        all
    }

    /// Every error, with no wildcard match.
    fn every_error() -> Vec<HealthError> {
        fn exhaustive(error: HealthError) {
            match error {
                HealthError::Unauthorized
                | HealthError::Unreachable
                | HealthError::Timeout
                | HealthError::BadResponse
                | HealthError::Internal => {}
            }
        }
        let all = vec![
            HealthError::Unauthorized,
            HealthError::Unreachable,
            HealthError::Timeout,
            HealthError::BadResponse,
            HealthError::Internal,
        ];
        exhaustive(HealthError::Internal);
        all
    }

    /// The JSON the window can receive, for every variant: what `src/ipc/health-contract.json`
    /// must contain. The Vitest suite parses every entry with the Zod schema and checks that the
    /// set of names equals the schema's, so a variant added on only one side fails a test.
    fn contract_json() -> serde_json::Value {
        let mut replies: Vec<HealthReply> = every_reply_without_reason();
        for reason in crate::sidecar::supervisor::every_failure_kind() {
            replies.push(HealthReply::Failed { reason });
        }
        // The exit code is the one payload: cover absent, negative and the i32 extremes.
        for code in [None, Some(i32::MIN), Some(i32::MAX)] {
            replies.push(HealthReply::Failed {
                reason: FailureKind::ExitedBeforeReady { code },
            });
        }
        serde_json::json!({
            "replies": replies,
            "errors": every_error(),
        })
    }

    const CONTRACT_FILE: &str = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../src/ipc/health-contract.json"
    );

    /// Compares the committed fixture with what Rust serializes today. Compared as parsed JSON
    /// values, so line endings and formatting cannot matter. To regenerate after a deliberate
    /// change, run with `GRC_WRITE_HEALTH_CONTRACT=1`: it rewrites the file and still FAILS, so a
    /// run that wrote the file can never look like a pass (an unconditional write would make drift
    /// invisible, because nothing would compare).
    #[test]
    fn the_ipc_contract_fixture_matches_what_rust_serializes() {
        let expected = contract_json();
        if std::env::var_os("GRC_WRITE_HEALTH_CONTRACT").is_some() {
            let mut text = serde_json::to_string_pretty(&expected).unwrap();
            text.push('\n');
            std::fs::write(CONTRACT_FILE, text).unwrap();
            panic!("wrote {CONTRACT_FILE}; unset GRC_WRITE_HEALTH_CONTRACT and run again");
        }
        let committed: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(CONTRACT_FILE).unwrap()).unwrap();
        assert_eq!(
            committed,
            expected,
            "src/ipc/health-contract.json is out of date; regenerate it with \
             GRC_WRITE_HEALTH_CONTRACT=1 and update src/ipc/health.ts. Expected:\n{}",
            serde_json::to_string_pretty(&expected).unwrap()
        );
    }

    #[test]
    fn the_client_debug_output_shows_no_token() {
        let token = AuthToken::generate().unwrap();
        let client = HealthClient::new(&token).unwrap();
        for text in [format!("{client:?}"), format!("{client:#?}")] {
            assert!(!text.contains(token.expose()), "token leaked into: {text}");
        }
    }

    #[test]
    fn the_header_value_is_marked_sensitive() {
        let token = AuthToken::generate().unwrap();
        let client = HealthClient::new(&token).unwrap();
        assert!(client.auth.is_sensitive());
        assert!(!format!("{:?}", client.auth).contains(token.expose()));
    }
}
