//! The closed route table and the typed requests (ADR-020 decisions 1 and 2).
//!
//! The window never supplies a URL, header, method or port. It names one of seven [`Route`]s with
//! that route's typed arguments ([`RouteRequest`]). For each route a static [`RouteSpec`] fixes the
//! HTTP method, the path template, the request cap, the response cap and the timeout. The only
//! caller-chosen part of a path is a [`HexId`], which matches `^[0-9a-f]{8,64}$` (the sidecar's own
//! pattern), so no `/`, `.` or `?` can reach the URL. The request body is serialized again from the
//! typed struct; the window's own text is never forwarded.
//!
//! Starting values for caps and timeouts are unmeasured (ADR-020 decision 2).

use std::time::Duration;

use serde::{Deserialize, Serialize};

use super::error::ProxyError;

const KIB: usize = 1024;
const MIB: usize = 1024 * KIB;

/// Request bodies are limited to 16 KiB and checked before sending (G34, proxied path only).
pub const MAX_REQUEST_BYTES: usize = 16 * KIB;
/// The default response cap.
pub const MAX_RESPONSE_BYTES: usize = MIB;
/// `readiness` is a few model names and three words; 16 KiB is far more than it needs (owner
/// amendment to the plan's B.4, which said 1 KiB, and to ADR-020's blanket 1 MiB).
pub const MAX_READINESS_RESPONSE_BYTES: usize = 16 * KIB;
/// `draft_rows`: up to 500 questions with their answers (the sidecar's cap).
pub const MAX_DRAFT_RESPONSE_BYTES: usize = 4 * MIB;

/// The sidecar's own limits, mirrored so a request that it would refuse is `bad_request` here.
const MAX_FILE_NAME_CHARS: usize = 260;
const MAX_SHEET_NAME_CHARS: usize = 64;
const MAX_HEADER_ROW: u32 = 1_048_576;
const MAX_COLUMN_INDEX: u32 = 16_384;

/// The seven M6 core routes. Closed: adding a variant breaks the exhaustive match in
/// [`Route::spec`], then the table test.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route {
    Readiness,
    QuestionnaireInspect,
    QuestionnaireStart,
    QuestionnaireStatus,
    VaultIndexStart,
    VaultIndexStatus,
    DraftRows,
}

impl Route {
    pub const ALL: [Route; 7] = [
        Route::Readiness,
        Route::QuestionnaireInspect,
        Route::QuestionnaireStart,
        Route::QuestionnaireStatus,
        Route::VaultIndexStart,
        Route::VaultIndexStatus,
        Route::DraftRows,
    ];

