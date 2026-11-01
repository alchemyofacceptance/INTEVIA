# INTEVIA

*A human-governed platform through which an organisation's rules, membership, decisions and activities are connected in one inspectable history — with AI assistance kept inside declared bounds, and every consequential decision attributable to a named Human.*

## What INTEVIA is for

Organisations run on rules — who belongs, who may decide what, how a decision is reached, what authorises an activity, and what happens when circumstances change. In most organisations those rules live in constitutions, minutes, spreadsheets and habit, and the connection between a rule and an action taken under it is reconstructed afterwards, if at all.

**INTEVIA connects an organisation's declared rules, its membership, its decisions and its activities through a single inspectable history**, so the basis on which something proceeded — or was refused — can be examined rather than recalled.

Two things are held apart deliberately:

- **Human authority.** Consequential decisions are attributable to a named Human. The record carries who exercised authority, under which rule, and when.
- **AI assistance.** AI functions assist within declared bounds. **AI agreement is never a decision and supplies no authority.** INTEVIA is also designed to help organisations govern the AI assistance used within their own work — a design intention, not a capability that operates today.

**INTEVIA is being developed for organisations, including businesses, not-for-profit and charitable bodies.** That states intended breadth, not existing customers, validated demand, or suitability for every use. Three relationships stay distinct: the **adopting organisation** operates INTEVIA and is accountable for its own conduct; **participating members** belong, participate, exercise authority and may disagree; the **wider community** may benefit from the organisation's activities without participating in its governance.

### Current position

**Current position — 1 November 2026, main `02790ab8572181ba602ec1404eb8da90666c1f5f`.** INTEVIA is pre-alpha software being built toward v1.0. It now has working foundations for recording membership, applications, founding, Birthing, governance formation and office actions. Birthing is the explicit act that completes an organisation's formation under its recorded conditions. These are behind-the-scenes capabilities, not a complete website experience.

