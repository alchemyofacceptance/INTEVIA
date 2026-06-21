# Service State Transitions v0.1

## Purpose

This file defines how Services move through governed states, what transitions mean, and how capability execution interacts with lineage, evidence, and Events.

Service state transitions answer:

> What condition is this Service in, what movement is allowed next, who may authorise it, and what evidence or audit record should be produced?

In INTEVIA, Services are not merely invoked or completed. They move through governed lifecycles. Each meaningful transition can carry authority, evidence, audit, lineage, profile impact, Event linkage, and CARE guidance.

---

## v0.1 Status

This is a placeholder architecture file.

Full content will be expanded in v0.2.

---

## v0.2 Expansion Targets

The next version should define:

* Service state transition purpose and scope;
* minimal v1.0 Service lifecycle states;
* valid transitions;
* prohibited transitions;
* transition permissions;
* transition evidence requirements;
* transition audit events;
* relationship to ServiceDefinition;
* relationship to ServiceProvider;
* relationship to ServiceActivity;
* relationship to ServiceActivityEvidence;
* relationship to ProfileUpdate;
* relationship to Events;
* relationship to Library resources;
* relationship to Governance Engine;
* relationship to CARE boundary warnings;
* cancellation, completion, review, and archival behaviour;
* public-safe versus governed/internal transition boundaries;
* v1.0 implementation constraints.

---

## Boundary Note

For v1.0, Service state transitions should remain minimal, explicit, and executable.

The priority is not to model every future Service lifecycle. The priority is to ensure that Services can move through a small set of governed states such as defined, assigned, active, delivered, evidenced, reviewed, cancelled, and archived without losing auditability, lineage, or meaning.

Advanced workflow engines, automated Service orchestration, semantic transition inference, payment-linked transitions, marketplace transitions, and broad notification automations remain deferred unless separately ratified.
