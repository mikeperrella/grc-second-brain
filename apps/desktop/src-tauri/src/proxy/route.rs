//! The closed route table and the typed requests (ADR-020 decisions 1 and 2).
//!
//! The window never supplies a URL, header, method or port. It names one of the [`Route`]s in the
//! closed table with that route's typed arguments ([`RouteRequest`]). For each route a static
//! [`RouteSpec`] fixes the HTTP method, the path template, the request cap, the response cap and
//! the timeout. The only caller-chosen parts of a path are a [`HexId`] (the sidecar's
//! `^[0-9a-f]{8,64}$`) and a [`PromiseId`] (the sidecar's dashed UUID), each written by hand so no
//! `/`, `.` or `?` can reach the URL. A suffix (`/void`, `/acknowledge`) is a closed Rust enum the
//! route decides. The one query string (`ledger_promises`) is built from constant keys and from
//! `HexId`, `bool` and integer values only (ADR-020 addendum, M6.5 plan section 6). The request
//! body is serialized again from the typed struct; the window's own text is never forwarded.
//!
//! Starting values for caps and timeouts are unmeasured (ADR-020 decision 2).

use std::time::Duration;

use serde::{Deserialize, Serialize};

use super::error::ProxyError;

const KIB: usize = 1024;
const MIB: usize = 1024 * KIB;

/// Request bodies are limited to 16 KiB unless a route says otherwise, and checked before sending
/// (G34, proxied path only).
pub const MAX_REQUEST_BYTES: usize = 16 * KIB;
/// `ledger_blast_radius`: the worst request is a 2 MiB text of control characters (6 bytes each
/// once escaped) plus the path, about 12.01 MiB; 13 MiB can never refuse a text the sidecar would
/// check (N2, with [`MAX_PROPOSED_TEXT_BYTES`] checked first).
pub const MAX_BLAST_REQUEST_BYTES: usize = 13 * MIB;
/// `reality_check`: three lists of up to 50 items of 64 characters, a 2,000-character quote and
/// the paths; the worst case is about 73.1 KiB (M6.5 plan section 4).
pub const MAX_REALITY_CHECK_REQUEST_BYTES: usize = 80 * KIB;
/// The sidecar's `MAX_FILE_BYTES` (`vault/normalize.py`), mirrored: a proposed text over this many
/// UTF-8 bytes is `bad_request` here and opens no connection.
pub const MAX_PROPOSED_TEXT_BYTES: usize = 2 * MIB;
/// The default response cap.
pub const MAX_RESPONSE_BYTES: usize = MIB;
/// `readiness` is a few model names and three words; 16 KiB is far more than it needs (owner
/// amendment to the plan's B.4, which said 1 KiB, and to ADR-020's blanket 1 MiB).
pub const MAX_READINESS_RESPONSE_BYTES: usize = 16 * KIB;
/// `draft_rows`: up to 500 questions with their answers (the sidecar's cap).
pub const MAX_DRAFT_RESPONSE_BYTES: usize = 4 * MIB;

/// `ledger_promises` (a page of promises, unmeasured), `ledger_blast_radius`, `contradictions_status`
/// (a finished report) and `vault_graph`.
pub const MAX_PAGE_RESPONSE_BYTES: usize = 4 * MIB;
/// `ledger_recall`: up to 2,000 items.
pub const MAX_RECALL_RESPONSE_BYTES: usize = 8 * MIB;
/// `ledger_void` and `ledger_acknowledge`: one event.
pub const MAX_EVENT_RESPONSE_BYTES: usize = 16 * KIB;
/// `reality_claims`: up to 100 candidates.
pub const MAX_CLAIMS_RESPONSE_BYTES: usize = 2 * MIB;

/// The sidecar's own limits, mirrored so a request that it would refuse is `bad_request` here.
const MAX_FILE_NAME_CHARS: usize = 260;
const MAX_SHEET_NAME_CHARS: usize = 64;
const MAX_HEADER_ROW: u32 = 1_048_576;
const MAX_COLUMN_INDEX: u32 = 16_384;
/// `api/ledger.py`: `MAX_PAGE`, `MAX_PAGE_SIZE` (`ledger/service.py`), `MAX_NEW_NAME_CHARS`,
/// `MAX_PATH_CHARS`, `MAX_EVENT_ID`.
const MAX_PAGE: u32 = 100_000;
const MAX_PAGE_SIZE: u32 = 100;
const MAX_NEW_CUSTOMER_NAME_CHARS: usize = 1_000;
const MAX_BLAST_PATH_CHARS: usize = 1_000;
const MAX_EVENT_ID: u64 = i64::MAX as u64;
/// `reality/check.py`: `MAX_TYPE_NAMES`, `MAX_TYPE_CHARS`, `MAX_PATH_CHARS`,
/// `MAX_REQUEST_QUOTE_CHARS`.
const MAX_TYPE_ITEMS: usize = 50;
const MAX_TYPE_CHARS: usize = 64;
const MAX_CLAIM_PATH_CHARS: usize = 512;
const MAX_CLAIM_QUOTE_CHARS: usize = 2_000;
/// `api/contradictions.py`: `MAX_SCOPE_CHARS`, `MAX_CAP`.
const MAX_SCOPE_CHARS: usize = 260;
const MAX_PAIR_CAP: u8 = 100;

/// The routes of the closed table: the seven M6 core routes, then the fourteen M6.5 routes (ledger,
/// reality check, contradiction runs, vault graph). Closed: adding a variant breaks the exhaustive
/// match in [`Route::spec`], then the table test. The snake_case name of each variant is the
/// `route` word the window sends and the name of its reply fixture in `fixtures/contracts/replies/`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route {
    Readiness,
    QuestionnaireInspect,
    QuestionnaireStart,
    QuestionnaireStatus,
    VaultIndexStart,
    VaultIndexStatus,
    DraftRows,
    LedgerCustomers,
    LedgerPromises,
    LedgerCitedPaths,
    LedgerImport,
    LedgerRecall,
    LedgerVoid,
    LedgerAcknowledge,
    LedgerBlastRadius,
    RealityInspect,
    RealityClaims,
    RealityCheck,
    ContradictionsStart,
    ContradictionsStatus,
    VaultGraph,
}

impl Route {
    pub const ALL: [Route; 21] = [
        Route::Readiness,
        Route::QuestionnaireInspect,
        Route::QuestionnaireStart,
        Route::QuestionnaireStatus,
        Route::VaultIndexStart,
        Route::VaultIndexStatus,
        Route::DraftRows,
        Route::LedgerCustomers,
        Route::LedgerPromises,
        Route::LedgerCitedPaths,
        Route::LedgerImport,
        Route::LedgerRecall,
        Route::LedgerVoid,
        Route::LedgerAcknowledge,
        Route::LedgerBlastRadius,
        Route::RealityInspect,
        Route::RealityClaims,
        Route::RealityCheck,
        Route::ContradictionsStart,
        Route::ContradictionsStatus,
        Route::VaultGraph,
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
            Route::LedgerCustomers => &LEDGER_CUSTOMERS,
            Route::LedgerPromises => &LEDGER_PROMISES,
            Route::LedgerCitedPaths => &LEDGER_CITED_PATHS,
            Route::LedgerImport => &LEDGER_IMPORT,
            Route::LedgerRecall => &LEDGER_RECALL,
            Route::LedgerVoid => &LEDGER_VOID,
            Route::LedgerAcknowledge => &LEDGER_ACKNOWLEDGE,
            Route::LedgerBlastRadius => &LEDGER_BLAST_RADIUS,
            Route::RealityInspect => &REALITY_INSPECT,
            Route::RealityClaims => &REALITY_CLAIMS,
            Route::RealityCheck => &REALITY_CHECK,
            Route::ContradictionsStart => &CONTRADICTIONS_START,
            Route::ContradictionsStatus => &CONTRADICTIONS_STATUS,
            Route::VaultGraph => &VAULT_GRAPH,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    Get,
    Post,
}

/// What follows a promise id in a path. Closed: the route decides it, the window never sends one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Suffix {
    Void,
    Acknowledge,
}

