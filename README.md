# INTEVIA

*INTEVIA is an AI-assisted human-governed constitutional and operational automation platform for making AI-mediated work inspectable, accountable, correctable, and recoverable.*

> **This is how professionals learn to govern AI, not just use it.**

## Why INTEVIA exists

Model-level safety matters. INTEVIA works at a different, complementary layer: the system of work around AI.

If you have ever wondered who is actually making the decisions in AI-assisted work—and how anyone could later inspect what happened—INTEVIA is being built around that question.

More specifically, it asks whether AI-assisted work can show:

- who authorised it;
- what boundary constrained it;
- what changed and why;
- what evidence supported the change;
- where Human judgement re-entered;
- what remains unresolved;
- what can be corrected or recovered; and
- what claim can safely be made.

Governance that cannot be inspected becomes trust theatre.

## Repository posture

This public repository is an **internal pre-alpha build under active governance** for INTEVIA v1.0. It contains implementation and test-definition paths associated with governed foundations across Identity, Events, Services, Library, CARE, contribution lineage, profile effect, a bounded Education Course-definition slice, and — since September 2026 — the **Living Organism substrate** (organism, circle and membership anchors with an append-only recorded chain and bitemporal fold) and the **INTEVIA Lineage-Chain (ILC)** contract laid onto that chain.

## What this repository currently lets you inspect

You can examine source, migrations, and test-definition paths for the bounded foundations listed below. You cannot enrol learners, deliver classes, run production services, populate an Organism, or treat any capability as accepted, released, or operational.

Every landing is ruled, designed and verified under the Human Governor's authority. The programme distinguishes four states — **designated** (the controlling design object is named), **implemented** (built and verified against it), **landed** (on `main`), and **accepted** (a separate, direct Human act) — and each Slice or packet Datacron records which of them its work has reached. Repository presence is the first three at most; it is never the fourth.

That is materially beyond the repository's earlier runtime-seed stage. It is not evidence that every defined test currently passes, that every historical result has been independently reproduced, or that INTEVIA is deployed, release-ready, externally validated, or complete.

The repository currently records:

- source and migration paths associated with governed implementation;
- test-definition paths whose presence is distinct from test execution;
- Slice and packet Datacrons that preserve bounded evidence and lineage;
- constitutional, governance, architecture, HPCC (HAT Practitioner Certification Curriculum), and public-language surfaces; and
- exact Human decisions where a Human-issued source is present.

For the evidence states behind any capability statement, use the [Current Implementation Crosswalk](docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md). The crosswalk is navigation, not authority.

## Read by purpose

New to INTEVIA terminology? Use the [plain-language glossary](docs/public/GLOSSARY.md) alongside this page. The glossary is a navigation aid, not an authority source.

- **Developers:** begin with this README, then the [architecture entry point](architecture/README.md) and [implementation crosswalk](docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md).
- **Funders and assessors:** begin with the repository posture above, then inspect the crosswalk's separate fields for repository presence, test definitions, recorded execution, Human acceptance, unresolved findings, and non-claims.
- **Governance readers:** use the [root governance index](GOVERNANCE_INDEX.md), [governance navigation index](docs/governance/governance-index.md), and exact constitutional or Human-issued sources they identify.
- **AI assessors:** do not compress path presence, test definitions, recorded execution, independent reproduction, Human acceptance, unresolved deferral, and release readiness into one maturity label. The two most recent Datacrons carry their own status blocks and non-claims; read those before the code.

## What INTEVIA is

INTEVIA is a human-centred organisational evolution platform and governance system for AI-mediated work. It is designed to help Humans and organisations preserve intention, meaning, evidence, authority, accountability, correction, lineage, and ratification.

INTEVIA does not remove the Human from responsibility. It makes Human responsibility visible.

## The Human-AI Team (HAT)

INTEVIA is developed through the Human-AI Team, or HAT: **one Human and ten AI functions, separated so they can check each other.** The Human is not one of the ten. Consequential decisions are attributable to a named Human, and **AI agreement never constitutes a decision.**

