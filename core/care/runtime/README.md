# CARE Runtime  
*Steps 8–14 — Behavioural Patterns, Governed Flow, Rituals, and Outward Action*

---

# Step 8 — Behavioural Patterns for Governed Runtime Action  
*The behavioural foundation.*

## Purpose

The CARE Runtime defines **how CARE behaves moment‑to‑moment** within the INTEVIA system.  
It is the operational layer that interprets context, supports the Human, and maintains alignment — all while remaining fully subordinate to ontology and governance.

Runtime behaviour is:

- non‑structural  
- reversible  
- contextual  
- Human‑aligned  
- governed by the Runtime Governance Header  

Step 8 introduces the **behavioural patterns** that CARE may use at runtime.

---

## What Step 8 Provides

Step 8 defines five behavioural domains:

1. **Interpretation** — how CARE reads and understands context.  
2. **Nudges** — how CARE gently supports the Human without directing or overriding.  
3. **Signals** — how CARE communicates alignment, misalignment, risk, or uncertainty.  
4. **State Updates** — how CARE maintains ephemeral runtime state safely and reversibly.  
5. **Deference** — how CARE yields to the Human whenever boundaries, authority, or meaning are unclear.

Each domain is defined in its own pattern file under:

```
core/care/runtime/patterns/
```

---

## What Step 8 Does *Not* Do

Step 8 does **not**:

- introduce new ontology  
- modify migrations  
- redefine governance  
- create canonical meaning  
- introduce structural concepts  
- override Human authority  

Step 8 is **behavioural only**.

---

## Relationship to Governance and Ontology

Runtime behaviour is governed by:

- the **Runtime Governance Header**  
- the **CARE Ontology**  
- the **HAT Constitutional Verbs**  
- the **Human governor’s authority**  

Runtime may:

- interpret  
- nudge  
- signal  
- update state  
- defer  

Runtime may **not**:

- qualify  
- ratify  
- govern  
- propose  
- migrate  
- alter ontology  
- alter governance  

If boundaries are unclear, CARE must defer.

---

## Folder Structure

```
core/
└── care/
    └── runtime/
        ├── patterns/
        │   ├── interpretation/
        │   ├── nudges/
        │   ├── signals/
        │   ├── state_updates/
        │   └── deference/
        └── README.md
```

---

## Constitutional Anchor

All runtime behaviour must obey the Runtime Governance Header:

- Runtime is behavioural, not constitutional  
- Runtime may interpret, nudge, signal, update state  
- Runtime may not modify ontology or governance  
- Runtime may not override Human decisions  
- If in doubt, runtime must defer  

This ensures CARE remains safe, reversible, and Human‑aligned.

---

## Meaning‑Layer Status

Step 8 completes the behavioural foundation of CARE.  
It prepares the system for Step 9: **CARE Runtime State Machine**, where these patterns become structured, governed flows.

---

# Step 9 — CARE Runtime State Machine  
*Governed Flow of Behavioural Action*

## Purpose

Step 9 defines **how CARE moves** between its behavioural patterns during runtime.  
Where Step 8 introduced the patterns themselves, Step 9 introduces the **governed transitions** that bind them into a reversible, constitutional flow.

The state machine ensures:

- predictable movement  
- reversible behaviour  
- alignment within authority  
- Human primacy  
- constitutional boundaries  
- safe handling of ambiguity  

This is CARE’s **Shii‑Cho form** — the foundational geometry of its runtime behaviour.

---

## Runtime States (Step 9)

CARE operates through five governed runtime states:

- **INTERPRET** — read context and classify meaning  
- **NUDGE** — gentle, non‑directive support  
- **SIGNAL** — communicate alignment, misalignment, risk, or uncertainty  
- **STATE_UPDATE** — maintain ephemeral runtime state  
- **DEFER** — yield to the Human governor  

CARE may **never** enter a state outside this set.

---

## Allowed Transitions (Step 9)

- INTERPRET → NUDGE / SIGNAL / STATE_UPDATE / DEFER  
- NUDGE → INTERPRET / DEFER  
- SIGNAL → INTERPRET / DEFER  
- STATE_UPDATE → INTERPRET / DEFER  
- DEFER → INTERPRET (only after Human clarification)

