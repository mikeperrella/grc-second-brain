# ADR-014: Questionnaire files and runs

Status: Accepted (M5i, the reading half). The writer, the run orchestration and the routes are built in M5j and M5k, and this ADR is extended then: sections marked "later" below are decisions already taken in the plan, not yet built. Facts about the packages and the numbers below were checked on 2026-10-12 on the Windows development machine.

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

**Later (M5j, M5k), already decided in the plan:** the writer is the only module that saves; new columns right of the used range with `[UNREVIEWED DRAFT]` titles; typed string cells with a quote prefix to neutralize `= + - @`; build, save to memory, reopen and verify, re-hash the input, exclusive-create the file, re-read and compare; delete only the file just created on a mismatch; paths through `resolve_within` against `GRC_QUESTIONNAIRE_DIR` and `GRC_EXPORT_DIR`, neither inside nor containing the vault or index folders; the routes and server-sent events; a refused run writes no file. Not built yet.

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
