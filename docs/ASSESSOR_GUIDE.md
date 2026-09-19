# INTEVIA — a guide for funders and assessors

**v0.1 · 19 September 2026**

This document is written for someone with no prior exposure to INTEVIA who needs to judge what it is,
how far along it is, and whether its claims can be checked. It separates **what is built** from **what
is planned**, and says where the evidence for each sits.

---

## 1. What INTEVIA is

INTEVIA is an open-source platform for **human-governed organisational automation**, aimed at charities
and foundations. Its distinguishing idea is that the rules an organisation governs itself by should be
enforced by the system rather than relied upon as convention — and that a human being remains
accountable for every consequential decision the system takes.

It is built under a **Human-AI Triad** operating model: a **Human Governor** who decides, a **Vision
Chamber** that designs and reviews, and a **Making Engine** that implements. Roles do not merge, and
work carries a record of which role produced it.

## 2. Who it is for

- **Charities and foundations** that must show a regulator, a funder or a member that a decision
  followed the constitution it claims to follow.
- **Boards and trustees** who need automation without surrendering accountability for it.
- **Funders and assessors** who need to check a claim rather than accept it.

## 3. Maturity — stated plainly

**INTEVIA is in active development and is not production software.** Nothing in this repository should
be deployed to run a real organisation's governance today.

| Area | State |
|---|---|
| Database-level governance rules (S015 slice) | **Delivered**: migrations 0019–0024 install schema, constraints, triggers and functions, with a contract test suite |
| Verification and continuous integration | **Delivered**: a re-runnable verification route, an authoritative qualification decision, and retained evidence. See [docs/verification](verification/README.md) |
| Application object model alignment with the database | **Incomplete**: known divergence between the object model and migration 0022 |
| Identity-split guard | **Incomplete**: installed as a function that does not act |
| Second-layer correspondence verification | **Not implemented**; a design decision is outstanding on what it should check |
| Organism-as-actor | **Planned** |
| Application database privileges | **Known weakness**: the application still connects as a superuser |
| Web application, deployment, operations | **Planned** |

The items marked incomplete, not implemented, or a known weakness are carried findings from an
**independent external review** of the public repository in September 2026. They are listed with their
current status in [Report 2 §7](verification/reports/REPORT_2_IMPLEMENTATION_v1_0.md).

## 4. What can be checked today, and how

This is the part most relevant to an assessor.

**The claim.** The S015 database foundations pass a defined set of tests against a live PostgreSQL
server, on two independent environments, and that result is decided by a program separate from the one
that produces the evidence.

**How to check it without taking our word for it:**

1. Open the [latest CI run](https://github.com/alchemyofacceptance/INTEVIA/actions/runs/35436023009).
   The check passes only when the verification route exits successfully **and** the qualification gate
   accepts its records. Either alone is not enough.
2. Download the run's retained evidence artifact. It contains the records the decision consumed and
   the decision itself.
3. Read [`docs/verification/CONSUMER_REQUIREMENT_v1_2.md`](verification/CONSUMER_REQUIREMENT_v1_2.md),
   which states the decision rules, and run `.github/s015_gate.py` over the downloaded evidence
   yourself.
4. Or run the whole thing: `pip install -r requirements-verification.txt`, point it at a PostgreSQL
   server, and execute the route.

**Where the numbers come from.** 264 tests across eight control steps. The per-step counts, the exact
database and interpreter versions, and every identity are in
[Reports 3 and 4](verification/reports/REPORT_3_LOCAL_QUALIFICATION_v1_0.md).

## 5. The strongest thing to look at

Two of the eight control steps are **mutation controls**. Each deliberately replaces a database
protection with an unrelated one, runs the test that is supposed to demand the original refusal, and
**requires that test to fail by assertion** — naming the refusal it did not receive.

This distinguishes a test that checks a specific protection from one that would pass on any error. The
evidence for both controls, including the assertion text each target failed on, is in the retained
artifact and quoted in [Report 1 §3](verification/reports/REPORT_1_DESIGN_v1_0.md).

## 6. Provenance

Every substantive claim in this repository is tied to a commit identity, and the governance record
distinguishes what was designed, what was implemented, what was qualified, and what a human being
accepted. Those are four different things and are never merged.

Superseded documents are retained rather than deleted — for example the historical
`CONSUMER_REQUIREMENT_v1_0.md` beside the governing v1.2 — so a reader can see what changed and when.

Failed attempts are preserved too. Report 2 records defects found in the verification tooling itself,
including work that was written, reviewed and then withdrawn.

## 7. What this document does not claim

It does not claim INTEVIA is complete, deployable, secure against a determined attacker, or independently
reproduced. It does not claim the database foundations are free of defects; §3 lists known ones. It
claims that a defined, bounded set of properties is checked, that the check is re-runnable by a third
party, and that the evidence is retained.

---

*Questions, and the governance record behind any claim here, via the repository's issues.*
