# INTEVIA / architecture/README.md v0.1

*A guided entry point into the INTEVIA architectural spine.*

---

## 1. Purpose of This Directory

This directory contains the core architectural spine of the INTEVIA organism.

It provides the three foundational layers that define:

* what the organism is;
* what the organism runs as;
* what the organism is made of.

These documents describe the broader INTEVIA organism, not the v1.0 implementation slice.

The v1.0 build remains governed by the v1.0 Boundary, Forge Activation Packet, Execution Plan, and Build Tasks.

---

## 2. Architectural Layers

INTEVIA’s architecture is structured into three primary layers, each building on the one above it.

### 2.1 System Context v0.1

Defines the outer boundary of the organism: external actors, external systems, AI systems, and the high-level interactions between them.

This is the highest-level view of INTEVIA.

It answers:

> What is INTEVIA in the world?

[Open System Context](./system_context.md)

---

### 2.2 Container Diagram v0.1

Defines the runtime structure of the organism: deployable units, communication paths, and governed interactions.

It answers:

> What does INTEVIA run as?

[Open Container Diagram](./container_diagram.md)

---

### 2.3 Domain Map v0.1

Defines the semantic structure of the organism: the nine core domains, their responsibilities, and their interactions.

It answers:

> What is INTEVIA made of?

[Open Domain Map](./domain_map.md)

---

## 3. Governance Layers

Three cross-cutting governance layers shape the architecture:

* **Governance Engine** — executable rules, permissions, transitions, evidence, and audit;
* **Corpus** — knowledge, lineage, and documentation;
* **HAT Collaboration Layer** — interpretation, sequencing, and reflection; governs build and meaning.

These layers help the organism remain coherent, safe, and human-led.

---

## 4. v1.0 Boundary

The architecture describes the full organism, but v1.0 is intentionally narrow.

v1.0 includes:

* Events;
* Service;
* Library;
* Governance Spine;
* CARE seed layer;
* minimal identity substrate.

Deferred unless ratified:

* Exchange;
* Education;
* Applications;
* broad Locations;
* payments;
* marketplace mechanics;
* SSO;
* HR, CRM, and finance integrations;
* HAT productisation.

For details, see the v1.0 Boundary, Forge Activation Packet, Execution Plan, and Build Tasks.

---

## 5. How to Read This Architecture

New developers should read the architecture in this order:

1. **System Context** — understand the organism boundary.
2. **Container Diagram** — understand the runtime shape.
3. **Domain Map** — understand the semantic organs.
4. **Governance boundary notes** — understand what is intentionally included, deferred, or protected.

This sequence gives a complete first mental model of INTEVIA in under an hour.

---

## 6. Summary

This directory contains the architectural spine of INTEVIA.

It defines the organism’s boundary, runtime structure, semantic domains, and governance layers.

The architecture shows what the organism is.

The v1.0 Boundary decides what must be built first.
