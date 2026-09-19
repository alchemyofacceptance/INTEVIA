# Change C — Design report

## Why INTEVIA verifies its database foundations this way

**Report 1 of 4 · v1.0 · 19 September 2026**
**For:** Carmian Owen, independent reviewers and prospective funders
**Subject:** the S015 verification route, design v0.5

---

### At a glance

INTEVIA enforces many of its rules inside the database itself, in triggers, constraints and functions.
A test suite reporting "120 of 120 passed" leaves three questions open:

1. **Did the tests run against the code they claim to describe?** A working copy can differ from any
   recorded version, and a run in a developer's editor establishes something about that machine.
2. **Did the tests actually test anything?** A negative test that accepts *any* error passes whether or
   not the protection it names still exists.
3. **Can anyone else confirm it?** A figure quoted in a document is an assertion. A figure a reader can
   reproduce is evidence.

Change C answers the third, and part of the second. **It does not repair the product defects an
external reviewer found in the schema itself**; those are recorded individually in Report 2 §7.

An external reviewer put the same point in September 2026, having inspected the public repository
without running anything: what could not be trusted without a fresh throwaway migrate-and-run was the
headline test figure, the trigger count, or any claim that the object model and the database still
described the same thing.

---

### 1. The shape of the design

A **route** runs a sequence of steps against a live PostgreSQL server:

| Step | What it does |
|---|---|
| `IDENTITY` | records which version and which files are being tested |
| `OFFLINE` | the verification tools' own self-tests, no database |
| `SELF` | the isolation harness checks itself |
| `S015` | the slice's contract tests |
| `OWNERSHIP` | establishes that the route only destroys databases it created |
| `LOCK` | establishes that two runs cannot collide on one server |
| `MUT-…` | one step per mutation control — see §3 |

A **launcher** prepares the run: it rebuilds the candidate in an isolated clone from a published change
file, confirms the resulting file tree matches the expected one, extracts the committed startup code and
verifies its identity, builds a fresh Python environment from the candidate's own requirements, and then
starts the route. **It writes nothing into the evidence and decides nothing.**

A **gate** takes the qualification decision afterwards, from the retained records, outside the run.

---

### 2. Why the decision is taken outside the run

A program that tests itself and then reports its own verdict has a single point of failure: if it is
wrong about what it did, it is also wrong about whether that was acceptable.

So the route writes records, and a separate program reads them and decides. The gate establishes that
the records agree with one another on the run's identity; that each **matches the digest recorded for
it**, so it has not changed since it was written; that the file tree the route executed from did not
change while it ran; and that every required control ran and passed.

Digest checks establish **consistency with recorded values**. They do not independently prove where a
record came from: a package rewritten wholly and consistently would satisfy them. §5 states that limit.

**A route exit of 0 never qualifies a run.** Every path reporting qualification must invoke the gate and
require its successful decision. This was ruled explicitly on 18 September 2026, when the programme also
reduced three separate decision implementations to **one authoritative implementation invoked on every
path**.

---

### 3. The mutation controls — the part worth understanding

A negative test asserts that something is refused. But a test that accepts *any* refusal passes even
when the specific protection it names has been deleted, because something else happens to fail.

The route therefore, for each control:

1. deliberately **replaces the intended protection with an unrelated one** inside a disposable database;
2. runs the test that is supposed to demand the intended refusal;
3. requires that test to **fail by assertion**, naming the refusal it did not receive.

If the test passes under the mutation, it was accepting any error and is not a control. If it fails with
an unrelated error rather than an assertion, it establishes nothing either. The gate requires the exact
evidence: the mutation applied, a positive count of failures, a non-empty record of the assertion that
failed, the two in agreement, and the failure being an assertion.

This is mechanically checked rather than asserted. **Two controls are currently registered**, and both
are demonstrated in the qualification evidence:

- `MUT-fu-b2-unrelated-reuse-refusal` — an unrelated refusal is substituted for the resolution-reuse
  guard, and the target test fails with `AssertionError: 'S015 R-d: identity reference' not found in
  'VERIFICATION MUTATION: an unrelated refusal …'`;
- `MUT-fu-b3-unrelated-circle-check` — an unrelated constraint is substituted for the circle-state
  vocabulary check, and the target test fails with an assertion naming the constraint it expected and
  did not get.

In each the target failed **by assertion, naming the refusal it did not receive**, which is what
distinguishes a control from a test that would accept any error. The expected set is derived from the
candidate's own registry, so it grows as further mutations are added.

---

### 4. What a passing check means, stated narrowly

Evidence, at the checked properties, for the tested file tree and environment.

**It is not** review, landing, external reproduction, or acceptance. A passing check states that a
particular tree behaved in a particular way on a particular server on a particular day, and that the
evidence for that is retained and re-readable.

---

### 5. What the design excludes, and says so

| Excluded | Why |
|---|---|
| Defence against a compromised interpreter or environment | Ruled out of scope on 17 September 2026: the objective is a reliable verification baseline, not an adversarial sandbox |
| Preventing a concurrent client replacing a database mid-run | PostgreSQL cannot drop a database by object identity, so the final check and the drop are separate statements. The route does not claim to prevent this, and does not guarantee that every such case is detected afterwards. Where it does observe a replacement it reports unresolved cleanup |
| Authenticity of a whole evidence package | Every record could be rewritten consistently and would pass. Nothing is signed. This limits whether a package is what it claims to be, and is distinct from the concurrency case above |
| Which files a run may write into its own execution directory | A reserved question; the route reports any change rather than judging it |

Each is recorded in the consumer contract rather than left for a reader to discover.

---

### 6. The decisions that shaped it

| Date | Ruling |
|---|---|
| 17 Sep 2026 | Bounded objective: a reliable local and CI verification baseline, not a defence against a compromised interpreter. No outcome-only fallback |
| 18 Sep 2026 | Authorship split: the party producing the evidence does not write the program judging it |
| 18 Sep 2026 | Qualification binds the exact candidate version, not the synthetic merge version a pull request generates. The base is recorded beside it and rechecked before any landing decision |
| 18 Sep 2026 | One authoritative decision implementation, invoked on both the local and the CI path. A route exit of 0 alone never qualifies |
| 19 Sep 2026 | The independent reviewer's four findings addressed as **field families** rather than as the individual examples supplied. Three were closed on a bounded closure check; the fourth, a staging issue, received a statement-only correction accepted by the Human Governor for this landing, **without subsequent closure confirmation by the reviewer** |

---

### 7. The governing contract

The decision rules are stated in **`docs/verification/CONSUMER_REQUIREMENT_v1_2.md`**, SHA-256
`19985675fca35f97ca51ba6dec64d5f1311c999a71d26063f502e992a70c1466`. **v1.2 governs.**

`CONSUMER_REQUIREMENT_v1_0.md` is retained in the repository as **historical material** — the version
against which the first qualifications were taken — and does not govern.

The statement is authoritative over its implementation: where the gate and the contract differ, the
contract governs and the difference is a defect in the gate. The contract is not to be inferred from
the code.

---

### 8. Status

Change C was merged into the main branch on 19 September 2026 as merge commit
`57015b13739aaedabf51f792ed0684bae18428e1`, from candidate
`faea7d29ae9bd4d7581e6596a3cb7e86318f822f`, tree
`84aea3aa1d6114ae38c5918e3e066c8d0950a6c4`. The main branch's tree was verified to equal the qualified
tree.

Merging is not acceptance of the wider product, external reproduction, or adoption of any governance
amendment. **Final authority remains with Carmian Owen, Human Governor.**
