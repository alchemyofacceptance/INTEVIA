# D-S015-PKT-A-2-GOVERNED-RECORDED-CHAIN-AND-BITEMPORAL-FOLD

**HOLOCRON Datacron Record.** Fourteen sections, to the spine ruled by the Human
Governor on 12 September 2026. Sections 2, 8, 9, 10 and 11 were drafted at U21i,
Vision Chamber, Claude Opus 5, 12 September 2026, and are carried here from those
exact bytes — `53824555b5aa5eefaa2654919777b8bc08221e15cae7de40b6ae48283ddc7012`
— rather than re-transcribed. Sections 1, 3, 4, 5, 6, 7, 12, 13 and 14 were
written at U21j on the same day. Two corrections to the carried text are recorded
at §2 and §10 and are marked as corrections.

Every figure is derived from the object it describes or carried from a named
source whose own identity is given. Authority classes are marked: `[H]`
Human-issued, `[E]` evidence-derived, `[R]` repository-derived, `[C]` carried from
a controlling object, `[A]` seat reading.

---

## 1. STATUS

```text
Type:            HOLOCRON Datacron Record
Purpose:         S015 PKT-A-2 lineage preservation
Phase:           Post-implementation Commit/Push; pre-Datacron MCP; pre-Human
                 implementation acceptance, pre-Datacron acceptance,
                 pre-packet closure. Slice S015 OPEN
Status:          Repository lineage record pending direct Human acceptance
Runtime effect:  None
```

This Datacron records the committed and verified `PKT-A-2` substrate completion
packet: migration `0021`, the recorded chain and the bitemporal fold, with its
test harness and canonical-form fixtures. It records the road that produced it
from 10 August 2026, the Human decisions that governed it, the evidence that
qualifies it, and the defects recorded against every seat that worked on it.

**It is the first Datacron written at packet level, and the first written to the
fixed fourteen-section spine.** Both were ruled by the Human Governor on
12 September 2026 `[H]`.

Committing this exact record does not constitute Human implementation acceptance,
Human Datacron acceptance, packet closure or slice closure. It does not discharge
the §14.1 carry-forward gate, accept `PKT-A`, authorise `PKT-A-3` or `PKT-B`,
deploy, publish, tag, or create operational data. **Its existence in the
repository constitutes none of those things.**

**Two facts a reader must not read past.**

**The §14.1 carry-forward gate has not been re-derived since the packet landed.**
Its last full derivation is 2 September 2026, against a state in which the
recorded chain did not exist `[E]`. Gate item 5 was recorded there as unsatisfied
*for a build reason, not a specification reason*. The build now exists. **Whether
the gate is satisfied is not established by this document and was not assessed.**

**The controlling object is not current.** Specification `v0.13` carries a
correction register of 43 rows, of which **13 are rulings ruled and not yet
written into the specification** `[C]`. Under the designation criterion ruled by
the Human Governor on 12 September 2026 — *any number of open questions, zero
undelivered rulings* — `PKT-A-2`'s controlling object does not yet qualify for
designation. **The packet is built and verified; its specification is owed a
`v0.14` pass.** Nothing in that register requires a Human ruling. It requires
writing.

---

## 2. IDENTITY

```text
Datacron: D-S015-PKT-A-2-GOVERNED-RECORDED-CHAIN-AND-BITEMPORAL-FOLD
Slice: S015 - Governed Living Organism Foundation (OPEN, not closed)
Packet: PKT-A-2 (first packet-level datacron in the series)
Domain owner: CORE / ORGANISM
Human Governor: Carmian Owen
Repository: https://github.com/alchemyofacceptance/INTEVIA.git
Branch: main
Implementation commit: 859960d8645b682f118c651d547c1327439a8b92
Implementation parent: 3efa7f6a6b9024c8ac608619c3c8dff9276f95de
Implementation tree: de68cdb86063d642ecd12772fabb26390222683b
Commit subject: S015 PKT-A-2: migration 0021 recorded chain and bitemporal
                fold, verified
Environment: internal-pre-alpha
Controlling protocol: IDOP v0.9.7
Predecessor protocol: IDOP v0.9.6 preserved as superseded lineage
Controlling object: S015_PKT_A_2_SPECIFICATION_v0_13.md
                    163,447 B  sha256 8d4d1304ed61af4cfb6b7e249de21cb420e315cd9d8beb35fd393e6a2b7f8563
```

The implementation commit is exactly one commit beyond `3efa7f6a`, which had
stood as HEAD with a clean working tree since 1 September and was independently
re-derived four times during U21i — at census, at preflight, at placement, and by
the commit agent `[E]`. It changes exactly eleven Human-authorised paths.

**The implementation tree, recorded as owed at U21i, is derived and closed.**
The U21i commit dispatch requested `HEAD`, `origin/main` and the eleven blob
identities and received all three; it did not request the tree, so the draft
recorded the value as owed rather than reconstructing it. It was derived at U21j
from a read-only clone of the published remote at U21j `[R]`:

```text
de68cdb86063d642ecd12772fabb26390222683b
```
 **Every other datacron in the series records an implementation
tree and this one now does.** The correction is recorded rather than made
silently, per the instruction that the finished sections are corrected and the
correction noted, never rewritten.

**S015 is open.** PKT-A-3 is specified and unbuilt; PKT-B through PKT-E were
planned at slice opening and are not started. This Datacron records one packet,
not the slice.

---

## 3. WHAT THE PACKET WAS

**`PKT-A-2` is the substrate completion packet** `[C — specification §2.1,
`U21g-1`]`. It carries migration `0021` as its build unit, together with the
substrate corrections at the specification's §10. **It carries forward directly
into `PKT-B`, and `PKT-A` acceptance is not on that path.**

**The Human Governor's own ground for its existence** `[H]`: `PKT-A` was built
and then crashed. An AI node swap in the Human-AI Team during `PKT-A` created a
substantial problem, and the work missed SQLite cleanup across four previous
slices and missed clearly documented requirements. **Development must be able to
progress rather than be held hostage to that.**

The supporting ground is structural and was verified rather than argued `[E]`:
**there is no route to `PKT-B` that does not run through migration `0021`.** Of
the twelve aggregates the contract covers, six have a usable existing chain
anchor, six need a new anchor or spine, and five have no shipped table at all —
**and the chain side is absent everywhere.** `PKT-B`'s services move subjects
along chains that did not exist.

### What the packet builds

```text
1  the recorded-chain closure contract, for twelve aggregates
2  the authoritative bitemporal effective-state fold, and the secondary
   cache contract
3  the four-time grammar on every governed event
4  the event tables, support tables and head pointers the contract
   requires and which were absent
5  the database guardians that 0021 owns
6  the substrate corrections at specification §10
7  the immutability corrections at U21g-5 and U21g-14
8  the migration proving evidence
```

### What it does not build

```text
no service, command or route — the packet builds predicates and
   structures; the commands that make them applicable are PKT-B's
no founding, no production founding data, no role assignment,
   no constitutional activation
no Circle activation and no qualification event of any kind
no legal move set, legal interpretation or statutory determination
exactly one replacement of a shipped guardian function and no other:
   0021 replaces 0019's s015_guard_founding_completion_update.
   Nothing else 0019 shipped is touched
no deployment, no publication, no Datacron closure
```

**The packet sits at `PKT-00 → PKT-A → PKT-A-2 → PKT-B` in a strict dependency
graph. No packet may depend operationally on a later packet, and each requires
its own exact Human authority** `[C]`.

**One consequence stated rather than left to follow** `[C — `U21g-1`]`: the
Human Governor's attributable carry-forward marker is issued **from `PKT-A-2`
into `PKT-B`, not from `PKT-A`.** A later reader finding `PKT-B` begun with no
`PKT-A` marker should read that as a decision, not as a missing gate.

### What landed

