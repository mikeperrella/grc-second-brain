---
doc_id: MQ-POL-001
type: policy
owner: Head of Systems (fictional role)
review_by: 2028-03-15
control_mappings: ["SOC2:CC6.1", "NIST-800-53:AC-2", "NIST-800-53:IA-2"]
links: ["[[mfa-enrollment-procedure]]"]
---
> SYNTHETIC DEMO DATA: the fictional company "Marlowe Quay Analytics, Ltd.". Not a real policy.

# Access Control Policy

## Purpose

This policy says who may use Marlowe Quay systems and how that access is granted, checked and removed.

## Account provisioning

Access follows job role and the principle of least privilege. Every account has a named owner. Managers request access and the Systems team approves it. Shared accounts are not permitted.

## Multi-factor authentication

Multi-factor authentication (MFA) is required for all staff accounts on every system that stores customer data, and for all remote access. Administrators must use a hardware security key. Text-message codes are not an accepted second factor. Staff enroll by following the [[mfa-enrollment-procedure|MFA enrollment steps]].

## Access reviews

The Systems team reviews all staff and administrator access every quarter. Accounts that are no longer needed are disabled within two business days of the review.

## Leavers

Access is removed on the employee's last working day.