    pub fn spec(self) -> &'static RouteSpec {
        match self {
            Route::Readiness => &READINESS,
            Route::QuestionnaireInspect => &QUESTIONNAIRE_INSPECT,
            Route::QuestionnaireStart => &QUESTIONNAIRE_START,
            Route::QuestionnaireStatus => &QUESTIONNAIRE_STATUS,
            Route::VaultIndexStart => &VAULT_INDEX_START,
            Route::VaultIndexStatus => &VAULT_INDEX_STATUS,
            Route::DraftRows => &DRAFT_ROWS,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    Get,
    Post,
}

/// Where the route lives on the sidecar. Only a [`HexId`] can ever be added to a path.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PathTemplate {
    /// The whole path, for example `/readiness`.
    Fixed(&'static str),
    /// A prefix ending in `/`, followed by one id.
    WithId(&'static str),
}

/// One row of the table.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RouteSpec {
    pub route: Route,
    pub method: Method,
    pub path: PathTemplate,
    pub request_cap: usize,
    pub response_cap: usize,
    pub timeout: Duration,
}

static READINESS: RouteSpec = RouteSpec {
    route: Route::Readiness,
    method: Method::Get,
    path: PathTemplate::Fixed("/readiness"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_READINESS_RESPONSE_BYTES,
    timeout: Duration::from_secs(5),
};

static QUESTIONNAIRE_INSPECT: RouteSpec = RouteSpec {
    route: Route::QuestionnaireInspect,
    method: Method::Post,
    path: PathTemplate::Fixed("/questionnaire/inspect"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

static QUESTIONNAIRE_START: RouteSpec = RouteSpec {
    route: Route::QuestionnaireStart,
    method: Method::Post,
    path: PathTemplate::Fixed("/questionnaire/runs"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

static QUESTIONNAIRE_STATUS: RouteSpec = RouteSpec {
    route: Route::QuestionnaireStatus,
    method: Method::Get,
    path: PathTemplate::WithId("/questionnaire/runs/"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(5),
};

static VAULT_INDEX_START: RouteSpec = RouteSpec {
    route: Route::VaultIndexStart,
    method: Method::Post,
    path: PathTemplate::Fixed("/vault/index"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(30),
};

static VAULT_INDEX_STATUS: RouteSpec = RouteSpec {
    route: Route::VaultIndexStatus,
    method: Method::Get,
    path: PathTemplate::WithId("/vault/index/"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(5),
};

static DRAFT_ROWS: RouteSpec = RouteSpec {
    route: Route::DraftRows,
    method: Method::Post,
    path: PathTemplate::Fixed("/questionnaire/draft"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_DRAFT_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

/// A run or job id: 8 to 64 characters, each `0-9` or `a-f` (the sidecar's `^[0-9a-f]{8,64}$`).
/// Written by hand so no regex crate is needed. The only text a caller can put in a URL path.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(try_from = "String")]
pub struct HexId(String);

impl HexId {
    pub fn parse(text: &str) -> Option<Self> {
        let valid = (8..=64).contains(&text.len())
            && text
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte));
        valid.then(|| Self(text.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl TryFrom<String> for HexId {
    type Error = &'static str;

    fn try_from(text: String) -> Result<Self, Self::Error> {
        Self::parse(&text).ok_or("not a run or job id")
    }
}

/// No arguments. A braced empty struct, so `deny_unknown_fields` refuses any extra key.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NoArgs {}

/// A file name in the questionnaire or export folder (`questionnaire_inspect`, `draft_rows`). This
/// checks only the sidecar's own length rule. The path-component and containment checks are the
/// host's (`host::check_request_file`, `folders::files`), run before the request is forwarded.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct FileArgs {
    pub file: String,
}

/// Where the questions are, as the sidecar's `QuestionMapping`. Rows and columns count from 1.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MappingArgs {
    pub sheet: String,
    pub header_row: u32,
    pub question_col: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id_col: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StartArgs {
    pub file: String,
    pub mapping: MappingArgs,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunIdArgs {
    pub run_id: HexId,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobIdArgs {
    pub job_id: HexId,
}

/// Which of the user's folders a file name refers to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileFolder {
    Questionnaires,
    Exports,
}

/// What the window sends: a route name in `route` plus that route's own fields, nothing else.
/// `{"route":"draft_rows","file":"x.xlsx"}`. An unknown route, a missing or extra field, or an id
/// that is not hex fails to parse ([`RouteRequest::from_json`] reports `bad_request`).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(tag = "route", rename_all = "snake_case")]
pub enum RouteRequest {
    Readiness(NoArgs),
    QuestionnaireInspect(FileArgs),
    QuestionnaireStart(StartArgs),
    QuestionnaireStatus(RunIdArgs),
    VaultIndexStart(NoArgs),
    VaultIndexStatus(JobIdArgs),
    DraftRows(FileArgs),
}

/// A request ready to send: which route, the exact path, and the body the host built.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Prepared {
    pub route: Route,
    pub path: String,
    pub body: Option<Vec<u8>>,
}

fn file_is_valid(file: &str) -> bool {
    let chars = file.chars().count();
    (1..=MAX_FILE_NAME_CHARS).contains(&chars)
}

fn mapping_is_valid(mapping: &MappingArgs) -> bool {
    (1..=MAX_SHEET_NAME_CHARS).contains(&mapping.sheet.chars().count())
        && (1..=MAX_HEADER_ROW).contains(&mapping.header_row)
        && (1..=MAX_COLUMN_INDEX).contains(&mapping.question_col)
        && mapping
            .id_col
            .is_none_or(|column| (1..=MAX_COLUMN_INDEX).contains(&column))
}

fn json_body<T: Serialize>(args: &T) -> Result<Vec<u8>, ProxyError> {
    serde_json::to_vec(args).map_err(|_| ProxyError::Internal)
}

impl RouteRequest {
    /// Parse the window's JSON. Every failure is `bad_request`; the serde message (which can quote
    /// the input) is dropped.
    pub fn from_json(text: &str) -> Result<Self, ProxyError> {
        serde_json::from_str(text).map_err(|_| ProxyError::BadRequest)
    }

    /// The same for a value the Tauri command already holds (the `sidecar_call` argument). Taking
    /// a JSON value instead of this type keeps a malformed argument out of Tauri's own error text,
    /// which can quote the input: every failure is the fixed word `bad_request`.
    pub fn from_value(value: serde_json::Value) -> Result<Self, ProxyError> {
        serde_json::from_value(value).map_err(|_| ProxyError::BadRequest)
    }

    /// The file name this request carries and the folder it is a name in: the questionnaire folder
    /// for `questionnaire_inspect` and `questionnaire_start`, the export folder for `draft_rows`
    /// (the sidecar resolves them the same way). `None` for a route with no file name.
    pub fn file(&self) -> Option<(FileFolder, &str)> {
        match self {
            RouteRequest::QuestionnaireInspect(args) => {
                Some((FileFolder::Questionnaires, args.file.as_str()))
            }
            RouteRequest::QuestionnaireStart(args) => {
                Some((FileFolder::Questionnaires, args.file.as_str()))
            }
            RouteRequest::DraftRows(args) => Some((FileFolder::Exports, args.file.as_str())),
            RouteRequest::Readiness(_)
            | RouteRequest::QuestionnaireStatus(_)
            | RouteRequest::VaultIndexStart(_)
            | RouteRequest::VaultIndexStatus(_) => None,
        }
    }

    pub fn route(&self) -> Route {
        match self {
            RouteRequest::Readiness(_) => Route::Readiness,
            RouteRequest::QuestionnaireInspect(_) => Route::QuestionnaireInspect,
            RouteRequest::QuestionnaireStart(_) => Route::QuestionnaireStart,
            RouteRequest::QuestionnaireStatus(_) => Route::QuestionnaireStatus,
            RouteRequest::VaultIndexStart(_) => Route::VaultIndexStart,
            RouteRequest::VaultIndexStatus(_) => Route::VaultIndexStatus,
            RouteRequest::DraftRows(_) => Route::DraftRows,
        }
    }

    /// Check the arguments against the sidecar's own limits and build the path and the body from
    /// the typed fields. The window's JSON text is not used past this point.
    pub fn prepare(&self) -> Result<Prepared, ProxyError> {
        let route = self.route();
        let (id, body) = match self {
            RouteRequest::Readiness(_) | RouteRequest::VaultIndexStart(_) => (None, None),
            RouteRequest::QuestionnaireInspect(args) | RouteRequest::DraftRows(args) => {
                if !file_is_valid(&args.file) {
                    return Err(ProxyError::BadRequest);
                }
                (None, Some(json_body(args)?))
            }
            RouteRequest::QuestionnaireStart(args) => {
                if !file_is_valid(&args.file) || !mapping_is_valid(&args.mapping) {
                    return Err(ProxyError::BadRequest);
                }
                (None, Some(json_body(args)?))
            }
            RouteRequest::QuestionnaireStatus(args) => (Some(&args.run_id), None),
            RouteRequest::VaultIndexStatus(args) => (Some(&args.job_id), None),
        };
        let path = match (route.spec().path, id) {
            (PathTemplate::Fixed(path), None) => path.to_owned(),
            (PathTemplate::WithId(prefix), Some(id)) => format!("{prefix}{}", id.as_str()),
            // The table and the request disagree: a bug in this file, never the window's doing.
            _ => return Err(ProxyError::Internal),
        };
        Ok(Prepared { route, path, body })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ID: &str = "0123456789abcdef";

    /// The whole table, written out. A route added, removed or changed (method, path, a cap or a
    /// timeout) fails here.
    #[test]
    fn route_table_has_exactly_the_seven_routes() {
        use Method::{Get, Post};
        use PathTemplate::{Fixed, WithId};
        let secs = Duration::from_secs;
        #[rustfmt::skip]
        let expected = [
            (Route::Readiness,            Get,  Fixed("/readiness"),              16 * 1024, secs(5)),
            (Route::QuestionnaireInspect, Post, Fixed("/questionnaire/inspect"),  MIB,       secs(60)),
            (Route::QuestionnaireStart,   Post, Fixed("/questionnaire/runs"),     MIB,       secs(60)),
            (Route::QuestionnaireStatus,  Get,  WithId("/questionnaire/runs/"),   MIB,       secs(5)),
            (Route::VaultIndexStart,      Post, Fixed("/vault/index"),            MIB,       secs(30)),
            (Route::VaultIndexStatus,     Get,  WithId("/vault/index/"),          MIB,       secs(5)),
            (Route::DraftRows,            Post, Fixed("/questionnaire/draft"),    4 * MIB,   secs(60)),
        ];
        assert_eq!(Route::ALL.len(), 7);
        assert_eq!(expected.len(), Route::ALL.len());
        for (index, (route, method, path, response_cap, timeout)) in
            expected.into_iter().enumerate()
        {
            assert_eq!(Route::ALL[index], route, "order of Route::ALL");
            let spec = route.spec();
            assert_eq!(spec.route, route);
            assert_eq!(spec.method, method, "{route:?}");
            assert_eq!(spec.path, path, "{route:?}");
            assert_eq!(spec.request_cap, 16 * 1024, "{route:?}");
            assert_eq!(spec.response_cap, response_cap, "{route:?}");
            assert_eq!(spec.timeout, timeout, "{route:?}");
        }
    }

    #[test]
    fn every_route_appears_once_in_all() {
        for route in Route::ALL {
            assert_eq!(
                Route::ALL.iter().filter(|other| **other == route).count(),
                1,
                "{route:?}"
            );
        }
    }

    #[test]
    fn only_a_lowercase_hex_id_of_8_to_64_characters_is_accepted() {
        assert!(HexId::parse("01234567").is_some());
        assert!(HexId::parse(&"a".repeat(64)).is_some());
        let bad = [
            "".to_owned(),
            "0123456".to_owned(),
            "a".repeat(65),
            "0123456G".to_owned(),
            "0123ABCD".to_owned(),
            "../../etc".to_owned(),
            "01234567/x".to_owned(),
            "01234567?x".to_owned(),
            "01234567.x".to_owned(),
            // Only hex digits and one other character, so each is refused for that character alone.
            "0123456.789".to_owned(),
            "........".to_owned(),
            "01234567..".to_owned(),
            "0123456-789".to_owned(),
            "0123456_789".to_owned(),
            "0123 4567".to_owned(),
            "01234567\n".to_owned(),
            "0123456\u{e9}".to_owned(),
        ];
        for text in bad {
            assert!(HexId::parse(&text).is_none(), "{text:?}");
        }
    }

    #[test]
    fn a_path_is_a_fixed_template_plus_at_most_one_hex_id() {
        let status = RouteRequest::from_json(&format!(
            r#"{{"route":"questionnaire_status","run_id":"{ID}"}}"#
        ))
        .unwrap()
        .prepare()
        .unwrap();
        assert_eq!(status.path, format!("/questionnaire/runs/{ID}"));
        assert_eq!(status.body, None);
        let job = RouteRequest::from_json(&format!(
            r#"{{"route":"vault_index_status","job_id":"{ID}"}}"#
        ))
        .unwrap()
        .prepare()
        .unwrap();
        assert_eq!(job.path, format!("/vault/index/{ID}"));
        let readiness = RouteRequest::from_json(r#"{"route":"readiness"}"#)
            .unwrap()
            .prepare()
            .unwrap();
        assert_eq!(readiness.path, "/readiness");
        assert_eq!(readiness.body, None);
        let start = RouteRequest::from_json(r#"{"route":"vault_index_start"}"#)
            .unwrap()
            .prepare()
            .unwrap();
        assert_eq!((start.path.as_str(), start.body), ("/vault/index", None));
    }

    #[test]
    fn the_body_is_rebuilt_from_typed_fields_not_forwarded() {
        // Odd spacing and key order: the host's body is the canonical serialization instead.
        let request =
            RouteRequest::from_json("{ \"file\" : \"a b.xlsx\" ,\n \"route\":\"draft_rows\" }")
                .unwrap();
        let prepared = request.prepare().unwrap();
        assert_eq!(prepared.path, "/questionnaire/draft");
        assert_eq!(
            prepared.body.as_deref(),
            Some(br#"{"file":"a b.xlsx"}"#.as_slice())
        );

        let start = RouteRequest::from_json(
            r#"{"mapping":{"id_col":1,"question_col":2,"header_row":3,"sheet":"S"},"route":"questionnaire_start","file":"q.xlsx"}"#,
        )
        .unwrap()
        .prepare()
        .unwrap();
        assert_eq!(
            start.body.as_deref(),
            Some(
                br#"{"file":"q.xlsx","mapping":{"sheet":"S","header_row":3,"question_col":2,"id_col":1}}"#
                    .as_slice()
            )
        );
        // No `id_col` is left out of the body, not sent as null.
        let no_id = RouteRequest::from_json(
            r#"{"route":"questionnaire_start","file":"q.xlsx","mapping":{"sheet":"S","header_row":1,"question_col":1}}"#,
        )
        .unwrap()
        .prepare()
        .unwrap();
        assert_eq!(
            no_id.body.as_deref(),
            Some(
                br#"{"file":"q.xlsx","mapping":{"sheet":"S","header_row":1,"question_col":1}}"#
                    .as_slice()
            )
        );
    }

    #[test]
    fn a_request_that_does_not_fit_its_route_is_bad_request() {
        let long_file = "x".repeat(261);
        let long_sheet = "s".repeat(65);
        let cases = [
            r#"{"route":"nope"}"#.to_owned(),
            r#"{"route":"ledger_import"}"#.to_owned(),
            r#"{}"#.to_owned(),
            r#"[]"#.to_owned(),
            "not json".to_owned(),
            // extra keys, on a route with arguments and on routes with none
            r#"{"route":"draft_rows","file":"a.xlsx","url":"http://example.com"}"#.to_owned(),
            r#"{"route":"readiness","path":"/x"}"#.to_owned(),
            r#"{"route":"vault_index_start","headers":{}}"#.to_owned(),
            // missing and mistyped fields
            r#"{"route":"draft_rows"}"#.to_owned(),
            r#"{"route":"draft_rows","file":5}"#.to_owned(),
            r#"{"route":"questionnaire_status","run_id":"../x"}"#.to_owned(),
            r#"{"route":"questionnaire_status","run_id":"ABCDEF01"}"#.to_owned(),
            r#"{"route":"vault_index_status","job_id":"short"}"#.to_owned(),
            // the sidecar's own limits
            r#"{"route":"draft_rows","file":""}"#.to_owned(),
            format!(r#"{{"route":"draft_rows","file":"{long_file}"}}"#),
            format!(
                r#"{{"route":"questionnaire_start","file":"q.xlsx","mapping":{{"sheet":"{long_sheet}","header_row":1,"question_col":1}}}}"#
            ),
            r#"{"route":"questionnaire_start","file":"q.xlsx","mapping":{"sheet":"S","header_row":0,"question_col":1}}"#.to_owned(),
            r#"{"route":"questionnaire_start","file":"q.xlsx","mapping":{"sheet":"S","header_row":1,"question_col":16385}}"#.to_owned(),
            r#"{"route":"questionnaire_start","file":"q.xlsx","mapping":{"sheet":"S","header_row":1,"question_col":1,"id_col":0}}"#.to_owned(),
            r#"{"route":"questionnaire_start","file":"q.xlsx","mapping":{"sheet":"S","header_row":1,"question_col":1,"extra":1}}"#.to_owned(),
        ];
        for text in cases {
            let outcome = RouteRequest::from_json(&text).and_then(|request| request.prepare());
            assert_eq!(outcome, Err(ProxyError::BadRequest), "{text}");
        }
    }

    #[test]
    fn only_the_three_file_routes_carry_a_file_name_and_each_has_its_own_folder() {
        let file_of = |json: &str| {
            RouteRequest::from_json(json)
                .unwrap()
                .file()
                .map(|(folder, name)| (folder, name.to_owned()))
        };
        assert_eq!(
            file_of(r#"{"route":"questionnaire_inspect","file":"a.xlsx"}"#),
            Some((FileFolder::Questionnaires, "a.xlsx".to_owned()))
        );
        assert_eq!(
            file_of(
                r#"{"route":"questionnaire_start","file":"b.xlsx","mapping":{"sheet":"S","header_row":1,"question_col":1}}"#
            ),
            Some((FileFolder::Questionnaires, "b.xlsx".to_owned()))
        );
        assert_eq!(
            file_of(r#"{"route":"draft_rows","file":"c.xlsx"}"#),
            Some((FileFolder::Exports, "c.xlsx".to_owned()))
        );
        for json in [
            r#"{"route":"readiness"}"#.to_owned(),
            r#"{"route":"vault_index_start"}"#.to_owned(),
            format!(r#"{{"route":"questionnaire_status","run_id":"{ID}"}}"#),
            format!(r#"{{"route":"vault_index_status","job_id":"{ID}"}}"#),
        ] {
            assert_eq!(file_of(&json), None, "{json}");
        }
    }

    #[test]
    fn from_value_gives_bad_request_for_anything_that_does_not_fit() {
        let ok = serde_json::json!({"route": "readiness"});
        assert!(RouteRequest::from_value(ok).is_ok());
        for value in [
            serde_json::json!(null),
            serde_json::json!("readiness"),
            serde_json::json!({"route": "readiness", "url": "evil"}),
            serde_json::json!({"route": "draft_rows"}),
            serde_json::json!({"route": "nope"}),
        ] {
            assert_eq!(RouteRequest::from_value(value), Err(ProxyError::BadRequest));
        }
    }

    #[test]
    fn a_file_name_of_260_characters_is_the_longest_accepted() {
        let file = "x".repeat(260);
        let request =
            RouteRequest::from_json(&format!(r#"{{"route":"draft_rows","file":"{file}"}}"#))
                .unwrap();
        let prepared = request.prepare().unwrap();
        assert!(prepared.body.unwrap().len() < MAX_REQUEST_BYTES);
    }
}
