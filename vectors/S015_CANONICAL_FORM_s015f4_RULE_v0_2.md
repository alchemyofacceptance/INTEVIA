# S015 — CANONICAL FORM `s015f4` — THE RULE

    Status      : Rule addendum. Candidate v0.2, prepared by the Vision Chamber (Claude Opus 5), UFUND-2,
                  16 September 2026. Authorises nothing. v0.2 adds the serialization-inputs section only.
    Supersedes  : S015_CANONICAL_FORM_s015f3_RULE_v0_1.md for the form tag and column set only. That rule
                  remains the full statement of the byte grammar and is kept as lineage.
    Requirement : PKT-A-3 bumps the canonical form [H — U21h §3, 12 Sep 2026]; the literal s015f3 in two
                  functions and five shape CHECKs moves to s015f4, and vectors regenerate [designated design
                  LD_RETURN_PKT_A_3_MIGRATION_0022_DESIGN_v0_6.md §9; census LD_PKT_A_3_DESIGN_CENSUS_v2.py].

## What changes

1. **Tag.**
   - Envelope: `["s015f4","<anchor token>","<anchor identity uuid>",[<records>]]`.
   - Digest: `s015f4:` followed by 64 lowercase hex characters, 71 characters in all.
2. **Column set.** Every event table's record adds the fourteen columns of design v0.6 §5.1: `actor_state`,
   `actor_capacity`, `enterer_identity_id`, `entry_mode`, `composing_rule_state`, `composing_rule_reference`,
   `composer_state`, `composer_identity_id`, `effective_until`, `effective_until_state`, `parts_commitment`,
   `l1_commitment`, `predecessor_l1_commitment`, `l2_commitment`. Their types are `varchar`, `bigint` and
   `timestamptz`, all of which the s015f3 grammar already covers. The twelve inventories total **475**
   columns, previously 307.

## What does not change

- **Byte grammar.** The value rules, the bytewise key order, the fold order, eligibility, separators and UTF-8 are all unchanged.
- **Other forms.** The L1 (`s015r1`), parts (`s015p1`) and L2 (`s015c1`) commitment forms are separate forms, and this bump does not touch them.
- **Error text.** `s015_canonical_value`'s refusal message still names `s015f3`. The design limits the bump to the two envelope functions and the five checks. The text is recorded here as stale, not changed.

## Vectors

`s015f4_fixed_vectors.json` is derived by `derive_s015f4_vectors.py`, independently of the installed SQL.

- **Self-test.** The derivation first reproduces all eight published s015f3 vectors byte for byte, then derives s015f4 from those inputs.
- **Authored values.** The fourteen columns hold authored values taken from the act each event represents: a person acting and entering the record themselves.
- **Serialization inputs only.** The authored `parts_commitment`, `l1_commitment` and `predecessor_l1_commitment` values are shape-valid strings chosen to exercise the grammar. None is computed from a parts, L1 or predecessor preimage, and none would be accepted by the live guardians. They establish nothing about commitments. The corpus states this in `serialization_inputs`.
- **Not yet established.** Agreement with the installed SQL has not been established; the installed-SQL fixed-vector test establishes it.

**Stored fingerprints.** Migration `0023` refuses, forward and reverse, where any cache-anchor or event
row exists. No ruling defines how existing s015f3-labelled fingerprints would be converted.

## Serialization inputs are not fixtures

The vectors test one thing: the canonical grammar over given rows. A value in a vector row is not thereby valid for a
live table, and must not be copied into a fixture.

| Field | In a vector | In a live row |
|---|---|---|
| `l1_commitment` | authored string | **database-assigned** at insert; a supplied value is refused; a fixture supplies none |
| `predecessor_l1_commitment` | repeats the authored predecessor string | declared, then verified at commit against the predecessor's assigned value; a fixture reads that value back |
| `parts_commitment` | authored string | declared, then verified at commit; a fixture takes the database-computed value |
| `l2_commitment` | null | null unless an L2 content row exists |
| the other ten added columns | the act each vector represents | each fixture follows the governing rules for the act it records (design v0.6 §5.1; `BD-1`) |
