//! Starting, supervising and stopping the Python sidecar (M2 plan sections 3, 5 and 6, ADR-004).
//!
//! * [`resolve`]: which interpreter to start (a compile-time, dev-only path; release is blocked
//!   until M8).
//! * [`job`]: the Windows Job Object that guarantees the sidecar and everything it starts die with
//!   the host. One of only two modules allowed to use `unsafe`.
//! * [`spawn`]: starts the process suspended, puts it in the job, then resumes it. The other module
//!   allowed to use `unsafe`.
//! * [`supervisor`]: the handshake, the state machine and shutdown. No `unsafe`, no Tauri types.
//!
//! The modules are `pub` because integration tests in `tests/` use them. Inside them, an item is
//! `pub` only if such a test (or the signature of such an item) needs it; everything else is
//! `pub(crate)`.

#[cfg(not(windows))]
compile_error!(
    "the sidecar host supports Windows only: the Job Object guarantee has no equivalent here yet \
     (ADR-004, revisit trigger: a non-Windows build)"
);

// The process id list query in `job` reads the counts and ids out of a word buffer and relies on
// 8-byte words. Every Windows target Tauri supports (x86_64, aarch64) is 64-bit.
#[cfg(all(windows, not(target_pointer_width = "64")))]
compile_error!("the sidecar host supports 64-bit Windows only");

pub mod job;
pub mod resolve;
pub mod spawn;
pub mod supervisor;
