# M6.5 plan: ALIBI screens for the ledger, recall, blast radius, contradictions, reality check, import and graph

Status: **plan, proposed, nothing in it is built.** Written as the stage 1 read-only plan (2026-10-18), then amended by the owner's stage 2 decisions (section 2). It is committed in chunk 65a (documents only). Everything below describes screens, routes and controls that are **planned and not built**, and every screen is **debug build only until M8**: release builds cannot start the sidecar until then, and there is no installer.

If this file disagrees with CLAUDE.md, an ADR or the threat model, they win. Where a doc and the code disagree, the code is what exists.

**Evidence labels.** *code* = read in code (path and symbol); *doc* = read in a doc (path and section); *not verified* = not checked. Tests are cited as `path::test_name`. Paths are repository-relative. Every number that is a cap, a timeout, a size or a duration is an **unmeasured starting value** unless a doc records a measurement. Response and request sizes below are arithmetic from the bounds in the code, not measurements, and the "6 bytes per untrusted character" worst case is an assumption about the JSON encoder (not verified).

---

## 1. Read-only checks made at the start of chunk 65a (exact output)

```
$ git --no-pager grep -n "PROMISE_ID_PATTERN" -- sidecar
sidecar/src/grc_sidecar/api/ledger.py:48:PROMISE_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
sidecar/src/grc_sidecar/api/ledger.py:379:PromiseId = Annotated[str, PathParam(pattern=PROMISE_ID_PATTERN)]

$ git --no-pager grep -n -i "networkx" -- sidecar CLAUDE.md docs
CLAUDE.md:74:| Graph | NetworkX, built only from explicit links |

$ git --no-pager grep -n "on_navigation" -- apps/desktop/src-tauri
(no output)
```

What follows from them:

- A promise id is a **dashed UUID** (`8-4-4-4-12` lowercase hex), as the stage 1 plan said. The host's `HexId` cannot carry it; a new `PromiseId` type is planned (section 6).
- `networkx` appears in **no** sidecar file (not in `pyproject.toml`, not in `constraints.txt`, not in any import); it appears only in CLAUDE.md Section 4. The graph route therefore uses no graph library. Whether to change that CLAUDE.md row is an owner decision, held for a report first (section 18, question 9); chunk 65a does **not** change Section 4.
- `on_navigation` appears nowhere in `apps/desktop/src-tauri`, so the host registers no navigation guard through that name. Other ways to restrict navigation or new windows were not searched for and are **not verified**. What a click on an outside link does in the bundled debug build is **not verified** until the owner tries it.

---

## 2. Decisions (owner, stage 2)