```text
migration    core/migrations/0021_s015_recorded_chain_and_bitemporal_fold.py
installs     163 triggers against a baseline of 83            [E]
             302 check constraints named s015_0021_%          [E]
              35 functions named s015_%                       [E]
tests        120 of 120 across three suites                   [E]
reverse      refuses once any governed event exists;
             on an empty database restores 0019's guardian
             byte-identically                                  [E]
```

---

## 4. CAPABILITY AND CONSTITUTIONAL MEANING

S015 establishes **BEING** — CORE and ORGANISM, and the question of what identity
is in INTEVIA. `PKT-A-2` is the packet in which the lineage chain stops being a
description and becomes a structure, **in the database, as triggers and
constraints, rather than in application code that runs before a save.**

### What the packet actually changed

Before `0021`, the contract `§6.0` demands existed in the repository in two
incompatible states. **The cache side was built and the chain side was not.**
Migration `0019` created thirteen models — no event model, no transition model,
no chain, no head pointer — while declaring a state-fingerprint field on four
anchors. Under `§6.0` that field carries a cached projection's evaluation
coordinate, and a cached projection must fail requalification when it differs
from the authoritative fold. **The authoritative fold is a deterministic
bitemporal fold over the immutable chain. Four anchors carried a field whose
meaning depended entirely on a structure that did not exist** `[C — `BLK-U3-02`,
2 September 2026]`.

`0021` builds the structure those four fields were already pointing at.

### What the shift of locus means

The move from application enforcement to database enforcement is not a change of
implementation. It changes four things about what the system is `[C — U15,
21 August 2026]`:

- **The locus decides who a rule binds.** A rule in `clean()` binds the ORM path.
  A rule in the database binds **every route, including our own tooling and any
  direct SQL.** This is the substitution `§6.0` line 195 forbids, closed.
- **A database rule can only use what is stored.** Any obligation depending on an
  actor, a principal or an authority requires those to be columns. Enforcement
  location is therefore a schema question, not a coding question.
- **Some rules are decidable only at commit.** Transaction boundaries become a
  governed design object rather than an implementation detail.
- **Refusal is symmetric.** A database that refuses a bad write also refuses a
  lawful repair, so a governed repair route becomes necessary. **None exists.**

### What a recorded chain does imply

That for the twelve aggregates in scope, a governed event cannot be written out
of sequence, cannot orphan, cannot fork, and cannot be silently mutated after the
fact — **and that these hold against direct SQL, not merely against the
application.** That effective state at a point in time is derivable from the
chain by a deterministic fold rather than read from a stored answer. That a
cached projection is refused at read time unless it reconciles against the live
fold — **a cache that must prove itself is not a stored answer.**

### What it does not imply

A recorded chain does not imply that any Living Organism exists, that any
founding has occurred, that any role has been assigned, or that any Circle has
been activated. **It does not imply that a command exists to move a subject
along any chain**: `PKT-A-2` builds no service, no command and no route, and the
packet's own published service modules carry no database write path at all `[E]`.

It does not imply that `§6.0` is satisfied. **Seventy atomic obligations were
derived from `§6.0`'s twenty-six lines on 10 August; four were satisfied by the
build as it then stood** `[E]`. `0021` closes the structural absence those
findings concentrated on. **It does not discharge the seventy, and no unit has
re-run that comparison against the new leaf.**

It does not imply that enforcement is complete or that its extent is governed.
`0021` installs **163 triggers under the §6.4 authorisation ceiling and 302
check constraints named `s015_0021_%` outside it** `[E]`. **The unwatched side is
roughly twice the watched side**, and a duplicate check constraint installed
twice on one column was found in this packet's own verification **by catalogue
query and not by execution** — the migration applied cleanly with the duplicate
present.

It does not imply that a fold has ever been computed over real data. **The
verification ran against a freshly provisioned throwaway database with no
operational rows.**

And it does not imply acceptance of anything. **`PKT-A` is not accepted. S015 is
not closed. The §14.1 gate has not been re-derived since the packet landed.**

### The constitutional reading

INTEVIA's programme axiom holds five offices: **Lineage is King, Law is Queen,
Transparency is the Seneschal, Privacy is the Chamberlain, Discretion is the
Chancellor** `[C — ruled 3 September 2026]`. Transparency, Privacy and Discretion
govern access to lineage; they do not determine whether lineage exists.

**`PKT-A-2` is the packet in which that precedence stops being a statement and
becomes a property of the substrate.** Before it, whether lineage existed for an
S015 aggregate depended on which route wrote the row. After it, the chain is
enforced where no route can go around it. **A rule held in a place a route can
bypass is not a rule**, and that is the same defect, at the same programme, that
the shift of enforcement locus exists to remove.

### The work that had no name

One finding underwrites the whole packet and it is the Human Governor's, recorded
on 12 August 2026 `[H — `F-U10-06`]`:

> **The database-guardian and testing regime was never scoped.** The shift from
> `clean()`-based enforcement to database-level triggers arrived through S015's
> packets as a consequence, never as a deliberate object. No member costs it.
> **This is the probable reason estimates in this channel repeatedly failed: the
> visible work was priced while the unnamed work sat underneath it.**

**`0021` installing 163 triggers and 302 check constraints is that unnamed work,
named and counted for the first time.**

---

## 5. THE ROAD TO IT

**Scope ruled by the Human Governor: 10 August 2026 onward. Records before that
date are out of scope by Human decision** `[H]`.

Derived from twenty-seven unit records, 472,957 B, every byte length agreeing
with an independent Drive census before any field was read `[E]`. The reading set
was bearing-filtered by Human ruling of 12 September 2026: records about the
protocol's own design and about funding work are in scope by date and do not bear
on how migration `0021` came to exist `[H]`.

### 10 August — seven becomes seventy, and the ground disappears

**Four units ran inside one day.**

A third-node schema comparison returned seven findings against `§6.0` and **the
return was voided** `[E]`. The receiver had reported its own configuration from a
trained prior rather than from its runtime, and one of its seven findings was
false. It also disclosed, unprompted, **four persistent memory files resident in
a nominally fresh session** — moving the receiver-memory hypothesis to confirmed
fact. **Without a mandatory nil-return disclosure, that run would have been
recorded as clean and independent when it was neither.**

**U4 then read `§6.0`'s twenty-six lines one at a time and found seventy atomic
obligations** `[E]`. All seven of the earlier findings appear in the matrix. **One
line alone — the bitemporal fold — carries eighteen of the seventy**, and it sat
between two lines that had been examined and in nobody's view. **Seven of the
seventy have no falsifying oracle at all, and every one of those seven lies on a
line the earlier seven never reached.**

The unit also recorded, against itself, that **three summary figures in its own
matrix were authored rather than counted and all three were wrong** — committed
inside the document whose opening section warns about that class `[E]`.

**U5 compared the seventy against the build. Four satisfied** `[E]`. And it found
the thing that makes the slice legible: **the contract `§6.0` demands was already
implemented four times in this repository for other aggregates and had not been
applied to S015.** A keyword probe for the obvious field names returns zero and is
structurally blind to all four.

**U6 designed the chain contract** for five aggregates and invented no entity
`[E]`.

**U7 committed the work and lost the ground under it.** The custody commit
`93581a79`, twenty-one files, states on its own face that it is **not
acceptance**, that approximately 53 of 70 `§6.0` obligations are unsatisfied or
unevidenced by Human decision, that the recorded-chain contract is **not
implemented**, and that **push is not authorised** `[E]`.

In the same unit, `F-U7-02`: **the PostgreSQL substrate that had produced S015's
only PostgreSQL evidence no longer existed.** An 8 August run had shown fifteen
tests, zero failures and **zero skips** — the two SQLite skips gone because a real
server was present. By the 10th the instance was gone and **no member recorded how
it had been provisioned.** Four surfaces checked, all negative. **A connection
timeout rather than a refusal: packets left and nothing came back.**

