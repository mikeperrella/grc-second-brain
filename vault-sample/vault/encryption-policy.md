---
doc_id: HR-POL-002
type: policy
owner: Chief Technology Officer (fictional role)
review_by: 2028-06-30
control_mappings: ["SOC2:CC6.1", "NIST-800-53:SC-28", "NIST-800-53:SC-13", "ISO27001:A.8.24"]
links: ["[[access-control-and-mfa-policy]]"]
---
> SYNTHETIC SAMPLE DATA: the fictional company "Halcyon Ridge Software, Inc.". Not a real policy.

# Encryption Policy

## Data at rest

Customer data stored in databases, object storage and backups is encrypted with AES-256.
Laptops use full-disk encryption.

## Data in transit

All traffic between customers and Halcyon Ridge services uses TLS 1.2 or later. Internal service
traffic that crosses a network boundary also uses TLS.

## Key management

Encryption keys are generated and stored in a managed key service. Keys are rotated at least
once a year, and immediately if compromise is suspected. Only the Platform team can administer
keys, and every administrative action is logged.

Access to the key service follows the [[access-control-and-mfa-policy]].
