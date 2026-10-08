# ADR-004: Host/sidecar lifecycle (spawn, Job Object, handshake, shutdown)

Status: Accepted

## Context

The Tauri host starts the Python sidecar, waits for it to announce its port, and must make sure the sidecar never outlives the host. Facts this decision rests on:

- ADR-002 fixes the contract: the host passes a 64-character lowercase-hex token in `GRC_AUTH_TOKEN`, the sidecar binds `127.0.0.1` on port 0 and prints exactly one line, `{"event":"ready","port":N}`, to stdout. On Windows that line ends in `\r\n`. ADR-002 also says orphan handling belongs to the host: a Job Object with kill-on-close.
- On Windows the venv interpreter `sidecar\.venv\Scripts\python.exe` is a launcher: it is the PARENT of the real interpreter, and the code runs in the child. A PyInstaller one-file sidecar (M8) has the same shape. So "the sidecar" is a process tree, and killing one PID is not enough.
- A listening port can linger after the process is killed on Windows, so process tests wait on real conditions, never on sleeps.
- Microsoft documents that completion-port messages for job events are not guaranteed to be delivered ("failure of a message to arrive does not necessarily mean that the event did not occur"). A design that trusts the message can hang or give a wrong answer.
- The sidecar's startup failure behaviour is not uniform: domain errors exit with code 2 and one fixed stderr line, but a missing `SYSTEMROOT` or home directory produces exit 1 with a traceback. The host therefore treats every exit before the ready line as a failure and reports exit code 2 distinctly.

## Decision

