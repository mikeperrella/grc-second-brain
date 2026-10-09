---
doc_id: HR-POL-001
type: policy
owner: Head of IT Operations (fictional role)
review_by: 2028-03-31
control_mappings: ["SOC2:CC6.1", "NIST-800-53:AC-2", "NIST-800-53:IA-2", "ISO27001:A.5.15", "ISO27001:A.8.5"]
links: ["[[mfa-enrollment-procedure]]"]
---
> SYNTHETIC SAMPLE DATA: the fictional company "Halcyon Ridge Software, Inc.". Not a real policy.

# Access Control and Multi-Factor Authentication Policy

## Purpose

This policy sets who may access Halcyon Ridge systems and how that access is proven, granted,
reviewed and removed.

## Scope

It applies to all employees, contractors and service accounts that touch production systems or
customer data.

## Account provisioning

Access is granted by role, using least privilege. Every account has a named owner. The hiring
manager requests access, and the IT Operations team approves it. Shared accounts are not allowed.

## Multi-factor authentication

Multi-factor authentication (MFA) is required for every workforce account on every system that
holds customer data, and for all remote access. Administrators must use a hardware security key.
Users enroll following the [[mfa-enrollment-procedure|MFA enrollment procedure]].

SMS codes are not an accepted second factor.

## Access reviews

IT Operations reviews all user and administrator access every quarter. Accounts that are no longer
needed are disabled within one business day of the review finding.

## Related documents

Stored credentials and keys are protected as described in [[encryption-policy#Key management]].
Suspected account compromise is handled under the [[incident-response-plan]].
