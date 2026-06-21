# Library State Transitions v0.1

## Purpose

This file defines how Library artefacts move through governed states, what transitions mean, and how knowledge interacts with lineage, evidence, Services, Events, and profile update boundaries.

Library state transitions answer:

> What condition is this artefact in, what movement is allowed next, who may authorise it, and what evidence or audit record should be produced?

In INTEVIA, Library artefacts are not merely created or updated. They move through governed lifecycles. Each meaningful transition can carry authority, evidence, audit, lineage, profile update boundaries, Event linkage, Service linkage, and CARE guidance.

---

## v0.1 Status

This is a placeholder architecture file.

Full content will be expanded in v0.2.

---

## v0.2 Expansion Targets

The next version should define:

* Library state transition purpose and scope;
* minimal v1.0 Library lifecycle states;
* valid transitions;
* prohibited transitions;
* transition permissions;
* transition evidence requirements;
* transition audit events;
* relationship to LibraryResource;
* relationship to LibraryResourceVersion;
* relationship to LibraryResourceEvidence;
* relationship to Events;
* relationship to Services;
* relationship to ProfileUpdate;
* relationship to Governance Engine;
* relationship to CARE boundary warnings;
* publication, revision, versioning, archival, and deprecation behaviour;
* public-safe versus governed/internal transition boundaries;
* v1.0 implementation constraints.

---

## Boundary Note

For v1.0, Library state transitions should remain minimal, explicit, and executable.

The priority is to ensure that artefacts can move through a small set of governed states such as draft, published, revised, versioned, deprecated, and archived without losing auditability, lineage, or meaning.

Advanced workflow engines, automated knowledge orchestration, semantic transition inference, payment-linked transitions, marketplace transitions, and broad notification automations remain deferred unless separately ratified.
