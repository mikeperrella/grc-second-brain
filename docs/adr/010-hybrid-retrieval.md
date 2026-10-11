# ADR-010: Hybrid retrieval, a lexical FTS5 table beside the vectors

Status: Accepted for the store (M3b). Query syntax and lexical search are implemented in M4d (below). Merging, the context budget and the rest are decided in M4e and M4f, which must also update CLAUDE.md 8.2 ("Graph RAG" retrieval flow) when retrieval uses the lexical table (done in M4g).

## Context

Questionnaire questions and policy text are full of exact identifiers: `AC-2`, `AC-2(1)`, `CC6.1`, `A.5.15`, `IA-2`, `3.1.1`. An embedding blurs them (`AC-2` and `AC-20` are near neighbours in vector space), while an exact word match finds the one control a question names. The reverse holds for paraphrase: "multi factor authentication" and "MFA" are close as vectors and unrelated as words. So the index keeps both, and retrieval can use both.

## Decision

1. `fts_chunks`, an FTS5 virtual table over the chunk text and heading path (identifiers often sit in headings such as "AC-2 Account Management"), lives in the same SQLite file as `chunks` and `vec_chunks`. Its rowid equals `chunks.rowid`. `replace_document`, `remove_document` and `begin_sync` write and delete it in the same transaction as the chunks and vectors (ADR-009), so a lexical hit can never come from a version of a file the vector side no longer has.
2. **Tokenizer: `unicode61 tokenchars '-'`.** The hyphen is a token character, so `AC-2` is one token. The dot is a separator, so `CC6.1` is the two tokens `cc6` and `1`, and a phrase query (`"CC6.1"`) matches them adjacent.
3. **Identifier queries must be phrase-quoted** (`"AC-2(1)"`, not `AC-2(1)`). Unquoted, FTS5 reads `-`, `(` and `.` as syntax. M4 owns the query builder and must quote.
4. M3 exposes **no lexical query method**. The tests query the table directly. Nothing in M3 scores, merges or ranks lexical hits.

## The tokenizer comparison (spike, 2026-10-09)

Method: a small synthetic corpus of heading-like and sentence-like rows, including longer-identifier "trap" rows (`AC-20`, `CC6.10`, `A.5.152`, `3.1.10`, `13.1.1`), identifiers inside prose (`AC-2,`  `(AC-2)`, `CC6.1.` and `A.5.15.` at the end of a sentence), and a distractor row with the same characters spaced out (`AC 2`, `3 1 1`). Each query was run as an FTS5 phrase. Identical results on Windows (Python 3.11.9, SQLite 3.45.1) and in the `--network none` container (Python 3.11.16, SQLite 3.46.1). "Exact" means the rows that really cite that identifier. "Child" rows (`AC-2(1)` for `AC-2`) are counted as acceptable, not as errors.

| Query | `unicode61` default (control) | **`unicode61 tokenchars '-'` (chosen)** | `unicode61 tokenchars '-.'` (rejected) | `trigram` (rejected) |
|---|---|---|---|---|
| `AC-2` (also `ac-2`) | all exact + child; **extra: "AC 2" distractor** | all exact + child, no extras | all exact + child, no extras | all exact + child; **extra: `AC-20`** |
| `AC-2(1)` | the one row | the one row | the one row | the one row |
| `CC6.1` | both; **extra: "CC6 1" distractor** | both (including the sentence-final `CC6.1.`); extra: "CC6 1" distractor | **misses the sentence-final `CC6.1.`** | both; **extra: `CC6.10`** |
| `A.5.15` | both; extra: "A 5 15" distractor | both (including the sentence-final `A.5.15.`); extra: "A 5 15" distractor | **misses the sentence-final `A.5.15.`** | both; **extra: `A.5.152`** |
| `IA-2` | exact + children; **extra: "IA 2" distractor** | exact + children, no extras | exact + children, no extras | exact + children, no extras |
| `3.1.1` | exact; **extra: "3 1 1" distractor** | exact; extra: "3 1 1" distractor | exact; **misses a sentence-final `3.1.1.`** | exact; **extras: `3.1.10`, `13.1.1`** |
| `03.01.01` (SP 800-171 Rev 3 format) | the one row, trap `03.01.10` not hit | the one row, trap not hit | the one row, trap not hit | the one row, trap not hit |
| `5.15` (ISO 27001, written without the `A.`) | `5.15` rows **and `A.5.15`** | `5.15` rows **and `A.5.15`** | `5.15` rows; **does not find `A.5.15`** | `5.15` rows and `A.5.15`; **extras: `A.5.152`, `15.15`** |
| `multi-factor` | hyphenated **and** spaced rows | hyphenated rows only | hyphenated rows only | hyphenated rows only |
| `multi factor` | hyphenated **and** spaced rows | spaced rows only | spaced rows only | spaced rows only |

