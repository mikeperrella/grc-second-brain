# vault-demo: synthetic demo vault for the M5 questionnaire engine

**Everything in this folder is synthetic.** "Marlowe Quay Analytics, Ltd." is a fictional company.
The policies are invented for development and demos. They are not real policies, are not legal or
compliance advice, and must not be presented as evidence for any real organization. The text is
original; it was written for this project and is not copied from any standard or questionnaire.

## Why a second vault

`vault-sample/` is pinned by the M4 retrieval golden tests and must not change. The M5 evaluation
set and the demo spreadsheet need more variety (absent topics, near misses, exact values), so they
use this vault. The M4 goldens never read it.

## Layout

```
vault-demo/
  README.md   this file: documentation only, kept OUTSIDE the vault so it is never indexed
  vault/      the Obsidian-compatible vault the indexer reads (point GRC_VAULT_DIR here)
```

Each file carries a `SYNTHETIC DEMO DATA` banner under its frontmatter and follows the same
frontmatter contract as `vault-sample` (CLAUDE.md 8.1).

## Documents

| File | doc_id | Type | Review by |
|---|---|---|---|
| `access-control-policy.md` | MQ-POL-001 | policy | 2030-03-15 |
| `mfa-enrollment-procedure.md` | MQ-PRC-001 | procedure | 2030-03-15 |
| `encryption-policy.md` | MQ-POL-002 | policy | 2030-05-31 |
| `data-retention-policy.md` | MQ-POL-003 | policy | 2030-01-31 |
| `incident-response-plan.md` | MQ-PRC-002 | procedure | 2030-01-31 |
| `vendor-management-policy.md` | MQ-POL-004 | policy | **2024-09-30** |
| `change-management-policy.md` | MQ-POL-005 | policy | 2030-03-31 |
| `backup-and-recovery-standard.md` | MQ-STD-001 | standard | 2030-04-30 |
| `acceptable-use-policy.md` | MQ-POL-006 | policy | 2030-03-31 |
| `vulnerability-management-procedure.md` | MQ-PRC-003 | procedure | 2030-02-15 |
| `security-awareness-standard.md` | MQ-STD-002 | standard | 2030-02-15 |
| `monitoring-standard.md` | MQ-STD-003 | standard | 2030-02-15 |

## Intentional defects (the evaluation set depends on them: do not fix)

1. **Two contradictions.** (a) `data-retention-policy.md` says security logs are kept **90 days**;
   `incident-response-plan.md` says **365 days**. (b) `data-retention-policy.md` says **database**
   backups are overwritten after **30 days**; `backup-and-recovery-standard.md` says production
   database backups are kept for **35 days**. The evaluation uses (a) in the dev split and (b) in
   the held-out split, so tuning on one cannot leak into the other.
2. **A stale review date.** `vendor-management-policy.md` has `review_by: 2024-09-30`. Every other
   note is reviewed by 2030, so the demo does not change behaviour when the real date passes the
   old 2028 dates; the evaluation also runs with a fixed clock (`clock_today` in
   `fixtures/eval/eval-set.json`, 2026-10-10), never the real date.
3. **Topics the vault deliberately does not cover**, so the right answer is a blank: penetration
   testing, physical security of offices, employee background checks, an AI usage policy, data
   classification, a secondary data center, a bug bounty program, a published audit report.
4. **Near misses.** True sentences that sit next to a question without answering it, for example
   multi-factor authentication for *staff* (the vault says nothing about *customers'* end users),
   and TLS for *customer-facing* connections (nothing about internal service-to-service traffic).
5. **No written control identifiers in the text.** The notes never write `AC-2`, `CC6.1` or similar
   in their bodies (only in frontmatter, which is not searched, G7), so a question that is just an
   identifier has no honest answer here.

## How it is used

- `fixtures/eval/eval-set.json` holds the labeled questions and their acceptable evidence (line
  ranges in these files). Do not edit a label after the first live run without a tuning-log entry
  in `docs/adr/013-answer-status-and-grounding.md`.
- The hostile notes used by the evaluation's injection items live in `fixtures/adversarial-m5/`,
  not here, so this vault stays clean for the demo spreadsheet.
- The demo spreadsheet (M5l) asks only dev-split questions, never held-out ones.