**The SQLite problem never announced itself as a failure. It announced itself as
two tests quietly skipping, across six slices, until a real server made them run**
`[E]`.

And `F-U7-01`, the same day: **sixteen of twenty-one paths normalised CRLF to LF
at staging**, so every digest recorded in the governance folder described the
worktree form and not the committed one. **A candidate-state record must carry
both populations or a later seat will conclude the files were altered.**

**U8 stood the substrate back up, and better than it had been** `[E]`. PostgreSQL
17.10 **pinned by digest before the container existed**, so the pin could not be
read off a running object — written that way because `F-U7-02` exists. **The free
volume was the load-bearing check**: a pre-existing one would have carried prior
data and silently defeated the only test that discriminates a real apply from a
faked one. Thirty-seven migrations applied to a database proved empty by two
independent instruments.

The differential census taken that evening is the origin of a number this packet
still carries:

```text
AT 0018    tables 57   constraints 393   triggers   0
AT 0019    tables 70   constraints 458   triggers  83
DELTA      tables 13   constraints  65   triggers  83
TRIGGERS_OUTSIDE_NEW_TABLES : 0                           [E]
```

**Thirteen tables matched thirteen `CreateModel` operations exactly**, declared
side and physical side derived independently. Backup and restore both reported
72 / 463 / 83. **The triggers are the part that matters: 83 restored means the
guardians survive a dump, which is the failure that would make a backup worthless
at the moment it is needed.** And **`VCD-28`'s retained control, which U7 had
established was unrunnable, ran and passed.**

### 11 August — public

**U9 published.** `e54e127`, verified from the remote by three independent
instruments including a different transport and a different operator `[E]`. The
record states plainly what publication does not do: **it does not close S015,
accept `PKT-A`, discharge the §14.1 gate or authorise `PKT-B`. Publishing is
weaker than committing, and committing was already not acceptance.**

### 12 August — the gate is found, and it is in conflict with a decision already made

**U10 read `§14.1` and recorded the unit's principal finding** `[E]`:

> **Section 14.1 item 5 and `VCD-28` are in direct conflict. NOT RECONCILED.**

`VCD-28` had set aside, for `PKT-A`, the forty-seven predetermined evidence
artefacts, the sixteen-row assurance preflight, the harness rebuild, the
218-stimulus inventory and the fifteen unassessed predicate classifications.
**`§14.1` item 5 requires those same objects.** `VCD-28` was decided to reach
commit and push; `§14.1` was written to gate packet-to-packet carry-forward.
**Neither anticipated the other. Both were live.**

And the sentence that maps the next month:

> **"Meet the gate" and "build the recorded chain" are substantially the same
> work, and the `§14.1` decision may become much easier once the chain exists.**

The same unit recorded `F-U10-06`, the Human Governor's finding on the unscoped
guardian regime, quoted in full at §4 above. **And it established that no
carry-forward marker for `PKT-A` exists anywhere in the folder record** — a Human
act, and not one anybody had performed.

### 13 to 25 August — the apparatus, and what it cost

**U11 scoped, and generalised the memory finding across node families.** Every
receiver returned a non-nil memory disclosure; **none was context-clean.** One
node's own output became a later node's input, through a persistent store, across
sessions, invisibly to the invocation. **The effort comparison the unit was
running was declared CONFOUNDED and not reported as a result** `[E]`.

U11 also recorded, for this Datacron by name, the Human Governor's position on
the period `[H]`: **what was experienced as a train wreck was a design-philosophy
transition. The increase buys database-enforced consistency across every future
slice. A funder receives a reason, not an apology.**

**U12 booted three work streams and completed none of them**, producing five
durable artefacts the boot did not anticipate `[E]`. **U13 and U14 restructured
the governance folder**, the root falling from 92 objects to 29 against a
hundred-item route ceiling. **U14b compiled. U15 priced the locus shift** and
classified sixty-seven decomposed obligations, finding that migration `0020`
would need nineteen mechanisms per chain model across five models. **U15b
compiled again.**

**U16 commissioned the first independent external review of INTEVIA
implementation work**, 23 August, on a different model family `[E]`. Its
highest-value output was not a governed object. **The reviewer derived its
obligation set from a wider surface than the programme had used, and found both
objects a prior unit had recorded as mapping to nothing. They were in `§6`.** The
extraction had declared `§7` as its surface, **so `§6`'s contents could not appear
as absent — only as not there.** The record's own conclusion: **every disposition
in S015 rests on a population derived from a partial surface.** Still not closed.

And the pre-declared scoring oracle for that review had itself been derived from
the same extraction. **A predicate whose only oracle is derived from the object it
governs is not governed** — committed inside the instrument written to prevent it.

**The Human Governor's observation at the close of that unit is the period's
sharpest finding and is recorded in his framing** `[H — `F-VC-U16-17`]`: of
thirty-nine findings, **five touched the thing being built. Thirty-four were the
apparatus inspecting itself.** Across three days and five channels: zero
migrations written, zero code changed. The record carries the counterweight in
the same entry — the same three days produced the extraction gap and a missing
check constraint on `Circle.state`, neither of which exists without the
governance work — and then refuses to let it cancel the finding: **the ratio is
what is wrong, not the apparatus.**

The same unit records two designation collisions and a decision identifier issued
twice, on the ground that **all three qualification routes read presences, and a
ruling with no landed record has none** `[E]`.

### 26 August to 1 September — five findings, and a migration that repairs one hole

**U17 derived four oracles from the external review, ran all four, and derived a
fifth finding** `[E]`. Three of the reviewer's four confirmed; one not falsified.
**Finding 5 was new**: a genesis principal could be created with a NULL bootstrap
fingerprint. The unit also established what no document held — **loopback
authentication is `trust`, so three connections had succeeded before anyone
discovered the stored credential was wrong.** Presence read as bearing, in the
substrate rather than in a document.

**U18 wrote and applied migration `0020`** and handed over a live defect: the
model class did not carry the three constraints the migration had added, so the
next `makemigrations` would have proposed removing them `[E]`. Its handover is
blunt about what `0020` is: **`0019` was the shift to database-level enforcement,
on 24 August. `0020` repairs one hole in one table. Designing `PKT-B` as though
enforcement is broadly established would be wrong.**

**U19 closed that defect and verified it three ways** — normalised syntax trees
against the migration, `makemigrations --check`, and a read-back from the
PostgreSQL catalogue — **and the pre-edit check returned the predicted failure,
so the oracle demonstrably could fail, and did** `[E]`. It committed and pushed
`3efa7f6`, the first push since 10 August. It disposed Findings 2, 3 and 4
against the plan, held in full for the first time.

**U20 built and executed a seventeen-trial non-superuser test. Seventeen for
seventeen** `[E]`. **Three HAT passes, five brief versions, and every pass found
something real.** The Lead Designer found an aborted-transaction cascade that
would have produced **an outcome identical whether the claim was true or false**.
The Adversator returned **BLOCKED on five grounds**, including two fail-open
controls, one of which would have persistently disabled triggers in the
development database. **And it falsified the seat's own claim that a guardian was
unfalsifiable at the unprivileged floor, with a statement shape the seat had not
considered. The Lead Designer verified it in three worlds before conceding. It
became trial 16.**

**The reach limit standing since U17 was discharged: every S015 result before
that day had come from the role that owns the tables. These did not.**

### 2 September — the finding that decides it

**U21 re-derived the eight-item gate against live state and established from
source that `PKT-A` has unbuilt work.** The unit did not attain its stated
objective — acceptance was not available on the evidence — and what it produced
instead was `BLK-U3-02` `[E]`:

> **Migration `0019` creates thirteen models. No event model. No transition
> model. No chain. No head pointer.**
>
> **The cache side of the contract was built. The chain side was not.**

And how it survived six correction attempts:

> **Every attempt audited the semantic matrix document rather than comparing
> source against specification.** One attempt restated the plan's forty-seven
> rows and asserted compliance with each. **A document audit cannot notice a
> missing table.**

