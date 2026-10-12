---
doc_id: MQ-POL-002
type: policy
owner: Head of Engineering (fictional role)
review_by: 2030-05-31
control_mappings: ["SOC2:CC6.7", "NIST-800-53:SC-13", "NIST-800-53:SC-28"]
links: ["[[backup-and-recovery-standard]]"]
---
> SYNTHETIC DEMO DATA: the fictional company "Marlowe Quay Analytics, Ltd.". Not a real policy.

# Encryption Policy

## Data at rest

Production databases and file stores are encrypted with AES-256. Laptops must use full-disk encryption.

## Data in transit

Connections from customers to Marlowe Quay services use TLS 1.2 or later. Older protocol versions are disabled.

## Key management

Encryption keys are held in a managed key vault, and only named administrators can use them. Keys are rotated at least once a year.

## Backups

Backups are encrypted as set out in the [[backup-and-recovery-standard]].
