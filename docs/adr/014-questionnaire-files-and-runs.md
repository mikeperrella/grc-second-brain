# ADR-014: Questionnaire files and runs

Status: Accepted, final for M5 (M5i, the reading half; M5j, the writing half; M5k, the runs and the routes; M5l, the demo and end-to-end run; live check B done). Facts about the packages and the numbers below were checked on 2026-10-12 on the Windows development machine.

## Context

A questionnaire arrives as an Excel workbook (`.xlsx`, sometimes `.xlsm`), which is a zip file of XML parts. It is untrusted input: it comes from a customer, a vendor or a download. Three things can go wrong before a single question is read. The archive can be a decompression bomb. An XML part can carry a `<!DOCTYPE>` with entity declarations (the "billion laughs" expansion, or an external-entity read). And the file can change between the moment it is checked and the moment it is parsed. M5i builds the reading half of the engine, with these in mind, and adds the first new runtime dependencies since `sqlite-vec`.

## Decision

**1. Two new runtime dependencies and one development dependency, with exact pins** (approved with the plan; the licenses and requirements below were read from the installed packages):

| Package | Pin | License | Purpose | Network, telemetry, auto-update |
|---|---|---|---|---|
| `openpyxl` | `3.1.5` | MIT | read and write `.xlsx` | none known. Checked, not assumed: `tests/test_dependency_hygiene.py` reads the installed source and fails on any import of a network, process or serialization module, any `eval`/`exec`/`system`-style call, or any string naming telemetry (0 found at these versions) |
| `et-xmlfile` | `2.0.0` | MIT | openpyxl's only dependency | same test, 0 found |
| `types-openpyxl` | `3.1.5.20260827` | Apache-2.0 | typeshed stubs so `mypy --strict` runs without an ignore override | development only; never in the runtime list |

The test also asserts that the closure is exactly these two (`importlib.metadata.requires`), that `lxml`, `defusedxml`, `PIL`, `numpy` and `pandas` are not installed, and that `openpyxl.xml.LXML` is false, so openpyxl uses the standard library's XML parser on the expat build `expat_2.6.0`. A new dependency of openpyxl in a later version fails the closure test. Hash-locking stays M8. `defusedxml` is not added; recommended as an M8 option.

**2. The file is read once, hashed, scanned, and then parsed from the same bytes.** `loader.read_input_bytes` resolves the path inside the approved questionnaire root with the path guard (`resolve_within`), then reads at most the size cap plus one byte (the extra byte is what tells "over" from "exactly at"). `package.scan_package` hashes those bytes and checks them. `loader.load_questionnaire` passes the *same bytes* to openpyxl through `BytesIO`. If the file on disk changes in between, nothing notices and nothing breaks: the parsed workbook is the hashed workbook (test: `test_the_bytes_that_were_scanned_are_the_bytes_that_are_parsed`). The re-hash of the file on disk before writing is a writer rule (later, M5j).

**3. Caps, counted on bytes actually decompressed.** A zip directory states each part's size, and it can lie. So each part is read in 64 KiB chunks through `ZipFile.open`, the running total is kept, and reading stops the moment it passes what is left of the budget (`read_capped`). The stated sizes are used only for a cheap early refusal. Starting values, approved with the plan: 20 MiB file, 100 MiB uncompressed, 2,000 parts, 100:1 ratio, 500 questions. **Three details are mine, not in the approved list, and are flagged for review:** (a) the ratio checks apply only above 1 MiB of uncompressed data, because a small part can compress very well and be harmless; (b) the ratio is checked per part and overall; (c) a worksheet is refused when rows times columns exceeds 250,000 (`MAX_SHEET_CELLS`), because reading a cell creates it, so one cell at row 200,000 would make a scan of the rectangle build a million cells. Duplicate part names, including names that differ only in case, are refused.

**Measured on synthetic workbooks written by openpyxl** (not on Excel-written files, and there is no real customer workbook in the repository, so these are lower-bound evidence only):

| Workbook | Rows x columns | File | Uncompressed | Overall ratio | Largest part ratio |
|---|---|---|---|---|---|
| CAIQ-like | 150 x 8 | 9,417 B | 68,504 B | 7.3:1 | 10.7:1 |
| SIG-Full-like, varied text | 1,800 x 20 | 108,069 B | 1,338,023 B | 12.4:1 | 12.8:1 |
| SIG-Full-like, one repeated sentence | 1,800 x 20 | 88,066 B | 1,240,544 B | 14.1:1 | 14.7:1 |
| 500 questions | 500 x 6 | 17,066 B | 152,824 B | 9.0:1 | 10.9:1 |

**The absolute caps do not depend on the 1 MiB floor.** The floor (`package.py:41`) gates only the two *ratio* checks (`package.py:339` per part, `package.py:346` overall). The part count is checked on the directory before anything is read (`package.py:316`), and the total of decompressed bytes is a running budget that every part, however small, is read against (`package.py:324` starts it, `package.py:330` passes what is left to `read_capped`, `package.py:335` subtracts what came out). So a small part cannot bypass them, and many small parts cannot add up past them. Tests: `tests/test_package_scan.py::test_the_part_count_cap_applies_to_tiny_parts` (2,001 one-byte parts), `tests/test_package_scan.py::test_small_parts_cannot_add_up_past_the_total_cap` (twenty incompressible 100 kB parts, each far below the floor, ratio about 1) and `tests/test_package_scan.py::test_the_running_total_counts_bytes_produced_not_sizes_claimed` (a stand-in archive whose directory claims 10 bytes per part while each part yields 400 kB, which the standard library would otherwise police by itself). Mutation proofs 23 and 24 below. The early refusal on the *claimed* sizes (`package.py:321`) is redundant by design: removing it fails no test, because the running total refuses the same files.

