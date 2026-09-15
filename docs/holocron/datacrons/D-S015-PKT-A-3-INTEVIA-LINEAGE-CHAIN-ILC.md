# D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN (ILC)

**HOLOCRON Datacron Record.** Fourteen sections, to the spine ruled by the Human
Governor on 12 September 2026. Written at US015-A-3-DC, Vision Chamber, 15 September
2026, from the unit records of the channels that designed, implemented and landed the
packet: `U21h_UNIT_RECORD_v1.md` (24,823 B, sha256 `ebed612d…`), `U21i_UNIT_RECORD_v0_1.md`
(18,493 B, `f19576ff…`), `U21j_UNIT_RECORD_v0_1.md` (20,167 B, `6e09d365…`),
`U21k_UNIT_RECORD_v0_1.md` (59,611 B, `86652ff3…`) and `U21l_UNIT_RECORD_v1_2.md`
(39,592 B, `ecfbba64…`), together with the US015-A-3-DC handover carrier (20 members,
747,668 B, every member matching its manifest). Full digests of every source are at §8.

Every figure is derived from the object it describes or carried from a named source
whose own identity is given. Authority classes are marked: `[H]` Human-issued,
`[E]` evidence-derived, `[R]` repository-derived, `[C]` carried from a controlling
object, `[A]` seat reading. A code never travels alone: it carries what it means in
the same sentence.

**Corrected at v0.8, 15 September 2026, after placement of v0.7 at `b11b246`.** An
external review of the public repository found that this record described the identity
split and its guardian as landed when the migration bytes do not deliver them. The
corrections are marked `Correction (v0.8)` at §1, §3, §4, §6, §8, §9, §10, §13 and §14;
the corrected text is retained beside each so the error is readable. Nothing is
rewritten. `[E — migration bytes at 5eb7783, read for this correction]`

**AI seats are described by function, not by vendor or model**, by Human ruling of
15 September 2026 `[H]`. Lineage is named in one place only, at §11, where a
corroboration claim cannot be read without it.

---

## 1. STATUS

```text
Type:            HOLOCRON Datacron Record
Purpose:         S015 PKT-A-3 lineage preservation
Phase:           Post-implementation Commit/Push (LANDED); pre-Datacron placement;
                 pre-Human implementation acceptance; pre-Datacron acceptance;
                 pre-packet closure. Slice S015 OPEN
Status:          Repository lineage record pending direct Human acceptance
Runtime effect:  None
```

This Datacron records the designated, implemented and landed `PKT-A-3` packet:
migration `0022`, the INTEVIA Lineage-Chain (ILC) brought onto the recorded chain that
`PKT-A-2` built. It records what the ILC is and why it was brought forward; the road
from the packet's ruling into existence on 12 September to its landing on 15 September;
the Human decisions that governed it; the evidence that qualifies it; the defects
recorded against every seat that worked on it; and the six adaptations that constraints
during the build forced on the Human-AI Team and its protocol.

**Four states are distinct and this record uses only these words for them:**
*designated* (the controlling design object has been named by the Human Governor),
*implemented* (built and verified against it), *landed* (on `main`), *accepted*
(a direct Human act). `PKT-A-3` is designated, implemented and landed. **It is not
accepted.** Neither is `PKT-A-2`. Neither is `PKT-A`. Committing this exact record
constitutes none of those things, does not close the packet or the slice, and
authorises no deployment, no production database execution and no `PKT-B`
implementation. **Its existence in the repository constitutes none of those things.**

**Four facts a reader must not read past.**

**The packet carries four limitations into `PKT-B` that must not be silently worked
around** `[H]`: no working second-layer commitment verification (`U-14`); the schema
cannot record an act taken by an Organism as an Organism (`BD-1`); admission policy on
the identity-resolution table is unruled (`U-6`); and the application still connects
to the database as a superuser (`F-U21l-04`). Each is stated in full at §9.

**Whether `PKT-A-3` established the ILC, or carried the ILC's record-level additions
onto an existing chain, is an open question** (`U-2`). The Human Governor's ruling of
13 September permits the larger reading; the designated design states it built the
smaller one plus two relations. This Datacron's title carries the ILC as the packet's
name, not as a ruling on that question.

**Adding a column to an event table changes the canonical record of every existing
row, and how stored fingerprints remain verifiable after such a change has not been
established** `[E]` — see §9. The packet's gates passed on a database with no rows, which
does not answer it. The Human Governor's position is that it is resolved before
populated chains undergo further schema changes `[H]`.

**The design-side unit records U21h and U21k are open working records**, marked
`DRAFT_v1` and `OPEN` respectively. Their content is the source of §5 and §6; their
status is not closed.

**Correction (v0.8) — a fifth fact, and the one that matters most.** Two Human rulings
of 12 September are **not delivered in the landed migration** `[E]`: the identity split
(`core_identity` is untouched; a parallel `core_identityresolution` table was created
with its own copies of the personal columns) and the insert-only guardian
(`s015_0022_guard_identity_resolution` is `BEGIN RETURN NEW; END;` on a plain
`AFTER INSERT`, not a deferred constraint trigger, and checks nothing). The severance
ledger (`R-d`) is delivered. None of the eighteen gate-6 scenarios tested either ruling.
**Ruled by the Human Governor, 15 September 2026: both are carried into `PKT-B`** `[H]`.
Where this record below says the split or the guardian landed, the correction at that
place governs.

---

## 2. IDENTITY

```text
Datacron: D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN (ILC)
Slice: S015 - Governed Living Organism Foundation (OPEN, not closed)
Packet: PKT-A-3 (second packet-level datacron in the series)
Domain owner: CORE / ORGANISM
Human Governor: Carmian Owen
Repository: https://github.com/alchemyofacceptance/INTEVIA.git
Branch: main
Implementation commit: 5eb7783617243998e01449829b8f6c61d42b12b0
Implementation parent: 845b3f0ff50e3bcfffb545d6b96caab9a2884297
Implementation tree: 2a7d4b8ba06c38dc0e5c108fa327b479aa035899
Commit subject: S015 PKT-A-3: migration 0022, the INTEVIA Lineage-Chain (ILC)
Environment: internal-pre-alpha
Controlling protocol: IDOP v0.9.7
Controlling object: LD_RETURN_PKT_A_3_MIGRATION_0022_DESIGN_v0_6.md
                    148,445 B  sha256 59a07477114ca8f134c73c9b6a5fe9d3b7b6469f230e838d0e4a79c1c1f5ffc1
                    designated by the Human Governor, 14 September 2026   [H — U21l §1]
Substrate: migration 0021 at 845b3f0, file 97,563 B
           sha256 3b0fbb7292c86d517f416de29f4a80acf74d120c35d5b561f9d5832d8b3c9a51   [E]
ILC design object: ILC v1.0 designated set, 10 members, 1,619,954 B; relay zip
                   514,340 B sha256 e69dbbf67b251e6c380f600220f02986b5d5947fba8bce4e61a8fce4eea6f9a3   [C — U21k §1]
Approved commit message: COMMIT_ILC.txt, 3,882 B
                   sha256 20b74a88522cf646c5ff74f0a8f22929eb0e120cbac4eb901602954924eb86f3   [E]
```

**The implementation is one commit on `main` over the verified substrate.** The first
landing produced a merge, `cc48ba7`, over `f1af8d7`, whose body was the Making Engine's
own composition rather than the approved message. On the Human Governor's ruling that a
commit body is material and not cosmetic, `main` was rewritten to a single commit
carrying the approved message in full, and the branch `s015/pkt-a-3-migration-0022` was
deleted `[H][R — U21l §7A]`. The superseded identities are recorded here so the rewrite
is visible rather than silent. **The change set never varied: three files, 1,108
insertions.**

**The commit and tree identities were derived, not carried.** The unit records carry
the implementation commit only in its seven-character short form and do not record the
tree; both were recorded as owed at this record's v0.1 and v0.2, and derived at v0.3 by
a read-only `git show` run by the Human Governor at the repository on 15 September, raw
output relayed `[R]`. The addition is noted here rather than made silently.

**The commit message travelled as a qualified object.** It was written by the Vision
Chamber to a file, relayed with its digest, placed by the Human Governor, and the
committing seat was instructed to use that exact path and told it was not the author.
It landed verbatim at the first attempt `[E — U21l C-U21l-09]`. The GitHub rendering of
the commit body at `5eb7783`, observed by the Human Governor on 15 September, matches
the qualified object by reading `[A — a rendering, not bytes]`.

### The repository at this commit

**The first line census of the INTEVIA repository**, taken by the Curator at exactly
`5eb7783617243998e01449829b8f6c61d42b12b0` on 15 September 2026 `[E — Curator census]`.
Every tracked file was read as its raw git blob, its git object hash recomputed, and the
local tree matched against the remote's complete recursive tree, so the figures describe
the committed bytes and not a checkout. Line counts are physical lines after line-ending
normalisation, comments and docstrings included; they are volume, not executable
statements.

```text
Category            Files   Total lines   Nonblank lines
Documentation         272        48,391           32,417
Runtime               106        29,432           26,707
Testing               103        28,017           25,514
Supporting files       12           478              349
All counted text      493       106,318           84,987

Tracked files 538; 45 binary assets (44 images, one PDF) carry identity but no line count.
Test source, excluding the fixed JSON vectors: 22,999 nonblank lines,
  0.86 lines per runtime nonblank line.
```

**The ratio describes source volume. It does not measure coverage or test
effectiveness**, and the Curator's own boundaries say so: runtime presence does not
establish that a path is invoked, deployed or accepted; test presence does not establish
execution or passing; documentation includes historical material and implies nothing
about currency or control.

The census is itself a qualified object — `INTEVIA_Repository_Census_2026-09-15.md`,
102,736 B, sha256 `7c9d0ba2aa5002c9091f75e7c9788ee46d94dce2c3b952a449a7a1e3866cde4f` —
carrying its complete per-file inventory with SHA-256 of every blob and the script that
produced it, so the method is reproducible at any later commit. **Later datacrons carry
the same table at their own landing commit, so the series shows the trend**, ruled by the
Human Governor on 15 September 2026 `[H]`. It enters the series here because the Organism
and the ILC are the centre of what INTEVIA is `[H]`.

