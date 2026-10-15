# M6 plan (revision 2): ALIBI UI core, host proxy, folders

Status: approved by the owner, then amended (amendments 1 to 8, below). Untracked until chunk 6a1 commits it. No code, installs, commits or Ollama calls were made while writing it.

Revision 2 applies the owner and reviewer decisions: M6 is cut into **M6 core (6a1, 6a2, 6b to 6i)** and **M6.5**; no sidecar restart; progress by polling; no skills, no squash exception, no SSE relay.

Amendments applied after approval: (1) chunk 6a split into 6a1 and 6a2; (2) the review queue needs the ledger folder and a run record, with a per-field source table (Check 4); (3) the ledger folder is set before the first questionnaire run, with warnings; (4) `vault_index` becomes `vault_index_start` and `vault_index_status`; (5) release builds cannot start the sidecar until M8, so every M6 screen is verified in a debug build only; (6) the mid-run pin test asserts the exact `completed` value after each answer; (7) the 6b cloud prompt waits for 6a1 and 6a2 to merge, and the model setting is checked by the owner; (8) new deviation candidates, open questions and unverified items.

Deviations for this work start at **M6-1** (table at the bottom).

---

## Read-only checks asked for (results)

### Check 1. What `GET /questionnaire/runs/{id}` returns while a run is going
Read from `sidecar/src/grc_sidecar/api/questionnaire.py` and `questionnaire/runs.py` (code read, **not run**).

Fields returned (`StatusResponse`): `run_id`, `status`, `total`, `completed`, `failure`, `summary`.
**Mid-run:**
- `status` = `"running"`, `total` = number of questions (fixed when the run starts), `completed` = index of the last finished question (`state.completed = index`, set in `_answer_all` right after each answer, before the progress event), `failure` = `null`, `summary` = `null`.
- **Not returned mid-run:** per-question status or reasons (only the SSE `progress` events carry them), elapsed time, the file name (only in `summary` after the run), any question or answer text.

