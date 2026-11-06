"""Guiding Intention records and objectives. Office authority is interim pending CQ."""
import json
from uuid import UUID

from django.db import IntegrityError, connections, transaction

from core import models
from src.intevia.services.law_records import _records
from src.intevia.services.library_laws import LawReadUnavailable, our_laws
from src.intevia.services.organism_command_support import (
    _lock_actor,
    _lock_registered_organism,
)

AUTHORITY_MESSAGE = "This GI act requires the selected owner's effective Coordinator authority at receipt and execution. CQ is not available yet."
LAW_MESSAGE = "Cite at least one local law in force for this GI owner; every cited exact version must be applicable and in force."
MESSAGES = {
    "GI_AUTHORITY": AUTHORITY_MESSAGE,
    "GI_LAW_REQUIRED": LAW_MESSAGE,
    "GI_PREDECESSOR": "Choose the current GI version; creation needs a new GI identity and no predecessor.",
    "GI_RETIRED": "This GI is retired. Its history remains available; this route cannot restore it.",
    "GI_REQUEST_CONFLICT": "This request identity already records a different GI act.",
    "GI_COMPLETE": "This GI act could not complete with its recorded authority and attribution. No changes were committed.",
    "GI_CITATIONS_COMPLETE": "This GI act could not complete with all its exact law citations still in force. No changes were committed.",
}


class GIRefused(ValueError):
    """A named, governed GI refusal, separate from database or programming errors."""
    def __init__(self, message, *, code=None):
        self.code = code
        super().__init__(message)


def _uuid(value):
    if type(value) is not UUID:
        raise GIRefused("Choose an exact UUID for this GI act.", code="GI_UUID")
    return str(value)


def _rows(sql, params=(), *, alias="default"):
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, params)
        return _records(cursor) if cursor.description else []


def _citations(values):
    if not isinstance(values, (tuple, list)):
        raise GIRefused("Supply the exact law binding and version for each citation.", code="GI_CITATION_SHAPE")
    result = []
    for item in values:
        if not isinstance(item, dict) or set(item) != {"binding", "version"}:
            raise GIRefused("Supply the exact law binding and version for each citation.", code="GI_CITATION_SHAPE")
        binding = _uuid(item["binding"])
        if type(item["version"]) is not int or item["version"] <= 0:
            raise GIRefused("Choose a positive exact LIBRARY version identity.", code="GI_CITATION_SHAPE")
        result.append({"binding": binding, "version": item["version"]})
    # Order has no authority meaning. Do not deduplicate: duplicates are refused.
    return sorted(result, key=lambda item: item["binding"])


def _write(*, credential, organism_uuid, gi_uuid, request_uuid, operation,
           circle_uuid=None, predecessor=None, title=None, content=None, citations=(),
           route=None, database_alias="default"):
    """Translate governed refusals within this call's transaction boundary.

    A caller-owned outer transaction can defer checks until after this call
    returns. Its owner must handle IntegrityError at that outer boundary;
    known diagnostic codes use MESSAGES. Unrecognised database errors remain
    technical failures, rather than being relabelled as governed refusals.
    """
    if database_alias != "default":
        raise GIRefused("GI writes use the configured default database.", code="GI_DATABASE_ROUTE")
    payload = {
        "organism": _uuid(organism_uuid), "circle": _uuid(circle_uuid) if circle_uuid is not None else None,
        "gi": _uuid(gi_uuid), "request": _uuid(request_uuid), "operation": operation,
        "predecessor": _uuid(predecessor) if predecessor is not None else None,
        "title": title, "content": content, "citations": _citations(citations),
        "route": route if route is not None else ("CIRCLE_COORDINATOR" if circle_uuid is not None else "COORDINATOR_ROUTE"),
    }
    if not isinstance(payload["route"], str):
        raise GIRefused(AUTHORITY_MESSAGE, code="GI_AUTHORITY")
    if operation != "RETIRE" and any(not isinstance(value, str) or not value.strip() for value in (title, content)):
        raise GIRefused("Write a title and text for this GI version.", code="GI_TEXT")
    try:
        with transaction.atomic():
            received = _rows("SELECT statement_timestamp() AS stamp")[0]["stamp"]
            actor = _lock_actor(database_alias="default", credential=credential, unavailable=GIRefused)
            lo = models.LivingOrganism.objects.filter(organism_id=organism_uuid).first()
            # Match absent and unauthorised organisations before taking the
            # registered-owner lock (which otherwise discloses absence).
            if lo is None:
                raise GIRefused(AUTHORITY_MESSAGE, code="GI_AUTHORITY")
            _lock_registered_organism("default", organism_uuid)
            result = _rows("SELECT public.gi_execute(%s::jsonb,%s,%s,%s) AS result",
                           [json.dumps(payload), actor.pk, actor.access_epoch, received])[0]["result"]
            return {key: UUID(value) if key in {"gi", "act", "version"} else value for key, value in result.items()}
    except IntegrityError as exc:
        diag = getattr(exc.__cause__, "diag", None)
        code = getattr(diag, "message_primary", None)
        if code not in MESSAGES:
            raise
        raise GIRefused(MESSAGES[code], code=code) from exc