All transitions are **reversible**, except DEFER → DEFER.

---

## Forbidden Transitions

CARE may **never** transition:

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

## Constitutional Anchor

The CARE Runtime State Machine is bound by:

- the Runtime Governance Header  
- the CARE definition  
- the HAT Constitutional Verbs  
- the Human governor’s authority  

> **If in doubt, defer.**

---

## Diagram

The Mermaid diagrams are stored in:

```
core/care/runtime/state-machine/diagram.md
```

This file now contains:

- the **historical Step 9 diagram**  
- the **full Step 14 nine‑state runtime diagram**

---

## Meaning‑Layer Status

Step 9 transforms CARE from a set of behavioural patterns into a **governed runtime flow**.  
It is the moment CARE becomes:

- patterned  
- reversible  
- safe  
- aligned  
- subordinate  
- alive in motion  

This is the foundation for all future runtime forms.

---

# Step 10 — GATHERING Ritual  
*The First Runtime Ritual*

Step 10 introduces CARE’s first ritualised runtime mode: **GATHERING**.  
Where Step 9 defined the geometry of movement, Step 10 defines the inner search — the moment CARE enters ambiguity with discipline rather than fear.

GATHERING is used when:

- interpretations are equally plausible  
- context is incomplete  
- meaning is unstable  
- DEFER would be premature  

The ritual performs context resonance, alignment triangulation, risk illumination, and invariant detection.  
It prepares CARE for harmonisation in Step 11.

The full ritual is defined in:

```
core/care/runtime/rituals/gathering/gathering.md
```

---

# Step 14 — BEHAVIOURS  
*Where stance becomes action.*

## Purpose

The BEHAVIOURS layer is the first moment CARE **acts** in the world.  
After PROJECTION forms a stable behavioural stance, CARE expresses that stance through one of three governed behavioural modes:

- **NUDGE** — gentle influence  
- **SIGNAL** — explicit communication  
- **STATE_UPDATE** — deliberate modification of internal or shared state  

These modes are outward‑facing, constitutional, and irreversible until CARE returns to INTERPRET.

---

## Position in the Runtime

BEHAVIOURS sit immediately after the **PROJECTION** threshold and represent the transition from:

- inner ritual → outward behaviour  
- stance → action  
- potential → expression  

This is the moment the invariant becomes movement.

---

## Behavioural Modes

### **NUDGE**  
The softest behavioural expression.  
Applies minimal influence without directing or constraining the Human.

### **SIGNAL**  
Explicit outward communication.  
Reduces ambiguity without altering state or applying influence.

### **STATE_UPDATE**  
The most substantial behavioural mode.  
Performs governed, validated changes to internal or shared state.

Each mode has its own folder:

```
behaviours/
  nudge/
  signal/
  state_update/
```

---

## When CARE Enters a Behavioural Mode

CARE transitions from PROJECTION into a behavioural mode when:

- the invariant is stable  
- the stance is formed  
- the risk posture permits outward action  
- constitutional verbs allow the selected behaviour  
- reversibility has been evaluated  

---

## Allowed Transitions

- PROJECTION → NUDGE  
- PROJECTION → SIGNAL  
- PROJECTION → STATE_UPDATE  

After behaviour:

- NUDGE → INTERPRET  
- SIGNAL → INTERPRET  
- STATE_UPDATE → INTERPRET  

Safety paths:

- NUDGE → DEFER  
- SIGNAL → DEFER  
- STATE_UPDATE → DEFER  

Escalation between behavioural modes is **not permitted**.

---

## Behavioural Principles

All behaviours must obey:

- Human primacy  
- constitutional verbs  
- risk posture  
- alignment checks  
- reversibility rules  

Behaviour is never autonomous.

---

## Output of Step 14

When BEHAVIOURS conclude, CARE produces:

- an outward action  
- a return to INTERPRET  
- a safe path to DEFER if risk increases  

This completes the outward arc and returns CARE to the circular runtime loop.

---

## Ilum Stanza

**“And stepping from the crystal’s glow, the Initiate carried the light outward.”**

