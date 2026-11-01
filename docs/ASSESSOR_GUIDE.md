# INTEVIA — a guide for funders and assessors

**v0.3 · 1 November 2026 · current at main `f829d1b2b714cb282d96a273fbd0887eac9c3522`**
*Refreshes v0.2 of 19 September 2026 for the completed PKT-B work.*
*Supersedes v0.1, which described the audience too narrowly and described HAT incorrectly.*

---

## 1. What INTEVIA is for

Organisations run on rules — who belongs, who may decide what, how a decision is reached, what
authorises an activity, what happens when circumstances change. In most organisations those rules live
in constitutions, minutes, spreadsheets and habit, and the connection between a rule and an action taken
under it is reconstructed after the fact, if at all.

**INTEVIA connects an organisation's declared rules, its membership, its decisions and its activities
through a single inspectable history**, so that the basis on which something proceeded — or was refused
— can be examined rather than recalled.

Two things are held apart deliberately:

- **Human authority.** Consequential decisions are attributable to a named person. The system records
  who exercised authority, under which rule, and when.
- **AI assistance.** AI functions assist the work within declared bounds. **AI agreement is not a
  decision and never supplies authority.** INTEVIA is also designed to help organisations govern the AI
  assistance used within their own work — a design intention, not a capability that operates today.

## 2. Who it is for

**INTEVIA is being developed for organisations, including businesses, not-for-profit and charitable
bodies.** That states intended breadth. It does not claim existing customers, validated demand, or
suitability for every use.

Three relationships are distinct and should not be collapsed:

| | |
|---|---|
| **The adopting organisation** | adopts and operates INTEVIA, declares its rules within it, and is accountable for its own conduct |
| **Participating members** | belong, participate, exercise authority and may disagree — and are represented in the record accordingly |
| **The wider community** | may benefit from the organisation's activities without participating in its governance |

Charities and community groups make useful illustrations because their governance obligations are
explicit. **They illustrate the application; they do not define its scope.**

## 3. Making it concrete — Community Decision and Delivery

**This is an illustration, not a pilot.** The community, the people and the numbers are fictional. No
host organisation has been recruited, no activity has been run, and no impact is claimed.

A community of around 36 adult members, organised in six Circles, considers how to use two trial evening
sessions for adult learning and mutual support within a specimen £1,000 budget. Ordinary questions
follow: who belongs, how the decision is reached, who may authorise the spend, what evidence connects
the work to that authority, and what happens when circumstances change.

The design work examines events of a kind any organisation meets:

| Event | Why it matters to a member | Status |
|---|---|---|
| The last essential Coordinator leaves before the activity | coverage and responsibility must be reassessed explicitly, not assumed to continue | **design** |
| The decision rules change mid-discussion | participants need to know which rules governed what they took part in | **design** |
| Someone participates but disagrees | dissent should be represented, not flattened into consensus or silence | **design** |
| An evaluator's authority lapses | authority that has expired should not quietly keep being exercised | **design** |

**What is implemented today** includes the organisation and history foundations, plus recorded processes for membership, applications, founding, explicit Birthing, governance formation and offices. Birthing is the formal act that completes formation under the recorded conditions. [The PKT-B record](holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) explains what these actions do for people.

This does not make the fictional scenario a delivered programme. There is no complete website journey or permission for public use. Custom offices remain held. The next agreed work strengthens database safeguards before office user access, then reviews the value of tests.

The [public crosswalk](S015_PUBLIC_CROSSWALK.md) maps current claims to source files and keeps the earlier foundation history.

## 4. Maturity — stated plainly

**INTEVIA is in pre-alpha development toward v1.0 and is not production software.** Nothing here should
be used to run a real organisation's governance today.

| Area | State at the named base |
|---|---|
| Organisation and recorded-history foundations | Built; the earlier PKT-A records retain their original boundaries |
| Membership, applications, founding, Birthing and governance formation | Working behind-the-scenes processes; see the PKT-B record |
| Office definitions, nomination, consent and appointment | Built through C5; custom-office access remains held and additional database safeguards remain due |
| Automatic checks on GitHub | C5 recorded 126 product tests and 19 deliberate-defect checks; the old suite is archived, not run |
| The fictional scenario in §3 | An illustration for design, not an operational pilot or completed programme |
| Complete website, public deployment and release readiness | Not established |

