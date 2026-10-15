//! Making one proxied call (ADR-020 decisions 2 and 3).
//!
//! [`call`] is a plain function: a client, an endpoint and a typed request in, a [`ProxyReply`] or a
//! [`ProxyError`] out. The `sidecar_call` command (chunk 6e) reaches it through `SidecarHost::call`.
//!
//! The path from request to reply, in order:
//!
//! 1. the typed request is checked and its body is rebuilt from the typed fields (`route.rs`);
//! 2. a body over the route's request cap is refused BEFORE any connection is opened;
//! 3. the request goes to `http://127.0.0.1:{port}{path}` with the sensitive token header and the
//!    route's own timeout; there is no redirect and no retry;
//! 4. a 401 is `unauthorized` and its body is never read;
//! 5. a status outside [`FORWARDED_STATUSES`] (including every 3xx and 5xx other than 503) is
//!    `bad_response`;
//! 6. the body is read within the route's response cap (declared length and chunked), and must be
//!    valid JSON, or the call is `too_large` or `bad_response`.
//!
//! Nothing the sidecar says is kept except the parsed JSON of an allowed reply. No error carries
//! text, and the only things logged are fixed words and the status number.

use reqwest::header::{HeaderValue, CONTENT_TYPE};

use super::error::{ProxyError, ProxyReply};
use super::route::{Method, RouteRequest, RouteSpec};
use crate::sidecar_client::{
    classify, read_capped, LoopbackEndpoint, ReadError, SidecarClient, Transport,
};

/// The statuses the sidecar's routes use (`api/errors.py` and the route code): success 200 and 202,
/// and the typed refusals 400, 404, 409, 422 and 503. Their bodies are `{"error": word, ...}` JSON
/// that the window parses with a strict schema. 401 is handled apart (it is an `unauthorized`
/// error). Anything else, including 500, is `bad_response`.
pub const FORWARDED_STATUSES: [u16; 7] = [200, 202, 400, 404, 409, 422, 503];

fn transport_error(transport: Transport) -> ProxyError {
    match transport {
        Transport::Unreachable => ProxyError::Unreachable,
        Transport::Timeout => ProxyError::Timeout,
        Transport::Other => ProxyError::BadResponse,
    }
}

/// Refuse a request body over `cap`. Separate from [`call_with_spec`] so the rule can be tested
/// with caps small enough to reach (no real argument can exceed 16 KiB).
pub(crate) fn check_request_size(length: usize, cap: usize) -> Result<(), ProxyError> {
    if length > cap {
        Err(ProxyError::TooLarge)
    } else {
        Ok(())
    }
}

/// Send one typed request to the sidecar and return its answer or a fixed-word error.
pub async fn call(
    client: &SidecarClient,
    endpoint: &LoopbackEndpoint,
    request: &RouteRequest,
) -> Result<ProxyReply, ProxyError> {
    let prepared = request.prepare()?;
    let result = call_with_spec(
        client,
        endpoint,
        prepared.route.spec(),
        &prepared.path,
        prepared.body.as_deref(),
    )
    .await;
    // Fixed words and one integer: the route name, the status or the error word. Never a body.
    match &result {
        Ok(reply) => log::debug!(
            "sidecar call {:?} answered {}",
            prepared.route,
            reply.status
        ),
        Err(error) => log::debug!("sidecar call {:?} failed: {error:?}", prepared.route),
    }
    result
}

