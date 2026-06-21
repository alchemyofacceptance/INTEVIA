# INTEVIA / architecture/events/README.md v0.1

*A guided entry point into the Events Domain — the organism’s lived experience layer.*

---

## 1. Purpose of This Directory

This directory defines the Events Domain: the layer where the organism records, structures, and interprets what happens.

Events are the atomic recordable units of lived experience inside INTEVIA.

Events are not logs, not notifications, and not raw data. They are governed experience objects with state, lineage, evidence, meaning, and transition rules.

This directory defines the public-safe architectural model for Events. It does not expose internal lineage engines, private modelling patterns, or governed practice materials.

---

## 2. Components of the Events Domain

### 2.1 events_overview.md

Defines the purpose, scope, and conceptual stance of Events as the organism’s experience layer.

[Open events_overview.md](./events_overview.md)

---

### 2.2 event_types.md

Defines the categories of Events and their classification boundaries.

[Open event_types.md](./event_types.md)

---

### 2.3 event_lineage.md

Defines how Events carry lineage, evidence, and meaning over time.

[Open event_lineage.md](./event_lineage.md)

---

### 2.4 event_state_transitions.md

Defines how Events move through governed states and what transitions mean.

[Open event_state_transitions.md](./event_state_transitions.md)

---

## 3. v1.0 Boundary

Events are load-bearing in v1.0.

Included in v1.0:

* minimal Event model;
* public-safe Event types;
* event creation and publishing;
* booking or registration;
* attendance or participation tracking;
* governed state transitions;
* evidence attachment points;
* resource links to Library artefacts;
* audit and governance attachment boundaries;
* integration with Governance Engine, Corpus, Library, CARE seed layer, and minimal identity substrate.

Deferred unless ratified:

* advanced lineage engines;
* semantic event graphs;
* automated event interpretation;
* event-driven automation features;
* runtime event orchestration;
* broad notification systems;
* full Locations domain;
* payment or marketplace behaviour.

For v1.0, Events are governed experience objects, not a full event-processing system.

---

## 4. How to Read This Slice

New developers should read the files in this order:

1. **events_overview.md** — what Events are.
2. **event_types.md** — how Events are classified.
3. **event_lineage.md** — how Events carry evidence and meaning.
4. **event_state_transitions.md** — how Events move.

This sequence provides a complete first mental model of Events in under an hour.

---

## 5. Summary

The Events Domain is the organism’s lived experience layer.

It defines what happens, how it is classified, how it carries evidence and lineage, and how it moves through governed states.

The v1.0 Boundary decides what must be implemented first.