The unit that came closest **had been scoped by the Vision Chamber to the part
that matched**, so it checked what was there and never looked at what was not.

The consequence, stated without softening: **`PKT-A` is not paperwork away from
acceptance. The remaining work is a build** — the chain and transition models,
head pointers, consecutive-predecessor and no-orphan constraints, the deferrable
commit guardian, in a single migration. **And the number `0020` is spent, so that
block starts at `0021`.**

**U21b then performed the `§14.1` amendment that `VCD-43` had selected on
13 August and nobody had performed for twenty days** `[E]`. Its finding sharpens
U10's:

> **Line 829 requires a manifest with per-member bytes and SHA-256 — and the
> member it names is exactly the file `VCD-28` dropped. Line 829 therefore
> requires a manifest of objects that no longer exist by ruling.**

The gate had not been asking for something unreasonable. **It was asking for
something ruled out of existence three days earlier, and nobody had reconciled
the two.** The amendment's closing line: **gate item 5 remains unsatisfied for a
build reason, not a specification reason. This unit corrected the target. It
built nothing.**

### 12 September — the build

The specification reached `v0.13` carrying forty-seven corrections against its own
author. The Making Engine built `0021`. The verification ran on a freshly
provisioned throwaway at PostgreSQL 17.10: **120 of 120 tests, the three
catalogue figures reproduced across three independent runs on three separately
provisioned databases at three distinct byte states of the migration, and the
reverse exercised on two further databases** `[E]`. Six source corrections were
applied by the Human Governor, **each with its byte length and SHA-256 predicted
before he made it, and every one matched the prediction exactly.**

Eleven paths were placed, staged by name, and committed at `859960d8`. **Seven of
the eleven normalise CRLF to LF, and both digest columns were carried.** All
eleven committed identities matched first time, with zero stop conditions `[E]`.

**`F-U7-01` was recorded on 10 August in a unit record whose only purpose was to
preserve what a channel had learned. On 12 September it governed the construction
of two instruments and a dispatch, and eleven files committed at identities that
matched on the first attempt.**

### What the road establishes

**The chain was specified on 10 August, found absent on 2 September, and built on
12 September.** Between those dates the programme did not build it because nobody
had established that it was missing — and nobody had established that, because
every instrument pointed at the population it was given. **Three separate
instruments across the month returned clean results about surfaces they could not
see: a keyword probe blind to four existing implementations, an extraction whose
declared surface excluded a third of the source, and six document audits that
could not notice a missing table.**

**The month's work was not wasted on finding that out. Finding that out was the
work.**

---

## 6. HUMAN DECISIONS

Carmian Owen, Human Governor, directly retained and exercised authority over each
of the following. Where a decision carries an identifier it is given; where it
was ruled in channel without one, that is stated.

### The substrate

**`VCD-33` — PostgreSQL as the sole substrate**, adopted 10 August 2026 `[H]`.
The decision that produced the whole locus shift. The unit's scope was broadened
on the Human Governor's own instruction from an S015 question to a programme-wide
one, and the server version became a Human decision rather than a recovered fact
**because no member recorded what the vanished instance had been** `[E]`.

**PostgreSQL 17.10, pinned by image digest** `[H]`, with the pin fixed before the
container existed.

### The commits

**`VCD-29` — the custody commit**, 10 August, discharged at `93581a79`. Its
message states on its face that **committing is not accepting** `[H]`.

**`VCD-36` — the substrate commit**, discharged at `e54e1273`. **`VCD-37` — push
authority**, issued as a fresh Human event because neither predecessor reached
push, and discharged at `6428833..e54e127` `[H]`.

**Commit authority for `3efa7f6`**, granted in channel on 1 September, **for that
push only** `[H]`.

**Commit and push authority for `859960d8`**, 12 September, bounded to eleven
named paths on `main`, with no branch and no force `[H]`.

### The packet

**`U21g-1` — `PKT-A-2` exists, and `PKT-A` acceptance is not on the path to
`PKT-B`** `[H]`. With it, the ruling that **the carry-forward marker is issued
from `PKT-A-2` rather than from `PKT-A`.**

**`U21g-24` — byte-exact** `[H]`. The reverse of `0021` must restore `0019`'s
prior function text exactly. Four characters of indentation broke that by text
and by nothing else; the SQL parsed identically and 120 tests passed with the
difference present. **The Human Governor ruled byte-exact and the indentation was
corrected.** A falsifier previously satisfied byte-exactly, then asked to accept
*near enough*, is a falsifier being weakened after the fact.

**`U21h-12` — the reverse refuses once any governed event exists** `[H]`, and
correction is by a successor migration.

**`VCD-43` — the `§14.1` amendment route is AMEND**, 13 August, carried as a
recorded supersession. Narrow was excluded with ground. **`VCD-44` — line 549 is
read with line 819**, requiring a database-level positive control at `PKT-A` and
not merely a negative one `[H]`.

**The `§14.1` gate item 5 amendment**, issued by the Human Governor's own relay,
2 September. **Per-member bytes and SHA-256 survive the amendment for the
evidence members that exist** — ground: `VCD-28` dropped ceremony and kept the
falsifier, and the identity of evidence that does exist is falsifier `[H]`.

**The canonical-form rule commits beside its vectors** `[H]`, so that the
reference already present inside the fixtures resolves inside the repository.

### The Datacron

**Datacrons are written per packet as well as per slice**, ruled 12 September
2026 `[H]`. This supersedes the allocation recorded at U15 and U15b, which
assigned Datacron authorship to `PKT-E`. **The supersession is recorded at §13
rather than left to be reconciled.**

**The spine is fixed at fourteen sections**, adopted from the IDOP unit-record
form `[H]`. **The fourteen-section structure was itself ruled on 21 August 2026**
`[E]`, and members 88 and 93 were written to it and carry explicit mappings into
it. The 12 September ruling fixes it as the model for all future datacrons.

**Unevidenced recollection does not enter a Datacron, even where the Human
Governor holds it first-hand** `[H — 12 September 2026]`:

> **If there is not evidence, it's not the kind of lineage I want to dogfood.**

Applied in this document to the attribution of the August substrate episode to
any named vendor, model or support process — **the channels that would evidence
it are gone** — and applied again, on the same ground, to a claim about
documentation failure that would have been convenient to record. **What is
evidenced and does go in is the seat configuration, from the `Seat` line each
unit record carries, and the structural finding: four separate units recorded that
something load-bearing had been done and not written down.**

**Section 5's reading scope: 10 August 2026 onward, bearing-filtered** `[H —
12 September 2026]`.

**The cost carve-off**: the month's tooling total is recorded whole, with the
grant-funding and business-administration streams carved off by time share
because they are not the build `[H]`. Governance stays in, **because HAT and IDOP
were under development in the same period.**

### The ones that cost something

**Stopping a seat's catalogue query** on the ground that the answer was already in
a file the seat held `[E]`. **Refusing a capacity argument by act** — relaying a
whole reading set and then granting a bounded reduction by ruling, which is the
rule operating rather than the seat optimising. **Ruling the shift to IDOP v0.9.7
against the seat's recommendation**, which within a day produced six provisions
resolving four of the seat's own open questions and a better boundary than the one
the seat had argued for.

**And an exemption, disclosed once rather than repeated**: S015 is exempt from
independence and bearing-signature disclosure, ruled 19 August, on the ground that
the node switch was forced mid-slice and rollback would discard the gains the
failure produced `[H]`. **The residual cost is stated with it: findings inside
S015 `PKT-A` rest on a single-seat chain, and a systematic blind spot in that seat
will not be caught by S015.**

---

## 7. EXACT IMPLEMENTATION BOUNDARY

The accepted implementation changes exactly eleven paths. **Both digest
populations are carried**, because seven of the eleven are CRLF in the worktree
and LF in the repository under `.gitattributes`. **A comparison against a
committed blob uses the second column; a comparison against a working tree uses
the first.**

```text
commit   859960d8645b682f118c651d547c1327439a8b92
parent   3efa7f6a6b9024c8ac608619c3c8dff9276f95de
tree     de68cdb86063d642ecd12772fabb26390222683b
branch   main, origin/main identical                            [R]
```

**Working tree identities, as placed** `[E]`:

```text
core/migrations/0021_s015_recorded_chain_and_bitemporal_fold.py
   97805  e1a4065f2ad37b8893393ebedcbe1480a10cc62c0c8f1ab29bc44de67e68f10d
