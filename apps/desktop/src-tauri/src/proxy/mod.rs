//! The host proxy core (M6 chunk 6d, ADR-020 decisions 1 to 3).
//!
//! * [`route`]: the closed table of seven routes and the typed requests the window may send.
//! * [`error`]: [`ProxyError`] (unit variants) and [`ProxyReply`] (`{status, body}`).
//! * [`call`]: the function that sends one request and checks the answer.
//!
//! Not yet reachable from the window: there is no Tauri command and no capability entry. Chunk 6e
//! adds both, with the folder work.

pub mod call;
pub mod error;
pub mod route;

#[cfg(test)]
mod test_support;

pub use call::call;
pub use error::{ProxyError, ProxyReply};
pub use route::{Route, RouteRequest};
