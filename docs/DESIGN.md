# ALIBI design rules (M6 core)

Status: **Planned. Nothing in this document is built.** It is written in chunk 6a2 from `docs/product-brief.md` and `M6_PLAN.md` (sections C, D and E), not from any design skill or template, and the chunks that build the screens (6f to 6h) change a line from "planned" to "built" with their own evidence. If this file disagrees with an ADR, the threat model or CLAUDE.md, they win. Everything here applies to a **debug build only until M8**: there is no installer, and release builds cannot start the sidecar until then.

Today the window is one plain stylesheet (`App.css`, a dark scheme) and a fixed 800 by 600 window that shows one status line (`M6_PLAN.md`, Step 2, a code read). Tailwind is **not** installed; whether to add it is decided at the start of chunk 6f (Section 3). The tokens below are CSS custom properties either way.

## 1. Purpose and principles

ALIBI's promise is that every answer is *backed*: it cites the exact lines behind it, says "no evidence" instead of guessing, and never claims more than code checked (`docs/product-brief.md`). The screens must not undo that. Five rules outrank any style choice:

1. **Status words come from code.** An exhaustive switch maps `SUPPORTED`, `PARTIAL` and `NEEDS_REVIEW` to a fixed label. An unknown value renders a visible error, never a label (F36-2, planned).
2. **Fixed statements are always visible.** They are not in a tooltip, a collapsed section or hover text.
3. **Status is never colour alone.** Each status also has its own label and its own shape.
4. **Model text is data.** It is shown as plain text and is never given the look of a verdict or an instruction (Section 8).
5. **Not configured is never "0 results".** Every screen has distinct empty, loading, stale, model-unavailable, busy and error states (Section 7).

Not in scope: brand identity, logos, illustration, marketing pages. Nothing here authors the product brief.

## 2. Window

| Property | Value (planned, chunk 6f) |
|---|---|
| Default size | 1100 by 720 |
| Minimum size | 900 by 600 |
| Resizable | Yes (today the window is fixed at 800 by 600) |
| Windows | One, labelled `main` |
| Zoom | Usable at 200% zoom at the 900 by 600 minimum (checked by hand in 6g and 6i) |

The window keys are pinned by a test in chunk 6f (`M6_PLAN.md` E.1, item 10).

## 3. Tokens

### 3.1 Colour: roles, not values

**No colour value is given here as passing.** A value is set in chunk 6f and is called passing only if a throwaway script, kept outside the repository, computed its contrast and its output is recorded. Until then this section lists roles and targets only.

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

**Contrast targets (planned):** WCAG 2.2 AA at minimum, meaning 4.5 to 1 for text, 3 to 1 for large text and for interface components and the focus indicator; and **7 to 1 for status words and for the fixed statements**, because they are honesty controls. Both a light and a dark scheme are planned, each checked against these targets by a script. Until that is done, **nothing in this document claims a pair passes**.

No transparency and no blur anywhere, so a "reduce transparency" preference has nothing to change. (Whether the WebView2 runtime supports that preference is **not verified**, and nothing relies on it.)

### 3.2 Status: label, shape and colour together

The label is the fixed word (the exact strings are fixed in chunk 6h with the exhaustive switch). Colour is the third signal, never the only one.

| Status | Label (planned) | Shape (planned) | Meaning shown with it |
|---|---|---|---|
| `SUPPORTED` | Supported | Filled circle with a check mark | Every code check passed; this is **not** a statement that the answer is correct (4.1) |
| `PARTIAL` | Partial | Half-filled circle | The checks passed except for a limit; the limits are listed in the row's reasons |
| `NEEDS_REVIEW` | Needs review | Triangle with an exclamation mark | The answer cell is blank and the reasons say why |

The three shapes differ by outline as well as fill, so they stay distinguishable in `forced-colors` mode and in greyscale. An unknown status uses none of these shapes: it shows an error block (Section 7).

### 3.3 Type

System font stack only, with no remote font and no font file fetched at runtime (the CSP allows none, ADR-005).

| Token | Stack (planned) |
|---|---|
| `font-body` | `system-ui`, `"Segoe UI"`, `sans-serif` |
| `font-mono` (quotes, file names, codes) | `ui-monospace`, `"Cascadia Mono"`, `Consolas`, `monospace` |

Type scale (planned starting values, adjusted in 6f against the 900 by 600 minimum):

| Token | Size | Use |
|---|---|---|
| `text-xs` | 0.75rem | Counters, reason codes |
| `text-sm` | 0.875rem | Table cells, secondary text |
| `text-base` | 1rem | Body, fixed statements |
| `text-lg` | 1.125rem | Section headings |
| `text-xl` | 1.5rem | Screen titles |

Fixed statements and status words never use `text-xs`.

### 3.4 Spacing and shape

A 4 px base, with steps of 4, 8, 12, 16, 24 and 32 px (planned). Corners are square or at most a small fixed radius. No shadows that rely on transparency.

### 3.5 Tailwind

Kept as CLAUDE.md Section 4 says, with an exact pin, and decided at the start of chunk 6f, not before. **Proof required in a bundled debug build:** the generated CSS is a bundled file and satisfies `style-src 'self'` (no inline `style=` markup, no `<style>` tag, no remote font). If it does not hold, 6f stops and asks, and plain CSS variables are the fallback. The package names, exact versions, licenses and network or telemetry behaviour are **not looked up yet**; they are stated at install time and need approval first. No component library, no shadcn, no jsdom, no Playwright and no React Flow in M6 core.

