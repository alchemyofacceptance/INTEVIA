# INTEVIA — Domain Map (v0.1)

## 1. Purpose of the Domain Map
The Domain Map defines the major conceptual domains that make up the INTEVIA organism. Each domain represents a coherent area of responsibility, behaviour, and data. Together, they form the semantic structure of the system.

This Domain Map describes the broader INTEVIA organism. It is not itself the v1.0 Implementation Boundary. v1.0 implementation remains governed by the v1.0 Boundary, Forge Activation Packet, Execution Plan, and Build Tasks.

This is the third layer of the architecture, following the System Context and Container Diagram.

---

## 2. Domain Overview
INTEVIA consists of nine primary domains, each representing a core aspect of the organism:

1. **People**
2. **Events**
3. **Locations**
4. **Service**
5. **Exchange**
6. **Education**
7. **Library**
8. **Applications**
9. **Care/Core**

These domains are governed by the Governance Engine and supported by the Corpus and HAT Collaboration Layer.

---

## 3. Domain Descriptions

### 3.1 People Domain
**Purpose:**  
Represent individuals participating in the organism. 

Events and Services cannot function without participant/member identity. For v1.0, People exists only as a minimal identity substrate required by Events and Services, not as a full People organ.

**Responsibilities:**  
- Member profiles  
- Roles and capabilities  
- Participation history  
- Contribution records  
- Onboarding and offboarding states  

**Interactions:**  
- Events  
- Service  
- Exchange  
- Education  
- Governance  

---

### 3.2 Events Domain
**Purpose:**  
Coordinate gatherings, activities, and structured interactions.

**Responsibilities:**  
- Event creation and scheduling  
- Attendance tracking  
- Capacity and logistics  
- Event states and lifecycle  
- Integration with Locations and People  

**Interactions:**  
- People  
- Locations  
- Service  
- Notifications  

---

### 3.3 Locations Domain
**Purpose:**  
Represent physical or virtual places where events and services occur.

Events need location metadata, but the full Locations domain remains deferred for v1.0.

**Responsibilities:**  
- Location metadata  
- Capacity and availability  
- Scheduling constraints  
- Accessibility and configuration  

**Interactions:**  
- Events  
- Service  
- Applications  

---

### 3.4 Service Domain
**Purpose:**  
Represent acts of service, contribution, or operational work.

**Responsibilities:**  
- Service requests  
- Assignments and fulfilment  
- Contribution tracking  
- Operational workflows  

**Interactions:**  
- People  
- Events  
- Exchange  
- Governance  

---

### 3.5 Exchange Domain
**Purpose:**  
Represent flows of value, contribution, or recognition.

**Responsibilities:**  
- Contribution units  
- Recognition events  
- Exchange rules  
- Balances and ledgers  

**Interactions:**  
- People  
- Service  
- Events  
- Governance  

---

### 3.6 Education Domain
**Purpose:**  
Represent learning, training, and development pathways.

**Responsibilities:**  
- Courses and modules  
- Learning paths  
- Assessments  
- Progress tracking  

**Interactions:**  
- People  
- Events  
- Library  

---

### 3.7 Library Domain
**Purpose:**  
Represent knowledge, documents, and reference materials.

**Responsibilities:**  
- Document storage  
- Versioning and lineage  
- Knowledge categorisation  
- Corpus integration  

**Interactions:**  
- Education  
- People  
- Governance  

---

### 3.8 Applications Domain
**Purpose:**  
Represent structured requests, forms, and submissions.

**Responsibilities:**  
- Application forms  
- Submission workflows  
- Review and approval processes  
- Integration with governance rules  

**Interactions:**  
- People  
- Locations  
- Governance  

---

### 3.9 CARE/Core Domain
**Purpose:**  
Represent the foundational support structures of the organism.

CARE (Circular Automated Response Engine) — represents the organism’s contextual support, response, orientation, and boundary-guidance layer.

**Responsibilities:**  
- Safety and wellbeing processes  
- Core operational rules  
- Escalation pathways  
- Organism‑level support  

**Interactions:**  
- People  
- Events  
- Service  
- Governance  

---

## 4. Cross‑Domain Governance
All domains are governed by:

- **Governance Engine** — rules, permissions, capabilities  
- **Corpus** — knowledge, lineage, documentation  
- **HAT (Human-AI Triad) Collaboration Layer** — interpretation, sequencing, and reflection across the organism; ensures coherent human‑led collaboration between Human, Coe, and Gee (the Founding HAT). HAT governs the build and interpretation layer, and is not a v1.0 product module.

Governance ensures consistency, safety, and coherence across domains.

---

## 5. Domain Interaction Diagram (Textual)

```
+---------------------------------------------------------------+
|                           People                              |
|   Profiles, Roles, Capabilities, Participation                |
+----------------------------+----------------------------------+
                             |
                             v
+---------------------------------------------------------------+
|                           Events                              |
|   Scheduling, Attendance, Logistics                           |
+----------------------------+----------------------------------+
                             |
                             v
+---------------------------------------------------------------+
|                          Locations                            |
|   Physical/Virtual Spaces, Availability                       |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                           Service                             |
|   Requests, Assignments, Contribution                         |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                           Exchange                            |
|   Value Flows, Recognition, Ledgers                           |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                           Education                           |
|   Courses, Learning Paths, Assessments                        |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                           Library                             |
|   Documents, Knowledge, Lineage                               |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                          Applications                         |
|   Forms, Submissions, Approvals                               |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                           Care/Core                           |
|   Safety, Wellbeing, Core Support                             |
+---------------------------------------------------------------+
```

---

## 6. Summary
The Domain Map defines the nine core domains of the INTEVIA organism and their responsibilities. These domains form the semantic backbone of the system and are governed by the Governance Engine, supported by the Corpus, and enhanced by the HAT Collaboration Layer.

