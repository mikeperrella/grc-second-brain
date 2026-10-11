---
doc_id: MQ-POL-003
type: policy
owner: Data Protection Lead (fictional role)
review_by: 2028-01-31
control_mappings: ["SOC2:CC6.5", "NIST-800-53:SI-12"]
links: ["[[vendor-management-policy]]"]
---
> SYNTHETIC DEMO DATA: the fictional company "Marlowe Quay Analytics, Ltd.". Not a real policy.

# Data Retention Policy

## Principle

Marlowe Quay keeps data only while there is a business or legal need for it, then deletes it.

## Retention schedule

| Data | Retention |
|---|---|
| Customer account data | Contract term plus 45 days |
| Application logs | 14 days |
| Security logs | 90 days |
| Invoices and tax records | 6 years |

## Deletion

Customer data is deleted from production within 45 days after a contract ends. Backups are overwritten after 30 days. Vendors that hold company data must delete it on request, as the [[vendor-management-policy]] requires.

## Legal holds

A legal hold overrides this schedule. The Data Protection Lead approves and records each hold.