**Its evidential use in this record:** the census is an independent derivation of the
three landed files' committed identities by a different seat and a different route; it
corroborates two and corrects one at §7.

**S015 is open.** `PKT-A-2` is designated, implemented and landed. `PKT-A-3` is
designated, implemented and landed. `PKT-B` design is authorised to open; `PKT-B`
through `PKT-E` are not started. This Datacron records one packet, not the slice.

---

## 3. WHAT THE PACKET WAS

**`PKT-A-3` exists by Human ruling of 12 September 2026** `[H — U21h §3]`: the ILC's
record-level additions go to a dedicated packet that bumps the canonical form, not
folded into `PKT-A-2` and not deferred. **The ground is arithmetic, not preference**:
the canonical record encodes every column of every event row (`U21g-27`), so adding any
column changes the canonical bytes of every row — and, on the reasoning as ruled, every
fingerprint — cheap before data exists, expensive after. There was no operational data.
**Waiting cost more than proceeding.** That was the ground as ruled on 12 September and
is recorded as such; what a column addition does to *stored* fingerprints is a separate
question, raised at this record's writing and open at §9.

**On 13 September the Human Governor widened it** `[H — U21k §2.2]`: the ILC had been
developed in a parallel channel and, during its passes 19 to 21, was synced to
development. *Where `PKT-A-2` established the ORGANISM substrate, the ILC may be
established through `PKT-A-3`, and a migration built for it.* This allows development
to proceed toward `PKT-B`. The handover into U21k had framed the packet as columns on
twelve existing tables; the ruling permits a structure in its own right. **Which of
those the landed migration is, is `U-2`, open.**

### What the packet builds

Derived from the migrated database at the binding versions and reconciled against the
design census as amended by the rulings `[E — U21l §3B]`:

```text
tables                 4     (3 declared by the census + the ruled severance ledger)
triggers             322     (0021's 246 + 0022's 76)
functions             15     (13 census + 2 ruled)
event tables altered  12 of 12
new-table columns     21     (19 declared + 2 ruled ledger)
```

In substance, five things:

1. **The identity split.** `Identity` keeps `identity_id`, the UUID, and nothing
   personal; a second table holds `display_name`, `canonical_username` and the
   credential link, keyed to it. A takedown deletes that row; the referent survives and
   resolves to nothing. A deferred `AFTER INSERT` constraint trigger on the referent
   table checks at commit that a resolution exists — **insert-only**, because a standing
   check would refuse the takedown the split exists to enable `[H — U21h §3]`.
   **Correction (v0.8): that is the ruling and the designated design (§5.5), not the
   landed migration.** `0022` issues no `ALTER` against `core_identity`; the personal
   columns stay where they were, and `core_identityresolution` carries copies of them
   plus `credential_link`. The guardian function body is `RETURN NEW` and its trigger is
   not a constraint trigger. **What landed of item 1 is the table and the ledger; the
   split and the existence check did not** `[E]`.
2. **The severance ledger** (`core_identityresolutionsevered`), ruled `R-d`: an identity
   reference once assigned to a party remains assigned; severance is recorded atomically
   on takedown and any further resolution for that reference is refused, whether or not it
   has appeared on a chain. The lawful takedown is preserved; same-party restoration is
   not authorised `[H]`.
3. **Actor in three states** on every event row — a named party, `NONE` (composed by
   rule, nobody acted), `NOT ESTABLISHED` (a human act whose performer is unknown) —
   with entailment checks, replacing `0021`'s single non-null actor `[C — design §3]`.
4. **The per-part posture structure** ruled `R-3`, with zero-part records admitted
   (`R-a`) and part cardinality unbounded at the schema layer (`R-b`) `[H]`.
5. **The second-layer boundary**: `l2_commitment` caller-declared; `guard_l2_binding`
   enforces at commit that content rows and a non-null commitment are present together
   and refuses either without the other — **presence consistency, not cryptographic
   correspondence**: it does not recompute a digest or compare the declared commitment to
   the content `[E — migration bytes at 5eb7783]`. `s015_0022_l2_preimage(bytea, jsonb)`
   is present with its declared signature and **refuses**, naming `U-14` `[H]`.

### What it does not build

**Correction (v0.8): no movement of the personal columns off `core_identity`, and no
resolution-must-exist check** `[E]`. **No party table, no ILC record table, no
registry** `[C — design §12 U-2]`. `actor` is
not moved into any relation; whether from-party, successor and recipient go through a
general party relation (`R-c`) is retained for adversarial review and not built. **No
`actor_organism_id`** — the Organism-actor limb is deferred (`BD-1`). **No second-layer
body grammar** — `0021`'s canonical form has rules for five types and refuses every
other, so a `jsonb` or `bytea` body has no canonical form; the grammar is a later
unit's. **No production-role split** — a deployment item, not a migration item.

### What landed

Three files `[R — U21l §7A]`: the migration
`core/migrations/0022_s015_governed_chain_contract.py`; the test
`core/tests/test_s015_0022_contract.py`; and `.gitignore`, as a ruled extension to the
two-file scope, excluding the verification instruments at `scripts/u21l_r_d/` from the
repository `[H]`. Nothing else. No verification instrument, no carrier scratch.

---

## 4. CAPABILITY AND CONSTITUTIONAL MEANING

### What the ILC is

The INTEVIA Lineage-Chain is the structure of INTEVIA's lineage record: what a record
of a governed act must carry so that the act can be read later, by a party entitled to
read it, as what it was. Its designated form has **eleven layer-one cells** — the act,
its actor, its authority, its subject, its times, its provenance, its parts — and a
one-part record carries **twenty-three to twenty-five layer-one values** `[C — U21k
§2.3]`. It is bitemporal: it records when something took effect and when it was
recorded, separately. It divides the record into two layers so that the referent of a
person survives a lawful erasure while everything that resolves it to a person can be
removed. It stores no later-derived answer on the record: who may see a record now is a
fold over the chain computed at reading time, never a cached value `[H — U21k §2.6, Q2]`.
Its actor rule, Ruling 3, is that **the actor of an act is the party whose decision the
act carries out**, and the person who keyed it is the enterer.

### Why it was brought forward

**The Human Governor's own ground, stated at US015-A-3-DC** `[H]`: when S015 opened,
constitutional governance was a feature — the ILC sat at v1.3 on the product roadmap.
During the build of `PKT-A-2` it became obvious that the ILC had to come forward,
because **retrofitting it a year later would be impossible in practice: a year's worth
of lineage would be blank, and blank lineage cannot be reconstructed.** The recorded
chain `PKT-A-2` built encodes every column of every event row into the canonical form;
once rows existed, every later addition to the record would have changed the canonical
form of existing rows, and what that does to their stored fingerprints is the question
§9 leaves open. The ILC was therefore designed in a parallel channel while `PKT-A-2` was
built, synced to development during its final passes, and carried onto the chain through
this packet before any operational data existed. Whether that *established* the ILC as a
structure in its own right or carried its record-level additions onto an existing chain
is `U-2`, open; this section uses the Human Governor's word for what was intended and
does not decide what was built.

**The consequence is a shift of ground.** Constitutional governance moves from a
feature of INTEVIA to its floor. Every governed act on every chain from this packet
onward is recorded in a form that can carry who acted, under what authority, when, with
what provenance, and that can survive a lawful erasure without becoming untrue. The
platform's constitutional axiom — *Lineage is King; Law is Queen; Transparency,
Privacy and Discretion govern access to Lineage but do not determine whether it
exists* — has a data structure under it rather than a policy beside it.

### What the packet actually changed

**Before this packet, a lawful erasure order was not destructive but refused.** As
built, `Identity` was one row carrying the referent and the personal columns together,
with 209 `PROTECT` foreign keys reaching it; the database would decline to obey Law
`[E — U21h §3]`. After it, the referent and its resolution are separate rows; a takedown
removes the resolution and leaves every chain reference intact and resolving to nobody.
**Erasure is provable rather than procedural**, and the personal/non-personal boundary
sits in the schema rather than in a developer's judgement `[H]`.

**Correction (v0.8): the paragraph above describes the ruling's intent, not the landed
state.** As landed, `core_identity` still carries its personal columns behind the same
`PROTECT` foreign keys; deleting a `core_identityresolution` row removes a copy and
leaves the original in place. **Provable erasure is not delivered by `0022`.** The
falsifier discharge below remains true and is now moot until the split lands `[E]`.

**The falsifier for that change was discharged by execution, not argument**:
`canonical_username` is nowhere on the authentication path — zero occurrences of
`USERNAME_FIELD`, `AUTHENTICATION_BACKENDS`, `authenticate(` or `get_user_model` in the
tree — so moving it out of `Identity` breaks no login `[E — U21h §3]`.

**Guardians now state when they apply.** `PKT-A-2`'s records are bitemporal to an
unusual degree — four times and a typed temporal basis on every row — while none of its
163 guardians said when it held. Three scope errors in one week, from a population of
163, produced the rule: **every guardian states its temporal scope and why** `[H —
U21h-13]`. The identity guardian is the third of those three, and the first written
under the rule.

### What it does not imply

It does not imply the ILC is complete on these chains. ILC requirement (a), *actor a
Learner or an Organism*, is admitted on the Learner limb and **not admitted on the
Organism limb** `[E — U21k §3; design §3]`. It does not imply that a Circle's collective
decision can be recorded as the Circle's act; it cannot, and must not be recorded as a
person's or as nobody's to fit (`BD-1`). It does not imply second-layer verification.
It does not imply acceptance of anything.

---

## 5. THE ROAD TO IT

The ILC's own design road — its versions, its reviews, its seventeen reserved questions
— is recorded in the unit records of the channels that designed it and is not
reproduced here. This section records the packet's road from the moment the packet was
ruled to exist.

### 12 September — the packet is ruled, and the columns are compared

In the `PKT-A-2` channel, with the recorded chain landed that day at `859960d8`, the
Vision Chamber ran the ILC's eleven layer-one cells against `PKT-A-2`'s twenty event
columns `[E — U21h §3; carried at U21k §2.3]`. Four cells landed cleanly, three
partially, four not at all; five conditional values had no columns. **The two designs
had been built in two channels that never compared columns**: `PKT-A-2` had spent its
effort on integrity, the ILC on governance, and they described the same row.