The [September review](verification/reports/REPORT_2_IMPLEMENTATION_v1_0.md) remains a historical record, not the current status list. The [PKT-B Datacron §9](holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md#9-findings-carried-forward) identifies remaining work and restrictions. It does not claim that every earlier foundation finding is closed.

## 5. How the work is governed — HAT and HPCC

**HAT is the Human-AI Team**: the working practice behind INTEVIA, in which **one human holds authority
and ten AI functions are kept separate so they can check each other**. The human is not one of the ten.
Functions include coordination, design, implementation, and several independent examination seats whose
exposure to each other's findings is declared rather than assumed.

The point is not that AI output is trustworthy. **The practice assumes individual seats will err, and
structures the work so errors surface** — and the record enumerates each defect with who found it and
how long it survived. HAT is a working practice, not a product, a standard or a certification.

**HPCC** is the **HAT Practitioner Certification Curriculum**, planned training material for
practitioners. It is not delivered.

The governing account of HAT is
[`HAT_THE_HUMAN_AI_TEAM_v0_3.md`](governance/HAT_THE_HUMAN_AI_TEAM_v0_3.md) in the governance corpus.

The current development practice is **IDOPT (formerly IDOP)** — INTEVIA Development Operating Protocol and Thinking. Historical records and protocol filenames retain IDOP.

## 6. What an assessor can check today

**The current evidence.** The [C5 GitHub run](https://github.com/alchemyofacceptance/INTEVIA/actions/runs/41308978727) recorded 126 product tests and 19 deliberate-defect checks. These are governed by six test admission rules (TAR). The automatic checking sequence took 3 min 32 s; the complete job took 4 min 10 s.

The Human Governor set aside the old suite of about 1,800 tests on 29 October. The new route builds one database template and uses disposable copies, called clone mode. GitHub continuous integration (CI) is the evidence of record; local diagnosis does not replace it. [The verification guide](verification/README.md) explains what a pass means. A smaller suite is not proven to give equivalent coverage.

### September qualification — historical evidence

**The September claim, retained as history.** The S015 foundations pass a defined set of tests against a live PostgreSQL server, on two
independent environments, and the result is decided by a program separate from the one that produces the
evidence.

**How to inspect that historical evidence:**

1. Open the [CI run](https://github.com/alchemyofacceptance/INTEVIA/actions/runs/35436023009). The
   check passes only when the verification route exits successfully **and** the qualification gate
   accepts its records. Either alone is insufficient.
2. Download the run's retained evidence artifact — the records the decision consumed, and the decision.
3. Read [`CONSUMER_REQUIREMENT_v1_2.md`](verification/CONSUMER_REQUIREMENT_v1_2.md), the governing
   decision rules, and run `.github/s015_gate.py` over that evidence yourself.
4. The current route has changed. Use [the current verification guide](verification/README.md) for present requirements; a new run is not reproduction of the September tree.

**The strongest evidence in that September qualification.** Two of the eight control steps deliberately replaced a database
protection with an unrelated one and required the corresponding test to **fail by assertion**, naming the
refusal it did not receive. That distinguished a test that checked a specific protection from one that
would pass on any error. Both were evidenced in the retained artifact and described in
[Report 1 §3](verification/reports/REPORT_1_DESIGN_v1_0.md).

264 tests across eight control steps. Per-step counts, database and interpreter versions, and every
identity are in [Reports 3 and 4](verification/reports/REPORT_3_LOCAL_QUALIFICATION_v1_0.md).

## 7. The intended benefit, and how it would be examined

**The intended benefit is more practicable, accountable participation in shared organisational
activity** — people able to contribute while understanding the applicable rules, whose authority is being
exercised, and how their participation is represented; and the organisation's own treatment of its
members open to examination.

**This is a proposed benefit pathway. It is not evidence of achieved impact, validated demand, or a
partner commitment.**

There are two different evaluations, and passing the first does not establish the second: technical
evidence that an implemented mechanism behaves as specified, and later evidence that people find it
usable and beneficial. A later evaluation would need a willing host organisation, appropriate
accessibility and privacy arrangements, and a separately agreed scope. **None is asserted here, and no
beneficiary counts, savings or social-impact results are claimed.**

## 8. Provenance, and what this document does not claim

Every substantive claim in this repository is tied to a commit identity. The record distinguishes what
was designed, what was implemented, what was qualified, and what a human being accepted — four different
things, never merged. Superseded documents are retained rather than deleted, including the historical
`CONSUMER_REQUIREMENT_v1_0.md` beside the v1.2 gate contract, and failed attempts are preserved with them.

**Development history in brief.** A substantial design period preceded software development; the
documentation that period produced is the design record, not an overhead. The work is now design-then-
build: designs are reviewed adversarially before implementation, and defects found in a document cost
nothing compared with the same defect reaching a live organisation's records.

**This document does not claim** that INTEVIA is complete, deployable, secure against a determined
attacker, independently reproduced, or free of defects in its database foundations — the PKT-B record carries open work. It claims that a defined, bounded set of properties is checked, that the check is re-runnable by a
third party, and that the evidence is retained.

---

*The governance record behind any claim here is available via the repository's issues.*
**Final authority remains with Carmian Owen, Human Governor.**
