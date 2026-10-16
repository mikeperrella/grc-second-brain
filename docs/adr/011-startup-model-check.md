# ADR-011: Startup model check and degraded mode

Status: Accepted (M4a). Amends CLAUDE.md 3.7 and supersedes the "Deviation from CLAUDE.md 3.7" paragraph of ADR-009.

## Context

CLAUDE.md 3.7 said the sidecar calls Ollama `GET /api/tags` at startup and "fails closed with a clear error if a required model is missing". M3 provided the check inside the indexer (before every indexing run) but did not wire it into startup, because exiting when Ollama is down would change the M2 host flows: the host would see a failed sidecar and show an error, although the user only needs to start Ollama or install a model. M4 retrieval makes the question concrete: lexical search over the index and framework browsing work without any model, and only embedding a query and (from M5) generating an answer need one.

## Decision

1. **The sidecar always starts.** At startup it lists the installed models (`GET /api/tags`, through the same restricted client as every other Ollama call) and records which of the required models are present. A missing model or an unreachable Ollama is a recorded state, not an exit. The new CLAUDE.md 3.7 wording is in that file, in `.cursor/rules/grc-security.mdc` (the policy-drift check compares them line by line) and as a CodeRabbit instruction for `sidecar/src/**/llm/**`.
2. **A typed degraded state on a new authenticated endpoint, `GET /readiness`.** Body: `{"state": "checking"|"ready"|"degraded", "models_missing": [names], "ollama": "unknown"|"reachable"|"unreachable"|"bad_response"}`. It holds configured model names and fixed words, never anything Ollama said.
3. **Every model-dependent operation fails closed.** It calls `ModelGate.require(model)` first, which raises the typed `ModelsUnavailableError` (HTTP 503 `models_unavailable`, mapped in one place, `api/errors.py`) before any request to that model is made. The gate is per model: a missing generation model does not block embedding, which is what M4 retrieval needs. The app never calls `/api/pull`: the client's request hook (ADR-009, M3c) still refuses every endpoint but `GET /api/tags` and `POST /api/embed`, and `installed_models()` adds no endpoint.
4. **Required models:** the embedding model (`nomic-embed-text`) and the generation model (`Settings.generation_model`, default `llama3.1:8b`, from CLAUDE.md Section 4). Nothing generates text in M4; the generation model is listed now so the report covers everything the app needs.

## Why `/readiness` and not a field on `/health`

`/health` is the host's liveness contract, ADR-006: Rust accepts only a JSON object with exactly one key, `status`, equal to `"ok"` (`health.rs`), the window parses it with Zod, and `test_health.py` pins the bytes `{"status":"ok"}`. Adding `models_missing` would make the host report a bad response, and it would also conflate "the process is alive" with "a model is installed". A degraded model state is not an unhealthy sidecar. `/health` is unchanged and a test proves it is byte-identical and never contacts Ollama.

## Why a background check, with a short timeout, an interval and a lock

- **Background.** The host gives the sidecar 30 seconds to print the ready line (CLAUDE.md 3.4), and uvicorn prints it after startup. A check awaited during startup could spend that time on an Ollama that hangs. The check is a task started by the app's lifespan; the ready line never waits for it. `test_startup.py::test_ready_line_does_not_wait_for_stuck_ollama` runs a real sidecar against a listener that accepts and never answers and shows the connection is still open and unanswered when the ready line has been read.
- **Short timeout.** The listing has its own explicit timeout (`TAGS_CHECK_TIMEOUT`: 2 s connect, 3 s read), not the 30 s embed default, so an unresponsive Ollama shows up as `unreachable` in seconds.
- **Re-check on demand, at most once per interval.** A snapshot taken at startup goes out of date when the user installs a model. `require` and `/readiness` re-check, but no more than once per `RECHECK_MIN_INTERVAL` (5 s) measured with the injected `Clock`, so a caller that retries in a loop cannot hammer Ollama. `Clock` gained `monotonic()`; `test_hygiene_clock.py` now also confines `time.monotonic()` to `vault/clock.py`, so interval logic is tested by advancing a fake clock, not by sleeping.
- **One lock.** The background check and every on-demand check share an `asyncio.Lock`, so they never overlap, and a caller that waited on the lock reuses the result of the check it waited for instead of sending another request.

## Consequences

Positive: the app opens even when Ollama is not running; the UI can say exactly which model to install; retrieval can fall back to lexical search with an explicit mode (M4e) instead of failing; the refusal happens before a request to a missing model is sent.

Negative or risky:

