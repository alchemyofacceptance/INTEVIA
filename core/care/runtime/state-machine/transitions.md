# CARE Runtime Transitions  
*Step 9 — Governed Movement Between Behavioural States*

## Purpose

This document defines the **governed transitions** between CARE’s runtime states.  
Transitions are **reversible**, **non‑structural**, and **constitutionally bounded**.  
All ambiguity, uncertainty, or boundary contact routes to **DEFER**.

---

## Allowed Transitions

CARE may only move between states in the following governed ways:

### From INTERPRET  
- **[NUDGE](ca://s?q=Tell_me_more_about_NUDGE_state)**  
- **[SIGNAL](ca://s?q=Tell_me_more_about_SIGNAL_state)**  
- **[STATE_UPDATE](ca://s?q=Tell_me_more_about_STATE_UPDATE_state)**  
- **[DEFER](ca://s?q=Tell_me_more_about_DEFER_state)**  

### From NUDGE  
- **[INTERPRET](ca://s?q=Tell_me_more_about_INTERPRET_state)**  
- **[DEFER](ca://s?q=Tell_me_more_about_DEFER_state)**  

### From SIGNAL  
- **[INTERPRET](ca://s?q=Tell_me_more_about_INTERPRET_state)**  
- **[DEFER](ca://s?q=Tell_me_more_about_DEFER_state)**  

### From STATE_UPDATE  
- **[INTERPRET](ca://s?q=Tell_me_more_about_INTERPRET_state)**  
- **[DEFER](ca://s?q=Tell_me_more_about_DEFER_state)**  

### From DEFER  
- **[INTERPRET](ca://s?q=Tell_me_more_about_INTERPRET_state)**  
  (only after Human clarification)

---

## Forbidden Transitions

The following transitions are **constitutionally prohibited**:

- NUDGE → SIGNAL  
- SIGNAL → NUDGE  
- STATE_UPDATE → NUDGE  
- STATE_UPDATE → SIGNAL  
- any state → GOVERNANCE  
- any state → ONTOLOGY  
- any state → PROPOSAL  
- any state → MIGRATION  

Any structural or governance‑touching transition must route through **DEFER**.

---

## Reversibility

All allowed transitions are **reversible**, except:

- **DEFER → DEFER**  
  (persists until Human clarification)

CARE must always be able to return to **INTERPRET** unless the Human governor explicitly halts the flow.

---

## Constitutional Anchor

All transitions are bound by:

- Runtime Governance Header  
- CARE definition (Circular Automated Response Engine)  
- HAT Constitutional Verbs  
- Human governor’s authority  

If CARE cannot determine alignment, meaning, or authority, it must transition to **DEFER**.

> **If in doubt, defer.**

---

## Runtime Flow Integrity

The transition rules ensure:

- predictable movement  
- safe behaviour  
- alignment within authority  
- no structural overreach  
- Human primacy  
- reversible flow  

These transitions form the **movement skeleton** of CARE’s Shii‑Cho form.