def create_intention(*, credential, organism_uuid, gi_uuid, request_uuid, title, content,
                     citations, circle_uuid=None, route=None, database_alias="default"):
    return _write(credential=credential, organism_uuid=organism_uuid, gi_uuid=gi_uuid,
                  request_uuid=request_uuid, operation="CREATE", title=title, content=content,
                  citations=citations, circle_uuid=circle_uuid, route=route, database_alias=database_alias)


def revise_intention(*, credential, organism_uuid, gi_uuid, request_uuid, predecessor,
                     title, content, citations, circle_uuid=None, route=None, database_alias="default"):
    return _write(credential=credential, organism_uuid=organism_uuid, gi_uuid=gi_uuid,
                  request_uuid=request_uuid, operation="REVISE", predecessor=predecessor, title=title,
                  content=content, citations=citations, circle_uuid=circle_uuid, route=route, database_alias=database_alias)


def retire_intention(*, credential, organism_uuid, gi_uuid, request_uuid, predecessor,
                     circle_uuid=None, route=None, database_alias="default"):
    return _write(credential=credential, organism_uuid=organism_uuid, gi_uuid=gi_uuid,
                  request_uuid=request_uuid, operation="RETIRE", predecessor=predecessor,
                  circle_uuid=circle_uuid, route=route, database_alias=database_alias)


def intention_history(*, credential, organism_uuid, gi_uuid, at=None, known_at=None, database_alias="default"):
    """Exact-record history; our_objectives adds the law-status projection.

    Present membership authorises historical records; past membership never does.
    The outer transaction retains our_laws' actor and organisation locks.
    """
    with transaction.atomic(using=database_alias):
        audience = our_laws(credential=credential, organism_uuid=organism_uuid, at=at,
                            known_at=known_at, database_alias=database_alias)
        if type(gi_uuid) is not UUID:
            raise ValueError("Choose an exact UUID for this GI history.")
        rows = _rows("""
            SELECT g.gi_uuid,lo.organism_id AS organism,c.circle_uuid AS circle,
                   a.act_uuid,a.operation,a.authority_source,a.actor_id AS enterer,
                   e.lineage_reference,e.authority_decision_reference,e.sequence AS event_sequence,
                   a.received_at,a.effective_at,a.recorded_at,
                   v.version_uuid,v.version_number,v.predecessor,v.title,v.content,
                   coalesce((SELECT jsonb_agg(jsonb_build_object('binding',x.binding_uuid,
                     'version',x.law_version_id) ORDER BY x.binding_uuid)
                     FROM public.core_gicitation x WHERE x.version_uuid=v.version_uuid),'[]'::jsonb) AS citations
              FROM public.core_guidingintention g JOIN public.core_livingorganism lo ON lo.id=g.organism_id
              LEFT JOIN public.core_circle c ON c.id=g.circle_id
              JOIN public.core_giact a ON a.gi_uuid=g.gi_uuid
              JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
              JOIN public.core_giversion v ON v.version_uuid=a.version_uuid
             WHERE lo.organism_id=%s AND g.gi_uuid=%s AND a.effective_at<=%s AND a.recorded_at<=%s
             ORDER BY a.effective_at,a.recorded_at,e.sequence
            """, [organism_uuid, gi_uuid, audience["at"], audience["known_at"]], alias=database_alias)
        if not rows:
            raise LawReadUnavailable("This GI history is unavailable in this organisation at these coordinates.")
        return {"organism": organism_uuid, "gi": gi_uuid, "at": audience["at"],
                "known_at": audience["known_at"], "history": rows,
                "current": None if rows[-1]["operation"] == "RETIRE" else rows[-1]}


