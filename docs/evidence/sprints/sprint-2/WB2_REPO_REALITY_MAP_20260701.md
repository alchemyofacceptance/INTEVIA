# WB2 Repo Reality Map — INTEVIA Sprint 2 (2026-07-01)

**Status:** Evidence / Audit Note  
**Mode:** Read-only repo reality map  
**Mutation Class:** Non-runtime, non-activating documentation  
**Scope:** Sprint 2 / WB2 — Structure Consolidation Entry

---

## 1. WB1 → WB2 Transition Marker

**Transition timestamp:** WB1 End / WB2 Start — 2026-07-01 19:39:53 Europe/London  

**Canonical wording:**

> WB1-equivalent foundation is materially present in the repo; WB2 begins by consolidating and documenting that reality before code normalisation.

Interpretation:

- WB1-equivalent foundation work is treated as materially present in the repository.
- WB2 begins as **Current Repo Structure Audit / Structure Consolidation**, not as new feature work.
- This file records what exists; it does not declare WB1 formally closed or WB2 fully activated.

---

## 2. Connector / Repo Inspection Status

- GitHub connector: **available**.  
- Repository: `alchemyofacceptance/INTEVIA` **inspected in read-only mode**.  
- No WB2 mutations had been performed before creation of this map.  

Grounded language:

- **Capability:** Connector can read the repo.  
- **Authority:** Human has authorised read-only inspection and creation of this evidence note.  
- **Reality:** This shard creates only this evidence file and performs no runtime or code normalisation.

---

## 3. Observed Top-Level Repo Structure

Read-only inspection shows the following top-level entries:

- `architecture/`  
- `config/`  
- `core/`  
- `docs/`  
- `governance/`  
- `intevia/`  
- `intevia_app/`  
- `scripts/`  
- `src/intevia/`  
- `tests/`  
- `.gitattributes`  
- `.gitignore`  
- `CHANGELOG.md`  
- `LICENSE`  
- `NOTICE`  
- `README.md`  
- `ROADMAP.md`  
- `manage.py`  
- `run.py`  

Interpretation:

- The repo is **not greenfield**; multiple structural surfaces already exist.
- There are overlapping application / module directories (`intevia/`, `intevia_app/`, `src/intevia/`) that will require later consolidation, but **not in this shard**.

---

## 4. Runtime Seed Observed

Runtime seed is present.

Key observations:

- `run.py` imports `breathe()` and governance status formatting.  
- `breathe()` returns: `INTEVIA organism initialised`.  
- `src/intevia/governance/status.py` exposes a governance status surface.  
- That status surface still references **INTEVIA Sprint 1** and status `governed build-study active`.

Interpretation:

- A runtime entry surface exists and is already wired to governance status.  
- Sprint 1-specific wording is still present and will require later normalisation for Sprint 2, but **WB2 entry does not change it**.

---

## 5. Django Scaffold Observed

Django scaffold is materially present.

Key observations:

- `manage.py` sets `DJANGO_SETTINGS_MODULE` to `intevia.settings`.  
- `src/intevia/app.py` exists as part of the application surface.  

Interpretation:

- Django configuration and application scaffolding are present.  
- WB2 treats this scaffold as **existing foundation**, not as a new build target.  
- No Django settings or app wiring changes are performed in this shard.

---

## 6. Documentation Surfaces Observed

Public documentation surfaces exist and are explicitly non-activating:

- `docs/public/IDENTITY_PROFILE_SURFACE_V1_0.md`  
- `docs/public/REVIEW_SURFACE_V1_0.md`  

Additional documentation:

- `README.md` — repo overview.  
- `ROADMAP.md` — currently blank / empty.  
- `CHANGELOG.md` — contains at least one entry:  
  - `2026-06-08 Execution Policy Exception Event added to Corpus v2`.

Interpretation:

- Documentation surfaces are **real and useful**, but they do not directly drive runtime behaviour.  
- Public identity and review surfaces are **non-activating documentation**, not execution engines.

---

## 7. Docs vs Runtime Boundary

Boundary statement:

> Documentation surfaces (including `docs/public/*`, `README.md`, `ROADMAP.md`, and `CHANGELOG.md`) are treated as non-activating evidence and narrative. Runtime behaviour is driven by code surfaces such as `run.py`, `manage.py`, `src/intevia/app.py`, and `src/intevia/governance/status.py`.

Operational implication:

- WB2 evidence work occurs in `docs/evidence/...` and related documentation paths.  
- WB2 does **not** change runtime code or configuration in this shard.  
- Governance language in docs must not be mistaken for runtime execution.

---

## 8. Sprint 1 References Requiring Later Normalisation

Observed Sprint 1-specific references include:

- `src/intevia/governance/status.py` — references **INTEVIA Sprint 1** and `governed build-study active`.  
- Other Sprint 1 wording may exist in documentation or governance surfaces.

WB2 stance:

- These references are **documented here** as reality.  
- They are **not** normalised or removed in this shard.  
- Later WB2 / WB3 work may propose normalisation once structure consolidation is complete.

---

## 9. Known Structure-Consolidation Candidates

Read-only inspection suggests the following future consolidation candidates:

- Overlapping directories:  
  - `intevia/`  
  - `intevia_app/`  
  - `src/intevia/`  

- Clarifying the relationship between:  
  - Django settings (`intevia.settings`)  
  - Application entry surfaces (`src/intevia/app.py`, `run.py`, `manage.py`)  

WB2 entry rule:

> Identify consolidation candidates, but do not mutate them yet.

---

## 10. Next Safe Code-Normalisation Candidates

After this evidence shard is committed and reviewed, safe next candidates may include:

- Normalising Sprint 1 wording in `src/intevia/governance/status.py` to reflect Sprint 2 state.  
- Aligning runtime status language with the CRP Annex v0.1 keeper and WB2 reality.  
- Clarifying and documenting the intended primary runtime entrypoint (`run.py` vs `manage.py`) before any refactor.

These are **future WB2 steps**, not part of this shard.

---

## 11. No-Mutation / No-Runtime-Change Boundary

Canonical boundary:

> This file records repo reality only. It does not change runtime behaviour, normalise code, activate WB2 execution, or alter doctrine.

Explicitly, this shard:

- does **not** modify `run.py`;  
- does **not** modify `manage.py`;  
- does **not** modify `src/intevia/app.py`;  
- does **not** modify `src/intevia/governance/status.py`;  
- does **not** change Django settings or deployment configuration;  
- does **not** amend CRP, doctrine, Article 4, or LanesFlow.

---

## 12. Keeper

> WB1-equivalent foundation is materially present.  
> WB2 begins with repo reality.  
> Inspect only what is needed.  
> Mutate only after Human authorisation.  
> Governance serves action, not theatre.
