# ADR-010: Hybrid retrieval, a lexical FTS5 table beside the vectors

Status: Accepted for the store (M3b). Query syntax, scoring and merging are decided in M4, which must also update CLAUDE.md 8.2 ("Graph RAG" retrieval flow) when retrieval uses the lexical table.

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

## Consequences

Positive: exact control IDs are findable even when an embedding blurs them, and a document that changes loses its lexical rows in the same transaction as its vectors. The tokenizer is built into SQLite (no package, no network).

Negative or risky:

- The table roughly doubles the text stored per chunk (the text is in `chunks` and in `fts_chunks`). The index is disposable, so this costs disk only.
- The quoting rule (item 3) is easy to forget. `tests/test_fts.py` pins the behaviour of the chosen tokenizer for every row of the table above, so a change to the tokenizer fails a test and has to update this ADR.
- Changing the tokenizer is a schema change: `SCHEMA_VERSION` is bumped and every index is rebuilt (ADR-009), which is cheap because the index is derived.

Revisit if a framework's identifiers use a character that the tokenizer splits in a way that breaks adjacency (none found so far), or if M4 finds the lexical side adds nothing the vectors do not already find.