Reading the table:

- **`tokenchars '-.'` (the first candidate in the M3 plan) is rejected.** A dot that is part of a token also sticks to a sentence-final dot, so `CC6.1.`, `A.5.15.` and `3.1.1.` become tokens that no identifier query can match. Policy prose cites controls at the end of sentences, so this is a silent miss on the most important inputs.
- **`trigram` is rejected.** It matches substrings, so every identifier also matches every longer identifier that begins with it (`AC-2` finds `AC-20`, `CC6.1` finds `CC6.10`, `A.5.15` finds `A.5.152`, `3.1.1` finds `3.1.10` and `13.1.1`). A hybrid search would then promote the wrong control with a lexical score that looks exact.
- **`unicode61` default** behaves like the chosen tokenizer on this corpus when queries are phrase-quoted, but treats `AC 2` and `AC-2` as the same, so spaced-out text (a part number, a date) matches identifier queries. Keeping the hyphen as a token character removes that class for the identifiers that use one. The distractor rows that remain for `CC6.1`, `A.5.15` and `3.1.1` come from the dot being a separator, which is the price of matching sentence-final identifiers.
- **A control ID's children are hits, deliberately.** `AC-2` finds `AC-2(1)` and `IA-2` finds `IA-2(1)`, because an enhancement belongs to its parent control. The reverse does not hold.

## What the tokenizer does not do (owned by M4)

- **`3.1.1` does not find `03.01.01`, and `5.15` is not guaranteed to find `A.5.15`.** These are different ways of writing one control. They are **query normalization, not tokenizer choice**: M4's query builder must expand an identifier into its known written forms (for example `3.1.1` into `3.1.1` and `03.01.01`; `5.15` into `5.15` and `A.5.15`) and OR them. The chosen tokenizer happens to match `5.15` against `A.5.15` as a side effect of the dot being a separator, which M4 must not rely on (the `-.` candidate, for instance, does not).
- **`multi-factor` and `multi factor` do not meet lexically.** The hyphen stays inside the token, so the hyphenated and spaced spellings are different tokens. Matching them is the job of the vector side of the hybrid search, where the two spellings embed close together. A lexical-only path would miss one of them, so a caller that needs both must not use the lexical table alone.
- Scoring (BM25 or otherwise), merging lexical and vector hits, and query syntax belong to M4.

## Query builder and lexical search (M4d, implemented)

`retrieval/identifiers.py` is a table of identifier rules (`nist-800-171-r3`, `iso-27001-annex-a`, `nist-800-53`, `nist-csf-2.0`, `soc2-tsc`, `generic-hyphenated`), each with a pattern and a function that returns the written forms (`3.1.1` and `03.01.01`; `5.15` and `A.5.15`; `AC-2(1)` and `AC-02(1)`; `PR.AA-01` and `PR.AA-1`; `CC6.1` and `SOC2:CC6.1`). It is format normalization only: nothing says two controls mean the same thing, and no form carries a revision. A test per row has positive and near-miss tokens. `retrieval/query.py` turns user text into an FTS5 MATCH string: the text is cut at 2,000 characters, control characters become spaces, words are split on whitespace, surrounding punctuation (quotes, brackets, Markdown `*` and `_`, sentence punctuation) is trimmed, a small fixed list of stop words is dropped (not `and`, `or`, `not`, `near`), identifiers are expanded to all their forms, at most 32 terms are kept, and **every term becomes one double-quoted FTS5 string with each inner `"` doubled**. The only syntax in the result is the ` OR ` the builder writes between terms: no parentheses, `*`, `^`, `:` or operators are ever generated, so `NEAR(a b)`, `a*`, `col:x`, `AND`, unbalanced quotes and the rest are literal words. A quoted string is tokenized by the same tokenizer as the index, so a phrase matches exactly what was indexed; a test compares the real `fts5vocab` terms of every identifier form with the independent oracle. Words are ORed and ranked by BM25 (`lexical_search`: parameterised `MATCH ?`, `ORDER BY bm25, chunk_id`, so ties never depend on insertion order). Limits: an identifier is recognised only as a whole whitespace-delimited word; the digits-with-spaces row (`3 1 1`) is still a false positive of the dot-as-separator tokenizer (above); a string such as `1.2.3` or `6.1` is treated as an identifier and also searched in its other forms (harmless noise); `control_mappings` in frontmatter are not searchable (frontmatter is not chunked), deferred to M5.

