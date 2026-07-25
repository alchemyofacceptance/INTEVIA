# D-S011B-GOVERNED-EVENT-RESOURCE-LINKING-AND-READBACK

## Status

```text
Type: HOLOCRON Datacron Record
Purpose: S011-B and S011 umbrella lineage preservation
Phase: Post-implementation acceptance; pre-Datacron-acceptance and pre-closure decisions
Status: Candidate lineage record
Runtime effect: None
```

This candidate records the Human-accepted S011-B implementation, its
independent inspection, bounded correction, and final requalification. It does
not accept itself, close S011-B, complete the S011 umbrella capability, activate
policy, deploy or publish INTEVIA, or authorize external use.

## Identity

```text
Datacron: D-S011B-GOVERNED-EVENT-RESOURCE-LINKING-AND-READBACK
Umbrella capability: S011 - Governed Event Resource Linking and Readback
Slice: S011-B - Governed Event Resource Linking and Readback
Domain owner: EVENT
Human Governor: Carmian Owen
Repository: https://github.com/alchemyofacceptance/INTEVIA.git
Branch: main
S011-A accepted implementation: 6c4ca0a315838a2f8613d914bf204b7e57162783
S011-A Datacron commit: c3ce6c4b15501d9f16208213a550e75e9f032695
S011-B original implementation: c77cf1d5bc8e44c901af3cef64a30ea5e82e79a4
S011-B corrected and Human-accepted implementation: b9c499c7987ec10fb6b559fd1f5d07513934f17c
S011-B corrected tree: 5d2c18d48d3f5387d6809bce49acd5226b89d8e1
Policy: policy:LIB-EXACT-VERSION-PREALPHA-001:v1
Environment: internal-pre-alpha
```

The S011-A Datacron is preserved at
[`D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT.md`](D-S011A-GOVERNED-LIBRARY-EXACT-VERSION-CONTRACT.md).
Its implementation acceptance, Datacron lineage, and retained boundaries remain
distinct from S011-B implementation acceptance and from the Human decisions
still required for this Datacron, S011-B closure, and S011 umbrella completion.

## Evidence Classification

### Human Decisions

Carmian Owen accepted the corrected S011-B implementation at
`b9c499c7987ec10fb6b559fd1f5d07513934f17c`, including discharge of the five
named findings and acceptance of the positive
`CREATE -> SUPERSEDE_VERSION -> AMEND_PURPOSE` lifecycle. That decision was
implementation acceptance only. Datacron acceptance, S011-B closure, S011
umbrella completion, policy activation, deployment, and publication were not
exercised.

### Repository-Verified Facts

Repository facts include the S011-A and S011-B commits, the four-model S011-B
aggregate, migration `core.0015_s011b_event_resource_relationship`, command and
read services, focused guardians, exact five-path correction, final local and
remote equality, and clean worktree. They establish committed implementation
and lineage, not deployment or production readiness.

### Independently Executed Evidence

Independent requalification directly inspected the corrected controls, ran the
focused and complete SQLite/PostgreSQL matrices and Node witnesses, inspected a
disposable PostgreSQL catalogue, verified teardown, and reconciled the local
repository with receiver-visible `origin/main`. These checks establish the
behaviour and identities exercised. They do not activate policy or grant
continuing permission.

### Human-Observed Process Evidence

Machine-mediated Drive transfer successes, binary relay limitations,
direct-attachment fallback, operational timing, and credit observations are
Human-observed delivery evidence where not independently represented by a
repository object. They are preserved as bounded process lineage, not audited
programme accounting or proof of universal method.

### Interpretation And Deferral

The constitutional significance, IDOP/HAT learning, and cross-domain milestone
are bounded interpretations of this programme instance. External
transmissibility, methodology canonisation, policy operation, personal-data use,
deployment, publication, and production suitability remain unproven or
deferred.

## S011 Umbrella And Slice Division

S011 establishes governed Event resource linking and readback by connecting two
constitutional domains without merging them. It was divided because a single
Slice would have obscured two different owners and two different kinds of
truth:

- S011-A is the LIBRARY-owned exact-version contract for action authority,
  linkability, and resource/version disclosure;
- S011-B is the EVENT-owned relationship identity, lifecycle, purpose,
  correction, relationship-purpose disclosure, and readback surface.

Each Slice has separate implementation acceptance and lineage. The S011-A
contract had to exist before EVENT could consume it without manufacturing
Library truth. S011 umbrella completion depends on both qualified Slice
lineages and their demonstrated integration; neither Slice alone completes the
umbrella. This record prepares evidence for separate Human decisions and does
not make those decisions.

## Constitutional Domain Boundary

LIBRARY retains ownership of:

- exact resource and version identity;
- resource/version linkability;
- action authority for the exact Library target;
- current resource/version disclosure.

EVENT retains ownership of:

- the Event-resource relationship identity;
- relationship lifecycle and correction;
- purpose semantics;
- relationship-and-purpose disclosure;
- Event-visible readback composition.

