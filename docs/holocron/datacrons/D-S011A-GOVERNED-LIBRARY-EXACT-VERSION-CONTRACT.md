# D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT

## Status

```text
Type: HOLOCRON Datacron Record
Purpose: S011-A lineage preservation
Phase: Post-implementation acceptance; pre-closure decision
Status: Candidate lineage record
Runtime effect: None
```

This candidate records the accepted S011-A implementation and its evidence. It
does not close S011-A, complete the S011 umbrella capability, activate or deploy
policy, publish an operational capability, or authorize S011-B implementation.

## Identity

```text
Datacron: D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT
Slice: S011-A - Governed Library Exact-Version Linkability, Authority, and Disclosure Contract
Domain owner: LIBRARY
Human Governor: Carmian Owen
Repository: https://github.com/alchemyofacceptance/INTEVIA.git
Branch: main
Implementation commit: 6bef24be3a20d88bbfd4c7cdbcd5ec6dadce20a4
Preserved whitepaper-only commit: 963a6a805de3ce0aaa448bc43138ef4192129f97
Fail-closed correction and accepted HEAD: 6c4ca0a315838a2f8613d914bf204b7e57162783
Policy: policy:LIB-EXACT-VERSION-PREALPHA-001:v1
Environment: internal-pre-alpha
```

Accepted lineage:

```text
6bef24be3a20d88bbfd4c7cdbcd5ec6dadce20a4
  -> 963a6a805de3ce0aaa448bc43138ef4192129f97
  -> 6c4ca0a315838a2f8613d914bf204b7e57162783
```

## Evidence Classification

### Human-Authored Decisions

Carmian Owen authored the S011-A architecture and policy boundaries and later
accepted the corrected implementation at the exact receiver-visible HEAD above.
The acceptance exercised implementation acceptance only. The policy instrument
remains `DRAFT`, `NOT ACTIVE`, `NOT DEPLOYED`, and `NOT ENABLED`. S011-A closure,
policy activation, deployment, publication, and S011-B implementation authority
remain unexercised.

### Repository-Verified Facts

The accepted repository contains the bounded contract and policy implementation
in:

```text
src/intevia/services/library_exact_version_contract.py
src/intevia/services/library_exact_version_policy.py
governance/policies/LIB-EXACT-VERSION-PREALPHA-001-v1.md
```

S011-A created no model or migration. It did not import EVENT into either
runtime module, create a generic policy engine, activate a binding provider, or
add routes, presentation, operational bindings, or durable viewer history.

### Independently Executed Evidence

The post-correction acceptance review recorded:

```text
Focused S011-A and Node suite: 48/48 passed
Preserved named regression set: 71/71 passed
Targeted PostgreSQL guardians: 5/5 passed
Decisive complete PostgreSQL suite: 336/336 passed
Migration drift: none
Django system checks: no issues
PostgreSQL: 16.14, official postgres:16 image
```

The later Human qualification independently executed the complete focused
S011-A surface against isolated PostgreSQL 16.14: `53/53` passed in `7.156s`.
It used loopback-only networking, tmpfs storage, a process-local credential,
and verified teardown with no container, volume, selector, or test-data residue.

### Packaged And Prior-Session Attestations

The acceptance review packet is exactly 16,867 bytes with SHA-256
`384b3fcdc222ded702df75c51d5a3ad992030a94e88f70b61592361a44752faa`.
The S011-A qualification execution bundle is exactly 337,209 bytes with SHA-256
`42cc5886fdae6ebf7609949480a10fea1374271786415a170b76fd74b8e2e005`.
These sources were retrieved by exact Drive name and ID, reconciled against
Drive metadata and detached records, and verified through their Manifests.

## Governed Contract

S011-A owns three independent determinations:

| Axis | Positive | Valid negative | Unresolved |
|---|---|---|---|
| Action authority | `QUALIFIED` | `REFUSED` | `HOLD` |
| Exact-version linkability | `LINKABLE` | `NOT_LINKABLE` | `HOLD` |
| Exact-version disclosure | `CONTENT_VISIBLE` | `HIDDEN` | `HOLD` |

The governed actions are `CREATE`, `SUPERSEDE_VERSION`, and `AMEND_PURPOSE`.
Each requires current explicit authority for the exact actor, action, resource,
and version. Ownership, role, authentication, staff/superuser flags, a reference,
or a prior receipt creates no authority. Staff and superuser credentials grant
no bypass.

The owning `LibraryResource` state produces:

