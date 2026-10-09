---
doc_id: nist-csf-2.0-pr
type: framework
owner: "NIST"
---
# PROTECT (PR)

Safeguards to manage the organization's cybersecurity risks are used

## PR.AA Identity Management, Authentication, and Access Control

Access to physical and logical assets is limited to authorized users, services, and hardware and managed commensurate with the assessed risk of unauthorized access

### PR.AA-01

Identities and credentials for authorized users, services, and hardware are managed by the organization
- Implementation example: Initiate requests for new access or additional access for employees, contractors, and others, and track, review, and fulfill the requests, with permission from system or data owners when needed
- Implementation example: Issue, manage, and revoke cryptographic certificates and identity tokens, cryptographic keys (i.e., key management), and other credentials
- Implementation example: Select a unique identifier for each device from immutable hardware characteristics or an identifier securely provisioned to the device
- Implementation example: Physically label authorized hardware with an identifier for inventory and servicing purposes

### PR.AA-02

Identities are proofed and bound to credentials based on the context of interactions
- Implementation example: Verify a person's claimed identity at enrollment time using government-issued identity credentials (e.g., passport, visa, driver's license)
- Implementation example: Issue a different credential for each person (i.e., no credential sharing)

### PR.AA-03

Users, services, and hardware are authenticated
- Implementation example: Require multifactor authentication
- Implementation example: Enforce policies for the minimum strength of passwords, PINs, and similar authenticators
- Implementation example: Periodically reauthenticate users, services, and hardware based on risk (e.g., in zero trust architectures)
- Implementation example: Ensure that authorized personnel can access accounts essential for protecting safety under emergency conditions

### PR.AA-04

Identity assertions are protected, conveyed, and verified
- Implementation example: Protect identity assertions that are used to convey authentication and user information through single sign-on systems
- Implementation example: Protect identity assertions that are used to convey authentication and user information between federated systems
- Implementation example: Implement standards-based approaches for identity assertions in all contexts, and follow all guidance for the generation (e.g., data models, metadata), protection (e.g., digital signing, encryption), and verification (e.g., signature validation) of identity assertions

### PR.AA-05

