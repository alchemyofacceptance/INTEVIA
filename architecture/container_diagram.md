# INTEVIA — Container Diagram (v0.1)

## 1. Purpose of the Container Diagram

The Container Diagram defines the major runtime containers that make up the INTEVIA system. It shows how the system is decomposed into deployable units, how they communicate, and how they collectively form the INTEVIA organism.

This Container Diagram describes the broader target runtime architecture. It is not itself the v1.0 Implementation Boundary. v1.0 implementation remains governed by the v1.0 Boundary, Forge Activation Packet, Execution Plan, and Build Tasks.

This is the second layer of the architecture, sitting directly below the System Context.

---

## 2. Containers Overview

INTEVIA consists of the following primary containers:

* **Web Application (Django API + Future Frontend)**
* **Worker Processes (Async + Scheduled Tasks)**
* **Data Layer (PostgreSQL + Object Storage)**
* **Governance Engine**
* **HAT Collaboration Layer**
* **External Integrations Layer**

Each container has a clear responsibility and communicates through governed interfaces.

For v1.0, implementation should focus only on the minimal runtime subset required by the v1.0 Boundary and Forge Build Tasks. Future-facing containers and integrations should remain mapped but not prematurely implemented.

---

## 3. Container Responsibilities

### 3.1 Web Application (Django API + Future Frontend)

**Purpose:**
The central interface for human and system interactions.

**Responsibilities:**

* Expose REST/JSON APIs
* Serve frontend surfaces where implemented
* Handle authentication and permissions
* Provide governed operations for implemented organism modules
* Enforce governance rules at the API boundary
* Validate and route requests to internal services

**Technology:**

* Django
* Django REST Framework
* Future: React/Vue frontend

**v1.0 Boundary Note:**
For v1.0, the Web Application should support only the operational spine required by Events, Services, Library, Governance Spine, CARE seed layer, and minimal identity substrate.

---

### 3.2 Worker Processes (Async + Scheduled Tasks)

**Purpose:**
Execute background, long-running, or scheduled tasks.

**Responsibilities:**

* Process notifications where required
* Support async evidence and audit workflows
* Run scheduled governance checks
* Perform controlled data imports/exports
* Support background processing for implemented modules

**Technology:**

* Celery / RQ / Django-Q — final selection pending
* Redis — task queue

**v1.0 Boundary Note:**
For v1.0, workers should remain minimal and should not introduce deferred organs, HAT productisation, or broad Corpus maintenance workflows unless explicitly ratified.

---

### 3.3 Data Layer (PostgreSQL + Object Storage)

**Purpose:**
Store persistent data and artefacts.

**Responsibilities:**

* Relational data storage
* File and media storage where required
* Maintain integrity of implemented organism modules
* Support evidence records, audit events, versioning, and governed artefact links

**Technology:**

* PostgreSQL
* Object storage: MinIO / AWS S3 / DigitalOcean Spaces — final selection pending

**v1.0 Boundary Note:**
PostgreSQL is part of the core v1.0 substrate. Object storage may be implemented only where required by Library, evidence, or artefact workflows.

---

### 3.4 Governance Engine

**Purpose:**
Central enforcement of INTEVIA’s governance rules.

**Responsibilities:**

* Permission evaluation
* Role and capability resolution
* State transition validation
* Boundary rule enforcement
* Evidence classification
* Governance logging and audit trails
* Validation of actions across implemented modules

**Technology:**

* Python governance layer
* Integrated with Django permissions
* Future: rule engine abstraction

**v1.0 Boundary Note:**
The Governance Engine is load-bearing in v1.0. Governance must be executable, not decorative: the system must block, warn, or record according to governance rules.

---

### 3.5 HAT Collaboration Layer

**Purpose:**
Provide human-governed AI-assisted interpretation, sequencing, and reflection across the broader organism.

**Responsibilities:**

* Assist with task interpretation
* Support governance decision-making
* Preserve meaning continuity
* Provide reflective insights
* Enable human-led Human–AI collaboration workflows