EVENT consumes the public Library contract. It does not reinterpret Library
state, select a latest version, infer Library permission, or manufacture a
Library determination. Action authority and disclosure remain separate.
Purpose is bound into EVENT evidence and disclosure but is not an authority
input. Ownership, authorship, authentication, role, staff status, superuser
status, and technical access create no implicit domain authority.

The milestone is KNOWING connecting to DOING without either domain annexing the
other. LIBRARY keeps knowledge identity and exact-version truth; EVENT keeps
occurrence relationship and purpose meaning.

## S011-B Implementation

### Aggregate And Migration

Migration `core.0015_s011b_event_resource_relationship` introduced:

- `EventResourceRelationship`, the stable Event-to-Library-resource identity;
- `EventResourceAssertion`, immutable exact-version, purpose, state, actor, and
  predecessor revisions;
- `EventResourceRelationshipTransition`, append-only action and lifecycle
  lineage;
- `EventResourceRelationshipEvidence`, immutable typed evidence bound to the
  transition that consumed it.

The aggregate has a guarded head assertion and append-only revision history.
The governed assertion states are `CURRENT`, `SUPERSEDED`, `RETIRED`, and
`VOIDED`. Purpose amendment appends a successor assertion rather than rewriting
history. The purpose value historically described as `PARTICIPATION` is
presented and interpreted as **During the Event**; it is not evidence that a
person participated.

### Command And Receipt Boundary

`EventResourceRelationshipService` implements `CREATE`,
`SUPERSEDE_VERSION`, `AMEND_PURPOSE`, `RETIRE`, and `VOID` in transactional,
idempotent command paths. Consequential Library determinations use the exact
resource version and one locked actor Identity/access epoch. EVENT authority,
Library authority, linkability or disclosure, relationship disclosure, and
correction evidence remain typed and action-specific.

`SUPERSEDE_VERSION` requires a different exact version. `AMEND_PURPOSE`
requires a changed purpose. Exact replays are resolved before those no-op
refusals. `RETIRE` and `VOID` are terminal. Duplicate correction validates one
exact historical survivor assertion in the same relationship with the same
exact version and purpose. `NO_SURVIVOR` remains structured.
`OTHER_GOVERNED_CORRECTION` remains fail-closed pending separate classification
authority.

### Readback And Projection Boundary

`EventResourceRelationshipReadService` reconstructs current lineage before
projection. It parses canonical evidence, rejects duplicate fields, verifies
receipts and digests, and binds action, actor or viewer, access epoch, policy,
Event, relationship, assertion, transition, exact resource/version, purpose,
and time. Missing, malformed, inconsistent, stale, or unavailable evidence
fails closed.

Readback proceeds only after outer Event visibility. It then obtains current
Library disclosure for the exact head version and current EVENT
relationship-purpose disclosure while holding and repeatedly validating one
active viewer Identity, credential, and access epoch. Negative, hidden,
unavailable, stale, or corrupt candidates are omitted before sorting and
rendering. The public projection contains only disclosed content and the
human-readable purpose.

This does not imply entitlement, actual participation, generic Library
linking, unrestricted history access, reusable historical permission, or a
deployed policy provider.

## Initial Implementation And Independent Inspection

The original S011-B implementation was committed at:

```text
c77cf1d5bc8e44c901af3cef64a30ea5e82e79a4
feat(event): add governed resource relationship readback
```

Substantial automated qualification was green. The later IDOP-founded,
repository-grounded post-implementation inspection nevertheless found five
semantic deficiencies above that test surface:

1. readback ignored mandatory consumed evidence and could project from an
   incomplete or invalid evidence set;
2. `SUPERSEDE_VERSION` accepted the currently bound exact version;
3. `AMEND_PURPOSE` accepted an unchanged purpose;
4. duplicate-survivor VOID correction did not qualify the referenced survivor,
   and `OTHER_GOVERNED_CORRECTION` was insufficiently bounded;
5. composed Library and EVENT readback gates could use different viewer access
   epochs.

Passing tests did not substitute for independent governed inspection. The
inspection was not implementation mutation or Human acceptance; it identified
the gaps and required a separately authorized correction.

## Bounded Correction

The correction was committed at:

```text
b9c499c7987ec10fb6b559fd1f5d07513934f17c
fix(s011b): enforce correction invariants
```

Its exact five-path boundary was:

```text
src/intevia/services/event_resource_relationship_read_service.py
src/intevia/services/event_resource_relationship_service.py
tests/test_event_resource_relationship_postgresql.py
tests/test_event_resource_relationship_readback.py
tests/test_event_resource_relationship_service.py
```

The two service changes added or strengthened mandatory evidence
reconstruction, same-version and same-purpose refusal, exact survivor
qualification, bounded `OTHER` handling, and same-viewer/epoch readback. The
three test changes qualified those controls, including canonical action-specific
evidence, positive lifecycle reconstruction, refusal without residue, stale and
asymmetric viewer cases, and PostgreSQL locking across both disclosure gates.

The lineage remains four distinct events:

```text
independent inspection found the gaps
    -> bounded implementation correction changed five paths
    -> focused and regression tests demonstrated corrected behaviour
    -> fresh independent requalification inspected and exercised the result
```

