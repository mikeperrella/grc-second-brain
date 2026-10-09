---
doc_id: nist-csf-2.0-id
type: framework
owner: "NIST"
---
# IDENTIFY (ID)

The organization's current cybersecurity risks are understood

## ID.AM Asset Management

Assets (e.g., data, hardware, software, systems, facilities, services, people) that enable the organization to achieve business purposes are identified and managed consistent with their relative importance to organizational objectives and the organization's risk strategy

### ID.AM-01

Inventories of hardware managed by the organization are maintained
- Implementation example: Maintain inventories for all types of hardware, including IT, IoT, OT, and mobile devices
- Implementation example: Constantly monitor networks to detect new hardware and automatically update inventories

### ID.AM-02

Inventories of software, services, and systems managed by the organization are maintained
- Implementation example: Maintain inventories for all types of software and services, including commercial-off-the-shelf, open-source, custom applications, API services, and cloud-based applications and services
- Implementation example: Constantly monitor all platforms, including containers and virtual machines, for software and service inventory changes
- Implementation example: Maintain an inventory of the organization's systems

### ID.AM-03

Representations of the organization's authorized network communication and internal and external network data flows are maintained
- Implementation example: Maintain baselines of communication and data flows within the organization's wired and wireless networks
- Implementation example: Maintain baselines of communication and data flows between the organization and third parties
- Implementation example: Maintain baselines of communication and data flows for the organization's infrastructure-as-a-service (IaaS) usage
- Implementation example: Maintain documentation of expected network ports, protocols, and services that are typically used among authorized systems

### ID.AM-04

Inventories of services provided by suppliers are maintained
- Implementation example: Inventory all external services used by the organization, including third-party infrastructure-as-a-service (IaaS), platform-as-a-service (PaaS), and software-as-a-service (SaaS) offerings; APIs; and other externally hosted application services
- Implementation example: Update the inventory when a new external service is going to be utilized to ensure adequate cybersecurity risk management monitoring of the organization's use of that service

### ID.AM-05

Assets are prioritized based on classification, criticality, resources, and impact on the mission
- Implementation example: Define criteria for prioritizing each class of assets
- Implementation example: Apply the prioritization criteria to assets
- Implementation example: Track the asset priorities and update them periodically or when significant changes to the organization occur

### ID.AM-07

Inventories of data and corresponding metadata for designated data types are maintained
- Implementation example: Maintain a list of the designated data types of interest (e.g., personally identifiable information, protected health information, financial account numbers, organization intellectual property, operational technology data)
- Implementation example: Continuously discover and analyze ad hoc data to identify new instances of designated data types
- Implementation example: Assign data classifications to designated data types through tags or labels
- Implementation example: Track the provenance, data owner, and geolocation of each instance of designated data types

### ID.AM-08

Systems, hardware, software, services, and data are managed throughout their life cycles
- Implementation example: Integrate cybersecurity considerations throughout the life cycles of systems, hardware, software, and services
- Implementation example: Integrate cybersecurity considerations into product life cycles
- Implementation example: Identify unofficial uses of technology to meet mission objectives (i.e., shadow IT)
- Implementation example: Periodically identify redundant systems, hardware, software, and services that unnecessarily increase the organization's attack surface
- Implementation example: Properly configure and secure systems, hardware, software, and services prior to their deployment in production
- Implementation example: Update inventories when systems, hardware, software, and services are moved or transferred within the organization
- Implementation example: Securely destroy stored data based on the organization's data retention policy using the prescribed destruction method, and keep and manage a record of the destructions
- Implementation example: Securely sanitize data storage when hardware is being retired, decommissioned, reassigned, or sent for repairs or replacement
- Implementation example: Offer methods for destroying paper, storage media, and other physical forms of data storage

## ID.IM Improvement

Improvements to organizational cybersecurity risk management processes, procedures and activities are identified across all CSF Functions

### ID.IM-01

Improvements are identified from evaluations
- Implementation example: Perform self-assessments of critical services that take current threats and TTPs into consideration
- Implementation example: Invest in third-party assessments or independent audits of the effectiveness of the organization's cybersecurity program to identify areas that need improvement
- Implementation example: Constantly evaluate compliance with selected cybersecurity requirements through automated means