- The recorded state is a snapshot. `/api/tags` listing a model is not proof the model loads or has enough memory; a failing embed call is still handled by the existing typed errors.
- `/readiness` makes an outbound request to loopback Ollama when asked (at most once per interval). It is authenticated like every route.
- `models_missing` for `unreachable` and `bad_response` lists every required model, because nothing could be confirmed.
- A new input to the host: the host does not read `/readiness` yet (no UI consumer until M6), so the degraded state is visible to tests and to `curl`-style checks only.
- **Known limit (M4b): `EnvRejected` is reused.** When the host cannot prepare the index folder for the sidecar it reports the existing `FailureKind::EnvRejected` ("the child environment could not be built"), because a new failure kind would change the IPC contract with the window (`health.ts` Zod schema and `health-contract.json`). The window therefore cannot tell "bad path" from "could not create the index folder". M6, which touches that contract for the folder picker anyway, adds a dedicated failure kind.
- The startup-not-blocked test measures a real child process against a connection that stays open for the 3 s read timeout, so it has a margin of about 3 s on a very slow machine.

## Mutation proofs (M4a)

Procedure (as in ADR-009): back up the file outside the repo and record its sha256, apply the one named break with the edit tool, run the test by its node ID, require `1 failed`, restore, show the sha256 equals the original, and show the test passes again. Originals: `model_gate.py` = `dcb71f230c9ec1220afc4773b54c461128bcb87fdc31caa715885f6a7bd7fb40`, `ollama_embeddings.py` = `928d07b5de873464881f06c93827578a030963dacd4844868fbbadec1f26354f`. Every restore matched.

| # | Control | Break | Test that failed | Result |
|---|---|---|---|---|
| 14 | A degraded gate refuses | `require()` returns at once (`if True: return`) | `test_model_gate.py::test_model_dependent_call_refuses_when_degraded` | 1 failed (`DID NOT RAISE ModelsUnavailableError`) |
| 15 | Re-check on demand | the check call in `require()` replaced with `pass` | `test_model_gate.py::test_gate_recovers_after_model_appears` | 1 failed (`ModelsUnavailableError`: the model that had appeared was never seen) |
| 25 | Minimum re-check interval | `_may_check()` always returns `True` | `test_model_gate.py::test_recheck_respects_the_minimum_interval` | 1 failed (`assert 4 == 1`: four requests inside the interval) |
| 26 | One check at a time | the `asyncio.Lock` replaced with a no-op context | `test_model_gate.py::test_background_and_on_demand_checks_never_overlap` | 1 failed (`assert 3 == 1`: three requests in flight at once) |
| 27 | Short timeout for the listing | the listing passes the client's 30 s default instead of `TAGS_CHECK_TIMEOUT` | `test_model_gate.py::test_the_tags_check_uses_the_short_timeout_and_reports_unreachable` | 1 failed (the request carried `read: 30.0`, expected `3.0`) |

Regression check (row 22, not a mutation proof: nothing is broken): `test_embeddings_ollama.py::test_the_client_refuses_every_endpoint_but_tags_and_embed` still passes after `installed_models()` was added, so the endpoint allowlist is unchanged.

Notes and known gaps:

- Row 14's test uses a stand-in for "the model call" (a flag set after `require`), and also asserts that no request other than `GET /api/tags` reached the fake Ollama. Real model-dependent operations (the embedding of a query) are wired to the gate in M4e, which adds its own refusal test.
- Rows 15, 25 and 26 break different lines of the same method (`require`), and each failed on its own test. Row 25 would also break the lock test's "one request in total" assertion; that test is named for the lock and is mutation-checked separately (row 26).
- The path check required by CLAUDE.md 3.6 on the Rust side is not part of this ADR (no path-taking command exists); it stays with the folder-picker chunk (ADR-009).

## Amended (built, ADR-020), written 2026-10-15, marked built 2026-10-16

Built in M6. Two known limits in the Consequences changed. (1) "Known limit (M4b): `EnvRejected` is reused" was closed in chunk 6e (PR #42, `caeaa31`) by a dedicated failure kind, `index_dir_unavailable`, which changed the Rust enum, `health-contract.json`, `health.ts` and its test together (24 kinds became 25; G8). No test makes the host's index-folder creation fail, so the kind's trigger is untested. (2) "the host does not read `/readiness` yet (no UI consumer until M6)" stopped being true when the host proxy (chunk 6d, PR #41, `251cda5`) and the status bar (chunk 6f, PR #43, `d0cdffe`) read it: the `readiness` route is a GET with a 5 s timeout and a **16 KiB** response cap (the planned 1 KiB was replaced by an owner decision at 6d), polled every 5 s while the window believes health is healthy; that last gate is in a hook that no test runs (G11, threat model F20-3). `/health` and `/readiness` stay separate. See [ADR-020](020-host-proxy-folders-polling.md).