Spawning
- The host starts the sidecar with `std::process::Command`, explicit arguments, no shell, and not through the Tauri shell plugin (its capability surface is exactly the frontend-reachable process execution that least privilege avoids; Semgrep forbids it).
- The interpreter path is a COMPILE-TIME, debug-only constant (`CARGO_MANIFEST_DIR` plus a fixed relative path to `sidecar/.venv`), canonicalized and required to be a file inside the repository's `sidecar/` directory. There is no environment variable, `PATH` lookup or frontend input. Release builds compile no such code and return `NotConfigured` until M8 bundles the sidecar as an `externalBin`.
- The child environment is an allowlist of exactly three keys, built by `child_env.rs`: `SYSTEMROOT` (from `GetSystemWindowsDirectoryW`, not the parent's environment), `GRC_AUTH_TOKEN`, `GRC_LOG_DIR`. `env_clear()` is called first. A test has the fake sidecar report the variable NAMES it sees and asserts set equality.

Job Object
- The process is created suspended (`CREATE_SUSPENDED`), assigned to the job before any of its threads has run, then resumed. Assigning after a normal spawn is rejected: the launcher starts the real interpreter in its first milliseconds, and a child created before the assignment is not retroactively in the job. Putting the host itself in the job is rejected: `TerminateJobObject` would kill the host.
- std does not return the initial thread, so the host does not try to identify a "main" thread. It snapshots the process's threads, resumes EVERY one, and judges the previous suspend counts: each must be 0 or 1 (a security product may inject a thread that is already running) and at least one must be exactly 1 (the thread we created suspended). No thread found, a thread that cannot be opened or does not belong to the child, `u32::MAX`, a count of 2 or more, or "nobody was suspended" fail closed: the job is terminated and the start fails with `UnexpectedThreadState`. The rule is a pure function with unit tests, and the resume loop is generic over the resume call so a test proves every thread is resumed even when an earlier count is bad.
- The job has `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and nothing else. With neither breakaway flag set, no descendant can leave the job. A test queries the limit flags and asserts the literal value `0x2000` and no UI restrictions. Closing the last handle kills the whole tree, including when the host is force-killed.
- "The job is empty" is decided by `QueryInformationJobObject` reporting zero active processes. The completion port is only a way to wake up early; the query runs after every wake-up and every 100 ms slice inside a 5 s bound. No `sleep` is used. A test creates a job whose port is never associated, so no message can ever arrive, and still expects the wait to succeed.
- Termination always goes through the job (`TerminateJobObject`, then the bounded empty check). `Child::kill` is used in exactly one place: when assigning to the job fails, where the child is still suspended and outside any job.
- Every Win32 handle is wrapped in `OwnedHandle` immediately, so no handle is closed by hand and the types are `Send + Sync` without `unsafe impl`.
- The dev sidecar's job holds THREE processes, not two (observed 2026-10-08 by listing the job's processes while the real sidecar ran): the venv launcher (`python.exe`, the process the host started), the real interpreter (`python.exe`, parent = the launcher), and a `conhost.exe` (parent = the launcher). The console host is there because `CREATE_NO_WINDOW` creates a console application WITHOUT a window, but it still has a (hidden) console, and a console is hosted by `conhost.exe`; it is started under the launcher, so it is inside the job and is killed with it. That explanation is the standard Windows behaviour and fits what was observed; it was not verified further. `tests/real_sidecar.rs` therefore checks identity, not a count, through `describe_processes` (a `CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS)` helper in `job.rs`, inside the already approved `Win32_System_Diagnostics_ToolHelp` feature): the job must hold exactly the launcher, exactly one `python.exe` whose parent is the launcher, and at most one `conhost.exe` whose parent is the launcher. The console host is allowed but not required, so a runner that does not start one cannot make the test flaky; any other process, or one whose parent is not the launcher, fails. The rule is a pure function with its own tests and mutation proofs (`tests/sidecar_shape.rs`).
- `DETACHED_PROCESS` (no console at all, so no `conhost.exe`) was considered and NOT adopted, and was not tried: it would change the creation flags that the M2b tests and the Job Object ordering were proven with, and the venv launcher starts the real interpreter itself, so detaching the launcher risks a visible console window for the interpreter it starts. If that is ever revisited, it needs its own run of the fake-sidecar suite and the real-sidecar test.
- Nested jobs: Windows 8 and later allow a process to be in more than one job, so assignment works when the host already runs inside a job (terminal, IDE, CI runner). A test in its own process places itself in an outer job first and repeats the whole flow including the grandchild kill.

Handshake and state
- The handshake timeout is 30 s, a named constant. Tests that expect success or a specific failure use 15 s; only tests that expect a timeout use 2 s.
- Three threads (stdout reader, stderr drainer, process waiter) feed one channel to a handshake thread. The ready line is read with a size cap and parsed strictly (`ready.rs`); raw sidecar output never crosses the channel.
- Any stdout byte after the ready line fails closed: the job is killed and the state becomes `Failed(ProtocolViolation)`; the content is never logged.
- stderr is drained into a 4 KiB tail that is logged only at `debug`, never in an error value and never forwarded to the UI.
- On a failure the host terminates the job, waits for it to be empty, and only THEN records the failure, so whoever sees `Failed` knows the processes are gone.
- If stdout closes before the ready line, the supervisor keeps waiting (within the same deadline) for the exit event so the exit code can classify the failure (2 is `StartupRejected`). Stdout EOF and the exit event travel on different threads and can arrive in either order.
- State: `Starting`, `Ready{port}`, `Failed(FailureKind)`, `Stopped`. `Failed` and `Stopped` are terminal, `Ready` is only entered from `Starting`, nothing returns to `Starting`, and every write goes through one function that enforces this. This is what makes an orderly shutdown end in `Stopped` (killing the process also produces an exit event, which would otherwise be recorded as `ExitedAfterReady`) and what stops a late ready line from reviving a stopped sidecar.
- The state lives in a `Mutex`. A poisoned lock is handled in one place: the state becomes `Failed(Internal)` through the same rule, the job is terminated, and a typed error is returned. `unwrap`/`expect` are denied crate-wide.
- Shutdown is layered: explicit `shutdown()` (state `Stopped` first, then terminate and wait), `Drop` of the supervisor, and the OS closing the job handle at process exit.

Crate-wide rules
- `unsafe_code = "deny"` and `clippy::undocumented_unsafe_blocks = "deny"` apply to every target. Only `sidecar/job.rs` and `sidecar/spawn.rs` carry a module-level `allow`, each `unsafe` block has a `// SAFETY:` comment, and integration tests contain no `unsafe` (the handle and process helpers they need are safe functions in `job.rs`).
- `unwrap_used`, `expect_used` and `dbg_macro` are denied crate-wide; tests are allowed through `clippy.toml`.
- The sidecar module refuses to compile (`compile_error!`) on non-Windows targets and on 32-bit Windows.
- Review rule (not enforceable by Semgrep, which has no type information): never Debug-format a `Command` or any struct that holds one; `{:#?}` prints the environment, including the token. The structural protections are that no struct in the crate stores a `Command` (it is built and spawned inside one function) and that `SidecarSpec` implements `Debug` by hand and prints only environment key names. Semgrep rules `grc-rs-dbg-macro` and clippy's `dbg_macro` cover the macro that would do it most easily; `grc-rs-command-without-env-clear` requires an `env_clear()` call in the same block as every `Command::new`, or in a block that encloses it.
- Semgrep's registry rule `unsafe-usage` is suppressed per site, never by path. Each of the 22 `unsafe` sites carries a same-line `// nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- <the Win32 call>; SAFETY above, ADR-004`, so any `unsafe` added later, in these modules or elsewhere, is still flagged until someone reviews it and adds its own comment. The same applies to the two registry findings in the test-only `fake_sidecar` (`current_exe`, `args`), whose comments say it is never shipped.

Fake sidecar and `default-run`
- A second binary, `fake_sidecar`, lives in the same crate so tests can start a process that speaks the protocol and misbehaves on request (silent, garbage, exit codes, oversized line, output after ready, a grandchild process, environment report). `default-run = "desktop"` makes `cargo run` and the Tauri CLI start the app; `tests/default_run.rs` asks Cargo for the package metadata and fails if the line is removed.

## Consequences

Positive:

