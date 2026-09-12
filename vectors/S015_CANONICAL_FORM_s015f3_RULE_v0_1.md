# S015 — CANONICAL FORM `s015f3` — THE RULE

    Seat            : Lead Designer, GitHub Copilot (Claude Fable 5.1), VS Code
    Human Governor  : Carmian Owen
    Programme       : INTEVIA, slice S015, packet PKT-A-2, migration 0021
    Protocol        : IDOP v0.9.7 (in_force.json IF-030)
    Commission      : IDOP097_S015_CANONICAL_FORM_S015F3_LD_COMMISSION_v0_1.md (11,912 B, qualified at STEP 1)
    Controlling     : S015_PKT_A_2_SPECIFICATION_v0_13.md (163,447 B, qualified at STEP 1) — where the commission
                      and the specification differ, the specification governs (commission STEP 8)
    Prepared at     : U21h, design gate — recorded BEFORE any line of code was changed
    Status          : Design rule. Not certified; authorises nothing. Candidate object for the Vision Chamber.

Authority classes (IF-019), carried on every exact figure: `[H]` Human-issued ruling; `[E]` executed in this unit and
captured under `evidence/`; `[R]` repository-derived at HEAD `3efa7f6a`; `[C]` carried from the controlling
specification; `[A]` this seat's own design choice or reading.

This document is deliverable 1. It is written so that another seat with no access to this seat's code can rebuild the
serialiser from it and reproduce every published vector byte for byte.

---

## 0. What the form is for, and what it must satisfy

