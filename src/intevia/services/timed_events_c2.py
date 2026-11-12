"""The C2 consumer of the TIMED EVENTS engine: LO coverage recovery, one step per attempt.

The landed command (``organism_recovery.advance``) notices every due timed input and
then takes every due phase step, under one LO lock. The engine takes the same steps one
at a time, in the same order: the oldest actionable notice first; a phase step only when
no notice is actionable (the selectors in migration 0095 keep that order).

- NOTICE calls the landed ``c2_notice_input`` for one source. The effect is the coverage
  assessment that cites the boundary at its due time.
- PHASE calls ``organism_recovery.phase_step``, the lines the landed command repeats. The
  effect is the LO event that cites the head it stepped from.

The enterer is never the carrier. The landed writers name the founding enterer for a
notice and the head's enterer for a phase step, and the receipt takes its enterer from
the effect's own proof (``timed_events.ENTERER_FROM_PROOF``). ``enterer_id`` is not used.

As with ELECT, a writer's return is never read as done: only the effect itself proves it.
"""

from core.models import LivingOrganism
from src.intevia.services import organism_recovery as recovery
from src.intevia.services import timed_events as te
from src.intevia.services.clock import database_now
from src.intevia.services.timed_events_elect import _answer, _one


def notice(*, alias, organism_id, enterer_id, purpose, source_ref, occurrence, due_at, known):
    """Notice one due timed input, as the landed command does for each."""
    changed = _one(alias, "SELECT public.c2_notice_input(%s,%s) AS changed", [organism_id, source_ref])["changed"]
    effect = _one(alias, """SELECT a.event_uuid::text AS effect FROM public.core_coverageassessment a
        JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
        WHERE c.living_organism_id=%s AND a.evidence_reference='c2-boundary:'||%s
        AND public.te_c2_occurrence(a.effective_at)=%s ORDER BY a.id LIMIT 1""",
                  [organism_id, source_ref, occurrence])
    return _answer(changed, effect)


def phase(*, alias, organism_id, enterer_id, purpose, source_ref, occurrence, due_at, known):
    """Take the due phase step from the selected head, and from no other head."""
    lo = LivingOrganism.objects.using(alias).get(pk=organism_id)
    now = database_now(alias)
    head = recovery.head_at(alias, lo, now, now)
    stepped = None
    if head is not None and str(head.event_uuid) == source_ref:
        # The check at the point of action: the landed step reads the head again at this same
        # cut and steps only if it is still due. A recovery recorded since selection stops it.
        stepped = recovery.phase_step(alias, lo, now)
    effect = _one(alias, """SELECT e.event_uuid::text AS effect FROM public.core_livingorganismevent e
        WHERE e.living_organism_id=%s AND e.action IN ('K2_DEACTIVATE','K2_DISSOLVE')
        AND e.evidence_reference='k2:'||%s ORDER BY e.id LIMIT 1""", [organism_id, source_ref])
    return _answer(stepped is not None, effect)


# Neither landed writer refuses by a name of its own: c2_notice_input answers false, and the
# phase step's refusals are the chain guards', which keep their SQLSTATE class.
te.register("C2", "NOTICE", notice)
te.register("C2", "PHASE", phase)
