# INTEVIA / architecture/education/README.md v0.1

*A guided entry point into the Education Domain — the organism’s capability-formation substrate.*

---

## 1. Purpose of This Directory

This directory defines the Education Domain: the layer that governs curriculum, courses, classes, learning activities, capability formation, and evidence-bearing learning outcomes.

Education in INTEVIA is not a content library, not a CMS, and not a training portal. It is a governed capability-formation substrate built on Identity, Events, Services, Library, Governance Engine, Corpus, and CARE.

This directory defines the public-safe architectural model for Education. It does not expose internal pedagogy engines, private modelling patterns, assessment doctrine, certification doctrine, or governed practice materials.

---

## 2. Components of the Education Domain

### 2.1 education_overview.md

Defines the purpose, scope, and conceptual stance of Education as the organism’s capability-formation substrate.

[Open education_overview.md](./education_overview.md)

---

### 2.2 education_types.md

Defines the categories of educational artefacts, including curriculum, course, class, assessment, and certification, and their classification boundaries.

[Open education_types.md](./education_types.md)

---

### 2.3 curriculum_records.md

Defines how curriculum records are structured, versioned, linked, evidenced, and governed.

[Open curriculum_records.md](./curriculum_records.md)

---

### 2.4 course_records.md

Defines how course records are structured, versioned, linked to curriculum, evidenced, and governed.

[Open course_records.md](./course_records.md)

---

### 2.5 class_events.md

Defines how classes are represented as governed Events, including attendance, delivery, evidence, lineage, and learning outcome boundaries.

[Open class_events.md](./class_events.md)

---

### 2.6 education_state_transitions.md

Defines how curriculum, courses, classes, and learning outcomes move through governed states.

[Open education_state_transitions.md](./education_state_transitions.md)

---

## 3. v1.0 Boundary

Education is testable in v1.0, but only as a minimal governed capability-formation substrate.

Included in v1.0:

* curriculum record definition;
* course record definition;
* class-as-Event definition;
* learner identity linkage;
* educator identity linkage;
* learning activity Service linkage;
* completion evidence;
* learning outcome evidence boundaries;
* governed transitions for curriculum, course, and class;
* Library resource linkage;
* evidence and audit attachment points;
* integration with Identity, Events, Services, Library, Governance Engine, Corpus, and CARE seed layer.

Deferred unless ratified:

* full LMS behaviour;
* automated assessment engines;
* semantic curriculum graphs;
* certification engines;
* credential marketplaces;
* marketplace education;
* payment-linked education;
* full Exchange integration;
* Education 2.0 automation;
* cohort orchestration engines;
* adaptive learning engines;
* broad learner analytics.

For v1.0, Education is a minimal governed capability-formation substrate, not a full learning system.

---

## 4. How to Read This Slice

New developers should read the files in this order:

1. **education_overview.md** — what Education is.
2. **education_types.md** — how educational artefacts are classified.
3. **curriculum_records.md** — how curriculum is structured and governed.
4. **course_records.md** — how courses are structured and governed.
5. **class_events.md** — how classes operate as governed Events.
6. **education_state_transitions.md** — how curriculum, courses, classes, and learning outcomes move.

This sequence provides a complete first mental model of Education in under an hour.

---

## 5. Summary

The Education Domain is the organism’s capability-formation substrate.

It defines how curriculum, courses, and classes are structured, linked, delivered, evidenced, and governed — and how learners and educators interact with these artefacts through governed Identity, Events, Services, and Library resources.

The v1.0 Boundary decides what must be implemented first.