impl Suffix {
    pub fn as_str(self) -> &'static str {
        match self {
            Suffix::Void => "/void",
            Suffix::Acknowledge => "/acknowledge",
        }
    }
}

/// Where the route lives on the sidecar. The only caller-chosen text that can reach a path is a
/// [`HexId`], a [`PromiseId`] or the typed query of [`PromisesQuery`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PathTemplate {
    /// The whole path, for example `/readiness`.
    Fixed(&'static str),
    /// A prefix ending in `/`, followed by one [`HexId`].
    WithId(&'static str),
    /// A prefix ending in `/`, one [`PromiseId`], then a fixed [`Suffix`].
    WithIdAndSuffix(&'static str, Suffix),
    /// A fixed path followed by `?` and the typed query of [`PromisesQuery`]. Only
    /// `ledger_promises` has this template (a test pins it).
    FixedWithQuery(&'static str),
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

static LEDGER_CUSTOMERS: RouteSpec = RouteSpec {
    route: Route::LedgerCustomers,
    method: Method::Get,
    path: PathTemplate::Fixed("/ledger/customers"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(5),
};

static LEDGER_PROMISES: RouteSpec = RouteSpec {
    route: Route::LedgerPromises,
    method: Method::Get,
    path: PathTemplate::FixedWithQuery("/ledger/promises"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_PAGE_RESPONSE_BYTES,
    timeout: Duration::from_secs(30),
};

static LEDGER_CITED_PATHS: RouteSpec = RouteSpec {
    route: Route::LedgerCitedPaths,
    method: Method::Get,
    path: PathTemplate::Fixed("/ledger/cited-paths"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(30),
};

static LEDGER_IMPORT: RouteSpec = RouteSpec {
    route: Route::LedgerImport,
    method: Method::Post,
    path: PathTemplate::Fixed("/ledger/import"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

static LEDGER_RECALL: RouteSpec = RouteSpec {
    route: Route::LedgerRecall,
    method: Method::Post,
    path: PathTemplate::Fixed("/ledger/recall"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RECALL_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

static LEDGER_VOID: RouteSpec = RouteSpec {
    route: Route::LedgerVoid,
    method: Method::Post,
    path: PathTemplate::WithIdAndSuffix("/ledger/promises/", Suffix::Void),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_EVENT_RESPONSE_BYTES,
    timeout: Duration::from_secs(10),
};

static LEDGER_ACKNOWLEDGE: RouteSpec = RouteSpec {
    route: Route::LedgerAcknowledge,
    method: Method::Post,
    path: PathTemplate::WithIdAndSuffix("/ledger/promises/", Suffix::Acknowledge),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_EVENT_RESPONSE_BYTES,
    timeout: Duration::from_secs(10),
};

static LEDGER_BLAST_RADIUS: RouteSpec = RouteSpec {
    route: Route::LedgerBlastRadius,
    method: Method::Post,
    path: PathTemplate::Fixed("/ledger/blast-radius"),
    request_cap: MAX_BLAST_REQUEST_BYTES,
    response_cap: MAX_PAGE_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

static REALITY_INSPECT: RouteSpec = RouteSpec {
    route: Route::RealityInspect,
    method: Method::Post,
    path: PathTemplate::Fixed("/reality/inspect"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

static REALITY_CLAIMS: RouteSpec = RouteSpec {
    route: Route::RealityClaims,
    method: Method::Get,
    path: PathTemplate::Fixed("/reality/claims"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_CLAIMS_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

static REALITY_CHECK: RouteSpec = RouteSpec {
    route: Route::RealityCheck,
    method: Method::Post,
    path: PathTemplate::Fixed("/reality/check"),
    request_cap: MAX_REALITY_CHECK_REQUEST_BYTES,
    response_cap: MAX_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

static CONTRADICTIONS_START: RouteSpec = RouteSpec {
    route: Route::ContradictionsStart,
    method: Method::Post,
    path: PathTemplate::Fixed("/contradictions/runs"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_EVENT_RESPONSE_BYTES,
    timeout: Duration::from_secs(30),
};

static CONTRADICTIONS_STATUS: RouteSpec = RouteSpec {
    route: Route::ContradictionsStatus,
    method: Method::Get,
    path: PathTemplate::WithId("/contradictions/runs/"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_PAGE_RESPONSE_BYTES,
    timeout: Duration::from_secs(5),
};

static VAULT_GRAPH: RouteSpec = RouteSpec {
    route: Route::VaultGraph,
    method: Method::Get,
    path: PathTemplate::Fixed("/vault/graph"),
    request_cap: MAX_REQUEST_BYTES,
    response_cap: MAX_PAGE_RESPONSE_BYTES,
    timeout: Duration::from_secs(60),
};

/// A run or job id: 8 to 64 characters, each `0-9` or `a-f` (the sidecar's `^[0-9a-f]{8,64}$`).
/// Written by hand so no regex crate is needed. The only text a caller can put in a URL path.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
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

/// A promise id: the sidecar's `PROMISE_ID_PATTERN`, a dashed lowercase UUID `8-4-4-4-12`
/// (`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`). It is NOT a [`HexId`]. Written
/// by hand like `HexId`; only `0-9`, `a-f` and four dashes in fixed places, so no uppercase, brace,
/// `/`, `.` or `?` can reach a path.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(try_from = "String")]
pub struct PromiseId(String);

impl PromiseId {
    pub fn parse(text: &str) -> Option<Self> {
        let valid = text.len() == 36
            && text.bytes().enumerate().all(|(position, byte)| {
                if matches!(position, 8 | 13 | 18 | 23) {
                    byte == b'-'
                } else {
                    byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
                }
            });
        valid.then(|| Self(text.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl TryFrom<String> for PromiseId {
    type Error = &'static str;

    fn try_from(text: String) -> Result<Self, Self::Error> {
        Self::parse(&text).ok_or("not a promise id")
    }
}

/// A calendar date written `YYYY-MM-DD` with ASCII digits (the sidecar's `sent_on` and
/// `exported_at`). Stricter than the sidecar in one way: the year must be 0001 to 9999 and the day
/// must exist (the sidecar refuses an impossible day later, with its own typed word).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(try_from = "String")]
pub struct IsoDate(String);

impl IsoDate {
    pub fn parse(text: &str) -> Option<Self> {
        let bytes = text.as_bytes();
        if bytes.len() != 10 || bytes[4] != b'-' || bytes[7] != b'-' {
            return None;
        }
        let number = |range: std::ops::Range<usize>| -> Option<u32> {
            let digits = bytes.get(range)?;
            if digits.iter().all(u8::is_ascii_digit) {
                digits
                    .iter()
                    .try_fold(0u32, |sum, digit| Some(sum * 10 + u32::from(digit - b'0')))
            } else {
                None
            }
        };
        let (year, month, day) = (number(0..4)?, number(5..7)?, number(8..10)?);
        let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        let days_in_month = match month {
            1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
            4 | 6 | 9 | 11 => 30,
            2 if leap => 29,
            2 => 28,
            _ => return None,
        };
        ((1..=9999).contains(&year) && (1..=days_in_month).contains(&day))
            .then(|| Self(text.to_owned()))
    }
}

impl TryFrom<String> for IsoDate {
    type Error = &'static str;

    fn try_from(text: String) -> Result<Self, Self::Error> {
        Self::parse(&text).ok_or("not a date")
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

/// `ledger_promises`: the typed query. The path never carries window text: [`PromisesQuery::to_query`]
/// writes constant keys in a fixed order and formats values only from a [`HexId`], a `bool` and
/// integers.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromisesQuery {
    #[serde(default)]
    pub customer_id: Option<HexId>,
    pub include_voided: bool,
    pub page: u32,
    pub page_size: u32,
}

const QUERY_CUSTOMER_ID: &str = "customer_id";
const QUERY_INCLUDE_VOIDED: &str = "include_voided";
const QUERY_PAGE: &str = "page";
const QUERY_PAGE_SIZE: &str = "page_size";

impl PromisesQuery {
    fn is_valid(&self) -> bool {
        (1..=MAX_PAGE).contains(&self.page) && (1..=MAX_PAGE_SIZE).contains(&self.page_size)
    }

    /// The query string without its `?`: `customer_id` (when given), `include_voided`, `page`,
    /// `page_size`, in that order.
    pub fn to_query(&self) -> String {
        let mut query = String::new();
        if let Some(customer) = &self.customer_id {
            query.push_str(&format!("{QUERY_CUSTOMER_ID}={}&", customer.as_str()));
        }
        query.push_str(&format!(
            "{QUERY_INCLUDE_VOIDED}={}&{QUERY_PAGE}={}&{QUERY_PAGE_SIZE}={}",
            self.include_voided, self.page, self.page_size
        ));
        query
    }
}

/// `ledger_import`. `file` is a draft in the Exports folder. The body is these fields as typed.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImportArgs {
    pub run_id: HexId,
    pub file: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub existing_customer_id: Option<HexId>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub new_customer_name: Option<String>,
    pub confirm_new: bool,
    pub sent_on: IsoDate,
    pub preview: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RecallArgs {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub customer_id: Option<HexId>,
}

/// The sidecar's `VoidReason` (`repositories/base.py`): a fixed set, so a void carries no free text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VoidReason {
    WrongCustomer,
    ImportedInError,
    NotActuallySent,
    Other,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VoidArgs {
    pub promise_id: PromiseId,
    pub reason: VoidReason,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AcknowledgeArgs {
    pub promise_id: PromiseId,
    pub seen_recall_event_id: u64,
}

/// `ledger_blast_radius`: exactly one of `proposed_text` (the whole new file) or `delete_file`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BlastArgs {
    pub path: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proposed_text: Option<String>,
    pub delete_file: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub customer_id: Option<HexId>,
}

/// The claim the owner chose, as the sidecar's `ClaimRef`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ClaimArgs {
    pub path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub quote: String,
}

/// `reality_check`. The three lists reach the sidecar trimmed ([`trim_type_list`]).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CheckArgs {
    pub file: String,
    pub claim: ClaimArgs,
    pub mfa_types: Vec<String>,
    pub phone_types: Vec<String>,
    pub active_statuses: Vec<String>,
    pub exported_at: IsoDate,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContradictionStartArgs {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scope: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cap: Option<u8>,
}

/// The body of `ledger_void`: the reason only. The promise id is in the path.
#[derive(Serialize)]
struct VoidBody {
    reason: VoidReason,
}

/// The body of `ledger_acknowledge`: the recall event seen. The promise id is in the path.
#[derive(Serialize)]
struct AcknowledgeBody {
    seen_recall_event_id: u64,
}

/// Which of the user's folders a file name refers to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileFolder {
    Questionnaires,
    Exports,
    Imports,
}

/// What the window sends: a route name in `route` plus that route's own fields, nothing else.
/// `{"route":"draft_rows","file":"x.xlsx"}`. An unknown route, a missing or extra field (a `query`,
/// `suffix` or `path` key is an extra field), or an id of the wrong shape fails to parse
/// ([`RouteRequest::from_json`] reports `bad_request`).
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
    LedgerCustomers(NoArgs),
    LedgerPromises(PromisesQuery),
    LedgerCitedPaths(NoArgs),
    LedgerImport(ImportArgs),
    LedgerRecall(RecallArgs),
    LedgerVoid(VoidArgs),
    LedgerAcknowledge(AcknowledgeArgs),
    LedgerBlastRadius(BlastArgs),
    RealityInspect(FileArgs),
    RealityClaims(NoArgs),
    RealityCheck(CheckArgs),
    ContradictionsStart(ContradictionStartArgs),
    ContradictionsStatus(RunIdArgs),
    VaultGraph(NoArgs),
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

fn chars_within(text: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&text.chars().count())
}

fn import_is_valid(args: &ImportArgs) -> bool {
    file_is_valid(&args.file)
        && args
            .new_customer_name
            .as_deref()
            .is_none_or(|name| chars_within(name, 1, MAX_NEW_CUSTOMER_NAME_CHARS))
}

/// The sidecar's vault-relative path rule for blast radius (`api/ledger.py`): not empty, not
/// absolute, no drive letter, no backslash, no NUL and no `..` segment.
fn blast_path_is_valid(path: &str) -> bool {
    let drive_letter = path.len() >= 2
        && path.as_bytes().first().is_some_and(u8::is_ascii_alphabetic)
        && path.as_bytes().get(1) == Some(&b':');
    chars_within(path, 1, MAX_BLAST_PATH_CHARS)
        && !path.starts_with('/')
        && !drive_letter
        && !path.contains(['\\', '\0'])
        && !path.split('/').any(|part| part == "..")
}

/// A blast-radius request the sidecar would check: a valid path, exactly one of `proposed_text`
/// or `delete_file`, and a text of at most [`MAX_PROPOSED_TEXT_BYTES`] UTF-8 bytes (the byte check
/// of N2: with it, the 13 MiB request cap can never refuse a text the sidecar would check).
fn blast_is_valid(args: &BlastArgs) -> bool {
    blast_path_is_valid(&args.path)
        && args.proposed_text.is_some() != args.delete_file
        && args
            .proposed_text
            .as_deref()
            .is_none_or(|text| text.len() <= MAX_PROPOSED_TEXT_BYTES)
}

/// Whether Python's `str.strip()` removes `c`. Rust's `char::is_whitespace` (Unicode `White_Space`)
/// removes 25 characters; Python removes those 25 and also U+001C to U+001F (checked over every
/// code point on 2026-10-19; see `the_trim_matches_python_on_every_code_point`). pydantic's
/// `strip_whitespace`, which the sidecar really uses for these lists, strips the same 25 as Rust.
fn python_strips(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Trim one list item for `reality_check` and say whether it may be sent. The item is trimmed with
/// Rust's `White_Space` set, which is what the sidecar's own trim removes. An item that, after
/// that, still begins or ends with a character Python's `str.strip()` would remove (U+001C to
/// U+001F) is refused: whichever of the two trims the sidecar applies, Rust never accepts an item
/// the sidecar would refuse (parity rule: Rust may refuse more, never less). An item must be 1 to
/// 64 characters after trimming.
fn trim_type_item(item: &str) -> Option<String> {
    let trimmed = item.trim();
    let edge_disagrees = trimmed.chars().next().is_some_and(python_strips)
        || trimmed.chars().next_back().is_some_and(python_strips);
    (!edge_disagrees && chars_within(trimmed, 1, MAX_TYPE_CHARS)).then(|| trimmed.to_owned())
}

/// Trim every item of a list of at most [`MAX_TYPE_ITEMS`] items and at least `min_items`.
fn trim_type_list(items: &[String], min_items: usize) -> Option<Vec<String>> {
    if !(min_items..=MAX_TYPE_ITEMS).contains(&items.len()) {
        return None;
    }
    items.iter().map(|item| trim_type_item(item)).collect()
}

fn claim_is_valid(claim: &ClaimArgs) -> bool {
    chars_within(&claim.path, 1, MAX_CLAIM_PATH_CHARS)
        && chars_within(&claim.quote, 1, MAX_CLAIM_QUOTE_CHARS)
        && claim.start_line >= 1
        && claim.end_line >= claim.start_line
}

/// The body of `reality_check`: the typed fields with the three lists trimmed.
fn check_body(args: &CheckArgs) -> Option<CheckArgs> {
    if !file_is_valid(&args.file) || !claim_is_valid(&args.claim) {
        return None;
    }
    Some(CheckArgs {
        file: args.file.clone(),
        claim: args.claim.clone(),
        mfa_types: trim_type_list(&args.mfa_types, 1)?,
        phone_types: trim_type_list(&args.phone_types, 0)?,
        active_statuses: trim_type_list(&args.active_statuses, 1)?,
        exported_at: args.exported_at.clone(),
    })
}

/// The sidecar's scope rule for contradiction runs: a vault-relative path prefix of 1 to 260
/// characters, with no backslash, colon, NUL, leading slash or `..` segment.
fn scope_is_valid(scope: &str) -> bool {
    chars_within(scope, 1, MAX_SCOPE_CHARS)
        && !scope.contains(['\\', ':', '\0'])
        && !scope.starts_with('/')
        && !scope.split('/').any(|part| part == "..")
}

/// The one variable part of a path, borrowed from the typed request.
enum PathPart<'a> {
    None,
    Hex(&'a HexId),
    Promise(&'a PromiseId),
    Query(String),
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
    /// and `ledger_import` (a reviewed draft), the import folder for `reality_inspect` and
    /// `reality_check` (the sidecar resolves them the same way). `None` for a route with no file
    /// name.
    pub fn file(&self) -> Option<(FileFolder, &str)> {
        match self {
            RouteRequest::QuestionnaireInspect(args) => {
                Some((FileFolder::Questionnaires, args.file.as_str()))
            }
            RouteRequest::QuestionnaireStart(args) => {
                Some((FileFolder::Questionnaires, args.file.as_str()))
            }
            RouteRequest::DraftRows(args) => Some((FileFolder::Exports, args.file.as_str())),
            RouteRequest::LedgerImport(args) => Some((FileFolder::Exports, args.file.as_str())),
            RouteRequest::RealityInspect(args) => Some((FileFolder::Imports, args.file.as_str())),
            RouteRequest::RealityCheck(args) => Some((FileFolder::Imports, args.file.as_str())),
            RouteRequest::Readiness(_)
            | RouteRequest::QuestionnaireStatus(_)
            | RouteRequest::VaultIndexStart(_)
            | RouteRequest::VaultIndexStatus(_)
            | RouteRequest::LedgerCustomers(_)
            | RouteRequest::LedgerPromises(_)
            | RouteRequest::LedgerCitedPaths(_)
            | RouteRequest::LedgerRecall(_)
            | RouteRequest::LedgerVoid(_)
            | RouteRequest::LedgerAcknowledge(_)
            | RouteRequest::LedgerBlastRadius(_)
            | RouteRequest::RealityClaims(_)
            | RouteRequest::ContradictionsStart(_)
            | RouteRequest::ContradictionsStatus(_)
            | RouteRequest::VaultGraph(_) => None,
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
            RouteRequest::LedgerCustomers(_) => Route::LedgerCustomers,
            RouteRequest::LedgerPromises(_) => Route::LedgerPromises,
            RouteRequest::LedgerCitedPaths(_) => Route::LedgerCitedPaths,
            RouteRequest::LedgerImport(_) => Route::LedgerImport,
            RouteRequest::LedgerRecall(_) => Route::LedgerRecall,
            RouteRequest::LedgerVoid(_) => Route::LedgerVoid,
            RouteRequest::LedgerAcknowledge(_) => Route::LedgerAcknowledge,
            RouteRequest::LedgerBlastRadius(_) => Route::LedgerBlastRadius,
            RouteRequest::RealityInspect(_) => Route::RealityInspect,
            RouteRequest::RealityClaims(_) => Route::RealityClaims,
            RouteRequest::RealityCheck(_) => Route::RealityCheck,
            RouteRequest::ContradictionsStart(_) => Route::ContradictionsStart,
            RouteRequest::ContradictionsStatus(_) => Route::ContradictionsStatus,
            RouteRequest::VaultGraph(_) => Route::VaultGraph,
        }
    }

    /// Check the arguments against the sidecar's own limits and build the path and the body from
    /// the typed fields. The window's JSON text is not used past this point.
    pub fn prepare(&self) -> Result<Prepared, ProxyError> {
        let route = self.route();
        let (part, body) = match self {
            RouteRequest::Readiness(_)
            | RouteRequest::VaultIndexStart(_)
            | RouteRequest::LedgerCustomers(_)
            | RouteRequest::LedgerCitedPaths(_)
            | RouteRequest::RealityClaims(_)
            | RouteRequest::VaultGraph(_) => (PathPart::None, None),
            RouteRequest::QuestionnaireInspect(args)
            | RouteRequest::DraftRows(args)
            | RouteRequest::RealityInspect(args) => {
                if !file_is_valid(&args.file) {
                    return Err(ProxyError::BadRequest);
                }
                (PathPart::None, Some(json_body(args)?))
            }
            RouteRequest::QuestionnaireStart(args) => {
                if !file_is_valid(&args.file) || !mapping_is_valid(&args.mapping) {
                    return Err(ProxyError::BadRequest);
                }
                (PathPart::None, Some(json_body(args)?))
            }
            RouteRequest::QuestionnaireStatus(args) | RouteRequest::ContradictionsStatus(args) => {
                (PathPart::Hex(&args.run_id), None)
            }
            RouteRequest::VaultIndexStatus(args) => (PathPart::Hex(&args.job_id), None),
            RouteRequest::LedgerPromises(query) => {
                if !query.is_valid() {
                    return Err(ProxyError::BadRequest);
                }
                (PathPart::Query(query.to_query()), None)
            }
            RouteRequest::LedgerImport(args) => {
                if !import_is_valid(args) {
                    return Err(ProxyError::BadRequest);
                }
                (PathPart::None, Some(json_body(args)?))
            }
            RouteRequest::LedgerRecall(args) => (PathPart::None, Some(json_body(args)?)),
            RouteRequest::LedgerVoid(args) => (
                PathPart::Promise(&args.promise_id),
                Some(json_body(&VoidBody {
                    reason: args.reason,
                })?),
            ),
            RouteRequest::LedgerAcknowledge(args) => {
                if !(1..=MAX_EVENT_ID).contains(&args.seen_recall_event_id) {
                    return Err(ProxyError::BadRequest);
                }
                (
                    PathPart::Promise(&args.promise_id),
                    Some(json_body(&AcknowledgeBody {
                        seen_recall_event_id: args.seen_recall_event_id,
                    })?),
                )
            }
            RouteRequest::LedgerBlastRadius(args) => {
                if !blast_is_valid(args) {
                    return Err(ProxyError::BadRequest);
                }
                (PathPart::None, Some(json_body(args)?))
            }
            RouteRequest::RealityCheck(args) => {
                let trimmed = check_body(args).ok_or(ProxyError::BadRequest)?;
                (PathPart::None, Some(json_body(&trimmed)?))
            }
            RouteRequest::ContradictionsStart(args) => {
                let scope_ok = args.scope.as_deref().is_none_or(scope_is_valid);
                let cap_ok = args.cap.is_none_or(|cap| (1..=MAX_PAIR_CAP).contains(&cap));
                if !scope_ok || !cap_ok {
                    return Err(ProxyError::BadRequest);
                }
                (PathPart::None, Some(json_body(args)?))
            }
        };
        let path = match (route.spec().path, part) {
            (PathTemplate::Fixed(path), PathPart::None) => path.to_owned(),
            (PathTemplate::WithId(prefix), PathPart::Hex(id)) => {
                format!("{prefix}{}", id.as_str())
            }
            (PathTemplate::WithIdAndSuffix(prefix, suffix), PathPart::Promise(id)) => {
                format!("{prefix}{}{}", id.as_str(), suffix.as_str())
            }
            (PathTemplate::FixedWithQuery(path), PathPart::Query(query)) => {
                format!("{path}?{query}")
            }
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
    /// timeout) fails here. Renamed once in chunk 65d from `route_table_has_exactly_the_seven_routes`
    /// (a name with a number stops being true when a route is added) and given a request-cap column.
    #[test]
    fn route_table_is_exactly_the_listed_routes() {
        use Method::{Get, Post};
        use PathTemplate::{Fixed, FixedWithQuery, WithId, WithIdAndSuffix};
        let secs = Duration::from_secs;
        let (kib16, kib80, mib13) = (16 * KIB, 80 * KIB, 13 * MIB);
        #[rustfmt::skip]
        let expected = [
            // route                       method path                                                      request  response   timeout
            (Route::Readiness,            Get,  Fixed("/readiness"),                                       kib16, 16 * KIB, secs(5)),
            (Route::QuestionnaireInspect, Post, Fixed("/questionnaire/inspect"),                           kib16, MIB,      secs(60)),
            (Route::QuestionnaireStart,   Post, Fixed("/questionnaire/runs"),                              kib16, MIB,      secs(60)),
            (Route::QuestionnaireStatus,  Get,  WithId("/questionnaire/runs/"),                            kib16, MIB,      secs(5)),
            (Route::VaultIndexStart,      Post, Fixed("/vault/index"),                                     kib16, MIB,      secs(30)),
            (Route::VaultIndexStatus,     Get,  WithId("/vault/index/"),                                   kib16, MIB,      secs(5)),
            (Route::DraftRows,            Post, Fixed("/questionnaire/draft"),                             kib16, 4 * MIB,  secs(60)),
            (Route::LedgerCustomers,      Get,  Fixed("/ledger/customers"),                                kib16, MIB,      secs(5)),
            (Route::LedgerPromises,       Get,  FixedWithQuery("/ledger/promises"),                        kib16, 4 * MIB,  secs(30)),
            (Route::LedgerCitedPaths,     Get,  Fixed("/ledger/cited-paths"),                              kib16, MIB,      secs(30)),
            (Route::LedgerImport,         Post, Fixed("/ledger/import"),                                   kib16, MIB,      secs(60)),
            (Route::LedgerRecall,         Post, Fixed("/ledger/recall"),                                   kib16, 8 * MIB,  secs(60)),
            (Route::LedgerVoid,           Post, WithIdAndSuffix("/ledger/promises/", Suffix::Void),        kib16, 16 * KIB, secs(10)),
            (Route::LedgerAcknowledge,    Post, WithIdAndSuffix("/ledger/promises/", Suffix::Acknowledge), kib16, 16 * KIB, secs(10)),
            (Route::LedgerBlastRadius,    Post, Fixed("/ledger/blast-radius"),                             mib13, 4 * MIB,  secs(60)),
            (Route::RealityInspect,       Post, Fixed("/reality/inspect"),                                 kib16, MIB,      secs(60)),
            (Route::RealityClaims,        Get,  Fixed("/reality/claims"),                                  kib16, 2 * MIB,  secs(60)),
            (Route::RealityCheck,         Post, Fixed("/reality/check"),                                   kib80, MIB,      secs(60)),
            (Route::ContradictionsStart,  Post, Fixed("/contradictions/runs"),                             kib16, 16 * KIB, secs(30)),
            (Route::ContradictionsStatus, Get,  WithId("/contradictions/runs/"),                           kib16, 4 * MIB,  secs(5)),
            (Route::VaultGraph,           Get,  Fixed("/vault/graph"),                                     kib16, 4 * MIB,  secs(60)),
        ];
        assert_eq!(Route::ALL.len(), 21);
        assert_eq!(expected.len(), Route::ALL.len());
        for (index, (route, method, path, request_cap, response_cap, timeout)) in
            expected.into_iter().enumerate()
        {
            assert_eq!(Route::ALL[index], route, "order of Route::ALL");
            let spec = route.spec();
            assert_eq!(spec.route, route);
            assert_eq!(spec.method, method, "{route:?}");
            assert_eq!(spec.path, path, "{route:?}");
            assert_eq!(spec.request_cap, request_cap, "{route:?}");
            assert_eq!(spec.response_cap, response_cap, "{route:?}");
            assert_eq!(spec.timeout, timeout, "{route:?}");
        }
    }

    #[test]
    fn only_ledger_promises_carries_a_query_and_only_the_two_promise_routes_a_suffix() {
        for route in Route::ALL {
            let path = route.spec().path;
            assert_eq!(
                matches!(path, PathTemplate::FixedWithQuery(_)),
                route == Route::LedgerPromises,
                "{route:?}"
            );
            assert_eq!(
                matches!(path, PathTemplate::WithIdAndSuffix(..)),
                matches!(route, Route::LedgerVoid | Route::LedgerAcknowledge),
                "{route:?}"
            );
        }
        assert_eq!(Suffix::Void.as_str(), "/void");
        assert_eq!(Suffix::Acknowledge.as_str(), "/acknowledge");
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

    /// Renamed in chunk 65d from `a_path_is_a_fixed_template_plus_at_most_one_hex_id`: a path is now
    /// a fixed template plus at most one typed id, a closed suffix or a typed query (ADR-020 proof
    /// 12 still holds for every route).
    #[test]
    fn a_path_is_a_fixed_template_plus_typed_ids_and_never_window_text() {
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

        // The M6.5 routes: a promise id plus a fixed suffix, a hex id, and fixed paths.
        for (suffix, route, extra) in [
            ("void", "ledger_void", r#","reason":"other""#),
            (
                "acknowledge",
                "ledger_acknowledge",
                r#","seen_recall_event_id":3"#,
            ),
        ] {
            let prepared = RouteRequest::from_json(&format!(
                r#"{{"route":"{route}","promise_id":"{PROMISE}"{extra}}}"#
            ))
            .unwrap()
            .prepare()
            .unwrap();
            assert_eq!(
                prepared.path,
                format!("/ledger/promises/{PROMISE}/{suffix}")
            );
        }
        let contradictions = RouteRequest::from_json(&format!(
            r#"{{"route":"contradictions_status","run_id":"{ID}"}}"#
        ))
        .unwrap()
        .prepare()
        .unwrap();
        assert_eq!(contradictions.path, format!("/contradictions/runs/{ID}"));
        for (name, path) in [
            ("ledger_customers", "/ledger/customers"),
            ("ledger_cited_paths", "/ledger/cited-paths"),
            ("reality_claims", "/reality/claims"),
            ("vault_graph", "/vault/graph"),
        ] {
            let prepared = RouteRequest::from_json(&format!(r#"{{"route":"{name}"}}"#))
                .unwrap()
                .prepare()
                .unwrap();
            assert_eq!(
                (prepared.path.as_str(), prepared.body),
                (path, None),
                "{name}"
            );
        }
    }

    /// The window's JSON is never URL text: `query`, `suffix` and `path` keys are extra fields,
    /// refused by `deny_unknown_fields` on every route that has a path part.
    #[test]
    fn a_window_json_with_query_suffix_or_path_keys_is_bad_request() {
        let promises = r#""include_voided":false,"page":1,"page_size":10"#;
        let void = format!(r#""promise_id":"{PROMISE}","reason":"other""#);
        let ack = format!(r#""promise_id":"{PROMISE}","seen_recall_event_id":1"#);
        let cases = [
            format!(r#"{{"route":"ledger_promises",{promises},"query":"x=1"}}"#),
            format!(r#"{{"route":"ledger_promises",{promises},"suffix":"/void"}}"#),
            format!(r#"{{"route":"ledger_promises",{promises},"path":"/x"}}"#),
            format!(r#"{{"route":"ledger_void",{void},"suffix":"/acknowledge"}}"#),
            format!(r#"{{"route":"ledger_void",{void},"path":"/ledger/promises/x"}}"#),
            format!(r#"{{"route":"ledger_void",{void},"query":"a=b"}}"#),
            format!(r#"{{"route":"ledger_acknowledge",{ack},"suffix":"/void"}}"#),
            format!(r#"{{"route":"ledger_acknowledge",{ack},"query":"a=b"}}"#),
            format!(r#"{{"route":"contradictions_status","run_id":"{ID}","query":"a=b"}}"#),
            format!(r#"{{"route":"contradictions_status","run_id":"{ID}","suffix":"/x"}}"#),
            r#"{"route":"ledger_customers","query":"a=b"}"#.to_owned(),
            r#"{"route":"vault_graph","path":"/vault/index"}"#.to_owned(),
            r#"{"route":"reality_claims","suffix":"/x"}"#.to_owned(),
        ];
        for text in cases {
            assert_eq!(
                RouteRequest::from_json(&text).and_then(|request| request.prepare()),
                Err(ProxyError::BadRequest),
                "{text}"
            );
        }
    }

    #[test]
    fn only_a_dashed_lowercase_uuid_is_a_promise_id() {
        assert!(PromiseId::parse(PROMISE).is_some());
        assert!(PromiseId::parse("00000000-0000-0000-0000-000000000000").is_some());
        // 35 and 37 characters, and the other shapes the sidecar's pattern refuses.
        let bad = [
            "".to_owned(),
            PROMISE[..35].to_owned(),
            format!("{PROMISE}a"),
            PROMISE.to_ascii_uppercase(),
            "01234567-89AB-cdef-0123-456789abcdef".to_owned(),
            format!("{{{PROMISE}}}"),
            PROMISE.replace('-', ""),
            "0123456789ab-cdef-0123-456789abcdef-".to_owned(),
            "01234567-89ab-cdef-0123_456789abcdef".to_owned(),
            "01234567-89ab-cdef-0123-456789abcde.".to_owned(),
            "01234567-89ab-cdef-0123-456789abc..f".to_owned(),
            "../../etc/passwd-0123-456789abcdef01".to_owned(),
            "01234567-89ab-cdef-0123-456789abcde?".to_owned(),
            "01234567-89ab-cdef-0123-456789abcde/".to_owned(),
            "01234567-89ab-cdef-0123-456789abcdeg".to_owned(),
            "01234567-89ab-cdef-0123-456789abcde\n".to_owned(),
            "0123456\u{e9}-89ab-cdef-0123-456789abcdef".to_owned(),
            // the right shape for a hex id, which a promise id is not
            ID.to_owned(),
        ];
        assert_eq!(PROMISE.len(), 36);
        for text in bad {
            assert!(PromiseId::parse(&text).is_none(), "{text:?}");
        }
        // And through the request: a bad promise id is `bad_request`, never a path.
        for id in ["01234567-89AB-cdef-0123-456789abcdef", "..", "a?b", ID] {
            let json = format!(r#"{{"route":"ledger_void","promise_id":"{id}","reason":"other"}}"#);
            assert_eq!(
                RouteRequest::from_json(&json),
                Err(ProxyError::BadRequest),
                "{id}"
            );
        }
    }

    #[test]
    fn the_void_and_acknowledge_bodies_carry_only_their_own_fields() {
        let void = RouteRequest::from_json(&format!(
            r#"{{"route":"ledger_void","promise_id":"{PROMISE}","reason":"wrong_customer"}}"#
        ))
        .unwrap()
        .prepare()
        .unwrap();
        assert_eq!(
            void.body.as_deref(),
            Some(br#"{"reason":"wrong_customer"}"#.as_slice())
        );
        for (word, reason) in [
            ("wrong_customer", VoidReason::WrongCustomer),
            ("imported_in_error", VoidReason::ImportedInError),
            ("not_actually_sent", VoidReason::NotActuallySent),
            ("other", VoidReason::Other),
        ] {
            assert_eq!(
                serde_json::from_str::<VoidReason>(&format!("\"{word}\"")).unwrap(),
                reason
            );
        }
        // Free text, a near miss and a different case are not a reason.
        for word in [
            "\"free text\"",
            "\"Other\"",
            "\"wrong customer\"",
            "\"\"",
            "5",
        ] {
            assert!(serde_json::from_str::<VoidReason>(word).is_err(), "{word}");
        }
        let ack = RouteRequest::from_json(&format!(
            r#"{{"route":"ledger_acknowledge","promise_id":"{PROMISE}","seen_recall_event_id":42}}"#
        ))
        .unwrap()
        .prepare()
        .unwrap();
        assert_eq!(
            ack.body.as_deref(),
            Some(br#"{"seen_recall_event_id":42}"#.as_slice())
        );
        // From 1 to i64::MAX; a zero, a negative, a fraction and a larger number are refused.
        let max = i64::MAX;
        for ok in ["1", &max.to_string()] {
            let json = format!(
                r#"{{"route":"ledger_acknowledge","promise_id":"{PROMISE}","seen_recall_event_id":{ok}}}"#
            );
            assert!(
                RouteRequest::from_json(&json).unwrap().prepare().is_ok(),
                "{ok}"
            );
        }
        let too_big = (i64::MAX as u64 + 1).to_string();
        for bad in ["0", "-1", "1.5", "\"1\"", "true", &too_big] {
            let json = format!(
                r#"{{"route":"ledger_acknowledge","promise_id":"{PROMISE}","seen_recall_event_id":{bad}}}"#
            );
            assert_eq!(
                RouteRequest::from_json(&json).and_then(|request| request.prepare()),
                Err(ProxyError::BadRequest),
                "{bad}"
            );
        }
    }

    /// The query is built from constant keys in a fixed order and from typed values only.
    #[test]
    fn the_promises_query_is_built_from_constant_keys_and_typed_values() {
        let query = |json: &str| {
            RouteRequest::from_json(json)
                .unwrap()
                .prepare()
                .unwrap()
                .path
        };
        assert_eq!(
            query(r#"{"route":"ledger_promises","include_voided":false,"page":1,"page_size":100}"#),
            "/ledger/promises?include_voided=false&page=1&page_size=100"
        );
        assert_eq!(
            query(&format!(
                r#"{{"route":"ledger_promises","page_size":7,"page":100000,"include_voided":true,"customer_id":"{ID}"}}"#
            )),
            format!(
                "/ledger/promises?customer_id={ID}&include_voided=true&page=100000&page_size=7"
            )
        );
        // No body, and no other route has a `?` in its path.
        let prepared = RouteRequest::from_json(
            r#"{"route":"ledger_promises","include_voided":false,"page":2,"page_size":5}"#,
        )
        .unwrap()
        .prepare()
        .unwrap();
        assert_eq!(prepared.body, None);
        // Out-of-range and mistyped values, and an id that is not hex.
        for bad in [
            r#""include_voided":false,"page":0,"page_size":10"#.to_owned(),
            r#""include_voided":false,"page":100001,"page_size":10"#.to_owned(),
            r#""include_voided":false,"page":1,"page_size":0"#.to_owned(),
            r#""include_voided":false,"page":1,"page_size":101"#.to_owned(),
            r#""include_voided":false,"page":-1,"page_size":10"#.to_owned(),
            r#""include_voided":"yes","page":1,"page_size":10"#.to_owned(),
            r#""include_voided":false,"page":"1&x=1","page_size":10"#.to_owned(),
            r#""include_voided":false,"page":1"#.to_owned(),
            r#""include_voided":false,"page":1,"page_size":10,"customer_id":"a&x=1""#.to_owned(),
            r#""include_voided":false,"page":1,"page_size":10,"customer_id":"ABCDEF012""#
                .to_owned(),
        ] {
            let json = format!(r#"{{"route":"ledger_promises",{bad}}}"#);
            assert_eq!(
                RouteRequest::from_json(&json).and_then(|request| request.prepare()),
                Err(ProxyError::BadRequest),
                "{json}"
            );
        }
        for route in Route::ALL {
            if route != Route::LedgerPromises {
                assert!(!matches!(
                    route.spec().path,
                    PathTemplate::FixedWithQuery(_)
                ));
            }
        }
    }

    #[test]
    fn the_blast_radius_text_is_limited_to_the_sidecars_two_mib_in_bytes() {
        let prepare = |path: &str, text: Option<&str>, delete: bool| {
            RouteRequest::LedgerBlastRadius(BlastArgs {
                path: path.to_owned(),
                proposed_text: text.map(str::to_owned),
                delete_file: delete,
                customer_id: None,
            })
            .prepare()
        };
        assert_eq!(MAX_PROPOSED_TEXT_BYTES, 2_097_152);
        let at_limit = "a".repeat(2_097_152);
        let over = "a".repeat(2_097_153);
        assert!(prepare("policies/a.md", Some(&at_limit), false).is_ok());
        assert_eq!(
            prepare("policies/a.md", Some(&over), false),
            Err(ProxyError::BadRequest)
        );
        // Counted in UTF-8 bytes, not characters: 1,048,577 two-byte characters are 2,097,154.
        let two_byte = "\u{e9}".repeat(1_048_577);
        assert_eq!(
            prepare("policies/a.md", Some(&two_byte), false),
            Err(ProxyError::BadRequest)
        );
        assert!(prepare("policies/a.md", Some(&"\u{e9}".repeat(1_048_576)), false).is_ok());
        // The worst request the byte check lets through fits the 13 MiB request cap: control
        // characters are escaped to 6 bytes each.
        let worst = prepare(&"p".repeat(1_000), Some(&"\u{1}".repeat(2_097_152)), false).unwrap();
        let length = worst.body.unwrap().len();
        assert!(
            length > 12 * MIB && length <= MAX_BLAST_REQUEST_BYTES,
            "{length}"
        );
        // Exactly one of the text and the deletion.
        assert!(prepare("a.md", None, true).is_ok());
        assert_eq!(prepare("a.md", None, false), Err(ProxyError::BadRequest));
        assert_eq!(
            prepare("a.md", Some("x"), true),
            Err(ProxyError::BadRequest)
        );
        // The sidecar's path rule.
        for path in [
            "",
            "/a.md",
            "C:a.md",
            "c:/a.md",
            "a\\b.md",
            "a\0b.md",
            "a/../b.md",
            "..",
            &"p".repeat(1_001),
        ] {
            assert_eq!(
                prepare(path, None, true),
                Err(ProxyError::BadRequest),
                "{path:?}"
            );
        }
        for path in [
            "a.md",
            "policies/sub dir/a.md",
            "a..b.md",
            "1:a.md",
            &"p".repeat(1_000),
        ] {
            assert!(prepare(path, None, true).is_ok(), "{path:?}");
        }
    }

    /// A well-formed promise id (8-4-4-4-12 lowercase hex).
    const PROMISE: &str = "01234567-89ab-cdef-0123-456789abcdef";

    fn check_json(mfa: &str, phone: &str, statuses: &str) -> String {
        format!(
            r#"{{"route":"reality_check","file":"users.csv","claim":{{"path":"policies/mfa.md","start_line":3,"end_line":4,"quote":"MFA is required"}},"mfa_types":{mfa},"phone_types":{phone},"active_statuses":{statuses},"exported_at":"2026-10-01"}}"#
        )
    }

    fn check_body_of(json: &str) -> Result<serde_json::Value, ProxyError> {
        let prepared = RouteRequest::from_json(json)?.prepare()?;
        Ok(serde_json::from_slice(&prepared.body.unwrap()).unwrap())
    }

    /// The lists are trimmed and then sent: a padded item must arrive without its padding.
    #[test]
    fn the_reality_check_lists_are_sent_trimmed() {
        let body = check_body_of(&check_json(
            r#"["  OKTA_VERIFY\t"," FIDO "]"#,
            r#"["\nSMS "]"#,
            r#"[" Active ","\u3000Staged\u00a0"]"#,
        ))
        .unwrap();
        assert_eq!(
            body["mfa_types"],
            serde_json::json!(["OKTA_VERIFY", "FIDO"])
        );
        assert_eq!(body["phone_types"], serde_json::json!(["SMS"]));
        assert_eq!(
            body["active_statuses"],
            serde_json::json!(["Active", "Staged"])
        );
        assert_eq!(body["exported_at"], "2026-10-01");
        // An empty phone list is allowed (the sidecar's default), the other two need an item.
        assert!(check_body_of(&check_json(r#"["FIDO"]"#, "[]", r#"["Active"]"#)).is_ok());
    }

    #[test]
    fn the_reality_check_list_limits_are_refused_as_bad_request() {
        let item = |chars: usize| format!("\"{}\"", "x".repeat(chars));
        let list = |items: usize| {
            format!(
                "[{}]",
                std::iter::repeat_n("\"a\"", items)
                    .collect::<Vec<_>>()
                    .join(",")
            )
        };
        // 64 characters after trimming is the longest item; padding does not count.
        let padded = format!("\"{}{}\"", " ".repeat(500), "x".repeat(64));
        assert!(check_body_of(&check_json(&format!("[{}]", item(64)), "[]", r#"["A"]"#)).is_ok());
        assert!(check_body_of(&check_json(&format!("[{padded}]"), "[]", r#"["A"]"#)).is_ok());
        for bad in [
            check_json(&format!("[{}]", item(65)), "[]", r#"["A"]"#),
            check_json(&format!("[\" {} \"]", "x".repeat(65)), "[]", r#"["A"]"#),
            check_json(r#"["A"]"#, &format!("[{}]", item(65)), r#"["A"]"#),
            check_json(r#"["A"]"#, "[]", &format!("[{}]", item(65))),
            // 51 items in any one list
            check_json(&list(51), "[]", r#"["A"]"#),
            check_json(r#"["A"]"#, &list(51), r#"["A"]"#),
            check_json(r#"["A"]"#, "[]", &list(51)),
            // an item that is empty or only whitespace, and an empty required list
            check_json(r#"[""]"#, "[]", r#"["A"]"#),
            check_json(r#"["  \t "]"#, "[]", r#"["A"]"#),
            check_json(r#"["A"]"#, r#"[" "]"#, r#"["A"]"#),
            check_json("[]", "[]", r#"["A"]"#),
            check_json(r#"["A"]"#, "[]", "[]"),
        ] {
            assert_eq!(check_body_of(&bad), Err(ProxyError::BadRequest), "{bad}");
        }
        assert!(check_body_of(&check_json(&list(50), &list(50), &list(50))).is_ok());
        // The claim, the date and the file follow the sidecar's limits too.
        let claim = |path: &str, start: u32, end: u32, quote: &str| {
            format!(
                r#"{{"route":"reality_check","file":"u.csv","claim":{{"path":"{path}","start_line":{start},"end_line":{end},"quote":"{quote}"}},"mfa_types":["A"],"phone_types":[],"active_statuses":["A"],"exported_at":"2026-10-01"}}"#
            )
        };
        assert!(check_body_of(&claim("p.md", 1, 1, "q")).is_ok());
        for bad in [
            claim("", 1, 1, "q"),
            claim(&"p".repeat(513), 1, 1, "q"),
            claim("p.md", 0, 1, "q"),
            claim("p.md", 5, 4, "q"),
            claim("p.md", 1, 1, ""),
            claim("p.md", 1, 1, &"q".repeat(2_001)),
        ] {
            assert_eq!(check_body_of(&bad), Err(ProxyError::BadRequest), "{bad}");
        }
        for date in [
            "2026-02-30",
            "2026-13-01",
            "2026-1-01",
            "20261001",
            "2026-10-01T00:00",
        ] {
            let json = check_json(r#"["A"]"#, "[]", r#"["A"]"#).replace("2026-10-01", date);
            assert_eq!(check_body_of(&json), Err(ProxyError::BadRequest), "{date}");
        }
    }

    /// Every code point from 0 to 0x10FFFF, against what Python's `str.strip()` removes.
    ///
    /// Measured on 2026-10-19 with `sidecar/.venv/Scripts/python`, `chr(c).strip() == ""` over every
    /// code point: 29 characters, listed in `PYTHON_STRIPS`. Rust's `char::is_whitespace` and
    /// `str::trim` (Unicode `White_Space`) removed 25 of them. The four that differ are U+001C,
    /// U+001D, U+001E and U+001F (the information separators). pydantic's `strip_whitespace`, which
    /// the sidecar's `_TypeName` really uses, removed the same 25 as Rust (also measured).
    ///
    /// The rule: Rust may refuse more than the sidecar, never less. So for each of the 29 an item
    /// padded with it on both sides must be refused or sent as `x`, and for every other code point
    /// the item must be sent unchanged (never trimmed to something the sidecar would not see).
    #[test]
    fn the_trim_matches_python_on_every_code_point() {
        const PYTHON_STRIPS: [u32; 29] = [
            0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0, 0x1680, 0x2000,
            0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x2028,
            0x2029, 0x202F, 0x205F, 0x3000,
        ];
        let mut python_only = Vec::new();
        for code in 0..=0x10FFFF_u32 {
            let Some(c) = char::from_u32(code) else {
                continue; // a surrogate is not a Rust char and cannot be in a request
            };
            let python = PYTHON_STRIPS.contains(&code);
            assert_eq!(python_strips(c), python, "U+{code:04X}");
            if python && !c.is_whitespace() {
                python_only.push(code);
            }
            let padded = format!("{c}x{c}");
            let outcome = trim_type_item(&padded);
            if python {
                assert!(
                    outcome.is_none() || outcome.as_deref() == Some("x"),
                    "U+{code:04X} padded: {outcome:?}"
                );
                // Alone it is empty to Python: it must never be accepted.
                assert_eq!(trim_type_item(&c.to_string()), None, "U+{code:04X} alone");
            } else {
                assert_eq!(outcome.as_deref(), Some(padded.as_str()), "U+{code:04X}");
            }
        }
        // The disagreement, stated: exactly these four, and Rust refuses an item that edges on one.
        assert_eq!(python_only, [0x1C, 0x1D, 0x1E, 0x1F]);
        for code in [0x1C_u32, 0x1D, 0x1E, 0x1F] {
            let Some(c) = char::from_u32(code) else {
                continue;
            };
            assert_eq!(trim_type_item(&format!("{c}FIDO")), None, "U+{code:04X}");
            assert_eq!(trim_type_item(&format!("FIDO{c}")), None, "U+{code:04X}");
            assert_eq!(trim_type_item(&format!(" {c} FIDO")), None, "U+{code:04X}");
            // Inside the item it is just a character.
            assert_eq!(
                trim_type_item(&format!("F{c}O")).as_deref(),
                Some(format!("F{c}O").as_str())
            );
        }
    }

    /// The worst reality-check request (three full lists of control characters, a full quote and
    /// path) is over the 16 KiB every other route has and inside its own 80 KiB.
    #[test]
    fn the_worst_reality_check_request_fits_its_cap_and_not_the_default_one() {
        let item = serde_json::to_string(&"\u{1}".repeat(64)).unwrap();
        let list = format!("[{}]", vec![item; 50].join(","));
        let json = format!(
            r#"{{"route":"reality_check","file":"{}","claim":{{"path":"{}","start_line":1,"end_line":1,"quote":{}}},"mfa_types":{list},"phone_types":{list},"active_statuses":{list},"exported_at":"2026-10-01"}}"#,
            "f".repeat(260),
            "p".repeat(512),
            serde_json::to_string(&"\u{1}".repeat(2_000)).unwrap()
        );
        let length = RouteRequest::from_json(&json)
            .unwrap()
            .prepare()
            .unwrap()
            .body
            .unwrap()
            .len();
        assert!(length > MAX_REQUEST_BYTES, "{length}");
        assert!(length <= MAX_REALITY_CHECK_REQUEST_BYTES, "{length}");
    }

    #[test]
    fn the_import_and_run_arguments_follow_the_sidecars_limits() {
        let import = |extra: &str| {
            format!(
                r#"{{"route":"ledger_import","run_id":"{ID}","file":"d.xlsx","confirm_new":false,"sent_on":"2026-10-01","preview":true{extra}}}"#
            )
        };
        let body = RouteRequest::from_json(&import(""))
            .unwrap()
            .prepare()
            .unwrap();
        assert_eq!(body.path, "/ledger/import");
        assert_eq!(
            body.body.as_deref(),
            Some(
                format!(
                    r#"{{"run_id":"{ID}","file":"d.xlsx","confirm_new":false,"sent_on":"2026-10-01","preview":true}}"#
                )
                .as_bytes()
            )
        );
        let long_name = format!(r#","new_customer_name":"{}""#, "n".repeat(1_001));
        let max_name = format!(r#","new_customer_name":"{}""#, "n".repeat(1_000));
        assert!(RouteRequest::from_json(&import(&max_name))
            .unwrap()
            .prepare()
            .is_ok());
        for bad in [
            long_name,
            r#","new_customer_name":"""#.to_owned(),
            r#","existing_customer_id":"NOTHEX01""#.to_owned(),
            r#","url":"http://x""#.to_owned(),
        ] {
            assert_eq!(
                RouteRequest::from_json(&import(&bad)).and_then(|request| request.prepare()),
                Err(ProxyError::BadRequest),
                "{bad}"
            );
        }
        for date in [
            "2026-10-1",
            "2026/10/01",
            "2026-02-29",
            "",
            "0000-01-01",
            "2026-10-01\n",
        ] {
            let json = import("").replace("2026-10-01", date);
            assert_eq!(
                RouteRequest::from_json(&json).and_then(|request| request.prepare()),
                Err(ProxyError::BadRequest),
                "{date:?}"
            );
        }
        assert!(IsoDate::parse("2024-02-29").is_some());
        assert!(IsoDate::parse("2100-02-29").is_none());

        let start = |extra: &str| format!(r#"{{"route":"contradictions_start"{extra}}}"#);
        let empty = RouteRequest::from_json(&start(""))
            .unwrap()
            .prepare()
            .unwrap();
        assert_eq!(empty.body.as_deref(), Some(b"{}".as_slice()));
        let full = RouteRequest::from_json(&start(r#","scope":"policies/","cap":100"#))
            .unwrap()
            .prepare()
            .unwrap();
        assert_eq!(
            full.body.as_deref(),
            Some(br#"{"scope":"policies/","cap":100}"#.as_slice())
        );
        let long_scope = format!(r#","scope":"{}""#, "s".repeat(261));
        for bad in [
            r#","cap":0"#.to_owned(),
            r#","cap":101"#.to_owned(),
            r#","cap":256"#.to_owned(),
            r#","cap":-1"#.to_owned(),
            r#","scope":"""#.to_owned(),
            r#","scope":"a\\b""#.to_owned(),
            r#","scope":"C:x""#.to_owned(),
            r#","scope":"/x""#.to_owned(),
            r#","scope":"a/../b""#.to_owned(),
            long_scope,
        ] {
            assert_eq!(
                RouteRequest::from_json(&start(&bad)).and_then(|request| request.prepare()),
                Err(ProxyError::BadRequest),
                "{bad}"
            );
        }
        let recall = RouteRequest::from_json(r#"{"route":"ledger_recall"}"#)
            .unwrap()
            .prepare()
            .unwrap();
        assert_eq!(recall.body.as_deref(), Some(b"{}".as_slice()));
        let one = RouteRequest::from_json(&format!(
            r#"{{"route":"ledger_recall","customer_id":"{ID}"}}"#
        ))
        .unwrap()
        .prepare()
        .unwrap();
        assert_eq!(
            one.body.as_deref(),
            Some(format!(r#"{{"customer_id":"{ID}"}}"#).as_bytes())
        );
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

    /// Renamed in chunk 65d from `only_the_three_file_routes_carry_a_file_name_and_each_has_its_own_folder`:
    /// there are now six routes with a file name, in three folders.
    #[test]
    fn only_the_file_routes_carry_a_file_name_and_each_has_its_own_folder() {
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
        // A reviewed draft is imported from the export folder; an IT export is read from the
        // import folder, for both reality routes.
        assert_eq!(
            file_of(&format!(
                r#"{{"route":"ledger_import","run_id":"{ID}","file":"d.xlsx","confirm_new":false,"sent_on":"2026-10-01","preview":false}}"#
            )),
            Some((FileFolder::Exports, "d.xlsx".to_owned()))
        );
        assert_eq!(
            file_of(r#"{"route":"reality_inspect","file":"users.csv"}"#),
            Some((FileFolder::Imports, "users.csv".to_owned()))
        );
        assert_eq!(
            file_of(&check_json(r#"["A"]"#, "[]", r#"["A"]"#)),
            Some((FileFolder::Imports, "users.csv".to_owned()))
        );
        for json in [
            r#"{"route":"readiness"}"#.to_owned(),
            r#"{"route":"vault_index_start"}"#.to_owned(),
            format!(r#"{{"route":"questionnaire_status","run_id":"{ID}"}}"#),
            format!(r#"{{"route":"vault_index_status","job_id":"{ID}"}}"#),
            r#"{"route":"ledger_customers"}"#.to_owned(),
            r#"{"route":"ledger_promises","include_voided":false,"page":1,"page_size":1}"#
                .to_owned(),
            r#"{"route":"ledger_cited_paths"}"#.to_owned(),
            r#"{"route":"ledger_recall"}"#.to_owned(),
            format!(r#"{{"route":"ledger_void","promise_id":"{PROMISE}","reason":"other"}}"#),
            format!(
                r#"{{"route":"ledger_acknowledge","promise_id":"{PROMISE}","seen_recall_event_id":1}}"#
            ),
            r#"{"route":"ledger_blast_radius","path":"a.md","delete_file":true}"#.to_owned(),
            r#"{"route":"reality_claims"}"#.to_owned(),
            r#"{"route":"contradictions_start"}"#.to_owned(),
            format!(r#"{{"route":"contradictions_status","run_id":"{ID}"}}"#),
            r#"{"route":"vault_graph"}"#.to_owned(),
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
