# INTEVIA / architecture/library/README.md v0.1

*A guided entry point into the Library Domain — the organism’s knowledge and resource layer.*

---

## 1. Purpose of This Directory

This directory defines the Library Domain: the layer where the organism stores, structures, retrieves, links, and governs its knowledge and resources.

Library artefacts are not raw files, documents, or uploads. They are governed knowledge objects with type, lineage, evidence, state, versioning, and transition rules.

This directory defines the public-safe architectural model for the Library. It does not expose internal knowledge engines, private modelling patterns, or governed practice materials.

---

## 2. Components of the Library Domain

### 2.1 library_overview.md

Defines the purpose, scope, and conceptual stance of the Library as the organism’s knowledge and resource layer.

[Open library_overview.md](./library_overview.md)

---

### 2.2 library_types.md

Defines the categories of Library artefacts and their classification boundaries.

[Open library_types.md](./library_types.md)

---

### 2.3 library_resources.md

Defines how Library artefacts are structured, linked, versioned, and governed.

[Open library_resources.md](./library_resources.md)

---

### 2.4 library_state_transitions.md

Defines how Library artefacts move through governed states and how knowledge interacts with lineage, evidence, Services, Events, and profile updates.

[Open library_state_transitions.md](./library_state_transitions.md)

---

## 3. v1.0 Boundary

The Library is load-bearing in v1.0.

Included in v1.0:

* minimal Library model;
* public-safe Library types;
* artefact definition;
* artefact versioning;
* resource linking;
* evidence and audit attachment points;
* Service and Event linkage;
* governed state transitions;
* profile update boundaries;
* integration with Governance Engine, Corpus, CARE seed layer, Services, Events, and minimal identity substrate.

Deferred unless ratified:

* semantic knowledge graphs;
* automated knowledge inference;
* advanced recommendation engines;
* marketplace resources;
* payment-linked resources;
* full Exchange integration;
* full Education integration;
* broad content marketplace behaviour.

For v1.0, the Library is a governed knowledge and resource layer, not a full knowledge engine or marketplace.

---

## 4. How to Read This Slice

New developers should read the files in this order:

1. **library_overview.md** — what the Library is.
2. **library_types.md** — how artefacts are classified.
3. **library_resources.md** — how artefacts are structured, linked, and versioned.
4. **library_state_transitions.md** — how artefacts move.

This sequence provides a complete first mental model of the Library in under an hour.

---

## 5. Summary

The Library Domain is the organism’s knowledge and resource layer.

It defines what the organism has, how knowledge is classified, how resources are structured, how artefacts link to Events and Services, and how knowledge moves through governed states.

The v1.0 Boundary decides what must be implemented first.
