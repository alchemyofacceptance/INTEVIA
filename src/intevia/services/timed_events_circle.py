"""Open one outstanding Circle vacancy occurrence under the runner's existing LO lock."""

from src.intevia.services import timed_events as te
from src.intevia.services.timed_events_elect import _answer, _one


def vacancy(*, alias, organism_id, enterer_id, purpose, source_ref, occurrence, due_at, known):
    """Revalidate the cause, then cite the actual Circle event for this occurrence."""
    changed = _one(alias, "SELECT public.circle_record_vacancy(%s,%s::uuid,%s,%s) AS changed",
                   [organism_id, source_ref, occurrence, enterer_id])["changed"]
    effect = _one(alias, """SELECT e.event_uuid::text AS effect
        FROM public.core_circlestateevent e JOIN public.core_circle c ON c.id=e.circle_id
        WHERE c.parent_organism_id=%s AND e.evidence_reference='vacancy:'||%s
          AND e.action=CASE %s WHEN 'OPENED' THEN 'OPEN_VACANCY' WHEN 'DORMANT' THEN 'BECOME_DORMANT' END""",
                  [organism_id, source_ref, occurrence])
    return _answer(changed, effect)


te.register("CIRCLE", "VACANCY", vacancy, refusals=("DISC_UNAVAILABLE", "CIRCLE_VACANCY_SOURCE"))