Access permissions, entitlements, and authorizations are defined in a policy, managed, enforced, and reviewed, and incorporate the principles of least privilege and separation of duties
- Implementation example: Review logical and physical access privileges periodically and whenever someone changes roles or leaves the organization, and promptly rescind privileges that are no longer needed
- Implementation example: Take attributes of the requester and the requested resource into account for authorization decisions (e.g., geolocation, day/time, requester endpoint's cyber health)
- Implementation example: Restrict access and privileges to the minimum necessary (e.g., zero trust architecture)
- Implementation example: Periodically review the privileges associated with critical business functions to confirm proper separation of duties

### PR.AA-06

Physical access to assets is managed, monitored, and enforced commensurate with risk
- Implementation example: Use security guards, security cameras, locked entrances, alarm systems, and other physical controls to monitor facilities and restrict access
- Implementation example: Employ additional physical security controls for areas that contain high-risk assets
- Implementation example: Escort guests, vendors, and other third parties within areas that contain business-critical assets

## PR.AT Awareness and Training

The organization's personnel are provided with cybersecurity awareness and training so that they can perform their cybersecurity-related tasks

### PR.AT-01

Personnel are provided with awareness and training so that they possess the knowledge and skills to perform general tasks with cybersecurity risks in mind
- Implementation example: Provide basic cybersecurity awareness and training to employees, contractors, partners, suppliers, and all other users of the organization's non-public resources
- Implementation example: Train personnel to recognize social engineering attempts and other common attacks, report attacks and suspicious activity, comply with acceptable use policies, and perform basic cyber hygiene tasks (e.g., patching software, choosing passwords, protecting credentials)
- Implementation example: Explain the consequences of cybersecurity policy violations, both to individual users and the organization as a whole
- Implementation example: Periodically assess or test users on their understanding of basic cybersecurity practices
- Implementation example: Require annual refreshers to reinforce existing practices and introduce new practices

### PR.AT-02

Individuals in specialized roles are provided with awareness and training so that they possess the knowledge and skills to perform relevant tasks with cybersecurity risks in mind
- Implementation example: Identify the specialized roles within the organization that require additional cybersecurity training, such as physical and cybersecurity personnel, finance personnel, senior leadership, and anyone with access to business-critical data
- Implementation example: Provide role-based cybersecurity awareness and training to all those in specialized roles, including contractors, partners, suppliers, and other third parties
- Implementation example: Periodically assess or test users on their understanding of cybersecurity practices for their specialized roles
- Implementation example: Require annual refreshers to reinforce existing practices and introduce new practices

## PR.DS Data Security

Data are managed consistent with the organization's risk strategy to protect the confidentiality, integrity, and availability of information

### PR.DS-01

The confidentiality, integrity, and availability of data-at-rest are protected
- Implementation example: Use encryption, digital signatures, and cryptographic hashes to protect the confidentiality and integrity of stored data in files, databases, virtual machine disk images, container images, and other resources
- Implementation example: Use full disk encryption to protect data stored on user endpoints
- Implementation example: Confirm the integrity of software by validating signatures
- Implementation example: Restrict the use of removable media to prevent data exfiltration
- Implementation example: Physically secure removable media containing unencrypted sensitive information, such as within locked offices or file cabinets

### PR.DS-02

The confidentiality, integrity, and availability of data-in-transit are protected
- Implementation example: Use encryption, digital signatures, and cryptographic hashes to protect the confidentiality and integrity of network communications
- Implementation example: Automatically encrypt or block outbound emails and other communications that contain sensitive data, depending on the data classification
- Implementation example: Block access to personal email, file sharing, file storage services, and other personal communications applications and services from organizational systems and networks
- Implementation example: Prevent reuse of sensitive data from production environments (e.g., customer records) in development, testing, and other non-production environments

### PR.DS-10

The confidentiality, integrity, and availability of data-in-use are protected
- Implementation example: Remove data that must remain confidential (e.g., from processors and memory) as soon as it is no longer needed
- Implementation example: Protect data in use from access by other users and processes of the same platform

### PR.DS-11

Backups of data are created, protected, maintained, and tested
- Implementation example: Continuously back up critical data in near-real-time, and back up other data frequently at agreed-upon schedules
- Implementation example: Test backups and restores for all types of data sources at least annually
- Implementation example: Securely store some backups offline and offsite so that an incident or disaster will not damage them
- Implementation example: Enforce geographic separation and geolocation restrictions for data backup storage

## PR.IR Technology Infrastructure Resilience

Security architectures are managed with the organization's risk strategy to protect asset confidentiality, integrity, and availability, and organizational resilience

### PR.IR-01

Networks and environments are protected from unauthorized logical access and usage
- Implementation example: Logically segment organization networks and cloud-based platforms according to trust boundaries and platform types (e.g., IT, IoT, OT, mobile, guests), and permit required communications only between segments
- Implementation example: Logically segment organization networks from external networks, and permit only necessary communications to enter the organization's networks from the external networks
- Implementation example: Implement zero trust architectures to restrict network access to each resource to the minimum necessary
- Implementation example: Check the cyber health of endpoints before allowing them to access and use production resources

### PR.IR-02

The organization's technology assets are protected from environmental threats
- Implementation example: Protect organizational equipment from known environmental threats, such as flooding, fire, wind, and excessive heat and humidity
- Implementation example: Include protection from environmental threats and provisions for adequate operating infrastructure in requirements for service providers that operate systems on the organization's behalf

### PR.IR-03

Mechanisms are implemented to achieve resilience requirements in normal and adverse situations
- Implementation example: Avoid single points of failure in systems and infrastructure
- Implementation example: Use load balancing to increase capacity and improve reliability
- Implementation example: Use high-availability components like redundant storage and power supplies to improve system reliability

### PR.IR-04

Adequate resource capacity to ensure availability is maintained
- Implementation example: Monitor usage of storage, power, compute, network bandwidth, and other resources
- Implementation example: Forecast future needs, and scale resources accordingly

## PR.PS Platform Security

The hardware, software (e.g., firmware, operating systems, applications), and services of physical and virtual platforms are managed consistent with the organization's risk strategy to protect their confidentiality, integrity, and availability

### PR.PS-01

Configuration management practices are established and applied
- Implementation example: Establish, test, deploy, and maintain hardened baselines that enforce the organization's cybersecurity policies and provide only essential capabilities (i.e., principle of least functionality)
- Implementation example: Review all default configuration settings that may potentially impact cybersecurity when installing or upgrading software
- Implementation example: Monitor implemented software for deviations from approved baselines

### PR.PS-02

Software is maintained, replaced, and removed commensurate with risk
- Implementation example: Perform routine and emergency patching within the timeframes specified in the vulnerability management plan
- Implementation example: Update container images, and deploy new container instances to replace rather than update existing instances
- Implementation example: Replace end-of-life software and service versions with supported, maintained versions
- Implementation example: Uninstall and remove unauthorized software and services that pose undue risks
- Implementation example: Uninstall and remove any unnecessary software components (e.g., operating system utilities) that attackers might misuse
- Implementation example: Define and implement plans for software and service end-of-life maintenance support and obsolescence

### PR.PS-03

Hardware is maintained, replaced, and removed commensurate with risk
- Implementation example: Replace hardware when it lacks needed security capabilities or when it cannot support software with needed security capabilities
- Implementation example: Define and implement plans for hardware end-of-life maintenance support and obsolescence
- Implementation example: Perform hardware disposal in a secure, responsible, and auditable manner

### PR.PS-04

Log records are generated and made available for continuous monitoring
- Implementation example: Configure all operating systems, applications, and services (including cloud-based services) to generate log records
- Implementation example: Configure log generators to securely share their logs with the organization's logging infrastructure systems and services
- Implementation example: Configure log generators to record the data needed by zero-trust architectures

### PR.PS-05

Installation and execution of unauthorized software are prevented
- Implementation example: When risk warrants it, restrict software execution to permitted products only or deny the execution of prohibited and unauthorized software
- Implementation example: Verify the source of new software and the software's integrity before installing it
- Implementation example: Configure platforms to use only approved DNS services that block access to known malicious domains
- Implementation example: Configure platforms to allow the installation of organization-approved software only

### PR.PS-06

Secure software development practices are integrated, and their performance is monitored throughout the software development life cycle
- Implementation example: Protect all components of organization-developed software from tampering and unauthorized access
- Implementation example: Secure all software produced by the organization, with minimal vulnerabilities in their releases
- Implementation example: Maintain the software used in production environments, and securely dispose of software once it is no longer needed

## Withdrawn controls

These controls are withdrawn. They are listed so old references still resolve.

- **PR.AC**: Withdrawn. Moved to: PR.AA.
- **PR.AC-01**: Withdrawn. Incorporated into: PR.AA-01, PR.AA-05.
- **PR.AC-02**: Withdrawn. Moved to: PR.AA-06.
- **PR.AC-03**: Withdrawn. Incorporated into: PR.AA-03, PR.AA-05, PR.IR-01.
- **PR.AC-04**: Withdrawn. Moved to: PR.AA-05.
- **PR.AC-05**: Withdrawn. Incorporated into: PR.IR-01.
- **PR.AC-06**: Withdrawn. Moved to: PR.AA-02.
- **PR.AC-07**: Withdrawn. Moved to: PR.AA-03.
- **PR.AT-03**: Withdrawn. Incorporated into: PR.AT-01, PR.AT-02.
- **PR.AT-04**: Withdrawn. Incorporated into: PR.AT-02.
- **PR.AT-05**: Withdrawn. Incorporated into: PR.AT-02.
- **PR.DS-03**: Withdrawn. Incorporated into: ID.AM-08, PR.PS-03.
- **PR.DS-04**: Withdrawn. Moved to: PR.IR-04.
- **PR.DS-05**: Withdrawn. Incorporated into: PR.DS-01, PR.DS-02, PR.DS-10.
- **PR.DS-06**: Withdrawn. Incorporated into: DE.CM-09, PR.DS-01.
- **PR.DS-07**: Withdrawn. Incorporated into: PR.IR-01.
- **PR.DS-08**: Withdrawn. Incorporated into: DE.CM-09, ID.RA-09.
- **PR.IP**: Withdrawn.
- **PR.IP-01**: Withdrawn. Incorporated into: PR.PS-01.
- **PR.IP-02**: Withdrawn. Incorporated into: ID.AM-08, PR.PS-06.
- **PR.IP-03**: Withdrawn. Incorporated into: ID.RA-07, PR.PS-01.
- **PR.IP-04**: Withdrawn. Moved to: PR.DS-11.
- **PR.IP-05**: Withdrawn. Incorporated into: PR.IR-02.
- **PR.IP-06**: Withdrawn. Incorporated into: ID.AM-08.
- **PR.IP-07**: Withdrawn. Incorporated into: ID.IM, ID.IM-03.
- **PR.IP-08**: Withdrawn. Moved to: ID.IM-03.
- **PR.IP-09**: Withdrawn. Moved to: ID.IM-04.
- **PR.IP-10**: Withdrawn. Moved to: ID.IM-02, ID.IM-04.
- **PR.IP-11**: Withdrawn. Moved to: GV.RR-04.
- **PR.IP-12**: Withdrawn. Moved to: ID.RA-01, PR.PS-02.
- **PR.MA**: Withdrawn. Incorporated into: ID.AM-08.
- **PR.MA-01**: Withdrawn. Moved to: ID.AM-08, PR.PS-03.
- **PR.MA-02**: Withdrawn. Moved to: ID.AM-08, PR.PS-02.
- **PR.PT**: Withdrawn.
- **PR.PT-01**: Withdrawn. Incorporated into: PR.PS-04.
- **PR.PT-02**: Withdrawn. Incorporated into: PR.DS-01, PR.PS-01.
- **PR.PT-03**: Withdrawn. Incorporated into: PR.PS-01.
- **PR.PT-04**: Withdrawn. Incorporated into: PR.AA-06, PR.IR-01.
- **PR.PT-05**: Withdrawn. Incorporated into: PR.IR-03.