All are far from the 100:1 limit and from the 250,000-cell limit (the largest is 36,000 cells). If a realistic workbook is refused, that is a stop-and-report condition, not a reason to quietly raise a cap. M5l re-measures on the demo workbook.

**4. XML safety without a new dependency: a byte search of every part.** openpyxl parses with the standard library, which expands entities. The only safe move is to refuse a `DOCTYPE` before the parser sees one. Probing this expat build showed it reads UTF-8, UTF-16 with or without a byte-order mark (little- and big-endian) and several single-byte encodings that keep `<!DOCTYPE` as the same ASCII bytes (iso-8859-1, us-ascii, cp1252, koi8-r, latin-1), and refuses UTF-32, EBCDIC (cp037, cp500), shift_jis, gb2312 and utf-7. So three byte layouts cover what the parser can read: ASCII/UTF-8, UTF-16LE and UTF-16BE. Every part, of every type, is searched case-insensitively for `<!DOCTYPE` and `<!ENTITY` in those three layouts; a hit refuses the whole file (`doctype`, `entity`). Binary parts (images, `vbaProject.bin`) are searched like any other and are never refused for holding NUL bytes. A UTF-32 byte-order mark is refused, and so is a declared encoding outside {utf-8, utf-16, us-ascii, iso-8859-1, latin-1, windows-1252, cp1252}, as defence in depth for an encoding another build of expat might accept. The test `test_every_encoding_the_local_parser_reads_is_scanned` asks the local expat which candidate encodings it reads and checks that a DOCTYPE document in each is refused; it also asserts that the three base encodings are among them, so it is not vacuous.

*A finding from the mutation proofs:* a UTF-16 text is its other-endian twin shifted by one byte, so inside a longer part either UTF-16 pattern finds either layout. Removing the little-endian pattern did **not** fail any of my first tests. The patterns differ only at the edges of a part (little-endian text that starts the part has no byte before its `<`; big-endian text that ends it has no byte after its last letter). Two tests now use exactly those edges (`test_utf16le_billion_laughs_part_refused`, `test_utf16be_billion_laughs_part_refused`), and the proofs below show each pattern is needed on its own.

**5. The macro decision comes from the content type, not the file name.** `[Content_Types].xml` is read with patterns (no XML is parsed in this module) for the override of `/xl/workbook.xml`: the workbook type means a plain package, the macro-enabled type means `keep_vba=True`, anything else (templates, a missing part) is refused (`unsupported_type`). A legacy `.xls` and an encrypted workbook both start with the same OLE header and are refused with one code (`legacy_or_encrypted`). openpyxl does not run a macro; it keeps `vbaProject.bin` as opaque bytes when asked.

**6. A part inventory.** The scan counts parts a save can lose: charts, drawings, media, comments, external links, pivot tables, embedded objects, form controls, slicers, printer settings, the VBA project, and sheets that hold the dropdown (data validation) extension that openpyxl drops. `compare_inventories(before, after)` reports every category that is reduced. The writer (M5j) calls it on the saved output so a dropped chart is a warning and not a surprise. openpyxl's own warnings are collected, not printed (`LoadedWorkbook.warnings`).

**7. Refusals say which rule fired, never what the file contains.** `WorkbookRefusedError` and `QuestionMappingError` carry a `code` from a fixed set. A parser failure is raised as `WorkbookRefusedError("unreadable")` outside the `except` block, so the parser's message (which can quote the file) is not chained to it; a test asserts `__cause__` and `__context__` are empty.

**8. Question mapping is explicit, and a guess is only a suggestion.** `QuestionMapping(sheet, header_row, question_col, id_col)` is a frozen pydantic model (rows and columns from 1, bounded). `suggest_mapping` guesses from header words on the first 30 rows and 100 columns of the first visible sheet that has one, changes nothing, and may return None; a run uses the mapping the caller names. `extract_questions` reads the rows under the header row and reports every skipped row: empty question cell (`blank`), a formula in the question cell (`formula`, never asked: a formula such as `=HYPERLINK(...)` is not a question), and a **section header**, which is a row whose question cell is the only non-empty cell **and** is bold or inside a horizontal merge. A plain one-cell row is read as a question: skipping a real question silently is worse than asking a model about a heading. A header row that already holds one of ALIBI's own column titles is refused (`already_has_answer_columns`), so an exported draft is never answered again. More than 500 questions is refused.

**9. One run at a time, and `warnings.catch_warnings` is process-wide.** The loader records openpyxl's warnings with `catch_warnings`, which is not thread-safe. That is acceptable only because a questionnaire run is the single caller and runs one at a time (the 409 rule, M5k).

### The writing half (M5j)

**10. The writer is the only module that saves a workbook, and its order is the control.** `writer.write_answers` (a) takes results that code can only have lowered, and blanks any answer whose cited vault file has a different hash now than when it was retrieved (`lower_for_changed_source`, ADR-013 check 7, with the file hash supplied by the caller); (b) refuses unless every question has a result (a run that stopped early supplies blank results with `model_unavailable` for the rest, so the file is still written, decision 13 of the plan); (c) writes into **new columns to the right of the used range**, never into a cell that is not empty or is merged, never a new or inserted row, and leaves every blank answer cell empty (no empty string); (d) saves to memory and reads that back through the loader, comparing every original cell (value and type), the sheet order, merged ranges, data-validation ranges and row counts, and each written cell (a string, typed as one, with the quote prefix where the text starts like a formula, and never a hyperlink), failing with `verify_failed` and writing nothing if anything differs; (e) re-reads the input file and compares its hash with the one taken when it was read, failing with `input_changed` and writing nothing if it differs; (f) creates the output with exclusive create (`open(path, "xb")`), so a file of that name, of any kind, is never overwritten (`output_exists`); (g) reads the file back and compares its hash with the bytes saved, and on a mismatch removes **only the file just created** and fails with `readback_mismatch`. Parts openpyxl dropped on save (charts, media and so on, from the part inventory of decision 6) are reported in the result, not fatal. A same-user process could swap a file between (e) and (f); the threat model records that as out of scope (O1). Both sides of (d) are openpyxl's view, so live check B opens the result in Excel.

