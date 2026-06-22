# Continuity Matrix v0.1

## Purpose

This file defines the interaction matrix for Continuity Engine v0.1 organs.

Each cell indicates whether two organs interact directly, indirectly, or minimally.

Continuity Matrix answers:

> Which continuity organs interact with each other, and how strongly?

This matrix is structural.

It is not prescriptive.

It does not create authority, dependency, or implementation priority by itself.

---

## Legend

* **●** — direct interaction
* **○** — indirect interaction
* **–** — minimal or no interaction

---

## Organ Abbreviations

| Abbreviation | Organ                                                                        |
| ------------ | ---------------------------------------------------------------------------- |
| Purp         | Purpose                                                                      |
| Bnd          | Boundaries                                                                   |
| BndA         | Advanced Boundaries                                                          |
| Evid         | Evidence                                                                     |
| Rec          | Records                                                                      |
| Mark         | Markers                                                                      |
| Patt         | Patterns                                                                     |
| Thrd         | Threads                                                                      |
| Tran         | State Transitions                                                            |
| ReEn         | Re-entry                                                                     |
| Dec          | Decisions                                                                    |
| Corr         | Corrections                                                                  |
| Sup          | Supersession                                                                 |
| Arch         | Archival                                                                     |
| Lin          | Lineage                                                                      |
| Id           | Identity                                                                     |
| Rol          | Roles                                                                        |
| Risk         | Risks                                                                        |
| Pub          | Public Surface                                                               |
| Gov          | Governance — future / optional unless created and governed in the repository |

---

## Matrix

| Organ ↓ / Organ → | Purp | Bnd | BndA | Evid | Rec | Mark | Patt | Thrd | Tran | ReEn | Dec | Corr | Sup | Arch | Lin | Id | Rol | Risk | Pub | Gov |
| ----------------- | ---- | --- | ---- | ---- | --- | ---- | ---- | ---- | ---- | ---- | --- | ---- | --- | ---- | --- | -- | --- | ---- | --- | --- |
| Purpose           | ●    | ●   | ○    | ○    | –   | –    | –    | –    | –    | –    | ○   | ○    | ○   | ○    | ○   | ○  | ○   | ○    | ○   | ○   |
| Boundaries        | ●    | ●   | ●    | ●    | ●   | ●    | ○    | ○    | ○    | ○    | ●   | ●    | ●   | ●    | ●   | ●  | ●   | ●    | ●   | ●   |
| Boundaries Adv    | ○    | ●   | ●    | ●    | ●   | ●    | ○    | ○    | ○    | ○    | ●   | ●    | ●   | ●    | ●   | ●  | ●   | ●    | ●   | ●   |
| Evidence          | ○    | ●   | ●    | ●    | ●   | ●    | ○    | ○    | ○    | ○    | ●   | ●    | ●   | ●    | ●   | ○  | ○   | ○    | ●   | ●   |
| Records           | –    | ●   | ●    | ●    | ●   | ●    | –    | –    | –    | –    | ○   | ○    | ○   | ●    | ●   | –  | –   | –    | ○   | ○   |
| Markers           | –    | ●   | ●    | ●    | ●   | ●    | ○    | ○    | ●    | ●    | ○   | ●    | ○   | ○    | ●   | ○  | ○   | ○    | ○   | ○   |
| Patterns          | –    | ○   | ○    | ○    | –   | ○    | ●    | ●    | ●    | ○    | –   | –    | –   | –    | ○   | –  | –   | –    | –   | –   |
| Threads           | –    | ○   | ○    | ○    | –   | ○    | ●    | ●    | ●    | ●    | –   | ○    | ○   | ○    | ●   | –  | –   | ○    | –   | –   |
| State Transitions | –    | ○   | ○    | ○    | –   | ●    | ●    | ●    | ●    | ●    | ○   | ○    | ○   | ○    | ●   | –  | –   | ○    | –   | –   |
| Re-entry          | –    | ○   | ○    | ○    | –   | ●    | ○    | ●    | ●    | ●    | ○   | ○    | ○   | ○    | ○   | –  | –   | ○    | –   | –   |
| Decisions         | ○    | ●   | ●    | ●    | ○   | ○    | –    | –    | ○    | ○    | ●   | ●    | ●   | ●    | ○   | ○  | ○   | ○    | ●   | ●   |
| Corrections       | ○    | ●   | ●    | ●    | ○   | ●    | –    | ○    | ○    | ○    | ●   | ●    | ●   | ●    | ○   | ○  | ○   | ○    | ●   | ●   |
| Supersession      | ○    | ●   | ●    | ●    | ○   | ○    | –    | ○    | ○    | ○    | ●   | ●    | ●   | ●    | ○   | ○  | ○   | ○    | ●   | ●   |
| Archival          | ○    | ●   | ●    | ●    | ●   | ○    | –    | ○    | ○    | ○    | ●   | ●    | ●   | ●    | ●   | ○  | ○   | ○    | ○   | ○   |
| Lineage           | ○    | ●   | ●    | ●    | ●   | ●    | ○    | ●    | ●    | ○    | ○   | ○    | ○   | ●    | ●   | ○  | ○   | ○    | ○   | ○   |
| Identity          | ○    | ●   | ●    | ○    | –   | ○    | –    | –    | –    | –    | ○   | ○    | ○   | ○    | ○   | ●  | ●   | ○    | ○   | ●   |
| Roles             | ○    | ●   | ●    | ○    | –   | ○    | –    | –    | –    | –    | ○   | ○    | ○   | ○    | ○   | ●  | ●   | ○    | ○   | ●   |
| Risks             | ○    | ●   | ●    | ○    | –   | ○    | –    | ○    | ○    | ○    | ○   | ○    | ○   | ○    | ○   | ○  | ○   | ●    | ○   | ○   |
| Public Surface    | ○    | ●   | ●    | ●    | ○   | ○    | –    | –    | –    | –    | ●   | ●    | ●   | ○    | ○   | ○  | ○   | ○    | ●   | ●   |
| Governance        | ○    | ●   | ●    | ●    | ○   | ○    | –    | –    | –    | –    | ●   | ●    | ●   | ○    | ○   | ●  | ●   | ○    | ●   | ●   |

