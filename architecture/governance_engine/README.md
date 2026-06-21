# INTEVIA / architecture/governance_engine/README.md v0.1

*A guided entry point into the Governance Engine — the layer that binds, validates, and protects the INTEVIA organism.*

---

## 1. Purpose of This Directory

This directory contains the architectural definition of the Governance Engine: the executable rule layer that helps INTEVIA remain coherent, safe, and human-led.

The Governance Engine is a cross-cutting layer.

It is not a domain, not a container, and not a module. It is the constitutional logic that binds the organism’s actions, permissions, transitions, evidence, and audit trails.

---

## 2. Components of the Governance Engine

### 2.1 governance_engine.md

Defines the purpose, responsibilities, and runtime stance of the Governance Engine.

[Open governance_engine.md](./governance_engine.md)

---

### 2.2 permissions_and_capabilities.md

Defines roles, capabilities, permission evaluation, and boundary rules.

[Open permissions_and_capabilities.md](./permissions_and_capabilities.md)

---

### 2.3 state_transitions.md

Defines governed state machines, transition rules, and validation logic.

[Open state_transitions.md](./state_transitions.md)

---

### 2.4 evidence_and_audit.md

Defines evidence classification, audit events, lineage, and governance logging.

[Open evidence_and_audit.md](./evidence_and_audit.md)

---

## 3. v1.0 Boundary

The Governance Engine is load-bearing in v1.0.

Included in v1.0:

* permission evaluation;
* capability resolution;
* state transition validation;
* boundary rule enforcement;
* evidence classification;
* audit logging.

Deferred unless ratified:

* rule engine abstraction;
* dynamic governance authoring;
* broad Corpus automation;
* advanced policy editing interfaces;
* AI-assisted governance automation as a product feature.

---

## 4. How to Read This Slice

New developers should read the files in this order:

1. **governance_engine.md** — the conceptual frame.
2. **permissions_and_capabilities.md** — who can do what.
3. **state_transitions.md** — how things move.
4. **evidence_and_audit.md** — how actions are recorded.

This sequence provides a complete first mental model of governance in under an hour.

---

## 5. Summary

The Governance Engine is the constitutional layer of INTEVIA.

It enforces rules, validates transitions, resolves capabilities, classifies evidence, and preserves audit lineage.

This directory defines how governance works.

The v1.0 Boundary decides what must be implemented first.
