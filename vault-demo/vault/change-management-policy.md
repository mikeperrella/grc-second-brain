---
doc_id: MQ-POL-005
type: policy
owner: Head of Engineering (fictional role)
review_by: 2028-03-31
control_mappings: ["SOC2:CC8.1", "NIST-800-53:CM-3"]
---
> SYNTHETIC DEMO DATA: the fictional company "Marlowe Quay Analytics, Ltd.". Not a real policy.

# Change Management Policy

## Scope

Every change to a production system needs a ticket.

## Review

Each change must be peer reviewed by someone other than its author before it is merged. Production deployments run only through the automated pipeline.

## Emergency changes

The on-call lead may approve an emergency change. It is reviewed after the fact within two business days.

## Rollback

Every change has a written rollback step.
