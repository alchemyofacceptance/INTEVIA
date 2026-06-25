# Work Block 9 — Candidate: Static Command Index

**Status:** Candidate definition only  
**Scope:** Sprint 1 command invocation visibility  
**Runtime impact:** None  
**Implementation status:** Not authorised

## 1. Purpose

This candidate defines a possible static command index for Sprint 1.

The purpose is to make the current governed command invocations easier to find and understand without adding runtime behaviour, routing, dynamic scanning, auto-discovery, inference, hidden state, or capability expansion.

This candidate follows the Sprint 1 keeper:

> The next surface should not make INTEVIA stronger before it makes INTEVIA clearer.

## 2. Candidate Command Invocations

Current governed command surfaces:

- `heartbeat`
  - Invocation: `python -m src.intevia.commands.heartbeat`
  - Purpose: returns breath and governed Sprint 1 condition.

- `inspect`
  - Invocation: `python -m src.intevia.commands.inspect`
  - Purpose: reports visible Sprint 1 command surfaces, governance surfaces, evidence artefacts, and boundary note.

## 3. Evidence Basis

This candidate follows the observed Sprint 1 visibility arc so far:

`heartbeat -> inspect -> manifest -> evidence receipt -> boundary charter`

Evidence-safe formulation:

- INTEVIA answered.
- INTEVIA exposed its rails.
- INTEVIA consolidated its surfaces.
- INTEVIA recorded the consolidation.
- INTEVIA defined the boundary before considering the next visibility surface.

## 4. Candidate Boundaries

A static command index, if later implemented, must remain:

- static;
- explicit;
- human-readable;
- Sprint 1-bounded;
- evidence-aligned;
- non-inferential;
- reversible.

It must not introduce:

- new runtime behaviour;
- routing;
- dynamic scanning;
- auto-discovery;
- runtime help behaviour;
- hidden state;
- dependency changes;
- capability expansion;
- architectural claims;
- maturity claims;
- general OPC claims.

## 5. Non-Claims

This candidate does not claim that INTEVIA has a complete help system.

It does not claim that command indexing is required.

It does not authorise implementation.

It does not authorise a new command module.

It does not expand runtime capability.

It only defines the candidate surface for Human review.

## 6. Keeper

> The next surface should not make INTEVIA stronger before it makes INTEVIA clearer.
