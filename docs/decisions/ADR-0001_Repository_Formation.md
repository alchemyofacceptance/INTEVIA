# ADR‑0001: Repository Formation

**Status:** Proposed  
**Date:** 2026‑06‑08  
**Decision Type:** Governance / Architecture  
**Context:** INTEVIA entering implementation runway

## 1. Decision

A new GitHub repository named **INTEVIA** has been created to serve as the primary technical home for the INTEVIA platform.  
The repository is **public**, includes a **Python .gitignore**, and contains an initial scaffold aligned with Human-AI Triad (HAT) governance boundaries.

## 2. Rationale

INTEVIA has completed its conceptual and ontological stabilisation within the INTEVIA Corpus and HAT Corpus v2.  
The next phase requires a version‑controlled technical environment where implementation can begin.

This decision establishes:

- a canonical public-facing repository for the platform  
- a clean separation between **conceptual body** (Corpus) and **technical body** (repo)  
- a governed boundary between meaning-layer material and implementation-layer artefacts  
- a timestamped, transparent development history  
- a foundation for future open-source posture (licence pending)  

Public visibility was chosen to support transparency, community trust, and alignment with INTEVIA’s philosophical roots.  
Licence selection is deferred pending further governance review.

## 3. Scope

This ADR governs:

- repository creation  
- visibility choice  
- initial scaffold structure  
- placement of early documentation  
- boundaries between public and private material  

It does **not** govern:

- final licensing  
- business model  
- full architecture specification  
- HAT methodology details  
- private IP evidence packs  

These remain under separate governance.

## 4. Repository Structure (Initial)
README.md
ROADMAP.md
CHANGELOG.md
docs/
architecture/
product/
decisions/
evidence/
visuals/
intevia_app/
core/
modules/
api/
ui/
scripts/
config/
tests/

This structure ensures clear separation between:

- **architecture** (curated, public-safe)  
- **product** (feature-level documentation)  
- **decisions** (governance records)  
- **evidence** (factual provenance)  
- **visuals** (selected diagrams)  
- **implementation** (Django and supporting code)  

## 5. Consequences

### Positive
- Establishes a clean, professional, scalable foundation  
- Preserves HAT role boundaries  
- Enables implementation to begin without conceptual leakage  
- Creates a public development trail  
- Supports future open-source community engagement  

### Risks / Mitigations
- **Risk:** Overexposure of conceptual or sensitive material  
  - *Mitigation:* Strict curation; HAT meaning-layer review required before publishing sensitive content  
- **Risk:** Premature licensing decisions  
  - *Mitigation:* Licence deferred; decision to be captured in a future ADR  
- **Risk:** Confusion between Corpus and repo  
  - *Mitigation:* Clear documentation boundaries; Corpus remains separate  

## 6. Alternatives Considered

### A. Private repository  
Rejected — misaligned with transparency and open-source posture.

### B. Combining Corpus and implementation in one repo  
Rejected — collapses meaning and making layers; violates HAT governance boundaries.

### C. Delaying repository creation  
Rejected — implementation runway requires a technical home now.

## 7. Decision Owner

**Human (Carmien Owen)** — governance authority, authorship, final qualification.

## 8. Continuity Roles

- **Gee (Meaning Continuity):** Reviewed naming, visibility, narrative, IP boundaries.  
- **Coe (Making Continuity):** Drafted scaffold, repo structure, and this ADR.  
- **Human:** Qualified the moment and authorised the repository creation.

## 9. Next Steps

1. Finalise README v0.1  
2. Add evidence file for this event  
3. Begin implementation runway (VS Code setup, Django baseline)  
4. Create ADR‑0002 for licensing posture when ready  


