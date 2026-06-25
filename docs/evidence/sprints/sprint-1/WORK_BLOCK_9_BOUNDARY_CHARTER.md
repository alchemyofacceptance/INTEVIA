# Work Block 9 Boundary Charter

**Status:** Prepared boundary charter for Sprint 1  
**Scope:** Static command-index candidate only  
**Mutation type:** Evidence / documentation surface only  
**Runtime impact:** None

## 1. Purpose

Work Block 9 may prepare a candidate definition for a static command index.

The purpose is to make Sprint 1 command invocation clearer without adding runtime behaviour, routing, inference, dynamic scanning, auto-discovery, or hidden state.

This charter exists to preserve the Sprint 1 rhythm:

> The next surface should not make INTEVIA stronger before it makes INTEVIA clearer.

## 2. Authorised Candidate Surface

If later authorised, Work Block 9 may prepare only:

`docs/evidence/sprints/sprint-1/WORK_BLOCK_9_CANDIDATE_COMMAND_INDEX.md`

This candidate may define:

- the idea of a static command index;
- the current governed command invocations;
- what such an index would contain;
- what boundaries it must obey;
- why it is being considered;
- how it fits the observed Sprint 1 visibility arc so far.

## 3. Observed Sprint 1 Visibility Arc So Far

Sprint 1 has so far produced the following public visibility sequence:

`heartbeat -> inspect -> manifest -> evidence receipt`

Evidence-safe formulation:

- INTEVIA answered.
- INTEVIA exposed its rails.
- INTEVIA consolidated its surfaces.
- INTEVIA recorded the consolidation.

This arc supports considering a static command-index candidate, but does not authorise implementation.

## 4. What Work Block 9 May Do

Work Block 9 may:

- define a candidate static command-index artefact;
- list current command invocations such as `heartbeat` and `inspect`;
- describe the candidate purpose;
- define boundaries and non-claims;
- explain why visibility should precede capability;
- remain fully Sprint 1-bounded.

## 5. What Work Block 9 Must Not Do

Work Block 9 must not introduce:

- `src` changes;
- new command modules;
- new tests;
- runtime behaviour;
- routing;
- dynamic scanning;
- auto-discovery;
- runtime help behaviour;
- inference;
- hidden state;
- dependency changes;
- capability expansion;
- architectural claims;
- maturity claims;
- general OPC claims.

## 6. Breaker Conditions

The work must halt if any proposed mutation would:

- change runtime files;
- add a command surface;
- imply automatic command discovery;
- imply autonomous help generation;
- scan the repository;
- infer hidden state;
- expand capability before evidence;
- modify files outside the authorised evidence/documentation scope.

## 7. Evidence Boundaries

This charter does not claim that INTEVIA has a complete help system.

It does not claim that command indexing is required.

It does not claim maturity, architectural completion, or general OPC validity.

It only establishes boundaries for preparing a static candidate artefact.

## 8. Expected Completion Condition

Work Block 9 boundary charter may be considered complete only when:

- this charter is created;
- content is echoed and inspected;
- mutation scope is limited to this file;
- commit is created;
- push is confirmed;
- clean working tree is verified.

## 9. Keeper

> The next surface should not make INTEVIA stronger before it makes INTEVIA clearer.
