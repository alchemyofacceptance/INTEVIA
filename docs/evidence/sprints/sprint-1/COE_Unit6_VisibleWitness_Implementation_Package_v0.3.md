# COE UNIT 6 — VISIBLE WITNESS IMPLEMENTATION PACKAGE v0.3

## Sherpet confirmation and governing evidence

- Sherpet: `COE_Unit6_VisibleWitness_Source_Alignment_Sherpet_v0.1.md`
- Transport: seven confirmed parts, all thirteen artefacts present, readable, and separately bounded.
- Evidence scope: Unit 6 SCF v0.4.2 and lineage package; observation journal and integration tests; contribution service; demo command surfaces.

The sherpet is confirmed as the sole governing evidence packet for this implementation revision.

## Exact source files inspected

- `src/intevia/observation/journal.py`
- `tests/test_observation_journal.py`
- `src/intevia/services/contribution_service.py`
- `tests/test_observation_integration.py`
- `src/intevia/commands/demo_activity_review.py`
- `tests/test_demo_activity_review.py`
- `src/intevia/commands/__init__.py`
- `src/intevia/commands/heartbeat.py`
- `src/intevia/commands/inspect.py`
- `src/intevia/commands/observation.py`
- `tests/test_observation_command.py`

Inspection was limited to source-alignment concerns for the Visible Witness shard; no mutation, execution, or PowerShell preparation was performed.

## Source-alignment findings

1. **Entry format and field order**

   The accepted SCF v0.4.2 requires the observation entry format:

   `event=<kind> activity=<id> contribution=<id-or-> actor=<actor-or-> prior=<state-or-> new=<state-or-> outcome=<outcome-or-> at=<timestamp>`

   `_format_entry` renders exactly this field set, in the accepted order, with:
   - `event` from `ObservationEventKind.value`;
   - `activity` from `activity_id` or `-`;
   - `contribution` from `contribution_id` or `-`;
   - `actor` from `actor_ref` or `-`;
   - `prior` from `prior_state` or `-`;
   - `new` from `new_state` or `-`;
   - `outcome` from `outcome` or `-`;
   - `at` from `occurred_at.isoformat()`.

   The formatter returns a single physical line per entry with no trailing newline.

2. **Snapshot rendering and newline boundary**

   `_render_snapshot` joins formatted entries with `"
"` and returns a string with no trailing newline.
   `observation_snapshot(journal)`:
   - calls `journal.list_entries()` exactly once;
   - passes the snapshot to `_render_snapshot`;
   - appends exactly one newline to the complete rendered snapshot, including the empty-snapshot case.

3. **Exception propagation and defensive boundaries**

   `observation_snapshot` does not wrap or reinterpret exceptions raised by `journal.list_entries()`.
   Subclass exceptions are propagated unchanged.

4. **CLI behaviour and dependency substitution**

   External adversarial review Finding F1 identified that monkey-patching the module-global `ObservationJournal` reference introduced avoidable test-order and parallel-execution risk.

   The CLI now supports optional direct journal injection:

   ```python
   def main(journal: ObservationJournal | None = None) -> int:
       journal = journal if journal is not None else ObservationJournal()
   ```

   This preserves:
   - default CLI behaviour;
   - return code `0`;
   - exact newline contract;
   - one-snapshot-call boundary;
   - all other production logic unchanged.

   The authentic CLI test now:
   - constructs a direct injected journal instance;
   - passes it to `main(journal)`;
   - captures real stdout;
   - asserts the return code is `0`;
   - asserts stdout is exactly `"
"`;
   - asserts the injected journal’s `list_entries()` was called exactly once;
   - contains no monkey-patching or module-global mutation.

5. **Snapshot discipline and determinism**

   Snapshot retrieval is performed once per call.
   Rendering is deterministic for a given journal state.
   No headings, entry numbering, total-count output, persistence, serialization, parsing guarantees, audit semantics, provenance assurance, or new identity semantics are introduced.

## Discrepancies corrected

- Restored strict one-line-per-entry rendering and exact field order.
- Replaced loose inclusion tests with exact-output tests covering:
  - empty snapshot;
  - single entry;
  - multiple entries in snapshot order;
  - all fields populated;
  - absent optional fields rendered as `-`;
  - exact field order;
  - one physical line per entry;
  - no formatter trailing newline;
  - exactly one wrapper newline;
  - exactly one `list_entries()` call;
  - subclass snapshot exception propagated unchanged;
  - deterministic repeated rendering.
- External adversarial correction F1:
  - replaced monkey-patching with direct dependency injection via `main(journal)`;
  - preserved default CLI behaviour;
  - preserved all rendering and newline boundaries;
  - introduced no new semantics or surfaces.

## Logical destination paths

- `src/intevia/commands/observation.py`
- `tests/test_observation_command.py`
- `docs/evidence/sprints/sprint-1/COE_Unit6_VisibleWitness_Implementation_Package_v0.3.md`

No repository mutation, execution, or deployment has been performed; this package is a governed proposal only.

## Shard identity

- **Shard code:** `U6-Ψ-VisibleWitness`
- **Shard name:** `Visible Witness`

## Known limitations and non-claims

- No persistence or durable audit trail is provided; output is a transient textual snapshot.
- Field integrity relies on upstream domain/journal validation.
- Timestamp normalisation remains owned by the journal/domain surface.
- No downstream parsing guarantee is provided.
- No provenance or tamper-evidence mechanisms are introduced.
- The command surface reports only current-process journal entries; it does not inspect hidden state or external logs.

## Test-matrix confirmation

The test suite contains exact-output assertions for:

- empty snapshot;
- single-entry snapshot;
- multiple-entry snapshot;
- deterministic repeated rendering;
- exactly one `list_entries()` call per snapshot;
- subclass snapshot exception propagated unchanged;
- authentic CLI behaviour using direct dependency injection.

## Final classification

**CORRECTION COMPLETE — READY FOR NARROW EXTERNAL CONFIRMATION**