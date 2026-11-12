"""The ELECT consumers of the TIMED EVENTS engine: term end, resignation coverage, vacancy.

Each adapter calls the landed ELECT writer and then looks for the effect itself. A
writer's false return proves nothing (it has several meanings), so it is never read
as done:

- the effect exists and the writer made it now: opened;
- the effect exists and the writer made nothing: found done, citing the effect;
- the effect does not exist: no longer due, and nothing is written.

The engine calls an adapter inside its savepoint, on the engine's own connection
(``alias``), under the LO lock and the claims it has already taken.
"""

from src.intevia.services import timed_events as te


def _one(alias, sql, params):
    rows = te._rows(alias, sql, params)
    return rows[0] if rows else None


def _answer(changed, effect):
    if effect is None:
        return te.Opening(te.NOT_DUE)
    return te.Opening(te.OPENED if changed else te.FOUND_DONE, effect_ref=effect["effect"])


def term_end(*, alias, organism_id, enterer_id, purpose, source_ref, occurrence, due_at, known):
    """Record the due Board office term end, citing the due ASSIGN event."""
    office = _one(alias, """SELECT a.id FROM public.core_contextualroleassignment a
        JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
        WHERE d.living_organism_id=%s AND a.assignment_uuid::text=%s""", [organism_id, source_ref])
    if office is None:
        return te.Opening(te.NOT_DUE)
    changed = _one(alias, "SELECT public.elect_record_term_end(%s,%s,%s) AS changed",
                   [organism_id, office["id"], enterer_id])["changed"]
    effect = _one(alias, """SELECT h.event_uuid::text AS effect
        FROM public.core_contextualroleassignmentstateevent h
        WHERE h.assignment_id=%s AND h.action='END' AND h.evidence_reference='elect-expiry:'||%s""",
                  [office["id"], occurrence])
    return _answer(changed, effect)


def resignation(*, alias, organism_id, enterer_id, purpose, source_ref, occurrence, due_at, known):
    """Record the coverage assessment a dated Board resignation owes."""
    changed = _one(alias, "SELECT public.elect_resignation_coverage(%s,%s::uuid,%s) AS changed",
                   [organism_id, source_ref, enterer_id])["changed"]
    effect = _one(alias, """SELECT x.event_uuid::text AS effect FROM public.core_coverageassessment x
        JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
        WHERE c.living_organism_id=%s AND x.evidence_reference='elect-resignation:'||%s
        ORDER BY x.id LIMIT 1""", [organism_id, source_ref])
    return _answer(changed, effect)


def vacancy(*, alias, organism_id, enterer_id, purpose, source_ref, occurrence, due_at, known):
    """Open the vacancy an effective ending owes."""
    changed = _one(alias, "SELECT public.elect_start_vacancy(%s,%s::uuid,%s) AS changed",
                   [organism_id, source_ref, enterer_id])["changed"]
    effect = _one(alias, """SELECT v.ending::text AS effect FROM public.core_electvacancy v
        WHERE v.organism_id=%s AND v.ending::text=%s""", [organism_id, source_ref])
    return _answer(changed, effect)


# The refusal codes the landed ELECT writers give, read at source: cq_refuse('DISC_UNAVAILABLE')
# in elect_record_term_end and elect_resignation_coverage; cq_refuse('ELECT_VACANCY_SOURCE')
# in elect_vacancy_guard, which also records a resignation's assessment for a seat vacancy.
te.register("ELECT", "TERM_END", term_end, refusals=("DISC_UNAVAILABLE",))
te.register("ELECT", "RESIGNATION", resignation, refusals=("DISC_UNAVAILABLE",))
te.register("ELECT", "VACANCY", vacancy, refusals=("ELECT_VACANCY_SOURCE", "DISC_UNAVAILABLE"))