/// The same as [`call`] for an already prepared path and body and an explicit spec. `pub(crate)` so
/// tests can use a spec with a small cap or timeout; the app only reaches it through [`call`].
pub(crate) async fn call_with_spec(
    client: &SidecarClient,
    endpoint: &LoopbackEndpoint,
    spec: &RouteSpec,
    path: &str,
    body: Option<&[u8]>,
) -> Result<ProxyReply, ProxyError> {
    if let Some(bytes) = body {
        // Before the request is built, let alone sent: an oversize body opens no connection.
        check_request_size(bytes.len(), spec.request_cap)?;
    }
    let method = match spec.method {
        Method::Get => reqwest::Method::GET,
        Method::Post => reqwest::Method::POST,
    };
    let mut builder = client.request(method, endpoint.url(path), spec.timeout);
    if let Some(bytes) = body {
        builder = builder
            .header(CONTENT_TYPE, HeaderValue::from_static("application/json"))
            .body(bytes.to_vec());
    }
    let mut response = builder
        .send()
        .await
        .map_err(|error| transport_error(classify(&error)))?;

    let status = response.status().as_u16();
    if status == 401 {
        // The body is never read, and the request is never repeated.
        return Err(ProxyError::Unauthorized);
    }
    if !FORWARDED_STATUSES.contains(&status) {
        return Err(ProxyError::BadResponse);
    }
    let bytes = read_capped(&mut response, spec.response_cap)
        .await
        .map_err(|error| match error {
            ReadError::TooLarge => ProxyError::TooLarge,
            ReadError::Transport(transport) => transport_error(transport),
        })?;
    let body = serde_json::from_slice(&bytes).map_err(|_| ProxyError::BadResponse)?;
    Ok(ProxyReply { status, body })
}

#[cfg(test)]
mod tests {
    use std::net::TcpListener;
    use std::time::Duration;

    use super::*;
    use crate::proxy::error::every_error;
    use crate::proxy::route::{PathTemplate, Route, MAX_REQUEST_BYTES};
    use crate::proxy::test_support::{chunked, response, FakeServer, Reply};
    use crate::token::AuthToken;

    const ID: &str = "0123456789abcdef";
    const CANARY: &str = "CANARY-7f3a91-do-not-leak";

    fn run<F: std::future::Future>(future: F) -> F::Output {
        tauri::async_runtime::block_on(future)
    }

    fn request(json: &str) -> RouteRequest {
        RouteRequest::from_json(json).unwrap()
    }