core/models.py
  183824  67851efc24abed01c180bbe401482fdd450a4c0aaff4f0d099356a44aef2a08b
tests/s015_0021_support.py
   20804  45f16c8dbf8ad78a5311ef1aa978cb3f6a4661144b747321964040a515375f85
tests/test_s015_0021_negative_direct_sql.py
   40643  577d89b1b8eb2099fa9b885ed4c3844d9f32bf783370f690f8cc992d31003577
tests/test_s015_0021_positive_controls.py
   34314  41b23644646a16f349dd8aeccf7852718aebd289539f1999295bbef58641abff
tests/test_s015_postgresql_guardians.py
   37619  2055c650ee0bf919129c57bc1791799cbf4eafa9f5bd63e6fb0b3ee246fe1c8a
vectors/s015f3_fixed_vectors.json
   78049  6b6eb36a437bb3d477ac882b9d4afbc47a551501dc68dc19f76748afab0e4660
vectors/S015_CANONICAL_FORM_s015f3_RULE_v0_1.md
   21565  4e713cd27339621811758d3df88b8a7fbfd944e2a9b62765689c393b2f3ee273
intevia/u21g_test_settings.py
     670  2b28d7190e4c7c32d526ac89e510bade5be45d0d55c267e3fa2ee1a4b6d1d58e
intevia/u21g_test_backend/base.py
    1682  cd24f248edbce0a4a9666b165813fbbeac7389677d0ec1625b18b11e6f51f271
intevia/u21g_test_backend/__init__.py
      69  d2d4b7ffb18ec178f75c207b395a82c7c4dd26793b43be02f18851c787ae7fcf
```

**Committed blob identities** `[R]`. **These are the identities to verify against
the repository, and they are not the ones above.**

```text
core/migrations/0021_s015_recorded_chain_and_bitemporal_fold.py
   96490  80d2bf01e87c9d6708d65a012a90e27feb2783070d25da1479dcc42c112a5ea0
core/models.py
  183824  67851efc24abed01c180bbe401482fdd450a4c0aaff4f0d099356a44aef2a08b
tests/s015_0021_support.py
   20477  90731f2fe16b82cb2005f33af02af5e2e96d0c7a26356ca2a5b4d10a87a13180
tests/test_s015_0021_negative_direct_sql.py
   40005  bc53a4a503fe4ab10f78bba9d2718dd4a946051211f096eb29497143fe2c304b
tests/test_s015_0021_positive_controls.py
   33811  f7a1412e2fea412118050a3b016b788e9b7c803ea85c3124d54b39822cde0a80
tests/test_s015_postgresql_guardians.py
   37619  2055c650ee0bf919129c57bc1791799cbf4eafa9f5bd63e6fb0b3ee246fe1c8a
vectors/s015f3_fixed_vectors.json
   75534  acd1ce00c91b64044486e5cfd060b6b2484d69a72c00c9451d56f7d7a4d84fb5
vectors/S015_CANONICAL_FORM_s015f3_RULE_v0_1.md
   21295  ddb7249b3ccf95f7ef9b1f6887e4f3cced88df019720b0831e0b84fd1b98541f
intevia/u21g_test_settings.py
     660  566586aff2aadde454ebc6d5788f8cd123c576f8ea2da2c079a3933119d415cf
intevia/u21g_test_backend/base.py
    1650  bba8da3686fa78a83b4c6fc6383148701a519c7fbd5c0689e150392ac12337c4
intevia/u21g_test_backend/__init__.py
      69  d2d4b7ffb18ec178f75c207b395a82c7c4dd26793b43be02f18851c787ae7fcf
```

**Four files are byte-identical across both columns** — `core/models.py`, the
guardians suite and the package initialiser — because they were already LF in the
worktree. **The remaining seven differ by exactly one byte per line, and the
deltas scale with file size as the mechanism predicts** `[E]`.

**Independent re-derivation.** The eleven committed identities were verified
three times by three parties against one declared table: by the commit agent at
execution, by the Vision Chamber seat re-deriving rather than reading the agent's
verdicts, and **at U21j from the published remote by a read-only clone, by a seat
that ran no part of the commit. Eleven of eleven MATCH on all three** `[R]`.

**No UI, API, service, command, route, publication, founding, activation,
qualification, enrolment, delivery, assessment, certification, payment or
cross-organism capability is introduced.**

---

## 8. VERIFICATION AND EVIDENCE

PKT-A-2 was verified by execution against a freshly provisioned throwaway
database, not by reasoning about the construction. Substrate:

```text
PostgreSQL 17.10          Python 3.12.10
Django 5.2.15             psycopg 3.3.4  (psycopg2 absent)
Throwaway: intevia_u21i_verify_throwaway_20260912, dropped and created per run
Protected and never contacted: intevia, intevia_dev
```

### Catalogue figures, derived by difference

The migration chain was applied in two steps so that what `0021` installs is a
difference between two catalogue snapshots rather than a figure carried from a
document:

```text
                                after 0020    after leaf    installed by 0021
non-internal triggers, public         83           246                  163
triggers named s015_0021_%             0           163                  163
check constraints named s015_0021_%    0           302                  302
functions named s015_%                 6            41                   35
```

Specification v0.13 §6.4 records 83, 246 and 163 as measured `[C]`. All three
reproduced exactly, and reproduced across **three independent runs on three
separately provisioned databases** at three distinct byte states of the
migration `[E]`. The trigger figure did not move under any of the six source
corrections applied during the unit, which is itself the check that no
correction reached further than intended.

`canonical_ck` constraints total 84, of which **eleven** are the
aggregate-specific reference columns — one on `assessment`, three on `goc`, four
on `obevent`, one on `pcevent`, two on `rcevent` — and 73 are the per-event-table
set across twelve tables `[E]`.

`pgcrypto`: **available 1, installed 0** `[E]`. It was neither present nor
created. `sha256(bytea)` is core PostgreSQL and no extension is required.

`makemigrations --check` returned no changes detected, exit 0, with the
`OrganismRoleDefinition.clean()` hook present in `core/models.py` `[E]`.

### Test evidence

```text
test_s015_0021_negative_direct_sql      78 ran   exit 0   OK
test_s015_0021_positive_controls        34 ran   exit 0   OK
test_s015_postgresql_guardians           8 ran   exit 0   OK
                                       120 of 120
```

Counts derived from the module contents before the run and compared against the
runner's own totals, so an under-collection would have been visible separately
from a failure `[E]`.

### Reversal evidence

The reverse of `0021` was exercised on two separately provisioned databases.

**Refused, on a seeded database.** One lawful non-root organism was founded
using the delivered harness helper, committed as the suite commits it. The
reverse exited 1 before any drop, raising from `remove_s015_0021`:

```text
RuntimeError: S015 0021 reversal refused: core_livingorganismevent holds 1
governed event(s); 0021 is irreversible once any governed event exists
(U21h-12). Correct forward with a successor migration.
```

**Completed, on an empty database**, leaving 83 triggers — exactly the `0020`
baseline `[E]`.

**The restored guardian was compared against what `0019` installs alone**, on a
third database migrated to `0019` only. Both instruments were read, because they
measure different things: `pg_proc.prosrc` is the body as stored, verbatim,
excluding the header and attributes; `pg_get_functiondef` is the whole
definition reconstructed from the catalogue, and would expose a divergence in
language, volatility or security definer that a body comparison cannot see.

```text
                        reverse-restored          0019 alone