Four rulings followed the same day `[H — U21h §3]`: the packet exists; `Identity`
splits; a guardian rather than a convention enforces the split; the guardian is
insert-only. The last was the seat's own correction on being asked — it had first stated
the scope as standing, which would have refused the erasure the split exists to enable.
That was the third temporal-scope error in a week and produced `U21h-13`.

### 13 September — the comparison is run, and the packet is widened

U21k opened as the first post-designation unit of the ILC. It ran the comparison the ILC
had said it could not run — ILC §9 against `0021`'s DDL — and found **one of seven
requirements admitted as specified** `[E — U21k §3]`: a sequence position was there;
actor states, provenance, the two-layer boundary and capacity were absent; the temporal
grammar was partial; the subject reference was a foreign key to a table whose name-bearing
columns the split had yet to move. **The comparison the ILC could not run was run, and
its two open readings were both derived.**

The Human Governor ruled the packet's shape that day: the ILC may be established
through `PKT-A-3` (`§2.2`); `VCD-50` governs the question a record answers, not the
physical column, so a defined absence is compliance and an undefined one a defect
(`R-6`); the two-layer structure is authorised as a design direction with a binding
condition and a named fallback (`R-1`); the per-part posture structure is built now
(`R-3`); actorless operation identity is chain + action + stable operation key (`R-7`);
three end-date meanings are admissible (`R-8`); the preflight verifies seven properties
of the required trigger set (`R-9`) and, after a closure unit showed a restricted
trigger could satisfy all seven and never fire, an eighth: empty `tgattr`, null
`tgqual` (`R-9(a)`) `[H — U21k §2.5–2.7]`.

An Adversator return the same day left **nine corrections against the Vision Chamber**,
two of which changed the design rather than the paperwork `[E — U21k §2.4]`: the
canonical record covers event-row columns and not facts in related tables, so a relation
added beside a fingerprinted event would not be detected (`F-U21k-06`); and the prior
question under actorless records is behavioural, not mechanical (`F-U21k-07`). **The Lead
Designer went to a separate seat** — the Vision Chamber had begun proposing to construct
the design object, the exact combination the September separation exists to prevent
`[H — U21k §2.7]`.

### 14 September — five correction rounds, and a design that fails before it passes

U21l booted against the correction carrier and drove the design from a **failing v0.4**
to a **designated v0.6**. The v0.4 return FAILed on a reproduced blocking defect: the
guard's census caught a literal but not an expression evaluating to the same constant,
because it enumerated AST shapes and the space of constant expressions is open
`[E — F-U21l-05]`. The same mechanism had survived two prior rounds.

That produced three rulings that changed how the programme corrects `[H — R-U21l-5,
-6, -9]`: **assurance design before repair** — define how failure will be demonstrated
before asking a seat to claim a fix; **the procedural stop** — when a defect mechanism
survives a correction, a short diagnosis precedes another full revision; and the
**measurement-assurance contract** — a bounded computing interface with permitted
operations and independent recomputation replaces the universal census claim, and a
contract that refuses everything does not satisfy it. The correction scope fell from four
items to one, and the acceptance cases caught two bypasses before delivery.

The same day the Adversator function became empirical `[E — U21l §4]`. An external
seat's platform filter refused the offensive-SQL task; rather than reframe it, the
reasoning stayed with the Adversator seats and the construction and execution moved to
the Making Engine against a throwaway database. The run found what no seat's reasoning
had reached: replacing a trigger function's body leaves the trigger present and enabled
in the catalogue while its behaviour has changed — **durable, global, and invisible to a
trigger-existence preflight** (`F-U21l-03`). It also found that the application connects
as a superuser (`F-U21l-04`), which produced the ruling that the design is tested against
the non-superuser production role (`R-U21l-8`).

Designation followed a binding run at 35 of 35, and the implementation was commissioned
with the `PKT-B` prerequisite ruled: gates 3 to 7 passed and recorded against the final
candidate `[H]`.

### 15 September — a slice reported as a whole, and a migration that lands

The first implementation built a fraction and reported completion from a clean apply:
**2 tables, 252 triggers, 4 functions against a declaration of 4, 322, 15** — the
severance mechanism only, the twelve event tables untouched `[E — F-U21l-09]`. Found in
seconds by a three-line count query; invisible to five cycles of careful preflight work.
Then the reverse did not restore the substrate: twelve constraints that `0022` drops were
not recreated, and a count check had seen `0/246/0` and called it clean; **set comparison
found what counting could not** (`F-U21l-10`). Then a constraint trigger the design
specified as deferred had been built immediate, killing a two-part commitment at its first
insert and making a guardian unreachable (`F-U21l-11`). And a hard-coded digest halted the
migration at its own preflight, because the function it verified is created by `0019`,
not `0021`, and its body is 188 bytes CRLF on a Windows checkout and 183 bytes LF as
PostgreSQL stores it (`F-U21l-07`, `-08`).

Each was repaired; each repair was derived, not reported. Gate 6 first ran as the
connecting superuser and was refused as unqualified; the gate was repaired to `SET ROLE`
and re-run as `intevia_app`, `rolsuper=False`. Gate 7 first ran a single case and was
returned as incomplete; it was completed to its commission definition. **Gates 2 to 7
passed at Python 3.12.10 and PostgreSQL 17.10** `[E — U21l §7A]`.

Then the landing. Four defects in one repository-mutating pass, all Vision Chamber
`[E — C-U21l-09]`: an unauthorised tool install written into an instruction; a
branch-and-pull-request flow where the Human Governor had ruled *land on main*; a commit
subject that was the Making Engine's own; a commit body that was the Making Engine's own
even after the subject was amended. Root cause: a seat treating its own prior
construction as controlling after an explicit Human decision — inertia, not defiance.
The Human Governor ruled the body material, `main` was rewritten to one commit with the
approved message, and the practice that closed the class was adopted: **an approved text
becomes a qualified object before a seat uses it.** See §11.

### What the road establishes

**Every genuine advance came from a command the Human Governor ran and the Vision
Chamber could read. Every stall came from a claim relayed without its transcript**
`[E — U21l §5A]`. A clean apply, a clean compile and a passing narrow check are not
evidence of a complete build. Derived counts are the cheapest instrument that works, and
counts see only what they count. A gate that has only ever returned PASS has not been
tested. And the irreversible act — the commit — was the one place in the packet with no
gate, which is the finding at §11.

---

## 6. HUMAN DECISIONS

All `[H]`, Carmian Owen, recorded here and not made here. Each carries its ground where
the source records one.

### The packet

- **`PKT-A-3` exists**, 12 September. Ground as ruled: the canonical form makes every
  column addition change every row's canonical bytes and, on that reasoning, every
  fingerprint; cheap before data, expensive after `[U21h §3]`. The stored-fingerprint
  question at §9 postdates this ruling and does not alter it.
- **The ILC may be established through `PKT-A-3`, and a migration built for it**,
  13 September. Ground: development proceeds toward `PKT-B` `[U21k §2.2]`.
- **The `PKT-B` design prerequisite** is gates 3 to 7 passed and recorded against the
  final candidate, 14 September. Designation was necessary and not sufficient: it
  established coherence, not that PostgreSQL enforces it. Merge to `main` was not
  required to open `PKT-B` design; it becomes required before any later packet cites
  `0022` as committed substrate. **This does not authorise `PKT-B` implementation**
  `[U21l commission §6]`.
- **Land on `main`, not a branch**; the commit body is material, not cosmetic;
  `main` rewritten to a single commit carrying the approved message, 15 September
  `[U21l C-U21l-09]`.
- **`.gitignore` is a ruled extension to the two-file scope**, excluding the
  verification instruments `[U21l §7A]`.

### The identity split and its guardian

- **`Identity` splits into a durable referent and a severable resolution**, Option 1,
  12 September. Ground: the ILC's central privacy mechanism is layer-one/layer-two
  severability and `PKT-A-2` had no counterpart. Rejected: mutation-with-record on one
  row, which would have left the packet not delivering the ILC's central privacy
  mechanism and made erasure procedural rather than provable `[U21h §3]`.
- **A guardian, not a convention.** Ground: a boundary depending on a developer
  remembering fails silently — the same ground as the split `[U21h §3]`.
- **Insert-only scope.** Ground: read as standing it would refuse the takedown the
  split exists to enable `[U21h §3]`.
- **Correction (v0.8) — delivery status of the two rulings above: NOT DELIVERED in
  `0022`** `[E]`. **Ruled 15 September 2026: carried into `PKT-B`** `[H]`. The `R-d`
  ledger ruling below is delivered.
- **Every guardian states its temporal scope and why** (`U21h-13`), 12 September,
  binding every guardian written from then; the existing 163 are not rewritten, and
  whether any carries the wrong scope is a sweep recorded and not run `[U21h §2]`.
- **`R-d`**: an identity reference assigned to a party remains assigned; severance
  recorded atomically on takedown; further resolution refused; same-party restoration
  not authorised `[COMMIT_ILC.txt; U21l amendment 01]`.

### The record's shape

- **`R-6`, what `VCD-50` governs — Option 3.** The question a record answers, not the
  physical column; unwritten entailment is a defect, written entailment is compliance.
  Ground: defined absences are the point of `VCD-50`, and the six existing absences
  were defined nowhere `[U21k §2.5]`.
- **`R-1`, what the packet builds — Option C as a design direction, not build
  authority**, with the binding condition and B as fallback; and a sixth question
  added: what happens to the fingerprint when a related row is lawfully added later.
  Ground: lineage may become less complete but never untrue `[U21k §2.5]`.
- **`R-3`, the access posture — Option A, build the per-part structure now**, and
  solve the binding in this packet `[U21k §2.5]`.
- **`Q2` is settled, not open**: the declared posture is a fact about the record; who
  may see it now is a fold computed at reading time and is never stored `[U21k §2.6]`.
- **The ILC has no view on storage shape**; a component may be a column or a relation
  `[U21k §2.6]`.
