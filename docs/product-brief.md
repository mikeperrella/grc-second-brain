# ALIBI: Product Brief

**Answers Linked to Immutable, Backed Information**

*Every compliance answer needs an alibi.*

Name clearance: a preliminary search in October 2026 found no GRC or compliance product named Alibi (an open-source ML explainability library and a security camera brand use the name). A formal trademark search is planned before M9.

## The problem

Security questionnaires (SIG, CAIQ, custom spreadsheets) bury compliance teams in hundreds of reworded questions, answered by copy and paste from old documents. Answers drift out of sync with policies, contradictions go unnoticed, policies claim controls that IT systems don't actually enforce, and nobody tracks which promises made to customers have quietly become false. AI tools speed this up but produce confident answers nobody can defend, and most require sending sensitive policies to a cloud service.

## Who it's for (version 1)

The compliance lead at a small organization that handles sensitive data, such as a defense subcontractor handling controlled government information or a health-tech vendor, answering customer security questionnaires, often as a team of one.

Later: vCISOs and GRC consultancies serving many clients (needs separate client workspaces), and larger regulated teams.

## The promise

A desktop compliance workbench that answers questionnaires from your own policies, cites the exact line behind every answer, says "no evidence" instead of guessing, checks your policies against what your IT systems actually show, remembers every promise you've made, and ALIBI itself never sends a byte off your machine: it talks only to Ollama on this computer.

## How it works

AI proposes, code verifies. A local model (through Ollama) reads policies and drafts answers. Code checks every quote word for word against its source, decides each answer's status, and keeps the promise ledger. The model has no tools and can only return structured data that code validates.

## Features and status

| Feature | What it does | Status |
|---|---|---|
| Secure offline foundation | Zero-trust host and sidecar, confined processes, strict CSP, CI-proven no-network operation | Built (M0 to M2, ADR-007) |
| Policy vault and indexing | Markdown vault with owners and review dates, change-driven indexing | Built (M3) |
| Search | Hybrid keyword and meaning search with citations, framework text kept separate from company evidence | Built (M4, internal; UI in M6) |
| Cited questionnaire answers | Excel in, cited answers out, original file never modified | Planned (M5) |
| Honest blanks | Unsupported questions flagged for a human, never guessed | Planned (M5) |
| Deal unblocker metrics | Measured turnaround, reuse and review counts per questionnaire, never estimated | Planned (M5) |
| Promise ledger and recall | Records every answer sent; flags it when its source policy changes | Planned (M5.5) |
| Blast-radius preview | Shows what a policy edit would break, before you make it | Planned (M5.5) |
| Contradiction detector | Finds policies that disagree with each other | Planned (M5.5) |
| Reality check | Offline import of IT exports (identity, device, cloud reports) checked against policy claims | Planned (M5.5) |
| Answer receipts | A tamper-evident fingerprint of the source text behind each answer | Planned |
| Egress attestation | A report of every connection ALIBI's own process made (loopback only), plus an OS-level check | Planned |
| Trust packet | A proactive security overview built from approved, cited answers | Later |
| AI governance pack | Answers AI-usage questions from the company's own AI policy (NIST AI RMF) | Later |
| Adversarial auditor | AI challenges your answers before you send them | Later |
| Compliance time machine | Shows what you promised, and what policies said, on any past date | Later |
| Gap-to-fix loop | A missing answer becomes drafted policy text or a remediation plan | Later (M7) |

## The demo (about 3 minutes)

1. The flood: Wi-Fi off. A messy 150-question spreadsheet fills in with clickable citations. Unsupported questions stay blank and flagged.
2. The attack: the auditor catches stale evidence and a contradiction between two policies.
3. The ripple: edit a policy line, and the blast radius shows a broken promise to a customer.
4. The proof: export with receipts, plus the report showing nothing left the machine.

Second demo: the reality check. "Your policy says MFA is required. Your identity export shows 3 users without it."

## First frameworks

- Full text (public domain): NIST CSF 2.0, NIST SP 800-53, NIST SP 800-171.
- Control IDs plus our own summaries: SOC 2, ISO 27001 (their text is licensed).
- Later packs: NIST AI RMF, HIPAA, CMMC workflows, and others.
- Frameworks are data packs, not code, each recording its license. Licensed text is imported by users, never bundled.

## Non-goals (version 1)

- Not a full GRC platform replacing Vanta or Drata.
- No cloud, no accounts, no telemetry, no auto-update, no live connections to IT systems (file imports only).
- No automatic compliance claims: a policy existing doesn't prove a control works.
- No deployment of generated infrastructure code. Drafts only, human-approved.
- No multi-client workspaces yet.

## Honest positioning

Cloud tools (Conveyor, Vanta, Drata) and some local-first projects (RepliSec, QResponder) already offer cited, AI-drafted answers. ALIBI's difference is the combination: verifiable offline operation, answers code-checked against their sources, policies checked against IT reality, and a memory of every promise with recall when one breaks. We don't claim to be the only offline tool.

Competitor details come from desk research in October 2026 and will be re-verified before public release.

## Demo success criteria

- A realistic spreadsheet (merged cells, dropdowns) fills in without breaking.
- Every filled answer has a working citation to an exact line.
- No unsupported question receives an answer.
- One policy edit visibly flags at least one affected promise.
- The CI no-network test and a local egress check both pass.