prosrc                  1548  1ce58c75…657f       1548  1ce58c75…657f
pg_get_functiondef      1681  b5df5018…b7ef       1681  b5df5018…b7ef
language / volatility / security definer:  plpgsql / v / false, both
```

**Byte-identical on both instruments**, satisfying `U21g-24`'s falsifier as
written `[E]`.

### Reports

```text
U21i_CENSUS_A_REPORT_20260912_164912.txt    137,702  87042d72…7291
U21i_PREFLIGHT_REPORT_20260912_172925.txt    34,034  b690a189…13be
U21i_RUN_REPORT_20260912_202640.txt          21,424  c17b9be2…f060
U21i_REVERSE_REPORT_20260912_202516.txt      11,883  fc84cf6c…a838
U21i_PLACE_REPORT_20260912_203606.txt         8,980  8e57ab3b…7b41
U21i_COMMIT_REPORT_20260912_204403.txt        2,911  fff82ed3…2827
```

Each report carries its own byte length and SHA-256, and each instrument that
produced one declares its own identity in its second line. Reports were relayed
as files; terminal output was not relied on at any point after the first round
of the unit.

### Evidence classification

The test and catalogue evidence is **producer-executed**. The instruments were
composed by the Vision Chamber seat and executed by the Human Governor on his
own machine; no independent party reproduced them. The eleven committed blob
identities were verified twice against the same declared table — once by the
commit agent and once by the Vision Chamber seat re-deriving the comparison
rather than reading the agent's verdicts `[E]`.

---

## 9. FINDINGS CARRIED FORWARD

**Eight instances of one class.** A ruling correctly built into one object and
not carried into every site that operates it — the class recorded at
specification v0.13 as `CR-41`/`CR-42`. In U21i alone the class appeared in the
boot carrier's member selection (four superseded test modules), in `core/models.py`
(one generation behind the delivered worktree), in the migration's forward
guardian and in its founding-population limb 4 (the `INTEVIA_` prefix not
propagated), in the shipped-guardian constant (the prefix propagated where it
must not be), and in a delivered test module's dictionary key where the adjacent
assertion string had been updated and the key had not `[E]`.

**The general form, recorded at U21h and carried:** *a carrier built by selecting
from multiple delivered generations must derive each member's provenance, not its
plausibility.* And its sharper successor: *a rename applied to a governed
identifier must be checked against every site that deliberately carries the old
name* — a reverse that restores a superseded definition is one such site, and it
is textually indistinguishable from an unpropagated one.

**Declarative constraints have no authorisation ceiling.** Specification register
row 32 records that §6.4's ceiling governs triggers and that `CHECK` constraints
and unique indexes are installed with nothing watching. That gap now has a
figure: `0021` installs **163 triggers under the ceiling and 302 check
constraints named `s015_0021_%` outside it** `[E]`. The unwatched side is roughly
twice the watched side. A duplicate `CHECK` constraint installed twice on one
column was found in this unit **by catalogue query and not by execution** — the
migration applied cleanly with the duplicate present, so the run would have
passed it silently. Recorded as the second confirmed instance of row 32's gap.

**Line endings, from 10 August to 12 September.** Unit record 52, `F-U7-01`,
recorded on 10 August that committed-blob digests differ from worktree digests
under the repository's own `.gitattributes`, and that a candidate-state record
must carry **both populations** or a later seat will conclude the files were
altered `[C]`. On 12 September, seven of the eleven committed paths normalised
CRLF to LF on staging. The placement instrument and the commit dispatch both
carried two digest columns, and all eleven committed identities matched first
time `[E]`. A finding recorded in August, written into an instrument in
September, and discharged without a round lost.

**`intevia/u21g_test_backend/__init__.py` has never been executed in the form
committed.** Every worktree on disk carries that file at zero bytes; the
committed form is a 69-byte docstring authored to the Human Governor's ruling of
12 September `[E]`. Python package semantics are identical, so this is a
divergence rather than a risk, and it is recorded rather than left to be found.

**`0021` line 897 raises `SyntaxWarning: invalid escape sequence '\ '` on every
import.** Pre-existing, reported by the Making Engine in the original carrier at
what was then line 889, not introduced by any U21i correction `[E]`. Harmless
today; a future Python release may make it an error.

**The canonical-form rule now exists in two custodial homes.**
`vectors/S015_CANONICAL_FORM_s015f3_RULE_v0_1.md` was committed so that the
reference already present inside `s015f3_fixed_vectors.json` resolves within the
repository. The Drive copy and the repository copy can now drift, and nothing in
the repository will detect it. Recorded as a known residual of the ruling, not as
something the commit fixed.

**Register row 37's residual stands.** A `--keepdb` run ending on a scaffold
class can leave `s015_platform_root_unset_ck` `NOT VALID` until the next
non-scaffold setup `[C]`. Not encountered in U21i; not discharged either.

---

## 10. CORRECTIONS RECORDED AGAINST THE SEATS

Stated plainly, with their finders, without self-abasement.

### Against the prior Vision Chamber seat (U21h)

**The U21i boot carrier carried four superseded test-module generations.** Two
generations of four files existed on disk and the wrong one was taken for all
four, because they were in the archive that happened to be open. Found by the
successor seat on reading the delivered support module against the migration's
own demands, before any run `[E]`. Self-corrected by U21h in the same message
that supplied the right generation.

**The answer on which guardians suite governs was wrong.** U21h ruled the relayed
37,611-byte version controlling; the Making Engine's delivered version is 37,619.
Same reasoning, wrong generation. Found and corrected by U21h unprompted.

**The handover's patch list was wrong.** It enumerated seven items where eight
were claimed, and one of the seven was not a hand patch at all. Corrected by
U21h from the diff chain rather than from recall, superseding the handover.

### Against the U21i Vision Chamber seat

**Generic filenames in a census, twice.** The first census probed for
`__init__.py`, `base.py` and `settings.py`, matching 982 and 41 unrelated paths
and raising two false stop conditions. The defect was recorded and the fix
stated; it was then **reintroduced** in the successor instrument through the
manifest's own member basenames. Second commission of a defect its own author had
already written down.

**A comparison that reported one side of itself.** The run script's mismatch
message printed the observed digest and not the expected one. When a stale copy
of the script was run, the message showed the correct value as a mismatch and was
undiagnosable from its own output. Cost one round. **General form: a comparison
that reports only one side cannot distinguish a bad object from a bad
instrument.**

**An expectation measured against the wrong scope.** A catalogue query was
written over all `s015_0021_%_canonical_ck` constraints and an expectation of
eleven was published against it, drawn from the narrower aggregate-specific set.
It returned 84. The eleven was correct and the query was not. **General form: an
expectation and its measurement must share a scope, or a match means nothing
either way.**

**A verdict parser defeated by line wrapping.** `Out-String -Stream` wraps to
console width, so the parser read `ok` from a wrapped test line rather than the
final `OK`. Exit codes carried the true verdict and nothing was misreported, but
a genuine failure whose verdict line happened to wrap would have been. Fix:
`-Width 4096`.

**A self-referential instruction.** The commit dispatch required the agent to
state its own report's byte length and SHA-256 **inside that report**, which is
impossible. The agent complied as well as the contradiction allowed, reporting a
true digest of the file as it stood before the final section was appended. Fix:
an artefact's identity is derived and reported outside it, never within it.

**Two instrument files shipped under one filename.** Two generations of the run
script, both 18,301 bytes, indistinguishable by name, size or date. Cost one
round and forced a digest check to tell them apart. Fix applied late in the unit:
instrument filenames carry their generation.

All six were found by the seat on reading its own return or the return of an
instrument it had written. None was found by a check that seat had designed in
advance.

### Against the commit agent

None. The agent executed six steps, staged eleven paths by name, used no wildcard,
modified no file, and reported a stop-condition count of zero. Where the dispatch
gave it an impossible instruction it produced a true and traceable answer rather
than inventing one or omitting it silently `[E]`.

### Against the U21j Vision Chamber seat

**Added at U21j rather than rewritten into the U21i text above.** The section is a
fixed part of the spine and the seat compiling the Datacron is not exempt from it.

**A total published from a superseded version of its own set.** The seat proposed
three candidate reading sets for section 5, published one of them at 26 records
and 454,770 B, then moved one record between its own lists in the same message and
did not carry the change into the total. **The correct figures are 27 records and
472,957 B, and the seat's own two lists said so.** Same class as the expectation
measured against the wrong scope recorded above, and the fourth instance of the
class in this Datacron.

### Against the relay

**Not a defect in any seat and recorded because the class recurs.** Two relays
delivered `U21e_UNIT_RECORD_v1.md` in place of records requested by name —
once inside a carrier and once as a standalone file — against a boot carrier
already holding `U21e_UNIT_RECORD_v9.md`, six times larger and eight versions
later. **Both were caught by comparison against an independently derived byte
length, and neither was read.** A name is not an identity, and two records whose
filenames differ by a single character will be confused by any route that selects
on name. **The census that produced the byte lengths is what made the check
possible**, which is the same finding U21i records above about designing an error
out rather than instructing against it.

---

## 11. IDOP AND HAT SIGNIFICANCE

**The single most productive pattern in this unit was designing out an error
rather than instructing against it.** The provenance rule — derive each member's
provenance, not its plausibility — was articulated as prose after the carrier
defect was found. It was then built into the preflight instrument as a census
that hashes every generation of every member present on disk and reports each
with its modification time. **The first thing that instrument did was catch the
next instance of the same class, in a member the successor seat had itself
selected.** An instruction not to take the wrong generation would not have caught
it. A census that hashes everything did.

**Instruments must carry identity, exactly as objects must.** A stale script is
indistinguishable from a changed object unless the script says what it is. After
that cost a round, each instrument was made to print its own path and SHA-256 as
its second line. The provenance discipline this programme applies to carriers
applies with equal force to the tools that qualify them.

**Execution settles some claims and counting settles others, and they are not
interchangeable.** The duplicate constraint applied cleanly and would have
survived any number of green test runs; only a catalogue query found it.
Conversely, the eight hand patches could not be reasoned about at all — seven of
the eight turned out to have been sound from the moment they were written, and
nobody knew, because nobody had run them. The lesson is not that execution wins.
It is that a claim must be routed to the instrument that can falsify it.

**Falsifiers move in one direction only.** `U21g-24` requires that the reverse
restore `0019`'s prior function text. Four characters of indentation broke that
by text and by nothing else — the SQL parsed identically and 120 tests passed
with the difference present. The Human Governor ruled byte-exact and the
indentation was corrected. **A falsifier previously satisfied byte-exactly, then
asked to accept "near enough", is a falsifier being weakened after the fact.**
The four spaces were never a defect in behaviour, and the record should not claim
they were; what they broke was the ability of a test to mean something the next
time it ran.

**Functions are seats, and seats can be reassigned.** The HAT model has
absorbed a mid-slice change of occupant in the Vision Chamber without redesign,
because the function was defined independently of who holds it. The seat
configuration on any given day is derivable from the `Seat :` line each unit
record carries `[E]`. A model built around functions survives a poor occupant; a
model built around a particular performer does not. The function set has grown
from three at inception — Human, Vision Chamber, Making Engine — through four
with the addition of the Adversator, to eleven at HAT v0.3, of which ten are AI
functions and one is the Human `[H]` for the first two figures, `[E]` for the
third.

**The open set travelled in full.** Per HAT v0.3 a seat may not decide which
reserved questions reach the Human Governor. In U21i the seat twice named an
inference it was drawing about a ruling rather than acting on it, and twice was
either confirmed or corrected. The cost was two sentences; the alternative was a
seat quietly deciding what the Human had meant.

**The error distribution matters more than the error count.** Defects in this
unit were recorded against the prior seat, the current seat and the specification
alike. Every one was found and reported by whoever made it, mostly on reading
their own return. That is not incidental to the result — a programme in which
seats report their own defects produces a record that can be trusted about
everything else.

**And the discipline paid across a month.** `F-U7-01` was recorded on 10 August
in a unit record whose only purpose was to preserve what a channel had learned.
On 12 September it governed the construction of two instruments and a dispatch,
and eleven files committed at identities that matched on the first attempt. The
unit records were written so that this Datacron could be written. They were also,
unplanned, the reason the commit landed clean.

---

**Final authority remains with Carmian Owen, Human Governor.**

---

## 12. COST AND TIME EVIDENCE

**A slice-level figure was reserved on 21 August 2026 and supplied on
12 September 2026.** Three separate records carry the same owed item — that the
Human Governor would relay a Clockify export at slice close **so the cost ratio is
recorded once, against the whole period, rather than estimated per unit** `[E]`.
Members 88 and 93 each state *no unit-level duration or model spend is asserted
here* for that reason. **They deliberately left this section empty for a figure
that did not yet exist.**

### Time

Two Clockify exports, relayed 12 September 2026 and qualified as files `[E]`:

```text
Clockify_Time_Report_Summary_10_08_2026-09_09_2026.pdf   525,646 B
  sha256 f68fc7484e909cee8dcd63e5a58938a6df931c1be41d3bd107554cfe816b71d2