## 4. Components

### 4.1 Fixed statement block (planned, chunks 6g and 6h)

A bordered block with `statement-bg` and `statement-text`, text at `text-base` or larger, in the normal reading order and never collapsed, on hover or behind a control. One statement is required on every `SUPPORTED` or `PARTIAL` row of the review queue:

> The quote is real company text that was in the context. ALIBI has not checked that it answers this question.

(Copied from `docs/adr/021-read-only-routes-and-review-queue.md` decision 7. The ADR sets the word "not" in bold; whether the screen emphasises it is decided in chunk 6h, and the words do not change.)

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

Two more statements are required but their words are **not fixed yet** and are set in chunk 6h: the plain-words explanation shown when a changed question cell makes the whole queue refuse (ADR-021 decision 5), and the line saying that approving is typing `approved` in the Review column of the workbook and importing it (a M6.5 screen). The recall "intact" statement and the numbers-only statement belong to the M6.5 screens, which must carry them.

### 4.2 Unreviewed-draft badge (planned)

The text `UNREVIEWED DRAFT`, shown wherever an ALIBI answer appears: the run summary, the queue header and each answer. It uses `badge-bg` and `badge-text`, is always visible and carries no icon that could be read as approval. Review state is always `unreviewed` in M5 (ADR-013), and the queue records no approval, so the badge never changes to anything else.

### 4.3 Edited-status line (planned, chunk 6h)

For a row whose Status cell was changed in the workbook: show the run record's status with the label **recorded by ALIBI**, then the edited-status statement from 4.1, in `edited-bg` and `edited-text`. The edited cell's text is **never** shown and never styled as a verdict (the route does not return it, ADR-021 decision 4). The row sorts first, with `NEEDS_REVIEW` rows.

### 4.4 Edited-answer line (planned, chunk 6h)

For a row whose Answer cell was changed: show the answer with the edited-answer statement from 4.1. The citation shown is the run record's, never the cell's. A row whose Evidence cell was changed also shows a warning that the citation is ALIBI's record, not the cell.

### 4.5 Status chip, folder row, progress text (planned)

- **Status chip:** label plus shape (3.2), never colour alone.
- **Folder row:** two columns, "In use now" and "Saved for next launch", shown separately when they differ. Rows are in the recommended order: vault, ledger, questionnaires, exports, imports, and the ledger row is marked "needed for the review queue".
- **Progress text:** `completed / total` from the status route, a local "time since the last answer" counter, and "writing the answers file" after `completed == total` while the status is still running. **No ETA, no smooth percent bar, and never the word "stuck"** (the count changes only when a question finishes). Progress is announced politely and throttled.

## 5. Review queue layout (planned, chunk 6h)

Rows are ordered by risk: edited-status rows and `NEEDS_REVIEW` first with the code's reasons, then `PARTIAL`, then `SUPPORTED`. Each row shows the question, the draft answer, and the source file, exact lines and verbatim quote side by side. Reasons are always visible and never hidden to shorten a row. The table is navigable by keyboard. The queue has no approve control and no control that calls a command writing a workbook.

## 6. Accessibility and motion

- Everything is reachable by keyboard in a logical order with a visible focus indicator and no keyboard trap.
- `prefers-reduced-motion: reduce` removes non-essential animation. Progress is text and numbers, so nothing essential depends on motion.
- **`forced-colors`:** tested in 6g and 6i. Meaning must survive: labels and shapes carry the status, and borders carry block boundaries.
- Checked at 200% zoom at the 900 by 600 minimum.
- Targets in 3.1 are checked by script against the tokens, in light and dark, once values exist. Until then they are targets, not results.

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

Question cells, draft answers, quotes and file names are untrusted data (CLAUDE.md 8.6). The rules, planned for chunks 6g and 6h and each backed by a planned test in the threat model (F36-1 to F36-4):

- **React text nodes only.** No `dangerouslySetInnerHTML`, no `innerHTML`; a lint rule and the existing Semgrep rule ban them.
- Shown with `white-space: pre-wrap` and `unicode-bidi: isolate`.
- Control and direction-control characters are shown as visible marks, not interpreted.
- No automatic links. Citations are plain text a person can look up; ALIBI sets no hyperlinks.
- Long text is collapsed behind a "show more" control, **except** fixed statements and status words, which are never collapsed.
- Model text never takes the style of a fixed statement, a status or an error.

## 9. Assets and the CSP

No remote assets, no remote fonts, no CDN. Everything is bundled. The shipped policy (ADR-005) is `default-src 'self'` with `style-src 'self'`, so there is **no inline `style=` attribute and no `<style>` tag**; styling comes from bundled CSS files only. `pnpm tauri dev` injects no CSP, so a violation does not show there and is checked in the bundled debug build (ADR-005, chunks 6g and 6i).

## 10. What is not decided here

- Colour values and their measured contrast (chunk 6f).
- The exact status label strings and the two undecided statements in 4.1 (chunk 6h).
- Whether Tailwind is used (chunk 6f).
- Whether release builds ship DevTools (checked in 6i, not verified).
- Anything about skills: nothing is installed in M6 core, and the decision is made at the start of M6.5 after the owner and the reviewer have read the repositories themselves.
- The M6.5 screens (ledger, recall, blast radius, contradictions, reality, graph view).
