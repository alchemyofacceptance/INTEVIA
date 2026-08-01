# INTEVIA / architecture/education/README.md v0.1

*A conceptual entry point into the Education Domain — INTEVIA's intended capability-formation substrate.*

> **Conceptual/current boundary — qualified 2026-07-31:** This v0.1 document describes broader Education architecture intent. At repository ref `1aed0f4da88209d5298e40867b08505661cfd451`, the bounded implemented foundation is Course definition and immutable version lineage only. See the [Current Implementation Crosswalk](../../docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md). Architecture intent is not implementation status.

## 1. Purpose of this directory

This directory describes the intended Education Domain: a governed capability-formation layer that may eventually connect curriculum, courses, classes, learning activity, evidence, assessment, and certification boundaries.

The documents are public-safe conceptual architecture. They do not expose private pedagogy engines, assessment doctrine, certification doctrine, or governed practice materials, and their presence does not establish that the concepts are implemented.

## 2. Conceptual documents

- [`education_overview.md`](education_overview.md) — intended purpose, scope, and conceptual stance.
- [`education_types.md`](education_types.md) — conceptual categories and classification boundaries.
- [`curriculum_records.md`](curriculum_records.md) — intended curriculum-record structure and governance.
- [`course_records.md`](course_records.md) — intended Course-record structure and lineage.
- [`class_events.md`](class_events.md) — intended class-as-Event model.
- [`education_state_transitions.md`](education_state_transitions.md) — intended transitions for Education artefacts.

## 3. Qualified bounded implementation

S014 adds repository paths for a bounded governed Course-definition foundation in which an explicitly authorised active Human identity can create a stable draft Course with version 1, append an ordered immutable successor version, and—when the creator—read current, exact-version, and complete-lineage projections under the Slice's stated constraints.

Exact route: [`D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md`](../../docs/holocron/datacrons/D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md).

The S014 record at this baseline states that it was pending direct Human implementation acceptance, Datacron acceptance, and closure, and keeps `MAT-S014-01` deferred to IDOP v0.9.6.

## 4. Explicit exclusions

The qualified repository state does **not** establish:

- curriculum delivery;
- class delivery or attendance as Education capability;
- learner enrolment;
- learning activity delivery;
- completion or learning-outcome evidence;
- assessment;
- certification or credentials;
- educator qualification;
- cohort orchestration;
- adaptive learning or analytics;
- payment, marketplace, or Exchange integration;
- publication, approval, fitness, quality, or operational Course status; or
- a complete LMS or training system.

Those concepts may remain in v0.1 architecture documents as intended scope. They must not be read as current implementation claims.

## 5. Reading order

1. Read [`education_overview.md`](education_overview.md) for conceptual purpose.
2. Read [`course_records.md`](course_records.md) for the closest conceptual neighbour to S014.
3. Read the [Current Implementation Crosswalk](../../docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md) for atomic evidence states.
4. Read the S014 Datacron for its exact technical and negative boundaries.

## 6. Summary

Education remains a broader conceptual capability-formation domain. The current repository contains a bounded Course-definition foundation, not a full Education system.