Clockify_Time_Report_Summary_10_09_2026-12_09_2026.pdf   278,136 B
  sha256 dbb791c79288166a09fa35bb836adc344877915842f616fa48499ee23d1d235a
```

**Each report's stated total equals the sum of its own project rows, derived.**
The two periods abut without overlapping — a check made because the programme's
own correction ledger records a prior period double-count of exactly that shape.

```text
Recorded time, 10 August – 12 September 2026, 34 days

TOTAL                                  298:54:54    [E]
  INTEVIA — Governance                 135:31:51    45.34%
  INTEVIA — Product Implementation     122:09:47    40.87%
  INTEVIA — Grant and Funding           34:15:37    11.46%
  INTEVIA — Operations and Admin          6:57:39     2.33%
```

**This is not asserted as a `PKT-A-2` duration**, and it is not a slice duration
either. It is every INTEVIA activity in the period across four streams.

### Cost

```text
Monthly subscriptions, Human-issued                  [H for the rates, E for the sum]
   Claude Max x20     GBP 150
   GPT max x5         GBP  80
   CoPilot Max        GBP  80
   Grok               GBP  23
                      --------
   TOTAL              GBP 333

No usage overage.                                    [H]
```

**Carved by Human ruling of 12 September 2026** `[H]`: the grant-funding stream
and the business-administration stream come off, on the ground that a grant
application and a bookkeeping reconciliation are not the build. **Governance stays
in, because HAT and IDOP were themselves under development in the same period.**

```text
Time bearing on the platform and its governance   257:41:38   86.21%   [E]
Tooling cost at the same proportion               approx GBP 287
```

**Stated as approximately £287, not to the penny.** Apportioning by hours assumes
subscription value tracks time and nothing establishes that; a ratio with no basis,
printed to the penny, reads as precision it has not got. **A plain figure with its
scope named is honest and a funder can still use it.**

### What this evidence is, and what it is not

**These are exports, not screenshots.** The programme's own ledger-integrity
finding records that figures read from images cannot be hashed and cannot be
handed to a diligence process `[C — `F-01`]`. **These can be hashed and are
itemised to individual entry descriptions with durations. That is a material
upgrade on the evidence the S014 Datacron carried**, and the Human Governor's
own account of the change is that he now understands how to use the instrument.

**The limit stands and is stated rather than buried.** A summary report is a
rendering of the ledger. It can be hashed as a file; **it cannot be tied to the
individual rows behind it.** The same standing finding applies: **a relay
establishes a floor, never a total** `[C — `F-04`]`.

**These are operational estimates, not audited financial accounts.** They are
preserved because the cost ratio materially informs what a funder is being asked
to fund, and because the programme's own position on the period is recorded and
should be read with the figure `[H — U11]`: **what was experienced as a train
wreck was a design-philosophy transition. The increase buys database-enforced
consistency across every future slice. A funder receives a reason, not an
apology.**

---

## 13. WHAT THIS PACKET DID NOT DO

This packet, and this Datacron, do not:

- **constitute Human implementation acceptance, Datacron acceptance, packet
  closure or slice closure** merely because they exist in the repository;
- **accept `PKT-A`**, which remains unaccepted;
- **discharge the `§14.1` carry-forward gate.** The gate's last full derivation is
  2 September 2026, against a state in which the chain did not exist. **It has not
  been re-derived since the packet landed, and this document does not assess it;**
- **satisfy gate item 8.** The Human Governor's attributable carry-forward marker
  does not exist and is **terminal by construction** — no seat can produce it;
- **satisfy gate item 6.** Two independent SO-PRO returns exist and **both are
  HOLDs, and neither has seen the published bytes** — both reviewed a pre-commit
  tree superseded the same evening;
- **authorise `PKT-A-3`, `PKT-B`, `PKT-C`, `PKT-D` or `PKT-E`.** `PKT-A-3` is
  specified and unbuilt; `PKT-B` through `PKT-E` were planned at slice opening and
  are not started;
- **discharge the seventy `§6.0` obligations.** Four were satisfied at the
  10 August comparison and **no unit has re-run that comparison against the new
  leaf**;
- **close the `§6`/`§7` extraction gap**, which bounds every disposition in S015
  and has stood since 23 August;
- **deliver the `v0.14` specification pass.** Thirteen ruled rulings are not yet
  written into the controlling object, and until they are, `PKT-A-2` fails the
  designation criterion of *any number of open questions, zero undelivered
  rulings*;
- **rebuild the `§6.4` ceiling table.** It is rebuilt and ready at 22 family rows
  summing to 163, and it does not yet carry the temporal-scope cell ruled on
  12 September;
- **govern the declarative side of enforcement.** 163 triggers sit under the
  ceiling and **302 check constraints sit outside it, with nothing watching**;
- **create any service, command, route or application path**, nor apply migration
  `0021` outside qualified test environments;
- **create operational data.** The verification ran against throwaway databases
  with no operational rows, and **`intevia` was never touched**;
- **deploy, publish, release, tag, or claim production readiness**;
- **establish security, privacy, compliance, accessibility, performance,
  operational or Human-usability readiness**;
- **upgrade producer-executed evidence into independent reproduction.** The
  instruments were composed by the Vision Chamber seat and executed by the Human
  Governor on his own machine; no independent party reproduced them;
- **amend or promote IDOP**, amend plan v9, or adopt any candidate refinement; or
- **turn possession, storage, copying, quotation, forwarding, replay or model
  repetition into Human authority.**

**One supersession is recorded here rather than left to be reconciled.** Members
88 and 93, written 21 August 2026, both state that **Datacron authorship is
`PKT-E` work under separate authorisation** and that those units produce input
only `[E]`. **The Human Governor's ruling of 12 September 2026 supersedes that
allocation**, extending datacrons from slice level to packet level. A reader
finding the August statement should read this document as written under the later
ruling, not out of turn.

**Historical evidence is lineage, not continuing permission.** Every future
consequential action remains subject to current Human authority and its owning
domain.

---

## 14. AUTHORITY BOUNDARY AND PENDING HUMAN DISPOSITION

### The MCP boundary

The exact repository destination for the separately authorised Datacron MCP is:

```text
docs/holocron/datacrons/D-S015-PKT-A-2-GOVERNED-RECORDED-CHAIN-AND-BITEMPORAL-FOLD.md
```

The authorised commit subject is:

```text
docs(datacron): record governed recorded chain and bitemporal fold
```

**The MCP may add exactly this one documentation path to `main` from exact
implementation baseline `859960d8645b682f118c651d547c1327439a8b92`, then perform
one normal non-force push and a receiver-direct remote readback.** It may not
alter the implementation, tests, migrations, other documentation, or any
lifecycle state. **The path is absent from `859960d8`, so the MCP adds rather than
overwrites** `[R]`. `.gitattributes` carries `*.md text eol=lf`, so both digest
columns travel with the dispatch and **any mismatch is a stop condition to be
reported, never repaired.**

**After that commit and push, Carmian Owen must separately and directly issue
Human acceptance and the packet closure marker. No seat issues that, and this
document cannot issue it for him.**

### Pending Human disposition

```text
PKT-A-2 implementation Commit/Push       COMPLETE, verified, three parties
Datacron repository Commit/Push          PENDING MCP
Human implementation acceptance          NOT YET ISSUED
Human Datacron acceptance                NOT YET ISSUED
PKT-A-2 packet closure                   NOT YET ISSUED
PKT-A acceptance                         NOT ISSUED
S015 closure                             NOT ISSUED, slice OPEN
§14.1 carry-forward gate                 NOT RE-DERIVED since the packet landed
   item 6, independent SO-PRO return     two exist, both HOLD, neither has
                                         seen the published bytes
   item 8, carry-forward marker          DOES NOT EXIST. Terminal by construction
