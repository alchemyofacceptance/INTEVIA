# Event State Transitions v0.1

## Purpose

This file defines how Events move through governed states, what transitions mean, and how state changes interact with lineage and evidence.

Event state transitions answer:

> What condition is this Event in, what movement is allowed next, who may authorise it, and what evidence or audit record should be produced?

In INTEVIA, Events are not merely created, edited, or deleted. They move through governed lifecycles. Each meaningful transition can carry authority, evidence, audit, lineage, and CARE guidance.

---

## v0.1 Status

This is a placeholder architecture file.

Full content will be expanded in v0.2.

---

## v0.2 Expansion Targets

The next version should define:

* Event state transition purpose and scope;
* minimal v1.0 Event lifecycle states;
* valid transitions;
* prohibited transitions;
* transition permissions;
* transition evidence requirements;
* transition audit events;
* relationship to EventStateHistory;
* relationship to Booking and Attendance;
* relationship to Library resource links;
* relationship to Governance Engine;
* relationship to CARE boundary warnings;
* cancellation and archival behaviour;
* public-safe versus governed/internal transition boundaries;
* v1.0 implementation constraints.

---

## Boundary Note

For v1.0, Event state transitions should remain minimal, explicit, and executable.

The priority is not to model every future Event lifecycle. The priority is to ensure that Events can move through a small set of governed states such as draft, published, booked or registered, attended, evidenced, reviewed, cancelled, and archived without losing auditability or meaning.

Advanced workflow engines, automated event orchestration, semantic transition inference, payment-linked transitions, and broad notification automations remain deferred unless separately ratified.
