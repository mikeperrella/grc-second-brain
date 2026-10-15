//! What the window is told when a proxied call does not return a sidecar answer (ADR-020 decision 3).
//!
//! Unit variants only, like `HealthError`: an error string can echo a path, a token or document
//! text, so errors are words from a fixed list. A sidecar reply (even a typed error body such as
//! `{"error":"run_in_progress"}`) is not a `ProxyError`: it passes as a [`ProxyReply`] when its
//! status is allowed. Serialized as `{"error":"not_ready"}` and so on.

use serde::Serialize;

/// Why the host could not give the window a sidecar answer. Unit variants only, so nothing here can
/// carry the token, a path or text from the sidecar.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, thiserror::Error)]
#[serde(tag = "error", rename_all = "snake_case")]
pub enum ProxyError {
    /// The sidecar is not running (starting, failed or stopped). No network call was made.
    #[error("the sidecar is not ready")]
    NotReady,
    /// The sidecar rejected the host's credentials (a 401). The body is never read.
    #[error("the sidecar rejected the host's credentials")]
    Unauthorized,
    /// The sidecar did not accept a connection.
    #[error("the sidecar did not accept a connection")]
    Unreachable,
    /// The sidecar did not answer within the route's timeout.
    #[error("the sidecar did not answer in time")]
    Timeout,
    /// A request or a response was over its cap.
    #[error("a request or response was over its size limit")]
    TooLarge,
    /// The window's arguments did not fit the route's typed request.
    #[error("the request did not fit the route's arguments")]
    BadRequest,
    /// The sidecar's answer was not in the allowed set: status, size or JSON.
    #[error("the sidecar's answer was not acceptable")]
    BadResponse,
    /// The host could not build its own request.
    #[error("the host could not build its request")]
    Internal,
}

/// A sidecar answer that passed the status, size and JSON checks. The window parses `body` with a
/// strict per-route schema; nothing else of the HTTP response is passed on.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ProxyReply {
    pub status: u16,
    pub body: serde_json::Value,
}

/// Every error, with no wildcard match: a new variant breaks compilation here, then the fixture
/// test. Used by the contract fixture and the tests.
#[cfg(test)]
pub(crate) fn every_error() -> Vec<ProxyError> {
    fn exhaustive(error: ProxyError) {
        match error {
            ProxyError::NotReady
            | ProxyError::Unauthorized
            | ProxyError::Unreachable
            | ProxyError::Timeout
            | ProxyError::TooLarge
            | ProxyError::BadRequest
            | ProxyError::BadResponse
            | ProxyError::Internal => {}
        }
    }
    exhaustive(ProxyError::Internal);
    vec![
        ProxyError::NotReady,
        ProxyError::Unauthorized,
        ProxyError::Unreachable,
        ProxyError::Timeout,
        ProxyError::TooLarge,
        ProxyError::BadRequest,
        ProxyError::BadResponse,
        ProxyError::Internal,
    ]
}