### ID.IM-02

Improvements are identified from security tests and exercises, including those done in coordination with suppliers and relevant third parties
- Implementation example: Identify improvements for future incident response activities based on findings from incident response assessments (e.g., tabletop exercises and simulations, tests, internal reviews, independent audits)
- Implementation example: Identify improvements for future business continuity, disaster recovery, and incident response activities based on exercises performed in coordination with critical service providers and product suppliers
- Implementation example: Involve internal stakeholders (e.g., senior executives, legal department, HR) in security tests and exercises as appropriate
- Implementation example: Perform penetration testing to identify opportunities to improve the security posture of selected high-risk systems as approved by leadership
- Implementation example: Exercise contingency plans for responding to and recovering from the discovery that products or services did not originate with the contracted supplier or partner or were altered before receipt
- Implementation example: Collect and analyze performance metrics using security tools and services to inform improvements to the cybersecurity program

### ID.IM-03

Improvements are identified from execution of operational processes, procedures, and activities
- Implementation example: Conduct collaborative lessons learned sessions with suppliers
- Implementation example: Annually review cybersecurity policies, processes, and procedures to take lessons learned into account
- Implementation example: Use metrics to assess operational cybersecurity performance over time

### ID.IM-04

Incident response plans and other cybersecurity plans that affect operations are established, communicated, maintained, and improved
- Implementation example: Establish contingency plans (e.g., incident response, business continuity, disaster recovery) for responding to and recovering from adverse events that can interfere with operations, expose confidential information, or otherwise endanger the organization's mission and viability
- Implementation example: Include contact and communication information, processes for handling common scenarios, and criteria for prioritization, escalation, and elevation in all contingency plans
- Implementation example: Create a vulnerability management plan to identify and assess all types of vulnerabilities and to prioritize, test, and implement risk responses
- Implementation example: Communicate cybersecurity plans (including updates) to those responsible for carrying them out and to affected parties
- Implementation example: Review and update all cybersecurity plans annually or when a need for significant improvements is identified

## ID.RA Risk Assessment

The cybersecurity risk to the organization, assets, and individuals is understood by the organization

### ID.RA-01

Vulnerabilities in assets are identified, validated, and recorded
- Implementation example: Use vulnerability management technologies to identify unpatched and misconfigured software
- Implementation example: Assess network and system architectures for design and implementation weaknesses that affect cybersecurity
- Implementation example: Review, analyze, or test organization-developed software to identify design, coding, and default configuration vulnerabilities
- Implementation example: Assess facilities that house critical computing assets for physical vulnerabilities and resilience issues
- Implementation example: Monitor sources of cyber threat intelligence for information on new vulnerabilities in products and services
- Implementation example: Review processes and procedures for weaknesses that could be exploited to affect cybersecurity

### ID.RA-02

Cyber threat intelligence is received from information sharing forums and sources
- Implementation example: Configure cybersecurity tools and technologies with detection or response capabilities to securely ingest cyber threat intelligence feeds
- Implementation example: Receive and review advisories from reputable third parties on current threat actors and their tactics, techniques, and procedures (TTPs)
- Implementation example: Monitor sources of cyber threat intelligence for information on the types of vulnerabilities that emerging technologies may have

### ID.RA-03

Internal and external threats to the organization are identified and recorded
- Implementation example: Use cyber threat intelligence to maintain awareness of the types of threat actors likely to target the organization and the TTPs they are likely to use
- Implementation example: Perform threat hunting to look for signs of threat actors within the environment
- Implementation example: Implement processes for identifying internal threat actors

### ID.RA-04

Potential impacts and likelihoods of threats exploiting vulnerabilities are identified and recorded
- Implementation example: Business leaders and cybersecurity risk management practitioners work together to estimate the likelihood and impact of risk scenarios and record them in risk registers
- Implementation example: Enumerate the potential business impacts of unauthorized access to the organization's communications, systems, and data processed in or by those systems
- Implementation example: Account for the potential impacts of cascading failures for systems of systems

### ID.RA-05

Threats, vulnerabilities, likelihoods, and impacts are used to understand inherent risk and inform risk response prioritization
- Implementation example: Develop threat models to better understand risks to the data and identify appropriate risk responses
- Implementation example: Prioritize cybersecurity resource allocations and investments based on estimated likelihoods and impacts