- **Authority** — the *Human Governor*: purpose, scope, rulings, reserved questions, acceptance. Final authority.
- **Coordination and construction** — *Vision Chamber* (understanding, coordination, briefs and carriers, the whole open set put to the Human), *Lead Designer* (constructs the design object; separated from coordination because combining them hides errors), *Making Engine* (bounded implementation and empirical work, with only the authority actually granted).
- **Examination** — *Reviewer* (correspondence, coherence, completeness; leaves repair to the builder), *Adversator I* (sustained adversarial examination), *Adversator II and III* (independent attack with other returns withheld; their value depends on what they were shown and when, which is declared).
- **Custody, retrieval and production** — *Drive Custodian* (places durable objects, halts on any difference from the instruction), *Curator* (finds and reports what exists, without ruling on it), *Scriptorium* (renders governed objects for publication; corrects within declared limits and reports every change; never decides what may be claimed).

The practice is written at [HAT — The Human-AI Team v0.3](docs/governance/HAT_THE_HUMAN_AI_TEAM_v0_3.md), including the cycle (construction → review → correction → review closure → adversarial examination → Human ruling), the rule that a seat may not decide which reserved questions reach the Human, and the evidence for why separated functions catch what a single seat cannot see in itself. HAT is a working practice, not a product, not a standard, and not a claim that AI output is trustworthy — the opposite: it assumes individual seats will err and structures the work so errors surface.

> **Delegation without governance is authority drift.**

## Evidence boundary

Repository evidence must be read by class:

- a path at an exact ref establishes repository presence only;
- test code establishes test-definition presence only;
- a cited run establishes only the recorded result, scope, actor, date, and repository state it names;
- a Slice or packet record establishes only its declared technical boundary;
- Human acceptance or closure requires an exact Human-issued source; and
- documentation creates no implementation, validation, acceptance, promotion, release, or publication authority.

The static inventory at the qualified documentation-review baseline, by the method stated in the crosswalk, found **97 tracked paths matched by `git ls-files 'test_*.py' '**/test_*.py'`** — 94 files whose own name begins `test_`, plus three matched through the directory `intevia/test_postgresql_backend/` — and **805 textual Python test-definition matches** using the pattern `^\s*(async\s+)?def\s+test_`. These are different measures from any recorded execution count; numerical equality between a static inventory and an executed selection does not make them the same evidence object.

## Current governed implementation families

At commit `4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`, repository paths are present for:

- governed contribution and knowledge-lineage foundations;
- Events, registration, attendance, and personal-event surfaces;
- Identity and direct self-registration foundations;
- Library resources and exact-version binding;
- Service foundations and governed service-activity orchestration;
- Event-resource linking and readback;
- governed service-submission profile effect;
- the bounded S014 Education Course-definition foundation;
- **the S015 Living Organism substrate** — migrations `0019` to `0021`: organism, circle and membership anchors; twelve append-only governed event chains with a canonical byte form, fingerprints and a bitemporal fold; 163 database guardians — designated, implemented and landed; not accepted; and
- **the S015 INTEVIA Lineage-Chain (ILC) contract** — migration `0022`: actor states, parts and commitments, an identity-resolution table with an append-only severance ledger — designated, implemented and landed; not accepted; carrying named limitations into the next packet, recorded in its Datacron.

These are bounded implementation families, not claims of complete modules or operational product capability. S014 does not establish curriculum delivery, class delivery, enrolment, assessment, certification, educator qualification, payment, or launched cohorts or Circles; its Datacron keeps `MAT-S014-01` visibly deferred. S015 does not establish a populated Organism, second-layer commitment verification, an Organism acting as an actor, or a production database role; its two Datacrons name each limitation and what is carried forward.

## HPCC, Organisms, and Circles

HPCC is the Human capability pathway being developed around governed Human–AI work. Repository materials support formation and curriculum design; they do not establish an active certification programme or a launched Circle.

Within INTEVIA, an Organism is an organisation and a Circle is a sub-unit, team, or grouping within it. The schema for Organisms, Circles and their memberships is present on `main` since S015, with every governed act on them recorded on an append-only chain. No Organism or Circle is claimed here as created, launched, populated, or operational.

## Repository map

