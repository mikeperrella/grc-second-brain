# ADR-006: Window status polling and the IPC contract

Status: Accepted (M2d). Evidence: Vitest and Rust tests with mutation proofs, and the manual check of 2026-10-08, both recorded below.

## Context

The window needs to show whether the sidecar is up. The host answers one question, `sidecar_health` ([ADR-005](005-webview-csp-and-capabilities.md)), and replies with a `HealthReply` (or fails with a `HealthError`) that Rust serializes as JSON. CLAUDE.md Section 7 requires every payload that crosses the Tauri IPC boundary to be parsed with Zod before use. Four things had to be decided: how the window reads a result that can arrive on two channels, how the Rust and TypeScript sides are kept from drifting apart, how often to ask, and how to stop the CI from passing with no tests.

## Decision

1. **The channel decides, never the content.** `invoke()` resolves with a reply or rejects with an error. A value that does not parse on the resolve channel is `invalid_response`; a value that does not parse on the reject channel (including the plain string Tauri returns for a command the capability does not allow) is `rejected`. Content is never used to guess a channel: a `{"status":"healthy"}` that arrives on the reject channel is `rejected`, never `healthy`, so a refused or hostile rejection cannot be mistaken for a good answer. The Zod error and the rejected value are dropped, because they can echo input.
2. **Strict schemas, and a view with no text.** Every object is a Zod `strictObject` inside a discriminated union, so an unknown key (a token, a `detail` string) makes the whole payload invalid instead of being carried along. What the window renders is a `HealthView` built only from fixed codes plus a `source` that tells apart failures sharing a code (the supervisor's handshake `timeout` and a health request `timeout`). The exit code of an early exit is parsed but never shown. `parseHealthResult`, `parseHealthRejection` and `fetchHealth` never throw, including for a value whose property getters throw.
3. **A committed fixture both sides test.** `apps/desktop/src/ipc/health-contract.json` holds the JSON Rust serializes for every variant (all 24 failure kinds, `exited_before_ready` with `code` 3, `null`, `i32::MIN` and `i32::MAX`, and every error). A Rust test compares it with the live serialization as parsed JSON (so line endings cannot matter), built through exhaustive matches with no wildcard, so a new Rust variant fails to compile until it is listed. The Vitest suite parses every entry and checks that the names equal the names read from the schema itself, in both directions. The Rust test writes the file only when `GRC_WRITE_HEALTH_CONTRACT=1` is set, and then still fails: an unconditional write would make drift invisible, because nothing would be left to compare.
4. **Poll every 500 ms while starting and every 5 s afterwards, including after healthy.** If polling stopped at "healthy", the window would keep saying "healthy" after the sidecar died, because the host's supervisor records the death at once but the window only learns it by asking. A 5 s request to loopback costs nothing measurable, so a stale "healthy" lasts at most one interval plus the probe's own time (the request timeout is 5 s). A failed health check (the sidecar is believed running but a probe failed) is retried at 5 s; every other failure is terminal and stops polling, because the host has no restart path in M2 and an unreadable reply will not fix itself. Probes never overlap: the next timer is set only after the previous probe finishes. The alternative, a Tauri event pushed from the host, would be instant but needs a core event permission and so would widen the capability; revisit at M6 if the UI needs instant updates.
5. **CI cannot be green with no tests.** `pnpm test` is plain `vitest run` (it fails when there are no test files). `scripts/check-test-count.mjs` fails the `frontend` job if the Vitest report is missing or malformed or if no test passed or failed (so an all-skipped suite fails too), after `scripts/check-test-count.selftest.mjs` has shown the guard can fail. Node globals are given only to `scripts/**/*.mjs`; `scripts/check-eslint-scope.mjs`, the last step of `pnpm lint`, fails if `src/` gets them, because typescript-eslint turns `no-undef` off for TypeScript and a leaked global would otherwise not be noticed.

## Consequences

- Adding a variant on the Rust side breaks the Rust build, then the Rust fixture test, then the Vitest suite, in that order, until the enum, the fixture and the schema agree.
- A command the capability does not allow shows as `Sidecar: failed (ipc: rejected)` and stops polling.
- The window makes one TCP connection per health poll (the manual check saw about one `TIME_WAIT` connection per poll, owned by PID 0, so connections are not reused). Harmless at 5 s; worth knowing if the poll rate is ever raised.
- Zod 4.6.5 (MIT, no dependencies, no install scripts) is the only new runtime dependency; there is no jsdom or Testing Library.

Revisit if: the window needs instant updates (M6, then use a host event and add its permission on its own); a command gets a payload that is not a closed enum; the host gains a restart path (then a terminal failure is no longer terminal); or a component test is wanted (then add jsdom or Testing Library with their own review).

## Known gaps

These are the controls and checks this chunk does NOT prove. The PR description repeats them.

- **`App.tsx` is not rendered in any test.** No jsdom, by choice (one dependency less). Only its pure logic is tested: `health.ts`, `healthPoller.ts`, `statusText.ts` and `requireRoot.ts`. That the `useEffect` starts the polling, that its cleanup stops it, and that the JSX shows `describeView`'s text is checked only by hand (the manual check below).
- **The mutated-capability window line was not recorded.** With `allow-sidecar-health` removed, the console showed the rejection string, but the status line in the window was not written down. The expected line (`Sidecar: failed (ipc: rejected)`) comes from unit tests, not from observation.
- **`main.tsx`'s use of `requireRoot` is not tested**, only the function.
- **Not every timing test has a mutation proof.** Proven: `healthy` stops polling (2 tests red), the reject-channel fallback (4 red), strict-object loosening on replies, reasons, the early-exit reason and errors, a schema missing a kind, the Rust serde rename, the zero-test guard and the Node-globals scope. Not mutation-proven: the no-overlap test, the cancel and cancel-while-in-flight tests, the terminal-stop test and `statusText`.
- **The new CI steps had not run on GitHub when this was written** (the guard, its self-test and the scope check were run locally). The first pull request run is their first CI evidence.
- **The Cursor terminal may or may not be inside a Windows job.** The manual check launches from both a Cursor terminal and a plain PowerShell window, but cannot tell which is nested. Nesting is covered by `tests/nested_job.rs`, not by this check.
- **The fixture proves serialization, not meaning.** It cannot tell if Rust and the window agree on what a failure kind means; that is covered by the supervisor tests and the real-sidecar test.

## Evidence: manual check (2026-10-08, branch `feat/m2d-frontend-status`, `725f0ea`)

Run by Mike; steps are in [dev-setup.md](../dev-setup.md#manual-check-of-the-running-app-m2d). The capability and CSP evidence is in ADR-005.

| Step | Observed |
|---|---|
| A, `pnpm tauri dev` | Window `Sidecar: healthy`. `desktop` (34332) running, no `fake_sidecar`. After closing with the X, no `desktop` and no `grc_sidecar` process; `pnpm` ended with the usual `ELIFECYCLE`. |
| B, mode B (fresh build) | `Sidecar: healthy`; probes as in ADR-005. |
| C, processes (launched from a Cursor terminal) | `desktop.exe` 16344 (parent 2032); launcher `\\?\C:\dev\grc-second-brain\sidecar\.venv\Scripts\python.exe -I -m grc_sidecar` 20688 (parent 16344); `conhost.exe` 23444 (parent 20688); interpreter `...\Python311\python.exe -I -m grc_sidecar` 2700 (parent 20688). No `cmd.exe` seen. `netstat -ano -p TCP` listening: one line, `127.0.0.1:52603`, PID 2700. `netstat -ano -p TCPv6` listening for those PIDs: nothing. |
| D, `Stop-Process -Name desktop -Force` | Launcher, `conhost`, interpreter and `desktop` all gone; no `LISTENING` line for 52603. Sixteen `TIME_WAIT` lines owned by PID 0 (closed health connections from 127.0.0.1:52603 to ephemeral client ports). |
| Normal close (X) | `desktop` 31980, launcher 10308, `conhost` 35424, interpreter 34680, listener `127.0.0.1:63928` (PID 34680). After closing: no processes, no listener. |
| E, launched from a plain PowerShell outside Cursor | `desktop` 32500 (parent 31932, not Cursor's 2032), launcher 27024, `conhost` 30788, interpreter 18480, listener `127.0.0.1:50996` (PID 18480). After force-close: no processes, no listener. |
| Capability mutation and restore | See ADR-005. |

Findings from the run:

- The venv launcher starts from a `\\?\` extended-length path (the canonical path Windows gives), and the real sidecar still started and answered `/health` through the host. That question had been open since M2b.
- `Get-NetTCPConnection -State Listen` reported "No MSFT_NetTCPConnection objects found with property 'State' equal to 'Listen'" for the whole system on this machine, so it cannot be used to prove a port is closed (it would say so for an open port too). The manual check uses `netstat -ano`.
- Rebuilding while `desktop.exe` is running fails with "Access is denied"; close the app first.