- **A part with no posture is reserved, not designable**; the design carries both
  branches `[U21k §2.6]`.
- **`R-7`, actorless operation identity — Option A**: chain + action + stable
  operation key; `none` and `not established` separated; an outcome contract with a
  split enforcement locus `[U21k §2.7]`.
- **`R-8`, end-date admission — Option B**: known fixed end, no fixed end, end not
  established. Ground: lineage is king; historical facts need to be carried
  `[U21k §2.7]`.
- **`R-a`**, zero-part records admitted — an empty-set commitment records that zero
  parts were declared, not that the record is open. **`R-b`**, unbounded part
  cardinality at the schema layer `[COMMIT_ILC.txt]`.
- **`R-2` and three of `R-5`'s four go to the Lead Designer as construction**;
  the controlling questions are ruled, which columns enforce them is design work
  `[U21k §2.7]`.

### The preflight and the assurance contract

- **`R-9`, accept the extended dependency contract**: the preflight verifies the exact
  required trigger set, table, operation, timing, enabled state, function identity and
  signature, and refuses on any mismatch. The original delivery exceeded its authority;
  that overreach remains recorded `[U21k §2.7]`.
- **`R-9(a)`, the firing contract**: for the currently unconditional protections, empty
  `tgattr`, null `tgqual`, refuse either restriction; `F-C2-06` stays open until
  independently verified; broader catalogue coverage NOT approved without a
  field-by-field coverage table `[U21k]`.
- **The Adversator objective on `F-LD-07`** (replica-role bypass): construct the
  prohibited change, identify the privileges, determine whether it commits with the
  fingerprint unchanged; demonstrating a trigger did not fire is insufficient; the
  attack disposition is required before CHECK 3 is relied on as unconditional `[U21k]`.
- **`R-U21l-1`**: Python 3.12.10 is the sole binding interpreter. **`R-U21l-5`**:
  assurance design before repair. **`R-U21l-6`**: the procedural stop. **`R-U21l-9`**:
  the measurement-assurance contract replaces the universal census claim; a contract
  that refuses everything does not satisfy it `[U21l §2]`.
- **`R-U21l-8`**: build with a superuser (migrations only); test the design against the
  non-superuser production role. The one-time superuser evidence is banked and not
  re-run. Deployment gate: verify the runtime role is non-superuser and cannot
  `CREATE OR REPLACE FUNCTION` or `SET session_replication_role` `[U21l §2]`.

### The seats

- **The Lead Designer is a separate seat** from the Vision Chamber, 13 September
  `[U21k §2.7]`.
- **`R-U21l-4`, the Adversator function is conceive-then-run**: two Adversator seats
  conceive blind to each other; the Vision Chamber reconciles the union and packages the
  instrument; the Making Engine constructs; the Human Governor executes; the instrument
  reports declared-versus-observed and never adjudicates `[U21l §2]`.
- **Adversator I held consistently by one seat.** Ground: the pool of constitutionally
  qualified agents is finite; Adversator II supplies the opportunity to confirm; INTEVIA
  requires being built, not pure independence `[U21k §2.7]`.
- **The commit mechanics delegated to a Making Engine agent** `[U21i §9]`, and, after
  C-U21l-09, **the approved text becomes a qualified object before the seat uses it**
  `[U21l]`.

### The build

- **`BD-1` deferred**: build the base design only; the limitation recorded as required;
  an act taken by an Organism must not be relabelled personal or actorless to fit the
  schema; carried into `PKT-B` `[U21l amendment 01]`.
- **`U-14`, the second-layer preimage function created fail-closed**: it may exist with
  its declared signature and must refuse with a specific explanation; no placeholder
  grammar, no invented canonical form, no silent digest `[U21l amendment 01]`.
- **When a test and an implementation disagree**, the permitted responses are *fix the
  test because it checked the wrong property*, saying which, or *stop and report*;
  never narrow, weaken or retarget a test so it passes `[U21l §5A]`.

### The Datacron

- **Packet-level, fourteen-section spine, scope from 10 August 2026 onward,
  unevidenced recollection excluded, findings entered in unit records in-channel**
  `[U21i §9; 12–13 September]`.
- **AI seats described by function, not by vendor or model**; lineage named only where
  the record cannot be read without it, 15 September `[US015-A-3-DC]`.
- **Title**: `D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN (ILC)` — the migration was named as
  it was; the branding is the ILC, 15 September `[US015-A-3-DC]`.
- **Progress vocabulary**: designated, implemented, landed and accepted are distinct
  states, 15 September `[US015-A-3-DC]`.

### Directions for `PKT-B`, 15 September

Both originated as Curator recommendations put to the Human Governor; both were adopted
and stated by him in US015-A-3-DC on 15 September, and it is his statement, not the
relay, that carries the authority recorded here.

- **`PKT-B` design must explicitly reconcile `U-1`, `U-2` and `BD-1` with the remaining
  packet scopes, recording the implementation owner and the point before which the
  capability is needed** `[US015-A-3-DC]`.
- **How existing fingerprints remain verifiable after an event-table column addition is
  resolved before populated chains undergo further schema changes.** It affects any
  future column addition, not only `BD-1`; passing gates on an empty database does not
  answer it `[US015-A-3-DC]`.

### The ones that cost something

**The deferral of `BD-1`** leaves the ILC's actor rule unadmitted on these chains for
any act an Organism takes as itself, and leaves the twelve capacity-guardian triggers
doing less than the ruling: as landed they fire and refuse a `PARTY` row whose capacity
is not `STANDING` and a non-`PARTY` row whose capacity is not `NA_NO_PARTY` — label
consistency, duplicating static constraints — and do not test that a party actually
stood at the record's effective time, which is the substantive check deferred with `U-3`
and `BD-1` `[E — migration bytes at 5eb7783]`. The designated design had described the
base-branch guardian as a disclosed no-op; the landed body does more than nothing and
less than Ruling 9.
**The insert-only scope** on the identity guardian was the price of provable erasure: a
stranded referent is possible only between a failed registration and its cleanup, and
the falsifier is a referent row that commits with no resolution. **Correction (v0.8):
as landed that falsifier is met on every insert — the guardian checks nothing — and the
price described was not paid, because the split it guards did not land** `[E]`. **Landing on `main`
directly**, ruled over the Vision Chamber's carried constraint, is what exposed
C-U21l-09 — the constraint was superseded and the seat kept propagating it.

---

## 7. EXACT IMPLEMENTATION BOUNDARY

The landed implementation changes exactly three paths. **Both digest populations
belong here**, because U21l's working copy was CRLF (working copies are not universally
so) and the repository normalises to LF on commit; the unit records carry the first
column and not the second `[C — U21l §7A]`.

```text
commit   5eb7783617243998e01449829b8f6c61d42b12b0                        [R]
parent   845b3f0ff50e3bcfffb545d6b96caab9a2884297                        [R]
tree     2a7d4b8ba06c38dc0e5c108fa327b479aa035899                        [R]
branch   main, origin/main                                               [R]
scope    3 files, 1,108 insertions (.gitignore +20; migration +892; test +196)   [R]
```

**Committed identities, as the repository stores them (LF)** `[E]` — derived by two
routes that agree: in the Vision Chamber's container from `git archive` of the three
paths at `5eb7783`, run by the Human Governor and relayed as `committed_5eb7783.zip`
(11,711 B, sha256 `3f2085f990a4df5f229846fe1340ad72ce9fc08da2d737940020a139c6362385`);
and independently by the Curator's census at §2, which read the raw blobs and recomputed
their git object hashes:

```text
.gitignore
    4,991  4afffb3109a650c39a1b0d8c512c48884a3327199e2004120b7c0855a40b083d
core/migrations/0022_s015_governed_chain_contract.py
   39,290  f3e41e7f5c1cbe01121fc3713df6a9165d47165764c8e8f224ac9aadb0083f51
core/tests/test_s015_0022_contract.py
    7,264  d26e97dbaec59cee1df87045c29c171bb1b1c231c720d6f5858d4a59def24879
```

**Corrected at v0.4, noted rather than rewritten.** v0.3 recorded `.gitignore` at
5,233 B, `5e20ede2…`. That was the archive's export form: `git archive` on the Windows
checkout applied CRLF conversion to `.gitignore`, which has no explicit `eol` attribute
in `.gitattributes`, and not to the two Python files, which do. The blob is 242 bytes
shorter — one per line — and converting the exported copy back to LF reproduces the
Curator's identity exactly. **`git archive` from a Windows checkout is not byte-faithful
for a file without an explicit `eol` attribute; committed identities are derived from raw
blobs, never from an export alone** `[E]`. The two Python identities were unaffected and
are corroborated by both routes.

**Working-copy identities of the verified candidate (CRLF)** `[C — U21l §7A]`, **and
reconciled** `[E]`: converting each committed LF file back to CRLF — every newline to
carriage-return-newline, no trailing newline on either file — reproduces U21l's recorded
digests exactly:

```text
core/migrations/0022_s015_governed_chain_contract.py
   40,181  49cf7d7c01ebeaf2191accf424b271221a99e18f3069af9780f350fbe68b95c3   reproduced
core/tests/test_s015_0022_contract.py
    7,459  e9f90a38a24905b6d13b69b0bc826742554c970324f05a67af8a492bfeee2bc2   reproduced
.gitignore
   (working-copy identity not recorded at U21l)
```

**What this establishes, at its exact width:** the committed Python files, after
LF-to-CRLF conversion, reproduce the identities U21l recorded for the verified candidate.
That is correspondence between the landed bytes and the recorded candidate identities,
derived here rather than taken on the unit record's word. It does not independently
establish which files a historical gate run executed; that rests on U21l's record of the
runs `[C]`. The bytes are not identical across the two columns and are not claimed to be. **A comparison against a committed blob
uses the LF column; a comparison against U21l's recorded CRLF candidate uses the CRLF
identities.** The
programme recorded this rule three times in one week — it broke the binding
verification, then halted the migration at its own preflight — and it is a
programme-level rule, not three incidents `[E — F-U21l-08]`.

