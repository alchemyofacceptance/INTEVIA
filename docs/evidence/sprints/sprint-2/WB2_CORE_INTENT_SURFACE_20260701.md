# WB2 Core Intent Surface Decomposition — INTEVIA Sprint 2 (2026-07-01)

**Status:** Evidence / Audit Note  
**Mode:** Read-only structural mapping  
**Mutation Class:** Non-runtime, non-activating documentation  
**Scope:** Sprint 2 / WB2 — Structure Consolidation Phase 1

---

## 1. Purpose

This shard records the intent surface decomposition of:

- `core/views.py`
- `core/models.py`

WB2 must determine whether any view or model layer indicates latent intent for future URL binding or application behaviour.

> WB2 is discovering the system, not designing it.  
> Only authorised intent surfaces are in scope.

---

## 2. WB2 Context

- WB2 Shard #1 anchored repo reality.  
- WB2 Shard #2 mapped module-level reality.  
- WB2 Shard #3 mapped the active Django app (`core/`).  
- WB2 Shard #4 mapped the URL surface (`intevia/urls.py`).  
- URL routing exposes no app endpoints, so WB2 must inspect internal intent surfaces.  
- CRP Annex v0.2 field refinement, building on v0.1, governs execution-context correctness.  
- This shard continues **Structure Consolidation Phase 1 — Reality Mapping**.

---

## 3. Inspection Metadata

- **Repo:** `alchemyofacceptance/INTEVIA`  
- **Branch:** `main`  
- **Paths inspected:** `core/views.py`; `core/models.py`  
- **Traversal depth:** one level only  
- **Mutation performed:** None  
- **Missing / inaccessible paths:** None

---

## 4. Intent Surface Decomposition

### 4.1 `core/views.py`

**File status:**  
Present and readable.

**Contents summary:**

- Imports `render` from `django.shortcuts`.  
- Contains only the standard placeholder comment:  
  `# Create your views here.`  
- No view functions.  
- No view classes.  
- No routing intent visible.

**Apparent purpose:**  
Placeholder Django views file.  
Does not currently indicate intended URL binding.

**Confidence:** High

---

### 4.2 `core/models.py`

**File status:**  
Present and readable.

**Contents summary:**

- Imports `models` from `django.db`.  
- Imports `User` from `django.contrib.auth.models`.  
- Defines three concrete Django models:

#### **Profile**
- One-to-one extension of Django `User`.  
- Adds `display_name` and `created_at`.  
- Comments describe it as the “human identity” anchor.

#### **Role**
- Unique `name`.  
- Optional `description`.  
- `created_at` timestamp.  
- Represents functional/conceptual roles.

#### **ProfileRole**
- Bridges `Profile` and `Role`.  
- Includes `assigned_at`.  
- Enforces uniqueness via `unique_together = ('profile', 'role')`.  
- Comments describe it as an “organ system” for profile-role relationships.

**Apparent purpose:**  
Concrete identity/role modelling.  
Represents meaningful application intent at the data layer.

**Confidence:** High

---

## 5. Dependency Relevance

- Models indicate active identity/role intent.  
- Views do not currently indicate routing intent.  
- Based on authorised files only, the app contains model-layer substance without visible view-layer implementation.

This is structurally consistent with early-stage Django apps.

---

## 6. Overlaps / Ambiguities

- `core/models.py` contains identity/role concepts that may later relate to public identity documentation surfaces.  
- `core/views.py` does not yet show how those models are exposed or used in UI/routing.  
- No URL-binding intent is visible in the inspected files.  
- No migration state was inspected, per boundary.  
- No admin/test/forms/template/API relationship was inspected, per boundary.  
- Relationship between model intent and future view/routing layer remains deferred.

No refactor or normalisation is proposed.

---

## 7. Held Items for Later WB2 Planning

- Clarify future routing strategy once view-layer intent emerges.  
- Document relationship between identity/role models and future UI/API surfaces.  
- Defer any consolidation or normalisation decisions.  
- Defer migration generation or model evolution.  
- Defer any relationship between Django app identity and runtime seed identity.

---

## 8. WB2 Next Step (Non-Mutating)

> Route this inspection report back through Gee and ME for sequencing.  
> The next smallest WB2 step will be selected based on dependency relevance.

This remains strictly observational and non-mutating.

---

## 9. Boundary Conditions

This shard:

- does **not** change runtime behaviour;  
- does **not** normalise code;  
- does **not** consolidate directories;  
- does **not** amend doctrine or CRP;  
- does **not** activate WB2 execution;  
- does **not** touch Django settings, URL routing, views, models, migrations, or runtime surfaces.

It records **reality only**.

---

## 10. Keeper

> WB2 is discovering the system, not designing it.  
> Only authorised intent surfaces are in scope.  
> Mutation comes later.
