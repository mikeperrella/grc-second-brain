# vault-sample: synthetic demo vault

**Everything in this folder is synthetic.** "Halcyon Ridge Software, Inc." is a fictional company.
The policies are invented for development and demos. They are not real policies, are not
legal or compliance advice, and must not be presented as evidence for any real organization.

## Layout

```
vault-sample/
  README.md   this file: documentation only, kept OUTSIDE the vault so it is never indexed
  vault/      the Obsidian-compatible vault the indexer reads (point GRC_VAULT_DIR here)
```

Open `vault/` in Obsidian to browse it. Each file carries a `SYNTHETIC SAMPLE DATA` banner under
its frontmatter.

## Documents

| File | doc_id | Type | Review by |
|---|---|---|---|
| `access-control-and-mfa-policy.md` | HR-POL-001 | policy | 2028-03-31 |
| `encryption-policy.md` | HR-POL-002 | policy | 2028-06-30 |
| `incident-response-plan.md` | HR-POL-003 | procedure | 2028-01-31 |
| `vendor-management-policy.md` | HR-POL-004 | policy | **2024-06-30** |
| `data-retention-policy.md` | HR-POL-005 | policy | 2028-01-31 |
| `mfa-enrollment-procedure.md` | HR-PRC-001 | procedure | 2028-03-31 |

## Intentional defects (for later milestones)

These are deliberate. Do not fix them.

1. **A contradiction.** `data-retention-policy.md` says security logs are kept **90 days**.
   `incident-response-plan.md` says security logs are kept **365 days**. The M5.5 contradiction
   detector is meant to find this.
2. **A stale review date.** `vendor-management-policy.md` has `review_by: 2024-06-30`, which is in
   the past for any realistic "today". Tests use a fixed clock, never the real date, so they do not
   depend on when they run. The M5.5 reality check is meant to flag it.

## Control mappings

`control_mappings` entries use the form `FRAMEWORK:ID` with real control identifiers, for example
`SOC2:CC6.1` or `NIST-800-53:AC-2`. The mapping of a synthetic policy to a real control ID is
illustrative only.
