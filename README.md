# INTEVIA

*INTEVIA is a governed Human–AI collaboration system for making AI-mediated work inspectable, accountable, correctable, and recoverable.*

> **This is how professionals learn to govern AI, not just use it.**

## Why INTEVIA exists

Model-level safety matters. INTEVIA works at a different, complementary layer: the system of work around AI.

It asks whether AI-assisted work can show:

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

This public repository is an **internal pre-alpha build under active governance** for INTEVIA v1.0. It now contains implementation and test-definition paths associated with governed foundations across Identity, Events, Services, Library, CARE, contribution lineage, profile effect, and a bounded Education Course-definition slice.

## What this repository currently lets you inspect

You can examine source, migrations, and test-definition paths for the bounded foundations listed below. You cannot enrol learners, deliver classes, run production services, or treat any capability as accepted, released, or operational. Human acceptance of the latest Slice remains pending.

That is materially beyond the repository's earlier runtime-seed stage. It is not evidence that every defined test currently passes, that every historical result has been independently reproduced, or that INTEVIA is deployed, release-ready, externally validated, or complete.

The repository currently records:

- source and migration paths associated with governed implementation;
- test-definition paths whose presence is distinct from test execution;
- Slice Datacrons that preserve bounded evidence and lineage;
- constitutional, governance, architecture, HPCC, and public-language surfaces; and
- exact Human decisions where a Human-issued source is present.

For the evidence states behind any capability statement, use the [Current Implementation Crosswalk](docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md). The crosswalk is navigation, not authority.

## Read by purpose

- **Developers:** begin with this README, then the [architecture entry point](architecture/README.md) and [implementation crosswalk](docs/architecture/CURRENT_IMPLEMENTATION_CROSSWALK.md).
- **Funders and assessors:** begin with the repository posture above, then inspect the crosswalk's separate fields for repository presence, test definitions, recorded execution, Human acceptance, unresolved findings, and non-claims.
- **Governance readers:** use the [root governance index](GOVERNANCE_INDEX.md), [governance navigation index](docs/governance/governance-index.md), and exact constitutional or Human-issued sources they identify.
- **AI assessors:** do not compress path presence, test definitions, recorded execution, independent reproduction, Human acceptance, unresolved deferral, and release readiness into one maturity label.

## What INTEVIA is

INTEVIA is a human-centred organisational evolution platform and governance system for AI-mediated work. It is designed to help Humans and organisations preserve intention, meaning, evidence, authority, accountability, correction, lineage, and ratification.

INTEVIA does not remove the Human from responsibility. It makes Human responsibility visible.

## Human–AI Triad operating model

INTEVIA is developed through a Human–AI Triad, or HAT. HAT separates role surfaces so Human authority, meaning continuity, implementation support, and review do not collapse into one uninspected output stream.

- **Human Governor** — authority, judgement, execution accountability, and final ratification.
- **Vision Chamber** — meaning, synthesis, doctrine, boundary interpretation, and public/private coherence.
- **Making Engine** — implementation, mutation, repository-facing execution, and technical verification.

Delegation Governance makes authority visible when AI-mediated work continues beyond a single prompt-response moment. Review valves return consequential work to Human judgement before public claims, commits, irreversible changes, or ratification.

> **Delegation without governance is authority drift.**

## Evidence boundary

Repository evidence must be read by class:

- a path at an exact ref establishes repository presence only;
- test code establishes test-definition presence only;
- a cited run establishes only the recorded result, scope, actor, date, and repository state it names;
- a Slice record establishes only its declared technical boundary;
- Human acceptance or closure requires an exact Human-issued source; and
- documentation creates no implementation, validation, acceptance, promotion, release, or publication authority.

The static inventory at the qualified documentation-review baseline found **84 tracked paths named `test_*.py`** and **663 textual Python test-definition matches** using the pattern `^\s*(async\s+)?def\s+test_`. These are different measures. The value `663` also appears in S014 as a recorded full-regression execution result; numerical equality does not mean the static definitions and the executed selection are the same evidence object.

## Current governed implementation families

At commit `1aed0f4da88209d5298e40867b08505661cfd451`, repository paths are present for:

- governed contribution and knowledge-lineage foundations;
- Events, registration, attendance, and personal-event surfaces;
- Identity and direct self-registration foundations;
- Library resources and exact-version binding;
- Service foundations and governed service-activity orchestration;
- Event-resource linking and readback;
- governed service-submission profile effect; and
- the bounded S014 Education Course-definition foundation.

These are bounded implementation families, not claims of complete modules or operational product capability. S014 does not establish curriculum delivery, class delivery, enrolment, assessment, certification, educator qualification, payment, or launched cohorts. Its Datacron keeps `MAT-S014-01` visibly deferred to IDOP v0.9.6.

## HPCC and cohorts

HPCC is the Human capability pathway being developed around governed Human–AI work. Repository materials support formation and curriculum design; they do not establish an active certification programme or a launched cohort.

Organism Cohorts are a future bounded setting for practising, observing, and improving governed Human–AI work. No launch claim is made here.

## Repository map

```text
INTEVIA/
├── README.md                         public front door
├── ROADMAP.md                        current direction and Human decision gates
├── CHANGELOG.md                      selected factual repository events
├── GOVERNANCE_INDEX.md               root governance navigation
├── manage.py                         Django management entry point
├── run.py                            bounded local run entry point
├── core/                             Django models, migrations, views, and commands
├── src/intevia/                      implementation services and command surfaces
├── tests/                            tracked test-definition paths
├── architecture/                     conceptual architecture and boundaries
├── docs/architecture/                current implementation crosswalk
├── docs/governance/                  live governance navigation and standards
├── docs/holocron/datacrons/          Slice lineage records
├── docs/evidence/                    governed evidence surfaces
├── docs/public/                      public-safe articulation surfaces
└── whitepapers/                      longer-form public reasoning
```

Longer-form public reasoning: [Whitepaper WHY — Human Judgement in the Age of AI Acceleration](whitepapers/WHITEPAPER_WHY_V2_0_HUMAN_JUDGEMENT_AI_ACCELERATION.md).

## Local Django configuration

Local Django commands require `DJANGO_SECRET_KEY` in the process environment. Generate an independent local value with Python's `secrets.token_urlsafe()` and set it before starting Django. Do not commit the generated value or a local `.env` file. The Django test command uses isolated test settings and does not reuse an operational key.

## Development approach

INTEVIA is developed through governed mutation: bounded change that preserves Human authority, evidence, lineage, and inspection discipline.

See [Current Direction and Decision Gates](ROADMAP.md), the [selected change record](CHANGELOG.md), and the [governance index](GOVERNANCE_INDEX.md).

## Licensing

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Current non-claims

This repository does not claim finished-product maturity, deployment, production readiness, scientific validation, universal productivity improvement, active certification, launched cohorts, or that INTEVIA has solved AI governance.

## Keeper

> INTEVIA helps professionals use AI responsibly without losing structure, evidence, oversight, or Human judgement. The missing layer is governed Human–AI work.
