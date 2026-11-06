"""Scoped LAW register reads for governed callers. P2 owns the command route."""

from datetime import datetime
import json
from uuid import UUID

from django.db import connections
from django.utils.timezone import is_naive


def _coordinates(organism_uuid, at, known_at):
    if not isinstance(organism_uuid, UUID):
        raise ValueError("Choose an organisation by UUID.")
    for value in (at, known_at):
        if value is not None and (not isinstance(value, datetime) or is_naive(value)):
            raise ValueError("LAW register coordinates must be timezone-aware.")


def _records(cursor):
    """Decode PostgreSQL JSON columns at the shared raw-cursor boundary.

    Use type OIDs (json=114, jsonb=3802), not field names or text contents:
    deliberate ::text projections and document text must remain strings.
    SQL NULL, decoded mappings/lists and non-string scalars pass through unchanged.
    """
    names = [column[0] for column in cursor.description]
    json_columns = {
        index for index, column in enumerate(cursor.description)
        if column[1] in (114, 3802)
    }
    return [
        dict(zip(names, (
            json.loads(value)
            if index in json_columns and isinstance(value, (str, bytes, bytearray))
            else value
            for index, value in enumerate(row)
        ), strict=True))
        for row in cursor.fetchall()
    ]


def laws_in_force(organism_uuid, *, at=None, known_at=None, database_alias="default"):
    """Return local, Circle ToR and inherited bindings at the two supplied cuts.

    The two default cuts use the same database statement time. A root adoption
    and its separate platform adoption retain their distinct scope and lineage.
    """
    _coordinates(organism_uuid, at, known_at)
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """SELECT l.*, r.resource_id AS library_reference
                 FROM public.l_laws_at(%s, coalesce(%s, statement_timestamp()),
                                     coalesce(%s, statement_timestamp())) l
                 JOIN public.core_libraryresource r ON r.id=l.resource
                ORDER BY r.resource_id,l.scope""",
            [organism_uuid, at, known_at],
        )
        return _records(cursor)


def law_history(
    organism_uuid, resource_reference, *, known_at=None, database_alias="default"
):
    """Exact adoptions and causes visible by the knowledge cut, including retired laws.

    This is a register projection, upstream of the provision resolver and of
    disclosure. References to the chain identify the source act and its parties.
    """
    _coordinates(organism_uuid, None, known_at)
    if not isinstance(resource_reference, str) or not resource_reference.strip():
        raise ValueError("Choose an exact LIBRARY resource reference.")
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """
            WITH coordinate AS (SELECT coalesce(%s,statement_timestamp()) AS known)
            SELECT b.binding_uuid,b.scope_key,b.law_class,b.consequence_class,b.version_id,
                   v.version_number,b.predecessor_id,b.owner_kind,b.owner_circle_id,b.proposing_circle_id,
                   b.terms_binding_id,b.area,b.in_force_from,b.decision_route,
                   a.event_uuid AS adoption_act,a.authority_decision_reference,a.authority_basis_id,
                   a.actor_state,a.actor_id,a.actor_capacity,a.enterer_identity_id,
                   a.received_at,a.recorded_at,ending.effective_until,ending.end_kind,
                   ending.ending_act,ending.ending_route,ending.successor_id
              FROM public.core_livingorganism subject CROSS JOIN coordinate
              JOIN public.core_organismlawbinding b ON b.organism_id=subject.id OR b.scope_kind='PLATFORM'
              JOIN public.core_libraryresource r ON r.id=b.resource_id
              JOIN public.core_libraryresourceversion v ON v.id=b.version_id
              JOIN public.core_livingorganismevent a ON a.id=b.adoption_event_id
              LEFT JOIN LATERAL (
                SELECT x.end_kind,x.decision_route AS ending_route,x.successor_id,e.event_uuid AS ending_act,e.effective_at AS effective_until
                  FROM public.core_organismlawending x JOIN public.core_livingorganismevent e ON e.id=x.end_event_id
                 WHERE x.binding_id=b.binding_uuid AND e.recorded_at<=coordinate.known
              ) ending ON true
             WHERE subject.organism_id=%s AND r.resource_id=%s AND a.recorded_at<=coordinate.known
               AND EXISTS (SELECT FROM public.core_livingorganismevent life WHERE life.living_organism_id=subject.id
                           AND life.recorded_at<=coordinate.known)
             ORDER BY b.scope_key,b.in_force_from,b.binding_uuid
            """,
            [known_at, organism_uuid, resource_reference],
        )
        return _records(cursor)


