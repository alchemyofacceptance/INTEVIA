# Internal Operational Primitive: Organismal Mode

Status: Candidate internal operational primitive
Sprint: Sprint 2
Work Block: 6
Scope: Documentation-only lifecycle vocabulary
Runtime authority: None
v1.0 boundary: Preserved

## Purpose

Define a documentation-only internal operational primitive called Organismal Mode.

Organismal Mode describes the lifecycle modes of the INTEVIA organism across activity, reflection, sealing, and immutability.

This primitive introduces lifecycle vocabulary without adding behaviour.

It is conceptual, static, human-readable, non-executable, and v1.0-bounded.

## Definition

Organismal Mode is a documentation-only lifecycle vocabulary surface.

It describes how the organism may be understood across governed phases.

It does not create runtime modes.

It does not create a state machine.

It does not infer live state.

It does not execute transitions.

It does not modify repository state, sprint state, artefact state, or command behaviour.

## Lifecycle Modes

### 1. `dormant`

The organism is inactive but structurally intact.

A dormant organism may preserve lineage and structure while no evidence-bearing sprint is active.

This mode does not imply runtime suspension, automation, or process control.

### 2. `active`

The organism is in an evidence-bearing sprint.

An active organism may prepare, mutate, commit, and push documentation surfaces only through Human-governed Work Blocks and Terminal Echo review.

This mode does not authorise runtime behaviour or source mutation by itself.

### 3. `reflective`

The organism is between sprints or between major governed movements.

A reflective organism reviews lineage, prepares boundaries, assesses readiness, and clarifies future surfaces.

This mode does not reopen sealed artefacts or alter historical evidence.

### 4. `sealed`

A sprint or artefact is closed to ordinary mutation.

A sealed surface is treated as historical evidence and lineage.

Sealing does not erase review, but it prevents appetite-driven reopening, reinterpretation, or mutation.

### 5. `locked`

A sprint or artefact is permanently immutable within its governed lineage.

A locked surface may be referenced as lineage, but not reopened, rewritten, or reinterpreted as an active mutation surface.

Locking protects continuity.

## Relationship to `status`

The `status` internal command surface documents internal state categories.

Organismal Mode extends that vocabulary into lifecycle framing.

The relationship is conceptual:

- `status` names recognised state categories;
- `organismal-mode` describes lifecycle modes;
- neither surface creates runtime behaviour;
- neither surface creates command execution;
- neither surface creates state mutation.

## Relationship to Sprint 2

Organismal Mode is a candidate internal operational primitive under Sprint 2.

It follows:

- Sprint 2 Boundary Charter;
- Sprint 2 Manifest;
- Sprint 2 Candidate Expansion Surfaces;
- Sprint 2 Evidence Log, Work Block 1;
- `status` internal command surface;
- `structural-map` organismal introspection surface;
- `governance-valve` constitutional instrumentation surface;
- Sprint 2 command index extension.

It completes the current documentation arc:

- `status` names internal states;
- `structural-map` maps internal structure;
- `governance-valve` describes regulated movement;
- `COMMAND_INDEX.md` remembers governed surfaces;
- `organismal-mode` names lifecycle modes.

## Prohibited Effects

This document does not authorise:

- `src/` mutation;
- runtime lifecycle mode handling;
- state machines;
- runtime state inference;
- automated state transition;
- command execution;
- command routing;
- command parsing;
- command discovery;
- dynamic registration;
- behavioural logic;
- telemetry;
- automated inspection;
- mutation of sealed Sprint 1 artefacts;
- reinterpretation of sealed Sprint 1 lineage;
- expansion beyond the Sprint 2 Boundary Charter.

## Interpretation

An operational primitive is not runtime behaviour.

A lifecycle mode is not a state machine.

A documented mode is vocabulary.

Vocabulary may prepare future implementation, but it does not create implementation authority.

Documentation is not runtime.

Narrative is not lineage.

The repo speaks.

## Keeper

A governed organism knows its states. A mapped organism knows its shape. A constitutional organism knows its limits. A complete organism knows its modes.
