# LIB-EXACT-VERSION-PREALPHA-001 v1

## Authority and status

- Policy identity: `LIB-EXACT-VERSION-PREALPHA-001`
- Version: `v1`
- Canonical reference: `policy:LIB-EXACT-VERSION-PREALPHA-001:v1`
- Human Governor: Carmian Owen
- Status: Human-governed internal pre-alpha policy instrument; DRAFT, NOT ACTIVE, NOT DEPLOYED, and NOT ENABLED
- Intended repository path: `governance/policies/LIB-EXACT-VERSION-PREALPHA-001-v1.md`
- Environment: `internal-pre-alpha`
- Capability: Library exact-version action authority, linkability, and content disclosure

This instrument records the Human Governor's bounded S011-A policy. Runtime code may later implement this policy only under separate authority. This instrument does not activate itself, create operational bindings, grant permission, mutate a repository or database, or authorise implementation, tests, deployment, or publication.

## Normative language

`MUST`, `MUST NOT`, `REQUIRED`, `SHALL`, `SHALL NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are normative within this instrument.

`HOLD` means the runtime cannot establish a complete, current, internally consistent basis for the requested determination. `HOLD` is not permission and is not interchangeable with a valid negative result.

## Capability boundary

This policy governs only:

1. whether an active Identity has current explicit authority for one S011-A Library action against one exact Library resource version;
2. whether that exact version is currently linkable from the owning logical resource's state; and
3. whether that exact version's content is currently visible to one explicitly bound active viewer.

This policy creates no:

- generic policy engine or policy-discovery mechanism;
- generic Library link engine;
- entitlement system;
- Event relationship, Event lifecycle, or Event purpose;
- ownership-derived or role-derived authority;
- viewer-history or profiling store;
- Library model or migration;
- policy-administration UI;
- runtime policy-authoring surface;
- retention, redaction, erasure, publication, or external-use claim.

The later EVENT-owned relationship-and-purpose disclosure gate is outside this policy. Purpose is never an S011-A authority input.

## Governed actions

The only action values are:

- `CREATE`: targets the exact Library version proposed for a new EVENT-owned relationship;
- `SUPERSEDE_VERSION`: targets the exact successor Library version proposed for an existing relationship;
- `AMEND_PURPOSE`: targets the exact Library version currently bound to the relationship.

The action names describe the consuming operation without importing Event identifiers, purpose values, Event roles, Event lifecycle, or Event policy into LIBRARY.

No action implies another. A binding for one action or target does not qualify a different action or target.

## Independent determination axes

The policy produces three separately evaluated determinations:

| Axis | Positive | Valid negative | Unresolved |
|---|---|---|---|
| Action authority | `QUALIFIED` | `REFUSED` | `HOLD` |
| Exact-version linkability | `LINKABLE` | `NOT_LINKABLE` | `HOLD` |
| Exact-version disclosure | `CONTENT_VISIBLE` | `HIDDEN` | `HOLD` |

The determinations MUST remain separate. No combined enum may grant authority. A consumer may transport multiple receipts together, but it MUST identify which positive results its consequential action requires.

One axis MUST NOT be inferred from another. In particular, `DEPRECATED` deliberately produces `NOT_LINKABLE` and, for a qualifying viewer, `CONTENT_VISIBLE`.

## Exact-version and logical-state truth

`LibraryResource` owns lifecycle state. `LibraryResourceVersion` owns immutable exact content and version lineage and has no independent lifecycle state under this policy.

The exact version MUST:

- exist;
- belong to the named logical resource;
- match the requested version number;
- be resolved together with its owning logical resource;
- never be substituted with the latest, current, predecessor, or successor version.

Missing, inconsistent, mismatched, or unverifiable resource/version identity returns `HOLD` for every determination that requires that truth.

## Linkability and disclosure table

The following outcomes derive from the owning `LibraryResource` state:

| Logical-resource state | Linkability | Content disclosure to a qualifying viewer |
|---|---|---|
| `PUBLISHED` | `LINKABLE` | `CONTENT_VISIBLE` |
| `DRAFT` | `NOT_LINKABLE` | `HIDDEN` |
| `DEPRECATED` | `NOT_LINKABLE` | `CONTENT_VISIBLE` |
| `ARCHIVED` | `NOT_LINKABLE` | `HIDDEN` |
| Missing, invalid, inconsistent, or unverifiable | `HOLD` | `HOLD` |

This table does not create version-level state.

## Identity qualification

An actor or viewer Identity MUST be re-resolved from durable Identity truth for each new determination.

The Identity MUST:

- exist exactly once;
- have the requested immutable `identity_id`;
- be in `ACTIVE` access state;
- have a current `access_epoch`;
- be associated with a valid credential record whose staff and superuser flags can be evaluated.

A valid, available Identity in `PENDING`, `RESTRICTED`, or `DEACTIVATED` state is a valid negative:

- action authority: `REFUSED`;
- disclosure: `HIDDEN`.

A missing, malformed, duplicated, inconsistent, unavailable, or unverifiable Identity is `HOLD`.

An otherwise valid Identity whose linked credential is staff-flagged or superuser-flagged is explicitly denied:

- action authority: `REFUSED`;
- disclosure: `HIDDEN`.

Staff or superuser status never grants authority, visibility, bypass, fallback, or diagnostic detail.

Authentication, role, ownership, authorship, organisational status, technical access, direct model access, possession of a reference, and prior qualification create no authority or visibility.

## Authoritative runtime binding

The authoritative binding source for this policy is a versioned, content-addressed, schema-validated governed configuration artifact selected at an explicitly Human-authorised activation-time composition boundary.

The artifact is disabled and unpopulated by default. An authorised composition boundary validates its identity and schema, constructs one immutable in-process snapshot, and injects a bounded runtime binding provider configured for the exact canonical policy reference.

No network lookup or mutable external read may occur while database locks are held. The artifact, its population, selection, enablement, deployment, supersession, and activation each require separate Human authority.

The request context is not a binding source. A caller-supplied `authority_binding_reference` is only a claimed reference that MUST be matched to authoritative binding truth.

The provider MUST return one of:

- one exact immutable binding snapshot;
- a complete and verified `NO_MATCH` result;
- an unavailable or unverifiable result.

Multiple matching bindings, partial results, provider failure, stale provider state, or inability to establish currency returns `HOLD`.

A complete verified `NO_MATCH` is a valid negative:

- action authority: `REFUSED`;
- disclosure: `HIDDEN`.

This instrument does not prescribe or create a database model for bindings. Runtime implementation MUST consume a narrow injected binding-provider contract and MUST NOT create a generic policy engine. Policy activation must separately identify and authorise the operational provider and its data.

### Required binding snapshot

Every binding snapshot returned from that immutable provider MUST contain:

- `binding_reference`;
- `binding_version`;
- `policy_reference`;
- `environment`;
- `binding_kind`;
- `subject_identity_id`;
- `enabled`;
- `effective_at`;
- `expires_at`;
- `revoked_at`;
- `superseding_binding_reference`;
- an immutable provider snapshot reference;
- for action bindings: exact `action`, `resource_id`, and `version_number`;
- for viewer bindings: exact viewer scope `LIBRARY_EXACT_VERSION_CONTENT`.

Unknown, missing, duplicated, malformed, or inconsistent fields return `HOLD`.

A returned snapshot represents authoritative binding truth, not caller authority. Action-target or viewer-scope mismatch in an otherwise valid snapshot is a valid negative. Malformed or internally inconsistent snapshot data is `HOLD`.

### Binding currency

A binding qualifies only when:

- `policy_reference` is exactly `policy:LIB-EXACT-VERSION-PREALPHA-001:v1`;
- `environment` is exactly `internal-pre-alpha`;
- `binding_kind` matches the requested determination;
- `subject_identity_id` matches the re-resolved Identity;
- `enabled` is true;
- `evaluated_at` is greater than or equal to `effective_at`;
- `evaluated_at` is strictly earlier than `expires_at`;
- `revoked_at` is null;
- `superseding_binding_reference` is null;
- the authoritative provider proves the snapshot current at evaluation.

For action authority, `action`, `resource_id`, and `version_number` MUST match exactly.

For viewer disclosure, the binding scope MUST be exactly `LIBRARY_EXACT_VERSION_CONTENT`. State-based disclosure is then evaluated for the requested exact version.

A valid current binding that explicitly denies the requested subject, kind, action, target, or scope returns the appropriate valid negative. An absent binding qualifies as a valid negative only when the authoritative provider gives a complete verified `NO_MATCH`. Any inability to distinguish absence from unavailable truth returns `HOLD`.

## Request context

`LibraryRequestContext` contains exactly:

- `request_reference`;
- `consumer_reference`;
- `authority_binding_reference`;
- `policy_reference`;
- `requested_at`.

No other field is accepted.

`policy_reference` MUST equal `policy:LIB-EXACT-VERSION-PREALPHA-001:v1`.

`authority_binding_reference` MUST match the exact authoritative snapshot selected for the determination.

`requested_at` MUST be timezone-aware. It is evidence of the consumer request time, not permission and not a substitute for runtime `evaluated_at`.

`request_reference` and `consumer_reference` are correlation values only. They grant no authority and carry no Event meaning.

Purpose, Event identifier, Event state, Event role, relationship identity, relationship lifecycle, and Event authority MUST be rejected if supplied.

## Opaque component grammar

Every opaque component MUST match:

`[A-Za-z0-9][A-Za-z0-9._~-]{0,127}`

Components are case-sensitive. Colon is reserved for structural delimiters in complete references.

Whitespace, control characters, empty components, Unicode confusables, filesystem or URL path meaning, and unbounded values are prohibited.

Required complete reference forms are:

- policy: `policy:LIB-EXACT-VERSION-PREALPHA-001:v1`;
- binding: `lib-authority-binding:<opaque-binding-id>:v1`;
- determination: `lib-determination:sha256:<64-lowercase-hex>`.

Possession, replay, discovery, or publication of a reference creates no authority.

## Evaluation precedence

For every axis, the runtime applies this precedence:

1. Validate policy identity, environment, input schema, reference grammar, canonical types, and evaluation time. Unresolved truth returns `HOLD`.
2. Resolve the exact resource/version association and owning logical-resource state. Unresolved truth returns `HOLD`.
3. Where applicable, re-resolve Identity and credential flags. Unresolved truth returns `HOLD`.
4. Where applicable, obtain one authoritative current binding outcome. Provider unavailability, ambiguity, or unverifiable currency returns `HOLD`.
5. Apply valid Identity, staff/superuser, binding, action-target, viewer-scope, and state negatives.
6. Return a positive result only when every required fact is positively established.

`HOLD` therefore takes precedence over a purported positive or negative when a fact needed to validate that result is unresolved. A valid negative may be returned only after the facts supporting that negative are themselves verified.

## Consequential-action freshness

A determination is a current-evaluation immutable value object, not a reusable permission.

For a consequential relationship mutation:

- the owning `LibraryResource` MUST be locked first;
- the Event parent MUST then be locked;
- existing EVENT relationship rows MUST then be locked in deterministic order;
- applicable Identity rows MUST then be locked in deterministic primary-key order;
- the exact version MUST be resolved under that locked resource;
- current Identity, policy, binding, action authority, and linkability MUST be evaluated within the same database transaction used to persist the consequential EVENT mutation;
- the mutation MUST complete before releasing the transaction;
- the consumer MUST require the exact positive axes needed for that action;
- a detached, historical, expired, or replayed receipt MUST NOT authorise the action.

Within-request memoisation is permitted only inside that same transaction for byte-identical inputs and the identical policy-binding snapshot.

`LibraryResourceVersion` is resolved under its locked owner and is not separately locked because it is immutable.

Identity MUST NOT be locked before Event on the shared consequential path.

The governed binding snapshot cannot be database-locked. It MUST already be local, immutable, content-addressed, and validated. If the provider cannot prove the selected snapshot current at evaluation, the result is `HOLD`.

PostgreSQL guardians MUST prove Library-state races, relationship races, deterministic multi-row ordering, and Event/Identity deadlock behavior before S011-A acceptance.

## Read-time disclosure

Viewer disclosure is evaluated at read time against current truth.

Ordinary read-time disclosure determinations are ephemeral. They MUST NOT create a per-Identity viewer-history store.

S011-B may later durably preserve exact Library evidence consumed by a consequential relationship mutation. Any durable storage of ordinary viewer-disclosure determinations requires separate Human authority and explicit privacy purpose, retention, access, and redaction rules.

## Canonical determination payload

Each determination uses the fixed schema identity:

- `schema_id`: `intevia.s011a.library-determination`
- `schema_version`: `1`
- `canonicalization`: `RFC8785+INTEVIA-S011A-v1`

The complete immutable payload MUST contain, with explicit nulls where a field is inapplicable:

- schema identity and version;
- canonicalization identity;
- determination kind;
- policy reference;
- environment;
- result;
- internal basis code;
- evaluated-at timestamp;
- revalidation boundary;
- exact resource identifier;
- exact version primary key;
- exact version number;
- source logical-resource state;
- actor Identity ID and access epoch, or null;
- viewer Identity ID and access epoch, or null;
- action, or null;
- binding reference, binding version, binding kind, and provider snapshot reference, or null;
- request context fields, or null where not applicable;
- unresolved limitation code, or null.

The following potentially wide integers MUST be represented as canonical non-negative decimal strings:

- exact version primary key;
- Identity `access_epoch`;
- Library version number;
- binding version.

Their grammar is:

`0|[1-9][0-9]*`

Signs, leading zeroes, decimal points, exponents, whitespace, locale formatting, and JSON-number representation are prohibited for these fields. Fixed small schema versions remain safe JSON integers.

The payload MUST NOT contain:

- the derived determination reference;
- Library content;
- title or display text;
- username or display name;
- credential identifier;
- role names;
- staff or superuser flags;
- Event meaning;
- relationship purpose;
- public error text.

Internal basis and limitation codes MUST be fixed policy enums and MUST NOT be exposed through presentation responses.

## Canonicalization and digest

Canonicalization uses RFC 8785 JSON Canonicalization Scheme with these additional restrictions:

- UTF-8 without BOM;
- fixed schema and canonicalization versions;
- ASCII field names;
- every schema field present, with explicit null where permitted;
- strings already in Unicode NFC, otherwise rejected;
- UTC timestamps exactly `YYYY-MM-DDTHH:mm:ss.ffffffZ`;
- enums in exact uppercase form;
- fixed small schema versions as safe JSON integers;
- potentially wide identifiers and counters as canonical decimal strings matching `0|[1-9][0-9]*`;
- floats, NaN, infinities, binary values, duplicate keys, and unknown fields prohibited.

The hash input is:

```text
UTF-8("INTEVIA:S011A:LIB-DETERMINATION:v1\n")
+ RFC8785(canonical_payload)
```

The SHA-256 lowercase hexadecimal digest forms the outer reference:

`lib-determination:sha256:<64-lowercase-hex>`

The outer reference is not part of the hashed payload. This removes circular self-reference.

Independent runtime implementations MUST reproduce the digest using published cross-runtime test vectors before S011-A acceptance.

The digest proves byte-identical evidence identity only. It does not prove truth, secrecy, currency, authorisation, policy correctness, continuing permission, or possession rights.

## Evidence and persistence boundary

S011-A creates no model or migration.

S011-A determinations are immutable ephemeral value objects. A later S011-B consequential mutation may preserve:

- the exact `LibraryResourceVersion` foreign key with `PROTECT`;
- canonical payload bytes or losslessly equivalent complete typed fields;
- determination reference;
- schema and canonicalization versions;
- policy and binding references;
- evaluation time;
- revalidation boundary;
- separate EVENT authority evidence.

S011-B may recompute a digest mechanically. It MUST NOT interpret, reproduce, or override the Library state table or policy semantics.

Historical evidence creates no continuing authority.

## Non-disclosure

`HIDDEN`, `REFUSED`, and `HOLD` MUST use non-revealing presentation behavior.

No non-positive response may reveal:

- content;
- logical-resource state;
- resource or version existence;
- counts;
- current-version identity;
- predecessor or successor;
- policy basis;
- binding status;
- viewer qualification;
- staff or superuser treatment;
- differentiated public error text.

Internal receipts and digests are evidence, not public identifiers or bearer tokens.

Timing-normalization requirements belong to the consuming presentation boundary. S011-A MUST provide no presentation-ready diagnostic that defeats that boundary.

## Retention and use limitation

This policy is limited to bounded private, internal, non-personal-resource pre-alpha work.

The retained retention/redaction gap blocks:

- erasure claims;
- public or external use;
- personal-data-bearing durable viewer history;
- production activation;
- any assertion that S011-A supplies a complete retention or redaction model.

## Runtime relationship

The intended bounded runtime files are:

- `src/intevia/services/library_exact_version_contract.py`;
- `src/intevia/services/library_exact_version_policy.py`.

The contract module owns enums, immutable value objects, canonical receipt construction, and narrow protocols.

The policy module implements only this exact instrument. It MUST NOT discover, author, amend, supersede, generalise, or activate policy.

The contract MUST NOT import the concrete policy module. Neither file may import EVENT.

`ContributionAuthority` MUST NOT terminate S011-A decisions because it collapses valid refusal and unavailable truth and lacks this policy identity, binding, and exact-target semantics. It remains unchanged for unrelated existing flows.

## Deployment and activation boundary

This instrument does not:

- deploy or enable the policy;
- select or populate the operational binding provider;
- create binding data;
- activate allowlists or bindings;
- alter routes or presentation;
- author code, tests, fixtures, or migrations;
- accept or close S011-A.

Policy activation requires separate explicit Human authority after implementation and evidence qualification.

Final authority remains with Carmian Owen, Human Governor.
