# INTEVIA — Static Command Index (Sprint 1)

**Status:** Internal, static, v1.0  
**Source candidate:** `docs/evidence/sprints/sprint-1/WORK_BLOCK_9_CANDIDATE_COMMAND_INDEX.md`  
**Runtime impact:** None  
**Behaviour:** None  
**Routing:** None  
**Dynamic scanning:** None  
**Auto-discovery:** None  
**CLI expansion:** None  

## Current Governed Command Invocations

### `heartbeat`

Invocation:

`python -m src.intevia.commands.heartbeat`

Purpose:

Returns the organism first-breath signal and governed Sprint 1 condition.

### `inspect`

Invocation:

`python -m src.intevia.commands.inspect`

Purpose:

Reports visible Sprint 1 command surfaces, governance surfaces, evidence artefacts, and boundary note.

## Boundary

This index is static and internal only.

It does not introduce runtime behaviour, routing, dynamic scanning, auto-discovery, runtime help behaviour, hidden state, dependency changes, capability expansion, architectural claims, maturity claims, or general OPC claims.

It implements the Work Block 9 candidate as documentation only.

## Sprint 2 Static Surface Extensions

Status: Static documentation extension
Sprint: Sprint 2
Work Block: 5
Runtime authority: None
v1.0 boundary: Preserved

### Purpose

Record the Sprint 2 documentation-only internal surfaces in the static internal command index.

This extension keeps the command index human-readable, static, non-executable, and aligned with the Sprint 2 Boundary Charter, Sprint 2 Manifest, Sprint 2 Candidate Expansion Surfaces, and Sprint 2 Evidence Log.

### Sprint 2 Surfaces

| Surface | Type | Path | Status | Runtime authority |
|---|---|---|---|---|
| `status` | Internal command surface | `docs/internal/commands/status.md` | Candidate documentation surface | None |
| `structural-map` | Organismal introspection surface | `docs/internal/introspection/structural-map.md` | Candidate documentation surface | None |
| `governance-valve` | Constitutional instrumentation surface | `docs/internal/instrumentation/governance-valve.md` | Candidate documentation surface | None |

### Boundary Conditions

This index extension does not authorise:

- `src/` mutation;
- runtime command behaviour;
- command execution;
- command routing;
- command parsing;
- command discovery;
- dynamic registration;
- behavioural logic;
- telemetry;
- automated inspection;
- reinterpretation of sealed Sprint 1 artefacts.

### Interpretation

The command index remembers surfaces. It does not activate them.

Documentation is not runtime.

Candidate surfaces are not implementation authority.

Narrative is not lineage.

The repo speaks.

### Keeper

> Expansion creates surfaces. Governance shapes them. The index remembers them.
