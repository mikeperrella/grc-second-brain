---
doc_id: HR-POL-003
type: procedure
owner: Security Lead (fictional role)
review_by: 2028-01-31
control_mappings: ["SOC2:CC7.4", "NIST-800-53:IR-4", "ISO27001:A.5.24", "ISO27001:A.5.26"]
links: ["[[data-retention-policy]]"]
---
> SYNTHETIC SAMPLE DATA: the fictional company "Halcyon Ridge Software, Inc.". Not a real plan.

# Incident Response Plan

## Declaring an incident

Any employee who suspects a security incident reports it to the Security Lead immediately. The
Security Lead decides whether to declare an incident and assigns an incident commander.

## Response phases

1. Contain the affected systems.
2. Investigate the cause and the scope.
3. Eradicate the cause and recover the systems.
4. Review what happened and record lessons learned within ten business days.

## Evidence and log retention

To support investigations that start late, security logs are retained for 365 days. Logs needed
for an open incident are kept until the incident is closed.

## Customer notification

If customer data is affected, the Security Lead and Legal decide on notification within 72 hours
of confirming the incident. See also the [[data-retention-policy]] and the
[[access-control-and-mfa-policy]].
