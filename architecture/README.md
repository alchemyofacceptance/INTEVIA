# INTEVIA Architecture

*A guided entry point into INTEVIA's conceptual architectural spine.*

> **Reading boundary — qualified 2026-07-31:** These documents describe architecture intent and conceptual scope. They are not a current implementation-status surface. Use the [Current Implementation Crosswalk](../docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md) to distinguish repository presence, test definitions, recorded execution, Human acceptance, unresolved findings, and release state at ref `1aed0f4da88209d5298e40867b08505661cfd451`.

## Purpose of this directory

The architecture directory describes:

- what the wider INTEVIA organism is intended to be;
- what its runtime containers may become; and
- how its conceptual domains relate.

Conceptual issuance labels such as `v0.1` remain truthful to the documents' formation stage. They should not be interpreted as the software version, implementation completeness, or release maturity.

## Primary views

1. [System Context v0.1](system_context.md) — the organism's outer boundary and external relationships.
2. [Container Diagram v0.1](container_diagram.md) — conceptual runtime containers and governed interactions.
3. [Domain Map v0.1](domain_map.md) — conceptual domains, responsibilities, and relationships.

## Cross-cutting governance layers

- **Governance Engine** — intended rules, permissions, transitions, evidence, and audit.
- **Corpus** — intended knowledge, lineage, and documentation structures.
- **HAT Collaboration Layer** — intended Human–AI interpretation, sequencing, and reflection boundaries.

Architecture language describes intended meaning. Whether a corresponding path exists or has recorded validation must be checked separately.

## v1.0 implementation boundary at the qualified ref

The repository contains paths associated with bounded governed foundations across contribution lineage, Identity, Events, Services, Library, CARE, resource relationships, service activity, profile effect, and an Education Course-definition slice.

That statement establishes path presence only. The crosswalk routes to the exact evidence classes and non-claims.

For Education specifically, S014 introduces a bounded governed Course aggregate and version lineage. It does not establish curriculum delivery, class delivery, enrolment, completion, assessment, certification, educator qualification, payment, or a full learning system. `MAT-S014-01` remains visibly deferred in the S014 record at this baseline.

Broader conceptual domains—including Exchange, Applications, Locations, marketplace mechanics, integrations, broader Education, Recognition, and HAT productisation—remain architecture intent unless exact current evidence establishes a narrower implemented boundary.

## Reading order

1. Read the System Context, Container Diagram, and Domain Map for conceptual scope.
2. Read the domain README relevant to your question.
3. Use the [Current Implementation Crosswalk](../docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md) before drawing a maturity or feasibility conclusion.
4. Follow linked Slice and Human-issued sources for the evidence class you need.

## Non-claims

Architecture does not establish implementation completeness, current test results, independent reproduction, Human acceptance, deployment, release readiness, certification, or external validation.
