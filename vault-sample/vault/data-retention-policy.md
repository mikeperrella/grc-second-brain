---
doc_id: HR-POL-005
type: policy
owner: Data Protection Officer (fictional role)
review_by: 2028-01-31
control_mappings: ["SOC2:CC6.5", "NIST-800-53:SI-12", "ISO27001:A.8.10"]
links: ["[[vendor-management-policy]]"]
---
> SYNTHETIC SAMPLE DATA: the fictional company "Halcyon Ridge Software, Inc.". Not a real policy.

# Data Retention Policy

## Principles

Halcyon Ridge keeps data only as long as there is a business or legal reason, and then deletes it
securely.

## Retention schedule

| Data | Retention |
|---|---|
| Customer account data | Life of the contract plus 30 days |
| Application logs | 30 days |
| Security logs | 90 days |
| Financial records | 7 years |

## Deletion

Expired data is deleted from production systems within 30 days and from backups within 90 days.
Vendors holding company data must delete it on request, as required by the [[vendor-management-policy]].

## Exceptions

Legal holds override this schedule. The Data Protection Officer approves and records each hold.