    fn readiness() -> RouteRequest {
        request(r#"{"route":"readiness"}"#)
    }

    fn do_call(
        server: &FakeServer,
        token: &AuthToken,
        request: &RouteRequest,
    ) -> Result<ProxyReply, ProxyError> {
        let client = SidecarClient::new(token).unwrap();
        run(call(&client, &server.endpoint(), request))
    }

    fn json_ok(body: &str) -> Reply {
        response(
            "200 OK",
            &["Content-Type: application/json"],
            body.as_bytes(),
        )
    }

    /// A JSON object padded with trailing spaces to `total` bytes: valid JSON, so only its size is
    /// wrong when it is over a cap.
    fn padded(total: usize) -> Vec<u8> {
        let mut body = br#"{"state":"ready"}"#.to_vec();
        body.resize(total, b' ');
        body
    }

    /// The request head's header names, lowercased.
    fn header_names(request: &str) -> Vec<String> {
        request
            .split("\r\n\r\n")
            .next()
            .unwrap_or("")
            .lines()
            .skip(1)
            .filter_map(|line| line.split(':').next())
            .map(str::to_ascii_lowercase)
            .collect()
    }

    #[test]
    fn url_builder_takes_only_a_port_and_a_route_id() {
        let token = AuthToken::generate().unwrap();
        let server = FakeServer::start(json_ok(r#"{"job_id":"x"}"#));
        let reply = do_call(
            &server,
            &token,
            &request(&format!(
                r#"{{"route":"vault_index_status","job_id":"{ID}"}}"#
            )),
        )
        .unwrap();
        assert_eq!(reply.status, 200);

        let requests = server.requests();
        assert_eq!(requests.len(), 1);
        let head = requests[0].to_ascii_lowercase();
        assert!(
            head.starts_with(&format!("get /vault/index/{ID} http/1.1\r\n")),
            "{head}"
        );
        assert!(
            head.contains(&format!("host: 127.0.0.1:{}\r\n", server.port)),
            "{head}"
        );
        assert!(
            head.contains(&format!("x-grc-auth: {}\r\n", token.expose())),
            "{head}"
        );
        // Nothing the window could have chosen: no cookie, no authorization, no custom header.
        let allowed = ["host", "x-grc-auth", "accept", "content-length"];
        for name in header_names(&requests[0]) {
            assert!(
                allowed.contains(&name.as_str()),
                "unexpected header {name}: {head}"
            );
        }
    }

    #[test]
    fn a_post_carries_the_rebuilt_body_and_a_json_content_type() {
        let token = AuthToken::generate().unwrap();
        let server = FakeServer::start(json_ok(r#"{"rows":[]}"#));
        do_call(
            &server,
            &token,
            &request(r#"{ "route" : "draft_rows", "file" : "d.xlsx" }"#),
        )
        .unwrap();

        let requests = server.requests();
        let head = requests[0].to_ascii_lowercase();
        assert!(
            head.starts_with("post /questionnaire/draft http/1.1\r\n"),
            "{head}"
        );
        assert!(
            head.contains("content-type: application/json\r\n"),
            "{head}"
        );
        assert!(
            requests[0].ends_with(r#"{"file":"d.xlsx"}"#),
            "{}",
            requests[0]
        );
    }

    #[test]
    fn a_bad_request_opens_no_connection() {
        let token = AuthToken::generate().unwrap();
        let server = FakeServer::start(json_ok("{}"));
        // Built in Rust, past `from_json`: `prepare` still refuses an empty file name.
        let empty = RouteRequest::DraftRows(crate::proxy::route::FileArgs {
            file: String::new(),
        });
        assert_eq!(
            do_call(&server, &token, &empty),
            Err(ProxyError::BadRequest)
        );
        assert_eq!(server.accepted(), 0);
        assert!(server.requests().is_empty());
    }

    #[test]
    fn the_request_cap_check_refuses_a_length_over_the_cap() {
        assert_eq!(
            check_request_size(MAX_REQUEST_BYTES, MAX_REQUEST_BYTES),
            Ok(())
        );
        assert_eq!(
            check_request_size(MAX_REQUEST_BYTES + 1, MAX_REQUEST_BYTES),
            Err(ProxyError::TooLarge)
        );
    }

    /// A spec like the real inspect route, with a cap small enough to exceed (no real argument can
    /// pass 16 KiB, so the seam is the only way to reach the end-to-end path).
    fn small_cap_spec(request_cap: usize, response_cap: usize) -> RouteSpec {
        RouteSpec {
            route: Route::QuestionnaireInspect,
            method: Method::Post,
            path: PathTemplate::Fixed("/questionnaire/inspect"),
            request_cap,
            response_cap,
            timeout: Duration::from_secs(5),
        }
    }

    #[test]
    fn request_over_cap_is_refused_before_any_connection_is_opened() {
        let token = AuthToken::generate().unwrap();
        let server = FakeServer::start(json_ok("{}"));
        let client = SidecarClient::new(&token).unwrap();
        let spec = small_cap_spec(16, 1024);

        let over = run(call_with_spec(
            &client,
            &server.endpoint(),
            &spec,
            "/questionnaire/inspect",
            Some(&[b'x'; 17]),
        ));
        assert_eq!(over, Err(ProxyError::TooLarge));
        assert_eq!(server.accepted(), 0, "a connection was opened");
        assert!(server.requests().is_empty());

        // The control: a body exactly at the cap is sent.
        let at_cap = run(call_with_spec(
            &client,
            &server.endpoint(),
            &spec,
            "/questionnaire/inspect",
            Some(&[b'x'; 16]),
        ));
        assert_eq!(at_cap.map(|reply| reply.status), Ok(200));
        assert_eq!(server.accepted(), 1);
    }

    #[test]
    fn response_over_cap_is_refused_declared_and_chunked() {
        let token = AuthToken::generate().unwrap();
        let cap = 16 * 1024;

        let declared = FakeServer::start(json_ok(&String::from_utf8(padded(cap + 1)).unwrap()));
        assert_eq!(
            do_call(&declared, &token, &readiness()),
            Err(ProxyError::TooLarge)
        );

        // No Content-Length: only the read loop can stop it. Without the cap this would parse.
        let streamed = FakeServer::start(chunked(&padded(cap + 1)));
        assert_eq!(
            do_call(&streamed, &token, &readiness()),
            Err(ProxyError::TooLarge)
        );
    }

    #[test]
    fn a_declared_length_over_the_cap_is_refused_before_any_body_arrives() {
        // The head promises 20 KiB and the connection closes with no body. If the declared length
        // were not checked up front, the read would fail as a broken body (`bad_response`) instead.
        let token = AuthToken::generate().unwrap();
        let head = b"HTTP/1.1 200 OK\r\nContent-Length: 20480\r\n\r\n".to_vec();
        let server = FakeServer::start(Reply::Bytes(head));
        assert_eq!(
            do_call(&server, &token, &readiness()),
            Err(ProxyError::TooLarge)
        );
    }

    #[test]
    fn a_response_within_the_cap_passes_declared_and_chunked() {
        let token = AuthToken::generate().unwrap();
        let cap = 16 * 1024;
        let declared = FakeServer::start(json_ok(&String::from_utf8(padded(cap)).unwrap()));
        let reply = do_call(&declared, &token, &readiness()).unwrap();
        assert_eq!(reply.body, serde_json::json!({"state": "ready"}));

        let streamed = FakeServer::start(chunked(&padded(cap)));
        assert_eq!(
            do_call(&streamed, &token, &readiness()).map(|r| r.status),
            Ok(200)
        );
    }

    #[test]
    fn draft_rows_may_be_up_to_four_mib_and_no_more() {
        let token = AuthToken::generate().unwrap();
        let four_mib = 4 * 1024 * 1024;
        let draft = request(r#"{"route":"draft_rows","file":"d.xlsx"}"#);

        let fits = FakeServer::start(chunked(&padded(four_mib)));
        assert_eq!(do_call(&fits, &token, &draft).map(|r| r.status), Ok(200));
        let over = FakeServer::start(chunked(&padded(four_mib + 1)));
        assert_eq!(do_call(&over, &token, &draft), Err(ProxyError::TooLarge));
    }

    #[test]
    fn every_forwarded_status_passes_with_its_json_body() {
        let token = AuthToken::generate().unwrap();
        for status in FORWARDED_STATUSES {
            let server = FakeServer::start(response(
                &format!("{status} Whatever"),
                &[],
                br#"{"error":"run_in_progress"}"#,
            ));
            let reply = do_call(&server, &token, &readiness()).unwrap();
            assert_eq!(reply.status, status);
            assert_eq!(reply.body, serde_json::json!({"error": "run_in_progress"}));
        }
    }

    #[test]
    fn any_status_outside_the_allowed_set_including_500_is_bad_response() {
        let token = AuthToken::generate().unwrap();
        for status in [
            "100 Continue",
            "201 Created",
            "204 No Content",
            "301 Moved Permanently",
            "302 Found",
            "403 Forbidden",
            "405 Method Not Allowed",
            "500 Internal Server Error",
            "502 Bad Gateway",
        ] {
            let server = FakeServer::start(response(status, &[], br#"{"ok":true}"#));
            assert_eq!(
                do_call(&server, &token, &readiness()),
                Err(ProxyError::BadResponse),
                "{status}"
            );
        }
    }

    #[test]
    fn a_redirect_is_not_followed() {
        let token = AuthToken::generate().unwrap();
        let target = FakeServer::start(json_ok("{}"));
        let location = format!("Location: http://127.0.0.1:{}/readiness", target.port);
        let origin = FakeServer::start(response("302 Found", &[&location], b""));
        assert_eq!(
            do_call(&origin, &token, &readiness()),
            Err(ProxyError::BadResponse)
        );
        assert_eq!(origin.accepted(), 1);
        assert_eq!(target.accepted(), 0, "the redirect target was contacted");
    }

    #[test]
    fn a_401_is_unauthorized_never_retried_and_its_body_is_not_read() {
        let token = AuthToken::generate().unwrap();
        // Over every cap: it would be `too_large` if the body were read.
        let huge = vec![b'x'; 5 * 1024 * 1024];
        let server = FakeServer::start(response("401 Unauthorized", &[], &huge));
        assert_eq!(
            do_call(&server, &token, &readiness()),
            Err(ProxyError::Unauthorized)
        );
        assert_eq!(server.accepted(), 1, "a 401 was retried");
    }

    #[test]
    fn a_body_that_is_not_json_is_bad_response() {
        let token = AuthToken::generate().unwrap();
        for body in [&b"not json"[..], b"", b"{\"a\":1}{\"b\":2}", b"{\"a\":"] {
            let server = FakeServer::start(response("200 OK", &[], body));
            assert_eq!(
                do_call(&server, &token, &readiness()),
                Err(ProxyError::BadResponse),
                "{}",
                String::from_utf8_lossy(body)
            );
        }
    }

    #[test]
    fn a_server_that_never_answers_times_out_and_a_closed_port_is_unreachable() {
        let token = AuthToken::generate().unwrap();
        let client = SidecarClient::new(&token).unwrap();

        let server = FakeServer::start(Reply::HoldOpen);
        let mut spec = small_cap_spec(1024, 1024);
        spec.timeout = Duration::from_millis(300);
        let slow = run(call_with_spec(
            &client,
            &server.endpoint(),
            &spec,
            "/x",
            None,
        ));
        assert_eq!(slow, Err(ProxyError::Timeout));

        let port = {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.local_addr().unwrap().port()
        };
        let endpoint = LoopbackEndpoint::new(port).unwrap();
        assert_eq!(
            run(call(&client, &endpoint, &readiness())),
            Err(ProxyError::Unreachable)
        );
    }

    #[test]
    fn sidecar_text_and_the_token_never_reach_proxy_error() {
        let token = AuthToken::generate().unwrap();
        // Every way a reply can fail, each with the canary in the status line, a header and the body.
        let bodies: Vec<(&str, Reply)> = vec![
            (
                "500",
                response(
                    &format!("500 {CANARY}"),
                    &[&format!("X-Canary: {CANARY}")],
                    format!(r#"{{"error":"{CANARY}"}}"#).as_bytes(),
                ),
            ),
            (
                "401",
                response(&format!("401 {CANARY}"), &[], CANARY.as_bytes()),
            ),
            ("not json", response("200 OK", &[], CANARY.as_bytes())),
            (
                "too large",
                json_ok(&format!(r#"{{"x":"{CANARY}{}"}}"#, " ".repeat(20 * 1024))),
            ),
        ];
        for (label, reply) in bodies {
            let server = FakeServer::start(reply);
            let error = do_call(&server, &token, &readiness()).unwrap_err();
            let json = serde_json::to_string(&error).unwrap();
            for text in [json, error.to_string(), format!("{error:?}")] {
                assert!(!text.contains(CANARY), "{label}: canary in {text}");
                assert!(!text.contains(token.expose()), "{label}: token in {text}");
            }
        }
        // The same for the errors that need no server.
        for error in every_error() {
            for text in [
                serde_json::to_string(&error).unwrap(),
                error.to_string(),
                format!("{error:?}"),
            ] {
                assert!(!text.contains(token.expose()), "token in {text}");
            }
        }
        // Unit variants only: a `String` field (a place for detail or the token) makes this larger.
        assert!(std::mem::size_of::<ProxyError>() <= 1);
    }

    #[test]
    fn every_error_serializes_to_one_fixed_word() {
        let expected = [
            (ProxyError::NotReady, r#"{"error":"not_ready"}"#),
            (ProxyError::Unauthorized, r#"{"error":"unauthorized"}"#),
            (ProxyError::Unreachable, r#"{"error":"unreachable"}"#),
            (ProxyError::Timeout, r#"{"error":"timeout"}"#),
            (ProxyError::TooLarge, r#"{"error":"too_large"}"#),
            (ProxyError::BadRequest, r#"{"error":"bad_request"}"#),
            (ProxyError::BadResponse, r#"{"error":"bad_response"}"#),
            (ProxyError::Internal, r#"{"error":"internal"}"#),
        ];
        assert_eq!(expected.len(), every_error().len());
        for (error, json) in expected {
            assert_eq!(serde_json::to_string(&error).unwrap(), json);
        }
    }

    /// What the window can receive, for every variant: what `tests/fixtures/proxy-contract.json`
    /// must contain. Chunk 6f's Vitest suite will parse every entry with a Zod schema and check the
    /// set of names equals the schema's, so a variant added on only one side fails a test.
    fn contract_json() -> serde_json::Value {
        serde_json::json!({
            "errors": every_error(),
            "forwarded_statuses": FORWARDED_STATUSES,
            "route_names": [
                "readiness",
                "questionnaire_inspect",
                "questionnaire_start",
                "questionnaire_status",
                "vault_index_start",
                "vault_index_status",
                "draft_rows",
            ],
        })
    }

    const CONTRACT_FILE: &str = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/proxy-contract.json"
    );

    /// Compares the committed fixture with what Rust serializes today, as parsed JSON values. To
    /// regenerate after a deliberate change, run with `GRC_WRITE_PROXY_CONTRACT=1`: it rewrites the
    /// file and still FAILS, so a run that wrote the file can never look like a pass.
    #[test]
    fn the_ipc_contract_fixture_matches_what_rust_serializes() {
        let expected = contract_json();
        if std::env::var_os("GRC_WRITE_PROXY_CONTRACT").is_some() {
            let mut text = serde_json::to_string_pretty(&expected).unwrap();
            text.push('\n');
            std::fs::write(CONTRACT_FILE, text).unwrap();
            panic!("wrote {CONTRACT_FILE}; unset GRC_WRITE_PROXY_CONTRACT and run again");
        }
        let committed: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(CONTRACT_FILE).unwrap()).unwrap();
        assert_eq!(
            committed,
            expected,
            "tests/fixtures/proxy-contract.json is out of date; regenerate it with \
             GRC_WRITE_PROXY_CONTRACT=1. Expected:\n{}",
            serde_json::to_string_pretty(&expected).unwrap()
        );
    }

    #[test]
    fn route_names_in_the_fixture_are_the_names_the_request_parser_accepts() {
        // The names above are written by hand; this ties them to the real `route` tag values and
        // to `Route::ALL`.
        let names = contract_json()["route_names"].as_array().unwrap().clone();
        assert_eq!(names.len(), Route::ALL.len());
        for name in names {
            let name = name.as_str().unwrap();
            let json = match name {
                "questionnaire_status" => format!(r#"{{"route":"{name}","run_id":"{ID}"}}"#),
                "vault_index_status" => format!(r#"{{"route":"{name}","job_id":"{ID}"}}"#),
                "questionnaire_inspect" | "draft_rows" => {
                    format!(r#"{{"route":"{name}","file":"f.xlsx"}}"#)
                }
                "questionnaire_start" => format!(
                    r#"{{"route":"{name}","file":"f.xlsx","mapping":{{"sheet":"S","header_row":1,"question_col":1}}}}"#
                ),
                _ => format!(r#"{{"route":"{name}"}}"#),
            };
            let parsed = RouteRequest::from_json(&json).unwrap_or_else(|_| panic!("{json}"));
            assert!(Route::ALL.contains(&parsed.route()), "{name}");
        }
    }
}