### ID.RA-06

Risk responses are chosen, prioritized, planned, tracked, and communicated
- Implementation example: Apply the vulnerability management plan's criteria for deciding whether to accept, transfer, mitigate, or avoid risk
- Implementation example: Apply the vulnerability management plan's criteria for selecting compensating controls to mitigate risk
- Implementation example: Track the progress of risk response implementation (e.g., plan of action and milestones (POA&M), risk register, risk detail report)
- Implementation example: Use risk assessment findings to inform risk response decisions and actions
- Implementation example: Communicate planned risk responses to affected stakeholders in priority order

### ID.RA-07

Changes and exceptions are managed, assessed for risk impact, recorded, and tracked
- Implementation example: Implement and follow procedures for the formal documentation, review, testing, and approval of proposed changes and requested exceptions
- Implementation example: Document the possible risks of making or not making each proposed change, and provide guidance on rolling back changes
- Implementation example: Document the risks related to each requested exception and the plan for responding to those risks
- Implementation example: Periodically review risks that were accepted based upon planned future actions or milestones

### ID.RA-08

Processes for receiving, analyzing, and responding to vulnerability disclosures are established
- Implementation example: Conduct vulnerability information sharing between the organization and its suppliers following the rules and protocols defined in contracts
- Implementation example: Assign responsibilities and verify the execution of procedures for processing, analyzing the impact of, and responding to cybersecurity threat, vulnerability, or incident disclosures by suppliers, customers, partners, and government cybersecurity organizations

### ID.RA-09

The authenticity and integrity of hardware and software are assessed prior to acquisition and use
- Implementation example: Assess the authenticity and cybersecurity of critical technology products and services prior to acquisition and use

### ID.RA-10

Critical suppliers are assessed prior to acquisition
- Implementation example: Conduct supplier risk assessments against business and applicable cybersecurity requirements, including the supply chain

## Withdrawn controls

These controls are withdrawn. They are listed so old references still resolve.

- **ID.AM-06**: Withdrawn. Incorporated into: GV.RR-02, GV.SC-02.
- **ID.BE**: Withdrawn. Incorporated into: GV.OC.
- **ID.BE-01**: Withdrawn. Incorporated into: GV.OC-05.
- **ID.BE-02**: Withdrawn. Incorporated into: GV.OC-01.
- **ID.BE-03**: Withdrawn. Incorporated into: GV.OC-01.
- **ID.BE-04**: Withdrawn. Incorporated into: GV.OC-04, GV.OC-05.
- **ID.BE-05**: Withdrawn. Incorporated into: GV.OC-04.
- **ID.GV**: Withdrawn. Incorporated into: GV.
- **ID.GV-01**: Withdrawn. Incorporated into: GV.PO, GV.PO-01, GV.PO-02.
- **ID.GV-02**: Withdrawn. Incorporated into: GV.OC-02, GV.RR, GV.RR-02.
- **ID.GV-03**: Withdrawn. Moved to: GV.OC-03.
- **ID.GV-04**: Withdrawn. Moved to: GV.RM-04.
- **ID.RM**: Withdrawn. Incorporated into: GV.RM.
- **ID.RM-01**: Withdrawn. Incorporated into: GV.RM-01, GV.RM-06, GV.RR-03.
- **ID.RM-02**: Withdrawn. Incorporated into: GV.RM-02, GV.RM-04.
- **ID.RM-03**: Withdrawn. Moved to: GV.RM-02.
- **ID.SC**: Withdrawn. Incorporated into: GV.SC.
- **ID.SC-01**: Withdrawn. Incorporated into: GV.RM-05, GV.SC-01, GV.SC-06, GV.SC-09, GV.SC-10.
- **ID.SC-02**: Withdrawn. Incorporated into: GV.OC-02, GV.SC-03, GV.SC-04, GV.SC-07, ID.RA-10.
- **ID.SC-03**: Withdrawn. Moved to: GV.SC-05.
- **ID.SC-04**: Withdrawn. Incorporated into: GV.SC-07, ID.RA-10.
- **ID.SC-05**: Withdrawn. Incorporated into: GV.SC-08, ID.IM-02.
