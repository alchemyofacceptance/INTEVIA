# S015 — COMMITMENT FORMS `s015r1` (L1) AND `s015p1` (PARTS) — THE BYTE CONTRACT

    Status      : Rule. Candidate v0.1, prepared by the Vision Chamber (Claude Opus 5), UFUND-2, 16 September 2026.
                  Authorises nothing.
    Requirement : designated design LD_RETURN_PKT_A_3_MIGRATION_0022_DESIGN_v0_6.md §5.4 (L1) and §6 binding contract
                  row 2 (parts) [S]; Human Governor rulings L1-a and L1-d, 16 Sep 2026: compact, no formatting
                  whitespace; meaningful spaces inside text values preserved [H].
    Relies on   : S015_CANONICAL_FORM_s015f3_RULE_v0_1.md for the value rules and the record grammar; the s015f4
                  addendum for the column set. Neither is changed.

Every choice below that is not stated by a source is marked `[R]` with its ground. None is left open.

## 1. Shared rules

- **Encoding.** The preimage is a UTF-8 string; the digest is the form tag, a colon, and the lowercase hexadecimal
  SHA-256 of those bytes: 71 characters in all.
- **Arrays.** `[`, elements separated by a single `,`, `]`. No whitespace between elements or around brackets `[H — L1-a, L1-d]`.
- **Strings.** The canonical string rule: a JSON string; only `"`, `\` and control characters are escaped; every
  other character, including spaces and non-ASCII, is written as itself in UTF-8 `[S — s015f3 rule]`. Text is
  written exactly as stored: no trimming, case change or normalisation happens in the preimage `[H — meaningful
  spaces preserved]`. (What may be stored is governed by the tables' own CHECKs.)
- **Integers.** Decimal, no sign unless negative, no leading zeros `[S — s015f3 rule, integer rule]`.
- **UUIDs.** A JSON string of the lowercase hyphenated form `[S — s015f3 rule, uuid rule]`.
- **Null.** The literal `null` `[S — s015f3 rule]`.
- **Table name.** The bare name of one of the twelve S015 event tables, e.g. `core_livingorganismevent`, as a string.
  Any other relation is refused `[R — L1-b: every event-table reference in 0022 is bare; the record grammar refuses rather than guesses]`.

## 2. `s015r1` — the L1 commitment of an event row

    ["s015r1",<table>,<record>]

- `<record>`: the canonical record of the whole row by the `s015_canonical_event_record` grammar — a JSON object,
  keys in bytewise column-name order, no whitespace — **with `l1_commitment` present and `null`** `[S — §5.4]`.
- The record is inserted as text; it is never re-parsed or re-formatted `[S — §5.4 "the same grammar"]`.
- Assigned by the database at insert, after `recorded_at`; a caller-supplied value is refused `[S — §5.4]`.

## 3. `s015p1` — the parts commitment of an event row

    ["s015p1",<table>,<event_uuid>,[<part>,<part>,…]]
    <part> = [<part_ordinal>,<part_class>,<posture>,<ground>,<office>]

- **Content and field order** — ordinal, class, posture, ground, office `[S — §6 row 2]`.
- **Ordering** — ascending `part_ordinal` `[S — §6 row 2 "in ordinal order"]`. Ordinals are unique per record
  `[S — 0022 unique constraint]` and dense from 1 at commit `[S — §5.6; parts binding guardian]`, so the order is total.
- **Types** — `part_ordinal` by the integer rule; `part_class`, `posture` and `ground` by the string rule; `office`
  by the string rule, or `null` where the part has no office (an open part) `[R — the column is nullable and
  `office_entailed_ck` makes null the open-part value; null is the value rule for an absent value]`.
- **Empty list** — a record with no parts is written `[]`, so `["s015p1",<table>,<event_uuid>,[]]`
  `[R — an empty array under the array rule; §5.6 makes zero parts lawful ("vacuous at zero parts")]`.
- **Which parts** — every row of `core_governedeventpart` whose `(event_table, event_uuid)` is the record's own; no
  other row `[S — §5.6 part identity]`.
- Declared by the caller in `parts_commitment` and verified at commit, from the event side and the part side
  `[S — §6 row 4; 0022 guardians parts_binding and part_referent]`.

## 4. Examples

The fixed vectors are in `s015r1_s015p1_fixed_vectors.json`, derived by `derive_s015r1_s015p1_vectors.py`
independently of any SQL, after the serialiser first reproduces all sixteen published `s015f3` and `s015f4` vectors.

    ["s015p1","core_livingorganismevent","aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",[]]
    ["s015p1","core_livingorganismevent","aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",[[1,"decision","OPEN","Recorded at the founding meeting",null],[2,"subject","HELD_CLOSED","Personal data of the founder","PRIVACY"]]]

## 5. What does not change

`s015f4` (the event-set fingerprint) and `s015c1` (L2, still refused under U-14). The set fingerprint covers the
committed row including its `l1_commitment` and `parts_commitment`, as before.
