# Identity State Transitions v0.1

## Purpose

This file defines how identity records move through governed states, what transitions mean, and how actors interact with lineage, evidence, Services, Events, Library resources, and profile update boundaries.

Identity state transitions answer:

> What condition is this identity record in, what movement is allowed next, who may authorise it, and what evidence or audit record should be produced?

In INTEVIA, identity records are not merely created or updated. They move through governed lifecycles. Each meaningful transition can carry authority, evidence, audit, lineage, profile update boundaries, Event linkage, Service linkage, Library linkage, and CARE guidance.

---

## v0.1 Status

This is a placeholder architecture file.

Full content will be expanded in v0.2.

---

## v0.2 Expansion Targets

The next version should define:

* Identity state transition purpose and scope;
* minimal v1.0 identity lifecycle states;
* valid transitions;
* prohibited transitions;
* transition permissions;
* transition evidence requirements;
* transition audit events;
* relationship to IdentityRecord;
* relationship to IdentityRecordVersion;
* relationship to IdentityRecordEvidence;
* relationship to Events;
* relationship to Services;
* relationship to Library resources;
* relationship to ProfileUpdate;
* relationship to Governance Engine;
* relationship to CARE boundary warnings;
* creation, activation, revision, deactivation, archival behaviour;
* authority boundary changes;
* responsibility boundary changes;
* capability boundary changes;
* public-safe versus governed/internal transition boundaries;
* v1.0 implementation constraints.

---

## Boundary Note

For v1.0, identity state transitions should remain minimal, explicit, and executable.

The priority is to ensure that identity records can move through a small set of governed states such as draft, active, revised, deactivated, deprecated, and archived without losing auditability, lineage, authority clarity, responsibility clarity, capability boundaries, or meaning.

Advanced workflow engines, automated identity orchestration, semantic transition inference, broad relationship modelling, marketplace transitions, payment-linked transitions, and broad notification automations remain deferred unless separately ratified.
