# ALIBI design rules (M6 core)

Status: **Built in M6 core except where a line says otherwise** (marked at the 6i close, 2026-10-16). It was written in chunk 6a2 from `docs/product-brief.md` and `M6_PLAN.md` (sections C, D and E), not from any design skill or template, and chunks 6f to 6h built the screens from it. **Not built or not decided:** colour values are neutral placeholders (a test enforces only the 4.5 to 1 and 3 to 1 minimums for the listed pairs; the 7 to 1 target is not enforced or measured), there is no light and dark pair, and the label wording is interim until the owner approves it (Section 10). If this file disagrees with an ADR, the threat model or CLAUDE.md, they win. Everything here applies to a **debug build only until M8**: there is no installer, and release builds cannot start the sidecar until then.

Today the window is a shell with a status bar and four screens (folders, index the vault, questionnaire, review queue), styled by one bundled stylesheet, `apps/desktop/src/styles.css`, in a 1100 by 720 window with a 900 by 600 minimum. Tailwind 4.3.3 is installed, with `@tailwindcss/vite` 4.3.3 (exact pins, MIT, build-time dev dependencies; PR #43 body); the tokens below are CSS custom properties exposed as Tailwind utilities. The colour values in the file are placeholders for the roles in 3.1; one test enforces only 4.5 to 1 for text and 3 to 1 for large text, borders and the focus ring on the listed pairs (3.1).

## 1. Purpose and principles

ALIBI's promise is that every answer is *backed*: it cites the exact lines behind it, says "no evidence" instead of guessing, and never claims more than code checked (`docs/product-brief.md`). The screens must not undo that. Five rules outrank any style choice:

1. **Status words come from code.** An exhaustive switch maps `SUPPORTED`, `PARTIAL` and `NEEDS_REVIEW` to a fixed label. An unknown value renders a visible error, never a label (F36-2, built).
2. **Fixed statements are always visible.** They are not in a tooltip, a collapsed section or hover text.
3. **Status is never colour alone.** Each status also has its own label and its own shape.
4. **Model text is data.** It is shown as plain text and is never given the look of a verdict or an instruction (Section 8).
5. **Not configured is never "0 results".** Every screen has distinct empty, loading, stale, model-unavailable, busy and error states (Section 7).

Not in scope: brand identity, logos, illustration, marketing pages. Nothing here authors the product brief.

## 2. Window

| Property | Value (built, chunk 6f-B) |
|---|---|
| Default size | 1100 by 720 |
| Minimum size | 900 by 600 |
| Resizable | Yes (before 6f-B the window was fixed at 800 by 600) |
| Windows | One, labelled `main` |
| Zoom | Usable at 200% zoom at the 900 by 600 minimum (**not checked**: the owner's close checklist was run once by hand on 2026-10-16 and no 200% zoom result is recorded) |

The window keys are pinned by a test (`apps/desktop/src-tauri/tests/security_config.rs::the_window_is_resizable_with_the_decided_default_and_minimum_sizes`; mutation proof: `minHeight` changed to 500 failed it, PR #45 body).

## 3. Tokens

### 3.1 Colour: roles, not values

**No colour value is given here as passing.** Chunk 6f set neutral placeholder values for these roles in `apps/desktop/src/styles.css` (one scheme, no light and dark pair); `apps/desktop/scripts/contrastTokens.test.mjs` reads `apps/desktop/src/styles.css` and fails if a listed text pair is below 4.5 to 1 or a listed large-text, border or focus-ring pair is below 3 to 1. It covers one scheme and enforces nothing else. The 7 to 1 target below is **not** enforced and **not** measured. This section lists roles and targets only.

| Role | Used for |
|---|---|
| `surface`, `surface-raised` | Window background and panels |
| `text`, `text-muted` | Body text and secondary text |
| `border`, `focus-ring` | Dividers and the keyboard focus indicator |
| `status-supported`, `status-partial`, `status-needs-review` | The three evidence statuses (3.2) |
| `status-error` | A refusal or a failed state |
| `statement-bg`, `statement-text` | The fixed-statement block (4.1) |
| `badge-bg`, `badge-text` | The unreviewed-draft badge (4.2) |
| `edited-bg`, `edited-text` | The edited-cell lines (4.3, 4.4) |

**Contrast targets:** WCAG 2.2 AA at minimum, meaning 4.5 to 1 for text, 3 to 1 for large text and for interface components and the focus indicator; and **7 to 1 for status words and for the fixed statements**, because they are honesty controls. Both a light and a dark scheme are planned. Today one scheme exists, and `contrastTokens.test.mjs` enforces only the 4.5 to 1 and 3 to 1 minimums on the listed pairs; the 7 to 1 target is **not** enforced or measured, so **nothing in this document claims a pair meets 7 to 1**.

No transparency and no blur anywhere, so a "reduce transparency" preference has nothing to change. (Whether the WebView2 runtime supports that preference is **not verified**, and nothing relies on it.)

### 3.2 Status: label, shape and colour together

The label is the fixed word, set by the exhaustive switch in `apps/desktop/src/status.ts` (the file calls the strings interim until the owner approves them, and `status.test.ts` pins them). Colour is the third signal, never the only one.

| Status | Label (built, interim) | Shape (built) | Meaning shown with it |
|---|---|---|---|
| `SUPPORTED` | Supported | Filled circle with a check mark | Every code check passed; this is **not** a statement that the answer is correct (4.1) |
| `PARTIAL` | Partial | Half-filled circle | The checks passed except for a limit; the limits are listed in the row's reasons |
| `NEEDS_REVIEW` | Needs review | Triangle with an exclamation mark | The answer cell is blank and the reasons say why |

The three shapes differ by outline as well as fill, so they stay distinguishable in `forced-colors` mode and in greyscale. An unknown status uses none of these shapes: it shows an error block (Section 7).

### 3.3 Type

System font stack only, with no remote font and no font file fetched at runtime (the CSP allows none, ADR-005).

| Token | Stack (built) |
|---|---|
| `font-body` | `system-ui`, `"Segoe UI"`, `sans-serif` |
| `font-mono` (quotes, file names, codes) | `ui-monospace`, `"Cascadia Mono"`, `Consolas`, `monospace` |

Type scale (starting values; the screens use Tailwind's own scale, and nothing here claims they were measured against the 900 by 600 minimum):

| Token | Size | Use |
|---|---|---|
| `text-xs` | 0.75rem | Counters, reason codes |
| `text-sm` | 0.875rem | Table cells, secondary text |
| `text-base` | 1rem | Body, fixed statements |
| `text-lg` | 1.125rem | Section headings |
| `text-xl` | 1.5rem | Screen titles |

Fixed statements and status words never use `text-xs`.

### 3.4 Spacing and shape

A 4 px base, with steps of 4, 8, 12, 16, 24 and 32 px (a target; Tailwind's spacing scale is used). Corners are square or at most a small fixed radius. No shadows that rely on transparency.

### 3.5 Tailwind

Adopted in chunk 6f-A (PR #43): `tailwindcss` 4.3.3 and `@tailwindcss/vite` 4.3.3, exact pins, MIT, build-time dev dependencies; the PR body reports no install scripts or telemetry in the packages or their direct dependencies, and that `lightningcss` (MPL-2.0) arrives transitively (see `docs/dependency-licenses.md`). **Proof in a bundled debug build** (PR #43 and PR #45 bodies, hand checks): the built CSS has no `@import`, `@font-face` or `url()`, `dist/index.html` has no `<style>` tag and no `style=` attribute, and the page was styled under the shipped CSP in the bundled debug window. No component library, no shadcn, no jsdom, no Playwright and no React Flow in M6 core.

## 4. Components

### 4.1 Fixed statement block (built, chunks 6g and 6h; the words are `apps/desktop/src/fixedTexts.ts`, which tests compare to the owner's literals)

A bordered block with `statement-bg` and `statement-text`, text at `text-base` or larger, in the normal reading order and never collapsed, on hover or behind a control. One statement is required on every `SUPPORTED` or `PARTIAL` row of the review queue:

> The quote is real company text that was in the context. ALIBI has not checked that it answers this question.

(Copied from `docs/adr/021-read-only-routes-and-review-queue.md` decision 7. The ADR sets the word "not" in bold; the screen shows the words as plain body text.)

The other fixed statements of M6 core:

| Where | Text | Source |
|---|---|---|
| Edited Status cell | The Status cell in the workbook was changed after drafting. The code's verdict is shown, not the cell. | ADR-021 decision 4 |
| Edited Answer cell | The Answer cell was changed after drafting. The citation belongs to the original answer. | ADR-021 decision 4 |
| Folders screen | Saved. Takes effect the next time you start ALIBI. | `M6_PLAN.md` C.2 |
| Folders screen, next to the ledger row | Set the ledger folder before your first questionnaire run if you want to use the review queue. A run without it keeps no run record, and its draft cannot be opened in the queue. | `M6_PLAN.md` C.2 |
| Before Start, no ledger folder in use | No ledger folder is in use. This run will keep no run record (`run_record_not_kept`) and its draft cannot be opened in the review queue. Set the ledger folder and restart ALIBI first, or run anyway. | `M6_PLAN.md` C.4 |
| During a run | each answer can take 40 to 60 seconds on a CPU-only laptop (measured on the owner's machine) | `M6_PLAN.md` C.4 |
| After a run with no record | No run record was kept: this draft cannot be opened in the review queue | `M6_PLAN.md` C.4 |
| Unknown run | this run is no longer known (only the last 20 are kept and none survive closing ALIBI); the file is in the exports folder | `M6_PLAN.md` C.4 |

Statements whose words were left open in 6a2 and set in chunk 6h (`apps/desktop/src/fixedTexts.ts`; the wording is for the owner's approval, PR #47 body): when a changed question cell makes the whole queue refuse, "A question in this file was changed after ALIBI drafted it, so ALIBI cannot match the rows to its record and shows none of them. Open the original draft instead, or run the questionnaire again. (Code: question_mismatch)"; the line shown in every state of the queue, "This queue is for reading. ALIBI does not record approval. To approve an answer, type "approved" in the Review column of the workbook, then import it into the ledger (the import screen comes in a later chunk)."; and for an edited Evidence cell, "The Evidence cell in the workbook was changed after drafting. The citation shown is ALIBI's record, not the cell." The recall "intact" statement and the numbers-only statement belong to the M6.5 screens, which must carry them.

### 4.2 Unreviewed-draft badge (built)

The text `UNREVIEWED DRAFT`, shown wherever an ALIBI answer appears: the run summary, the queue header and each answer. It uses `badge-bg` and `badge-text`, is always visible and carries no icon that could be read as approval. Review state is always `unreviewed` in M5 (ADR-013), and the queue records no approval, so the badge never changes to anything else.

### 4.3 Edited-status line (built, chunk 6h)

For a row whose Status cell was changed in the workbook: show the run record's status with the label **recorded by ALIBI**, then the edited-status statement from 4.1, in `edited-bg` and `edited-text`. The edited cell's text is **never** shown and never styled as a verdict (the route does not return it, ADR-021 decision 4). The row sorts first, with `NEEDS_REVIEW` rows.

### 4.4 Edited-answer line (built, chunk 6h)

For a row whose Answer cell was changed: show the answer with the edited-answer statement from 4.1. The citation shown is the run record's, never the cell's. A row whose Evidence cell was changed also shows a warning that the citation is ALIBI's record, not the cell.

### 4.5 Status chip, folder row, progress text (built, chunks 6f and 6g)

- **Status chip:** label plus shape (3.2), never colour alone.
- **Folder row:** two columns, "In use now" and "Saved for next launch", shown separately when they differ. Rows are in the recommended order: vault, ledger, questionnaires, exports, imports, and the ledger row is marked "needed for the review queue".
- **Progress text:** `completed / total` from the status route, a local "time since the last answer" counter, and "writing the answers file" after `completed == total` while the status is still running. **No ETA, no smooth percent bar, and never the word "stuck"** (the count changes only when a question finishes). Progress is announced politely and throttled.

## 5. Review queue layout (built, chunk 6h; keyboard navigation of the table was not checked)

Rows are ordered by risk: edited-status rows and `NEEDS_REVIEW` first with the code's reasons, then `PARTIAL`, then `SUPPORTED`. Each row shows the question, the draft answer, and the source file, exact lines and verbatim quote side by side. Reasons are always visible and never hidden to shorten a row. The table is navigable by keyboard. The queue has no approve control and no control that calls a command writing a workbook.

## 6. Accessibility and motion

- Everything is reachable by keyboard in a logical order with a visible focus indicator and no keyboard trap.
- `prefers-reduced-motion: reduce` removes non-essential animation. Progress is text and numbers, so nothing essential depends on motion.
- **`forced-colors`:** checked once by hand in Windows high contrast at the close checklist on 2026-10-16 (a screenshot of the Folders screen only; the Review queue rows were not screenshotted; an observation, not a test, in `M6_PLAN.md` "M6 close check results"). Meaning must survive: labels and shapes carry the status, and borders carry block boundaries.
- To be checked at 200% zoom at the 900 by 600 minimum (not run).
- A test checks the 4.5 to 1 and 3 to 1 targets in 3.1 against the tokens of the one scheme that exists; the 7 to 1 target and a dark scheme are not checked. Keyboard-only use and reduced motion were not measured in M6 core.

## 7. States: empty, loading, stale, error

Every screen has all of these, written as fixed words (`M6_PLAN.md` C):

| State | Rule |
|---|---|
| Empty | Says exactly what is empty and what to do ("no .xlsx or .xlsm files in the folder"), never an empty table with no text |
| Not configured | Says a folder is not set and that a change applies at the next launch; never "0 results" |
| Loading | Names what is being read and, where true, that the file is not changed ("reading only, the file is not changed") |
| Stale | Says what is out of date (files withheld because they changed after indexing, an index that must be rebuilt) |
| Model unavailable | Lists the exact missing model names and says "ALIBI never downloads models; install them with Ollama yourself"; buttons that need a model are disabled with that reason |
| Busy | `409 run_in_progress` is shown as "another job is running", with no retry loop |
| Typed refusal or error | Each code from a closed set has its own fixed message; no raw sidecar text ever reaches the window |
| Unknown value | A visible error block, never a guessed label |

Text in these states is written in plain words for a compliance lead who is not a programmer, with the code shown in monospace beside it where it helps.

## 8. Rules for model and file text (plain text only)

Question cells, draft answers, quotes and file names are untrusted data (CLAUDE.md 8.6). The rules, built in chunks 6f to 6h and each backed by tests in the threat model (F36-1 to F36-4, with the limits stated there):

- **React text nodes only.** No `dangerouslySetInnerHTML`, no `innerHTML`; a lint rule and the existing Semgrep rule ban them.
- Shown with `white-space: pre-wrap` and `unicode-bidi: isolate`.
- Control and direction-control characters are shown as visible marks, not interpreted.
- No automatic links. Citations are plain text a person can look up; ALIBI sets no hyperlinks.
- Long text is collapsed behind a "show more" control, **except** fixed statements and status words, which are never collapsed.
- Model text never takes the style of a fixed statement, a status or an error.

## 9. Assets and the CSP

No remote assets, no remote fonts, no CDN. Everything is bundled. The shipped policy (ADR-005) is `default-src 'self'` with `style-src 'self'`, so there is **no inline `style=` attribute and no `<style>` tag**; styling comes from bundled CSS files only. `pnpm tauri dev` injects no CSP, so a violation does not show there and is checked in the bundled debug build (ADR-005; hand checks in PR #42, #45 and #46 bodies; the close checklist was run once by hand on 2026-10-16: observations, not tests, in `M6_PLAN.md` "M6 close check results" and threat model F5-1b and G12).

## 10. What is not decided here

- Colour values (placeholders today; the light and dark pair is not done, and the 7 to 1 target is not enforced; a test enforces the 4.5 to 1 and 3 to 1 minimums for one scheme).
- The owner's approval of the status labels and of the wording first written in chunk 6h (4.1).
- Whether release builds ship DevTools (not checked, not verified).
- Anything about skills: nothing is installed in M6 core, and the decision is made at the start of M6.5 after the owner and the reviewer have read the repositories themselves.
- The M6.5 screens (ledger, recall, blast radius, contradictions, reality, graph view).

## 11. M6.5 screens (planned, not built; for the owner's approval)

Written 2026-10-18 in chunk 65a from `M6_5_PLAN.md` and `docs/adr/022-m6-5-screens-writes-and-approval.md`. **Nothing in this section is built, and every word of it is for the owner's approval.** It applies to a **debug build only until M8**. The rules of sections 1 to 9 apply to every M6.5 screen. No new colour token is planned; if one is added it needs a pair in `apps/desktop/scripts/contrastTokens.test.mjs`, which fails on an uncovered token.

### 11.1 Fixed statements (drafts)

Each is defined once as a window constant (as in section 4.1), shown in body text and never collapsed. Where the sidecar also sends the sentence, the reply schema pins it with a Zod literal on the same constant, so a drifted sentence makes the reply invalid instead of showing different words.

| Where | Draft text | Source |
|---|---|---|
| Recall, above results and in each item | The sidecar's `STATEMENT`: "intact means the cited lines are unchanged since drafting; it does not mean the promise is still true." and its `SCOPE` | `docs/adr/017-promise-ledger-run-record-and-recall.md` decision 17; threat-model F31-4b |
| Recall, before running | Checking records a change of state in the ledger when one is found. It never edits a promise. | ADR-017 decision 20 |
| Ledger | A promise is an answer you told ALIBI you sent to a customer. ALIBI never sends anything. | ADR-017 decisions 1, 11 |
| Ledger | Immutable here means ALIBI has no way to change or delete a ledger row. It is not tamper-evident: another program running as you could edit the file. | ADR-017 decision 9 |
| Void, second step | Voiding cannot be undone. The promise stays in the ledger, marked void, and is left out of recall. | ADR-017 decision 13 |
| Acknowledge | Acknowledging records that you saw this state on this date. It does not change the promise. | ADR-017 addendum (G35) |
| Acknowledge, stale | This promise's state changed after you acknowledged it. | ADR-017 addendum (G35) |
| Ledger, promises page too large | This page of promises is too large to show. Choose a smaller page size. | `M6_5_PLAN.md` N6 |
| Blast radius | The sidecar's `PREVIEW_LINE`, `NO_MATCH_LINE`, `STATEMENT` and `SCOPE`, and: Blast radius checks promises only. It does not check controls or the documents that link to this file. | ADR-018; threat-model F32-4, G36 |
| Blast radius | New: this flag is not on the file as it is now. | ADR-018 addendum (`flags_now`) |
| Blast radius | The text you paste stays in this window for this preview only. ALIBI does not save it. | ADR-018 decision 7 |
| Contradictions | The sidecar's numbers-only statement, always visible when results show | ADR-019; threat-model F33-9 |
| Contradictions | The model's answer for the same pair can differ between identical runs. | threat-model F33-11, G42 |
| Contradictions | Findings are not kept. Closing ALIBI or starting another run drops them. | ADR-019 decision 1 |
| Contradictions | Both quotes are shown so you can decide. ALIBI does not decide which statement is right. | threat-model F33-10 |
| Contradictions, `VALUE_CONFLICT` label | Numbers differ (the model kept this pair in this run). Never "detected" or "confirmed". | ADR-019, "What the results say, and what they do not" |
| Contradictions, `CANDIDATE` label | Numbers differ (not kept), with the reason shown. | ADR-019 |
| Reality check, result | This export shows users in scope without MFA, by the MFA types you confirmed. / This export shows no users in scope without MFA, by the MFA types you confirmed. Never "compliant". | ADR-016 decision 11 |
| Reality check | The sidecar's `STATEMENT`, `STALENESS_NOTE` and `DUPLICATE_NOTE` | ADR-016 |
| Reality check, phone | Counting phone (text message or voice) as MFA is a policy choice: phone codes can be phished or intercepted. | ADR-016 decision 6 |
| Reality check, logins | The logins below are personal data. ALIBI does not save or log them. (Not "gone when you leave this screen": the App-level hooks are rendered in no test, F6-1c, so that would need a tested pure reset first.) | threat-model F26-7 |
| Import | Approval happens in the workbook: type "approved" in the Review column, save the file with Save (not Save As), then import it here. | ADR-017 decisions 1, 2 |
| Import | ALIBI only reads the workbook. It never saves it. | ADR-014 decision 10; ADR-021 decision 7 |
| Import | Importing creates promises that cannot be deleted, only voided. A new customer cannot be removed. | ADR-017 decisions 8, 10, 13 |
| Import | The date is your statement of when you sent these answers. ALIBI cannot check it. | ADR-017 decision 11 |
| Review queue approval line | The existing line in section 4.1, without its clause "(the import screen comes in a later chunk)" once the import screen exists. | ADR-021 addendum |
| Graph | Lines are the explicit links written in the files. A missing line does not mean two documents are unrelated. | CLAUDE.md 8.2 |
| Graph | This map is not evidence that a control works or that a promise is true. | ADR-023 |
| Graph, no ledger folder | Promises: not available (no ledger folder in use). Never "0". | ADR-023 decision 5 |

### 11.2 The two-step confirm component

For an irreversible write (a void, an import, and by extension an acknowledgement): the first click opens an in-page area that restates, in fixed words from 11.1, what will happen and what cannot be undone, and shows the target (the customer name or the promise as plain text). A second, separate button carries out the write. Cancel closes the area and calls nothing. It is a bordered block in the section 4.1 style; it never uses `confirm()` (the dialog plugin overrides it, deviation M6-22), a tooltip or colour alone. The write is made by a pure action module that calls the route only when it is handed a `confirmed` value that only the confirm reducer produces; it is tested with a recording fake `invoke`.

### 11.3 The "new flag" marker

On a blast-radius item, a proposed flag that is not among the item's current flags (`flags_now`) is shown with the text "New" in front of its word, in a bordered inline box. The marker is text and shape, never colour alone, and the fixed line "New: this flag is not on the file as it is now." is always visible on the screen. "New" is worked out in a pure tested function (proposed flags minus current flags), not from the response's words.

### 11.4 Navigation groups

Proposed groups for the shell (names are drafts): **Questionnaires** (Folders, Index the vault, Questionnaire, Review queue), **Promises** (Ledger, Recall, Blast radius, Import), **Checks** (Contradictions, Reality check), **Map** (Graph). The current screen keeps the section 4.5 marker (`aria-current`, underline and thick border). The grouped navigation **must be hand-checked at the 900 by 600 minimum window** in the bundled debug build; no test covers the real layout (threat-model F44-4, planned).

### 11.5 What is not decided here

- Every word in 11.1 to 11.4.
- Whether React Flow is adopted for the graph view (`docs/adr/023-graph-view.md`); the list view comes first.
- Colour values and the dark scheme (section 10 still applies).
