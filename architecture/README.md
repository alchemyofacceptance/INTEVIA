# INTEVIA Architecture

*A guided entry point into INTEVIA's conceptual architectural spine.*

> **Reading boundary — refreshed 2026-11-01:** These documents describe architecture intent and conceptual scope. They are not a current implementation-status surface. Use the [Current Implementation Crosswalk](../docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md) to distinguish repository presence, test definitions, recorded execution, Human acceptance, unresolved findings, and release state at ref `f829d1b2b714cb282d96a273fbd0887eac9c3522`.

For the current position and its limits, see [the README](../README.md#current-position).

The current development practice is **IDOPT (formerly IDOP)** — INTEVIA Development Operating Protocol and Thinking. Historical records and protocol filenames retain IDOP.

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
- **HAT Collaboration Layer** — intended Human–AI interpretation, sequencing, and reflection boundaries. The operating practice itself is documented at [HAT — The Human-AI Team v0.3](../docs/governance/HAT_THE_HUMAN_AI_TEAM_v0_3.md); the layer documents here describe intent, not the practice.

Architecture language describes intended meaning. Whether a corresponding path exists or has recorded validation must be checked separately.

## v1.0 implementation boundary at the qualified ref

The repository contains paths associated with bounded governed foundations across contribution lineage, Identity, Events, Services, Library, CARE, resource relationships, service activity, profile effect, an Education Course-definition slice, and — since September 2026 — the Living Organism substrate (organism, circle and membership anchors on an append-only recorded chain with a bitemporal fold) and the INTEVIA Lineage-Chain contract laid onto that chain. Those two foundation packets retain their September status in their own records. PKT-B has since added recorded organisation and office actions; its current boundaries are in the [PKT-B Datacron](../docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md).

The crosswalk distinguishes files being present from tests being run and work being accepted. GitHub continuous integration (CI) is now the evidence of record. The smaller suite follows six test admission rules (TAR), uses prepared database copies, and does not run the archived old suite. See [verification](../docs/verification/README.md).

For Education specifically, S014 introduces a bounded governed Course aggregate and version lineage. It does not establish curriculum delivery, class delivery, enrolment, completion, assessment, certification, educator qualification, payment, or a full learning system. `MAT-S014-01` remains visibly deferred in the S014 record at this baseline.

Broader conceptual domains—including Exchange, Locations, marketplace mechanics, integrations, broader Education, Recognition, and HAT productisation—remain architecture intent unless exact current evidence establishes a narrower implemented boundary.

## Reading order

1. Read the System Context, Container Diagram, and Domain Map for conceptual scope.
2. Read the domain README relevant to your question.
3. Use the [Current Implementation Crosswalk](../docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md) before drawing a maturity or feasibility conclusion.
4. Follow linked Slice and Human-issued sources for the evidence class you need.

## Non-claims

Architecture does not establish implementation completeness, current test results, independent reproduction, Human acceptance, deployment, release readiness, certification, or external validation.
