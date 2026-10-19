//! `SidecarHost::call` against the fake sidecar (chunk 6d). The fake only announces a port; a
//! separate loopback server answers on it. The call must be `not_ready` with no network call while
//! the sidecar is starting or stopped, and must reach exactly the announced port once it is ready.

mod common;
mod proxy_support;

use std::ffi::OsString;
use std::fs;

use common::{Reaper, Scratch, TestResult, SETTLE_SLACK, TEST_HANDSHAKE};
use desktop_lib::folders::UserFolders;
use desktop_lib::host::{SidecarDirs, SidecarHost};
use desktop_lib::proxy::{ProxyError, ProxyReply, RouteRequest};
use proxy_support::{json_response, Server};

fn start_with(
    scratch: &Scratch,
    args: Vec<OsString>,
) -> Result<SidecarHost, Box<dyn std::error::Error>> {
    start_with_folders(scratch, args, &UserFolders::default())
}

fn start_with_folders(
    scratch: &Scratch,
    args: Vec<OsString>,
    folders: &UserFolders<'_>,
) -> Result<SidecarHost, Box<dyn std::error::Error>> {
    let index_dir = scratch.file("index");
    let dirs = SidecarDirs {
        log_dir: scratch.path(),
        index_dir: &index_dir,
        folders: *folders,
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

#[test]
fn a_bad_file_name_is_bad_request_and_no_connection_is_opened() -> TestResult {
    let scratch = Scratch::new("host_proxy_file_names")?;
    let questionnaires = scratch.file("questionnaires");
    let exports = scratch.file("exports");
    fs::create_dir(&questionnaires)?;
    fs::create_dir(&exports)?;
    fs::write(questionnaires.join("q.xlsx"), b"synthetic")?;
    fs::write(exports.join("draft.xlsx"), b"synthetic")?;
    // A real file outside both folders, which a `..` name would reach if it were let through.
    fs::write(scratch.file("outside.xlsx"), b"synthetic")?;

    let server = Server::start(json_response("200 OK", r#"{"ok":true}"#))?;
    let folders = UserFolders {
        questionnaire: Some(&questionnaires),
        export: Some(&exports),
        ..UserFolders::default()
    };
    let host = start_with_folders(
        &scratch,
        vec![
            OsString::from("ready-on"),
            OsString::from(server.port.to_string()),
        ],
        &folders,
    )?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());
    host.supervisor()
        .wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    let mapping = r#""mapping":{"sheet":"S","header_row":1,"question_col":1}"#;
    let bad_names = [
        r"..\\outside.xlsx",
        "../outside.xlsx",
        r"sub\\..\\..\\outside.xlsx",
        "C:\\\\Windows\\\\win.ini",
        r"\\\\server\\share\\q.xlsx",
        "NUL.xlsx",
        "q.xlsx.",
        "q.xlsx:stream",
        "missing.xlsx",
        "q.xlsx\\u0000",
    ];
    for name in bad_names {
        for json in [
            format!(r#"{{"route":"questionnaire_inspect","file":"{name}"}}"#),
            format!(r#"{{"route":"questionnaire_start","file":"{name}",{mapping}}}"#),
            format!(r#"{{"route":"draft_rows","file":"{name}"}}"#),
        ] {
            assert_eq!(call(&host, &json), Err(ProxyError::BadRequest), "{json}");
        }
    }
    // The right file name in the WRONG folder is a missing file there: a draft is looked up in the
    // export folder, a workbook in the questionnaire folder.
    assert_eq!(
        call(&host, r#"{"route":"draft_rows","file":"q.xlsx"}"#),
        Err(ProxyError::BadRequest)
    );
    assert_eq!(
        call(
            &host,
            r#"{"route":"questionnaire_inspect","file":"draft.xlsx"}"#
        ),
        Err(ProxyError::BadRequest)
    );
    assert_eq!(
        server.accepted(),
        0,
        "a refused name must open no connection"
    );

    // The same routes with the right names go through to the announced port.
    let inspect = call(
        &host,
        r#"{"route":"questionnaire_inspect","file":"q.xlsx"}"#,
    )?;
    assert_eq!(inspect.status, 200);
    let draft = call(&host, r#"{"route":"draft_rows","file":"draft.xlsx"}"#)?;
    assert_eq!(draft.status, 200);
    assert_eq!(server.accepted(), 2);

    host.supervisor().shutdown()?;
    Ok(())
}

fn reality_check_json(file: &str) -> String {
    format!(
        r#"{{"route":"reality_check","file":"{file}","claim":{{"path":"p.md","start_line":1,"end_line":1,"quote":"q"}},"mfa_types":["A"],"phone_types":[],"active_statuses":["Active"],"exported_at":"2026-10-01"}}"#
    )
}

fn ledger_import_json(file: &str) -> String {
    format!(
        r#"{{"route":"ledger_import","run_id":"0123456789abcdef","file":"{file}","confirm_new":false,"sent_on":"2026-10-01","preview":true}}"#
    )
}

/// Chunk 65d, scope item 5 (the same rule as F35-3 for the other folders): a file name for the
/// Imports folder (the two reality routes with a file) and for the Exports folder (`ledger_import`)
/// is one plain name of a regular file in THAT folder, or `bad_request` with no connection.
#[test]
fn a_bad_file_name_for_the_imports_folder_is_bad_request_and_no_connection_is_opened() -> TestResult
{
    let scratch = Scratch::new("host_proxy_import_names")?;
    let imports = scratch.file("imports");
    let exports = scratch.file("exports");
    fs::create_dir(&imports)?;
    fs::create_dir(&exports)?;
    fs::write(imports.join("users.csv"), b"synthetic")?;
    fs::write(exports.join("draft.xlsx"), b"synthetic")?;
    // A real file outside every folder, which a `..` name would reach if it were let through.
    fs::write(scratch.file("outside.csv"), b"synthetic")?;

    let server = Server::start(json_response("200 OK", r#"{"ok":true}"#))?;
    let folders = UserFolders {
        export: Some(&exports),
        import: Some(&imports),
        ..UserFolders::default()
    };
    let host = start_with_folders(
        &scratch,
        vec![
            OsString::from("ready-on"),
            OsString::from(server.port.to_string()),
        ],
        &folders,
    )?;
    let mut reaper = Reaper::new();
    reaper.add(host.supervisor().child_pid());
    host.supervisor()
        .wait_until_settled(TEST_HANDSHAKE + SETTLE_SLACK)?;

    let bad_names = [
        r"..\\outside.csv",
        "../outside.csv",
        r"sub\\..\\..\\outside.csv",
        "C:\\\\Windows\\\\win.ini",
        r"\\\\server\\share\\users.csv",
        "NUL.csv",
        "users.csv.",
        "users.csv:stream",
        "missing.csv",
        "users.csv\\u0000",
    ];
    for name in bad_names {
        for json in [
            format!(r#"{{"route":"reality_inspect","file":"{name}"}}"#),
            reality_check_json(name),
            ledger_import_json(name),
        ] {
            assert_eq!(call(&host, &json), Err(ProxyError::BadRequest), "{json}");
        }
    }
    // The right name in the WRONG folder is a missing file there: an IT export is looked up in the
    // import folder, a reviewed draft in the export folder.
    for json in [
        r#"{"route":"reality_inspect","file":"draft.xlsx"}"#.to_owned(),
        reality_check_json("draft.xlsx"),
        ledger_import_json("users.csv"),
    ] {
        assert_eq!(call(&host, &json), Err(ProxyError::BadRequest), "{json}");
    }
    assert_eq!(
        server.accepted(),
        0,
        "a refused name must open no connection"
    );

    // The same routes with the right names go through to the announced port.
    for json in [
        r#"{"route":"reality_inspect","file":"users.csv"}"#.to_owned(),
        reality_check_json("users.csv"),
        ledger_import_json("draft.xlsx"),
    ] {
        assert_eq!(call(&host, &json)?.status, 200, "{json}");
    }
    assert_eq!(server.accepted(), 3);

    host.supervisor().shutdown()?;
    Ok(())
}