So **polling gives real `completed` and `total` with no sidecar change.** Four behaviours the screen must handle:
1. `completed` changes only when a question finishes (about every 40 to 60 s on the owner's CPU-only laptop, as measured in M5), so most one-second polls show the same number. The screen shows a local "time since the last answer" counter and never says "stuck".
2. After the last question `completed == total` but `status` is still `"running"` while the run reads the model digest, **writes the workbook** and writes the ledger run record. The screen says "writing the answers file", not "done". Done means `status != "running"` and `summary` is present.
3. If the model goes away mid-run, the remaining questions are blanked at once (`model_unavailable`), so `completed` jumps to `total`, then `status` becomes `"stopped"` and `summary.complete` is `false`. The screen shows that.
4. An unknown or evicted run id is `404 run_not_found` (only the last 20 runs are kept, and none survive a restart: ADR-014 decision 16).

**Missing, and the smallest change if wanted:** nothing is missing for progress. Existing tests only check the final state (`test_questionnaire_routes.py:264`, `test_questionnaire_runs.py:82`: `completed == 3` after completion), **no test pins a mid-run value**. Chunk 6b adds one test that pins the exact value after **each** answer (amendment 6): a scripted answerer is gated per question; the test releases one question at a time and, after each, reads the status route and asserts `completed == k` for k = 0 (before the first answer), 1, 2, up to `total`, with `status == "running"` and `summary` null every time until the run ends; then asserts `status` is `complete` and `summary` is present. Whether the write phase after the last answer can be paused to assert `summary is None` while `completed == total` is **not verified** (it depends on whether `write_answers` can be gated through `RunDependencies`); if it cannot, the test asserts that state only at the last answer's event and the plan records that limit. Per-row statuses during the run would need a new field on the status route; not proposed (the review queue reads the finished draft instead).

### Check 2. Does any committed file cite `M5_PLAN.md`?
`git grep -n "M5_PLAN"` over tracked files: **no matches.** (Case-insensitive "m5 plan" appears twice in tracked ADR-019, lines 50 and 222, as prose "the environment check from the M5 plan", no file name.) `M5_5_PLAN.md` itself cites `M5_PLAN.md` three times (lines 7 and 9 as the other untracked file, line 617 "M5_PLAN.md deviations 1 to 50 stay in that file").
**Rule applied: commit `M5_PLAN.md` only if a committed file cites it. None does, so it is not committed.** Consequences to state: (a) `M5_5_PLAN.md` line 617 will point at a file that stays local, so M5 deviations 1 to 50 remain only in the owner's working copy; (b) `M5_5_PLAN.md` line 7 says its plan file "is never staged", so committing it reverses an earlier statement in that file (a deviation M6-1, recorded in 6a1 with a one-line note at the top of `M5_5_PLAN.md`); (c) tracked files that cite `M5_5_PLAN.md`: `docs/adr/018-blast-radius.md:9` and `docs/threat-model.md:652` (G36), so committing it fixes two dangling references.

### Check 3. Local paths and machine details in the plan files
Searched `M5_5_PLAN.md`, `M5_PLAN.md` and this plan for paths, user and machine details. **To remove or generalize before `M5_5_PLAN.md` or `M6_PLAN.md` is committed** (decisions for the owner; nothing has been edited):

| File and line | What |
|---|---|
| `M5_5_PLAN.md:95` | a Windows user-profile path to a CI tools folder (`...\<username>\alibi-ci-tools\.venv`); same line: "Windows 11 (Python 3.11.9 venv, Rust stable, pnpm, Docker Desktop, Semgrep image pinned by digest)" |
| `M5_5_PLAN.md:179` | user-profile Downloads paths for recount scripts and output |
| `M5_5_PLAN.md:47`, `:132`, `:596` | plan backups in a OneDrive Documents folder; "the owner's OneDrive backup" (line 596) |
| `M5_5_PLAN.md:46`, `:85`, `:608` | "your Downloads folder", "Live-check files (Downloads)", `Downloads\m55-scripts` |
| `M5_5_PLAN.md:143` | OneDrive/Dropbox named only as examples of sync folders (fine) |
| `M5_5_PLAN.md:368` | "the owner's CPU-only laptop" (the same phrase is already in tracked ADRs and the brief) |
| `M5_PLAN.md:270` | the same user-profile CI tools path (not being committed) |
| `M6_PLAN.md` (this plan) | no local path |

For context: tracked files already contain the Windows username: `docs/adr/012` (2 hits), `docs/adr/017` (2: the STOP L3 commands, lines 73 and 79), `docs/adr/019` (5) and one `OneDrive` hit in ADR-019. No email address was found in the plan files. 6a1 generalizes the two files being committed (proposal: replace user-profile paths with `<your Downloads folder>` or `<tools folder>` placeholders, by edit-tool edits shown as a diff) and asks about the tracked ADR hits (J1).

### Check 4. What the run record holds (amendment 2)
Read from `repositories/base.py` (`NewRunRecord`, `RunAnswer`) and `questionnaire/writer.py` (`ExportedRow`, `output_name`); code read, **not run**.

**The run record (`RunRecord`) holds, per run:** `run_id`, `model`, `digest_before`, `digest_after`, `template_sha256`, `input_sha256`, `file_name` (the draft's output name), `sheet`, `header_row`, `question_col`, `first_new_column`, `question_count`, `complete`, plus `created_on` and a row hash.
**Per question (`RunAnswer`, answered or blank rows both):**
- **Status: yes.** `status` is one of `SUPPORTED`, `PARTIAL`, `NEEDS_REVIEW`, the FINAL status after the changed-source lowering.
- **Reason codes: yes.** `reasons` is a tuple of reason-code strings.
- **Citation: yes, or None.** `source_path`, `doc_id`, `start_line`, `end_line`, `quote`.
- **Hashes:** `question_sha256`, `cited_lines_sha256`, `cited_file_sha256`, `answer_cell_sha256`, `status_cell_sha256` (not null, even for a blank row), `evidence_cell_sha256`.
- **Not held:** the question text, the answer text, the Reason cell text or any hash of it, the Confidence cell or any hash of it, the Review cell. So the Answer and Question texts must come from the workbook (checked by hash), and the workbook's Reason and Confidence cells **cannot be verified** and are not read.
- **The draft's file name carries only the first 8 hex digits of the run id** (`<stem>-UNREVIEWED-DRAFT-<run_id[:8]>.xlsx`, `output_name`), while the ledger import needs the full id. After ALIBI is closed the window does not know the full run id (see A.3 item 8).

---

## STEP 0. Repo state (read-only git)

- `main` = `origin/main` = `bee7364`. **Matches.** `git status`: only `?? M5_5_PLAN.md`, `?? M5_PLAN.md` (and now `?? M6_PLAN.md`). **Matches.**
- **Differs, not fixed:** extra local branches `feat/m3-framework-packs`, `feat/m3a-vault-parsing`, `feat/m3b-vector-store`, `feat/m3c-embeddings-indexer`, `m5-verified-answers`; four remote Dependabot branches (cargo `getrandom-0.4.3`, github_actions, npm, pip minor-patch). Not examined.

## STEP 1. What I read and did not read

**Read in full:** `CLAUDE.md`, `docs/product-brief.md`, ADR-005, 006, 009, 011, 017, 018; threat-model Sections 1 to 5 and 7 to 13.
**Read in part:** threat-model Section 6 (only F5-1a/b, F6-1a/b, F12-2c, F12-4c, F14-1, F20-1/2, F30-7, F31-4b, F32-2, F33-9; I did **not** read the other ~170 rows); ADR-019 lines 1 to 197 (not "Chunk 4c" or "Live check C: results"); `M5_5_PLAN.md` deviation table (lines 596 to 617) plus grep hits; sidecar `api/errors.py`, `api/questionnaire.py`, `api/contradictions.py`, `api/ledger.py` (lines 56 to 230), `api/readiness.py`, `RunState`/`RunEvent`/`_execute`/`_answer_all`/`events()` in `questionnaire/runs.py`; `tests/security_config.rs` lines 60 to 256.
**Host and frontend read:** `lib.rs`, `commands.rs`, `app_commands.rs`, `child_env.rs`, `health.rs`, `host.rs`, `dev_paths.rs`, `Cargo.toml`, `tauri.conf.json`, `capabilities/main-window.json`, `FailureKind` in `supervisor.rs`; `package.json`, `App.tsx`, `main.tsx`, `ipc/health.ts`, `statusText.ts`, `App.css`, `vite.config.ts`, `tsconfig.json`, `eslint.config.js`, `.github/workflows/frontend.yml` (first 70 lines), `.pre-commit-config.yaml` (hook list only: gitleaks).
**Not read:** `M5_PLAN.md` content (grep only: M5 deviations 1 to 50 unread), ADR-001 to 004, 007, 008, 010, 012 to 016, `docs/dev-setup.md` (grep only), `.cursor/rules`, `.coderabbit.yaml`, `.github/workflows/policy.yml` (the Cursor-rule drift check), the rest of `supervisor.rs`, `spawn/job/resolve/ready/token.rs`, other Rust and frontend tests, `healthPoller.ts`, `requireRoot.ts`, sidecar service internals, `main.py`.

## STEP 2. What the frontend and host contain today

- **Runtime deps:** `@tauri-apps/api ^2` (2.12.1 installed), `react ^19.1.0`, `react-dom ^19.1.0`, `zod 4.6.5` (exact).
- **Dev deps:** `eslint 10.12.0`, `@eslint/js 10.0.1`, `typescript-eslint 8.71.1`, `globals 17.13.0`, `typescript ~6.0.3`, `vite ^8.0.16` (8.3.2), `@vitejs/plugin-react ^6.0.2` (6.1.1), `vitest 5.0.3`, `@tauri-apps/cli ^2` (2.12.1), `@types/react ^19.1.8`, `@types/react-dom ^19.1.6`; `packageManager pnpm@12.9.1`. Pins mixed (some exact, some caret).
- **Tailwind: not installed** (CLAUDE.md Section 4 lists it); styling is one plain `App.css` with a dark scheme.
- **Test runner:** Vitest in Node, **no jsdom, no Testing Library**. `App.tsx` is rendered in no test (F6-1b). **React Flow: not installed.** No component library, no formatter (no Prettier); ESLint has no `--fix` step.
- **Window:** one, label `main`, title ALIBI, fixed 800x600 (no `resizable`/min size keys).
- **Host commands:** exactly one, `sidecar_health` (no arguments). It is pinned in three places that must agree: `commands.rs`, `app_commands.rs` (`APP_COMMANDS`), `capabilities/main-window.json` (`allow-sidecar-health`).
- **Capability file:** `windows: ["main"]`, `permissions: ["allow-sidecar-health"]`, description "may call the sidecar health command and nothing else".
- **CSP:** `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src ipc: http://ipc.localhost; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, `freezePrototype: true`, no `devCsp`, no plugins. Verified only in the bundled debug build (ADR-005). `style-src 'self'` also blocks inline `style=` markup and `<style>` tags.
- **Sidecar environment:** `env_clear()` then **five** keys: `SYSTEMROOT`, `GRC_AUTH_TOKEN`, `GRC_LOG_DIR`, `GRC_INDEX_DIR`, optional `GRC_VAULT_DIR`; pinned by `the_allowlist_is_exactly_these_five_keys`, `child_env_contains_only_allowlist`, `supervisor_handshake::the_child_environment_is_exactly_the_allowlist`.
- **Vault source:** `dev_paths.rs` reads host-process env `GRC_VAULT_DIR` in **debug builds only**; release never reads it (ADR-009).
- **Sidecar start:** `start_dev` only (debug). Release builds report `NotConfigured` until M8, so **M6 is exercised end to end in a debug build only.**
- **Host HTTP:** one reqwest client in `health.rs` (`no_proxy`, no redirect, 1 KiB cap, 5 s timeout, `LoopbackEndpoint` takes only a port); `reqwest 0.13.5` with default features off.
- **Host lifecycle:** `HostSlot` starts once; `ShutDown` is terminal. **M6 adds no restart path** (decision 3).

---

## A. Scope

### A.1 What M6 core and M6.5 own

| Item | Owner after this revision | Chunk (M6 core) |
|---|---|---|
| G8 dedicated failure kind for the index/folder preparation | M6 core | 6e |
| G9 Rust `canonicalize` + `starts_with` for folders and window-supplied file names | M6 core | 6e |
| G10 junction test for the vault scanner | M6 core | 6c |
| G11 host reads `/readiness`, window shows model-unavailable | M6 core | 6d (route), 6f (screen) |
| (new) Review queue needs ledger folder and run record; typed refusal without one | M6 core | 6b (route), 6g (warning), 6h (queue) |
| G12 CSP/DevTools verified in a bundled debug build | M6 core | 6g checks, 6i final |
| G29 canary test for logging in the code the UI adds | M6 core | 6d (Rust), 6f (frontend), re-run 6g and 6h |
| G30 hostile cell texts as real workbook cells | M6 core | 6b |
| G34 request-body limit at the proxy | M6 core for routes in the M6 table; the 8 MiB `blast-radius` cap row moves to M6.5 with that route | 6d |
| F12-2c, F12-4c wording and tests | M6 core | 6h |
| F6-1b what the window shows per parsed state | M6 core | 6f |
| **G35** acknowledge records the recall state seen (and F30-7) | **M6.5** | |
| **G40** run-id design for the contradiction run | **M6.5** | |
| **F32-2** flags shown are proposed only (`flags_now`) | **M6.5** | |
| **F31-4b** screen must not hide the "intact" statement | **M6.5** (screen tests) | |
| **F33-9** screen must not hide the numbers-only statement | **M6.5** (screen tests) | |
| React Flow graph view, its sidecar route and dependency | **M6.5** | |
| Ledger, recall, blast-radius, contradiction, reality screens | **M6.5** | |
| Skills (Impeccable, Emil Kowalski, web-design-guidelines) decision | **M6.5**, decided at its start after the owner and reviewer read the repos | |

**M6 core scope:** folders (pickers, saved on the host, applied on next launch), vault index action, questionnaire inspect/map/run with polled progress, review queue of the finished draft, status bar with health and readiness, host proxy, path checks.

### A.2 Disagreements found (and what the revision does)

1. CLAUDE.md Section 4 names TailwindCSS and React Flow; neither is installed. Tailwind: decided at the start of 6f (decision 7). React Flow moves to M6.5, so Section 4 should say so.
2. CLAUDE.md M6 row lists "React Flow graph view" and "Rust SSE proxy"; both leave M6 core (React Flow to M6.5; the SSE relay is dropped for polling).
3. CLAUDE.md M6 row passes "questionnaire and export roots"; the questionnaire flow also needs the vault, and later screens need import and ledger: **five roots** are saved and passed.
4. CLAUDE.md 8.4 last bullet says "The Rust host proxy comes in M6" for SSE progress; the revision polls the status route instead.
5. The brief says recall, contradictions, ledger and blast radius have "no screen until M6": after the re-cut that is **M6.5**.
6. The brief's "Honest blanks" row says "Human review, which arrives with the M6 review queue, is the control". True for M6 core only as *reading*; the queue records no approval (approval stays Excel plus import, ADR-017 decision 1; the writer is the only saver, ADR-014 decision 10).
7. Threat model F12-2c / F12-4c say "the control is human approval (M6)"; with no approval UI it is human review (C.5).
8. `security_config.rs` and ADR-005 pin "exactly one command, no plugin permission"; M6 core adds commands and a Rust-side plugin (decision 11, section E).
9. ADR-009: "a release app cannot be handed an arbitrary path through its environment" stays true; a saved config file is a new source (same-user write, O1). Amended by addition in ADR-020.
10. Threat-model Section 1, F20, F24 say "not yet consumed by the host": becomes false in 6d to 6g.
11. ADR-011 line 37: a dedicated failure kind changes `health-contract.json`, `health.ts` and the Rust fixture test together (ADR-006 says "all 24 failure kinds").
12. **Plan-file rule vs plan decision:** `M5_5_PLAN.md:7` says it is never staged; decision 9 commits it.
13. **README.md line 33** says "There is no screen yet" and describes the env-variable way to run; it becomes false when M6 core lands. It is not in the owner's H list; added to the 6i docs sweep (J10).

### A.3 Gaps found by reading code (in M6 core scope)

1. **No route returns per-row questionnaire results.** The status route returns counts and a summary only; answer text lives only in the draft `.xlsx`, and the ledger run record holds status, reason codes, citation and hashes but no text (Check 4). The review queue needs a new **read-only** sidecar route (6b, C.5), and **it needs the ledger folder and a run record**: with no run record there is nothing to verify the workbook against (amendment 2).
2. **No route builds the vault index.** `Indexer.sync` is called only by the pack loader; the questionnaire runtime only has `refresh_staleness`. Without an "index the vault" action a user hits `409 index_not_built` forever. New sidecar route (6b).
3. **No file-listing route.** Routes take file *names*; the host lists `*.xlsx/*.xlsm` in the questionnaire and export folders (6e).
4. The window is a fixed 800x600; a review table needs a larger window (decision 8: resizable, default 1100x720, minimum 900x600; edit in 6f).
5. Per-run states (last 20) vanish when ALIBI is closed; the questionnaire screen says so.
6. No mid-run pin test for `completed` (Check 1; 6b adds it, asserting the exact value after each answer).
7. **Until M8, release builds cannot start the sidecar** (`start_dev` is debug only; release reports `NotConfigured`). **Every M6 screen is therefore verified and demonstrated in a debug build only** (a debug build of the bundled app, run from the repository checkout with the dev sidecar venv; there is no installer and no release build that works). Every M6 PR, chunk report and doc must say "debug build", never "the app" or "the installer", where that matters (section H lists the lines).
8. **After ALIBI is closed the window does not know a draft's full run id.** The draft's file name carries only 8 hex digits of it, the in-memory run list is gone, and no route lists run records. The draft-rows route is keyed by the **draft file name** (chosen from the host's listing of the export folder) **only if the ledger repository can look up a run record by its recorded `file_name`** (owner answer J9). **Chunk 6b must first read the repository and report whether such a lookup exists**; if it does not, 6b adds the **smallest read-only lookup** (a read method only, so the ledger stays append-only). **A run is never matched by the 8-hex-digit prefix in the file name**, only by the full recorded `file_name`: a renamed draft therefore has no record under its new name and is refused with `run_record_missing`, which is the safe behaviour (a `run_id` field for renamed copies is not in M6 core). See J9.

---

## B. Architecture

### B.1 The Rust proxy (6d)

**Choice: one command, `sidecar_call`, taking a closed `Route` enum.** Rust holds a static table: for each variant the HTTP method, a fixed path template (only a run id matching `^[0-9a-f]{8,64}$` is substituted), a typed request struct (`deny_unknown_fields`), a request-body cap, a response cap and a timeout. The window never supplies a URL, header, method or port.
*Interview reason:* the token and the port stay in Rust, so the window can only ask for named operations. One command with a closed table gives the capability file one entry and one table to test; separate commands per route would give no extra security (they all go to the same window) and more places to drift. A command that took a path string would make the host an open relay to the sidecar.

M6 core routes in the table (seven): `readiness` (GET), `questionnaire_inspect`, `questionnaire_start`, `questionnaire_status`, `vault_index_start` (new, 6b), `vault_index_status` (new, 6b), `draft_rows` (new, 6b). Ledger, recall, blast-radius, contradictions and reality routes are **added in M6.5**.
- **`vault_index_start` and `vault_index_status` (amendment 4; job plus poll decided, J3).** The job-plus-poll design is decided; the exact field names are fixed in 6b's plan: `vault_index_start` is a POST with an empty body (`extra="forbid"`), answers 202 `{job_id}` after taking the shared heavy-job slot and passing the model gate, or `409 run_in_progress`, `503 models_unavailable` (names), `503 vault_root_not_configured`; `vault_index_status` is a GET with the job id (the same `^[0-9a-f]{8,64}$` pattern substituted into the path) and answers `{status, files_done, files_total, ...}` with fixed words and integers only, `404` for an unknown id. Both are short requests, so neither needs a long timeout (start 30 s because it preflights the model gate, status 5 s). The route table test covers both variants (method, path, body schema, caps, timeout); a missing or extra variant fails it.
- **Rebuilds the body in Rust** from the typed struct; it does not forward the window's text.
- **Returns** `{status: u16, body: JSON}` only after the status is in an allowed set, the body is within its cap and is valid JSON; the window parses it with a strict per-route Zod schema. The sidecar's typed error bodies pass as JSON (`{"error": word}`, `workbook_refused` + `code`, `models_unavailable` + `models_missing`, ...); Zod enumerates every code. Raw sidecar text never reaches the window.
- **Timeouts (starting values, unmeasured):** connect 2 s; `readiness` and `questionnaire_status` 5 s; `questionnaire_inspect` 60 s; `questionnaire_start` 60 s (it preflights models and reads the digest); `vault_index_start` 30 s, `vault_index_status` 5 s; `draft_rows` set in 6b once the route exists (it reads one workbook, so 60 s is the starting value).
- **Body limit (G34):** request caps checked **before** sending (16 KiB for these routes), response caps (1 MiB; `draft_rows` up to 4 MiB with the sidecar's 500-question cap). **Limit, stated:** this protects window to sidecar only; another local process holding the token can still send an oversize body to the sidecar, so G34 is closed only for the proxied path.
- **IPC errors (separate from sidecar errors):** a closed `ProxyError` enum of unit variants (`not_ready`, `unauthorized`, `unreachable`, `timeout`, `too_large`, `bad_request`, `bad_response`, `internal`), serialized `{"error": ...}` like `HealthError`, with a committed contract fixture and exhaustive-match test (ADR-006 decision 3 pattern), `size_of <= 1`.
- **Shared client:** refactor `HealthClient::build` into a shared sidecar client (same `no_proxy`, `Policy::none()`, sensitive header, port-only endpoint); `sidecar_health` keeps its fixed reply.
- **Polling, not SSE (decision 4):** the window calls `questionnaire_status` about once a second through `sidecar_call`. No `Channel`, no event permission, no SSE parser, no change to ADR-006's capability stance. The sidecar still serves SSE (ADR-014); the host does not relay it. *Interview reason:* the status route already carries `completed` and `total` (Check 1), so a poll gives true progress with fewer moving parts and no new permission; the cost is up to one second of delay, which is nothing next to a 40 to 60 second answer.

### B.2 Folders (6e), no restart (decision 3)

**Seven folder settings:** vault, index, log, questionnaire, export, import, ledger. The host chooses index and log; the user picks the other five. The environment allowlist becomes **nine keys: `SYSTEMROOT`, `GRC_AUTH_TOKEN`, and the seven folder variables** (the five user folders are optional keys; the index and log keys are always present).

1. **The host owns the settings.** A small JSON file in the app config directory holds the five chosen folders. The window never gives the sidecar a path; it asks the host to *pick* a folder of a named kind (enum), and the host returns the validated folder's display path or a typed error.
2. **Saved now, applied on the next launch.** The sidecar reads its settings once, so a change takes effect the next time ALIBI starts. The screen shows two columns per folder: **In use now** and **Saved for next launch**, and says in plain words: "Saved. Takes effect the next time you start ALIBI." No restart path, no new host state, no restart-state tests (removed: F35-5 and the restart design).
3. **Validation on set and on every start.** `canonicalize`, strip `\\?\` (reuse `dev_paths::existing_directory`: refuses UNC, device and non-drive verbatim paths), must be a directory, then the **same overlap rules as the sidecar's `Settings`** (equal, inside or containing, pairwise among vault, index, questionnaire, export, import, ledger), using `starts_with` on canonical paths. At start, each saved folder is re-validated; an invalid one is **not passed** (its key is absent, so the sidecar reports its usual "not configured") and the host records a typed reason the screen shows ("saved folder is no longer valid"). The sidecar still validates again (defence in depth).
4. **Picker:** `tauri-plugin-dialog`, **used only from Rust**: the plugin is registered, but **no dialog permission is granted to the window** (decision 11), so the window cannot open a dialog itself or receive a path as authority. Approved in principle; at install time I give name, exact pinned version, license, network/telemetry statement (I have **not** looked these up).
5. **File names, not paths, from the window (G9):** questionnaire and export file names are validated as one path component (no separator, `..`, drive, colon, NUL, over 260 characters, reserved device names), then `canonicalize(root.join(name))` must `starts_with(root)` and be a regular file before the request is forwarded. The sidecar repeats its own `resolve_within`. The host lists names (`*.xlsx`, `*.xlsm`) with a count cap.
6. **G8:** a dedicated `FailureKind` (provisional name `index_dir_unavailable`) replaces the reuse of `EnvRejected` for "could not prepare the index folder". It changes the Rust enum, `health-contract.json`, `health.ts` and `health.test.ts` together (count 24 becomes 25).
7. **Debug-only fallback kept (decision 8):** `dev_paths.rs` still reads the host env `GRC_VAULT_DIR` in debug builds, used **only when no vault is saved**. A saved vault wins. Release builds still never read it.
8. **Configuration order for the user (amendment 3):** vault, then "Index the vault" (needs the embedding model), then the **ledger folder**, then questionnaires and exports, then the first run. **The ledger folder must be set before the first questionnaire run if the user wants the review queue**: a run without it keeps no run record (`run_record_not_kept`), and a draft with no run record cannot be opened in the queue. Because folders apply at the next launch, "set before the first run" means "set, then restart ALIBI, then run"; the screens say so (C.2, C.4).

*Interview reason:* the folders are the app's authority over the user's files, so the host, not the webview, decides them and checks them twice. Applying them at the next launch keeps "the sidecar reads its settings once", which the folder-overlap and allowlist tests rely on; a runtime reconfigure or restart path would add new state and re-open those tests.

**Tests and ADRs that change in 6e:** `child_env.rs` tests pinned to five keys (`the_allowlist_is_exactly_these_five_keys`, `child_env_contains_only_allowlist`, `with_no_vault_the_vault_key_is_absent_and_nothing_else_is`, `relative_paths_are_rejected`, `a_nul_character_in_a_path_is_rejected`, `a_path_that_is_not_unicode_is_rejected`, `hostile_looking_values_are_carried_verbatim_and_never_add_keys`, `every_error_variant_formats_without_a_token_or_path`, `error_type_has_no_payload_so_it_cannot_carry_a_token_or_path`, `debug_output_lists_keys_but_never_values`) are re-pinned to nine keys and gain overlap/abs-path cases; `supervisor_handshake::the_child_environment_is_exactly_the_allowlist` and the `fake_sidecar` env report; `health.rs::the_ipc_contract_fixture_matches_what_rust_serializes` plus `health-contract.json`, `health.ts`, `health.test.ts`; `dev_paths` tests stay. `security_config.rs`: see E.1. Python: none. ADRs: ADR-020 (written in 6a2) amends ADR-009 ("How the sidecar gets its folders") and ADR-017 decision 7's note ("five-key child environment unchanged until M6") by addition; ADR-005 (command list), ADR-011 (G8 closed, G11), ADR-014 (new routes) are amended by addition.

### B.3 Host path check (G9) and the junction test (G10)

G9 is B.2 items 3 and 5 (Rust tests: `..`, absolute, drive, UNC, device, trailing dots and spaces, reserved names, a file in the folder that is a link to outside where Windows allows creating one without privilege). **Rust junction tests are omitted, and the Python test in 6c covers the junction case.**
6c: a Windows-only Python test creates a real junction inside a temporary vault with `_winapi.CreateJunction` and asserts the scanner refuses it, with a mutation proof (remove the "resolved path equals the walked path" equality in `vault/scan.py`).

### B.4 `/readiness` for the window (G11)

`readiness` is a route in the 6d table (GET, no body, 5 s, 1 KiB cap), strict-parsed to `{state, models_missing, ollama}` with closed enums (`checking|ready|degraded`; `unknown|reachable|unreachable|bad_response`). The 6f status bar polls it every 5 s **only while health is `healthy`**. A degraded state shows the exact missing model names and "ALIBI never downloads models; install them with Ollama yourself". Model-dependent buttons are disabled with that reason, and a typed `503 models_unavailable` from a route is still handled (the snapshot can be stale: ADR-011).

---

## C. Screens and flows (M6 core)

**Global rules.**
- **Status words come from code:** an exhaustive `switch` maps `SUPPORTED`, `PARTIAL`, `NEEDS_REVIEW` to a fixed label; an unknown value renders a visible error, never a label.
- **Drafts are marked unreviewed:** any answer shows "Unreviewed draft" (review state is always `unreviewed` in M5, ADR-013).
- **Model text is plain text and never authority:** React text nodes only (no `dangerouslySetInnerHTML`; Semgrep `grc-ts-dangerously-set-inner-html` exists), `white-space: pre-wrap`, `unicode-bidi: isolate`, control characters shown visibly, no auto-links, long text collapsed behind "show more".
- **Fixed statements are always visible** (not in a tooltip). In M6 core: the F12-2c sentence (C.4) and "UNREVIEWED DRAFT". The recall "intact" statement and the numbers-only statement are M6.5; those screens must carry them, and their tests are M6.5 scope.
- **Every screen has states:** empty, loading, stale, model-unavailable, 409 busy, each typed error, success. "Not configured" is never rendered as "0 results".

### C.1 Shell and status bar (6f)
Health (existing codes, plus the new failure kind) and readiness; a folders-configured summary. States: starting (navigation disabled), failed, healthy, models degraded (names listed).

### C.2 Folders screen (6g)
Five rows (vault, questionnaires, exports, imports, ledger). Each: not set; set with "In use now" and "Saved for next launch" (shown when they differ); invalid (typed host reason: not a directory, overlaps another, UNC or device path refused, no longer valid at start); picking. Fixed text: "Saved. Takes effect the next time you start ALIBI." Ledger help: back it up, never put it in a sync folder (ADR-017 decision 21; the ledger screens themselves are M6.5, but the folder is chosen here). **Fixed warning next to the ledger row (amendment 3):** "Set the ledger folder before your first questionnaire run if you want to use the review queue. A run without it keeps no run record, and its draft cannot be opened in the queue." The rows are shown in the recommended order (vault, ledger, questionnaires, exports, imports) with the ledger row marked "needed for the review queue". A ledger folder that is saved but not yet in use shows the same warning with "applies next launch".

### C.3 Index the vault (6g, route from 6b)
Button calls `vault_index_start`, then the screen polls `vault_index_status` about once a second (same pattern as the questionnaire run). States: no vault (`vault_root_not_configured`), model unavailable (embedding model, `503 models_unavailable` with names), `409 run_in_progress`, running (`files_done` / `files_total`, a local elapsed counter, no ETA), finished, failed (fixed code), unknown job (`404`, shown as "this index job is no longer known"), stale files withheld (ADR-009). The routes' final field names are set in 6b's plan (job plus poll is decided, B.1).

### C.4 Questionnaire flow (6g)
| State | Shown |
|---|---|
| No questionnaire/export folder | `503 questionnaire_root_not_configured` / `export_root_not_configured`: "choose a folder" (needs next launch) |
| Folder empty | "no .xlsx or .xlsm files in the folder" |
| Inspect loading | 60 s timeout; "reading only, the file is not changed" |
| Inspect refused | `workbook_refused` + each `code` (closed Zod enum) |
| Mapping | sheets, suggestion, `macro_enabled`, dropped-parts inventory warning (images and charts may be dropped, CLAUDE.md 8.4) |
| Before start, ledger folder not in use (amendment 3) | Warning shown **before** the Start button is enabled to confirm: "No ledger folder is in use. This run will keep no run record (`run_record_not_kept`) and its draft cannot be opened in the review queue. Set the ledger folder and restart ALIBI first, or run anyway." The host knows the in-use folders, so the warning does not wait for the run to finish. "Run anyway" is allowed (the run still works, ADR-017 decision 15 (d)) |
| Start | 202 `{run_id,total}`; `409 run_in_progress`; `503 models_unavailable` (names); `409 index_not_built` (link to the index screen); `409 vault_changed_reindex`; `503 retrieval_unavailable`; `mapping_refused` codes; `export_refused` codes + `column` |
| Running | poll once a second: `completed / total` from the route, a local "time since last answer" counter, the fixed line "each answer can take 40 to 60 seconds on a CPU-only laptop (measured on the owner's machine)". **No ETA and no smooth percent bar.** After `completed == total` while `status == running`: "writing the answers file". |
| Done | counts per status, reason counts, file name, seconds, warnings, dropped parts; "UNREVIEWED DRAFT". `run_record_not_kept` or `run_record_failed` in `summary.warnings` is shown as "No run record was kept: this draft cannot be opened in the review queue" and the "Open in review queue" action is disabled with that reason |
| Stopped / failed | `stopped` with `complete: false` (model went away); `failure` code from the closed set; `cancelled` |
| Unknown run | `404 run_not_found`: "this run is no longer known (only the last 20 are kept and none survive closing ALIBI); the file is in the exports folder" |

### C.5 Review queue (6h) and F12-2c, answered honestly
**What it can do:** list every row of a finished draft ordered by risk (`NEEDS_REVIEW` first with the code's reasons, then `PARTIAL`, then `SUPPORTED`), show the question, the draft answer, the **source file and exact lines and the verbatim quote side by side**, and on every `SUPPORTED` or `PARTIAL` row the fixed sentence: "The quote is real company text that was in the context. ALIBI has **not** checked that it answers this question."
**What it cannot do:** prove relevance. F12-2c and F12-4c **stay No mitigation** as code controls; their rows are reworded to "the control is human review in the review queue, which has no completion check", and the measured counts stay cited (blank-labeled items answered 4, 5 and 3 of 9 in three held-out runs, ADR-013 live check B). **The queue records no approval:** approving remains typing `approved` in the Review column of the workbook and importing it (ADR-017 decision 1, a M6.5 screen). A "mark approved" button would need ALIBI to save a workbook, against ADR-014 decision 10 and CLAUDE.md 11 (never overwrite). The screen says so in a fixed line.
**Data source (6b), and what it needs (amendment 2).** The review queue **needs the ledger folder (`GRC_LEDGER_DIR`) and a run record** for the draft. A new read-only route (provisional `POST /questionnaire/draft`, body `{file}` only, keyed as A.3 item 8 and J9 say: full recorded `file_name` lookup only, never the 8-hex prefix) reads the draft from the export folder through the same loader as the ledger import (`read_input_bytes`, the loader's scan, the same bytes parsed; the route never saves a workbook) and joins the run record, exactly as the ledger import does for `status_edited` and `evidence_edited`. Cap 500 rows (the engine's cap). No writes, no log of content at any level.

**Per field of the response: where it comes from.**
| Field | Source | Trust rule |
|---|---|---|
| `row` (row index) | run record (`RunAnswer.row_index`) | code's |
| `question_text` | **workbook** (the question cell) | the cell's hash is compared with the record's `question_sha256`; **any mismatch, or a different number of question rows, refuses the whole response** (`question_mismatch`, integer counts only), as the import does |
| `status` | **run record** (`RunAnswer.status`, the final status) | code's; **never read from the workbook** |
| `status_edited` | workbook Status cell hashed and compared with the record's `status_cell_sha256` | `true` when they differ. **For an edited-status row (owner answer J12) the queue shows the run record's status labelled "recorded by ALIBI", plus the fixed line "The Status cell in the workbook was changed after drafting. The code's verdict is shown, not the cell."** The edited cell's text is **never** shown as the verdict (the route does not even return it). The row is ordered first, like `NEEDS_REVIEW` |
| `reasons` | **run record** (`RunAnswer.reasons`, reason codes) | code's; the workbook Reason cell is **not read** (the record holds no hash of it, so it cannot be verified) |
| `answer_text` | **workbook** (the Answer cell), only when the record says an answer was written | plain text, untrusted |
| `answer_edited` | workbook Answer cell hashed and compared with `answer_cell_sha256` | `true` when they differ ("the owner or someone edited this answer") |
| `citation` (`source_path`, `start_line`, `end_line`, `quote`) | **run record only** (`RunAnswer`); **never parsed from the Evidence cell** | code's; `null` when the record has none |
| `evidence_edited` | workbook Evidence cell hashed and compared with `evidence_cell_sha256` | `true` when they differ; the screen then warns that the cell was edited and that the citation shown is ALIBI's record, not the cell |
| Confidence cell | not returned | the record holds no hash of it and the model's self-rating decides nothing; omitted |
| Review cell | not returned in M6 core | approval is a M6.5 screen (ADR-017 decision 1) |
| Header: `model`, `file_name`, `complete`, `question_count` | run record | code's |

**When there is no run record.** The route **refuses; it does not return a degraded read**: without the record nothing in the workbook can be verified, and the Evidence cell may not be used as a citation. Typed errors (fixed words, no text): `503 ledger_root_not_configured` when no ledger folder is in use; `422 draft_refused` with code `run_record_missing` when the ledger has no record for this draft (including a run made without a ledger, `run_record_not_kept`), and with `headers_changed`, `question_mismatch` (counts) and the loader's own refusal codes as the import has them; `503 export_root_not_configured`; `404 file_not_found`. The screen turns each into a fixed message, and the `run_record_missing` message tells the user how to avoid it (set the ledger folder, restart, run again). Whether to reuse `ImportRefusedError` codes or add a `DraftRefusedError` is decided in 6b's plan.
**Tests (6h):** `renderToStaticMarkup` of every state; the fixed sentence present on every `SUPPORTED`/`PARTIAL` row; the 11 hostile cell texts from `fixtures/adversarial-m5/questionnaire-cells.json` render as escaped plain text; the order test; a row with `status_edited` shows the run record's status labelled "recorded by ALIBI" with the exact fixed line from the table above, never the edited cell's text, and sorts first; `answer_edited` and `evidence_edited` warnings render; a refusal (`run_record_missing`, `question_mismatch`, `ledger_root_not_configured`) renders its fixed message and no rows; a test that no control in the queue calls a command that writes a workbook (there is none).

---

## D. Design system and tooling

- **Skills: nothing is installed in M6 core.** Which skills, if any, are used is decided **at the start of M6.5**, after the owner and the reviewer have read the repositories themselves. (For the record, this session's web searches found only secondary sources and verified nothing about install behaviour or telemetry; nothing in this plan relies on them.)
- **`docs/DESIGN.md`** is written from the brief in chunk 6a2: tokens (status colours that also differ by label and shape), type scale, spacing, the fixed-statement component, the unreviewed-draft badge, empty/loading/error patterns, and the minimum window size.
- **Tailwind:** keep as CLAUDE.md Section 4 says, **exact pin**, **decided at the start of 6f, not before.** Proof required in a bundled debug build: the generated CSS is a bundled file and satisfies `style-src 'self'` (no inline `style=` markup, no `<style>` tag, no remote font). If it does not hold, 6f stops and asks; plain CSS variables are the fallback. Approved in principle (decision 10); at install time I state package names (Tailwind and its Vite integration), exact versions, licenses and network/telemetry behaviour, which I have **not** looked up.
- **No shadcn, no jsdom, no Playwright, no Excel library, no React Flow in M6 core.**
- **Rules that outrank any tool:** no remote assets; fixed statements always visible; status words from code; no new dependency without approval; nothing here authors the brief.

---

## E. Accessibility and testing

**Accessibility (M6 core screens).** Everything reachable by keyboard with a logical order and a visible focus ring; no keyboard trap; the review table navigable by keyboard. **Contrast target (proposed):** WCAG 2.2 AA minimum (4.5:1 text, 3:1 large text and UI components), and 7:1 for status words and the fixed statements because they are honesty controls; status is never colour alone; checked by script against the tokens in light and dark. **Reduced motion:** `prefers-reduced-motion: reduce` removes non-essential animation; progress is text and numbers. **Reduced transparency:** design with no transparency or blur, so the preference has nothing to change (support in WebView2 is unverified, so nothing relies on it); also test `forced-colors` and 200% zoom at the 900x600 minimum. Progress is announced politely and throttled.

**Testing.** Pure reducers and state machines per screen; every Zod schema against fixture JSON (host-owned contracts generated from Rust as in ADR-006); exhaustive-switch tests for status words; **`react-dom/server` `renderToStaticMarkup` tests of presentational components** (no jsdom), which is how F6-1b is covered (what the window shows per parsed state). Hooks stay thin and are checked by hand. No scripted UI tool in M6. **Real WebView2 window (G12):** in 6g and 6i from the bundled debug build (`pnpm tauri build --debug --no-bundle`, run `desktop.exe`): the ADR-005 probes (`fetch('http://127.0.0.1:65000/')` refused by `connect-src`, `eval('1')` throws), the delivered CSP header, each screen's states, the hostile cell texts shown as plain text, forced-colors, keyboard-only pass; recorded as observed or not observed. DevTools stay available in debug builds (F5-1b stays No mitigation); whether release builds ship DevTools is checked in 6i (not verified now).

### E.1 `security_config.rs` and the dialog plugin (decision 11), exactly
The window must not be able to call the dialog plugin directly. What changes in `apps/desktop/src-tauri/tests/security_config.rs` (edited in 6e):
1. `the_app_manifest_commands_are_exactly_the_health_command` (asserts `APP_COMMANDS == ["sidecar_health"]`): updated to the exact new list (provisional: `sidecar_health`, `sidecar_call`, `folders_get`, `folder_pick`, `folder_list_files`); renamed to say "exactly the app commands".
2. `the_capability_grants_exactly_the_health_command_to_the_main_window` (asserts permissions `== ["allow-sidecar-health"]`): updated to exactly the `allow-<command>` entries for that list, **and nothing else**; its message "no core:default, no plugin permission, nothing else" stays and is now the live assertion.
3. `the_capability_covers_each_app_command_and_nothing_more`: logic unchanged; it passes only if (2) is right.
4. **New test** `the_window_is_granted_no_plugin_permission`: every permission string in every capability file starts with `allow-` and none contains `:` or begins with `dialog`, `core`, `opener` or `fs`.
5. `no_global_tauri_object_and_no_plugin_configuration` (asserts `plugins` is empty in `tauri.conf.json`): **kept unchanged**; the dialog plugin is registered in Rust (`lib.rs`), not in `tauri.conf.json`. If the plugin proves to need a `plugins` entry (not verified), this test is amended on purpose with the evidence.
6. **New test** `exactly_one_plugin_crate_is_a_direct_dependency`: parses `Cargo.toml` and asserts `tauri-plugin-dialog` is present with an exact `=` pin and that no other `tauri-plugin-*` appears, so any later plugin is a visible test change.
7. **New test** `no_command_hands_the_window_a_path_it_can_submit_back`: reads `commands.rs` and asserts the folder commands take an enum kind (never a path argument) and the only `PathBuf`/`String` path in a command reply is a display value (a source-level check plus a unit test of the command's argument types).
8. **Mutation proof:** add `dialog:default` to the capability; tests 2 and 4 must fail; restore and show equal hashes.
9. **Runtime proof (6e, repeated in 6i), in the bundled debug build:** from DevTools, `await window.__TAURI_INTERNALS__.invoke('plugin:dialog|open', {directory: true})` must reject with a "not allowed" message; the text is recorded as ADR-005 recorded the `sidecar_health not allowed` string. If the plugin's command name differs, the observed name is recorded.
10. `main-window.json` `description` is updated by edit (the test checks keys, not description). `tauri.conf.json` window keys are pinned by a new test in 6f: `resizable: true`, `width 1100`, `height 720`, `minWidth 900`, `minHeight 600`.

---

## F. Threat model: rows planned for M6 core, with the chunk that writes each test

Each row is written as "No mitigation (planned, M6 chunk X)" at the start of its PR and relabelled at its close; a row says Mitigated only with a resolvable test. Chunk 6a2 writes the planned rows; 6i recounts the tally (throwaway scripts outside the repo, outputs hashed, as PR 1 to 4).

| Row | Threat | Test it would cite | Chunk |
|---|---|---|---|
| F34-1 | Window asks the proxy for an operation outside the route table | route-table test (every `Route` has method, path, caps, timeout; exactly the seven M6 core variants, including `vault_index_start` and `vault_index_status`); `security_config.rs` tests 1 to 3 | 6d (table), 6e (capability) |
| F34-2 | Proxy used to reach a non-loopback host or to inject a header or path | URL builder takes a port and a route id only; a fake server records request line and headers | 6d |
| F34-3 | Oversize request or response buffered (G34) | request refused before send; declared-length and chunked responses over cap refused | 6d |
| F34-4 | Sidecar text, token or detail reaches the window in an error | `ProxyError` fixture and `size_of <= 1`; token canary in every error string | 6d |
| F34-5 | Proxy or HTTP library logs content (G29, Rust half) | capturing `log` logger at Trace across the proxy and reqwest/hyper with a canary in body and reply | 6d |
| F35-1 | Tampered saved-folders file (same-user write, O1) | accepted; start-time re-validation test | 6e |
| F35-2 | A chosen folder overlaps another or is UNC/device/junction | overlap and prefix tests | 6e (junction case: 6c) |
| F35-3 | A file name from the window escapes the folder (G9) | path tests: `..`, absolute, drive, UNC, device, reserved names, link to outside | 6e |
| F35-4 | The window calls the dialog plugin or submits a path as authority | `security_config.rs` tests 2, 4, 6, 7 and the mutation proof; the runtime probe | 6e, re-run 6i |
| F35-5 | A saved folder is invalid at launch and is silently used or silently dropped | start-time test: key absent, typed reason shown | 6e |
| F35-6 | The screen shows a saved folder as if it were in use | static-markup test of the two-column states | 6g |
| F36-1 | Hostile text (cell, answer, quote) rendered as markup or a link | 11 hostile cell texts as escaped plain text; Semgrep and a lint ban on `dangerouslySetInnerHTML`/`innerHTML` | 6g (questionnaire screens), 6h (queue) |
| F36-2 | Status word built from text, or unknown status shown as a label | exhaustive-switch tests; unknown renders an error | 6f, 6h |
| F36-3 | Direction or control characters mislead the reader | rendering test with direction overrides and controls | 6h |
| F36-4 | UI logs content (G29, frontend half) | ESLint `no-console` plus a console-spy canary per screen | 6f, re-run 6g, 6h |
| F37-1 | Review queue read as proof of correctness (F12-2c) | fixed-sentence test on every `SUPPORTED`/`PARTIAL` row; row stays No mitigation | 6h |
| F37-2 | The queue hides a `NEEDS_REVIEW` reason | static-markup test of reasons | 6h |
| F37-3 | The UI offers or implies approval | test that no approve control or workbook-writing command exists; fixed line shown | 6h |
| F37-4 | The queue shows a workbook-edited status as the code's verdict | static-markup test: `status_edited` rows show the run record's status labelled "recorded by ALIBI" with the fixed line "The Status cell in the workbook was changed after drafting. The code's verdict is shown, not the cell."; the edited cell's text never appears | 6h |
| F37-5 | A draft with no run record is run without the user knowing it cannot be reviewed | static-markup tests of the ledger warnings on the folders screen, before Start, and after `run_record_not_kept` | 6g |
| F38-1 | The draft-rows route leaks content (log, error, echo) or reads outside the export folder | canary at DEBUG; path guard test; no echo | 6b |
| F38-1b | The draft-rows route shows a workbook value as if it were the record's: status read from the Status cell, a citation parsed from the Evidence cell, or a workbook Reason read as verified | tests that the response status and citation equal the run record when the workbook cells are rewritten; `status_edited`, `answer_edited`, `evidence_edited` flags; mutation proofs (read status from the workbook; parse the Evidence cell; drop a hash comparison), each must fail a test | 6b |
| F38-1d | A run record is matched by the 8-hex prefix of the draft file name (two runs with the same prefix, or a renamed file) | lookup uses the full recorded `file_name` only; a test with two records sharing a prefix returns only the exact match; a renamed copy is refused `run_record_missing`; mutation proof (match by prefix) must fail a test | 6b |
| F38-1c | A draft with no run record is returned as if verified | `run_record_missing` and `ledger_root_not_configured` refusals return no rows; test with a draft from a run made without a ledger | 6b |
| F38-2a | `vault_index_start` reads outside the vault, writes outside the index folder, runs while another job is running, or runs without the model | folder-guard tests; shared-slot 409 test; model-gate 503 test; no content in logs | 6b |
| F38-2b | `vault_index_status` leaks paths or content, or answers for an unknown job | fixed fields only; `404` for an unknown id; canary | 6b |
| F38-3 | Hostile cells as real workbook cells (G30) | `test_adversarial_cells_workbook.py` (proposed name) through loader, scripted answerer, writer, offline: no status raised, no formula or hyperlink live, question cells unchanged, no extra row | 6b |
| F38-4 | The status-route progress assumption is wrong mid-run | exact-value pin test: `completed == k` after each answer for k = 0 to `total`, `summary` null until the end | 6b |
| Changed | F1 (nine keys), F5-1a (command list), F5-1b (G12 observed), F6-1b (static markup: relabel at 6f), F12-2c and F12-4c (text), F13-1b/F24-3 (new routes), F20-1/2 (host now reads `/readiness`), F24 ("not yet consumed by the host"), Section 1/4/5 text | tests above | 6a2 writes, closes 6f to 6i |
| Backlog | G8 (6e), G9 (6e), G10 (6c), G11 (6f), G12 (6i), G29 (6d, 6f), G30 (6b), G34 (6d) | | |

**Rows and items that change owner from M6 to M6.5:** G35 and F30-7; G40; F32-2; the "(M6)" limit sentences in F31-4b and F33-9 (the screen tests); the LLM-table text that says "M6" for those. Their **text edits** are made in 6a2 (owner changes only; labels do not change), and G34's blast-radius cap row is added in M6.5. Rows that **stay** M6: F12-2c, F12-4c, F6-1b, G8 to G12, G29, G30, G34.

---

## G. Chunks and PRs (M6 core)

One Claude Code session per chunk; each ends with its verification commands (CLAUDE.md Section 6) and a diff saved in your Downloads folder. Branch off `main`, merge commit. Every chunk starts with its own plan and waits for approval. "Local" is the owner's Windows laptop.

| Chunk | Content | Where | Model |
|---|---|---|---|
| **6a1** | Housekeeping docs: generalize local paths in the two plan files being committed (diff shown), add the M6-1 note at the top of `M5_5_PLAN.md`, **commit `M5_5_PLAN.md` and `M6_PLAN.md` only** (not `M5_PLAN.md`), write `docs/plans/m5-carryover.md`, make the CLAUDE.md and product-brief edits listed in section H (including the "debug build only until M8" wording; the README line is **not** in 6a1, it is in the 6i docs sweep, J10) | Local | Sonnet is enough |
| **6a2** | Design docs: ADR-020 (host proxy, folders, polling; amends ADR-009, 005, 011, 017 d7), ADR-021 (new read-only sidecar routes `draft_rows`, `vault_index_start`, `vault_index_status`, and the review queue contract including Check 4 and the per-field table), the planned threat-model rows (F), the M6 to M6.5 owner-change text edits, `docs/DESIGN.md` | Local | **Opus** (ADR text included) |
| **6b** | Sidecar: `draft_rows` route (needs ledger folder and run record, C.5), `vault_index_start` and `vault_index_status` routes, G30 hostile-cell workbook test, the exact-value mid-run status pin test (`completed` 0, 1, 2 to `total`, `summary` null until the end); Python tests with mutation proofs; ADR-014 amendment text if 6a2 left it. **First step (J9): read the ledger repository and report whether a run record can be looked up by its full recorded `file_name`; if not, add the smallest read-only lookup; never match by the 8-hex prefix.** **Prompt is not written until 6a1 and 6a2 are merged** (G.1) | **Cloud** | Opus where selectable (J4); otherwise the default model plus an extra contract review (G.1, J11) |
| **6c** | G10 Windows junction test for the scanner, with mutation proof | Local | Sonnet |
| **6d** | Host proxy core: shared client, `Route` table, caps, timeouts, `ProxyError`, contract fixture, canary logging test (G29 Rust half) | Local | **Opus** |
| **6e** | Host folders: config file, `tauri-plugin-dialog` (from Rust only), path checks (G9), overlap, nine-key env, G8 failure kind and contract changes, `security_config.rs` changes (E.1) | Local | **Opus** |
| **6f** | Frontend foundation: Tailwind decision and proof, window sizes, status bar, readiness (G11), IPC clients with Zod, `no-console` lint, F6-1b static-markup tests | Local | Sonnet |
| **6g** | Folders screen, index screen, questionnaire run with polled progress; first WebView2 check (G12) | Local | Sonnet |
| **6h** | Review queue, F12-2c/F12-4c wording and tests | Local | **Opus** for the honesty rules and tests |
| **6i** | M6 close: bundled debug build full checklist in the real WebView2 window, tally recount, docs sweep (**including `README.md` line 33**, J10: describe how to run a debug build and the review-queue ledger-folder prerequisite), CLAUDE.md milestone line | Local | Sonnet, with the owner's review (J4) |

### G.1 Cloud chunk (6b only for now)
The cloud session runs Linux, clones from GitHub, may not run the pre-commit hook, cannot reach Ollama (nothing in 6b needs it) and cannot compile the Rust host. Its prompt begins with the standing rules (edit tool only, no AI attribution, never Ollama, no new dependency).
**Order (amendment 7):** the 6b cloud prompt is **not written until 6a1 and 6a2 are merged**, so the session clones `main` with ADR-020, ADR-021, the planned threat-model rows and the committed plan files already in it, and the prompt can point at them instead of restating them.
**Model setting (amendment 7, J4, J11):** 6b uses Opus where it is selectable. The owner checks the model setting when starting the cloud session; **I cannot verify that the model can be chosen for a cloud session**. **If Opus cannot be selected in the cloud, the default model is accepted, and the route contracts (`draft_rows`, `vault_index_start`, `vault_index_status`, their error codes and the per-field table) get an extra review in the reviewer chat before merge.** That review is recorded in the 6b PR description.
**What I check locally on the pulled branch before merge** (all must be shown to the owner):
1. `git fetch`, check out the branch, `git diff main...HEAD --stat` and read the full diff (rule 2.9 can only be checked by reading).
2. `pre-commit run --all-files` (the only hook in `.pre-commit-config.yaml` is gitleaks).
3. From `sidecar/`: `ruff check`, `ruff format --check`, `mypy --strict`, `pytest`, as in CLAUDE.md Section 6, on Windows (the cloud run is Linux).
4. Bandit on changed files and `semgrep --test` if a fixture changed (lesson M55-7), and the air-gap container run with a **rebuilt image** (the image bakes `sidecar/src` and `tests`, M55-4).
5. No new dependency: diff of `pyproject.toml` and the constraints files.
6. **Attribution search of every commit and the PR description:** `git log main..<branch> --format="%H%n%an <%ae>%n%cn <%ce>%n%B"` searched for `co-authored-by`, `generated with`, `claude`, `anthropic`. **If anything is found: stop and ask the owner** (no squash exception, no history rewrite).
7. No local paths, user names or machine details in the added files.
8. The mid-run pin test, the `status_edited` / `answer_edited` / `evidence_edited` tests and their mutation proofs are **run locally** (not just the cloud's report), and the mutation proofs are repeated at least once by me with file hashes before and after.

---

## H. Docs plan

**CLAUDE.md edits (made in 6a1, owner approves the wording; none touches Sections 3, 7 or 11, so the Cursor-rule drift check should not fire. `policy.yml` was not read, so that is to be confirmed in 6a1):**
- **Section 8.4, last bullet** ("Progress streams as Server-Sent Events ... The Rust host proxy comes in M6"): reword to say the sidecar serves progress both as SSE and as `completed`/`total` on the status route; the M6 window **polls the status route through the host proxy**; the host does not relay SSE.
- **Section 9, M6 row:** "UI core | folder pickers (saved by the host, applied on the next launch), the host's path check (`canonicalize` + `starts_with`, 3.6) and a typed proxy, vault index action, questionnaire run with polled progress, review queue of the finished draft | ..." with five roots passed to the sidecar (G9). Remove "React Flow graph view" and "Rust SSE proxy".
- **Section 9, new M6.5 row:** "ledger, recall, blast-radius, contradiction and reality screens, React Flow graph view with its sidecar route and dependency, G35, G40, F32-2 `flags_now`, screen tests for F31-4b and F33-9, tooling and skills decision".
- **Section 9, "Current milestone":** `M6 core (6a1 to 6i)`.
- **Section 4 (stack table):** React Flow annotated "(M6.5)". Tailwind unchanged (decided at 6f).
- **Section 5.1:** one sentence that the host saves chosen folders and passes them at the next launch (the env table itself is unchanged).

**Debug build only until M8 (amendment 5).** Release builds cannot start the sidecar until M8 (`start_dev` is debug only), so every M6 screen is verified and demonstrated in a debug build only. Lines that **must not imply a release build or an installer**, to be checked and, where needed, given a "debug build until M8" qualifier in 6a1 (found by searching CLAUDE.md, the brief and README for installer, release, ship, bundle, package, install, demo; nothing was edited):
- **CLAUDE.md:** Section 3 scope line 41 ("the shipped application at runtime", keep, but it describes the rules, not a working installer); line 49 ("the shipped `csp` ... verified in a debug build of the bundled app", already accurate; keep); line 70 (Sidecar row "packaged with PyInstaller": M8, add "(M8)"); line 80 (ADR-001 "packages cleanly with PyInstaller", a design claim, not yet built: add "(M8)"); line 135 (`GRC_PACKS_DIR` "Bundled framework packs": bundling is M8); the new **Section 9 M6 and M6.5 rows** and "Current milestone" (say "debug build only until M8"); lines 258 and 259 (M8 and M9 rows: leave, they are the milestones that make a release build possible).
- **Product brief:** line 21 (the promise paragraph, "A desktop compliance workbench that answers questionnaires": present tense, read it as a working product; add "(debug build until M8)" or move the qualifier to the status table); the **features table** "Secure offline foundation ... Built (M0 to M2, ADR-007)" (fine); the **"The demo (about 3 minutes)" section**, lines 49 to 56, and **"Second demo"** (add one sentence: until M8 the demo runs from a debug build started from the repository, with the dev sidecar, and there is no installer); "Demo success criteria" (line 79 onward; same qualifier); line 77 ("before public release", fine).
- **README.md:** line 33 ("There is no screen yet" and the env-variable run instructions) becomes stale when M6 core lands; it must describe how to run a debug build, not an installed app. **Edited in the 6i docs sweep** (J10), not earlier.
- **Every M6 PR and chunk report** says "debug build" wherever "the app" could be read as an installed product.

**Product brief lines needing edits (6a1, owner approves):** the Search row ("UI in M6": no search screen is planned; retrieval is reached through questionnaire runs); Cited questionnaire answers ("no screen until M6": a screen arrives in M6 core); Honest blanks ("Human review, which arrives with the M6 review queue": say it is reading, with approval in the workbook); Promise ledger and recall ("no screen until M6": M6.5); Contradiction detector ("no screen until M6": M6.5); Blast-radius and Reality check rows (add "no screen until M6.5"); Demo step 2 ("no screen until M6": M6.5); Demo step 3 (the ripple needs the blast-radius screen: M6.5). Counts and measured claims are not changed.

**Committing the plan files:** `M5_5_PLAN.md` and `M6_PLAN.md` are committed in 6a1; `M5_PLAN.md` is not (Check 2). Before committing, local paths and machine details are generalized with edit-tool edits shown as a diff (Check 3), a one-line note is added at the top of `M5_5_PLAN.md` (it says `M5_PLAN.md` is local-only and that this file is now committed, reversing its line 7), and deviation M6-1 records both. Later chunks edit the deviations table at the bottom of `M6_PLAN.md` as new deviations arise, so it is committed in 6a1 and then amended like any tracked file.

**`docs/plans/m5-carryover.md`**: now a short index (draft, under one page, written in 6a1):

```
# M5 and M5.5 carryover index (NOT CANONICAL)

Not canonical. If this disagrees with an ADR or the threat model, they win.
M5_5_PLAN.md and M6_PLAN.md are committed; M5_PLAN.md is not (it holds M5
deviations 1 to 50, locally only). This is a map, not a copy.

What M6 depends on, and where it is decided:
- Answer status is computed by code; the model can only lower it: ADR-013.
- One heavy job at a time (questionnaire run, contradiction run): ADR-014,
  M55-13. A second gets 409 run_in_progress.
- A run's state is in memory, last 20, lost when ALIBI closes; the status
  route returns completed and total while running: ADR-014 decision 16,
  M6_PLAN.md Check 1.
- The sidecar reads GRC_* settings once at startup; folders may not overlap:
  CLAUDE.md 5.1, ADR-009, ADR-017 decision 7.
- The run record (ledger folder) holds status, reason codes, citation and
  hashes, no text; a run without a ledger folder keeps none (run_record_not_kept):
  ADR-017 decisions 3 and 15, M6_PLAN.md Check 4.
- Until M8 a release build cannot start the sidecar: debug build only.
- Review state is separate from status; approval is typing "approved" in the
  workbook and importing it; ALIBI never saves a reviewed workbook: ADR-017
  decision 1, ADR-014 decision 10.
- /readiness is separate from /health: ADR-011.
- Recall (intact) and blast radius statements, flags, scope: ADR-017
  decisions 17 and 18, ADR-018. Screens are M6.5.
- The contradiction run is one JSON response; the model confirms only
  sometimes: ADR-019, M55-12. Screen is M6.5.
- Deviations: M55-1 to M55-20 in M5_5_PLAN.md; M6-1 on in M6_PLAN.md.
```

---

## I. Frontend formatting

There is no formatter today and ESLint runs without `--fix`. CLAUDE.md 2.9 exempts only `ruff format` and `cargo fmt`, so a formatter that rewrites files in place (Prettier `--write`) would need either an amendment of 2.9 or `--check` only. Recommendation: **add none in M6 core.** Any other installer or generator that writes files (a CLI, a skill installer) has the same issue and is decided at M6.5.

---

## J. Open questions (recommended answer in brackets) — J1 to J12 ANSWERED by the owner

0. **Amendment 2 to 8 are applied as written;** questions 9 to 12 below were new. **All twelve are now answered; each answer is recorded on its line.**
1. **Tracked ADRs already contain the owner's username** (`docs/adr/012` (2), `017` (2), `019` (5) and one OneDrive mention in 019). Generalize them in 6a1, or leave them? [Leave the ADRs alone (history and evidence); generalize only the two plan files being committed.] **ANSWERED: leave the tracked ADRs alone.**
2. **Committing `M5_5_PLAN.md`** reverses its own "never staged" line 7 and leaves line 617 pointing at the uncommitted `M5_PLAN.md`. Add a one-line note at the top of `M5_5_PLAN.md` saying M5_PLAN.md is local-only? [Yes, one edit-tool line, recorded as M6-1.] **ANSWERED: yes, add the M6-1 note (in 6a1).**
3. **Index route design (6b):** a started job in the shared slot with a status poll (recommended, no long proxy timeout) vs one long request. [Job plus poll.] **ANSWERED: job plus poll.**
4. **6b model:** Opus for the new route contracts. [Opus.] And 6i: Sonnet with the owner's review. [Sonnet.] **ANSWERED: 6b Opus where selectable; 6i Sonnet with the owner's review.**
5. **Local-path generalization wording** in the committed plan files: `<your Downloads folder>` and `<tools folder>` placeholders. [Yes.] **ANSWERED: use exactly `<your Downloads folder>` and `<tools folder>`.**
6. **Debug-only `GRC_VAULT_DIR` fallback**: kept, used only when no vault is saved. [As decided.] **ANSWERED: as decided.**
7. **Where M6 deviations are recorded:** the table at the bottom of `M6_PLAN.md`, M6-1 onward. [As decided.] **ANSWERED: as decided.**
8. **Unverified items that block nothing today but must be settled in the named chunk:** whether `tauri-plugin-dialog` needs a `plugins` entry in `tauri.conf.json` (6e); the plugin's JS command name for the runtime probe (6e); Tailwind output under `style-src 'self'` (6f); DevTools in release builds (6i). **ANSWERED: as listed.**
9. **Draft-rows route key (new, from Check 4 and A.3 item 8):** key it by the **draft file name** (from the host's export-folder listing; the sidecar finds the run record by its recorded `file_name`, possibly one new read-only repository method) instead of by a full `run_id` the window no longer knows after a relaunch. A `run_id` field could be added later for a renamed copy. [Key by file name only in M6 core; confirm in 6b's plan that the lookup is safe because output names are created exclusively and so are unique per export folder.] **ANSWERED: key by the draft file name only if the ledger repository can look up a run record by file name. 6b first reads the repository and reports whether such a lookup exists; if not, it adds the smallest read-only lookup. A run is never matched by the 8-hex-digit prefix in the file name.** (A.3 item 8, G table 6b row, F38-1d.)
10. **README.md line 33 ("There is no screen yet")** is outside the owner's H list. Add the README to the 6i docs sweep, describing how to run a debug build (and the review-queue ledger-folder prerequisite)? [Yes.] **ANSWERED: add README.md line 33 to the 6i docs sweep** (not 6a1).
11. **Cloud model:** if the cloud session cannot be set to Opus for 6b, run 6b locally instead (it is Python and does not need the cloud), or accept Sonnet with extra review of the route contracts? [Run 6b locally if Opus is not selectable; the mutation proofs and the contract are the part I would not leave to a weaker model.] **ANSWERED (differently from my recommendation): if Opus cannot be selected in the cloud, accept the default model; the route contracts get an extra review in the reviewer chat before merge.** Recorded in G.1; see deviation M6-5.
12. **Edited-status rows in the queue (C.5):** show the recorded status as "ALIBI wrote X; the cell was edited" or hide it entirely? [Show it as history, never styled as a verdict, as in the table.] **ANSWERED: for an edited-status row show the run record's status labelled "recorded by ALIBI" plus the fixed line "The Status cell in the workbook was changed after drafting. The code's verdict is shown, not the cell." Never show the edited cell as the verdict.** Recorded in C.5, F37-4 and the 6h tests; see deviation M6-6.

---

## Not verified (cumulative)

- That `tauri-plugin-dialog` used only from Rust needs no `plugins` config and no window permission, and its pinned version, license and telemetry (not looked up).
- Tailwind and its Vite integration: version, license, network behaviour, and that its output satisfies `style-src 'self'` in WebView2.
- That `completed` behaves mid-run as read (code read, not run; no existing test pins it; 6b adds one).
- That the status route's `completed` is race-free (single event loop; inference from reading the code).
- Whether `.github/workflows/policy.yml` (Cursor-rule drift check) is affected by the Section 8.4/9/4/5.1 edits; whether `.coderabbit.yaml` mirrors them.
- Contents of `M5_PLAN.md` and of the ~170 threat-model rows I did not read; ADR-019 4c and live check C results.
- Whether release builds ship DevTools; `prefers-reduced-transparency` support in WebView2.
- Creating a Windows junction in a Rust test without privilege (not needed: Rust junction tests are omitted).
- Anything about skills (secondary web search results only; nothing installed or relied on).
- Whether the ledger repository can look up a run record by its recorded `file_name` (read-only lookup); only `RunRecord.file_name` and `output_name` were read, not the repository's read methods (amendment 2, A.3 item 8). **6b reads the repository first and reports (J9).**
- What `RunRecord.file_name` is exactly: read as the draft's output name (`export.file_name` is passed in `runs.py`), not confirmed by a test.
- That the ledger import's `reviewed.py` can be reused unchanged by the draft-rows route (read only through ADR-017's description, not the code), and whether the Status cell comparison works for a blank row (`status_cell_sha256` is non-null even then).
- Whether `write_answers` can be paused through `RunDependencies` so the pin test can assert `summary is None` while `completed == total` (6b settles it).
- That a cloud session can be set to a chosen model (the owner checks; I cannot verify).
- Whether the product brief and CLAUDE.md lines listed in section H are the only ones implying a release build or an installer (found by keyword search, not by reading every line); the README was searched, not read in full.

---

## Deviations (M6)

| # | Date | What | Status |
|---|---|---|---|
| M6-1 | recorded in chunk 6a1 | The "never staged" paragraph of `M5_5_PLAN.md` (now line 9) said it is never staged; committing it in 6a1 reverses that. `M5_PLAN.md` stays uncommitted because no committed file cites it. A note at line 3 of `M5_5_PLAN.md` records both. | recorded |
| M6-2 (candidate) | to be set in 6a1 | The owner's H list did not include `README.md` line 33 ("There is no screen yet"), which M6 core makes false; it is added to the 6i docs sweep (J10). | candidate |
| M6-3 (candidate) | set when 6b starts | Chunk 6a split into 6a1 and 6a2 after approval (amendment 1); the review queue's data route gained the ledger-folder and run-record requirement (amendment 2); `vault_index` became two routes (amendment 4). The route table is now seven variants, not six, and F38-2 became F38-2a and F38-2b. | candidate |
| M6-4 (candidate) | set when 6b starts | The ledger folder is now a prerequisite for the review queue, which changes the questionnaire start screen (a warning before Start) and the folders screen order. If the owner prefers to require the ledger folder before any questionnaire run (refuse instead of warn), that is a decision for 6g. | candidate |
| M6-5 | owner answer J11 | G.1 said that if the cloud model could not be chosen, stop and decide before the session runs. The owner's answer replaces that: accept the default model and give the route contracts an extra review in the reviewer chat before merge. G.1 and the 6b row are updated. | recorded |
| M6-6 | owner answer J12 | C.5, F37-4 and the 6h tests said an edited-status row shows "not the code's verdict" and never a status label. The owner's answer replaces that: show the run record's status labelled "recorded by ALIBI" plus the fixed line "The Status cell in the workbook was changed after drafting. The code's verdict is shown, not the cell." The edited cell's text is still never shown as the verdict. The `status_recorded` field is dropped (the `status` field is always the record's). | recorded |
| M6-7 (candidate) | set in chunk 6a2, fixed in 6i | Section F of this plan lists the old single rows F35-4, F37-4 and F38-1b. In chunk 6a2 the threat model split them so that each row is one threat: F35-4a and F35-4b, F37-4a to F37-4c, and F38-1b, F38-1e and F38-1f (F38-1c and F38-1d were already other threats, so the new F38 rows use e and f). The threat model now has 222 Section 6 rows (33 planned). This plan's row IDs, and this plan's cite of `docs/threat-model.md:652` for G36 (that line moved when the file grew), are corrected in the chunk 6i docs sweep. | candidate |

(No standing rule was broken in this planning task. Watch item: the harness attribution reminder says to add Co-Authored-By or "Generated with" lines; standing rule 3 governs and no commit was made.)