The fingerprint of an anchor's admitted eligible event set is `s015_fingerprint_event_set(token, anchor_id, state_at,
known_at)`: the SHA-256 of a canonical UTF-8 preimage of that set, prefixed with the form tag `[C — §5.7 item 2]`.

Requirements the form must meet, and the falsifier of each `[C — §5.7 item 2; H — U21g-27 at §4.25]`:

| # | Requirement | Falsifier |
|---|---|---|
| R1 | **Total**: every admitted eligible set has a canonical form. | An admitted set for which the serialiser raises or returns NULL. |
| R2 | **Injective over admitted eligible sets**: two distinct sets never produce the same bytes. | Two admitted sets, differing in any column of any row, with byte-identical preimages (the `s015f2` survivor is `evidence_reference` — Adversator II A2-F5, paired return Finding 6). |
| R3 | **Encodes every column of every event row** `[H — U21g-27]`. A redundant column is *not wrong*; an omitted column *is*. | Any column of any of the twelve inventories without a slot in the record. |
| R4 | **Deterministic**: bytes depend only on the rows' column values, the anchor token and the anchor identity — not on session settings (`TimeZone`, `DateStyle`, `extra_float_digits`, client encoding) or catalogue positions. | Two sessions with different settings producing different bytes for the same rows. |
| R5 | **Digest shape unchanged**: `s015f3:` + 64 lowercase hex = 71 characters; `varchar(71)` and the shape `CHECK` length stand `[C — §5.8]`. | Any fingerprint not matching `^s015f3:[0-9a-f]{64}$`. |

Not changed by this form and not this seat's to change `[C; commission STEP 4]`: the fold order
`(effective_at, occurred_at, received_at, recorded_at, sequence, event_uuid)`; eligibility (`effective_at <= state_at
AND received_at <= known_at AND recorded_at <= known_at`); the four-time grammar; the anchor token and anchor identity in
the envelope.

**The `s015f2` defect in one sentence** `[E — carrier migration lines 240–247 read]`: the record was a fixed nine-element
array (four times, sequence, event_uuid, prior_state, state, obligation projection); every other column had no slot, so
any two rows equal on those nine and different elsewhere were indistinguishable.

**Design principle** `[A]`: *one grammar, one set of type rules, one ordering, applied to twelve inventories*. No table
has a special case. The record is an object keyed by column name; the column set and each column's type are read from
the installed table itself, so no serialiser can omit a column the table has.

---

## 1. Step 1 — Aggregate column inventories `[E]`

**Derivation** `[E — design/derive_inventories.py over the qualified carrier migration → design/inventories_from_migration.json]`.
The migration module was imported and every `migrations.CreateModel` whose table is one of the twelve `ANCHORS` event
tables was walked field by field. Column names are the PostgreSQL names Django creates: `ForeignKey` / `OneToOneField`
→ `<field>_id`; everything else → the field name. No specification prose was consulted for this step.

| Event table | Anchor token | State column | Per-aggregate extras (beyond the common member, state and anchor FK) | Columns |
|---|---|---|---|---:|
| `core_livingorganismevent` | `core_livingorganism` | `resulting_state` | — | 24 |
| `core_circlestateevent` | `core_circle` | `resulting_state` | — | 24 |
| `core_organismmembershiptransition` | `core_organismmembership` | `resulting_state` | `reason_class` | 25 |
| `core_contextualroleassignmentstateevent` | `core_contextualroleassignment` | `resulting_state` | — | 24 |
| `core_membershipconditionstateevent` | `core_membershipcondition` | `resulting_state` | — | 24 |
| `core_authorityinvalidationevent` | `core_authoritybasis` | `resulting_state` | `kind` | 25 |
| `core_determinationcontestevent` | `core_determinationcontestcase` | `resulting_state` | — | 24 |
| `core_coverageassessment` | `core_essentialcoveragecase` | `result` | `determiner_capacity_reference`, `evaluated_known_at`, `evaluated_state_at`, `roster_version` | 28 |
| `core_restrictedcontinuityevent` | `core_restrictedcontinuitycase` | `resulting_state` | `accountable_actor_id`, `permitted_measures_reference`, `prohibited_effects_reference`, `triggering_assessment_id` | 28 |
| `core_visibilitygrantstateevent` | `core_governedvisibilitygrant` | `resulting_state` | — | 24 |
| `core_obligationstateevent` | `core_governedobligationcase` | `resulting_state` | `consequences_reference`, `effective_deadline`, `escalation_path_reference`, `extension_basis_reference`, `interim_measures_reference`, `legal_basis_determination_id`, `legal_deadline_status`, `qualified_legal_deadline` | 32 |
| `core_planningclassificationevent` | `core_planningclassificationcase` | `result` | `criterion_reference` | 25 |
| **Total** | | | | **307** |

**Common member present in all twelve** `[E]` — 22 columns: `action`, `actor_access_epoch`, `actor_id`,
`authority_basis_id`, `authority_decision_reference`, `effective_at`, `event_uuid`, `evidence_reference`, `id`,
`idempotency_key`, `lineage_reference`, `occurred_at`, `payload_fingerprint`, `predecessor_id`, `predecessor_sequence`,
`prior_state`, `received_at`, `recorded_at`, `request_reference`, `sequence`, `temporal_basis_kind`,
`temporal_basis_reference`. Each table adds its state column (`resulting_state` or `result`) and its anchor FK
(`<anchor>_id`), making the 24-column base; the extras above make up the rest. The exact column list per table, in
declared and in canonical order, with type and nullability, is in `design/inventories_from_migration.json`.

**Decision D1 — `id` and every `*_id` surrogate are in the inventory** `[A; ground H — U21g-27 "every column of every
event row"; falsifier text "redundant rather than wrong"]`. `id` is redundant with `event_uuid` (both unique), and
`predecessor_id` with `predecessor_sequence`; redundancy is permitted, omission is not. **Consequence, reported not
resolved**: the fingerprint now depends on database-assigned surrogate keys (`id`, `predecessor_id`, `actor_id`,
`authority_basis_id`, the anchor FK, `triggering_assessment_id`, `accountable_actor_id`, `legal_basis_determination_id`).
`recorded_at` (trigger-assigned) already made the `s015f2` fingerprint database-assigned; `s015f3` widens that to
surrogates. A logical re-import that renumbers surrogates changes every fingerprint. A form that encodes referenced
rows by their identity uuid instead would be a *different* design decision, not this one, and would require the Human
Governor to qualify U21g-27. **Vision Chamber item.**

**Types present across the 307 columns** `[E]`: `bigint` (surrogate PK `id`; FK surrogates; `actor_access_epoch`),
`integer` (`sequence`, `predecessor_sequence`, `roster_version`), `timestamptz`, `uuid`, `varchar`. **No `boolean`, no
`json`/`jsonb`, no numeric/float, no bytea, no array column exists in any of the twelve inventories** `[E]`. Nullable
columns: 79 of 307 `[E]`.

---

## 2. Step 2 — Type serialisation rules

One rule per PostgreSQL type; the rule is a function of the column's *catalogue type* (`pg_attribute.atttypid`), never
of its value's appearance.

| PostgreSQL type | Rule (states the rule, not an example) |
|---|---|
| `timestamp with time zone` | Convert to UTC; render as `YYYY-MM-DD"T"HH24:MI:SS.US"Z"` — four-digit year, two-digit fields, exactly six fractional digits (zero-padded), literal `T` and `Z`; wrap in a JSON string. Identical to the installed `s015_canonical_ts` `[C — Phase 2 precedent]`. Domain `TS_MIN..TS_MAX` `[R]` guarantees four-digit years. |
| `uuid` | The 36-character lowercase hyphenated `8-4-4-4-12` hexadecimal text (PostgreSQL `uuid::text`), wrapped in a JSON string. |
| `character varying` / `text` | JSON string with the **mandatory** escapes only: `"`→`\"`, `\`→`\\`, U+0008/0009/000A/000C/000D → `\b \t \n \f \r`, every other control character U+0000–U+001F → `\u00xx` with **lowercase** hex; every other code point, including non-ASCII and U+007F, emitted as raw UTF-8; no `\/`, no `\uXXXX` for non-ASCII. This is PostgreSQL `to_json(text)::text` and Python `json.dumps(s, ensure_ascii=False)` `[C — Phase 2 precedent; E — F-3 tests the equivalence]`. |
| `bigint` / `integer` / `smallint` | JSON number: the shortest decimal representation, optional leading `-`, no leading zeros, no exponent, no fraction, no quotes. |
| `boolean` | **Undefined in `s015f3` — the serialiser refuses.** No boolean column exists in any inventory `[E]`; an unexercised rule is an unverified rule. A future migration adding a boolean column must define the rule *and* move the form tag (§4). *Divergence from the commission, which lists booleans among the rules to state — reported, not resolved.* |
| `json` / `jsonb` | **Undefined in `s015f3` — the serialiser refuses.** Same ground; same divergence reported. |
| any other type | **Refused** (`RAISE EXCEPTION 'S015 canonical form s015f3 has no rule for type …'`). An unknown type can never be silently encoded. |
| `NULL` of any type | The four-byte literal `null` (§3). |

**Decision D2 — refuse, do not guess** `[A; ground R1/R2]`. Totality (R1) is over the *admitted* domain, which contains
only the five types present; refusing an unseen type is not a totality failure, it is the guarantee that R3 cannot be
silently broken later.

---

## 3. Step 3 — The null representation

**Stated once**: a column present in the table whose value is SQL `NULL` is emitted as the key followed by the literal
`null`. The key is **always present** for every catalogue column of the table, nullable or not.

**Absent versus null — distinguished, and how** `[A]`:

- A column *absent from a table* is a fact about the table, not about the row. The table is fixed by the anchor token
  in the envelope (§5), and the token → table mapping is `s015_anchor_map()` `[R]`. Two records from different tables
  therefore never compare; within one table every record carries exactly the same key set.
- A column *present but null* emits `"<name>":null`. It occupies bytes; its absence would not.
- The installed serialiser **refuses** a row object that lacks any catalogue column of its table (`RAISE`), and ignores
  nothing: a key in the row object that is not a catalogue column is likewise refused. Thus "absent" can never be
  represented as bytes at all — only "present-null" can — and the two facts cannot collide.

Why the key is always present rather than omitted-when-null `[A]`: omitted-when-null makes the byte grammar depend on
the value, so a reader cannot know from the bytes which columns the table had; and a future nullable column added to a
table would leave all-null rows byte-identical to the old form, silently. Always-present keys make the column set
visible in every record.

---

## 4. Step 4 — The record ordering, and stability across future migrations

**Record shape**: a JSON object, no whitespace anywhere, `{"k":v,"k":v,…}`.

**Key order — derived, not declared** `[A]`: keys are the table's column names sorted by **bytewise (C-collation)
ascending order of the UTF-8 bytes of the name**. Column names are ASCII `[E]`, so this equals Unicode code-point order
and Python `sorted()`. The order does not depend on `pg_attribute.attnum` (declared position), on `ALTER TABLE` history,
or on dump/restore.

**Why derived and not a declared list** `[A]`: a declared list per table *is* twelve serialisers written down twice
(once in the inventory, once in the code) and can drift from the table — exactly the `s015f2` failure class. Reading the
column set and order from the catalogue makes R3 structural: the serialiser cannot omit a column it did not know about.

**What makes it stable across future migrations that add columns** `[A]`:

1. Adding a column changes the record's byte grammar for that table. **By the rule of §5 that is a new form**, and the
   tag must move (`s015f4`, …). The catalogue-derived serialiser makes the *bytes* change immediately; what must not be
   allowed is a *silent* change under the same tag.
2. Therefore the corpus (`s015f3_fixed_vectors.json`) **publishes each table's exact canonical column list**, and the
   travelling test asserts that the installed catalogue column set and canonical order of every one of the twelve
   tables equals the published list. A migration that adds, drops or renames a column of an event table fails that test
   until the form is re-versioned and the corpus regenerated. The inventory is pinned by evidence, not by code.
3. Ordering by name rather than by position means a column added later lands at its alphabetical place, so the
   published order for any future form is still derivable by rule with no list to maintain.

**Records within the set**: emitted in fold order `(effective_at, occurred_at, received_at, recorded_at, sequence,
event_uuid)` ascending, exactly as `s015_eligible_event_ids` orders them `[C — unchanged]`; comma-separated inside a
JSON array.

**Not `to_jsonb(row)::text`** `[A]`: jsonb orders object keys by *length then bytes*, renders timestamps in the session
time zone, and would not honour §2. The row is passed to the serialiser as `jsonb` only as a typed carrier; the bytes are
built column by column by the rules above.

---

## 5. Step 5 — The form version

`s015f2` → **`s015f3`** `[C — commission STEP 3 item 5; A — a changed byte grammar is a new form; the tag exists to
separate forms]`.

- Envelope tag literal: `"s015f3"`.
- Digest: `'s015f3:' || lower-hex(sha256(convert_to(preimage,'UTF8')))` — 7 + 64 = **71 characters** `[E — derived:
  len("s015f3:")=7]`; `varchar(71)` and the `CHECK` length are unchanged `[C — §5.8]`; only the literal moves.
- Envelope grammar (unchanged but for the tag): `["s015f3","<anchor token>","<anchor identity uuid>",[<records>]]` —
  token as a JSON string, identity uuid per the uuid rule, records per §4, no whitespace. An empty eligible set is
  `[…,[]]`.

---

## 6. Step 6 — The fingerprint consumers (relayed `[A]`, verified `[E]`)

Every place the `s015f2` literal appears must follow the form. Relayed counts 8 / 1 / 6 / 19 / 0 were **verified
against the qualified carrier bytes** `[E — outline.py counts, this channel]`: all five match.

| Surface | Occurrences | Where |
|---|---:|---|
| migration `0021` | 8 | comment (line 222); `s015_canonical_envelope` literal (250); `s015_digest_envelope` prefix (254); `goc_fp_shape_ck` in `install_s015_0021` (890); four `AddConstraint` fingerprint-shape regexes for living organism, circle, membership, contextual role assignment (1153–1156) |
| `test_s015_0021_negative_direct_sql.py` | 1 | `CacheBoundary.test_neg_insert_with_non_empty_set_fingerprint` literal fixture (293) |
| `test_s015_0021_positive_controls.py` | 6 | prefix assertion (82); `FixedVectors` docstring, path, absolute fallback, preimage/digest (263, 267, 269, 278–279) |
| `s015f2_fixed_vectors.json` | 19 | `form_version`, grammar text, eight digests, eight preimages |
| `s015_0021_support.py` | 0 | — |

Outside the carrier `[R]`: the Phase 2 worktree-only `core/models.py` change carries `S015_FINGERPRINT_SHAPE =
r"^s015f2:[0-9a-f]{64}$"`; the migration's `AddConstraint` operations must agree with model state for
`makemigrations --check` (F-4), so the worktree copy of `models.py` moves too. The carrier does not contain
`models.py`; the copy used is the Phase 2 deliverable (183,308 B, sha256 `3f482559…dae7`) `[E — 02_provision.out]`.
**Reported: this is a repository-tracked file the construction depends on and does not deliver; its change is a
worktree-only change, as at Phase 2.**

---

## 7. Step 7 — The published vectors

`s015f3_fixed_vectors.json` regenerates the eight Phase 2 vectors — **seven re-derived (V1, V2, V3a, V3b, V3c, V4, V5)
plus one additional (V6, obligation)** `[C — carrier corpus; A2-F4]` — under the new form. Each vector publishes:

- `anchor_token`, `anchor_identity`, `event_table`, and the table's **canonical column list** (§4 item 2);
- for every event: **every column** of the inventory as a JSON object — the same object the serialiser consumes —
  including the surrogate `id`, FK surrogates and `recorded_at` (authored values `[A]`, part of the published input);
- `preimage` (exact), `preimage_bytes` (UTF-8 length), `digest`;
- and, for the whole corpus, the grammar of §2–§5 in prose so a reader reproduces the bytes **with no code of this
  seat's**.

Inputs carried from Phase 2 are unchanged where they existed (times, sequences, event uuids, states, obligation
fields); columns that the `s015f2` record did not carry receive authored values `[A]` that are stated in the corpus.
Because a vector's `id`/`recorded_at` are database-assigned in a live table, the record-level and envelope-level
functions are driven with the published rows directly (5.1), and the set-level functions (eligibility, ordering,
`s015_event_set_preimage`, `s015_fingerprint_event_set`) are driven against live rows whose values are read back and
re-serialised independently — both in the travelling test.

---

## 8. Divergences reported, not resolved

| # | Between | Divergence | Left to |
|---|---|---|---|
| DV-1 | commission STEP 3 item 2 ↔ inventories `[E]` | Commission asks for boolean and JSON rules; no such column exists; `s015f3` refuses both types rather than defining an unexercised rule. | Vision Chamber / Human Governor |
| DV-2 | U21g-27 `[H]` ↔ portability | "Every column" includes database-assigned surrogates; fingerprints are not portable across a renumbering re-import (D1). | Human Governor to qualify or confirm U21g-27 |
| DV-3 | specification §5.7 ↔ carrier corpus | "seven fixed vectors" against eight published (A2-F4); this rule names eight = seven re-derived + one additional; the specification's correction is the Vision Chamber's. | Vision Chamber |
| DV-4 | specification §5.7 ↔ F-3 | "three independent implementations" is under an open finding (Finding 22); this unit's second implementation is the same seat's and adds nothing to independence. | Vision Chamber |
| DV-5 | carrier ↔ construction | `core/models.py` is not in the carrier yet its `S015_FINGERPRINT_SHAPE` must move for `makemigrations --check`; changed in the worktree copy only. | Human Governor (packet contents) |
| DV-6 | commission STEP 3 item 4 ↔ this rule | The commission offers "fixed declared order or derived"; this rule chooses derived-by-name and pins the inventory by published evidence rather than by code. A reviewer preferring a declared list would be choosing a different form. | Vision Chamber |

---

## 9. The rule in one block (for the rebuilding seat)

    preimage(token, identity, rows) =
        '["s015f3","' ‖ token ‖ '","' ‖ uuid(identity) ‖ '",[' ‖ join(',', [record(T, r) for r in fold_order(rows)]) ‖ ']]'

    record(T, r) =
        '{' ‖ join(',', ['"' ‖ c ‖ '":' ‖ value(type_T(c), r[c])  for c in sorted_bytewise(columns_T)]) ‖ '}'
        — refuse if r lacks any c in columns_T, or has any key not in columns_T

    value(_, NULL)          = 'null'
    value(timestamptz, v)   = '"' ‖ utc(v) formatted YYYY-MM-DD"T"HH24:MI:SS.US"Z" ‖ '"'
    value(uuid, v)          = '"' ‖ lowercase hyphenated 8-4-4-4-12 ‖ '"'
    value(varchar|text, v)  = JSON string, mandatory escapes only, \u00xx lowercase, raw UTF-8 otherwise
    value(int2|int4|int8,v) = shortest decimal, optional leading '-'
    value(other, v)         = REFUSE

    digest(preimage) = 's015f3:' ‖ lowercase_hex(SHA-256(UTF-8(preimage)))       — 71 characters

    fold_order = ORDER BY effective_at, occurred_at, received_at, recorded_at, sequence, event_uuid   (unchanged)
    eligible   = effective_at <= state_at AND received_at <= known_at AND recorded_at <= known_at     (unchanged)

Final authority remains with Carmian Owen, Human Governor.
