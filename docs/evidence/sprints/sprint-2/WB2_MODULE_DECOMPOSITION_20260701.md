# WB2 Module-Level Decomposition — INTEVIA Sprint 2 (2026-07-01)

**Status:** Evidence / Audit Note  
**Mode:** Read-only structural mapping  
**Mutation Class:** Non-runtime, non-activating documentation  
**Scope:** Sprint 2 / WB2 — Structure Consolidation Phase 1

---

## 1. Purpose

This shard records the module-level decomposition of three overlapping directory surfaces:

- `intevia/`
- `intevia_app/`
- `src/intevia/`

It documents repo reality only, without proposing refactors or normalisation.

> WB2 begins with repo reality.  
> Structure consolidation requires observation before change.

---

## 2. WB2 Context

- WB2 has completed its first evidence mutation: `WB2_REPO_REALITY_MAP_20260701.md`.
- Repo reality is anchored.
- Runtime seed and Django scaffold are present but untouched.
- CRP Annex v0.2 is field-active, building on v0.1, and governs execution-context correctness.
- This shard continues **Structure Consolidation Phase 1 — Reality Mapping**.

---

## 3. Inspection Metadata

- **Repo:** `alchemyofacceptance/INTEVIA`
- **Branch:** `main`
- **Paths inspected:** `intevia/`, `intevia_app/`, `src/intevia/`
- **Mutation performed:** None
- **Missing / inaccessible paths:**
  - None inaccessible.
  - `intevia_app/` exists as a directory, but no standard Django app files were confirmed by bounded file probes.

---

## 4. Directory Decomposition

### 4.1 `intevia/` — Django Project Surface

**Files found:**

- `intevia/__init__.py` — package marker.
- `intevia/settings.py` — Django settings module; identifies the project as `intevia`, includes Django 5.2.15 generated settings, sets `INSTALLED_APPS`, includes `core`, uses SQLite, and sets standard static configuration.
- `intevia/urls.py` — Django URL configuration with admin route only.
- `intevia/asgi.py` — ASGI config setting `DJANGO_SETTINGS_MODULE` to `intevia.settings`.
- `intevia/wsgi.py` — WSGI config setting `DJANGO_SETTINGS_MODULE` to `intevia.settings`.

**Apparent purpose:**

> Django project / configuration package.

**Confidence:** High

---

### 4.2 `intevia_app/` — Ambiguous / Unconfirmed App Surface

**Files found:**

- Directory path is present and accessible.
- Bounded probes did not confirm readable standard Django app files such as:
  - `__init__.py`
  - `apps.py`
  - `models.py`
  - `admin.py`
  - `views.py`
  - `tests.py`
  - `migrations/__init__.py`
  - `README.md`
  - `.gitkeep`

**Apparent purpose:**

> Unclear from confirmed files. The name suggests a possible Django app surface, but no file-level purpose was confirmed in this bounded inspection.

**Confidence:** Low

---

### 4.3 `src/intevia/` — Python Package / Runtime Seed

**Files found:**

- `src/intevia/__init__.py` — package root marker with docstring `INTEVIA package root.`
- `src/intevia/app.py` — initial runtime seed; defines `breathe()` returning `INTEVIA organism initialised`.
- `src/intevia/governance/__init__.py` — governance package marker.
- `src/intevia/governance/status.py` — governance status surface; still identifies the sprint as `INTEVIA Sprint 1` and status as `governed build-study active`.

**Apparent purpose:**

> Python package / runtime seed surface, separate from the Django project package, including a minimal runtime function and a governance status formatter.

**Confidence:** High

---

## 5. Overlaps / Ambiguities

Observed overlaps and ambiguities:

- `intevia/` is confirmed as the Django project/configuration package.
- `src/intevia/` is confirmed as a separate Python package/runtime seed.
- `intevia_app/` is ambiguous: directory exists, but no standard Django app files were confirmed.
- `intevia/settings.py` includes `core` in `INSTALLED_APPS`, not `intevia_app`.
- The relationship between Django project (`intevia/`), ambiguous app surface (`intevia_app/`), and runtime package (`src/intevia/`) requires later clarification.

No refactor or normalisation is proposed in this shard.

---

## 6. Held Items for Later WB2 Planning

Held for later inspection and planning:

- Clarify the intended role of `intevia_app/`.
- Document and later clarify the relationship between Django project and runtime seed.
- Sprint 1 governance wording in `src/intevia/governance/status.py` remains observed only, not changed.
- Directory consolidation candidates remain deferred.
- Django entrypoint clarification (`run.py` vs `manage.py`) remains deferred.

---

## 7. WB2 Next Step (Non-Mutating)

Recommended next smallest WB2 step:

> Perform a bounded read-only inspection of the `core/` directory, because `intevia/settings.py` includes `core` in `INSTALLED_APPS`.

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

> WB1-equivalent foundation is materially present.  
> WB2 begins with repo reality.  
> Inspect only what is needed.  
> Mutate only after Human authorisation.  
> Governance serves action, not theatre.
