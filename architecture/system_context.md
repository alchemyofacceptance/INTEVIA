# INTEVIA — System Context (v0.1)

## 1. Purpose of the System Context

The System Context defines the outer boundary of the INTEVIA organism: what it is, what it is not, and the external actors and systems that interact with it. This is the highest-level architectural view from which all other architectural layers descend.

This System Context describes the broader INTEVIA organism and target system boundary. It is not itself the v1.0 Implementation Boundary. v1.0 implementation remains governed by the v1.0 Boundary, Forge Activation Packet, Execution Plan, and Build Tasks.

---

## 2. INTEVIA as a System

INTEVIA is a unified socio-technical platform integrating:

* organisational operations;
* learning and development;
* events and coordination;
* service and contribution;
* governance and decision-making;
* human–AI collaboration through HAT;
* knowledge and lineage through the Corpus.

INTEVIA behaves as a living organism, not a collection of disconnected applications. It provides a coherent environment where people, processes, knowledge, governance, and intelligent automation can evolve together.

---

## 3. System Boundary

The broader INTEVIA system boundary includes:

* INTEVIA Web Platform — Django backend and future frontend;
* INTEVIA Data Layer — PostgreSQL and object storage where required;
* Governance Engine;
* HAT Collaboration Layer;
* Organism Modules:

  * Events;
  * People;
  * Locations;
  * Service;
  * Exchange;
  * Education;
  * Library;
  * Applications;
  * Care/Core;
* Corpus-derived knowledge, lineage, and documentation;
* API surface for governed integrations.

Everything outside this boundary is considered an external actor or external system.

For v1.0, only the implementation subset ratified by the v1.0 Boundary is in scope. The full organism boundary names the broader system; it does not authorise all modules or integrations to be built in v1.0.

---

## 4. External Actors

### 4.1 People

* Members;
* organisers;
* administrators;
* guests.

### 4.2 External Services

* email providers;
* notification channels;
* authentication providers — future;
* payment processors — future.

### 4.3 External Systems

* third-party applications such as calendars and communication tools;
* data import/export sources;
* organisation systems such as HR, CRM, and finance — future.

### 4.4 AI Systems

* external AI models used for governed augmentation;
* model hosting providers;
* future AI orchestration providers where explicitly ratified.

---

## 5. High-Level Interactions

### 5.1 Human Interaction

* Members join, participate, learn, and contribute.
* Organisers coordinate events and operations.
* Administrators configure governance and structure.
* Human governance remains responsible for authority, judgement, ratification, and correction.

### 5.2 Operational Interaction

* Events are created, scheduled, attended, and evidenced.
* Services are requested, delivered, and tracked.
* Knowledge is created, versioned, and linked.
* Contributions and recognition flows are part of the broader organism, but remain governed by implementation boundaries.

### 5.3 AI Interaction

* AI assists with interpretation, sequencing, and reflection.
* AI supports governed decision-making and sense-making.
* AI may support Corpus-derived knowledge workflows where explicitly governed.
* AI does not replace Human governance authority.

### 5.4 External System Interaction

* Emails and notifications may be sent through external providers.
* Payments may flow through external processors in future phases.
* Data may be exchanged with organisational systems in future phases.
* External integrations must pass governance review before implementation.

---

## 6. v1.0 Boundary Note

The System Context names the broader organism boundary.

For v1.0:

* Events, Services, Library, Governance Spine, CARE seed layer, and minimal identity substrate form the operational spine.
* HAT governs build and interpretation but is not implemented as a v1.0 product module.
* Exchange, Education, Applications, broad Locations functionality, payment processing, marketplace mechanics, SSO, HR/CRM/finance integrations, and HAT productisation remain deferred unless separately ratified.
* External AI systems may assist the governed build process, but runtime AI productisation is not assumed by this document.

This distinction prevents the system context from expanding the implementation scope by accident.

---

## 7. System Context Diagram (Textual)

```text
+---------------------------------------------------------------+
|                           EXTERNAL                            |
|                                                               |
|  People: Members, Organisers, Admins, Guests                  |
|  External Services: Email, Notifications, Future Auth/Payment |
|  External Systems: Calendars, Communication Tools, Future HR  |
|  External AI Models: governed augmentation                    |
|                                                               |
+----------------------------+----------------------------------+
                             |
                             | Interactions, API calls, events
                             v
+---------------------------------------------------------------+
|                           INTEVIA                             |
|                                                               |
|  Web Platform (Django API + future frontend)                  |
|  Governance Engine                                            |
|  HAT Collaboration Layer                                      |
|     - governs build and interpretation                        |
|     - not a v1.0 product module                               |
|                                                               |
|  Organism Modules:                                            |
|     - Events                                                  |
|     - People                                                  |
|     - Locations                                               |
|     - Service                                                 |
|     - Exchange                                                |
|     - Education                                               |
|     - Library                                                 |
|     - Applications                                            |
|     - Care/Core                                               |
|                                                               |
|  Corpus-derived Knowledge + Lineage                           |
|  Data Layer (PostgreSQL + Object Storage where required)      |
|  Governed API Surface                                         |
|                                                               |
+---------------------------------------------------------------+
```

---

## 8. Summary

INTEVIA is a unified organism platform interacting with people, external services, external systems, and external AI models. Its broader boundary contains the platform, Governance Engine, organism modules, Corpus-derived knowledge, Data Layer, API surface, and HAT Collaboration Layer.

This document defines the outermost architectural frame of the system.

The System Context names the organism’s boundary.

The v1.0 Boundary decides what enters the first build.