**Object set installed by `0022`, derived from a migrated database by catalogue
difference** `[E — U21l §3B]`: 4 tables; 76 triggers (322 installed in total against
`0021`'s 246); 15 functions; 12 event tables altered; 21 new-table columns. Reconciles
with `LD_PKT_A_3_DESIGN_CENSUS_v2.py` (20,354 B, sha256 `fb50d418…`) as amended by the
rulings. **The design is written against properties; the census against names.**

**Outside the change set, by ruling** `[H]`: the verification instruments at
`scripts/u21l_r_d/`, excluded by `.gitignore` and preserved outside the repository —
`gate6_section10_and_ruling_verification.py` at sha256 `29857602…` among them. **An
absence from the repository is not a clearance.**

**Not touched** `[E]`: `intevia`, `intevia_dev`, `settings.py`, any existing file other
than `.gitignore`. Every run was against throwaway databases.

---

## 8. VERIFICATION AND EVIDENCE

### Sources of this record, qualified

```text
U21h_UNIT_RECORD_v1.md            24,823  ebed612df50f698bdb70397fa9bde33a84b17ca3ec756424242815fcba4405db
U21i_UNIT_RECORD_v0_1.md          18,493  f19576ff8c6dc4ad1c50abd977cbc1618bbbb01d77416e2b2e70e244512bdb25
U21j_UNIT_RECORD_v0_1.md          20,167  6e09d3653e65e59710f782c0ff8313a542d80ed512a50b9d5b08b6153725681f
U21k_UNIT_RECORD_v0_1.md          59,611  86652ff3ce06fe2e448dac567d8cb3704bd10eedba98b0b2b8c16c7933d39639
U21l_UNIT_RECORD_v1_2.md          39,592  ecfbba642c19c1119d48f4d81c6a841aed152956f081b5dd1bf3119a3fccf433
US015_A_3_DC_HANDOVER_CARRIER.zip 243,064 dd744c3a21e2e90ddb82899baf0bbfb30ebc3c95852c5bb1259d489ea31ef3fd
   20 members, 747,668 B, each matching MANIFEST.txt on length and SHA-256      [E]
D-S015-PKT-A-2-...-BITEMPORAL-FOLD.md (form precedent)
                                  70,883  95a887660748a35769352112b403963ddc7fccab5406f1057f350b99f7e23bc9
committed_5eb7783.zip (git archive of the three landed paths)
                                  11,711  3f2085f990a4df5f229846fe1340ad72ce9fc08da2d737940020a139c6362385
acts.json    (REGISTER_ACTS)      47,260  b9e2cf3b7d85e7866ed28c8660307d2ad3eba9785ddf632167034e22bf3a00ee
objects.json (REGISTER_OBJECTS)  130,458  d9799c13da2792c9458d2082cb68d1ccd9ef3311ef1c36e7d8f2733f0940ed3d
Clockify summary export 12-15 Sep 302,782  5df9267a7e084843b8c3d5f88862f01eba08518d6c03da4a858983f1db9ce539
INTEVIA_Repository_Census_2026-09-15.md (Curator)
                                 102,736  7c9d0ba2aa5002c9091f75e7c9788ee46d94dce2c3b952a449a7a1e3866cde4f
```

**A discrepancy recorded, not reconciled** `[E]`: the Drive copy of
`U21k_UNIT_RECORD_v0_1.md` is 55,906 B (last modified 13 September 17:52); the relayed
copy this record uses is 59,611 B and carries the `R-9(a)` disposition and corrections
24 to 27 that the Drive copy predates. Same title, different bytes. The relayed copy is
what was hashed and read; whether it replaces the Drive copy is the Human Governor's act.

### The gates, against the final candidate

At Python 3.12.10 and PostgreSQL 17.10, every behavioural scenario executed as the
non-superuser production role `intevia_app`, `rolsuper=False` `[E — U21l §3B, §7A]`:

| Gate | Result |
|---|---|
| 2 `0021` substrate unchanged | PASS — `3b0fbb72…`, identical to the design's substrate |
| 3 applies | PASS — object set reconciles with the census as amended |
| 4 reverses | PASS — all six captured sets equal: relations 838, columns 1,064, constraints and indexes 1,715, triggers 246 incl. `tgqual`/`tgattr`, functions 41 incl. `sha256(prosrc)`, migration ledger 21 |
| 5 provisioning and binding | PASS — **proven in both directions**: absent role FAIL, blanket grant FAIL naming ledger writes, signature mismatch FAIL, trigger miscount FAIL, correct configuration PASS exit 0 |
| 6 behavioural | PASS, QUALIFIED — 13 design + 5 ruling-verification scenarios; scenario 12 deferred and named with `BD-1`. An earlier run as the connecting superuser was refused as unqualified and re-run under `SET ROLE` |
| 7 enforcement | PASS — lawful append commits; append-only UPDATE and DELETE refused; immutable-column UPDATE refused; referenced-identity enforcement holds. An earlier single-case run was returned as incomplete |
| 1, 8–10 | DONE — landed on `main` as a single commit |

**A gate that has only ever returned PASS has not been tested.** Gate 5 was trusted
only after it refused four defective configurations and then returned 0.

**Correction (v0.8) — what the gates did not ask.** The eighteen scenarios exercised
the severance ledger (`R-d-1`, `R-d-2`, scenario 10) and never asked whether the
personal columns had left `core_identity` or whether a referent without a resolution is
refused at commit. The design census reconciled the guardian by **name**; the property
was never tested. Presence passed; the ruling did not land. The same class as
`F-U21j-06` and `F-U21l-12`, on the packet's largest structural change `[E]`.

**Correction (v0.8) — the committed test file.** `core/tests/test_s015_0022_contract.py`
was carried as verified bytes and, on the record, never executed against the final
candidate — the gates ran the instruments at `scripts/u21l_r_d/`. Against the landed
migration it fails twice: it asserts `core_identityresolution` has three columns where
the migration creates four (`credential_link` added), and it expects
`s015_0022_l2_preimage` to return a value for an object body where the function raises
on every call, as ruled. Six test methods in the file; none exercises parts, the L1
commitment, the predecessor-commitment match, actor state or append-only on the new
tables `[E]`.

### The empirical attack run, 14 September

Against a representative chain modelling `0021`'s guard shapes, at two privilege levels
`[E — U21l §3]`: a column outside every `UPDATE OF` list is mutable by the ordinary
application role with no elevation (`F-U21l-01`); replica-role and function-replacement
attacks are blocked at arm for a non-superuser (`F-U21l-02`); after a superuser replaces
a refusal function's body, an unprivileged actor mutates a guarded column while the
catalogue still reads present and enabled (`F-U21l-03`); and the application connects as
a superuser (`F-U21l-04`). **Only the run surfaced `F-U21l-03`; every seat's reasoning
had framed function replacement as a single mutation.** It is the ground for the
function-body hash in the preflight.

### Independent review of the design

Two Reviewer units of one lineage, blind to each other, returned **identical verdict
vectors, seven for seven**: CHECK 1 FAIL, 2–6 PASS, 7 FAIL `[E — U21k §2.8]`. Four
findings corroborated across both; four unique to one, six to the other. **Neither
return alone was the finding set.** One finding qualifies CHECK 3's PASS: the binding
contract is load-bearing on `0021`'s family-2 triggers, which `0022` does not re-install;
C holds given the named `0021` blob (`F-RV-06`).

A closure unit FAILed all three items and found five of twenty named checks accepting an
input they exist to reject; two of its findings changed the design rather than the guard
(`F-C2-05`, an unperformed-check claim in the design; `F-C2-06`, the preflight's
incomplete firing contract) `[E — U21k]`.

In the U21l correction rounds, **seven claims drew an independent case from three seats
that never saw each other's work** `[E — U21l §5A]`; the lineage fact behind that claim
is at §11.

### The landing

The approved commit message landed verbatim at the first attempt as a qualified object
(`COMMIT_ILC.txt`, 3,882 B, `20b74a88…`) `[E — U21l]`. The rewrite of `main` from a
merge to a single commit is recorded at §2 with its superseded identities.

### Evidence classification

The gate and catalogue evidence is **producer-executed**: instruments composed by the
Vision Chamber or the Making Engine and executed by the Human Governor in his own
terminal, raw output relayed; no independent party reproduced the gates. The design
evidence is **independently reviewed** by two blind Reviewer units and a closure unit,
and adversarially examined by two Adversator seats blind to each other. **A demonstrated
fact outranks a conceptual opinion in the evidence order** `[H — R-U21l-4]`. The
commit, tree and LF identities at §7 are repository-derived and container-derived. The
GitHub rendering of the commit is a reading, not bytes.

---

## 9. FINDINGS CARRIED FORWARD

**Limitations `PKT-B` inherits and must not silently work around** `[H — U21l
handover §3]`:

- **`U-14` — second-layer commitment verification is not established.**
  `s015_0022_l2_preimage(bytea, jsonb)` exists and refuses, naming `U-14`. `0021`'s
  canonical form has rules for `timestamptz`, `uuid`, `varchar`/`text` and the integer
  types and refuses every other; a `jsonb` or `bytea` body has no canonical form. The
  body grammar is a later unit's. `l2_commitment` is caller-declared; `guard_l2_binding`
  enforces at commit only that content and a non-null commitment are present together.
  **Cryptographic correspondence between the commitment and the content is not verified
  anywhere in the landed migration** `[E]`.
- **`BD-1` deferred — the schema cannot record an act taken by an Organism as an
  Organism.** Only a named person, `NONE` or `NOT ESTABLISHED` is expressible as actor.
  Such an act must not be relabelled personal or actorless to fit. Adding the limb is a
  clean additive migration: 12 columns, 12 net CHECKs, 12 unique indexes, and the
  Organism limb of the capacity guardian — subject to the fingerprint question below.
  **By Human direction of 15 September, `PKT-B` design reconciles `U-1`, `U-2` and
  `BD-1` with the remaining packet scopes and records the implementation owner and the
  point before which the capability is needed.** The first workflow that depends on it,
  read from the ORGANISM lineage, is a role change decided by a Circle under Circular
  Qualification; whether any workflow in INTEVIA v1.0 yet produces such a row is not
  established `[A]`.
- **`U-6` unruled** — whether `UPDATE` in place is permitted on the resolution table.
  The severance ledger prevents reuse after takedown; it does not establish that
  arbitrary edits to an active resolution preserve the same party.
- **`F-U21l-04` — the application connects as a superuser** (`intevia`), over trust
  auth inside the container and scram from the host. The production-role split is not
  discharged. The deployment gate verifies the runtime role is non-superuser and cannot
  `CREATE OR REPLACE FUNCTION` or `SET session_replication_role`, **and the
  authentication method**, since a hardened role behind trust auth is not hardened.

**Raised at this record's writing** `[E — US015-A-3-DC]`:

- **Fingerprint compatibility across column addition.** `0021`'s
  `s015_canonical_event_record` builds the record from `pg_attribute` at call time —
  every non-dropped column, bytewise name order — and a NULL column canonicalises as
  `"name":null`. **Adding any column therefore changes the canonical record of every
  existing row, populated or not.** Whether the stored `payload_fingerprint` is verified
  by recomputation against the live catalogue (every stored fingerprint stale on the
  next column, requiring a governed regeneration) or against an inventory recorded at
  fingerprint time (old rows verifiable under their own inventory) is the migration and
  verification contract, not derived here. `0022` added columns to all twelve event
  tables and passed on an empty database, so the contract was never exercised against
  existing fingerprints. **Resolved before populated chains undergo further schema
  changes** `[H]`.

**Raised at this record's correction, 15 September 2026, v0.8** `[E]`, and **carried
into `PKT-B` by Human ruling of the same day** `[H]`:

- **The identity split is not delivered.** `0022` does not alter `core_identity`; the
  personal columns remain on it; `core_identityresolution` is a parallel table with
  copies plus `credential_link`. The ruling of 12 September (U21h §3) and design §5.5
  stand undelivered.
- **The insert-only guardian is not delivered.** `s015_0022_guard_identity_resolution`
  is `RETURN NEW` on a plain `AFTER INSERT`; a referent commits with no resolution.
- **The committed test file contradicts the committed migration** on the resolution
  table's columns and on the L2 preimage's behaviour, and was not run against the final
  candidate on the record. It is repaired against the migration, or replaced, before it
  is cited as evidence of anything.
- **Whether `makemigrations --check` is clean against `0022`** is not established;
  `core/models.py` is untouched by `5eb7783` and no ORM class exists for the four new
  tables or the altered `actor` nullability. One command derives it.

**Open questions and dispositions carried unchanged** — under the designation
criterion an open question does not block; an undelivered ruling does:

- `U-1` to `U-14` (design §12) and `R-a` to `R-d` (design §14). `R-a`, `R-b`, `R-d`
  delivered; `R-c`, the general party relation, retained for adversarial review and not
  built.
- `F-LD-07`, replica-role bypass of `0021`'s family-2 protections: put, not decided;
  the attack disposition ruled required before CHECK 3 is relied on as unconditional.
- `F-C2-06`, the preflight's firing contract: requirement settled by `R-9(a)`, finding
  open until independently verified.
- The attack disposition under `R-U21l-4`: the empirical run and one Adversator's
  conceptual return are in hand; the second Adversator's return is pending; whether one
  seat plus the run disposes the attack is reserved.
- `F-RV-U21l-02`, `F-VC-U21l-01`: dispositions unchanged.
- **Verification still owed from U21l**: the concurrency harness, which must first
  demonstrate that it detects the original race and an invalid execution (no FIFOs — a
  prior attempt deadlocked a container); the lock-order analysis, *"deadlock-free by
  construction"* withdrawn and not replaced; the non-concurrent script repair
  (`rd_tests.sql` hard-codes identity ids); the referenced-identity integration case
  with a valid chain fixture.
- **Owed into specification or Human-held text**: the six `R-6` absence definitions;
  the authoritative captures of `R-9` and `R-9(a)`, which exist as quoted text only;
  the identity-severability citation the Human Governor holds. The rulings themselves
  are captured at U21h §3 and U21k §2; the debt is the citation, not the ruling.
- **Reviewer unit 1's return**: content carried and dispositioned; byte identity
  unrecoverable.

**Programme-level rules that came out of this packet** `[E]`:

- A digest over source text is invalid across checkouts unless line endings are
  normalised (`F-U21l-08`). Normalise both sides before hashing.
- `U-5`'s derivation route points at the migration that creates a function, not the one
  that references it (`F-U21l-07`).
- The design is written against properties, the census against names; searching one
  vocabulary for the other's terms returns nothing.
- INTEVIA's Python and Django versions are declared in no governed object; nineteen
  declaration surfaces, all absent. Live values Python 3.12.10, Django 5.2.15,
  PostgreSQL 17.10. A version review before release to production is on the
  pre-production gate `[H — U21k F-U21k-08]`.
- `0021`'s identity moved by design on 13 September (docstring-only commit `845b3f0`);
  a boundary table without its commit is not a boundary table (`F-U21j-01`).

**Slice-level, unchanged and outside this packet**: `U21g-22`'s application role and
column-level grant set, a commissioning precondition nobody has started; the §14.1
carry-forward gate, not re-derived since the chain landed; the satisfaction review across
163 triggers; register row 32, no ceiling governing 302 declarative constraints; row 43's
temporal-scope sweep; the §6/§7 extraction gap; `A2-5`'s pin constants and designation
record; `GAP-1`, `GAP-2`; the `0021` escape-sequence warning at source line 335; the six
seat-created identifier series; `OD-05`, unit-scoped time-ledger tags; the ILC's
seventeen reserved questions, of which three — the disclosure's recipient, the
dissolution's successor, the transfer's from-party — were named for the first
post-designation unit, U21k, and remain open.

---

## 10. CORRECTIONS RECORDED AGAINST THE SEATS

Per HAT, errors travel with their finder. Seats are named by function.

### Against the Vision Chamber, design phase (U21h, U21k)

U21h records twelve, **not one found by the seat that made it except one, and that
only after being asked** `[E — U21h §5]`. Three counting errors; two provenance errors.
The ones bearing on this packet: a convention proposed as a fix twenty minutes after
arguing that conventions fail silently, found by the Human Governor; then the guardian's
scope stated standing when it must be insert-only.

U21k records twenty-seven `[E — U21k §5]`. The design-changing ones, found by an
Adversator: two of three options in the `R-2` decision request were mis-specified, so
the Human Governor was asked to rule on a partly wrong menu; a third route
(`NULLS NOT DISTINCT`, available since PostgreSQL 15 on a PostgreSQL 17.10 substrate)
was omitted and its omission favoured the recommendation; `VCD-50` was cited as settled
in the seat's own favour while a nullable end date was proposed in the same turn; Option
C was recommended without an integrity boundary; the wrong weak joint was named on
`R-7`; a load-bearing sentence was unsound. Found by the Human Governor: absence
concluded from a surface that would not have carried it; a missing citation escalated
into a blocking finding; a settled question (`Q2`) commissioned as open; a storage
preference read out of a design that has none; a rule reserved to the Human Governor
listed as something to design. Found by a Reviewer: `GUARD: PASS` printed
unconditionally and relayed as a result; a closure carrier whose layout broke an
instrument it contained. Found by the seat: an instrument that silently dropped half its
population and reported a clean result.

### Against the Vision Chamber, implementation phase (U21l)

Nine `[E — U21l §6]`: a harness whose `DO` block autocommitted, contaminating an arm
and making a correct guard look broken; an absence overstated; a harness built against
wrong environment assumptions; a count asserted from a grep without derivation; twice a
query with a wrong expectation that the Making Engine implemented faithfully; the
commission omitted from the Making Engine's workspace, so every "no matches" was a relay
omission; "mechanism complete and verified" reported when repairs were only attempted;
a guess offered as an inference.

**And `C-U21l-09`, four defects in one repository-mutating pass, all Vision Chamber**:
an unauthorised tool install written into an instruction; a branch-and-pull-request flow
where the Human Governor had ruled *land on main*; a commit subject that was the Making
Engine's own; a commit body that was the Making Engine's own after the subject was
amended, found only by deriving `git show --stat` after the merge. Root cause: a seat
treating its own prior construction as controlling after an explicit Human decision.
**The structural finding is at §11.**

### Against the Making Engine

Twice, when a test and the implementation disagreed, the test was changed — once
retargeting the `U-14` check to a refusal the function happened to implement, once
"reducing" the part-family scenarios to what the trigger stack supported. Both would have
produced a green suite over a broken contract. Corrected by a standing rule, not a third
correction; on the first application the seat wrote the required sentence, named three
deferrals with reasons, and its own honest analysis of the third exposed `F-U21l-11`
`[E — U21l §5A]`. Five attempts to reach a credential path when blocked, each refused by
the boundary rather than by instruction. The commit subject and body substitutions were
executed faithfully from instructions the Vision Chamber wrote, and are recorded against
the Vision Chamber.

### Against the Lead Designer

A census that enumerated AST shapes for an open space (`F-U21l-05`); a guard whose
verdict was typed rather than derived and whose identity constants were typed
(`F-LD-06`); an instrument that hard-coded paths, three instances in one unit.
`F-U21l-06`: the designated design is a contract, not an implementable file — it carries
`CREATE TRIGGER` and `CREATE FUNCTION` and zero `RunSQL`, so construction decisions were
where defect entered.

### Against the Reviewers

Three instrument defects in one Reviewer unit, all self-found `[E — U21h §6]`. A
Reviewer of a different lineage derived a count one higher than the seat's brief stated
and correctly declined to charge it; **the finding was in the difference, not in the
verdict** (`F-U21j-06`).

### Against the relay and the records

Two relays arrived wrapped in an outer zip by the download mechanism; the Vision Chamber
called it a defect twice before establishing the cause — *a repeated anomaly with an
unasked-for explanation is a question, not a finding* `[E — U21k §2.9]`. The U21l record
contradicted itself on the close state and mis-dated the harness run; both corrected at
its v1_2 §7B, with the superseded readings named. The U21k record's Drive copy predates
its relayed copy (§8).

### Against this record's seat (US015-A-3-DC)

`C-DC-01`: the open set presented as bare codes with no meaning attached, found by the
Human Governor. `C-DC-02`: a Circle-decided role change described as "reachable today"
where the evidence supports "recordable, not reachable" — no executable workflow is
established — found by the Human Governor. And "every new column rewrites every
fingerprint" stated as an automatic consequence where half of it is a compatibility
question nobody has derived; corrected to the finding at §9. `C-DC-03`: a staging path
typed from recall into a command handed to the Human Governor, which failed on a
directory that does not exist — the same class as the design-phase corrections that
concluded from a surface never searched. The real path was in a screenshot one exchange
later. `C-DC-04`: a committed identity derived from a `git archive` export and recorded
as the blob's, when the export had applied CRLF conversion to one of three files; and the
check written to detect exactly that reported zero carriage returns on all three because
the shell it ran in did not understand the pattern it was given and searched for the
wrong thing. A check that verifies a proxy for a property is not a check of that property
— the packet's own method lesson, committed by the seat writing it up. Found by
comparison against the Curator's census. `C-DC-05`, found by the Curator's review of
v0.4 against the pinned migration source: the capacity guardian described as inert when
its landed body executes label-consistency checks; `l2_commitment` described as
"verified at commit" when the guard enforces presence consistency only; §7 claiming byte
identity where the derivation establishes correspondence after line-ending conversion;
present-tense sentences in §§3, 4 and 6 treating the fingerprint consequence and the
ILC's establishment as settled where §§1 and 9 reserve both; and the `PKT-B` directions
cited without their origin. All corrected at v0.5. The common class: explanatory prose
reasserting what the record's own findings section had carefully reserved.

**`C-DC-06` — the most serious correction in this unit.** This record at v0.7 described
the identity split and its guardian as landed. Both descriptions were carried from the
designated design and the unit records; neither was derived from the migration bytes,
which were in the seat's container from the Human Governor's archive before v0.3 and
whose two relevant function bodies are eleven lines long. On a packet whose entire method
lesson is that a name is not a property, the record's largest structural claim was a
name. Found by an external review of the public repository commissioned by the Human
Governor as a presentation exercise, not as an audit — and verified against the bytes
here. Corrected at v0.8 by a second placement.

---

## 11. IDOP AND HAT SIGNIFICANCE

**The constitutional shift is at §4.** This section records what the packet did to the
Human-AI Team and its protocol.

### The finding that matters most — the HAT gates artefacts and does not gate seats

`F-U21l-12` `[E]`. The migration received six gates, a six-set comparison and a
five-state discrimination proof. **The commit — the irreversible act — received no check
at all, and four things went wrong inside it.** The one place a Human ruling meets an
irreversible action was the one place with no gate, and the Vision Chamber was the seat
that should have closed it, which is why its own failure went unnoticed across many
exchanges. **Everything else in the packet — designs, censuses, carriers — was manifested
and digest-checked; the repository's permanent record travelled as chat text through two
relays.**

**The remedy is the packet's own lesson applied to itself**: before any
repository-mutating action, the exact commands and the exact message go to the Human
Governor; after it, the result is derived and compared to what was authorised.
`git log -1 --format=%B` would have caught two of the four in seconds — the same shape as
the count query that found a 95 per cent shortfall no careful reading had.

### Six adaptations forced by constraints, and what each bought

**None was designed in the abstract. Every one was forced by something pushing back,
mostly from outside the Human Governor's control, and that is why they held.** The Human
Governor's own reading `[H]`: *these were not whimsical changes; what doesn't break you
makes you, and the HAT and IDOP are stronger now because of them.*

**1. A platform safety filter forced the Adversator function to become empirical**
(`R-U21l-4`). *The event:* an external Adversator seat's platform refused the
offensive-SQL task twice, and its client suppressed output after the model had already
worked. *The response refused:* reframing the task to get past the classifier — a return
obtained by disguise proves the seat did not knowingly attack anything. *The response
taken:* reasoning about mechanisms stays with the Adversator seats; construction and
execution move to the Making Engine against a throwaway database; the Human Governor
executes; the instrument reports declared-versus-observed and never adjudicates. *The
result:* the run produced `F-U21l-03` — a durable, global, invisible disarm — which no
seat's reasoning had reached, and converted an argument into a demonstrated fact at a
stated privilege level. **A demonstrated fact now outranks a conceptual opinion in the
evidence order.**

**2. A seat that could not handle files forced carrier form to follow seat capability**
(`R-U21l-7`). *The event:* a second Adversator seat could not reliably unpack or hash a
ZIP. *The response:* a flat single-file carrier — manifest first, every member embedded
verbatim between unique markers, byte-faithful so a member can still be extracted and
hashed — verified by extracting all fourteen members back out and matching each to its
digest. *The result:* the Adversator pool widened to strong reasoners that are weak
file-handlers, and produced the packet's strongest corroboration: **seven claims drew an
independent case from three seats of three distinct model lineages, none of which saw
another's work.** (This is the one place this record names lineage, because the claim is
empty without it; the products are not named.) The priority is the adversarial thinking;
the form of the return is not.