The [PKT-B record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) explains what was built and how it was checked. SR-H added the database safeguards required before office user access; custom offices still wait for entitlement-lifecycle assurance. The Human Governor closed PKT-B on 1 November 2026; its [closing record](docs/holocron/datacrons/D-S015-PKT-B-CLOSING-RECORD.md) lists where each unfinished item goes. See [the roadmap](ROADMAP.md#agreed-next-work-and-longer-term-direction) for the agreed next steps. This does not authorise public deployment or claim a completed product.

## Start here

| If you are… | Read |
|---|---|
| **new to INTEVIA** | [A guide for funders and assessors](docs/ASSESSOR_GUIDE.md) — what it is for, what exists today, what is still design work, what comes next |
| **checking the engineering** | [docs/verification](docs/verification/README.md) — a re-runnable verification route whose result is decided by a program separate from the one producing the evidence, with the evidence retained |
| **tracing foundations to commits** | [the public S015 crosswalk](docs/S015_PUBLIC_CROSSWALK.md) — PKT-A foundations and current PKT-B paths, with their evidence and limits |
| **unfamiliar with the terminology** | [the plain-language glossary](docs/public/GLOSSARY.md) — a navigation aid, not an authority source |
| **assessing evidence states** | [the Current Implementation Crosswalk](docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md) — navigation, not authority |

A passing check is evidence at the checked properties for the tested tree and environment. It is not review, external reproduction, or acceptance of the wider product.

## The layer this works at

An organisation that connects its rules to its actions has to answer the same questions about the
AI-assisted work inside it as about anything else. Model-level safety matters; this is the
complementary layer — the system of work around the model.

The question is whether AI-assisted work can show:

- who authorised it;
- what boundary constrained it;
- what changed and why;
- what evidence supported the change;
- where Human judgement re-entered;
- what remains unresolved;
- what can be corrected or recovered; and
- what claim can safely be made.

Governance that cannot be inspected becomes trust theatre. **INTEVIA does not remove the Human from
responsibility; it makes Human responsibility visible.**

## Repository posture

This public repository is an **internal pre-alpha build under active governance** for INTEVIA v1.0.

Its current centre is the organisation work through offices C5 and the SR-H safeguards at main `02790ab8572181ba602ec1404eb8da90666c1f5f`. PKT-B adds recorded actions to the earlier database foundations and history chain. The [packet record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) explains both the capabilities and the remaining restrictions.

It also contains implementation and test-definition paths from earlier work: Identity, Events, Services, Library, CARE, contribution lineage, profile effect, and a bounded Education Course-definition slice.

## What this repository currently lets you inspect

You can inspect the source, database changes and tests for the foundations below. Recorded founding and registration now have working code. That does not provide course delivery, a complete website, or authority to use the software for a real organisation.

Each unit is designed, reviewed and verified under the Human Governor's authority. **Designated** names the controlling design; **implemented** means built; **landed** means included on `main`; **accepted** records the applicable Human decision. A file being present proves neither verification nor acceptance. Under the standing 19 October ruling, the Human Governor committing or authorising commitment of a Datacron accepts that record. It does not accept unfinished product work. See the [PKT-B record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md).

That is materially beyond the repository's earlier runtime-seed stage. It is not evidence that every defined test currently passes, that every historical result has been independently reproduced, or that INTEVIA is deployed, release-ready, externally validated, or complete.

The repository currently records:

- source and migration paths associated with governed implementation;
- test-definition paths whose presence is distinct from test execution;
- **a verification route, its qualification decision and its retained evidence**, for the S015 foundations;
- Slice and packet Datacrons that preserve bounded evidence and lineage;
- constitutional, governance, architecture, HPCC (HAT Practitioner Certification Curriculum), and public-language surfaces; and
- exact Human decisions where a Human-issued source is present.

For the evidence states behind any capability statement, use the [Current Implementation Crosswalk](docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md). The crosswalk is navigation, not authority.

## Read by purpose

- **Developers:** begin with this README, then the [architecture entry point](architecture/README.md) and [implementation crosswalk](docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md).
- **Funders and assessors:** begin with [the assessor guide](docs/ASSESSOR_GUIDE.md), then the repository posture above, then inspect the crosswalk's separate fields for repository presence, test definitions, recorded execution, Human acceptance, unresolved findings, and non-claims.
- **Governance readers:** use the [root governance index](GOVERNANCE_INDEX.md), the [governance navigation index](docs/governance/governance-index.md), and the exact constitutional or Human-issued sources they identify.
- **AI assessors:** do not compress path presence, test definitions, recorded execution, independent reproduction, Human acceptance, unresolved deferral, and release readiness into one maturity label. The [PKT-B Datacron](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) and the earlier packet records carry their own status and limits; read those before the code.

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

At the current base, the [PKT-B census](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md#2-identity) counts repository text and distinguishes the archive from the live tests. It is a measure of volume, not protection.

**GitHub continuous integration (CI) is the evidence of record.** The SR-H run recorded 134 product tests and 19 deliberate-defect checks: 153 checks under the six test admission rules (TAR). The old suite of about 1,800 tests was archived on 29 October and is not run. The route now builds one database template and uses disposable copies, called clone mode. Its checking sequence took 4 min 12 s; the whole job took about 4 min 49 s. A smaller suite is not proven to give equivalent coverage. See [verification](docs/verification/README.md) for the route and evidence.

## Current governed implementation families

At main `f829d1b2b714cb282d96a273fbd0887eac9c3522`, repository paths are present for:

- governed contribution and knowledge-lineage foundations;
- Events, registration, attendance, and personal-event surfaces;
- Identity and direct self-registration foundations;
- Library resources and exact-version binding;
- Service foundations and governed service-activity orchestration;
- Event-resource linking and readback;
- governed service-submission profile effect;
- the bounded S014 Education Course-definition foundation;
- **the S015 Living Organism substrate** — migrations `0019` to `0021`: organism, circle and membership anchors; twelve append-only governed event chains with a canonical byte form, fingerprints and a bitemporal fold; 163 database guardians at that original landing. The PKT-A-2 record retains its issue-time status; and
- **the S015 INTEVIA Lineage-Chain (ILC) contract** — migration `0022`: actor states, parts and commitments, an identity-resolution table with an append-only severance ledger — with its September status and carried limitations retained in its Datacron.

Later foundation work also present on `main`:

- **migrations `0023` and `0024`** — the canonical form moved by forward migration, and the L1 and parts preimages built as compact canonical text;
- **the S015 verification route, its qualification decision and evidence rules** — subsequently updated for the new suite and database copies. Its reports, decision rules and retained evidence are at [docs/verification](docs/verification/README.md).

PKT-B then added organisation membership and application decisions; founding and registration; explicit Birthing and governance formation; recorded request handling; subscription feature access; and office definitions, consent and actions. [The current crosswalk](docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md#9-pkt-b-organisations-membership-and-offices) maps those claims to code. [The Datacron](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) records their limits and the Human decisions.

These are bounded implementation families, not claims of complete modules or operational product capability. S014 does not establish curriculum delivery, class delivery, enrolment, assessment, certification, educator qualification, payment, or launched cohorts or Circles; its Datacron keeps `MAT-S014-01` visibly deferred. The earlier S015 packet records retain their September limitations. They are not a current status list for PKT-B. The current Datacron names remaining office safeguards, custom-office restrictions and other follow-up work; no release or operational use is established.

## HPCC, Organisms, and Circles

HPCC is the Human capability pathway being developed around governed Human–AI work. Repository materials support formation and curriculum design; they do not establish an active certification programme or a launched Circle.

Within INTEVIA, an Organism is an organisation and a Circle is a sub-unit, team, or grouping within it. The schema for Organisms, Circles and their memberships is present on `main` since S015, with every governed act on them recorded on an append-only chain. No Organism or Circle is claimed here as created, launched, populated, or operational.

## Repository map

```
INTEVIA/
├── README.md                         public front door
├── ROADMAP.md                        current direction and Human decision gates
├── CHANGELOG.md                      selected factual repository events
├── GOVERNANCE_INDEX.md               root governance navigation
├── manage.py                         Django management entry point
├── run.py                            bounded local run entry point
├── .github/                          CI workflow, qualification gate and its controls
├── core/                             Django models, migrations, views and commands
├── verification/                     the S015 verification route and its self-tests
├── src/intevia/                      implementation services and command surfaces
├── tests/                            current live test files
├── vectors/                          the S015 canonical-form rule and its fixed test vectors
├── architecture/                     conceptual architecture and boundaries
├── docs/ASSESSOR_GUIDE.md            guide for funders and assessors
├── docs/S015_PUBLIC_CROSSWALK.md     PKT-A history and current PKT-B paths
├── docs/verification/                decision rules, qualification reports and navigation
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

```
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

```
pip install -r requirements-postgresql.txt
```

To run the S015 verification route, install its own requirements instead:

```
pip install -r requirements-verification.txt
```

### Required environment

| Variable                    | Required | Notes                                                   |
| --------------------------- | -------- | ------------------------------------------------------- |
| `INTEVIA_DATABASE_ENGINE`   | yes      | Exactly `postgresql`, lowercase. Any other value raises |
| `INTEVIA_POSTGRES_DB`       | yes      |                                                         |
| `INTEVIA_POSTGRES_USER`     | yes      |                                                         |
| `INTEVIA_POSTGRES_PASSWORD` | yes      |                                                         |
| `DJANGO_SECRET_KEY`         | yes      | Generate with `secrets.token_urlsafe()`                 |
| `INTEVIA_POSTGRES_HOST`     | no       | Defaults to `127.0.0.1`                                 |
| `INTEVIA_POSTGRES_PORT`     | no       | Defaults to `5432`                                      |

Django will not start without these. Missing configuration raises `ImproperlyConfigured` rather than falling back to another database. Management
commands that only read files, such as `makemigrations --check`, still need them,
because settings must load before any command runs.

Do not commit any of these values or a local `.env` file. The Django test command
uses isolated test settings and does not reuse an operational key.

## Development approach

INTEVIA is developed through bounded, reviewed changes that preserve Human authority and an inspectable record. The current development practice is **IDOPT (formerly IDOP)** — INTEVIA Development Operating Protocol and Thinking. Historical records and protocol filenames retain IDOP.

See [Current Direction and Decision Gates](ROADMAP.md), the [selected change record](CHANGELOG.md), and the [governance index](GOVERNANCE_INDEX.md).

## Licensing

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Current non-claims

This repository does not claim finished-product maturity, deployment, production readiness, scientific validation, universal productivity improvement, active certification, launched cohorts or Circles, a populated Organism, or that INTEVIA has solved AI governance.

## Keeper

> INTEVIA helps professionals use AI responsibly without losing structure, evidence, oversight, or Human judgement. The missing layer is governed Human–AI work.
>
> **This is how professionals learn to govern AI, not just use it.**
