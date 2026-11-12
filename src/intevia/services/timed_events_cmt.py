"""The committee consumer of the TIMED EVENTS engine: a mandate's stated end date.

The effect is the marker row (migration 0094). Writing it makes the landed committee
refresh run at commit, dated at the end date, and that refresh writes every composition
gap and custody move through its own guards. This adapter writes no custody row.

As with ELECT, the writer's false return is never read as done: only the marker itself
proves completion.
"""

from src.intevia.services import timed_events as te
from src.intevia.services.timed_events_elect import _answer, _one


def mandate_end(*, alias, organism_id, enterer_id, purpose, source_ref, occurrence, due_at, known):
    """Mark the mandate's stated end date, if it is genuine and not yet marked."""
    changed = _one(alias, "SELECT public.cmt_record_dated_end(%s,%s::uuid,%s) AS changed",
                   [organism_id, source_ref, enterer_id])["changed"]
    effect = _one(alias, """SELECT x.mandate::text AS effect FROM public.core_cmtmandateend x
        WHERE x.organism_id=%s AND x.mandate::text=%s""", [organism_id, source_ref])
    return _answer(changed, effect)


# DISC_UNAVAILABLE: the writer's own refusal of an enterer who is no longer an active member.
# CQ_COMPLETE: the marker guard's refusal of a date that is not genuine. The writer selects only
# due dates, so the guard is a backstop: the refusal is declared in case it is ever reached.
te.register("CMT", "MANDATE_END", mandate_end, refusals=("DISC_UNAVAILABLE", "CQ_COMPLETE"))
