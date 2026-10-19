//! The host proxy core (M6 chunk 6d, ADR-020 decisions 1 to 3).
//!
//! * [`route`]: the closed table of routes (21 after M6.5 chunk 65d) and the typed requests the
//!   window may send.
//! * [`error`]: [`ProxyError`] (unit variants) and [`ProxyReply`] (`{status, body}`).
//! * [`call`]: the function that sends one request and checks the answer.
//!
//! Reachable from the window through the one `sidecar_call` command (chunk 6e), which hands the
//! typed request to `host::SidecarHost::call`; that checks a file name in the request before
//! anything is sent.

pub mod call;
pub mod error;
pub mod route;

#[cfg(test)]
mod test_support;

pub use call::call;
pub use error::{ProxyError, ProxyReply};
pub use route::{Route, RouteRequest};