- The sidecar and everything it starts die with the host, however the host ends. This is tested for shutdown, for dropping the job handle without terminating it, and when the host is already inside another job.
- Every way the start can go wrong ends in a typed, secret-free failure and a verified-empty job; there is no code path that leaves a process running.
- The `unsafe` surface is two small modules with a written safety argument per block, and the rest of the host cannot add more without changing a lint.

Negative or risky:

- A dev checkout needs `sidecar/.venv` before the host can start the real sidecar. A release build cannot start any sidecar until M8 adds the `externalBin` and its resolver.
- The Job Object design needs Windows 8 or later (nested jobs) and a 64-bit Windows target. There is no non-Windows build.
- The extra `fake_sidecar` binary must never ship. The M8 checklist (CLAUDE.md M8 row) requires verifying that the installer contains only the app binary and the sidecar `externalBin`.
- On Windows, `canonicalize` returns a `\\?\` extended-length path. **Resolved in M2c (2026-10-08): the venv launcher starts correctly from it.** The first real spawn (`tests/real_sidecar.rs`) reached `Ready` from `\\?\C:\dev\grc-second-brain\sidecar\.venv\Scripts\python.exe`; a process listing taken while it ran showed that exact command line (`"\\?\C:\...\python.exe" -I -m grc_sidecar`) for the launcher. The same test then got a healthy `/health` through the host's client, a `401` with an empty body without the header, an orderly stop and a closed port, in three consecutive runs. No prefix stripping is implemented or needed.
- The thread-snapshot step trusts `Thread32First`/`Thread32Next` to enumerate the child's threads. A truncated enumeration is not distinguishable from the end of the list, but it can only make the start fail closed (an empty list is rejected), never run unconfined.
- The `reqwest` dependency for the health command arrives in M2c and has its own cost.

Revisit if: a non-Windows build is wanted; M8 packaging changes how the sidecar is started; any command takes a path from the frontend; or the sidecar starts its own long-lived children that must survive it.

## Known gaps: controls without a mutation proof

These controls are implemented, and the tests exercise most of them, but no test turns red when the control is removed. A green run therefore says nothing about them. They are listed so a reviewer does not mistake their absence from the mutation table for coverage. This is the list of security-relevant controls known to be unproven; it is not a claim that every other behaviour in this ADR has a mutation proof.

1. **The hand-written `Debug` for `SidecarSpec`** (program, arguments, working directory and environment KEY names only).
   Replacing it with `#[derive(Debug)]` leaves `the_spec_debug_output_shows_key_names_but_no_values` green. The only field that holds a secret is the environment, and `ChildEnv`'s own `Debug` (from M2a) already prints key names only, so the derived and the hand-written output are indistinguishable today. The impl is a guard against a future field that holds a secret and would print it. Until such a field exists there is nothing for a test to assert, and the review rule above carries the control.

2. **"`Failed` is recorded only after the job is empty"** (the order inside `fail()`: terminate the job, wait until it is empty, then record the state).
   Swapping the order opens a window of milliseconds, at most the 5 s bound, between the state change and the processes exiting. A test that checks "the job is empty once the state is `Failed`" would pass or fail depending on a race, and it usually passes because the kill completes within milliseconds, so it cannot go red reliably. A deterministic proof needs a seam between the termination and the state write (an injectable job, or a test hook), which production code does not have. Adding one only for a test was judged worse than documenting the gap.

3. **The thread-owner check in `resume_one`** (`GetProcessIdOfThread` must equal the child's process id before `ResumeThread`, so a recycled thread id cannot make the host resume another process's thread).
   The check only matters when a thread id from the snapshot is reused by another process in the microseconds before `OpenThread`. That cannot be produced on demand, and `resume_one` calls the operating system directly, with no seam to inject a fake thread. Every process test runs the check and it always passes, which exercises the code but proves nothing. It can only be reviewed against the Win32 documentation.

4. **The start-failure cleanup paths** (job assignment fails: `Child::kill` then wait; resuming breaks the rule: terminate the job and wait; a supervision thread cannot be spawned: `abort_start`). This item goes beyond the three requested at review and is added because it is the same kind of gap.
   Each needs `AssignProcessToJobObject`, `ResumeThread` or thread creation to fail on demand, and none has a seam. Their success paths are covered by every process test.

5. **The stderr handling** (the pipe is drained to the end so the sidecar can never block on a full pipe, and the last 4 KiB is logged only at `debug`, never above it, never in an error, never forwarded). Like item 4, this goes beyond the three requested at review.
   No test removes the drain: the fake writes at most one short line, far below the pipe buffer, so a missing drain would not block. No test captures log output either, because the crate installs no logger in M2, so "debug only" is not asserted. It holds by code review of one function (`stderr_thread`) until a log sink exists to assert against.

Each gap would close if the code gained a narrow test seam, or if a later milestone needs one for another reason (M2c's real-sidecar test and any log sink are the likely first ones). The pull request description repeats this list.