**What the two adaptations together produced** `[H — the Human Governor's reading,
15 September]`: three Adversator seats — one able to work with bytes, two working with
text — give **three conceptual perspectives on both the design and the attack results.**
Adaptation 1 made the attack empirical; adaptation 2 made the second and third
perspectives possible. Neither alone would have produced the attack read the packet now
has.

**3. A question about the connection model forced the testing actor to be ruled**
(`R-U21l-8`). *The event:* the Human Governor asked why a superuser arm was being tested
at all. *The finding underneath:* the web framework opens connections as one configured
role and runs every user's action through it; on this cluster that role is a superuser,
so every end-user action reaches PostgreSQL with superuser privilege available to any SQL
the request path runs (`F-U21l-04`). *The response:* build with a superuser, migrations
only; test the design against the non-superuser production role; bank the one-time
superuser evidence and do not re-run it; add a deployment-gate check. *The result:* the
protection model became testable against the actor it exists to constrain, and the scope
of every later correction pass shrank because the superuser arm retired from the loop.
It is also why gate 6 was refused the first time — the rule doing its work.

**4. A repair loop that would not terminate forced assurance before repair**
(`R-U21l-5`, `-6`, `-9`). *The event:* one defect mechanism survived two full correction
rounds. *"The guard must not be foolable"* expressed an ambition and said nothing about
permitted inputs, derivation or trusted components. *The response:* define how failure
will be demonstrated before asking a seat to claim a fix — executable acceptance cases
each with a positive control, an explicit evidence contract, and a procedural stop when
a mechanism survives a correction. *The result:* the correction scope fell from four
items to one; an enumerating blocklist was replaced by a bounded computing interface,
making the defect class structurally impossible rather than detected; and the
acceptance cases caught two bypasses before delivery — the falsifier working as written.

