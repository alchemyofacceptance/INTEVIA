# Sprint 1 Manifest
*(Interim visibility artefact for Sprint 1 surfaces, evidence, rails, and lineage)*

## 1. Status of This Manifest

This manifest is a static visibility artefact.

It makes current Sprint 1 runtime surfaces, governance surfaces, command surfaces, evidence artefacts, governance rails, and key commit lineage easier to find.

It introduces no runtime behaviour.
It introduces no routing.
It introduces no inference.
It introduces no hidden state.
It introduces no dependency expansion.

It is not final doctrine.
It is not a maturity claim.
It is not an architectural completion claim.
It is not a general proof of OPC validity.

## 2. Runtime / Core Surfaces

- `breathe` — first observable organism breath.
  - Location: `src/intevia/app.py`

## 3. Governance Surfaces

- `current_status` — governed Sprint 1 status as data.
  - Location: `src/intevia/governance/status.py`
- `format_status` — Human-readable governance status output.
  - Location: `src/intevia/governance/status.py`

## 4. Command Surfaces

- `heartbeat` — named governed call returning breath and governed condition.
  - Location: `src/intevia/commands/heartbeat.py`
  - Invocation: `python -m src.intevia.commands.heartbeat`
- `inspect` — bounded visibility surface reporting visible rails and evidence artefacts.
  - Location: `src/intevia/commands/inspect.py`
  - Invocation: `python -m src.intevia.commands.inspect`

## 5. Verification Surfaces

- `tests/test_first_breath.py`
- `tests/test_heartbeat.py`
- `tests/test_inspect.py`

Validation command:

`python -m unittest discover -s tests -p "test_*.py"`

## 6. Evidence Artefacts

- `docs/evidence/sprints/sprint-1/SPRINT_1_EVIDENCE_LOG.md`
- `docs/evidence/sprints/sprint-1/SPRINT_1_CONSTITUTIONAL_CHECKPOINT.md`
- `docs/evidence/sprints/sprint-1/SPRINT_1_OPC_BOUNDED_DOCTRINE_NOTE.md`
- `docs/evidence/sprints/sprint-1/WORK_BLOCK_7_BOUNDARY_CHARTER.md`

## 7. Governance Rails

- Human authority remains final.
- Echo precedes interpretation.
- Evidence precedes expansion.
- Git preserves lineage.
- No hidden state.
- No autonomous recovery.
- No command surface without paired verification.
- No expansion faster than evidence.
- Breakers are evidence, not failure.
- Ordinary life is part of the observed Sprint 1 operating environment.

## 8. Key Sprint 1 Commit Lineage

- `0adf46b` — initialise Sprint 1 evidence log
- `f8cf35f` — establish initial organism skeleton
- `bfcfe14` — add governance status surface
- `3938bc7` — format governance status output
- `9513efc` — add first breath verification
- `40a3908` — add heartbeat command surface
- `b373172` — add bounded Sprint 1 inspect command surface
- `508dfa8` — add Work Block 7 boundary charter
- `7699815` — add Sprint 1 constitutional checkpoint
- `e18a38c` — add Sprint 1 bounded OPC doctrine note
- `6523b3b` — record Work Block 7 inspect command evidence

## 9. Boundary Note

This manifest lists visible Sprint 1 artefacts.

It does not discover artefacts.
It does not scan the repository.
It does not infer state.
It does not create behaviour.
It does not replace the evidence log, constitutional checkpoint, bounded OPC doctrine note, or boundary charter.

It exists to make the current evidence easier to find.

## 10. Keeper

> After visibility, manifest.
> Before more action, make the evidence easy to find.
