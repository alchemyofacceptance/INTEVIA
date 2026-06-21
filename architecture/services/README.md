# INTEVIA / architecture/services/README.md v0.1

*A guided entry point into the Service Domain — the organism’s capability layer.*

---

## 1. Purpose of This Directory

This directory defines the Service Domain: the layer that describes what the organism can do.

Services represent governed capabilities that can be defined, invoked, delivered, evidenced, linked to Events, and constrained by governance.

Services are not scripts, not endpoints, and not raw functions. They are governed capability objects with type, contract, state, lineage, evidence, and transition rules.

This directory defines the public-safe architectural model for Services. It does not expose internal capability engines, private modelling patterns, or governed practice materials.

---

## 2. Components of the Service Domain

### 2.1 services_overview.md

Defines the purpose, scope, and conceptual stance of Services as the organism’s capability layer.

[Open services_overview.md](./services_overview.md)

---

### 2.2 service_types.md

Defines the categories of Services and their classification boundaries.

[Open service_types.md](./service_types.md)

---

### 2.3 service_contracts.md

Defines how Services declare their inputs, outputs, constraints, responsibilities, and governed behaviour.

[Open service_contracts.md](./service_contracts.md)

---

### 2.4 service_state_transitions.md

Defines how Services move through governed states and how capability delivery interacts with lineage, evidence, profile updates, and Events.

[Open service_state_transitions.md](./service_state_transitions.md)

---

## 3. v1.0 Boundary

Services are load-bearing in v1.0.

Included in v1.0:

* minimal Service model;
* public-safe Service types;
* Service definition;
* provider assignment;
* Service invocation and delivery;
* Service–Event linkage;
* Service contracts;
* governed state transitions;
* evidence and audit attachment points;
* profile update boundaries;
* integration with Governance Engine, Corpus, Library, CARE seed layer, Events, and minimal identity substrate.

Deferred unless ratified:

* advanced capability engines;
* automated Service orchestration;
* semantic capability graphs;
* payment-linked Services;
* marketplace Services;
* broad notification systems;
* full Exchange integration;
* full Education integration.

For v1.0, Services are governed capability delivery objects, not a full service orchestration system.

---

## 4. How to Read This Slice

New developers should read the files in this order:

1. **services_overview.md** — what Services are.
2. **service_types.md** — how Services are classified.
3. **service_contracts.md** — how Services declare behaviour.
4. **service_state_transitions.md** — how Services move.

This sequence provides a complete first mental model of Services in under an hour.

---

## 5. Summary

The Service Domain is the organism’s capability layer.

It defines what the organism can do, how capabilities are classified, how they declare behaviour, how they link to Events and evidence, and how they move through governed states.

The v1.0 Boundary decides what must be implemented first.