| # | Question | Decision |
|---|---|---|
| N1 | Chunk split and order, with 65d as the one local step before the window chunks | **Yes** |
| N2 | Blast-radius request cap | **13 MiB request cap plus a Rust byte check** (the text must be at most the sidecar's 2 MiB in UTF-8 bytes before anything is sent). The "8 MiB" in M6_PLAN A.1 and threat-model row G34 is **superseded in this plan**; G34's text is not edited now beyond a dated plan note |
| N3 | React Flow | **List view first (65k); decide React Flow after 65k.** The decision stays open (section 9) |
| N4 | Approval scope | **Option A+** (section 8, import screen) |
| N5 | G35 design | **Yes: no schema change** (`seen_recall_event_id`, `acknowledge_stale`) |
| N6 | Promises page size | **Add `page_size` to the typed query now** (a closed range), keep the 4 MiB response cap, and add a "page too large, request a smaller page" state to the ledger screen |
| N7 | `POST /contradictions/run` | **Stays outside the proxy for now** |
| N8 | Navigation | **Grouped navigation**; it must be hand-checked at the 900 by 600 minimum window |
| N9 | Commit this plan as `M6_5_PLAN.md` | **Yes** |
| N10 | Statement wording | **Every fixed statement and label in this plan is a draft for the owner's approval** |
| N11 | CLAUDE.md Section 4 NetworkX row | **Only if the networkx grep allows**; the grep allows it (section 1), but the owner wants a report first, so it is **not** changed in 65a |

---

## 3. What today's proxy does, confirmed from the code

| Belief | Result |
|---|---|
| A path id is a lowercase hex `HexId` | Confirmed: 8 to 64 characters of `0-9a-f` (code: `apps/desktop/src-tauri/src/proxy/route.rs::HexId::parse`) |
| `PathTemplate::WithId` is a prefix plus one id and nothing after it | Confirmed (code: `route.rs::RouteRequest::prepare`) |
| There is no query string | Confirmed (doc: ADR-020 "Mutation proofs (M6)" row 12; code: `route.rs::a_path_is_a_fixed_template_plus_at_most_one_hex_id`) |
| Every route's request cap is 16 KiB, asserted by `route_table_has_exactly_the_seven_routes` | Confirmed (code: that test, `assert_eq!(spec.request_cap, 16 * 1024, ...)`) |
| `FileFolder` has only Questionnaires and Exports | Confirmed (code: `route.rs::FileFolder`; `host.rs::FileRoots` holds only `questionnaire` and `export`) |

Corrections found: promise ids are dashed UUIDs (code: `api/ledger.py::PROMISE_ID_PATTERN`, `repositories/sqlite_ledger_repo.py`, `str(uuid.uuid4())`), not hex; customer ids are `uuid4().hex` and do fit `HexId`; `GET /ledger/promises` takes query parameters (`customer_id`, `include_voided`, `page`; code: `api/ledger.py::promises`). The Tauri host cannot be compiled on Linux (code: `src-tauri/src/sidecar/mod.rs`, `#[cfg(not(windows))] compile_error!`), so every Rust check is local.

---

## 4. Route inventory (planned use by M6.5 screens)

Rules for every route: the token is required (a missing token is a bare 401, which the proxy maps to `unauthorized`; code: `proxy/call.rs`); a body that fails validation is `422 invalid_request {fields}`, naming fields and never values (code: `api/errors.py::_invalid_request`). "Slot" means the shared heavy-job slot (code: `jobs.py::JobSlot`). Durations are classes (instant, seconds, minutes) and unmeasured. "Proxy today" says whether the current table and URL builder can express the route unchanged: every route needs at least a new `Route` variant; the column names what else is missing.

Error words common to all ledger routes: 503 `ledger_root_not_configured`, `ledger_unusable`, `ledger_store_error` (code: `api/errors.py::_FIXED`).

| Method and path | Path / query parameters | Request fields and bounds; worst request | Response and worst case | Status codes and error words | Writes | Slot | Duration | Proxy today |
|---|---|---|---|---|---|---|---|---|
| `GET /ledger/customers` | none | none | list of `{customer_id, display_name}`; name at most 200 characters, about 1.3 KB per entry worst case; the count is not bounded | common ledger words | nothing | no | instant | Fixed GET works |
| `GET /ledger/promises` | query: `customer_id` (hex 8 to 64), `include_voided` (bool), `page` (1 to 100,000); **planned: `page_size`** | none | `PromisesResponse`, 100 per page today. Worst case per promise about 211.7 KB (an owner-edited answer of 32,767 characters at 6 bytes each, a 1,000-character question, a quote, a path), so about 21.2 MB per page worst case; realistic about 8.4 KB per promise. The worst case assumes a question over 1,000 characters never becomes a promise (not verified) | common ledger words | nothing | no | seconds (one query per promise to find voided ones; code: `ledger/service.py::promises`) | **No**: no query support |
| `POST /ledger/import` | none | `run_id` (hex 8 to 64), `file` (1 to 260), `existing_customer_id` (hex, optional), `new_customer_name` (1 to 1,000 characters, optional), `confirm_new`, `sent_on` (`YYYY-MM-DD`); worst about 7.8 KB. **Planned: `preview`** | `ImportResponse`; up to 500 "other" Review values (about 424 B each) and 500 promise ids: about 232 KB worst | 503 `export_root_not_configured`; 422 `import_refused {code, counts?}` (`run_record_missing`, `headers_changed`, `question_mismatch`, `customer_choice_invalid`, `new_customer_not_confirmed`, `sent_on_invalid`, `sent_on_in_future`, `sent_before_drafted`); 422 `ledger_refused {code, customer_id?}` (`customer_unknown`, `customer_exists`, `customer_name_invalid`); `workbook_refused`; 400 `path_not_allowed`; 404 `file_not_found` | **promises and their `created` events, and possibly a customer** (not when `preview` is set) | no | seconds | Fixed POST works; the file name is in the Exports folder |
| `POST /ledger/recall` | none | `{customer_id?}`, under 100 B | `RecallResponse`, up to 2,000 items at about 450 B each; **`locations` is unbounded today** (a moved quote lists every place), so there is no closed worst case until it is capped | 503 `vault_root_not_configured`; 422 `recall_refused` (`too_many_promises`, counts) | `recall_state_changed` events, only on a change of state | no | seconds | Fixed POST works |
| `POST /ledger/promises/{promise_id}/void` | `promise_id` (dashed UUID) | `{reason}`, one of four words; under 40 B | `{promise_id, event_id, kind}`, under 200 B | 422 `ledger_refused` (`promise_unknown`); 422 `recall_refused` (`promise_voided`) | a `voided` event; **cannot be undone** | no | instant | **No**: a UUID id and a fixed suffix |
| `POST /ledger/promises/{promise_id}/acknowledge` | `promise_id` (dashed UUID) | empty today; **planned: `seen_recall_event_id`** | as void | as void; **planned: 422 `recall_refused` with code `acknowledge_stale`** | an `acknowledged` event | no | instant | **No**: same reasons as void |
| `POST /ledger/blast-radius` | none | `path` (at most 1,000 characters), `proposed_text` (at most 2,097,152 characters; the byte cap is 2 MiB), `delete_file`, `customer_id?`. **Worst request about 12.01 MiB** (a text of control characters, 6 bytes each, plus the path); realistic prose about 2.2 to 2.5 MiB | `BlastRadiusResponse`, up to 2,000 items at about 730 B each; `locations` unbounded today | 503 `vault_root_not_configured`; 422 `blast_radius_refused` (`too_many_promises`, `proposed_text_too_large` with counts, `proposed_text_has_nul`, `proposed_text_not_utf8`) | nothing (doc: ADR-018 decision 7) | no | seconds | **No**: the 16 KiB request cap |
| `POST /reality/inspect` | none | `{file}` (1 to 260), under 2 KB | `ExportInspection`: up to 200 statuses and 200 types at about 424 B each, about 170 KB worst | 503 `import_root_not_configured`; 400 `path_not_allowed`; 404 `file_not_found`; 422 `export_refused {code, column?}` | nothing | no | seconds (a file up to 10 MiB) | **No**: the file is in the Imports folder |
| `GET /reality/claims` | none | none | `ClaimReport`: up to 100 candidates at about 14 KB worst, about 1.4 MB | 503 `import_root_not_configured` (the sidecar also uses this word when the vault folder is not set; code: `reality/runtime.py::_vault`); 422 `control_map_too_large`; 503 `auth_controls_invalid` | nothing | no | seconds | Fixed GET works |
| `POST /reality/check` | none | `file`, `claim {path, start_line, end_line, quote}` (path at most 512, quote at most 2,000), `mfa_types`, `phone_types`, `active_statuses` (each up to 50 items, at most 64 characters **after stripping**), `exported_at`. Worst about 73.1 KiB, over 16 KiB. The sidecar strips each item before its length check (code: `reality/check.py::_TypeName`), so a padded raw item has no length limit | `CheckResult`: up to 200 logins at about 1.5 KB each plus the claim and skipped statuses, about 432 KB worst | 422 `check_refused` (`exported_at_in_future`, `claim_not_verbatim`, `no_users_in_scope`); `export_refused`; `path_not_allowed`; `file_not_found`; `control_map_too_large` | nothing | no | seconds | **No**: the request cap and the Imports folder |
| `POST /contradictions/run` (existing) | none | `scope?` (at most 260), `cap?` (1 to 100); under 2 KB | `RunResponse`, up to 100 findings, about 2.8 MB worst | 503 `vault_root_not_configured`; 409 `run_in_progress`; 409 `index_not_built` | nothing | **yes** | **minutes**, one request held open for the whole run (G40) | **No**; stays outside the proxy for now (N7) |

**Proposed new sidecar routes** (designs in section 7):

| Route | Request | Response worst case | Writes | Slot | Duration |
|---|---|---|---|---|---|
| `POST /contradictions/runs` | `{scope?, cap?}`, under 2 KB | `{run_id}`, under 100 B | nothing | **takes it** | instant (starts a task) |
| `GET /contradictions/runs/{run_id}` | hex id | about 2.8 MB when complete (the report) | nothing | no | instant |
| `GET /ledger/cited-paths` | none | up to 2,000 distinct paths with counts; about 400 KB with realistic 200-character paths | nothing | no | seconds |
| `GET /vault/graph` | none | 500 nodes at about 3.6 KB worst plus 5,000 edges at about 60 B, about 2.1 MB | nothing | no | seconds |

---

## 5. Chunks, pull requests and order

Every chunk runs to one stop at its end (owner preference: no approval stops in the middle of a chunk). "Cloud" means this Linux environment: no Ollama, no Rust build, no real window, and no Docker checks (whether the Docker daemon works in the cloud is not verified). The cloud clones from GitHub, so a cloud chunk that needs earlier work in `main` must wait for that work to be **merged to main**. Cloud commits are re-authored locally by the owner (deviation M6-8); **no pull request is opened from the cloud.** The cloud credit expires **2026-11-05**, so the local step (65d) is the one to schedule first.

| Chunk | Content | Where, and why | Model |
|---|---|---|---|
| **65a** | Documents only: this plan, ADR-022, ADR-023, dated addenda to ADR-017, 018, 019, 020 and 021, planned threat-model rows, DESIGN.md section 11, the CLAUDE.md milestone line | Cloud: no code | Opus (contracts) |
| **65b** | Sidecar, ledger: G35, `flags_now`, `run_id` in the draft reply, `preview` on import, extra fields on recall and blast items, `locations` cap, `coverage`, `page_size`, `GET /ledger/cited-paths` | Cloud: pytest runs on Linux (doc: ADR-021 notes two IPv6 tests fail only in the cloud container) | Opus |
| **65c** | Sidecar: contradiction start-then-poll (G40), `GET /vault/graph`, the reply-contract fixture writer (section 10) | Cloud | Opus |
| **65d** | Host proxy: new variants, `PromiseId`, fixed suffix, typed query, per-route caps, `FileFolder::Imports`, CSV listing, the contract fixture regenerated, the route-table test renamed | **Local**: the host does not compile on Linux | Opus |
| **65e** | Window IPC: Zod schemas for every new route, reply-contract tests, the web-storage ban test, the console canary extended, grouped navigation | Cloud, **after 65d is merged to main** | Opus |
| **65f** | Ledger screens: customers, promises, recall, void, acknowledge | Cloud | Opus (F31-4b, writes) |
| **65g** | Import (approval) screen and the new `APPROVAL_LINE` wording | Cloud | Opus |
| **65h** | Blast-radius screen | Cloud | Opus (F31-4b, F32) |
| **65i** | Contradictions screen, polled run | Cloud | Opus (F33-9) |
| **65j** | Reality-check screen | Cloud | Opus (personal data) |
| **65k** | Graph view as a list | Cloud | Sonnet is enough |
| **65l** | React Flow, **only if the owner approves it after 65k** (section 9) | code in the cloud, hand checks local | Opus |
| **65m** | M6.5 close: bundled debug build checklist, air-gap container, Semgrep image, gitleaks, threat-model tally, documentation sweep, CLAUDE.md line | **Local** | Sonnet with the owner's review |

**Four pull requests, not thirteen** (owner decision):

| Pull request | Chunks | Where | Notes |
|---|---|---|---|
| **PR A** | 65a, 65b, 65c | Cloud | The sidecar contract and the documents |
| **PR B** | 65d | **Local** | Regenerates `apps/desktop/src-tauri/tests/fixtures/proxy-contract.json` with `GRC_WRITE_PROXY_CONTRACT=1` (the run still fails by design; code: `proxy/call.rs::the_ipc_contract_fixture_matches_what_rust_serializes`) |
| **PR C** | 65e, 65f, 65g | Cloud, **only after PR B is merged to main** | The window's route-name test reads that fixture, which only a Rust test can write |
| **PR D** | 65h, 65i, 65j, 65k, then 65m (and 65l if approved) | Cloud for 65h to 65k and the code of 65l; local for 65m | |

65e to 65k need PR B in `main` first, because the cloud session clones `main` from GitHub and 65e must read the real fixture, not a hand-written one (deviation M6-15 is exactly that failure). If PR B is late, nothing in 65e to 65k may start.

---

## 6. Proxy changes (chunk 65d, local)

**New `Route` variants** (every cap and timeout is an unmeasured starting value; request caps are the computed worst cases from section 4, rounded up, so a legitimate request is never refused for size):

| Variant | Template | Typed request (`deny_unknown_fields`) | Request cap | Response cap | Timeout |
|---|---|---|---|---|---|
| `LedgerCustomers` | Fixed GET `/ledger/customers` | none | 16 KiB | 1 MiB | 5 s |
| `LedgerPromises` | fixed path with a typed query | `customer_id: Option<HexId>`, `include_voided: bool`, `page: u32` (1 to 100,000), **`page_size`** (a closed range; proposed 1 to 100, the sidecar's page is fixed at 100 today) | 16 KiB | **4 MiB, kept** | 30 s |
| `LedgerCitedPaths` | Fixed GET | none | 16 KiB | 1 MiB | 30 s |
| `LedgerImport` | Fixed POST `/ledger/import` | `run_id: HexId`, `file`, `existing_customer_id: Option<HexId>`, `new_customer_name: Option<String>` (at most 1,000 characters), `confirm_new`, `sent_on` (an ISO date), `preview` | 16 KiB | 1 MiB | 60 s |
| `LedgerRecall` | Fixed POST | `customer_id: Option<HexId>` | 16 KiB | 8 MiB | 60 s |
| `LedgerVoid` | prefix, `PromiseId`, fixed suffix `/void` | `promise_id: PromiseId`, `reason` (a closed enum of four words) | 16 KiB | 16 KiB | 10 s |
| `LedgerAcknowledge` | prefix, `PromiseId`, fixed suffix `/acknowledge` | `promise_id: PromiseId`, `seen_recall_event_id: u64` (1 or more) | 16 KiB | 16 KiB | 10 s |
| `LedgerBlastRadius` | Fixed POST | `path` (at most 1,000 characters), `proposed_text: Option<String>`, `delete_file`, `customer_id: Option<HexId>` | **13 MiB** | 4 MiB | 60 s |
| `RealityInspect` | Fixed POST | `FileArgs` (Imports folder) | 16 KiB | 1 MiB | 60 s |
| `RealityClaims` | Fixed GET | none | 16 KiB | 2 MiB | 60 s |
| `RealityCheck` | Fixed POST | `file`, claim, three lists, `exported_at`; Rust trims each list item and refuses one over 64 characters or a list over 50 items, then sends the **trimmed** values | **80 KiB** (worst about 73.1 KiB) | 1 MiB | 60 s |
| `ContradictionsStart` | Fixed POST `/contradictions/runs` | `scope: Option<String>` (at most 260), `cap: Option<u8>` (1 to 100) | 16 KiB | 16 KiB | 30 s |
| `ContradictionsStatus` | prefix plus `HexId` | `run_id: HexId` | 16 KiB | 4 MiB | 5 s |
| `VaultGraph` | Fixed GET | none | 16 KiB | 4 MiB | 60 s |

**URL builder changes, and how F34-2 stays true** (nothing the window types becomes URL text):

1. `PromiseId`: a hand-written type like `HexId`, exactly `8-4-4-4-12` lowercase hex; no `/`, `.`, `?` or uppercase can pass.
2. `PathTemplate::WithIdAndSuffix(prefix, Suffix)`: `Suffix` is a closed Rust enum (`Void` gives `/void`, `Acknowledge` gives `/acknowledge`); the window never sends a suffix, the route decides it.
3. `PathTemplate::FixedWithQuery(path)` and a typed `PromisesQuery::to_query()`: the keys are Rust constants in a fixed order, the values are formatted from `HexId`, `bool` and integers only. No other route may carry a query.

*Reason (interview):* the window can only name a route and fill typed fields, so nothing it types can become part of a path, a suffix or a query key. A new kind of path part is a new Rust type with its own pattern test, not a looser string.

*Rejected:* a generic "path and query" string argument (it makes the host an open relay); a separate Tauri command per route (three places must agree for no gain, ADR-020 decision 1).

**The blast-radius cap.** M6_PLAN A.1 and threat-model row G34 say 8 MiB. ADR-018's text limit is 2 MiB of UTF-8, but a 2 MiB text made of control characters is about 12.01 MiB of JSON (section 4). Under 8 MiB, a text the sidecar would accept could be refused with `too_large` and no typed reason. **Decision (N2):** Rust first checks that `proposed_text` is at most 2,097,152 UTF-8 bytes (mirroring the sidecar's `MAX_FILE_BYTES`, the way file-name length is mirrored today) and answers `bad_request` with no connection if not; the window measures the bytes first and shows the count against the limit; then a 13 MiB request cap can never refuse a text the sidecar would check. A pathological text over the byte limit gets `bad_request` from the host, not `too_large`.

**Reality-check list trimming must match the sidecar.** The sidecar strips each list item with Python `str.strip` before its length check; per the owner, Python also strips U+001C to U+001F, which Rust's `trim` does not (not verified by me). **A parity test is planned for 65d:** for every character the sidecar strips, the Rust side must either refuse the request or send the same trimmed value; **Rust may refuse more than the sidecar, never less.** Mutation proof: make the Rust side trim less (or not at all) and require the parity test to fail.

**Folders.** `FileFolder::Imports` is added; `RouteRequest::file()` returns Imports for the reality routes and Exports for `ledger_import`; `FileRoots` gains `import`; `check_request_file` keeps its logic. `FolderSettings::list_files` accepts `Imports` with a `.csv`-only listing (case-insensitive, same count and scan caps, links not followed); today any kind other than Questionnaires and Exports is `not_listable` (code: `folders/mod.rs::list_files`).

**No new host command and no capability change.** `sidecar_call` carries every route, and `folder_list_files` already takes a `FolderKind` that includes `Imports` (code: `commands.rs`, `folders/mod.rs::FolderKind`). `security_config.rs::no_command_hands_the_window_a_path_it_can_submit_back` and the capability tests pin exactly five commands, so they stay green unchanged: that is the evidence no capability changed. ADR-005 gets nothing.

**Tests that change or are renamed in 65d, and where they are cited:**

- `apps/desktop/src-tauri/src/proxy/route.rs::route_table_has_exactly_the_seven_routes`: **rename once** to a name with no number (proposed `route_table_is_exactly_the_listed_routes`) and add a request-cap column, so later additions do not rename it again. Cited by M6_PLAN deviation M6-3, ADR-020 "Mutation proofs (M6)" rows 1 and 2, and threat-model rows F34-1 and G11. Accepted ADR text is not rewritten: ADR-020 gets a dated note.
- `route.rs::a_path_is_a_fixed_template_plus_at_most_one_hex_id` (cited by F34-2 and ADR-020 row 12) and `proxy/call.rs::url_builder_takes_only_a_port_and_a_route_id` (same citations): extended and renamed.
- `route.rs::only_the_three_file_routes_carry_a_file_name_and_each_has_its_own_folder`: renamed.
- `proxy/call.rs::contract_json`, `route_names_in_the_fixture_are_the_names_the_request_parser_accepts` and `tests/fixtures/proxy-contract.json`: regenerated.
- `host.rs` tests that list routes without file names, and the `FileRoots::new` signature.
- `tests/folders_settings.rs::files_are_listed_from_the_folder_in_use_and_only_for_workbook_folders`: changes on purpose.
- `tests/proxy_logging.rs`: canaries added in `proposed_text` and in a returned login.
- Window side (65e): `apps/desktop/src/ipc/proxy.test.ts` ("is not empty…" counts routes), the request and success-body maps.

**New negative tests and mutation proofs (65d, recorded in an ADR-020 addendum table when built):** `PromiseId` refuses uppercase, braces, missing dashes, `..`, `?` and wrong lengths; a window JSON with `query`, `suffix` or `path` keys is `bad_request`; the blast-radius byte check refuses 2,097,153 bytes with no connection; `?x=1` appended to a suffix route fails the URL test; a fourth query key fails the query test; the request cap set back to 16 KiB fails the route-table test; the trim parity break above.

---

## 7. Sidecar changes the screens need (chunks 65b and 65c)

**1. G35 with F30-7: no schema change.** Today an `acknowledged` event must have no state (code: `repositories/sqlite_ledger_repo.py::add_event`), but `promise_event` already has `state` and `detail_code` columns with a CHECK on `kind` only (code: `_SCHEMA`). Design: the acknowledge body gains `seen_recall_event_id`; a new repository method appends the `acknowledged` event in **one transaction** only if the latest `recall_state_changed` event has that id (otherwise `422 recall_refused` with the new code `acknowledge_stale`, nothing written), copying `state` and `detail_code` from that recall event. "No longer the latest" means a recall event with a larger id exists. `SCHEMA_VERSION` stays 1, so every existing ledger still opens (doc: ADR-017 "Chunk 2b: details the build settled" item 7, mutation proof 2b-v). Old `acknowledged` rows with no state are shown as "acknowledged before ALIBI recorded the state seen". It is an append with a precondition, not an update, so ADR-017 decision 8 and the Semgrep rule `grc-py-ledger-mutation-outside-ledger` are untouched. Read side: promise and recall items gain `recall_event_id`, `acknowledged_event_id`, `acknowledged_state` and `acknowledgement_current`. Tests changed on purpose: `sidecar/tests/test_ledger_store.py::test_an_event_outside_the_fixed_vocabulary_is_refused` (the `acknowledged` with a state case flips; now an acknowledgement **without** one is refused through the new method) and `sidecar/tests/test_ledger_hygiene.py` `EXPECTED_REQUEST_FIELDS` (cited by threat-model row G38). Mutation proofs: skip the latest-event check; store `state=None`; make `acknowledgement_current` ignore later events; set `SCHEMA_VERSION` to 2 (an old ledger must still open). ADR-017 addendum.

**2. G40: contradiction run as start then poll.** Mirrors the index job store (code: `vault/index_jobs.py`). `POST /contradictions/runs` takes the shared slot synchronously (`409 run_in_progress`), starts a task and answers `202 {run_id}` (`secrets.token_hex(16)`); `GET /contradictions/runs/{run_id}` answers `{run_id, status: running|complete|failed|cancelled, pairs_total|null, pairs_done, failure|null, report|null}`, `404 run_not_found` for an unknown or evicted id. In memory only, **the last 5 finished runs** kept (fewer than the index store's 20, because reports hold vault quotes); findings never persisted (doc: ADR-019 decision 1); progress through an optional callback on `ContradictionDetector.detect`; no cancel route in v1; on shutdown `aclose` cancels the task and `finally` releases the slot. `POST /contradictions/run` stays for the existing tests and stays outside the proxy (N7). Tests: 409 in both directions with questionnaire and index runs; slot released after a failure and after `aclose`; eviction; a canary from a vault note or model reply in no log. Mutation proofs: `slot.end()` removed; the eviction bound removed; a report written to a file. ADR-019 addendum.

**3. F32-2: `flags_now`.** Add `flags_now` (the flags of the **current** file) to `BlastItem` in `ledger/service.py` (set in `_blast_item` from the "now" result) and to `BlastItemResponse` in `api/ledger.py`; the window works out "new" as `flags minus flags_now` in a pure tested function. Mutation proof: `flags_now` taken from the proposed side. ADR-018 addendum.

**4. The run id for the import screen.** The draft route returns `model`, `file_name`, `complete`, `question_count` and rows, and **no `run_id`** (code: `ledger/draft.py::DraftRows`, `api/questionnaire.py::DraftResponse`). Proposal: add the record's full `run_id` (from the record found by its full recorded `file_name`) to both. The import screen opens the draft, then imports with that `run_id` and the same `file`. A renamed draft has no record under its new name and stays `run_record_missing` (doc: ADR-021 decision 2, threat-model row F38-1d). Never matched by the 8-hex prefix. Mutation proof: return `run_id[:8]`. ADR-021 addendum.

**5. Graph route** (design in ADR-023): `GET /vault/graph`, a vault scan (not the index), resolved explicit links only, 500 nodes and 5,000 edges refused whole above the cap, no document text sent, `live_promises` is **null (shown as "not available") when no ledger folder is in use, never 0**. **networkx is not used:** it is in no sidecar file (section 1).

**6. Other additions:** `GET /ledger/cited-paths` (distinct stored paths of live promises with counts, capped at 2,000; blast radius picks a path from it instead of a typed path); `source_path`, line numbers and `customer_id` on recall and blast items (items are text-free today, so a screen cannot say which promise an item is); a cap of 20 on `locations` per item with `locations_not_listed`; a fixed `coverage` field on the blast report; `preview: bool` on import (every check and count, nothing written, no customer created); `page_size` on `GET /ledger/promises` (N6). ADR-017 and ADR-018 addenda.

---

## 8. Screens (all planned, none built; debug build only until M8)

Shared rules: text only through `PlainText` and `LongText` (React text nodes, control characters made visible, no links; threat-model F36); every new Zod file imports `zodConfig` first (code: `apps/desktop/scripts/zodConfigImport.test.mjs`); no new colour token unless it gets a pair in `apps/desktop/scripts/contrastTokens.test.mjs` (it fails on an uncovered token); `409 run_in_progress` is shown as `BUSY_STATEMENT` ("another job is running"); a not-configured state names the folder and says a change applies at the next launch; an irreversible write uses an in-page two-step confirm, never `confirm()` (the dialog plugin overrides `alert` and `confirm`, deviation M6-22); no screen writes results to a file (G32); every new screen is covered by the no-console canary (F36-4).

**Every statement and label below is a draft for the owner's approval (N10).** "Verbatim" means the window's constant equals the sidecar's string and the reply schema pins the sidecar's string with a Zod literal, so a drifted sentence makes the reply invalid.

| Screen | What it does and its states | Draft statements (source) |
|---|---|---|
| **Ledger** (customers, promises, void, acknowledge) | Lists customers and promises. States: no ledger folder; `ledger_unusable`; empty; loading; list; **page too large** (the proxy answers `too_large` for a promises page over its cap: the screen says "This page of promises is too large to show. Choose a smaller page size." and offers the closed page-size choices); 409 busy where it applies. Void and acknowledge are the writes. | "A promise is an answer you told ALIBI you sent to a customer. ALIBI never sends anything." (ADR-017 decisions 1, 11). "Immutable here means ALIBI has no way to change or delete a ledger row. It is not tamper-evident: another program running as you could edit the file." (ADR-017 decision 9). Void, second step: "Voiding cannot be undone. The promise stays in the ledger, marked void, and is left out of recall." (decision 13). Acknowledge: "Acknowledging records that you saw this state on this date. It does not change the promise." Stale: "This promise's state changed after you acknowledged it." (G35) |
| **Recall** | Runs recall, shows results. States: no vault folder; `too_many_promises` (counts); results. | The sidecar `STATEMENT` ("intact means the cited lines are unchanged since drafting; it does not mean the promise is still true.") verbatim in a `Statement` block above the results **and** in each item's detail (F31-4b); `SCOPE` verbatim; "Checking records a change of state in the ledger when one is found. It never edits a promise." (ADR-017 decision 20) |
| **Blast radius** | The path is chosen from `cited-paths`, never typed; the proposed text is pasted into a text area and held in component state only; "preview deleting this file" is exclusive with the text; the byte count is shown against 2,097,152 before sending. No command reads files for the window. States: no ledger or vault folder; refusals; no matching promise (`NO_MATCH_LINE`); results with a "new" flag marker from `flags_now`. | `PREVIEW_LINE` and `NO_MATCH_LINE` verbatim; `STATEMENT` and `SCOPE`; coverage: "Blast radius checks promises only. It does not check controls or the documents that link to this file." (F32-4, G36); "New: this flag is not on the file as it is now."; "The text you paste stays in this window for this preview only. ALIBI does not save it." F32-4's label stays **No mitigation** until the `coverage` field and its screen test exist. |
| **Contradictions** | Starts a run, polls it. States: no vault folder; `index_not_built`; busy; model degraded (a warning before Start: a run still works, every finding a candidate); running (`pairs_done` of `pairs_total`, no ETA); complete; failed; unknown run (404). Each finding shows both quotes with file and lines, `model_status`, reasons, and the model's explanation labelled "the model's words". | `NUMERIC_ONLY_STATEMENT` verbatim, always visible (F33-9). "The model's answer for the same pair can differ between identical runs." (F33-11, G42). "Findings are not kept. Closing ALIBI or starting another run drops them." (ADR-019 decision 1). "Both quotes are shown so you can decide. ALIBI does not decide which statement is right." (F33-10). Labels, never "detected" or "confirmed": `VALUE_CONFLICT` = "Numbers differ (the model kept this pair in this run)"; `CANDIDATE` = "Numbers differ (not kept)" with the reason shown |
| **Reality check** | Flow: pick a CSV from the Imports listing, inspect (counts), confirm MFA types, phone types and in-scope statuses (from the inspect values), pick a claim, enter the export date, check. States: `import_root_not_configured` shown as "the import folder or the vault folder is not set"; `export_refused` codes; `check_refused` codes; result. Up to 200 user logins are personal data: no logging, no storage, plain text only (F26-7). | `STATEMENT`, `STALENESS_NOTE` and `DUPLICATE_NOTE` verbatim. Finding words (the qualifier is required): "This export shows users in scope without MFA, by the MFA types you confirmed" and "This export shows no users in scope without MFA, by the MFA types you confirmed"; never "compliant" (ADR-016 decision 11). "Counting phone (text message or voice) as MFA is a policy choice: phone codes can be phished or intercepted." (ADR-016 decision 6). For the logins: "The logins below are personal data. ALIBI does not save or log them." **Not** "gone when you leave this screen": the App-level hooks are rendered in no test (F6-1c), so a "dropped on leaving" claim would need a tested pure reset first. |
| **Import (approval)** | Option A+ (below). Pick a draft from the export listing, read it with `draft_rows` (gives the header and `run_id`), choose a customer (existing, or new with confirm), enter the sent date, **Preview** (`preview: true`: counts and skip codes), then a second step "Create N promises for this customer". Then the outcome, including `already_changed_since_draft`. | "Approval happens in the workbook: type "approved" in the Review column, save the file with Save (not Save As), then import it here." (ADR-017 decisions 1, 2). "ALIBI only reads the workbook. It never saves it." (ADR-014 decision 10, ADR-021 decision 7). "Importing creates promises that cannot be deleted, only voided. A new customer cannot be removed." (ADR-017 decisions 8, 10, 13). "The date is your statement of when you sent these answers. ALIBI cannot check it." (decision 11). The review queue's `APPROVAL_LINE` loses "(the import screen comes in a later chunk)"; that changes the owner-literal test in `apps/desktop/src/components/reviewQueue.test.tsx` on purpose. |
| **Graph view** (list first) | A per-document list: links out, links in, live promise count, counts of broken links. States: no vault folder; `graph_too_large` (counts); the ledger-not-configured state shows promises as "not available (no ledger folder in use)", never 0. | "Lines are the explicit links written in the files. A missing line does not mean two documents are unrelated." (CLAUDE.md 8.2). "This map is not evidence that a control works or that a promise is true." |

**Approval scope (decided: Option A+).** Approval stays typing `approved` in the Review column in Excel and importing the workbook (ADR-017 decision 1). A+ adds the import screen with the preview step. **Option B** (the queue shows the Review mark) is rejected for now: it needs the F37-3 source scan (`apps/desktop/scripts/reviewQueueSources.test.mjs`, which bans the word "approve" in queue files) changed on purpose and ADR-021 decision 3's "Review cell not returned" amended. **Option C** (approval inside the app) is rejected by ADR-017 decision 1, ADR-014 decision 10 and CLAUDE.md Section 11, because ALIBI would have to save a workbook. The import screen lives in separate files so the queue scan stays unchanged.

**How the screens cannot drop the F31-4b and F33-9 statements.** The window shows its **own constant** and the sidecar's text is pinned by a Zod literal on the same constant. Static-markup tests: the intact statement appears in every result state and in every item detail; the numbers-only statement appears in every state that shows results. Mutation proofs: remove the `Statement` from the results component; change the literal (the contract-fixture parse fails). Planned names are given when the tests are written.

**Web-storage ban (not present today).** Only the queue's files are scanned for `localStorage` and `sessionStorage` (code: `reviewQueueSources.test.mjs`). Planned: `apps/desktop/scripts/noWebStorage.test.mjs` over every file under `src/` (tests excluded) for `localStorage`, `sessionStorage`, `indexedDB`, `caches.`, `document.cookie` and `navigator.storage`, plus an ESLint `no-restricted-globals` rule proved live in `eslint-rules.test.mjs`. Mutation proof: plant `localStorage.setItem` in a `src` file; both must fail. This keeps personal data and policy text out of the webview's profile folder.

**Hooks and click handlers without jsdom (F6-1c).** Every ledger write goes through a pure action module (for example `voidPromise(invoke, confirmed, promiseId, reason)` calls `invoke` only when the confirm reducer has produced a `confirmed` value), tested with a recording fake `invoke`: no call without confirmation, exactly one with it. A source scan proves each writing route name appears in exactly one action module. Hooks stay thin, and F6-1c stays "claimed, not verified".

**Navigation (N8).** Grouped navigation (proposed groups: Questionnaires, Promises, Checks, Map). It must be **hand-checked at the 900 by 600 minimum window** in the bundled debug build; no test covers the real layout.

---

## 9. React Flow: the request, and the decision (open)

Nothing is installed. **The decision stays open**; the list view ships first (65k), then the owner decides (N3).

- **Package:** `@xyflow/react`, exact pin **12.12.0** (published 2026-09-24; `13.0.0-next.0` is a prerelease and excluded). License MIT (npm view).
- **Direct dependencies:** `@xyflow/system` 0.0.83 (MIT), `zustand` ^4.4.0 (4.5.7, MIT), `classcat` ^5.0.3 (5.0.5, MIT). Peers: `react` and `react-dom` 17 or later.
- **Transitive:** d3-drag, d3-zoom, d3-selection, d3-interpolate, d3-dispatch, d3-transition, d3-color, d3-timer (ISC), d3-ease (**BSD-3-Clause**), `use-sync-external-store` (MIT), and `@types/d3-*` (MIT). Install scripts: none of `@xyflow/react`, `@xyflow/system`, d3-zoom, d3-drag, d3-selection, d3-transition, zustand and classcat has a `preinstall`, `install` or `postinstall` (read in their `package.json`); `classcat` has `prepare`, which a registry install does not run (pnpm 12.9.1 behaviour **not verified**). The install scripts of the other d3 packages and `use-sync-external-store` were read by npm view only, not from tarballs (**not verified**).
- **Network and telemetry:** no `fetch`, `XMLHttpRequest`, `sendBeacon` or `WebSocket` in the `@xyflow/react` and `@xyflow/system` bundles or the extracted d3, zustand and classcat sources (zustand's README mentions one only). Warnings are guarded by `NODE_ENV === 'development'`; `console.log` runs only with a `debug` prop.
- **CSP:** no `createElement('style')`, `insertRule`, `adoptedStyleSheets` or `setAttribute('style')` in either bundle (read in `dist/esm/index.js`). Styling is React `style` props, and d3 sets styles through `this.style.setProperty` (the CSSOM, which `style-src 'self'` does not block). Its CSS ships as `dist/style.css` and `base.css` with no `url()`, `@import` or `@font-face`.
- **Attribution:** it renders `<a href="https://reactflow.dev?utm_source=attribution" target="_blank">` by default (read in `dist/esm/index.js`, `Attribution`). `proOptions={{ hideAttribution: true }}` removes it. The authors ask: "Please only hide this attribution when you are subscribed to React Flow Pro".
- **Layout:** it does not lay out nodes; each node needs a `position`. Without a second dependency: a deterministic layered layout in our own code (by `type`, then path order, on a grid).
- **Measured DOM:** it uses `ResizeObserver` for node and pane sizes. Without jsdom, `renderToStaticMarkup` can show only the wrapper and unmeasured nodes (how it renders is **not verified**). Canvas behaviour is checked by hand; the tested content is the list view and the pure layout function.
- **Navigation in the host:** no `on_navigation` appears in `apps/desktop/src-tauri` (section 1), and `tauri.conf.json` has no navigation setting. Whether the webview can navigate to or open an outside page is **not verified**; what a click on the attribution link does in the bundled debug build is **not verified** until the owner tries it.

**Owner rule (stage 2): no clickable outside link may ship unguarded.** If React Flow is adopted, either (a) the attribution is hidden **after the owner has read the React Flow Pro terms**, or (b) it is kept and a **Rust navigation guard with its own test** ships first. The attribution is never left as an unguarded link.

**Hand checks (local, 65l if built):** a click on any `target=_blank` link; the DevTools Issues tab clean under the shipped CSP; pan and zoom by keyboard and mouse; forced-colors; the 900 by 600 minimum window; a vault near the node cap; the console silent.

**What the graph view loses without React Flow.** A list per document (or an SVG our own code draws from the same layout function) has no pan, zoom, drag or minimap, and a large vault is read through a selected-document view rather than one canvas. On the other side it is fully keyboard and screen-reader accessible, testable as static markup, adds no dependency and carries no outside link. React Flow gives an explorable canvas but adds a set of transitive packages, an attribution decision, a measured DOM that our tests cannot reach, and still needs the list as an accessible fallback.

---

## 10. Contract checks (fixes the cause of deviation M6-15)

No new dependency. `sidecar/tests/test_reply_contracts.py` builds synthetic data (a vault, a ledger, an export CSV, a fixed `Clock`, a scripted model for contradictions), calls every route the window uses through the real app with the token, and covers at least one success and one example per error family. Ids are replaced in order of appearance by fixed values of the right shape. The results are written to `fixtures/contracts/replies/<route>.json` **only** when `GRC_WRITE_REPLY_CONTRACTS=1`, and that run still fails (the `GRC_WRITE_HEALTH_CONTRACT` pattern, doc: ADR-006 decision 3); otherwise the test compares. `fixtures/` is mounted read-only in the air-gap container (doc: `docs/dev-setup.md`, "Air-gap proof"), so the comparison also runs there. A Vitest file parses every reply file with `routeReplySchemas[route]` and requires that every route in `proxy-contract.json` has a reply file. The seven M6 core routes are covered too. Mutation proofs: a field dropped from a Zod schema fails Vitest; a field renamed in Python fails the sidecar comparison.

---

## 11. Tests per chunk and where mutation proofs are recorded

Each chunk has at least one negative or security test (listed first in its chunk PR). Mutation proofs are **recorded in the repository**, never only in a pull request body (deviations M6-29, M6-30): in an "M6.5 mutation proofs" table in ADR-022 and ADR-023 and in the addenda of ADR-017 to ADR-021, each with the break, the failing `path::test_name` and the file's sha256 before and after.

- **65b:** a stale acknowledgement is refused and the ledger bytes are unchanged; a preview writes nothing and creates no customer; the `locations` cap; the draft `run_id` is the full id; `page_size` stays in range.
- **65c:** the slot is released after a failure and on `aclose`; the graph has no edge from similarity (a note that only resembles another gets none); a link out of the vault is refused; `graph_too_large` returns nothing partial.
- **65d:** section 6.
- **65e:** the web-storage ban; the reply-contract parse; a canary through each new client's failure paths.
- **65f to 65k:** static markup of every state; the fixed statements; hostile text from `fixtures/adversarial-m5/questionnaire-cells.json` in quotes, customer names and logins; a source scan per screen naming exactly its routes (the review queue scan stays as it is).

---

## 12. Threat-model changes

**Added in 65a (this chunk), all "No mitigation (planned, M6.5 chunk 65x)":** flows F39 to F44 in Section 5 and rows in Section 6: F39 proxy routes (65d), F40 ledger writes from the window (65f, 65g), F41 the contradiction job store (65c), F42 the graph route (65c, 65k), F43 the reality screen (65j), F44 rendering (65f to 65k). Each existing row whose text will change at build time received one dated sentence, "M6.5 plan note (2026-10-18): ...", at its end, with no label or evidence changed: F25-11, F26-7, F30-7, F31-4b, F32-2, F32-3, F32-4, F33-7, F33-9, F34-1, F34-2, F34-3, G34, G35, G36 and G40. The tally is not touched.

**To be done at chunk 65m (not now):** Section 1 (the "Planned, not built" sentence); the Section 4 diagram notes ("seven named routes", the routes not in the table); the F30 flow text in Section 5 ("Not yet consumed by the host"); relabelling the new rows against real tests; the tally.

**Rows that may become Mitigated, and only once a resolvable test exists:** F30-7 and G35; the `flags_now` part of F32-2; F32-3 for the proxied path only (G34 stays open in the sidecar); F32-4 (with `coverage`); the screen limits of F31-4b and F33-9; the G40 limit of F33-7; the host limit of F26-7.

---

## 13. ADRs

New: **ADR-022** (screens, writes and approval scope) and **ADR-023** (graph view), both "Proposed". Dated addenda ("Addendum (2026-10-18): M6.5 plan, proposed, not built") at the end of ADR-017 (G35, preview, item fields, cited-paths, locations cap, `page_size`), ADR-018 (`flags_now`, `coverage`, locations cap, the proxied request cap), ADR-019 (start-then-poll), ADR-020 (route table growth, typed ids, suffix, query, caps, Imports folder, CSV listing, the test rename) and ADR-021 (`run_id`, the `APPROVAL_LINE` wording). ADR-005 gets nothing. ADR-016 may get an optional addendum when the reality routes are built.

---

## 14. Other documents

- **CLAUDE.md Section 9, "Current milestone"** (replaced in 65a): "M6.5 started (plan in `M6_5_PLAN.md`, chunk 65a documents only, no screens built yet); debug build only until M8. ..." Sections 3, 7 and 11 are untouched. **Section 4** is unchanged in 65a; its Graph row says NetworkX, which no sidecar file uses (section 1). If React Flow is pinned, Section 4 says so at that time.
- **docs/DESIGN.md:** section 11 added in 65a (planned, not built, all for the owner's approval).
- **docs/product-brief.md:** the rows that say "no screen until M6.5" (promise ledger and recall, blast radius, contradiction detector, reality check, demo steps 2 and 3) are reworded **when each screen lands**, not now.
- **README.md:** the debug-build run steps gain the M6.5 screens and their folder prerequisites when they exist. Every line says "debug build" where it matters.

---

## 15. Cloud or local, per chunk

| Chunk | Checks that can run in the cloud | Must wait for the owner's machine |
|---|---|---|
| 65a | none needed (documents only) | pre-commit gitleaks (if it cannot run in the cloud, that is stated, not worked around); the tally and reference scripts |
| 65b, 65c | ruff, `mypy --strict`, pytest on Linux | pytest on Windows; the air-gap container with a rebuilt image; the pinned Semgrep image; Bandit; gitleaks |
| 65d | none (the host does not compile on Linux) | `cargo fmt --check`, `cargo clippy --all-targets --all-features -- -D warnings`, `cargo test`, `cargo deny`, Semgrep. **Quit Docker Desktop first** (os error 1455, paging file too small, seen on the 16 GB machine) |
| 65e to 65k | `pnpm lint`, `pnpm typecheck`, `pnpm test` | the same on Windows; `pnpm audit`; gitleaks; a spot check in the bundled debug window |
| 65l | lint and tests | the attribution, navigation and CSP hand checks in the bundled debug build |
| 65m | none | everything in CLAUDE.md Section 6 and `docs/dev-setup.md` |

---

## 16. Tooling and skills

No tool or skill is evaluated, installed or recommended here (owner decision at M6.5 start). A tool that writes files would meet CLAUDE.md 2.9 (edit tool only) and M6_PLAN section I at: any scaffolder or skill in 65e to 65k; `pnpm add` in 65l (it writes `package.json` and the lockfile, as Tailwind's install did in PR #43, so the diff must be shown); the `GRC_WRITE_*` fixture writers in 65c and 65d (existing ADR-006 practice, to be stated in the PR); any formatter other than `ruff format` and `cargo fmt`.

---

## 17. Not verified

1. Whether Tauri 2.12 with no `on_navigation` lets the webview navigate to, or open, an outside URL; other navigation guards were not searched for.
2. pnpm 12.9.1's handling of `prepare` scripts and blocked lifecycle scripts.
3. Install scripts of `d3-dispatch`, `d3-interpolate`, `d3-color`, `d3-ease`, `d3-timer` and `use-sync-external-store` (npm view only).
4. The JSON encoder's escaping (the 6-bytes-per-character worst case).
5. That a question over 1,000 characters is never a promise.
6. Vault path-length caps in the scanner; which link-extraction function the graph route should reuse.
7. The exact length of a questionnaire `run_id`.
8. Whether the Docker daemon works in the cloud.
9. All durations, caps, timeouts and sizes (unmeasured).
10. How React Flow renders under `renderToStaticMarkup`.
11. Whether `POST /contradictions/run` is used outside `sidecar/tests/test_contradiction_routes.py`.
12. That Python `str.strip` strips U+001C to U+001F and Rust `trim` does not (stated by the owner; not checked by me).
13. **Document disagreements found (not fixed):** `apps/desktop/package.json` pins zod `4.6.7`, while ADR-006 and `M6_PLAN.md` say `4.6.5` (deviation M6.5-1); CLAUDE.md Section 4 lists NetworkX and React Flow and neither is installed; the `api/contradictions.py` docstring still says the "M6 proxy" for G40 (ADR-019's own addendum says M6.5).

---

## 18. Open questions for the owner

1. **React Flow** (decided later, after 65k): hide the attribution after reading the Pro terms, or keep it behind a Rust navigation guard with its own test, or no React Flow.
2. **Graph size limit:** a vault above 500 documents gets no graph, while the ControlMap accepts 2,000 documents (doc: ADR-016 decision 2). Known limit, not a decision: raise the cap, page the graph, or accept it?
3. **`page_size` range:** the proposed closed range is 1 to 100; confirm or change.
4. **Statement wording** (N10): approve, change or reject each draft statement and label in section 8.
5. **Navigation groups** (N8): the proposed groups are Questionnaires, Promises, Checks, Map; hand-check at 900 by 600.
6. **Option B** for the review queue (showing the Review mark) is rejected for now; revisit after the import screen exists?
7. **Zod version** (deviation M6.5-1): which of `4.6.5` and `4.6.7` should the documents say?
8. **Old `POST /contradictions/run`** stays outside the proxy for now (N7); remove it in M8?
9. **CLAUDE.md Section 4 NetworkX row:** the grep allows changing it (no sidecar file uses networkx); change it to "not used; plain lists (ADR-023)" after your report?

---

## 19. Deviations (M6.5)

| # | Date | What | Status |
|---|---|---|---|
| M6.5-1 | 2026-10-18, found while writing 65a | `apps/desktop/package.json` pins zod `4.6.7`, while ADR-006 ("Zod 4.6.5 … is the only new runtime dependency") and `M6_PLAN.md` (STEP 2 "zod 4.6.5 (exact)") say `4.6.5`. Not edited: those are accepted or approved documents; recorded here and in "Not verified". | recorded, open |
