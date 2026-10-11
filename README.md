# ALIBI

**Answers Linked to Immutable, Backed Information**

*Every compliance answer needs an alibi.*

ALIBI is a desktop compliance workbench for answering security questionnaires from your own policies. A local model (through Ollama) drafts answers, and code checks every quote word for word against its source, so each answer points at the exact line behind it. Everything runs on your machine: no cloud, no accounts, no telemetry. ALIBI's own code makes no network connection except to Ollama on 127.0.0.1, and the sidecar's no-network operation is tested in CI. ALIBI cannot vouch for Ollama, Windows, or other software on the machine, which have their own network behaviour (see [docs/threat-model.md](docs/threat-model.md)).

ALIBI was formerly called GRC Second Brain. Some internal names predate the rename and are unchanged on purpose (the `grc_sidecar` Python package, the Tauri identifier, the repository directory).

## Status: early development

Only the secure offline foundation exists today. The app opens a window and shows the live status of its local sidecar process. None of the compliance features below is built yet, and the status column says so. The current milestone is M5 (see [CLAUDE.md](CLAUDE.md), Section 9).

## The problem map

| Problem | How ALIBI addresses it | Status |
|---|---|---|
| Hundreds of reworded questions answered by copy and paste from old documents | Excel in, cited answers out; the original file is never modified | Planned (M5) |
| AI answers that are confident but cannot be defended | Every quote is verified word for word against its source, and unsupported questions are flagged for a human instead of guessed | Planned (M5) |
| Answers drift out of sync with policies | A promise ledger records every answer sent and flags it when its source policy changes | Planned (M5.5) |
| Nobody sees what a policy edit would break | Blast-radius preview, before you make the edit | Planned (M5.5) |
| Policies that contradict each other go unnoticed | Contradiction detector | Planned (M5.5) |
| Policies claim controls that IT systems do not enforce | Reality check: offline import of IT exports (identity, device, cloud reports) checked against policy claims | Planned (M5.5) |
| Sensitive policies have to be sent to a cloud service | Zero-trust host and sidecar, confined processes, strict CSP, and a CI test that proves the sidecar runs with no network | Built (M0 to M2, ADR-007). This is the foundation only: no ALIBI feature runs on it yet |

The full feature list, including the items marked Later, the demo plan and the non-goals, is in [docs/product-brief.md](docs/product-brief.md).

## Where to read more

- [docs/product-brief.md](docs/product-brief.md): the problem, audience, promise, features and non-goals.
- [CLAUDE.md](CLAUDE.md): the project rules, architecture contracts and milestones.
- [docs/dev-setup.md](docs/dev-setup.md): how to set up, run and verify the project locally.
- [docs/adr/](docs/adr/): the architecture decisions and their evidence.
