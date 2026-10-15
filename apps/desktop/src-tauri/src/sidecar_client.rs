//! The one HTTP client the host uses to talk to the sidecar (CLAUDE.md 3.2 and 3.4, ADR-020
//! decision 3). `health.rs` (the `/health` probe) and `proxy/` (the window's named operations) both
//! use it, so the rules below are written once:
//!
//! * [`LoopbackEndpoint`] is the only way to name an address. It holds a port and nothing else, so no
//!   caller can point the client at another host. The path is a separate argument that only the
//!   route table and the health probe supply.
//! * The client has no proxy (`.no_proxy()`), follows no redirects, and has a connect timeout. The
//!   request timeout is set per request, so a fast status poll and a slow workbook read can share it.
//! * The token is put in a header marked sensitive and is never part of an error or a `Debug` print.
//! * Transport errors are reduced to [`Transport`], three unit variants, because a reqwest error can
//!   carry the URL.

use std::fmt;
use std::num::NonZeroU16;
use std::time::Duration;

use reqwest::header::{HeaderName, HeaderValue};
use reqwest::redirect::Policy;

use crate::token::AuthToken;

/// The header the sidecar's auth middleware checks (CLAUDE.md 3.4). `from_static` requires lowercase;
/// HTTP header names are case-insensitive.
pub(crate) const AUTH_HEADER: HeaderName = HeaderName::from_static("x-grc-auth");

pub(crate) const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
/// The timeout `HealthClient::new` uses. Routes in `proxy/` name their own.
pub(crate) const DEFAULT_TIMEOUT: Duration = Duration::from_secs(5);

/// The only address the host ever contacts: `http://127.0.0.1:{port}`. There is deliberately no
/// constructor that takes a host or a string.
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

    /// The full URL for `path`, which must start with `/`. Callers pass only paths built from the
    /// route table (a fixed template plus a validated hex id) or the fixed `/health`.
    pub(crate) fn url(&self, path: &str) -> String {
        format!("http://127.0.0.1:{}{path}", self.0)
    }
}

impl fmt::Display for LoopbackEndpoint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "http://127.0.0.1:{}/health", self.0)
    }
}

/// The client could not be built. A unit variant: no detail that could carry a secret.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum ClientBuildError {
    #[error("the host could not build its sidecar client")]
    Internal,
}

/// The host's HTTP client for the sidecar. Build one when the sidecar is ready and reuse it.
pub struct SidecarClient {
    client: reqwest::Client,
    pub(crate) auth: HeaderValue,
    default_timeout: Duration,
}

impl SidecarClient {
    pub fn new(token: &AuthToken) -> Result<Self, ClientBuildError> {
        Self::build(token, CONNECT_TIMEOUT, DEFAULT_TIMEOUT)
    }

    /// Separate from `new` so tests can use short timeouts. Every client in the crate is built
    /// here (the Semgrep rule `grc-rs-reqwest-without-no-proxy` checks the chain).
    pub(crate) fn build(
        token: &AuthToken,
        connect_timeout: Duration,
        default_timeout: Duration,
    ) -> Result<Self, ClientBuildError> {
        // The second of the token's two intended readers (the first is the child environment).
        let mut auth =
            HeaderValue::from_str(token.expose()).map_err(|_| ClientBuildError::Internal)?;
        auth.set_sensitive(true);
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(Policy::none())
            .connect_timeout(connect_timeout)
            .build()
            .map_err(|_| ClientBuildError::Internal)?;
        Ok(Self {
            client,
            auth,
            default_timeout,
        })
    }

    /// A request with the token header and the client's default timeout (the health probe).
    pub(crate) fn get(&self, url: String) -> reqwest::RequestBuilder {
        self.request(reqwest::Method::GET, url, self.default_timeout)
    }

    /// A request with the token header and its own timeout (a proxied route).
    pub(crate) fn request(
        &self,
        method: reqwest::Method,
        url: String,
        timeout: Duration,
    ) -> reqwest::RequestBuilder {
        self.client
            .request(method, url)
            .header(AUTH_HEADER, self.auth.clone())
            .timeout(timeout)
    }
}

impl fmt::Debug for SidecarClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // No field is printed: one holds the token.
        f.debug_struct("SidecarClient").finish_non_exhaustive()
    }
}

/// How a transport error is told to callers, without the error itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Transport {
    Unreachable,
    Timeout,
    Other,
}

pub(crate) fn classify(error: &reqwest::Error) -> Transport {
    // `is_connect` first: a connect that times out is still "could not connect". On Windows a
    // refused connection takes about 2 s to be reported, which is our connect timeout, so checking
    // `is_timeout` first would call a closed port a timeout.
    if error.is_connect() {
        Transport::Unreachable
    } else if error.is_timeout() {
        Transport::Timeout
    } else {
        Transport::Other
    }
}

/// Why a body could not be read within its cap.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ReadError {
    TooLarge,
    Transport(Transport),
}

/// Read a response body of at most `cap` bytes. A declared length over the cap is refused up front;
/// the loop enforces the cap for responses with no length (chunked) or a wrong one.
pub(crate) async fn read_capped(
    response: &mut reqwest::Response,
    cap: usize,
) -> Result<Vec<u8>, ReadError> {
    if response
        .content_length()
        .is_some_and(|length| length > cap as u64)
    {
        return Err(ReadError::TooLarge);
    }
    let mut body = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|error| ReadError::Transport(classify(&error)))?
    {
        if body.len() + chunk.len() > cap {
            return Err(ReadError::TooLarge);
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body)
}
