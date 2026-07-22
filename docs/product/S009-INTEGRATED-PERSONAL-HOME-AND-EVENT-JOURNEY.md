# S009 Integrated Personal Home and Event Journey

## Composition boundary

The authenticated root is the personal home. CORE authenticates, arranges,
navigates, renders, and applies generic personal-response protection. EVENT
owns visibility, Event projections, current-registration selection,
attendance meaning, and subject-safe record history.

The home may bring truths together. It must not turn their proximity into a
new truth. Registration and attendance are rendered as separate records. No
persistent aggregate, combined status, progress, completion, eligibility,
entitlement, next action, or session-stored journey state exists.

## Read contract

All S009 domain routes are authenticated GET-only. The existing logout route
remains CSRF-protected POST-only. S009 connects no Event, registration, or
attendance command service.

Visible Events use one EVENT-owned predicate:

```text
owner
OR personal registration
OR historical participation
OR personal attendance
```

Results are default-deny, deduplicated, and ordered by `created_at`, then `pk`.
Staff and superuser flags do not widen product visibility.

## Presentation contract

The home and Event detail receive presentation DTOs rather than raw model
enums. A valid single registration predecessor chain selects either a current
record or a cancelled prior record. Missing, branching, disconnected, or
otherwise ambiguous lineage fails closed.

Attendance is mapped independently:

- present: `A current attendance record says you were present.`
- withdrawn: `A previous attendance record was withdrawn. It does not record you as absent.`
- no row: `No attendance record is currently held for you for this Event. This is not an absence record.`

Record history exposes only neutral Human-facing sentences and timestamps.
It excludes actors, authority, evidence, provenance, rationale, cancellation
basis, eligibility internals, idempotency, staff notes, lineage identifiers,
and other Humans.

## Personal-response protection

Home, Event, registration, attendance, history, and restricted responses use
`Cache-Control: private, no-store` and `Vary: Cookie`. Session epoch, access
state, and the accepted eight-hour absolute cutoff remain fail-closed.

## Human-qualified contact deferral

The Human Governor deferred the record-question contact reference from S009.
This slice adds no support address, mailto link, contact page, form, dispute
workflow, or correction workflow. Internal pre-alpha questions are handled
outside the product.

## Non-goals

S009 adds no migration, API, Session architecture, booking, cancellation,
re-registration, attendance command, contact mutation, page-view logging,
admin model registration, deployment configuration, or deployment claim.