| State | Linkability | Qualifying-viewer disclosure |
|---|---|---|
| `PUBLISHED` | `LINKABLE` | `CONTENT_VISIBLE` |
| `DRAFT` | `NOT_LINKABLE` | `HIDDEN` |
| `DEPRECATED` | `NOT_LINKABLE` | `CONTENT_VISIBLE` |
| `ARCHIVED` | `NOT_LINKABLE` | `HIDDEN` |
| Missing, malformed, inconsistent, or unverifiable | `HOLD` | `HOLD` |

The exact version is never substituted by current, latest, predecessor, or
successor state. `HOLD` is fail-closed unresolved truth, not permission and not
an interchangeable negative.

## Binding Correction And Accepted Meaning

The correction at `6c4ca0a315838a2f8613d914bf204b7e57162783` requires
`type(snapshot.decision) is BindingDecision` at the shared provider boundary.
A malformed decision now yields provider `UNAVAILABLE` and policy `HOLD`; it
cannot reach `QUALIFIED`. Exact valid `DENY` remains `REFUSED`. The Human
Governor expressly accepted this corrected fail-closed meaning.

## Evidence Identity And Freshness

Determinations are immutable current-evaluation value objects. The reference
form is `lib-determination:sha256:<64-lowercase-hex>`, computed over the complete
domain-separated canonical payload under schema
`intevia.s011a.library-determination` version `1` and canonicalization
`RFC8785+INTEVIA-S011A-v1`.

The digest proves byte-identical evidence identity only. It does not prove
truth, secrecy, currency, authorization, continuing permission, or possession
rights. Viewer identity and access epoch are bound into disclosure evidence.
A detached or historical determination is not a reusable permission.

For a consequential action, LIBRARY truth must be evaluated in the same
transaction after locking the owning resource and before the consuming EVENT
mutation. The bounded scope is single-use, non-serializable, connection-bound,
and invalid after transaction exit.

## S011-B Boundary

S011-A does not own Event relationship or purpose meaning. A future S011-B may
consume the public Library contract only after separate authority. EVENT must
own the relationship-and-purpose disclosure gate and supply separate current
EVENT authority evidence. S011-B must not reinterpret the Library state table,
manufacture Library determinations, treat a digest as permission, infer purpose
from Library evidence, or weaken non-disclosure.

S011-B may preserve exact Library evidence used by a consequential mutation,
including the protected exact `LibraryResourceVersion` reference and canonical
receipt identity, but it must preserve the separate authority, linkability, and
disclosure axes and revalidation boundaries.

## Non-Disclosure And Retained Gap

`HIDDEN`, `REFUSED`, `HOLD`, and not-found presentation must remain
non-revealing. Non-positive results must not disclose content, state, existence,
counts, current-version identity, lineage, policy basis, binding status, viewer
qualification, privileged-account treatment, or differentiated public errors.

The retained retention/redaction gap blocks erasure claims, public or external
use, personal-data-bearing durable viewer history, production activation, and
any claim that S011-A supplies a complete retention or redaction model. Ordinary
read-time disclosure remains ephemeral. Durable viewer history requires separate
Human authority and explicit privacy purpose, retention, access, and redaction
rules.

## Deferrals And Prohibited Interpretations

- Policy activation and operational binding composition are deferred.
- Deployment, publication, external use, and production claims are deferred.
- S011-A closure remains a separate Human decision.
- The S011 umbrella capability is not complete.
- S011-B implementation is not authorized by this record or by implementation acceptance.
- This Datacron has no runtime, policy, deployment, publication, or closure effect.
- Full-suite success does not turn every regression into an S011-A domain claim.
- Packaged attestations are distinguished from receiver-direct repository and execution evidence.

## Custody

The controlling closeout instruction is exactly 15,098 bytes with SHA-256
`4c094aeeb3011be60c77a1ba4eef28c38d5ec117f6d1ef294bac419ee04933fb`.
The promoted IDOP v0.9 archive is exactly 338,086 bytes with SHA-256
`ef92e4e3c0a0979c1c2ef2c14710c657ed6c750726e133b1a766f26a46e2cbef`.
Its Human promotion record explicitly activates v0.9 while retaining immutable
predecessors. Source bytes, extracted lineage, evidence, and outputs remain in
the authorized closeout run directory pending separately authorized cleanup.

## Exact Next Governed Step

Carmian Owen reviews the closure qualification packet, this committed Datacron,
and the S011-B inherited baseline. If satisfied, the Human Governor may exercise
a separate explicit S011-A closure decision using the drafted marker. That
decision does not activate policy, deploy or publish capability, complete the
S011 umbrella, or authorize S011-B implementation unless separately stated.