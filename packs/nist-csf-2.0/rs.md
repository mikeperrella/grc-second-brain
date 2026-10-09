---
doc_id: nist-csf-2.0-rs
type: framework
owner: "NIST"
---
# RESPOND (RS)

Actions regarding a detected cybersecurity incident are taken

## RS.AN Incident Analysis

Investigations are conducted to ensure effective response and support forensics and recovery activities

### RS.AN-03

Analysis is performed to establish what has taken place during an incident and the root cause of the incident
- Implementation example: Determine the sequence of events that occurred during the incident and which assets and resources were involved in each event
- Implementation example: Attempt to determine what vulnerabilities, threats, and threat actors were directly or indirectly involved in the incident
- Implementation example: Analyze the incident to find the underlying, systemic root causes
- Implementation example: Check any cyber deception technology for additional information on attacker behavior

### RS.AN-06

Actions performed during an investigation are recorded, and the records' integrity and provenance are preserved
- Implementation example: Require each incident responder and others (e.g., system administrators, cybersecurity engineers) who perform incident response tasks to record their actions and make the record immutable
- Implementation example: Require the incident lead to document the incident in detail and be responsible for preserving the integrity of the documentation and the sources of all information being reported

### RS.AN-07

Incident data and metadata are collected, and their integrity and provenance are preserved
- Implementation example: Collect, preserve, and safeguard the integrity of all pertinent incident data and metadata (e.g., data source, date/time of collection) based on evidence preservation and chain-of-custody procedures

### RS.AN-08

An incident's magnitude is estimated and validated
- Implementation example: Review other potential targets of the incident to search for indicators of compromise and evidence of persistence
- Implementation example: Automatically run tools on targets to look for indicators of compromise and evidence of persistence

## RS.CO Incident Response Reporting and Communication

Response activities are coordinated with internal and external stakeholders as required by laws, regulations, or policies

### RS.CO-02

Internal and external stakeholders are notified of incidents
- Implementation example: Follow the organization's breach notification procedures after discovering a data breach incident, including notifying affected customers
- Implementation example: Notify business partners and customers of incidents in accordance with contractual requirements
- Implementation example: Notify law enforcement agencies and regulatory bodies of incidents based on criteria in the incident response plan and management approval

### RS.CO-03

Information is shared with designated internal and external stakeholders
- Implementation example: Securely share information consistent with response plans and information sharing agreements
- Implementation example: Voluntarily share information about an attacker's observed TTPs, with all sensitive data removed, with an Information Sharing and Analysis Center (ISAC)
- Implementation example: Notify HR when malicious insider activity occurs
- Implementation example: Regularly update senior leadership on the status of major incidents
- Implementation example: Follow the rules and protocols defined in contracts for incident information sharing between the organization and its suppliers
- Implementation example: Coordinate crisis communication methods between the organization and its critical suppliers

## RS.MA Incident Management

Responses to detected cybersecurity incidents are managed

### RS.MA-01

The incident response plan is executed in coordination with relevant third parties once an incident is declared
- Implementation example: Detection technologies automatically report confirmed incidents
- Implementation example: Request incident response assistance from the organization's incident response outsourcer
- Implementation example: Designate an incident lead for each incident
- Implementation example: Initiate execution of additional cybersecurity plans as needed to support incident response (for example, business continuity and disaster recovery)

### RS.MA-02

Incident reports are triaged and validated
- Implementation example: Preliminarily review incident reports to confirm that they are cybersecurity-related and necessitate incident response activities
- Implementation example: Apply criteria to estimate the severity of an incident

### RS.MA-03

Incidents are categorized and prioritized
- Implementation example: Further review and categorize incidents based on the type of incident (e.g., data breach, ransomware, DDoS, account compromise)
- Implementation example: Prioritize incidents based on their scope, likely impact, and time-critical nature
- Implementation example: Select incident response strategies for active incidents by balancing the need to quickly recover from an incident with the need to observe the attacker or conduct a more thorough investigation

### RS.MA-04

Incidents are escalated or elevated as needed
- Implementation example: Track and validate the status of all ongoing incidents
- Implementation example: Coordinate incident escalation or elevation with designated internal and external stakeholders

### RS.MA-05

The criteria for initiating incident recovery are applied
- Implementation example: Apply incident recovery criteria to known and assumed characteristics of the incident to determine whether incident recovery processes should be initiated
- Implementation example: Take the possible operational disruption of incident recovery activities into account

## RS.MI Incident Mitigation

Activities are performed to prevent expansion of an event and mitigate its effects

### RS.MI-01

Incidents are contained
- Implementation example: Cybersecurity technologies (e.g., antivirus software) and cybersecurity features of other technologies (e.g., operating systems, network infrastructure devices) automatically perform containment actions
- Implementation example: Allow incident responders to manually select and perform containment actions
- Implementation example: Allow a third party (e.g., internet service provider, managed security service provider) to perform containment actions on behalf of the organization
- Implementation example: Automatically transfer compromised endpoints to a remediation virtual local area network (VLAN)

### RS.MI-02

Incidents are eradicated
- Implementation example: Cybersecurity technologies and cybersecurity features of other technologies (e.g., operating systems, network infrastructure devices) automatically perform eradication actions
- Implementation example: Allow incident responders to manually select and perform eradication actions
- Implementation example: Allow a third party (e.g., managed security service provider) to perform eradication actions on behalf of the organization

## Withdrawn controls

These controls are withdrawn. They are listed so old references still resolve.

- **RS.AN-01**: Withdrawn. Incorporated into: RS.MA-02.
- **RS.AN-02**: Withdrawn. Incorporated into: RS.MA-02, RS.MA-03, RS.MA-04.
- **RS.AN-04**: Withdrawn. Moved to: RS.MA-03.
- **RS.AN-05**: Withdrawn. Moved to: ID.RA-08.
- **RS.CO-01**: Withdrawn. Incorporated into: PR.AT-01.
- **RS.CO-04**: Withdrawn. Incorporated into: RS.MA-01, RS.MA-04.
- **RS.CO-05**: Withdrawn. Incorporated into: RS.CO-03.
- **RS.IM**: Withdrawn. Incorporated into: ID.IM.
- **RS.IM-01**: Withdrawn. Incorporated into: ID.IM-03, ID.IM-04.
- **RS.IM-02**: Withdrawn. Incorporated into: ID.IM-03.
- **RS.MI-03**: Withdrawn. Incorporated into: ID.RA-06.
- **RS.RP**: Withdrawn.
- **RS.RP-01**: Withdrawn. Incorporated into: RS.MA-01.