```text
INTEVIA/
├── README.md                         public front door
├── ROADMAP.md                        current direction and Human decision gates
├── CHANGELOG.md                      selected factual repository events
├── GOVERNANCE_INDEX.md               root governance navigation
├── manage.py                         Django management entry point
├── run.py                            bounded local run entry point
├── core/                             Django models, migrations, views, commands, and contract tests
├── src/intevia/                      implementation services and command surfaces
├── tests/                            tracked test-definition paths
├── vectors/                          the S015 canonical-form rule and its fixed test vectors
├── architecture/                     conceptual architecture and boundaries
├── docs/architecture/                current implementation crosswalk
├── docs/constitution/                ratified constitutional core and doctrines
├── docs/governance/                  live governance navigation, standards, and HAT practice
├── docs/superseded/                  superseded documentation, retained as lineage
├── docs/holocron/datacrons/          Slice and packet lineage records
├── docs/evidence/                    governed evidence surfaces
├── docs/public/                      public-safe articulation surfaces
├── governance/policies/              pre-alpha policy instruments
├── scripts/                          documentation-authoring tooling
├── INTEVIA_BACKUP/                   backup invocation protocols
├── v1.0_hardening/                   hardening notes and external-system references
└── whitepapers/                      longer-form public reasoning
```

Longer-form public reasoning: [Whitepaper WHY — Human Judgement in the Age of AI Acceleration](whitepapers/WHITEPAPER_WHY_V2_0_HUMAN_JUDGEMENT_AI_ACCELERATION.md).

## Local Django configuration

INTEVIA requires **PostgreSQL**. SQLite is not a development substrate.

The governed database constraints — deferrable constraint triggers, partial
unique indexes, and check constraints calling functions — are PostgreSQL-specific
and have no SQLite equivalent. A passing test run against SQLite would show that
the Python path is clean while showing nothing about whether the database itself
refuses anything.

### Provisioning a local instance

Verified against PostgreSQL 17.10.

```bash
docker volume create intevia_pgdata

# Set POSTGRES_PASSWORD in your shell first; passing the name without a value
# keeps it out of the process listing.
docker run -d --name intevia-postgres \
	-e POSTGRES_PASSWORD \
	-e POSTGRES_USER=intevia \
	-e POSTGRES_DB=intevia \
	-v intevia_pgdata:/var/lib/postgresql/data \
	-p 127.0.0.1:5432:5432 \
	--restart unless-stopped \
	postgres:17
```

The port binds to `127.0.0.1` only and is not reachable from other machines.

Install the PostgreSQL driver:

```bash
pip install -r requirements-postgresql.txt
```

### Required environment

| Variable | Required | Notes |
|---|---|---|
| `INTEVIA_DATABASE_ENGINE` | yes | Exactly `postgresql`, lowercase. Any other value raises |
| `INTEVIA_POSTGRES_DB` | yes | |
| `INTEVIA_POSTGRES_USER` | yes | |
| `INTEVIA_POSTGRES_PASSWORD` | yes | |
| `DJANGO_SECRET_KEY` | yes | Generate with `secrets.token_urlsafe()` |
| `INTEVIA_POSTGRES_HOST` | no | Defaults to `127.0.0.1` |
| `INTEVIA_POSTGRES_PORT` | no | Defaults to `5432` |

Django will not start without these. Missing configuration raises
`ImproperlyConfigured` rather than falling back to another database. Management
commands that only read files, such as `makemigrations --check`, still need them,
because settings must load before any command runs.

Do not commit any of these values or a local `.env` file. The Django test command
uses isolated test settings and does not reuse an operational key.

## Development approach

INTEVIA is developed through governed mutation: bounded change that preserves Human authority, evidence, lineage, and inspection discipline. The operating practice within the HAT is IDOP; its current version is named in each Datacron.

See [Current Direction and Decision Gates](ROADMAP.md), the [selected change record](CHANGELOG.md), and the [governance index](GOVERNANCE_INDEX.md).

## Licensing

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Current non-claims

This repository does not claim finished-product maturity, deployment, production readiness, scientific validation, universal productivity improvement, active certification, launched cohorts or Circles, a populated Organism, or that INTEVIA has solved AI governance.

## Keeper

> INTEVIA helps professionals use AI responsibly without losing structure, evidence, oversight, or Human judgement. The missing layer is governed Human–AI work.