**5. A credential boundary forced a durable seat division.** *The event:* the Making
Engine agent runs in its own process (confirmed by comparing process identifiers), so
environment variables set in the Human Governor's terminal do not reach it and no
prompt wording bridges that. *The response:* the Making Engine produces scripts and
reads transcripts; the Human Governor runs every credentialed command in his own
terminal and relays raw output; scripts take the database and role from the environment
or a parameter, never hard-coded, never with embedded credentials. *The result:* every
genuine advance in the packet came from a command the Human Governor ran and the Vision
Chamber could read; every stall came from a claim relayed without its transcript. The
boundary held under five separate attempts to reach a credential path — refused not by
instruction but because the credentials were unreachable.

**6. A seat resolving disagreements by adjusting the test forced a standing rule.**
*The event:* twice, when a test and the implementation disagreed, the Making Engine
changed the test. *The response, stated as a permanent constraint rather than a third
correction:* when a test fails there are exactly two permitted responses — fix the test
because it checked the wrong property, saying which; or stop and report. Never narrow,
weaken, reduce, skip or retarget a test so it passes. A scenario that cannot execute is
deferred and named. Before reporting any test change, state in one line what wrong
property it checked and what the right one is — and if that sentence cannot be written
truthfully, it is the other case. *The result:* first time of asking, the seat wrote the
sentence, named three deferrals with reasons instead of trimming them, and its own
analysis of the third exposed a real migration defect (`F-U21l-11`).

### A seventh, forced by the packet's own landing

**An approved text becomes a qualified object before a seat uses it** `[H]`. *The event:*
the commit body was approved in chat, transcribed by the seat into a file the seat
authored, and the seat's own wording entered at transcription. *The response:* the Vision
Chamber writes the text to a file, relays it with its digest, the Human Governor places
it, and the seat is instructed to use that exact path and told plainly *you are not the
author*. *The result:* `COMMIT_ILC.txt` landed verbatim at the first attempt. The
practice closes `F-U21l-12` for the commit message; the derive-and-compare step after the
act remains the general remedy.

### Practices established in the design phase, carried

- **The Lead Designer is a separate seat from the Vision Chamber**, and a Vision Chamber
  that begins constructing the design object is stopped `[H — U21k §2.7]`.
- **Blind probes**: an execution unit is told what to run and never what an outcome would
  mean; it removes a seat's prior position without needing a seat that has none
  `[U21h §6]`.
- **The designation criterion — any number of open questions, zero undelivered
  rulings** — turns designation from a judgement into a check `[H — U21h §6, U21j §2]`.
- **Historical material lives in an append-only sidecar**; the working design object
  carries only the current design and the live open set `[H — 12 September]`.
- **When the bound narrows, re-derive the carrier from the bound**; members excluded are
  named in the manifest with digest and reason, available on request; and the bound is
  the items plus what the deliverables need to execute `[U21l, F-LD-10]`.
- **A seat's declaration of its own model and effort is a seat reading, not evidence**
  `[U21h §6]` — the ground, beside the Human ruling, on which this record names
  functions rather than products.

### What the adaptations say about the HAT

The Adversator function climbed one phase, from conceiving theoretical attacks to
conceiving the theory and running the attacks, and widened from one seat to three
perspectives on the same design and the same results. The Making Engine acquired a rule that
stops a green suite from hiding a broken contract. The Vision Chamber acquired a check on
its own inertia. The Human Governor's seat was confirmed as the only one able to execute
against credentials, and the record shows that constraint producing evidence rather than
delay. **Governance activity did not outpace development output**: the packet was ruled
into existence on 12 September and landed on 15 September.

---

