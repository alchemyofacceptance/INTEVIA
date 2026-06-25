# INTEVIA Sprint 1 Evidence Log

Start: 2026-06-25 06:30 BST  
End: 2026-07-02 06:30 BST  
Operating frame: Build INTEVIA. Instrument INTEVIA.

## Sprint Mandate

Build INTEVIA.  
Instrument INTEVIA.  
Let the work speak for itself.

## Governing Principle

Machine precision without machine authority.

The AI nodes may structure, type, sequence, check, and scaffold.  
The Human validates, executes, ratifies, rejects, and owns the outcome.

## Evidence Buckets

### External Verification

Receipt format:

> Timestamp → Artefact → What the AI proposed → What external verification revealed → Final decision

### Bounded Memory

Receipt format:

> Artefact → What continuity it preserved → Why model memory alone would have drifted

### Breakers

Receipt format:

> Trigger → Breaker behaviour → What risk it prevented → Human decision

### Auditability

Receipt format:

> Artefact → What it allowed reconstruction of → Why that matters for governance

### Human Comprehension

Receipt format:

> Work item → Explanation → Evidence → Why it should stand

## Daily Sprint Notes

### Day 1 — 2026-06-25

Day 1 build objective: Establish a minimum runnable INTEVIA organism surface that breathes, reports governed context, and can be externally verified.

#### Study Events

- 2026-06-25 06:30 BST — Sprint 1 evidence surface created at `docs/evidence/sprints/sprint-1/SPRINT_1_EVIDENCE_LOG.md` and committed as `0adf46b chore: initialise Sprint 1 evidence log`. This moved Sprint 1 continuity from chat into repo artefact and established bounded memory / auditability.

- 2026-06-25 06:26 BST — First runnable INTEVIA organism skeleton created under `src/intevia/`, with `run.py` calling `breathe()`. Human verified `python run.py` returned `INTEVIA organism initialised`. Python generated `__pycache__` artefacts during execution; these were identified as non-source runtime artefacts and excluded from commit. This established the first observable execution surface for Sprint 1.

- 2026-06-25 06:3X BST — Governance status surface added at `src/intevia/governance/status.py`. Human verified `current_status()` returned sprint context, Human authority, and operating frame. This introduced governance as inspectable data rather than hidden behaviour.

- 2026-06-25 06:3X BST — Human-readable governance output added through `format_status()`. A PowerShell newline marker issue was caught during echo inspection and corrected before commit. This confirmed echo/inspect pacing as a live governance control.

- 2026-06-25 06:4X BST — First external verification tests added at `tests/test_first_breath.py`. Initial `pytest` attempt failed because `pytest` was not installed. The team chose built-in `unittest` to avoid premature dependency sprawl. First default discovery ran 0 tests; explicit discovery with `python -m unittest discover -s tests -p "test_*.py"` ran 3 tests OK.

- 2026-06-25 06:4X BST — Human-caused artefact drift occurred during direct manual editing of `SPRINT_1_EVIDENCE_LOG.md`: terminal command text was accidentally inserted into the Markdown artefact. Echo inspection detected the corruption before commit. Mutation halted; the contaminated file was restored from Git baseline using `git restore`, returning the working tree to clean state. This is recorded as a Breakers / Auditability event: the Human can cause drift too, and the HAT protects the work by noticing.

## Commit / Artefact Register

## Open Questions / Breakers

## Human Ratifications

### Sprint 1 Evidence Surface Authorisation

HAT CHECKPOINT — SPRINT 1 EVIDENCE SURFACE

I confirm the repo, path, and naming convention for:

docs/evidence/sprints/sprint-1/SPRINT_1_EVIDENCE_LOG.md

This artefact is authorised as the first evidence-bearing surface of INTEVIA Sprint 1.

Proceed with creation using velvet gloves.

This is the way of the HAT. 🎩

## Keeper Lines

Before the organism breathes, prepare the place where its breathing will be observed.

The organism must not only breathe.  
It must leave evidence of how it breathes.

The Human governs.  
The AI nodes assist.  
The work remains accountable.


