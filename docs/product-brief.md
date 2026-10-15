# ALIBI: Product Brief

**Answers Linked to Immutable, Backed Information** (immutable to the application, not tamper-evident, ADR-017 decision 9)

*Every compliance answer needs an alibi.*

Name clearance: a preliminary search in October 2026 found no GRC or compliance product named Alibi (an open-source ML explainability library and a security camera brand use the name). A formal trademark search is planned before M9.

## The problem

Security questionnaires (SIG, CAIQ, custom spreadsheets) bury compliance teams in hundreds of reworded questions, answered by copy and paste from old documents. Answers drift out of sync with policies, contradictions go unnoticed, policies claim controls that IT systems don't actually enforce, and nobody tracks which promises made to customers have quietly become false. AI tools speed this up but produce confident answers nobody can defend, and most require sending sensitive policies to a cloud service.

## Who it's for (version 1)

The compliance lead at a small organization that handles sensitive data, such as a defense subcontractor handling controlled government information or a health-tech vendor, answering customer security questionnaires, often as a team of one.

Later: vCISOs and GRC consultancies serving many clients (needs separate client workspaces), and larger regulated teams.

## The promise

A desktop compliance workbench that answers questionnaires from your own policies, cites the exact line behind every answer, says "no evidence" instead of guessing, checks one Okta MFA export against one policy claim (MFA required), records every answer you send and flags it when its cited lines change, and ALIBI itself never sends a byte off your machine: it talks only to Ollama on this computer. Until M8 it runs only as a debug build started from the repository, with the dev sidecar; there is no installer.

## How it works

AI proposes, code verifies. A local model (through Ollama) reads policies and drafts answers. Code checks every quote word for word against its source, decides each answer's status, and keeps the promise ledger. The model has no tools and can only return structured data that code validates.

## Features and status

| Feature | What it does | Status |
|---|---|---|
| Secure offline foundation | Zero-trust host and sidecar, confined processes, strict CSP, CI-proven no-network operation | Built (M0 to M2, ADR-007) |
| Policy vault and indexing | Markdown vault with owners and review dates, change-driven indexing | Built (M3) |
| Search | Hybrid keyword and meaning search with citations, framework text kept separate from company evidence | Built (M4, internal; no search screen is planned, retrieval is reached through questionnaire runs) |
| Cited questionnaire answers | Excel in, cited answers out, original file never modified | Built (M5, ADR-012 to ADR-014; sidecar routes only; a screen is planned for M6 core). The original is only read, the output is a new file marked as an unreviewed draft, and code checks that every quote appears word for word in a company policy file. It does not check that the quote answers the question: in the held-out runs a right answer cited the wrong document in some cases (ADR-013, "Live check B results"). Speed on the owner's CPU-only laptop was about 40 to 60 seconds per question |
| Honest blanks | Unsupported questions flagged for a human, never guessed | Partly built (M5). Code blanks an answer when its quote is not verbatim, not from company policy, too short, or does not contain the identifier asked about, and records the reason. Whether an absent-topic, near-miss, conflict or framework-bait question ends blank mostly depends on the model choosing "no evidence", and it does not always. On the held-out split, blank-labeled items were answered in 4, 5 and 3 of 9 items in three runs, and the acceptance check (every blank-labeled item ends blank) failed in all three. Human review is the control for these: the M6 review queue (planned for M6 core, not built) will be for reading the finished draft, and approval will be done in the workbook |
| Deal unblocker metrics | Measured turnaround, reuse and review counts per questionnaire, never estimated | Partly built (M5): a finished run reports its seconds, its counts of each status and its reason codes. Reuse and review counts are not built |
| Promise ledger and recall | Records every answer sent; flags it when its source policy changes | Built (M5.5 PR 2, ADR-017); sidecar routes only, no screen until M6.5. Recall watches the cited lines only: an edit elsewhere in the file still reads intact |
| Blast-radius preview | Shows what a policy edit would break, before you make it | Built (M5.5 PR 2, ADR-018); sidecar route only, no screen until M6.5. Covers promises only: controls and linking documents are not built (G36) |
| Contradiction detector | Finds pairs of company-policy statements whose numbers disagree and shows both quotes with file and lines | Partly built (M5.5 PR 4, ADR-019); sidecar route only, no screen until M6.5. Numbers only. Code finds the pairs; the local model confirms a real conflict only sometimes: in three identical held-out runs the same pair got different answers (4, 1 and 4 of 8 expected conflicts confirmed), and 1 of 9 scope-difference item-runs was wrongly confirmed. Findings are never stored. No finding does not mean the policies agree |
| Reality check | Offline import of one Okta "MFA Enrollment by User" CSV, checked against one claim in the policies (MFA required) | Built in a narrow form (M5.5 PR 1, ADR-016): other formats, other claims and the administrators' hardware-key claim are not checked; no screen until M6.5 |
| Answer receipts | A tamper-evident fingerprint of the source text behind each answer | Planned |
| Egress attestation | A report of every connection ALIBI's own process made (loopback only), plus an OS-level check | Planned |
| Trust packet | A proactive security overview built from approved, cited answers | Later |
| AI governance pack | Answers AI-usage questions from the company's own AI policy (NIST AI RMF) | Later |
| Adversarial auditor | AI challenges your answers before you send them | Later |
| Compliance time machine | Shows what you promised, and what policies said, on any past date | Later |
| Gap-to-fix loop | A missing answer becomes drafted policy text or a remediation plan | Later (M7) |

## The demo (about 3 minutes)

1. The flood: Wi-Fi off. A messy 150-question spreadsheet fills in with a citation for each answer: the document, the exact lines and the quoted text, shown as plain text in the Evidence column (ALIBI sets no hyperlinks in the file, ADR-014). Clickable citations are not built and are not on the M6 milestone list (CLAUDE.md Section 9); if they come, it would be in the M6 review screen and would need its own decision. Unsupported questions stay blank and flagged.
2. The checks: an answer whose source policy is past its review date is flagged (`PARTIAL`, with the reason), and recall reports changes to the lines an answer cited (sidecar routes, no screen until M6.5). The contradiction detector compares numbers only and shows both quotes for each pair that disagrees; the local model confirms a real conflict only sometimes (see the feature table). An auditor that challenges your answers is planned (Later), not built.
3. The ripple: edit a policy line, and the blast radius shows a broken promise to a customer (needs the blast-radius screen, M6.5).
4. The proof: export with receipts, plus the report showing nothing left the machine.

Second demo: the reality check. "Your policy says MFA is required. Your identity export shows 3 users without it."

Until M8 the demos run from a debug build started from the repository, with the dev sidecar; there is no installer.

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

Until M8 these are checked in a debug build started from the repository, with the dev sidecar; there is no installer.

- A realistic spreadsheet (merged cells, dropdowns) fills in without breaking.
- Every filled answer names the document and exact lines it came from, as text a person can look up.
- No unsupported question receives an answer. **Not met today**: see Honest blanks in the feature table.
- One policy edit visibly flags at least one affected promise.
- The CI no-network test and a local egress check both pass.