# Both views use one PostgreSQL statement snapshot. The version FK is evidence
# supplied at creation. A text reference, even a plausible one, cannot supply it.
_CITATION_HISTORY = """
    WITH RECURSIVE citation AS (
        SELECT p.provision_uuid, p.provision_reference, p.provision_version, p.relationship,
               d.role_uuid, lo.organism_id, d.received_at, d.effective_at, d.recorded_at,
               k.version_id AS cited_version, k.locator, v.version_number AS cited_number,
               r.resource_id AS resource_reference,
               CASE WHEN k.locator IS NULL THEN v.content ELSE src.content END AS cited_clause
          FROM public.core_organismroledefinitionprovision p
          JOIN public.core_organismroledefinition d ON d.id=p.definition_id
          JOIN public.core_livingorganism lo ON lo.id=d.living_organism_id
          LEFT JOIN public.core_lawprovisionlocator k ON k.provision_id=p.id
          LEFT JOIN public.core_libraryresourceversion v ON v.id=k.version_id
          LEFT JOIN public.core_libraryresource r ON r.id=v.resource_id
          LEFT JOIN LATERAL public.l_clauses(v.content) src ON src.locator=k.locator
         WHERE lo.organism_id=%s AND p.provision_uuid=%s
    ), origins AS (
        SELECT law.binding, law.effective_until AS original_until, count(*) OVER () AS matches
          FROM citation c
          CROSS JOIN LATERAL public.l_laws_at(c.organism_id,c.received_at,c.recorded_at) law
          JOIN public.core_organismlawbinding b ON b.binding_uuid=law.binding
         WHERE law.version=c.cited_version AND b.scope_kind IN ('PLATFORM','LIVING_ORGANISM')
           AND public.l_visible_window(b,c.effective_at,c.recorded_at)
    ), chain AS (
        SELECT b.* FROM origins o
          JOIN public.core_organismlawbinding b ON b.binding_uuid=o.binding
         WHERE o.matches=1
        UNION ALL
        SELECT b.* FROM chain parent
          JOIN public.core_organismlawbinding b ON b.predecessor_id=parent.binding_uuid
           AND b.resource_id=parent.resource_id AND b.scope_key=parent.scope_key
    ), history AS (
        SELECT b.binding_uuid, b.version_id, v.version_number, b.scope_key, b.law_class,
               b.consequence_class, b.in_force_from, a.event_uuid AS adoption_act,
               a.authority_decision_reference AS authority_reference, a.recorded_at AS adopted,
               issuing.organism_id AS issuer, b.decision_route,
               e.effective_at AS end_at, e.recorded_at AS end_known, x.end_kind,
               e.event_uuid AS ending_act, x.decision_route AS ending_route,
               (c.locator IS NULL OR cl.locator IS NOT NULL) AS present,
               CASE WHEN c.locator IS NULL THEN convert_to(v.content,'UTF8')=convert_to(c.cited_clause,'UTF8')
                    ELSE cl.content=c.cited_clause END AS same_text
          FROM chain b CROSS JOIN citation c
          JOIN public.core_livingorganism issuing ON issuing.id=b.organism_id
          JOIN public.core_livingorganismevent a ON a.id=b.adoption_event_id
          JOIN public.core_libraryresourceversion v ON v.id=b.version_id
          LEFT JOIN public.core_organismlawending x ON x.binding_id=b.binding_uuid
          LEFT JOIN public.core_livingorganismevent e ON e.id=x.end_event_id
          LEFT JOIN LATERAL public.l_clauses(v.content) cl ON cl.locator=c.locator
    ), cuts AS (
        SELECT 'at_act' AS view, coalesce(%s,c.effective_at) AS cut,
               coalesce(%s,c.recorded_at) AS known FROM citation c
        UNION ALL SELECT 'now', statement_timestamp(), statement_timestamp()
    )
    SELECT c.provision_uuid, c.provision_reference, c.provision_version, c.relationship,
           c.role_uuid, c.cited_version, c.cited_number, c.resource_reference, c.locator,
           c.received_at, c.effective_at, c.recorded_at, cuts.view, cuts.cut, cuts.known,
           (SELECT count(*) FROM origins) AS origin_count,
           (SELECT original_until FROM origins WHERE matches=1) AS original_until,
           h.binding_uuid, h.version_id, h.version_number, h.scope_key, h.law_class,
           h.consequence_class, h.in_force_from, h.adoption_act, h.authority_reference,
           h.issuer, h.decision_route, h.present, h.same_text,
           CASE WHEN h.end_known<=cuts.known THEN h.end_at END AS effective_until,
           CASE WHEN h.end_known<=cuts.known THEN h.end_kind END AS end_kind,
           CASE WHEN h.end_known<=cuts.known THEN h.ending_act END AS ending_act,
           CASE WHEN h.end_known<=cuts.known THEN h.ending_route END AS ending_route,
           public.l_visible_window(b,cuts.cut,cuts.known) AS governing
      FROM citation c CROSS JOIN cuts
      LEFT JOIN history h ON h.adopted<=cuts.known
      LEFT JOIN public.core_organismlawbinding b ON b.binding_uuid=h.binding_uuid
     ORDER BY cuts.view, h.in_force_from
"""


