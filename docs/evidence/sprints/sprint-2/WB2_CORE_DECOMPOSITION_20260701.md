# WB2 Core Directory Decomposition — INTEVIA Sprint 2 (2026-07-01)

**Status:** Evidence / Audit Note  
**Mode:** Read-only structural mapping  
**Mutation Class:** Non-runtime, non-activating documentation  
**Scope:** Sprint 2 / WB2 — Structure Consolidation Phase 1

---

## 1. Purpose

This shard records the dependency-linked decomposition of the `core/` directory, which is referenced in:

> `intevia/settings.py → INSTALLED_APPS`

WB2 must map referenced structure before proposing any refactor or normalisation.

> WB2 is discovering the system, not designing it.  
> Only dependency-linked inspection is valid.

---

## 2. WB2 Context

- WB2 Shard #1 anchored repo reality.  
- WB2 Shard #2 mapped module-level reality.  
- CRP Annex v0.2 field refinement, building on v0.1, governs execution-context correctness.  
- `core/` is the next dependency-linked structural anchor.  
- This shard continues **Structure Consolidation Phase 1 — Reality Mapping**.

---

## 3. Inspection Metadata

- **Repo:** `alchemyofacceptance/INTEVIA`  
- **Branch:** `main`  
- **Path inspected:** `core/`  
- **Mutation performed:** None  
- **Missing / inaccessible paths:** None identified during bounded inspection.

---

## 4. Directory Decomposition — `core/`

### 4.1 Files Found

- `core/__init__.py` — empty package marker.  
- `core/apps.py` — defines `CoreConfig` with `name = 'core'`.  
- `core/admin.py` — standard Django admin placeholder.  
- `core/models.py` — contains concrete Django models:  
  - `Profile` — extends Django `User` with INTEVIA-specific identity fields.  
  - `Role` — represents functional/conceptual roles.  
  - `ProfileRole` — bridges profiles and roles with uniqueness metadata.  
- `core/views.py` — standard Django view placeholder.  
- `core/tests.py` — standard Django test placeholder.  
- `core/migrations/__init__.py` — empty migrations package marker.

### 4.2 Apparent Purpose

> `core/` appears to be the active Django app surface for early identity/role modelling.  
> It is not merely scaffold; `core/models.py` contains concrete identity and role models.

### 4.3 Confidence

High

### 4.4 Dependency Relevance

- `core/` is referenced in `INSTALLED_APPS`.  
- `core/apps.py` defines `CoreConfig` with `name = 'core'`.  
- This confirms the app identity matches Django configuration.

---

## 5. Overlaps / Ambiguities

- `core/` appears to be the active Django app surface.  
- `intevia_app/` remains ambiguous / unconfirmed.  
- `src/intevia/` remains a separate runtime seed / package surface.  
- `core/models.py` identity/role concepts may overlap conceptually with documentation identity surfaces, but no runtime or documentation relationship was inferred.  
- Relationship between `core/`, `intevia_app/`, and `src/intevia/` remains for later analysis.

No refactor or normalisation is proposed.

---

## 6. Held Items for Later WB2 Planning

- Clarify intended role of `intevia_app/`.  
- Document relationship between Django project (`intevia/`), Django app (`core/`), and runtime seed (`src/intevia/`).  
- Sprint 1 governance wording in `src/intevia/governance/status.py` remains observed only.  
- Directory consolidation candidates remain deferred.  
- Django entrypoint clarification (`run.py` vs `manage.py`) remains deferred.  
- No migration generation or model normalisation proposed.

---

## 7. WB2 Next Step (Non-Mutating)

> Route this inspection report back through ME/Gee for analysis and sequencing.  
> The next smallest WB2 step is likely a read-only inspection of the next dependency-linked directory, to be selected by ME.

This remains strictly observational and non-mutating.

---

## 8. Boundary Conditions

This shard:

- does **not** change runtime behaviour;  
- does **not** normalise code;  
- does **not** consolidate directories;  
- does **not** amend doctrine or CRP;  
- does **not** activate WB2 execution;  
- does **not** touch Django settings or runtime surfaces.

It records **reality only**.

---

## 9. Keeper

> WB2 is discovering the system, not designing it.  
> Only dependency-linked inspection is valid.  
> Mutation comes later.