Specification v0.14 pass                 OWED. 43 rows, 13 undelivered rulings
§6.4 ceiling table                       REBUILT, awaiting the temporal-scope cell
```

### Open at the time of writing, presented in full

**The open set travels in full. A seat may not decide which reserved questions
reach the Human Governor** `[H]`. This is the set as it stands from the material
this Datacron rests on; **it is a floor, not a total.**

**At slice level.** `U21g-22`'s grant set, the largest outstanding item, which
nobody has started. The three identities per assurance row at `§8.3.1`. The
satisfaction review across the 163 installed triggers. Adversator II, rescheduled
post-fix. **`GAP-1` and `GAP-2`** under the forcing condition at `§4.14`.
**`A2-5`'s two pin constants**, requiring a Human invocation naming the INTEVIA
root organism. **`A2-5`'s designation record**, proposed and not ruled, with two
BLOCKING findings standing. **`A2-2`'s shape**, proposed and not ruled. **The
application database role and its column-level grant set**, now a commissioning
precondition. **Every assurance-row classification** under `BLK-U3-03`. **Three of
the four failed dispositions at `§14.1`** — Findings 8, 9 and 11, of which 8 and
9 were **disposed by assertion**, a mechanism named in one section and omitted
from its firing surface in another, **which survived two reviews because the
Adversator Return was never in the room.**

**At packet level, for `PKT-A-3`.** Row 9's per-part cardinality, which is a
relation and not a column. Whether from-party and successor are per-kind values or
the general party relation.

**Recorded and not fixed.** `0021` line 897 raises a syntax warning on every
import, pre-existing. The committed 69-byte package initialiser has never been
executed in the form committed; every worktree carries it at zero bytes. **The
canonical-form rule now exists in Drive and in the repository, and nothing
detects drift between them.** Register row 32's gap: 163 triggers watched, 302
check constraints unwatched. Register row 37's residual under `--keepdb`.

**Left in place for inspection.** Four throwaway databases from the verification
run, and the verify worktree. **Dropping any is a Human act.**

**Carried from the road and not closed.** The `§6`/`§7` extraction gap. The
governance-to-development ratio recorded at U16, **which is not a defect in any
object and is the Human Governor's to act on.** The six ungoverned identifier
series created by a seat without any Human decision. `OD-05`, the unit-scoped
Clockify tags.

---

**The next authority event after the bounded Datacron MCP is the direct Human
acceptance and the packet closure marker.**

---

**Final authority remains with Carmian Owen, Human Governor.**