**Columns and name.** Headers: `GRC Answer [UNREVIEWED DRAFT]`, `Confidence (model self-rating) [UNREVIEWED DRAFT]`, `Status [UNREVIEWED DRAFT]`, `Reason`, `Evidence`, `Review`, written in the header row. `Evidence` is `<vault path>:L<start>-L<end> "<quote>"`; `Reason` lists every reason in the fixed order, joined by `; `; `Review` is always `unreviewed`. The file is named `<stem>-UNREVIEWED-DRAFT-<run8>.<xlsx or xlsm>`. The stem is the last path component of the source name without its extension, with every character outside letters, digits, dot, space, hyphen and underscore replaced by `_`, at most 80 characters (`..\..\x`, drive letters and reserved names cannot escape the folder or collide with a device); the run id must be 8 to 64 lowercase hex characters.

**11. Text from the model and the vault is cleaned, and formula text is typed, not altered (`sanitize.py`).** Unsafe control characters, lone surrogates and the two non-characters are removed; line endings become `\n`; the length is capped at 32,000 characters (Excel allows 32,767) with a visible marker. **Line endings are the one change to the text, and the reason is a measurement:** written and read back through openpyxl, `\r` came back as `\n` and `\r\n` as two line feeds, so text written would not have equalled text read. Text starting with `=`, `+`, `-`, `@`, tab, carriage return or line feed is stored as a typed string (`data_type` `s`) with the quote prefix style set, so Excel shows it as text and keeps it text if someone presses F2 and Enter; the text itself is not changed. A leading carriage return becomes a line feed first, so the line feed is in the trigger list. **The honest trade:** a visible apostrophe prefix is the usual defence and survives a CSV export; this does not. It was chosen because it keeps quotes verbatim and Markdown `-` and `+` readable. openpyxl reads `quotePrefix` back from a saved file (checked by the writer's own verification), so the plan's stop condition for that did not fire. No hyperlink is ever set on a cell.

**Direction controls (owner's addition at the M5j review).** Unicode bidirectional controls (U+202A to U+202E, U+2066 to U+2069, U+200E, U+200F, U+061C: twelve in all) can make text display in a different order from the order it is stored in, so a reviewer could read something other than what is there. They are handled by where the text comes from. **In an answer** (model output, no verbatim requirement) they are removed, before the formula-trigger check, so a control placed in front of `=` cannot hide it. **In a quote** (it must stay verbatim) they are kept, and when the Evidence text (path and quote) contains any of them, the cell starts with the visible marker `[contains direction-control characters] `, so the reviewer knows what is displayed may be reordered. No status rule changes. The letters of right-to-left languages, zero-width joiners (emoji sequences) and zero-width spaces are not direction controls and are left alone (tests). Other look-alike tricks, such as homoglyphs, are not handled.

**A scanner finding, fixed in `39aa7cd`:** CI's Bandit (B613, "trojansource") and Semgrep (`generic.unicode.security.bidi`) both failed the first push of M5j, because `sanitize.py` held the twelve direction controls as raw characters in a regex: the code that defends against them contained them. The `\u` escapes I had typed were turned into the characters when the file was written, and the formatter kept them. The ranges are now built from code point numbers (`_character_class`), so the file is plain ASCII; the tests build the characters with `chr()`; and `tests/test_source_hygiene.py` scans the whole repository (code, tests, documents) for them and tests its own scanner on each of the twelve, so a raw one fails a test here before CI's scanners see it. Mutation proofs 46 to 49 were re-run on the new construction (U+061C left out of the ranges: 2 failed, 99 passed; lone surrogates left out of the unsafe set: 3 failed, 98 passed); the final `sanitize.py` sha256 is `82d26c7c...49155`.

**A leak found while testing, and fixed:** for a macro-enabled package openpyxl keeps the project in an in-memory zip file inside the workbook. Left to the garbage collector it can fail while closing, after the memory it writes to has been released, and the test run (which turns warnings into errors) reported that as a failure in whichever test happened to run next. `LoadedWorkbook.close()` closes it explicitly; the writer closes the workbook it reads back, and the tests close what they load. Mutation proof 50.

**12. The questionnaire and export folders** are two new settings, `GRC_QUESTIONNAIRE_DIR` and `GRC_EXPORT_DIR` (absolute, optional, unset until the folder pickers exist in M6). Neither may be inside, the same as, or contain the vault or the index, checked lexically when settings load (an error that names the field, not the path) and again on the resolved paths, because a link can fool the first check (`validate_runtime_dirs`). They may be the same folder as each other. The host's five-key child environment is unchanged: the host does not pass them yet.

**13. The write policy is enforced twice.** Four Semgrep rules, scoped to the questionnaire package: `.save(` only in `writer.py`; no `write_bytes`, `write_text`, `os.replace`, `os.rename`, `shutil.copy*` or `shutil.move`; `open()` only with mode `rb` or `xb`; file removal only in `writer.py`. And `tests/test_questionnaire_write_hygiene.py` reads the same policy from the syntax tree, so it runs offline, and its scanner is tested against every forbidden construct.

### Runs and routes (M5k)

**14. A run refuses before it works.** `QuestionnaireRuns.start` (`questionnaire/runs.py`) refuses, with a typed error and no file written, in this order: a run already going (409 `run_in_progress`); the file outside the questionnaire folder, missing, or refused by the scan (400, 404, 422 with the loader's code); a mapping that does not fit or a workbook with no questions (422 with the mapper's code); a required model not confirmed by the gate, or the generation model's digest unreadable (503 `models_unavailable`); the index not built (409 `index_not_built`); and **any vault file changed after it was indexed (409 `vault_changed_reindex`)**, asked of the retriever's own freshness check (`Retriever.vault_withheld`). A refused start leaves nothing running and nothing remembered. Mid-run edits are handled by the answer status rules, not here: a file that changes during the run caps the later answers at PARTIAL with `evidence_withheld`, and a cited file that changed blanks its answer when the file is written (F23-6).

**15. Ollama failing part-way stops the asking, not the file (ADR-013, "Ollama fails partway").** When one answer reports the model unavailable (the client could not reach it, it timed out, it was refused or answered from a non-local host), every remaining question gets a blank `model_unavailable` result without calling the model again, the file is still written, the run ends `stopped`, and the summary says `complete: false`. A generation read timeout takes this path (ADR-012, "The read timeout, derived (M5k)": 660 s, 720 s for the first call). A failure that is not about the model (the index disappears, the input file changes during the run so the write refuses, an unexpected error) ends the run `failed` with a fixed code (`index_not_built`, `retrieval_unavailable`, `write_<code>`, `internal_error`, `cancelled`) and **writes no file**; the exception's own text is never kept, sent or logged (the log line holds the code and the exception's type name).

**16. Events and routes carry codes, never text.** The run reports as server-sent events: `started` (run id, total), one `progress` per question (index, total, status, reason codes), then `done` (complete, total, counts) or `failed` (code). A test lists the allowed keys and fails on a canary in a question, an answer, a quote, the file name or the path. The routes (`api/questionnaire.py`) are thin: `POST /questionnaire/inspect`, `POST /questionnaire/runs` (202), `GET /questionnaire/runs/{id}` (status and summary: model, digests before and after, template and input hashes, counts, reason counts, file name, dropped parts, warnings, seconds) and `GET /questionnaire/runs/{id}/events`. All sit behind the auth middleware that wraps the whole app, so the stream needs the token too, and a malformed body or run id is a 422 that names the fields and never echoes a value (a path may be in one). One run is held at a time; the last 20 finished runs are kept in memory (they are gone after a restart; the file is not). All the work that blocks (the workbook, the files, the hashes) runs off the event loop, and a test holds a run in the middle of a question with an event, with no sleeps, and shows `/health` still answers.

**17. The real parts are assembled from the settings, and a missing setting is a refusal.** `questionnaire/runtime.py` builds the answer service, retriever, generation client and repositories when the vault, the questionnaire folder and the export folder are all configured (`GRC_VAULT_DIR`, `GRC_QUESTIONNAIRE_DIR`, `GRC_EXPORT_DIR`); otherwise there is no runtime and every route answers 503 `questionnaire_root_not_configured`. Nothing connects to Ollama while it is built. **No route builds the index, and M5 adds none (owner decision):** it is built by the existing indexer, deliberately started, so with no index a run refuses with `index_not_built`. The M5l end-to-end live test builds the `vault-demo` index with that indexer before it starts the sidecar, the same way live check A did. A production "build index" trigger belongs with the M6 folder picker. Live check B keeps the live event stream as an explicit step: a real client reading it while a run is going.

**The tests never reach a real Ollama.** While writing these tests I made read-only `GET /api/tags` calls to the owner's running Ollama by accident (the run's model reader and the app's startup check had no test double); I found it, stopped, and reported it. They now use a mock transport (`build_runtime(generator_transport=...)`) or a loopback port nothing listens on, a test asserts the port is never 11434, and an instrumented run of every new test file recorded no connection to port 11434.

**Logging, measured.** During a whole run through the routes at DEBUG, the only records were asyncio's own and the test client's httpx request lines; openpyxl and the zip library logged nothing. No canary from a question, an answer or a quote appears in any record (`tests/test_questionnaire_routes.py::test_no_canary_appears_in_any_log_record_for_a_full_run_through_the_routes`). The run's own log holds the failure code and the exception type name only.

### The demo spreadsheet and the end-to-end run (M5l)

**18. The demo workbook is generated, never committed.** `tools/make_demo_xlsx.py` (standard library plus the pinned openpyxl; no network, process or OS imports, checked by a test) writes `demo-questionnaire.xlsx` into the gitignored `fixtures-out/` with an exclusive create, so it never overwrites a file. It holds 15 questions in our own wording (not SIG, CAIQ or any other questionnaire), written against `vault-demo/` (11 meant to be answerable, 1 the planted 90-vs-365 conflict, 1 the overdue vendor policy, 3 absent or framework-only). It has the features the engine must leave alone: a header row, three merged bold section banners (skipped as `section_header`), a drop-down on an empty response column, column widths and a second sheet. The expectation notes live in the tool and are printed beside the live result; they are not written into the workbook and no test asserts them (a model's answers vary, ADR-013, 2a).

**19. One scenario, run twice.** `tests/e2ekit.py` starts the real sidecar as a child process (`python -m grc_sidecar`, token in its environment, folders and the Ollama address in `GRC_*` settings), reads the ready line, and over real HTTP does inspect, start, read the event stream line by line while the run is going, and status. The index is built first, in the test process, by the existing indexer (decision 17: no route builds it). `test_e2e_wiring.py` runs it offline against a small fake Ollama on an unused loopback port that answers "no evidence" to everything; `test_e2e_live.py` (marker `ollama`) runs it against the real Ollama and only the owner starts it. The structural checks hold however the model answers: stream shape and counts, summary counts add up, input hash unchanged and equal in inspect and summary, no original cell changed, no new row, merged ranges and data validations unchanged, the six new headers, a blank answer exactly on the NEEDS_REVIEW rows, no question text in the stream, and (offline) none in the sidecar log. The kit refuses to point a non-live run at port 11434 (`test_an_offline_scenario_refuses_the_real_ollama_port`).

**20. A bug the end-to-end run found in the M5j writer.** The reopen check compared a written number with `type(cell.value) is type(value)`. The file stores a confidence of 0.0 or 1.0 as `0` or `1`, which openpyxl reads back as an int, so any answer with model confidence exactly 0.0 or 1.0 (every fake "no evidence" reply; a real model saying 1.0) failed the run with `write_verify_failed` and no file. The unit tests had used 0.95 and `None`. Fixed in `writer._stored_as_intended` (a number is compared as a number, a bool is not a number, text must be the same `str`); four tests, and the old comparison fails six tests including the end-to-end ones (mutation proof 69). The check is not weaker for text, and the written cell is still type-checked.

**What the offline run cannot show:** what a real model says, a SUPPORTED or PARTIAL cell written through the routes, or a live stream read while a slow run is going (the fake answers at once). Live check B covers those. The port-11434 guard in the kit has a mutation proof (70), run on the second attempt: the first was refused by the tool permission layer and was not worked around.

## Consequences

Positive: a hostile or broken workbook is refused with a stable code before it reaches the parser; the file that was hashed is the file that was parsed; the new dependencies are checked, not assumed; every refusal and every skipped row is reported, none is silent.

Negative or risky:

- **The DOCTYPE search is a byte search.** It refuses a harmless part that merely contains the text `<!DOCTYPE`, for example a binary file that happens to hold it. Vanishingly unlikely, accepted, and the safe direction.
- **It depends on this build of expat.** The set of encodings a parser reads can differ between builds. The test asks the local expat, and the encoding test runs on Windows and in the Linux air-gap container; the Linux result is the CI job's, not something I observed.
- **Both sides are openpyxl's view.** What openpyxl reads is not necessarily what Excel shows. Live check B opens the output in Excel or LibreOffice.
- **Known limit: a workbook with an empty chart sheet is refused.** openpyxl 3.1.5 fails to load a chart sheet that holds no chart (`AttributeError: 'list' object has no attribute 'find'`), and the loader reports it as `unreadable`; a chart sheet that holds a chart loads. I found it with a workbook built by openpyxl itself. Excel can save such a workbook, so a real questionnaire may hit it. The refusal fails closed, nothing is written, and there is no workaround in M5. **Live check B** therefore opens the Excel-saved copy of the demo workbook as one of its steps and records whether it loads; an `unreadable` refusal on a workbook that Excel opens fine is reported, not worked around.
- **The ratio and cell limits are starting values** measured only on openpyxl-written files. They may refuse a legitimate workbook; if so that is reported, not worked around.
- **The section-header rule is a heuristic.** A bold one-cell row that is really a question is skipped (and reported in `skipped`); a non-bold heading is asked as a question.
- **Windows is the only platform analysed** (threat model, Section 1).

## Mutation proofs (M5i)

Each control was broken with the edit tool, the named test run, and the file restored by the edit tool; the sha256 of each file after restoring equalled the value taken before the first break (`package.py` `cda53691...2fea1`, `loader.py` `66edf26d...f7354`, `mapping.py` `f5c0da08...e0f90`). The counts are those of the run at the time. Nothing was weakened to make a proof pass.

| # | Control | Mutation | Result |
|---|---|---|---|
| 1 | DOCTYPE, UTF-16LE pattern | removed | **first attempt: all passed (a test gap).** Added the edge-case tests (finding above); then 1 failed, 54 passed (`test_utf16le_billion_laughs_part_refused`) |
| 2 | DOCTYPE, UTF-16BE pattern | removed | 1 failed, 54 passed (`test_utf16be_billion_laughs_part_refused`) |
| 3 | DOCTYPE, ASCII/UTF-8 pattern | removed | 4 failed, 51 passed |
| 4 | ENTITY check | removed | 4 failed, 52 passed |
| 5 | Binary parts accepted | a refusal for any NUL byte added | 19 failed, 37 passed (the binary-part test and every UTF-16 test) |
| 6 | Count of bytes produced | limit check in `read_capped` disabled | 2 failed, 53 passed (the endless stream, ending by its own guard, not by hanging) |
| 7 | Overall ratio | removed | 1 failed, 54 passed |
| 8 | Per-part ratio | removed | **first attempt: all passed** (the overall ratio caught my bomb too). Added `test_one_extreme_part_is_refused_even_when_the_whole_file_looks_ordinary`; then 1 failed, 55 passed |
| 9 | UTF-32 byte-order mark | removed | 2 failed, 54 passed |
| 10 | Declared-encoding allowlist | removed | 6 failed, 50 passed |
| 11 | Duplicate part names, case-insensitive | case-insensitive half removed | 1 failed, 55 passed |
| 12 | Macro decision from the content type | macro type always read as plain | 2 failed, 72 passed (package and loader tests) |
| 13 | One read, capped at the size cap plus one byte | `read()` of the whole file | 1 failed, 18 passed (`test_an_oversize_file_is_never_read_past_one_byte_over_the_cap`) |
| 14 | Path guard before reading | replaced by `root / candidate` | 4 failed, 15 passed |
| 15 | openpyxl warnings collected, not raised | filter set to "error" | 1 failed, 18 passed |
| 16 | No parser message chained to the refusal | raised inside the `except` block | 1 failed, 18 passed |
| 17 | The scan runs before the parser | a refusal swallowed and replaced by a blank scan | 2 failed, 17 passed |
| 18 | Section header needs bold or a merge | any one-cell row skipped | 2 failed, 34 passed |
| 19 | Exported draft refused | check removed | 4 failed, 32 passed |
| 20 | Sheet size cap before any cell is visited | removed | 1 failed, 35 passed |
| 21 | Question limit | allowed 501 | 1 failed, 35 passed |
| 22 | Formula cells skipped | removed | 1 failed, 35 passed |
| 23 | Total of decompressed bytes is a running budget | `remaining` no longer reduced by each part | 1 failed, 58 passed (`test_the_running_total_counts_bytes_produced_not_sizes_claimed`). Rows 6 and 23 together: the per-read count stops one part, the running budget stops the sum |
| 24 | Part count cap | limit raised by 5,000 | 2 failed, 57 passed (`test_the_part_count_cap`, `test_the_part_count_cap_applies_to_tiny_parts`) |

Rows 23 and 24 were added after the owner's review of the absolute caps; `package.py` was restored with the same sha256 as above.

**Not proven by mutation, and why.** The Semgrep rule `grc-py-load-workbook-outside-loader` (the rule count is now 23) could not be run with `semgrep --test`, because Semgrep is not installed on this machine; I checked its regex against its own fixture annotations with Python's `re` (4 lines that must match did, 3 that must not did not) and the coverage script reports 23 rules, 23 annotated. The CI `semgrep` job runs the real test. The text scan in `tests/test_questionnaire_loader.py` is the second line of defence and its scanner is itself tested against offenders. The dependency scan is tested against seven constructs it must see and one it must leave alone, but it cannot say anything about code it did not read.

## Mutation proofs (M5j)

Same method and rules as above. Final sha256 of each source file after restoring equalled the value taken before the first break: `writer.py` `f107aded...33a35`, `sanitize.py` `2931a431...1a72c`, `config.py` `d0764471...57c97` for rows 25 to 45. Rows 46 to 49 (direction controls) were run on the next versions of `writer.py` (`59021617...64df9`) and `sanitize.py` (`d30b73dc...969a3`), which also matched after restoring. Row 50 then added `LoadedWorkbook.close()` to `loader.py` (the M5i proofs above were run on the version before that; its final sha256 is `789a4182...50df8`) and the close calls to `writer.py` (final `a9c02478...2431f`). Counts are those of the run at the time; "writer tests" are `test_writer.py`, "sanitize" `test_sanitize.py`, "dirs" `test_questionnaire_dirs.py`.

| # | Control | Mutation | Result |
|---|---|---|---|
| 25 | Quote prefix on formula-like text | never set | 7 failed, 39 passed (writer tests) |
| 26 | Typed string | `data_type` no longer forced to `s` | 2 failed, 44 passed (the `=` trigger and the evidence cell, because the text became a formula) |
| 27 | Each trigger is listed | `@` removed from the trigger list | 2 failed, 72 passed (writer and sanitize) |
| 28 | No hyperlink ever | a cell starting with `http` gets a hyperlink | 1 failed, 45 passed. **The test had to be sharpened first:** its answer began with "See https://...", which the mutation never touched; it now starts with the URL |
| 29 | Exclusive create | `"xb"` changed to `"wb"` | 1 failed, 45 passed (`test_an_existing_output_is_never_overwritten`) |
| 30 | Input re-hash before writing | removed | 1 failed, 45 passed |
| 31 | Delete the file just created | removal skipped | 1 failed, 45 passed |
| 32 | Delete only that file | removes a different path | 1 failed, 45 passed (the bystander file) |
| 33 | Reopen and verify | verification returns at once | 1 failed, 45 passed (`test_the_reopen_check_catches_an_altered_original_cell`) |
| 34 | Blank answer cell stays blank | writes an empty string | 4 failed, 42 passed |
| 35 | Changed source blanks the answer | check skipped | 2 failed, 44 passed |
| 36 | Every question has a result | check removed | 1 failed, 45 passed |
| 37 | Target cell empty and not merged | check removed | 1 failed, 45 passed |
| 38 | Output name characters | replacement removed | 1 failed, 45 passed |
| 39 | Run id shape | check removed | 1 failed, 45 passed |
| 40 | Export folder through the path guard | used as given | 1 failed, 45 passed |
| 41 | Unsafe characters removed | removal skipped | 7 failed, 67 passed (writer and sanitize) |
| 42 | Line endings normalized | skipped | 5 failed, 69 passed |
| 43 | Length cap | threshold raised tenfold | 1 failed, 27 passed |
| 44 | Folders overlap check (lexical) | one direction only | 4 failed, 20 passed (the "contains" cases) |
| 45 | Folders overlap check (resolved) | disabled | 4 failed, 20 passed (the link cases) |
| 46 | Direction controls removed from the answer | answer written as given | 6 failed, 95 passed (I printed only the counts for this run; the four parametrized writer cases and the hidden-trigger test are among them, and a failure from the leak below may be the sixth) |
| 47 | The quote stays verbatim | controls removed from the quote too | 2 failed, 52 passed (`test_a_quote_with_a_direction_control_stays_verbatim_and_is_marked`, plus the dropdown test, which was failing from the leak below and is not evidence for this proof) |
| 48 | The Evidence marker | never added | 3 failed, 51 passed (the quote test, the path test, and the leak victim) |
| 49 | Every one of the twelve controls is in the set | U+061C removed from the pattern | 3 failed, 98 passed (`test_direction_controls_are_removed_from_the_answer` (its U+061C case) and `test_each_direction_control_is_removed_from_model_text[U+061C]`, and the leak victim) |
| 50 | Macro project closed explicitly | `LoadedWorkbook.close()` no longer closes it | 2 failed, 73 passed (`test_closing_a_workbook_closes_the_macro_project_it_holds`, and the garbage-collector failure it prevents, which reappeared in whichever test ran next) |

**Not proven by mutation.** The four new Semgrep rules (27 rules in all) could not be run with `semgrep --test`, because Semgrep is not installed here. I checked each regex against its fixture annotations with Python's `re` (2 to 7 lines that must match and 2 to 5 that must not, no problems) and ran all four over the real package (no hits). Because these rules use `paths.include` for the first time in this repository, with the fixture file listed as well so `semgrep --test` can see it, the CI `semgrep` job is the real test, and if it is red the cause is the path scoping, not the policy. The AST test is the check that ran here.

## Mutation proofs (M5k)

Same method. Files restored with matching sha256 (`runs.py` `a2e0271c...c23bc`, `questionnaire.py` (api) `4c6d443d...c0069`, `errors.py` (api) `65a85d43...a83a`, `main.py` `3851e085...8f4ad`, `scan.py` `0fd6d719...62e7d`, `retrieval/service.py` `cdf97a1b...fd4`, `runtime.py` `167f857a...3daba`). "Run tests" are `test_questionnaire_runs.py` and `test_questionnaire_routes.py` (73 together).

| # | Control | Mutation | Result |
|---|---|---|---|
| 51 | Read timeout margin | factor 2 changed to 1 | 3 failed, 78 passed |
| 52 | Read timeout speed | 5.8 changed to 2.9 tokens per second | 3 failed, 78 passed |
| 53 | `num_predict` derived from the schema maximum | `ANSWER_MAX_CHARS` doubled | 6 failed, 75 passed |
| 54 | The client takes the derived timeout | read timeout typed in as 120 s | 1 failed, 80 passed |
| 55 | One run at a time | check removed | 2 failed, 71 passed |
| 56 | Models confirmed before a run | `gate.require` removed | 3 failed, 70 passed |
| 57 | Digest readable before a run | check removed | 1 failed, 72 passed |
| 58 | Vault current before a run | withheld check removed | 3 failed, 70 passed |
| 59 | Stop asking when the model is gone | the stop flag never set | 3 failed, 70 passed |
| 60 | Events carry no text | the question text added to a progress event | 3 failed, 70 passed |
| 61 | The log holds a code, not the error's text | the exception itself logged | 1 failed, 72 passed |
| 62 | The run is a background task | the run executed inside `start` | 5 failed, 68 passed (154.9 s: each blocked scenario ends at its 30 s limit instead of hanging the suite) |
| 63 | A workbook with no questions is refused | check removed | 1 failed, 72 passed |
| 64 | An unknown run is a 404 before streaming | check removed | 1 failed, 44 passed (routes tests) |
| 65 | The routes sit behind the token | the auth middleware not applied | 18 failed, 51 passed (routes and middleware tests) |
| 66 | Malformed requests do not echo values | the validation handler removed | 11 failed, 34 passed |
| 67 | `Retriever.vault_withheld` | always returns nothing | 1 failed, 14 passed |
| 68 | `current_file_sha256` | hashes the path, not the text | 3 failed, 12 passed |

## Mutation proofs (M5l)

| # | Control | Mutation | Result |
|---|---|---|---|
| 69 | The reopen check compares numbers as numbers | the float branch of `_stored_as_intended` back to `type(stored) is float and stored == intended` (the old behaviour) | 6 failed, 66 passed: `test_a_whole_number_confidence_is_written_and_verified[0.0]` and `[1.0]`, `test_the_reopen_comparison_is_exact_in_value_and_kind[0-0.0-True]` and `[1-1.0-True]`, and both end-to-end runs (`write_verify_failed`). Restored; `writer.py` sha256 matches the pre-mutation hash. |
| 70 | The kit refuses a non-live run at port 11434 | `if False and ...` in front of the port check in `_refuse_real_ollama_unless_live` (retried with the owner's approval after a first denial) | 1 failed: `test_an_offline_scenario_refuses_the_real_ollama_port`. Restored; `e2ekit.py` sha256 matches the pre-mutation hash. |

## Live check B results: the real sidecar, the demo workbook (2026-10-12)

Read from the owner's result files (`lcb-3-demo.txt`, `lcb-4-hash-before-excel.txt`, `lcb-4-hash-after-excel.txt`, `lcb-5-excel-notes.txt`, `lcb-6-wifi-off.txt`, `lcb-7-excel-copy.txt`, `lcb-7-hash-before.txt`, `lcb-7-hash-after.txt`), which are outside the repository. Nothing was run to write this section. Every run used `llama3.1:8b` with digest `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e` before and after, the real sidecar as a child process, 15 questions, 15 progress events, `complete`, no dropped parts and no warnings. Where a file shows more than one timing, the figure below is the pytest final total, and the run's own `seconds` is given beside it.

| Run | Test result | Pytest total | Run's own seconds | Status counts (SUPPORTED / PARTIAL / NEEDS_REVIEW) |
|---|---|---|---|---|
| a. Demo end to end | 1 passed | 771.88 s (0:12:51) | 765.2 | 11 / 2 / 2 |
| b. Wi-Fi off | 1 passed | 654.82 s (0:10:54) | 647.6 | 9 / 3 / 3 |
| c. Excel-saved copy | 1 passed | 662.12 s (0:11:02) | 655.9 | 11 / 3 / 1 |

**a. Demo e2e over the real sidecar.** The stream was read live: first progress event 71.5 s after it opened, last at 764.6 s, done at 765.2 s. The test reports the input hash equal before and after the run (`True`). Separately, the owner hashed the demo input (the file in the run's `questionnaires` folder) before and after opening the output in Excel, and both hash files read `B76037E643A35C0A70F9DC4ED48059DDA9E0F5665A2F520AF0F48C2F1525A5DF`: **equal**. The path column in those two files is truncated by the terminal, so the file named is only the one under `...\grc-live-e2e\a349c771\questionnaires\`, the work folder of this run.

**b. Wi-Fi off.** Passed (first progress event 49.2 s, done 647.6 s; input hash equal before and after, `True`). The owner's offline statement, verbatim: "Wi-Fi was turned off by hand; the network test result was not captured". So this run shows the engine completing with Wi-Fi off; it does not show, by any captured output, that the machine had no route out.

**c. Excel-saved copy.** The input was `excel-saved-copy.xlsx`, a copy saved by Excel (the original is only read). It loaded and ran: no refusal, no dropped parts, no warnings (first progress event 58.4 s, done 655.9 s). Input hash before the run `0186B101C724A2B7963CC1B277A6FC6871549F6990D603ED7FA553BC2B0CCB70`, after `0186B101C724A2B7963CC1B277A6FC6871549F6990D603ED7FA553BC2B0CCB70`: **equal**, and the test reports `True`. Output file `excel-saved-copy-UNREVIEWED-DRAFT-bf2b4934.xlsx`. The result files do not say whether this copy contains an empty chart sheet, so they do not show whether the known limit in decision 3 was exercised.

**d. Part G, manual inspection** (the owner, by eye, `lcb-5-excel-notes.txt`; the owner confirmed it was run a's output, the demo e2e run whose input was hashed before and after opening it in Excel): original columns and text unchanged; six new columns present (GRC Answer, Confidence, Status, Reason, Evidence, Review); the Status header carries the UNREVIEWED DRAFT label; the Evidence column filled for answered rows; the Review column says "unreviewed" on every row; the About sheet preserved; no Excel repair prompt or security warning on open. **Not recorded in the notes:** the F2-then-Enter check on a formula-trigger cell, merged cells and drop-downs by name, and the LibreOffice open.

**e. Run-to-run difference.** The three runs of the same 15 questions ended with different status counts (11 / 2 / 2, 9 / 3 / 3 and 11 / 3 / 1), because CPU inference is not bit-for-bit repeatable even with seed 42 and temperature 0.

Demo question D-06 (the planted 90-vs-365-day conflict, expected blank) ended SUPPORTED in runs a and c and NEEDS_REVIEW with `possible_conflict` in run b; the expectation notes are notes, not promises (decision 18). The held-out results and the model-dependence they show are in ADR-013, "Live check B results".

## Limits of the writing half

- A visible apostrophe survives a CSV export; the quote prefix does not (decision 11).
- Direction controls are removed from answers and kept, with a visible marker, in quotes (decision 11). Homoglyphs and other look-alike characters are not handled.
- A same-user process could swap the file between the input re-hash and the exclusive create (threat model O1).
- Both sides of the read-back comparison are openpyxl's view of the file, not Excel's.
- `\r` cannot survive the file format, so it becomes `\n` (decision 11).

## Limits of the runs and routes (M5k)

- **As of M5k, nothing here had been run against a real Ollama** (live check B has since run it; see "Live check B results"). Every unit test uses a scripted answerer or a mock transport. How a real model, the real index and a real workbook behave together is what live check B (M5l) is for.
- **No route builds the index** (decision 17), and the folders come from the environment until the pickers exist (M6); the host does not pass them yet.
- **Runs live in memory.** A restart forgets the last 20 runs (their output files stay). The event stream has no heartbeat and no resume position: a client that reconnects reads the events again from the first.
- **The HTTP test transport buffers a whole response**, so the tests read a stream after the run it describes has ended, and test concurrency (a held run, `/health`, a second start) with separate requests. A real client reading the stream live is exercised only at live check B.
- **One timeout measurement** (ADR-012, "The read timeout, derived (M5k)").
- **`ConfigurationError` is mapped to `questionnaire_root_unusable`** (503) for every route; today only the questionnaire routes can raise it.

## A test that failed only in the air-gap container (found at M5n)

When the M5n local checks ran the full suite in the `--network none` container (the CI `airgap` job's own command), one test failed: `test_source_hygiene.py::test_no_file_in_the_repository_contains_a_direction_control`. It had been added in `39aa7cd` and had never run under CI (billing block). It found its repository root by counting two folders above the test file; in the image the tests live in `/app/tests`, so that root was `/`, the walk covered the whole filesystem, and it read a hostile fixture another test had written into pytest's temp folder (`hostile/rtl.md`, which holds a direction control on purpose). On Windows the count points at the real repository, so the test passed there and the mistake was invisible. The fix (commit `e709d77`) finds the root by markers (`CLAUDE.md` and `sidecar/pyproject.toml`, never a filesystem root); with no root the test skips with an explicit reason, which only the `airgap` job allows, and negative tests cover a missing marker. Mutation proof: with the marker check changed from "all" to "any", `test_no_root_is_returned_when_a_marker_is_missing` fails for the two single-marker cases (2 failed, 18 passed); restored, the file hash matches. Limit: the separate guard against a filesystem root has no mutation proof, because without it the marker check alone still returns nothing there.
