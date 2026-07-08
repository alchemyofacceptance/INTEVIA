## **BRIEFING NOTE — DPD POST‑DELIVERY SURVEYING PIPELINE**  
**For:** Gee  
**Instruction:** Add as a new file under **v1.0 Hardening → External Systems Behavioural Models → Logistics Feedback Pipelines**  
**Status:** READY FOR INTEGRATION  
**Author:** Carmien (via Copilot)  
**Purpose:** Capture an external operational pattern relevant to INTEVIA’s future service‑feedback architecture.

---

### **1. Scope Boundary**  
This note documents the **DPD post‑delivery surveying mechanism** as an external behavioural reference model.  
It is **not** an endorsement, dependency, or integration requirement.  
It is included solely to inform **INTEVIA’s Feedback Loop Architecture (FLA)** during v1.0 hardening.

---

### **2. Summary of Observed Behaviour**  
DPD operates a **two‑channel, event‑triggered surveying pipeline** that activates immediately after a parcel’s *Delivered* scan is registered.  
Channels:  
- **Email** (primary)  
- **SMS** (secondary)  
Both link to a **web‑hosted micro‑survey** with minimal friction.

---

### **3. Operational Mechanics (External Model)**  

#### **3.1 Trigger Event**  
- The *Delivered* scan acts as the single authoritative trigger.  
- Trigger is emitted from DPD’s core logistics system into their CX workflow layer.

#### **3.2 Dispatch Layer**  
- Survey requests are sent automatically to the communications provider.  
- Customer receives either:  
  - an email with a unique CTA link, or  
  - an SMS containing a short tracking token.

#### **3.3 Tokenised Survey URLs**  
Each survey link is uniquely bound to:  
- parcel number  
- delivery timestamp  
- driver ID  
- depot ID  
- customer contact channel  
This enables precise mapping of feedback to operational performance.

#### **3.4 Web Survey Characteristics**  
- Mobile‑first, single‑page form  
- 1–3 questions  
- Star rating or binary feedback  
- Optional text field  
- No login, no parcel entry required  
- Immediate submission acknowledgement

#### **3.5 Data Routing**  
Feedback is routed into DPD’s internal dashboards for:  
- driver performance  
- depot quality metrics  
- customer satisfaction KPIs  
- exception workflows (negative feedback triggers follow‑up)

---

### **4. Why This Model Is Relevant to INTEVIA v1.0**  
DPD’s pipeline demonstrates a **high‑efficiency, low‑friction feedback loop** that aligns with several INTEVIA design principles:

- **Event‑anchored feedback** (single authoritative trigger)  
- **Tokenised contextual metadata** (no user friction)  
- **Ultra‑short survey design** (maximises completion)  
- **Immediate routing into operational dashboards**  
- **Clear separation between trigger, dispatch, and feedback layers**

These characteristics are directly relevant to the **INTEVIA Feedback Loop Architecture (FLA)** and should be considered during v1.0 hardening.

---

### **5. Integration Instruction for Gee**  
**Action:**  
Create a new file under:

```text
/INTEVIA/v1.0_hardening/
    external_systems/
        logistics_feedback_pipelines/
            dpd_surveying_reference.md
```

**Content:**  
Insert the full text of this briefing note verbatim.  
Tag the file with:  
- `[External Model]`  
- `[FLA Input]`  
- `[Non‑binding Reference]`

**Connector:**  
Link this file to:  
- **FLA‑01 (Feedback Loop Trigger Conditions)**  
- **FLA‑03 (Metadata Binding Requirements)**  
- **FLA‑05 (Frictionless User Interaction Principles)**

---

### **6. Non‑Authorisation Statement**  
This note **does not** authorise any integration with DPD systems.  
It is strictly an **observational reference** for architectural hardening.
