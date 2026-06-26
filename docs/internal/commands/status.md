# Internal Command Surface: `status`

Status: Candidate internal command surface
Sprint: Sprint 2
Work Block: 2
Scope: Documentation-only internal command vocabulary
Runtime authority: None
v1.0 boundary: Preserved

## Purpose

Define a new bounded internal command surface, `status`, that documents the organism's internal state categories in a static, non-behavioural, v1.0-bounded form.

This surface extends the internal command family established in Sprint 1 through `heartbeat` and `inspect`, but it does not introduce runtime behaviour.

## Definition

`status` is a documentation-only internal command surface.

It describes recognised internal state categories that may support future introspection, governance, and instrumentation.

These categories are conceptual vocabulary. They do not produce runtime output, modify state, trigger behaviour, register a command, alter routing, alter parsing, or create command discovery.

## Internal State Categories

The `status` surface documents the following internal state categories:

- `dormant` — the organism is inactive but structurally intact.
- `active` — the organism is in an evidence-bearing sprint.
- `reflective` — the organism is between sprints, reviewing lineage.
- `sealed` — a sprint or artefact is closed to mutation.
- `locked` — a sprint or artefact is permanently immutable within its governed lineage.

## Characteristics

This surface is:

- static;
- human-readable;
- documentation-only;
- non-executable;
- non-behavioural;
- strictly v1.0-bounded;
- subject to later Human-selected implementation authority before any runtime use.

## Prohibited Effects

This document does not authorise:

- `src/` mutation;
- runtime command behaviour;
- command execution;
- command routing;
- command parsing;
- command discovery;
- dynamic registration;
- behavioural logic;
- state mutation;
- reinterpretation of sealed Sprint 1 artefacts;
- expansion beyond the Sprint 2 Boundary Charter.

## Relationship to Sprint 2

This surface is a candidate expansion surface under Sprint 2.

It follows:

- Sprint 2 Boundary Charter;
- Sprint 2 Manifest;
- Sprint 2 Candidate Expansion Surfaces;
- Sprint 2 Evidence Log, Work Block 1.

It remains documentation-only unless a later Human-selected Work Block explicitly authorises implementation.

## Interpretation

A new command surface is not behaviour.

A documented command surface is vocabulary.

Vocabulary may prepare future implementation, but it does not create implementation authority.

Narrative is not lineage.

The repo speaks.

## Keeper

A new command is not behaviour — it is vocabulary. The organism grows by naming what it can become.