The correction environment initially entered a valid HOLD because the exact
PostgreSQL image was absent and acquisition was not authorized. The Human
Governor granted exact image acquisition and retention authority. A later
GitHub Copilot usage-limit interruption was preserved as an external execution
interruption. Neither event was erased by eventual success.

## Final Requalification Evidence

The independently qualified controlling sources are:

```text
Return: 10,721 bytes
Return SHA-256: 945cafd678a8fc3ac3806d9cb45f4da48cc0e6ad354f0e9c119d40d505e0d455
Evidence bundle: 8,574 bytes
Evidence bundle SHA-256: 9fa74f6772c4cf0a61d1de08f4f0c710b52484e2223435677999a7bcb90d5b03
Qualified commit: b9c499c7987ec10fb6b559fd1f5d07513934f17c
```

Final observations:

```text
Focused SQLite qualification: 28/28
Focused PostgreSQL qualification: 5/5
Node witnesses: 2/2
Complete non-PostgreSQL regression: 325/325
Complete PostgreSQL 16.14 regression: 387/387
Migration core.0015: applied
S011-B PostgreSQL tables: 4
Governed constraints: 7
Invalid indexes: 0
Protected-boundary changes: 0
Local HEAD and remote main: exact agreement
Final worktree: clean
Temporary database, container, and volume: removed
Qualified PostgreSQL image: retained
```

The retained image was official PostgreSQL 16.14 for `linux/amd64`, identified
by image and repository digest
`sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20`.
These observations qualify the accepted implementation and bounded integration.
They do not establish deployment, production readiness, policy activation,
publication, or external-use suitability.

## IDOP And HAT Significance

S011-B was the programme's first serious cross-domain constitutional
interoperability stress test. The IDOP inspection layer found five logical gaps
above extensive green automated qualification. Correction and requalification
then showed why architecture, challenge, implementation, inspection,
qualification, and Human decision are separate functions rather than one
undifferentiated completion claim.

The work is an observed instance of Human-governed, multi-nodal AI practice.
Different nodes contributed architecture, challenge, implementation,
inspection, correction, qualification, and evidence preparation while the Human
Governor retained interpretation, authority, acceptance, and closure. Capability
was built beneath governance rather than governance being added afterwards.

The functional challenge role is the **Adversator**:

> The Adversator is loyal to the work by being hostile to its nonsense.

The Adversator may challenge and HOLD. It cannot mutate, accept, activate,
close, or exercise Human authority. Historical source language and node names
remain part of their original lineage; this prospective role title does not
rewrite them.

S011-B is material observed-practice input for later IDOP and HPCC/HPC
case-study synthesis. One programme instance does not canonise a methodology,
prove universal transmissibility, or promote pending observations.

## Process And Delivery Evidence

The controlling practice was IDOP Machine-Readable Family v0.9.2. Relevant
lineage includes:

- receiver-direct qualification of repository state and source bytes;
- machine-mediated Drive transfer successes for bounded source discovery and
  custody;
- a binary relay limitation and direct-attachment fallback preserved as
  delivery evidence rather than hidden by later success;
- directory-only staging bootstrap, exact-path containment, empty-directory,
  and reparse-point integrity rails;
- avoidable and environmental HOLDs retained with their triggers, minimum Human
  decisions, and later continuations;
- separate inspection, correction, test, requalification, implementation
  acceptance, Datacron authoring, and closure-decision stages;
- explicit separation of evidence delivery, source custody, authority, and
  acceptance.

The binary-transfer observation remains capture without promotion. Pending
post-Slice IDOP observations are not resolved or incorporated into a revised
practice by this Datacron.

## Cost And Time Evidence

The controlling requalification sources contain exact test durations, but no
fully reconciled Clockify total or credit total for the complete S011-A/S011-B
programme lineage. No exact programme cost or elapsed-time total is therefore
asserted here. Any broader time or credit observations remain Human-observed
operational evidence, not audited financial or programme accounting.

## Retained Boundaries And Deferrals

This Datacron does not:

- activate `policy:LIB-EXACT-VERSION-PREALPHA-001:v1` or compose an operational
  provider;
- deploy or publish INTEVIA;
- establish production readiness or external-use suitability;
- erase the Library retention/redaction gap;
- create a generic Library link engine;
- imply entitlement, participation proof, or unrestricted relationship history;
- authorize personal-data-bearing or external use while deferred safeguards
  remain unresolved;
- resolve or authorize `OTHER_GOVERNED_CORRECTION` classification;
- complete unrelated future Slices;
- revise or promote IDOP or pending observed-practice candidates;
- accept itself;
- close S011-B;
- complete the S011 umbrella capability.

Historical evidence is reconstruction evidence, not continuing permission.
Current action and disclosure remain subject to their owning domains and current
Identity/access-epoch evaluation.

## Human Decision Boundary

The corrected S011-B implementation has been accepted. This candidate Datacron
and its closeout packet prepare three separate decisions for Carmian Owen:

1. accept or decline the S011-B Datacron;
2. close or retain open S011-B;
3. complete or retain open the S011 umbrella capability after considering both
   S011-A and S011-B lineage and their qualified integration.

Readiness evidence is not any of those decisions. Final authority remains with
the Human Governor.