# All objective records and law-status evidence share this statement snapshot.
# Recover exact law metadata at the version's original act, never through a
# current-law inner join that would erase an objective when its law ends.
_OBJECTIVES = """
    WITH cuts AS (SELECT %s::uuid AS subject, %s::timestamptz AS at_time,
                        %s::timestamptz AS known_time)
    SELECT g.gi_uuid, lo.organism_id AS organism, c.circle_uuid AS circle,
           a.act_uuid, a.operation, a.authority_source, a.actor_id AS enterer,
           e.lineage_reference, e.authority_decision_reference, e.sequence AS event_sequence,
           a.received_at, a.effective_at, a.recorded_at,
           v.version_uuid, v.version_number, v.predecessor, v.title, v.content,
           (SELECT jsonb_agg(jsonb_build_object(
               'binding', x.binding_uuid, 'version', x.law_version_id,
               'in_force', public.l_visible_window(b, cuts.at_time, cuts.known_time),
               'law', (to_jsonb(original) - 'effective_from' - 'effective_until') ||
                   jsonb_build_object('in_force_from', original.effective_from,
                                      'effective_until', ending.effective_until),
               'ending', CASE WHEN ending.ending_act IS NOT NULL THEN to_jsonb(ending) END
           ) ORDER BY x.binding_uuid)
              FROM public.core_gicitation x
              JOIN public.core_organismlawbinding b ON b.binding_uuid=x.binding_uuid
              LEFT JOIN LATERAL (
                  SELECT law.* FROM public.lib_laws_at(cuts.subject, origin.effective_at, origin.recorded_at) law
                   WHERE law.binding=x.binding_uuid AND law.version=x.law_version_id
              ) original ON true
              LEFT JOIN LATERAL (
                  SELECT end_event.effective_at AS effective_until, end_event.recorded_at,
                         end_event.event_uuid AS ending_act, ending_fact.end_kind,
                         ending_fact.decision_route AS ending_route, ending_fact.successor_id AS successor
                    FROM public.core_organismlawending ending_fact
                    JOIN public.core_livingorganismevent end_event ON end_event.id=ending_fact.end_event_id
                   WHERE ending_fact.binding_id=b.binding_uuid AND end_event.recorded_at<=cuts.known_time
              ) ending ON true
             WHERE x.version_uuid=v.version_uuid) AS citations
      FROM cuts JOIN public.core_livingorganism lo ON lo.organism_id=cuts.subject
      JOIN public.core_guidingintention g ON g.organism_id=lo.id
      LEFT JOIN public.core_circle c ON c.id=g.circle_id
      JOIN public.core_giact a ON a.gi_uuid=g.gi_uuid
      JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
      JOIN public.core_giversion v ON v.version_uuid=a.version_uuid
      JOIN public.core_giact origin ON origin.act_uuid=v.act_uuid
     WHERE a.effective_at<=cuts.at_time AND a.recorded_at<=cuts.known_time
     ORDER BY g.gi_uuid, a.effective_at, a.recorded_at, e.sequence
"""


def our_objectives(*, credential, organism_uuid, at=None, known_at=None,
                   include_history=False, database_alias="default"):
    """Read the LO's and all its Circles' objectives at effect/knowledge cuts.

    The unchanged Our laws reader authorises present membership, including
    SUSPENDED/RESTRICTED. Its actor/LO locks stay held through the projection.
    History includes retired GIs and all visible acts; the default omits them.
    Citation status is evaluated at the requested cuts, including in history;
    original act authority and exact cited content never follow a successor.
    """
    with transaction.atomic(using=database_alias):
        audience = our_laws(credential=credential, organism_uuid=organism_uuid, at=at,
                            known_at=known_at, database_alias=database_alias)
        if type(include_history) is not bool:
            raise ValueError("Choose true or false for GI history.")
        rows = _rows(_OBJECTIVES, [organism_uuid, audience["at"], audience["known_at"]], alias=database_alias)
        grouped = {}
        for row in rows:
            # A missing exact law projection is an evidence-integrity error,
            # never permission to omit a citation or silently substitute text.
            if not row["citations"] or any(citation["law"] is None for citation in row["citations"]):
                raise RuntimeError("A recorded GI citation has no exact law evidence.")
            row["needs_review"] = any(not citation["in_force"] for citation in row["citations"])
            grouped.setdefault(row["gi_uuid"], []).append(row)
        objectives = []
        for gi_uuid, history in grouped.items():
            latest = history[-1]
            retired = latest["operation"] == "RETIRE"
            if retired and not include_history:
                continue
            objective = {
                "gi": gi_uuid,
                "owner": {"kind": "CIRCLE" if latest["circle"] else "LIVING_ORGANISM",
                          "organism": organism_uuid, "circle": latest["circle"]},
                "retired": retired, "current": None if retired else latest,
                "needs_review": latest["needs_review"],
            }
            if include_history:
                objective["history"] = history
            objectives.append(objective)
        return {"organism": organism_uuid, "at": audience["at"], "known_at": audience["known_at"],
                "evaluated_at": audience["evaluated_at"], "objectives": objectives}
