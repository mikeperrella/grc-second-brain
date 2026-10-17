# ALIBI

**Answers Linked to Immutable, Backed Information** (immutable to the application, not tamper-evident, ADR-017 decision 9)

*Every compliance answer needs an alibi.*

ALIBI is a desktop compliance workbench for answering security questionnaires from your own policies. A local model (through Ollama) drafts answers, and code checks every quote word for word against its source, so each answer points at the exact line behind it. Everything runs on your machine: no cloud, no accounts, no telemetry. ALIBI's own code makes no network connection except to Ollama on 127.0.0.1, and the sidecar's no-network operation is tested in CI. ALIBI cannot vouch for Ollama, Windows, or other software on the machine, which have their own network behaviour (see [docs/threat-model.md](docs/threat-model.md)).

ALIBI was formerly called GRC Second Brain. Some internal names predate the rename and are unchanged on purpose (the `grc_sidecar` Python package, the Tauri identifier, the repository directory).

## Status: early development

The secure offline foundation and the questionnaire engine (M5) exist, and M6 core added a window with four screens: choose folders, index the vault, run a questionnaire, and read the finished draft in a review queue. **They run in a debug build started from the repository, with the development sidecar: there is no installer and no release build that works until M8.** The reality check, the promise ledger with recall, the blast-radius preview and the contradiction detector (M5.5) are built as sidecar routes only, with no screen until M6.5. The owner's close checklist for M6 in the real window was run once by hand on 2026-10-16 (one machine, Windows, a debug build; observations, not tests, in [docs/threat-model.md](docs/threat-model.md) F5-1b and G12; the results were recorded in PR #51), and the colours are placeholders. The other compliance features below are not built, and the status column says so (see [CLAUDE.md](CLAUDE.md), Section 9).

## The problem map

| Problem | How ALIBI addresses it | Status |
|---|---|---|
| Hundreds of reworded questions answered by copy and paste from old documents | Excel in, cited answers out; the original file is never modified | Built (M5); screens since M6 core, debug build only |
| AI answers that are confident but cannot be defended | Every quote is verified word for word against its source. Blanks with reasons are only partly reliable: some questions that should stay blank are answered (see "Known limits" below) | Partly built (M5) |
| Answers drift out of sync with policies | A promise ledger records every answer sent and flags it when its source policy changes | Built (M5.5 PR 2), sidecar routes only. Recall watches the cited lines only |
| Nobody sees what a policy edit would break | Blast-radius preview, before you make the edit | Built (M5.5 PR 2), sidecar route only. Covers promises only |
| Policies that contradict each other go unnoticed | Contradiction detector: finds pairs of policy statements whose numbers disagree and shows both quotes | Partly built (M5.5 PR 4), sidecar route only. Numbers only. Code finds the pairs; the model confirms a real conflict only sometimes |
| Policies claim controls that IT systems do not enforce | Reality check: one Okta "MFA Enrollment by User" CSV checked against one claim (MFA required) | Built in a narrow form (M5.5 PR 1) |
| Sensitive policies have to be sent to a cloud service | Zero-trust host and sidecar, confined processes, strict CSP, and a CI test that proves the sidecar runs with no network | Built (M0 to M2, ADR-007). This is the foundation only: no ALIBI feature runs on it yet |

The full feature list, including the items marked Later, the demo plan and the non-goals, is in [docs/product-brief.md](docs/product-brief.md).

## The questionnaire engine (M5)

**What it does.** You give it a questionnaire spreadsheet (`.xlsx` or `.xlsm`). It reads each question, looks it up in your Markdown policy vault, asks the local model for an answer, and writes a **new copy** of the spreadsheet with six extra columns on the right: the answer, the model's own confidence rating, a status (`SUPPORTED`, `PARTIAL` or `NEEDS_REVIEW`), the reason, the evidence (file, exact lines and the quote) and a review column that always says "unreviewed". Code, not the model, checks that each quote appears word for word in a company policy file and decides the status. When a check fails the answer cell is left blank and the reason column says why. The original file is only read, never modified, and the output is a new file with `UNREVIEWED-DRAFT` in its name. Every output is a draft for a human to review.

**How to run it today (a debug build only; there is no installer until M8).** From `apps/desktop/`, build and start the app as [docs/dev-setup.md](docs/dev-setup.md) describes (`pnpm tauri build --debug --no-bundle`, then start `src-tauri\target\debug\desktop.exe`; it starts the development sidecar from this repository's `sidecar/.venv`). Ollama must be running with the two models already installed (ALIBI never downloads models); the status bar names any that are missing. In the window, in this order: on the **Folders** screen choose the vault, the **ledger** folder, the questionnaires folder and the exports folder; **close and start ALIBI again**, because a saved folder takes effect at the next launch (the screen shows "In use now" and "Saved for next launch" separately); then **Index the vault**; then on the **Questionnaire** screen choose a workbook, check the mapping and start the run (each answer can take 40 to 60 seconds on a CPU-only laptop); then open the finished draft on the **Review queue** screen. **The ledger folder must be in use before the run if you want to open its draft in the queue:** a run without it keeps no run record, and the queue refuses a draft with no record. The queue is for reading: it shows each row's status, reasons, source, lines and quote, and records no approval (approving is typing `approved` in the workbook's Review column and importing it, a screen planned for M6.5). The folders may not overlap, except that the questionnaires and exports folders may. The sidecar still reads the same settings from `GRC_*` environment variables (CLAUDE.md Section 5.1), and its routes are `POST /questionnaire/inspect`, `POST /questionnaire/runs`, `GET /questionnaire/runs/{id}`, `GET /questionnaire/runs/{id}/events`, `POST /questionnaire/draft`, `POST /vault/index` and `GET /vault/index/{job_id}`, all behind the sidecar's token. The end-to-end test in `sidecar/tests/test_e2e_live.py` builds a small demo workbook and the demo vault and runs the real sidecar without the window (its instructions are at the top of the file; it uses your Ollama, so run it only when you mean to).

**Known limits.**

- **Slow on a CPU-only machine.** On the owner's laptop, about 40 to 60 seconds per question.
- **Right answer, wrong source.** A quote can be real company text and still not be the best source for the question. In the held-out runs one question was answered correctly from a different document in all three runs.
- **Some questions that should stay blank get an answer.** The code checks that a quote is real, current company text that was in the context. It cannot check that the quote answers the question, so items such as a near miss or a contradiction between two policies can still be answered (the contradiction detector compares numbers only). On the held-out split the acceptance check failed in all three runs. This is why every output is an unreviewed draft, and why the review queue (M6) matters: in its first live run (one run, one machine, a synthetic vault, counts, not a rate), 5 of 15 answers cited a real company sentence that did not answer the question, by the owner's reading, not measured by code, so the queue shows each quote beside the answer and says on every row that ALIBI has not checked it answers the question.
- **Results vary from run to run**, even with a fixed seed.

For the details and the evidence, see [ADR-012](docs/adr/012-generation-client.md) (the model client and its limits), [ADR-013](docs/adr/013-answer-status-and-grounding.md) (how status is computed, and the measured results) and [ADR-014](docs/adr/014-questionnaire-files-and-runs.md) (reading and writing the workbook, runs and routes).

## Where to read more

- [docs/product-brief.md](docs/product-brief.md): the problem, audience, promise, features and non-goals.
- [CLAUDE.md](CLAUDE.md): the project rules, architecture contracts and milestones.
- [docs/dev-setup.md](docs/dev-setup.md): how to set up, run and verify the project locally.
- [docs/adr/](docs/adr/): the architecture decisions and their evidence.