---

## How to Read the Matrix

This matrix shows conceptual interaction strength between continuity organs.

A direct interaction means two organs commonly depend on, constrain, substantiate, or shape each other.

An indirect interaction means two organs may relate through another organ or through governed context.

A minimal interaction means the relationship is weak, occasional, or not central to v0.1.

Interaction strength does not imply:

* governance authority;
* implementation priority;
* maturity level;
* dependency order;
* data flow;
* automation;
* hidden state;
* mandatory coupling.

---

## Matrix Interpretation Notes

### Boundaries

Boundaries interact directly with nearly all organs because every continuity object must remain Human-governed, evidence-linked, non-surveillant, and protected from false continuity.

### Evidence

Evidence interacts directly with records, markers, decisions, corrections, supersession, archival, lineage, public surface, and governance because continuity claims must remain substantiated.

### Movement Organs

Patterns, Threads, State Transitions, and Re-entry interact strongly with each other because they define how continuity moves through interruption, parallelism, pause, correction, and return.

### Governance & Change Organs

Decisions, Corrections, Supersession, and Archival interact strongly because continuity changes through governed choice, repair, replacement, and closure.

### Identity & Roles

Identity and Roles interact directly with each other and with Governance because Human-AI boundaries, responsibility, authority, and participation must remain clear.

### Public Surface

Public Surface interacts directly with Evidence, Decisions, Corrections, Supersession, and Governance because external signalling must remain aligned with actual work and governed boundaries.

### Governance

`continuity_governance.md` is future / optional unless created and governed in the repository.

Where included in this matrix, Governance represents the conceptual governance layer already expressed through Purpose, Boundaries, Decisions, Corrections, Identity, Roles, and Public Surface.

---

## v0.1 Status

This is a placeholder architecture file.

Full content will be expanded in v0.2.

---

## v0.2 Expansion Targets

The next version should define:

* validated interaction matrix;
* active versus future organs;
* required versus optional organs;
* direct interaction definitions;
* indirect interaction definitions;
* matrix interpretation rules;
* matrix update rules;
* interaction evidence requirements;
* matrix-to-crosslinks relationship;
* matrix-to-map relationship;
* matrix-to-taxonomy relationship;
* matrix-to-index relationship;
* public-safe versus governed/internal matrix boundaries;
* v1.0 implementation constraints.

---

## Boundary Note

This matrix is structural, not prescriptive.

Interactions must remain governed by individual organ boundaries.

A matrix cell does not create authority.

A matrix cell does not prove evidence.

A matrix cell only indicates conceptual relationship strength within the v0.1 Continuity Engine architecture.

Governed meaning resides in the individual continuity organs.