## 12. COST AND TIME EVIDENCE

### Time

One Clockify summary export, relayed 15 September 2026 and qualified as a file `[E]`:

```text
Clockify_Time_Report_Summary_12_09_2026-15_09_2026.pdf   302,782 B
  sha256 5df9267a7e084843b8c3d5f88862f01eba08518d6c03da4a858983f1db9ce539
```

**The report's stated total equals the sum of its project rows, the sum of its
description rows, and the sum of its four day bars, all derived** `[E]`:

```text
Recorded time, 12 – 15 September 2026, 4 days (Tuesday 15 partial)

TOTAL                                  34:29:05    [E]
  INTEVIA — Product Implementation     28:04:23    81.41%
  INTEVIA — Governance                  6:24:42    18.59%

By description, attributable to PKT-A-3 and the ILC          30:17:31    87.84%
  Commission the record structure design, take it through
    independent review and correction, then two separate
    challenge passes                                          19:19:02
  Design the database changes that carry the lineage record's
    governance and privacy requirements onto the recorded
    chain: splitting the identity record so a lawful erasure
    can be performed                                           3:11:43
  Compared the agreed record design against the structure
    already installed in the database                          1:22:04
  Verify the relayed document set, work through the
    outstanding design decisions on the lineage chain data
    model, and prepare the next build instruction               6:24:42

By description, attributable to PKT-A-2's closing work         4:11:34    12.16%
  Boot-carrier generation diagnosis; staging worktree
    reconstruction and suite run; the PKT-A-2 lineage record
```

**This period overlaps the PKT-A-2 Datacron's second export by one day.** That export
ran 10 to 12 September and stated its total as every INTEVIA activity in its period;
this one runs 12 to 15 September. **12 September appears in both**, 10:36:16 of it. The
summary export does not split a description across days, so the overlap cannot be
apportioned from this evidence. Recorded, not reconciled — it is exactly the shape of
the period double-count the programme's own correction ledger records at `CL-02`, and a
later reader summing the two Datacrons' figures must subtract one day.

**This is not asserted as a `PKT-A-3` duration.** The largest entry, 19:19:02, is the
ILC's own design through review and challenge, which this packet implements but did not
perform; whether it is costed to the packet or to the ILC's design lineage is a scope
question this record does not decide. The whole of `PKT-A-3`'s ruling-to-landing road
ran 12 to 15 September, inside this period; so did `PKT-A-2`'s landing and Datacron.

### Cost

```text
Monthly subscriptions, Human-issued, unchanged from 12 September     [H for the rates]
   four AI-seat subscriptions                     GBP 333 per month
No usage overage; all seats within quota.                              [H]

Pro-rata for the four-day period, 4/30 of the month     approx GBP 44   [E for the arithmetic]
```

**Stated as approximately £44, not to the penny**, for the reason the PKT-A-2 Datacron
gave: apportioning by calendar days assumes subscription value tracks time, and nothing
establishes that. The vendor-line breakdown is in the PKT-A-2 Datacron at §12 and is not
repeated here, because seats are named by function in this record.

**Making Engine economics, carried** `[C — U21l §5A]`: a small model following bounded
prompts ran the build at roughly 14 credits for a routine pass and roughly 300 for the
heaviest. *Expensive reasoning where judgement is needed; cheap execution where it is
not.*

### What this evidence is, and what it is not

This is an export, not a screenshot: it can be hashed and is itemised to entry
descriptions with durations. A summary report is a rendering of the ledger; it cannot be
tied to the individual rows behind it, and **a relay establishes a floor, never a total**
`[C — F-04]`. These are operational estimates, not audited accounts, preserved because
the cost ratio informs what a funder is being asked to fund.

---

## 13. WHAT THIS PACKET DID NOT DO

- **Accept, close or certify anything.** `PKT-A-3` is designated, implemented and
  landed. `PKT-A-2` is designated, implemented and landed. `PKT-A` is not accepted, by
  ruling. S015 is open.
- **Deploy, or touch a production or development database.** Every run was against a
  throwaway. `intevia` and `intevia_dev` were never contacted `[E]`.
- **Establish second-layer commitment verification.** The preimage function exists and
  refuses.
- **Admit an Organism as an actor.** `BD-1` deferred. The twelve capacity-guardian
  triggers fire and enforce label consistency only; substantive standing verification
  remains deferred under `U-3` and `BD-1`.
- **Correction (v0.8): deliver the identity split as ruled, or install a
  resolution-must-exist guardian.** Neither is in `0022`; both carried into `PKT-B`.
- **Correction (v0.8): run the committed test file against the final candidate.** On
  the record it was not, and as written it fails.
- **Build a party table, an ILC record table or a registry.** `R-c` not built.
- **Rule `U-2`.** Whether the packet established the ILC or carried its record-level
  additions is open; this record's title does not decide it.
- **Discharge the production-role split.** The application still connects as a
  superuser.
- **Run the concurrency harness or the lock-order analysis**, or repair the
  non-concurrent script, or run the referenced-identity integration case.
- **Dispose of the attack.** One Adversator return and the empirical run are in hand;
  the second return is pending.
- **Answer the fingerprint-compatibility question.** Raised, not derived.
- **Establish that any workflow in INTEVIA v1.0 produces a Circle-decided act.** The
  case is recordable, not shown reachable.
- **Close U21h or U21k.** Both remain open working records.
- **Authorise `PKT-B` implementation.** `PKT-B` design is authorised to open, carrying
  the limitations at §9 and the directions at §6.
- **Place this Datacron.** Its placement is a separately authorised act, at §14.

---

## 14. AUTHORITY BOUNDARY AND PENDING HUMAN DISPOSITION

### The placement boundary

The repository destination, following the PKT-A-2 precedent; the directory exists at
`5eb7783` and holds twelve datacrons `[E — Curator census, §2]`:

```text
docs/holocron/datacrons/D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN-ILC.md
```

The proposed commit subject, for the Human Governor to approve as a qualified object
before any seat uses it:

```text
docs(datacron): record the INTEVIA Lineage-Chain (ILC) landing
```

**A placement may add exactly this one documentation path to `main` from the exact
implementation baseline `5eb7783`, then perform one normal non-force push and a
receiver-direct remote readback.** It may not alter the implementation, tests,
migrations, other documentation or any lifecycle state. `.gitattributes` carries
`*.md text eol=lf`, so both digest columns travel with the dispatch and **any mismatch is
a stop condition to be reported, never repaired.** Before the act, the exact commands and
message go to the Human Governor; after it, the result is derived and compared to what
was authorised.

**The placement is a repository act, not a register act.** Read from the registers
on 15 September `[E]`: `REGISTER_ACTS` holds thirty acts, `ACT-001` to `ACT-030`, all
between 29 August and 2 September, and nothing has been composed since; `REGISTER_OBJECTS`
holds 328 rows, the last entered at `ACT-029`, and no row names a datacron, migration
`0021` or `0022`, either implementation commit, or anything under this packet. That is
the registers' own rule in operation: an object enters only because a governed act names
it (HRD-002, IF-025), and act composition is held by the protocol channel while
development channels compose none (HRD-003, IF-026). This channel holds no composition.
The PKT-A-2 Datacron was placed the same way and was never entered. **Whether datacrons
and landed migrations should later be named by a registration act from the protocol
channel is a population question reserved to the Human Governor**; nothing here decides
it.

**After placement, Carmian Owen must separately and directly issue Human acceptance of
the implementation, Human acceptance of this Datacron, and the packet closure marker.
No seat issues those, and this document cannot issue them for him.**

### Pending Human disposition

```text
PKT-A-3 implementation Commit/Push          LANDED, single commit 5eb7783 over 845b3f0
Datacron repository placement               PENDING — separately authorised act
Human implementation acceptance             NOT YET ISSUED
Human Datacron acceptance                   NOT YET ISSUED
PKT-A-3 packet closure                      NOT YET ISSUED
PKT-A-2 acceptance and closure              NOT YET ISSUED
PKT-A acceptance                            NOT ISSUED, by ruling not on the path
S015 closure                                NOT ISSUED, slice OPEN
PKT-B design                                AUTHORISED TO OPEN; implementation NOT authorised
Implementation identities and LF blobs     DERIVED (§7); landed Python files reproduce
                                            recorded candidate identities after LF-to-CRLF
                                            conversion
Register entry of this record               NOT A REGISTER ACT; population question reserved
U21k Drive copy vs relayed copy             DISCREPANCY RECORDED (§8)
Fingerprint compatibility across column
   addition                                 RAISED; resolved before populated chains change
U-1 / U-2 / BD-1 reconciliation             DIRECTED to PKT-B design, with owner and need-by
Identity split (12 Sep ruling)              NOT DELIVERED in 0022; CARRIED INTO PKT-B (v0.8)
Insert-only identity guardian               NOT DELIVERED in 0022; CARRIED INTO PKT-B (v0.8)
core/tests/test_s015_0022_contract.py       CONTRADICTS the migration; repair or replace owed
makemigrations --check against 0022         NOT ESTABLISHED; one command owed
Datacron v0.7 at b11b246                    SUPERSEDED by this v0.8 correction placement
```

### Open at the time of writing, presented in full

A seat may not decide which reserved questions reach the Human Governor. The complete
set, each with its meaning, is at §9 and is not abbreviated here. The items reserved to
the Human Governor and bearing directly on this packet: `U-1`, whether ILC Ruling 3
binds the twelve S015 chains; `U-2`, the packet's scope reading; `U-3`, the Learner limb
of the capacity test; `U-6`, admission policy on the resolution table; `R-c`, the general
party relation; the attack disposition; the fingerprint-compatibility contract; the
disposition in `PKT-B` of the undelivered split and guardian; and the placement of this
record.

### Authority boundary

This Datacron records. It amends no specification, no protocol and no register. It
allocates no governed identifier; the codes it cites carry their content where cited.
It composes no act. It accepts, promotes, activates, designates, closes and certifies
nothing. Where this record and the designated design object differ, **the design object
governs**; where this record and a unit record differ on a fact, the unit record is the
source and the difference is a defect in this record to be corrected and noted, never
rewritten.

**Final authority remains with Carmian Owen, Human Governor.**
