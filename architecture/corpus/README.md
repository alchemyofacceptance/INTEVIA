# INTEVIA / architecture/corpus/README.md v0.1

*A guided entry point into the Corpus — the layer that stores, structures, and interprets the organism’s memory.*

---

## 1. Purpose of This Directory

This directory defines the architectural role of the Corpus: INTEVIA’s knowledge, memory, and lineage layer.

The Corpus is not merely a database, not merely a document store, and not a content module.

It is the organism’s structured memory: the place where meaning, evidence, documentation, lineage, and knowledge are preserved, organised, and made interpretable.

This directory does not expose the full INTEVIA Corpus. It defines the public-safe architectural model for how Corpus-like memory and lineage should operate inside the platform.

The Corpus supports governance, CARE, HAT, and all future learning and knowledge-bearing features.

---

## 2. Components of the Corpus

### 2.1 corpus_overview.md

Defines the purpose, scope, and conceptual stance of the Corpus.

[Open corpus_overview.md](./corpus_overview.md)

---

### 2.2 documentation_model.md

Defines how documentation, architectural lineage, and knowledge artefacts are structured, classified, and related.

[Open documentation_model.md](./documentation_model.md)

---

### 2.3 evidence_lineage.md

Defines how evidence, audit, and historical records are linked, traced, and versioned across the organism.

[Open evidence_lineage.md](./evidence_lineage.md)

---

### 2.4 knowledge_structures.md

Defines the semantic structures, taxonomies, and knowledge relationships that allow INTEVIA to organise internal meaning.

[Open knowledge_structures.md](./knowledge_structures.md)

---

## 3. v1.0 Boundary

The Corpus is important to the broader organism, but v1.0 remains intentionally narrow.

Included in v1.0:

* minimal Corpus architecture;
* evidence lineage support for governance;
* documentation lineage for architecture and implementation;
* basic knowledge classification for Events, Services, Library, evidence, and audit;
* public-safe architectural documentation of Corpus behaviour.

Deferred unless ratified:

* full Corpus exposure;
* semantic graph expansion;
* automated knowledge extraction;
* AI-assisted documentation generation as a product feature;
* dynamic lineage visualisation;
* broad learning features;
* full ontology management.

---

## 4. How to Read This Slice

New developers should read the files in this order:

1. **corpus_overview.md** — what the Corpus is.
2. **documentation_model.md** — how documentation and knowledge artefacts are structured.
3. **evidence_lineage.md** — how memory, proof, and lineage are preserved.
4. **knowledge_structures.md** — how meaning is organised.

This sequence provides a complete first mental model of the Corpus in under an hour.

---

## 5. Summary

The Corpus is the organism’s memory and meaning layer.

It preserves lineage, structures knowledge, and supports governance, CARE, HAT, and future learning features.

This directory defines how the organism remembers.

The v1.0 Boundary decides what must be implemented first.
