# WB2 URL Surface Decomposition — INTEVIA Sprint 2 (2026-07-01)

**Status:** Evidence / Audit Note  
**Mode:** Read-only structural mapping  
**Mutation Class:** Non-runtime, non-activating documentation  
**Scope:** Sprint 2 / WB2 — Structure Consolidation Phase 1

---

## 1. Purpose

This shard records the dependency-linked decomposition of the Django project’s URL surface:

- `intevia/urls.py`
- any directly referenced local URL modules

WB2 must map referenced structure before proposing any refactor or normalisation.

> WB2 is discovering the system, not designing it.  
> Only directly referenced URL surfaces are in scope.

---

## 2. WB2 Context

- WB2 Shard #1 anchored repo reality.  
- WB2 Shard #2 mapped module-level reality.  
- WB2 Shard #3 mapped the active Django app (`core/`).  
- CRP Annex v0.2 field refinement, building on v0.1, governs execution-context correctness.  
- URL routing is the next dependency-linked structural anchor.  
- This shard continues **Structure Consolidation Phase 1 — Reality Mapping**.

---

## 3. Inspection Metadata

- **Repo:** `alchemyofacceptance/INTEVIA`  
- **Branch:** `main`  
- **Paths inspected:** `intevia/urls.py`  
- **Traversal depth:** one level only  
- **Mutation performed:** None  
- **Missing / inaccessible paths:** None

---

## 4. URL Surface Decomposition

### 4.1 `intevia/urls.py`

**File status:**  
Present and readable. Django project-level URL configuration file.

**Imports:**

- `from django.contrib import admin`  
- `from django.urls import path`

**URL patterns:**

- `path('admin/', admin.site.urls)`

**Direct local URL includes:**  
None  
(No `include()` calls present.)

**Apparent purpose:**  
Defines the root URL routing for the Django project.  
Currently exposes only the Django admin interface.  
Does not delegate routing to any local application URL modules.

**Confidence:** High

---

### 4.2 Directly Referenced Local URL Modules

None

(No `include()` calls found in `intevia/urls.py`, so no local URL modules such as `core.urls` or `intevia_app.urls` are referenced.)

---

## 5. Overlaps / Ambiguities

- URL configuration is currently minimal and centralized at project level.  
- No app-level routing layer is wired into the project yet.  
- `core/` exists in `INSTALLED_APPS` but is not exposed through URL routing.  
- `intevia_app/` remains unreferenced and ambiguous.  
- System is structurally ready for expansion but currently exposes only the admin endpoint.

No refactor or normalisation is proposed.

---

## 6. Held Items for Later WB2 Planning

- Potential future introduction of `core.urls` or app-level routing separation.  
- Clarification of routing strategy: single-module vs modular URL dispatch.  
- Relationship between app surfaces and URL exposure remains deferred.  
- No action required; this is purely structural observation.

---

## 7. WB2 Next Step (Non-Mutating)

> Perform read-only inspection of `core/views.py` and `core/models.py`  
> to determine whether any view layer is intended for future URL binding,  
> since the current URL surface does not expose any application endpoints.

This remains strictly observational and non-mutating.

---

## 8. Boundary Conditions

This shard:

- does **not** change runtime behaviour;  
- does **not** normalise code;  
- does **not** consolidate directories;  
- does **not** amend doctrine or CRP;  
- does **not** activate WB2 execution;  
- does **not** touch Django settings, URL routing, or runtime surfaces.

It records **reality only**.

---

## 9. Keeper

> WB2 is discovering the system, not designing it.  
> Only directly referenced URL surfaces are in scope.  
> No recursive traversal.  
> Mutation comes later.