**Technology:**

* External AI model APIs
* Internal orchestration logic — future-facing

**v1.0 Boundary Note:**
For v1.0, HAT remains a governed collaboration method and build-governance layer, not a runtime product container. HAT governs the build and interpretation layer; it is not implemented as a v1.0 product module.

---

### 3.6 External Integrations Layer

**Purpose:**
Provide governed interfaces to external systems.

**Responsibilities:**

* Email delivery
* Authentication providers
* Notification channels
* Payment processing
* Organisation system integrations such as HR, CRM, and finance

**Technology:**

* SMTP / transactional email
* OAuth / SSO — future
* Stripe API — future
* External organisation APIs — future

**v1.0 Boundary Note:**
For v1.0, integrations should remain minimal. Payment processing, marketplace mechanics, broad organisation-system integrations, SSO, and finance/HR/CRM integrations are deferred unless explicitly ratified.

---

## 4. Container Interaction Diagram (Textual)

```text
+---------------------------------------------------------------+
|                        External Actors                        |
|  People, Email Services, AI Models, Future Integrations       |
+----------------------------+----------------------------------+
                             |
                             | API calls, events, web requests
                             v
+---------------------------------------------------------------+
|                   Web Application (Django)                    |
|  - REST API                                                   |
|  - Authentication & Permissions                               |
|  - Frontend Delivery where implemented                        |
|  - Governance Enforcement                                     |
+----------------------------+----------------------------------+
                             |
                             | Governed internal service calls
                             v
+---------------------------------------------------------------+
|                     Governance Engine                         |
|  - Rule evaluation                                            |
|  - Capability resolution                                      |
|  - State transition validation                                |
|  - Audit & logging                                            |
+----------------------------+----------------------------------+
                             |
                             | Background tasks, async jobs
                             v
+---------------------------------------------------------------+
|                     Worker Processes                          |
|  - Notifications where required                               |
|  - Evidence and audit workflows                               |
|  - Scheduled governance checks                                |
|  - Controlled imports/exports                                 |
+----------------------------+----------------------------------+
                             |
                             | Read/write operations
                             v
+---------------------------------------------------------------+
|                         Data Layer                            |
|  PostgreSQL + Object Storage where required                   |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                   HAT Collaboration Layer                     |
|  - Interpretation                                             |
|  - Sequencing                                                 |
|  - Reflection                                                 |
|                                                               |
|  v1.0 note: governs build and interpretation;                 |
|  not implemented as a v1.0 product module.                    |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                 External Integrations Layer                   |
|  - Email / notifications where required                       |
|  - Future: payments, SSO, HR, CRM, finance                    |
|                                                               |
|  v1.0 note: broad integrations are deferred unless ratified.  |
+---------------------------------------------------------------+
```

---

## 5. v1.0 Implementation Boundary Summary

The Container Diagram names the broader runtime architecture, but the first implementation slice is narrower.

For v1.0:

* The **Web Application** supports the implemented operational spine.
* The **Data Layer** supports core persistence, audit, evidence, and artefact/versioning needs.
* The **Governance Engine** enforces permissions, transitions, boundary rules, evidence classification, and audit events.
* **Worker Processes** remain minimal and support only required async or scheduled workflows.
* The **HAT Collaboration Layer** governs build and interpretation but is not a v1.0 product module.
* The **External Integrations Layer** remains minimal; broad integrations are deferred.

Deferred for v1.0 unless separately ratified:

* payment processing;
* marketplace mechanics;
* SSO;
* HR/CRM/finance integrations;
* HAT productisation;
* broad Corpus maintenance automation;
* Education, Discussion, Recognition, and Exchange runtime modules.

---

## 6. Summary

The Container Diagram defines the major deployable units of the INTEVIA organism and the governed interactions between them. It establishes the broader runtime structure that supports the organism’s behaviour, governance, and human–AI collaboration.

The Container Diagram shows what the organism may run as.

The v1.0 Boundary decides what must run first.