def _binding_details(row):
    return {key: row[key] for key in (
        "binding_uuid", "version_id", "version_number", "scope_key", "issuer",
        "law_class", "consequence_class", "adoption_act", "authority_reference",
        "decision_route", "in_force_from", "effective_until",
    )}


def _citation_view(rows):
    head = rows[0]
    view = dict(at=head["cut"], known_at=head["known"], governing=None, first_divergence=None)
    if head["known"] < head["recorded_at"] or head["cut"] < head["effective_at"]:
        return dict(view, status="NOT_YET_CITED")
    if head["cited_version"] is None:
        return dict(view, status="UNRESOLVED", reason="NO_VERSION_LINK")
    if head["origin_count"] != 1:
        return dict(view, status="UNRESOLVED",
                    reason="AMBIGUOUS_SCOPE" if head["origin_count"] else "NOT_LAW_HERE")

    past = [row for row in rows if row["binding_uuid"] and row["in_force_from"] <= head["cut"]]
    for row in past[1:]:
        if not row["present"]:
            status = "GONE"
        elif not row["same_text"]:
            status = "CHANGED"
        elif (row["law_class"], row["consequence_class"]) != (head["law_class"], head["consequence_class"]):
            status = "RECLASSIFIED"
        else:
            continue
        view["first_divergence"] = dict(status=status, binding=_binding_details(row))
        break

    current = next((row for row in past if row["governing"]), None)
    if current is None:
        later_known = any(row["binding_uuid"] and row["in_force_from"] > head["cut"] for row in rows)
        return dict(view, status="GAP" if later_known else "RETIRED",
                    previous=_binding_details(past[-1]) if past else None,
                    ending_act=past[-1]["ending_act"] if past else None,
                    ending_route=past[-1]["ending_route"] if past else None)
    view["governing"] = _binding_details(current)
    view["crossed_gap"] = any(row["end_kind"] == "RETIRED" for row in past[:-1])
    if view["first_divergence"]:
        return dict(view, status=view["first_divergence"]["status"])
    return dict(view, status="IN_FORCE" if len(past) == 1 else "EQUIVALENT")


def resolve_citation(organism_uuid, provision_uuid, *, at=None, known_at=None, database_alias="default"):
    """Return exact office citation evidence and its act-time and current LAW readings.

    Defaults for the act view come from the definition. The second view always
    uses database time. Unknown or foreign provisions return None. Like the LAW
    register this is an internal read for governed callers. It returns no clause bytes.
    """
    _coordinates(organism_uuid, at, known_at)
    if not isinstance(provision_uuid, UUID):
        raise ValueError("Choose an office provision by UUID.")
    with connections[database_alias].cursor() as cursor:
        cursor.execute(_CITATION_HISTORY, [organism_uuid, provision_uuid, at, known_at])
        rows = _records(cursor)
    if not rows:
        return None
    views = {name: [r for r in rows if r["view"] == name] for name in ("at_act", "now")}
    origin = views["now"][0]
    cited = {key: origin[key] for key in (
        "provision_uuid", "role_uuid", "relationship", "provision_reference", "provision_version",
        "resource_reference", "cited_version", "cited_number", "locator",
        "effective_at", "received_at", "recorded_at",
    )}
    cited["binding"] = None
    if origin["origin_count"] == 1:
        cited["binding"] = dict(_binding_details(origin), effective_until=origin["original_until"])
    return {"cited": cited, **{name: _citation_view(records) for name, records in views.items()}}