**Why the oracle is independent.** `tests/oracle_fts.py` re-implements the tokenizer from its definition and imports nothing from `grc_sidecar` (an AST test fails on any import, relative or aliased, and is itself tested on bad samples); if the builder had a bug, an oracle made from the same code would share it. `test_hostile_inputs_never_change_meaning` compares the rows found for 40 hostile strings with the oracle's, so a test that only checked "no exception" cannot pass for a builder that quietly changed the meaning.

### Mutation proofs (M4d)

Procedure as in ADR-009 (backup and sha256, one break, run, restore, hash equal, green again). Originals: `query.py` = `4018c8e3a4fd1c946ad3e0332e60beb8330d6310ac4ebef1f08bdb8d5ea8ac0b`, `identifiers.py` = `25f42cd9b17d6c637f14c1ce25ab8a7979ccb1f2ebec9b2f9344a9c245136768`, `sqlite_vec_repo.py` = `dda70013b0a97ca6eea4191665ae8d827d5a8343bad52194c9be3aab3804e80a`, `tests/oracle_fts.py` = `83145befacf2a6d7a28d4300d31079fe8128af2d1497d134f27eaa46e2370dad`. Every restore matched.

| # | Control | Break | Tests that failed | Result |
|---|---|---|---|---|
| 1a | FTS5 escaping: inner quotes doubled | `quote_fts5` stops doubling `"` | `test_query_builder.py::test_hostile_inputs_never_raise[x"y]`, `...::test_hostile_inputs_never_change_meaning[x"y]`, `...::test_hostile_text_never_adds_syntax_outside_quotes[x"y]`, `...::test_quote_fts5_doubles_inner_quotes` | 4 failed, 117 passed (selected tests) |
| 1b | FTS5 escaping: every term quoted | `quote_fts5` returns the text unquoted | `test_hostile_inputs_never_change_meaning[...]` (for example `NEAR(x y)`, `NEAR/3`, `heading_path:ac-2`, `col:x`) and `test_hostile_inputs_never_raise[...]` | 31 failed, 49 passed (selected tests) |
| 10L | Deterministic lexical tie-break | `ORDER BY score, c.chunk_id` becomes `ORDER BY score` | `test_lexical_search.py::test_order_does_not_depend_on_insertion_order`, `...::test_equal_scores_are_ordered_by_chunk_id` | 2 failed |
| 28 | The oracle shares no code with the builder | `from grc_sidecar.retrieval.query import quote_fts5` added to `oracle_fts.py` | `test_query_builder.py::test_oracle_is_independent` | 1 failed (the scan test passed: it does not scan the real file) |
| N1 | Identifier forms | the zero-padded form dropped from the 800-171 row | `test_identifiers.py::test_a_token_gets_every_written_form[nist-800-171-r3-...]` (4) and `test_lexical_search.py::test_every_written_form_finds_the_same_rows[3.1.1-...]`, `[03.01.01-...]` | 6 failed, 79 passed (selected tests) |

Row 1 is the security control. 1a fails on a hostile string that contains an inner quote, which is the only way doubling matters (quotes at the edge of a word are trimmed before quoting); 1b fails on strings whose meaning changes without an error as well as on strings that raise. Row 10L is the lexical half of the deterministic tie-break; the merge half (RRF) is added in M4e.

## Consequences

Positive: exact control IDs are findable even when an embedding blurs them, and a document that changes loses its lexical rows in the same transaction as its vectors. The tokenizer is built into SQLite (no package, no network).

Negative or risky:

- The table roughly doubles the text stored per chunk (the text is in `chunks` and in `fts_chunks`). The index is disposable, so this costs disk only.
- The quoting rule (item 3) is easy to forget. `tests/test_fts.py` pins the behaviour of the chosen tokenizer for every row of the table above, so a change to the tokenizer fails a test and has to update this ADR.
- Changing the tokenizer is a schema change: `SCHEMA_VERSION` is bumped and every index is rebuilt (ADR-009), which is cheap because the index is derived.

Revisit if a framework's identifiers use a character that the tokenizer splits in a way that breaks adjacency (none found so far), or if M4 finds the lexical side adds nothing the vectors do not already find.
