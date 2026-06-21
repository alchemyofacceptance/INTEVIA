# INTEVIA / architecture/identity/README.md v0.1

*A guided entry point into the Identity Domain — the organism’s minimal actor substrate.*

---

## 1. Purpose of This Directory

This directory defines the Identity Domain: the layer that describes who can act, who can be referenced, who can be linked to Events and Services, and who can carry capability, authority, or responsibility.

Identity in INTEVIA is minimal, governed, and capability-oriented. It is not a full user system, not a social graph, and not a CRM. It is the smallest possible substrate that allows the organism to attach meaning, authority, and responsibility to actors.

This directory defines the public-safe architectural model for Identity. It does not expose internal identity engines, private modelling patterns, or governed practice materials.

---

## 2. Components of the Identity Domain

### 2.1 identity_overview.md

Defines the purpose, scope, and conceptual stance of Identity as the organism’s actor substrate.

[Open identity_overview.md](./identity_overview.md)

---

### 2.2 identity_types.md

Defines the categories of identity records and their classification boundaries.

[Open identity_types.md](./identity_types.md)

---

### 2.3 identity_records.md

Defines how identity records are structured, linked, versioned, and governed.

[Open identity_records.md](./identity_records.md)

---

### 2.4 identity_state_transitions.md

Defines how identity records move through governed states and how actors interact with lineage, evidence, Services, Events, and profile update boundaries.

[Open identity_state_transitions.md](./identity_state_transitions.md)

---

## 3. v1.0 Boundary

Identity is load-bearing in v1.0, but only as a minimal actor substrate.

Included in v1.0:

* minimal identity model;
* public-safe identity types;
* identity record definition;
* identity linkage to Events and Services;
* capability-bearing identity boundaries;
* authority and responsibility attachment points;
* evidence and audit attachment points;
* governed identity state transitions;
* profile update boundaries;
* integration with Governance Engine, Corpus, CARE seed layer, Services, Events, and Library.

Deferred unless ratified:

* full user account system;
* authentication and authorisation engines;
* social graphs;
* broad relationship modelling;
* CRM behaviour;
* marketplace identity;
* payment-linked identity;
* full Exchange integration;
* full Education integration;
* advanced profile automation.

For v1.0, Identity is a minimal governed actor substrate, not a full identity system.

---

## 4. How to Read This Slice

New developers should read the files in this order:

1. **identity_overview.md** — what Identity is.
2. **identity_types.md** — how actors are classified.
3. **identity_records.md** — how identity is structured, linked, and governed.
4. **identity_state_transitions.md** — how identity moves.

This sequence provides a complete first mental model of Identity in under an hour.

---

## 5. Summary

The Identity Domain is the organism’s minimal actor substrate.

It defines who can act, who can be referenced, how actors are classified, how identity records are structured, how authority and responsibility attach, and how identity moves through governed states.

The v1.0 Boundary decides what must be implemented first.
