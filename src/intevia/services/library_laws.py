"""D1 law-only member disclosure at the governing exact version; D2 act times."""

import re
from uuid import NAMESPACE_URL, uuid5

from django.db import connections, transaction

from core.models import CircleStateEvent
from src.intevia.services._record_names import full_name_and_short_label
from src.intevia.services.cq_reads import _project_authorities
from src.intevia.services.law_records import _coordinates, _records
from src.intevia.services.organism_command_support import (
    _lock_actor,
    _lock_registered_organism,
)
from src.intevia.services.organism_membership_service import _OWN_MEMBERSHIP_STATES


class LawReadUnavailable(PermissionError):
    """The caller has no current access to this organisation's law register."""


def our_laws(
    *, credential, organism_uuid, at=None, known_at=None,
    database_alias="default",
):
    """Return governing text to a current member, including a member Coordinator.

    D1 intentionally widens disclosure for law bindings only. The existing own-
    membership reader defines the current member states; this read folds those
    states at the present database time, independently of historical law cuts.
    D2's received time belongs to the adoption, including platform adoption.
    No per-organisation receipt is inferred. General LIBRARY policy is unchanged.
    """
    _coordinates(organism_uuid, at, known_at)
    with transaction.atomic(using=database_alias):
        actor = _lock_actor(
            database_alias=database_alias, credential=credential,
            unavailable=LawReadUnavailable,
        )
        # Refuse absent organisations and non-members through the same check,
        # before the registered-organism lock can expose an existence error.
        # Recheck membership in the final statement after acquiring that lock.
        with connections[database_alias].cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM public.core_livingorganism lo
                    JOIN public.core_organismmembership m ON m.living_organism_id=lo.id
                    WHERE lo.organism_id=%s AND m.identity_id=%s
                      AND public.s015_fold_effective_state(
                          'core_organismmembership',m.id,
                          public.intevia_clock_statement(),public.intevia_clock_statement()
                      )=ANY(%s::text[])
                )
                """,
                [organism_uuid, actor.pk, sorted(_OWN_MEMBERSHIP_STATES)],
            )
            if not cursor.fetchone()[0]:
                raise LawReadUnavailable("Reading these laws requires current membership in this organisation.")
        _lock_registered_organism(database_alias, organism_uuid)
        # One statement snapshot for current audience, both law cuts, exact text
        # and adoption evidence. The preliminary audience check authorises no
        # text; the shared actor-first lock remains held through disclosure.
        with connections[database_alias].cursor() as cursor:
            cursor.execute(
                """
                WITH audience AS (
                    SELECT public.intevia_clock_statement() AS evaluated_at
                      FROM public.core_livingorganism lo
                     WHERE lo.organism_id=%s
                       AND EXISTS (
                           SELECT FROM public.core_organismmembership m
                            WHERE m.living_organism_id=lo.id AND m.identity_id=%s
                              AND public.s015_fold_effective_state(
                                  'core_organismmembership',m.id,
                                  public.intevia_clock_statement(),public.intevia_clock_statement()
                              )=ANY(%s::text[])
                       )
                )
                SELECT audience.evaluated_at, laws.*
                  FROM audience
                  LEFT JOIN LATERAL public.lib_laws_at(
                      %s,coalesce(%s,audience.evaluated_at),
                      coalesce(%s,audience.evaluated_at)
                  ) laws ON true
                 ORDER BY laws.library_reference,laws.scope
                """,
                [organism_uuid, actor.pk, sorted(_OWN_MEMBERSHIP_STATES),
                 organism_uuid, at, known_at],
            )
            rows = _records(cursor)
        if not rows:
            raise LawReadUnavailable("Reading these laws requires current membership in this organisation.")
        evaluated_at = rows[0]["evaluated_at"]
        _project_authorities(rows, actor, organism_uuid, database_alias)
        records = []
        for row in rows:
            if row["binding"] is None:
                continue
            row.pop("evaluated_at")
            row["in_force_from"] = row.pop("effective_from")
            records.append(row)
        return {
            "organism": organism_uuid,
            "at": at if at is not None else evaluated_at,
            "known_at": known_at if known_at is not None else evaluated_at,
            "evaluated_at": evaluated_at,
            "laws": records,
        }


def _version_coordinates(organism_uuid, binding, known_at, database_alias):
    """Locate one candidate without reading its text. Only our_laws may release it.

    An inherited law can predate its subject. At that organisation's first
    effective instant, only events already known at the read's cut may count.
    """
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT r.resource_id AS reference, v.version_number,
                   greatest(b.in_force_from, life.first_at) AS probe_at,
                   ending.end_kind, ending.end_at
              FROM public.core_livingorganism subject
              JOIN public.core_organismlawbinding b
                ON (b.organism_id=subject.id OR b.scope_kind='PLATFORM')
              JOIN public.core_libraryresource r ON r.id=b.resource_id
              JOIN public.core_libraryresourceversion v ON v.id=b.version_id
              JOIN public.core_livingorganismevent adoption ON adoption.id=b.adoption_event_id
              CROSS JOIN LATERAL (
                   SELECT min(e.effective_at) AS first_at
                     FROM public.core_livingorganismevent e
                    WHERE e.living_organism_id=subject.id AND e.recorded_at<=%s
              ) life
              LEFT JOIN LATERAL (
                   SELECT x.end_kind, e.effective_at AS end_at
                     FROM public.core_organismlawending x
                     JOIN public.core_livingorganismevent e ON e.id=x.end_event_id
                    WHERE x.binding_id=b.binding_uuid AND e.recorded_at<=%s
              ) ending ON true
             WHERE subject.organism_id=%s AND b.binding_uuid=%s
               AND adoption.recorded_at<=%s AND life.first_at IS NOT NULL
            """,
            [known_at, known_at, organism_uuid, binding, known_at],
        )
        candidates = _records(cursor)
    return candidates[0] if candidates else None


# INTERIM (C1-444): governed titles and families will replace the two Alpha labels.
_FILM_TITLES = {"alpha-treasurer-duties": "Treasurer office", "alpha-board-minimum": "Board membership"}


def _film_root(reference):
    """Match the issued founding adoption coordinate in the trustee film setup."""
    return uuid5(NAMESPACE_URL, "urn:intevia:alpha:trustees-question:v1:adopt-" + reference)


def film_law_titles(organism_uuid, released, database_alias="default"):
    """Mark released bindings by same-organisation, same-resource descent from the film adoption."""
    candidates = [row["binding"] for row in released if row["library_reference"] in _FILM_TITLES]
    founders = {}
    if candidates:
        with connections[database_alias].cursor() as cursor:
            cursor.execute(
                """
                WITH RECURSIVE chain(origin, binding, previous, issuer, resource) AS (
                    SELECT b.binding_uuid, b.binding_uuid, b.predecessor_id, b.organism_id, b.resource_id
                      FROM public.core_organismlawbinding b
                      JOIN public.core_livingorganism lo ON lo.id=b.organism_id
                     WHERE lo.organism_id=%s AND b.binding_uuid=ANY(%s::uuid[])
                    UNION
                    SELECT c.origin, p.binding_uuid, p.predecessor_id, p.organism_id, p.resource_id
                      FROM chain c JOIN public.core_organismlawbinding p ON p.binding_uuid=c.previous
                       AND p.organism_id=c.issuer AND p.resource_id=c.resource
                )
                SELECT origin, binding FROM chain WHERE binding=ANY(%s::uuid[])
                """,
                [organism_uuid, candidates, [_film_root(ref) for ref in _FILM_TITLES]],
            )
            founders = dict(cursor.fetchall())
    titles = {}
    for row in released:
        reference = row["library_reference"]
        if reference in _FILM_TITLES and founders.get(row["binding"]) == _film_root(reference):
            titles[row["binding"]] = _FILM_TITLES[reference]
    return titles


def film_law_family(film_title, class_name):
    """Both law and office pages use the interim family only for a marked Policy."""
    return "Governance" if film_title and class_name == "POLICY" else None


def governance_board_names(organism_uuid, cut):
    """Resolve the Governance Circle born by this cut within the admitted organisation."""
    birth = CircleStateEvent.objects.filter(
        circle__parent_organism__organism_id=organism_uuid, circle__kind="GOVERNANCE", sequence=1,
        effective_at__lte=cut, received_at__lte=cut, recorded_at__lte=cut,
    ).select_related("circle").only("circle__parent_organism_id", "circle__kind").first()
    if birth is None:
        return None, None
    circle = birth.circle
    return full_name_and_short_label(owner_id=circle.parent_organism_id, circle=circle, cut=cut)


def law_heading(content):
    """Read only a top-level heading on the opening line. Keep all other text verbatim."""
    match = re.match(r"\A\ufeff?#[ \t]+([^\r\n]+)(?:\r\n|\r|\n|\Z)", content)
    if match and (heading := match[1].strip()):
        return heading, content[match.end():]
    return None, content


def law_display_rows(organism_uuid, released, database_alias="default"):
    """Name already released laws from film provenance, opening text, then class."""
    titles = film_law_titles(organism_uuid, released, database_alias)
    rows = []
    for row in released:
        reference = row["library_reference"]
        film = titles.get(row["binding"])
        heading, _ = law_heading(row["content"])
        title = film or (heading if heading != reference else None) or row["display_class"] or row["class_name"]
        rows.append({
            "binding": row["binding"], "version_number": row["version_number"], "reference": reference,
            "title": title, "family": film_law_family(film, row["class_name"]),
            "class_name": row["class_name"], "display_class": row["display_class"],
        })
    return rows


def _law_ranks(class_names, database_alias):
    with connections[database_alias].cursor() as cursor:
        cursor.execute("SELECT name, public.l_law_rank(name) FROM unnest(%s::text[]) AS names(name)",
                       [class_names])
        return dict(cursor.fetchall())


def law_class_groups(rows, database_alias="default"):
    """Group in stable register order within each rank, with unranked classes last."""
    groups = {}
    for row in rows:
        key = row["class_name"]
        group = groups.setdefault(key, {
            "label": row["display_class"] or key, "family": row["family"], "rows": [],
        })
        group["rows"].append(row)
        if row["family"] != group["family"]:
            group["family"] = None
    if not groups:
        return []
    ranks = _law_ranks(list(groups), database_alias)
    ordered = sorted(groups, key=lambda key: (ranks[key] is None, ranks[key] or 0))
    return [groups[key] for key in ordered]


def _released_version(scope, binding, version_number, current):
    known = current["known_at"]
    now = current["at"]
    if known > now:
        raise LawReadUnavailable()
    for row in current["laws"]:
        if row["binding"] == binding and row["version_number"] == version_number:
            return row, "In force now"

    return _released_historical_version(scope, binding, version_number, current)


def _released_historical_version(scope, binding, version_number, current):
    known = current["known_at"]
    now = current["at"]
    candidate = _version_coordinates(scope["organism_uuid"], binding, known, scope["database_alias"])
    if (candidate is None or candidate["version_number"] != version_number
            or candidate["end_kind"] not in ("SUPERSEDED", "RETIRED") or candidate["end_at"] > now
            or candidate["probe_at"] >= candidate["end_at"]):
        raise LawReadUnavailable()
    historical = our_laws(**scope, at=candidate["probe_at"], known_at=known)
    for row in historical["laws"]:
        if (row["binding"] == binding and row["version_number"] == version_number
                and row["library_reference"] == candidate["reference"]):
            return row, {"SUPERSEDED": "Superseded", "RETIRED": "No longer in force"}[candidate["end_kind"]]
    raise LawReadUnavailable()


def our_law_version(*, credential, organism_uuid, binding, version_number, known_at=None,
                    database_alias="default"):
    """Release one exact adoption at both cuts under the current member's grant."""
    scope = dict(credential=credential, organism_uuid=organism_uuid, database_alias=database_alias)
    with transaction.atomic(using=database_alias):
        current = our_laws(**scope, known_at=known_at)
        return _released_version(scope, binding, version_number, current)


def _earlier_coordinates(binding, known, database_alias):
    """Return bounded candidate addresses only, within this law's original adoption scope."""
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT b.binding_uuid, v.version_number
              FROM public.core_organismlawbinding selected
              JOIN public.core_organismlawbinding b
                ON b.resource_id=selected.resource_id AND b.scope_key=selected.scope_key
               AND b.organism_id=selected.organism_id AND b.in_force_from<selected.in_force_from
              JOIN public.core_libraryresourceversion v ON v.id=b.version_id
              JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
             WHERE selected.binding_uuid=%s AND e.recorded_at<=%s
             ORDER BY b.in_force_from DESC, b.binding_uuid
             LIMIT 20
            """, [binding, known],
        )
        return cursor.fetchall()


def our_law_version_page(*, credential, organism_uuid, binding, version_number, database_alias="default"):
    """Release the addressed version and each earlier link at the same knowledge cut."""
    scope = dict(credential=credential, organism_uuid=organism_uuid, database_alias=database_alias)
    with transaction.atomic(using=database_alias):
        current = our_laws(**scope)
        selected, status = _released_version(scope, binding, version_number, current)
        earlier = []
        current_version = None
        if status == "In force now":
            for previous, number in _earlier_coordinates(binding, current["known_at"], database_alias):
                try:
                    row, previous_status = _released_historical_version(scope, previous, number, current)
                except LawReadUnavailable:
                    # Inherited history can predate the subject's first release.
                    continue
                earlier.append({"binding": row["binding"], "version_number": number,
                                "status": previous_status, "in_force_from": row["in_force_from"],
                                "end_at": row["effective_until"]})
        else:
            # The live register already released the version in force at this cut.
            # A different scope or issuer can adopt the same published resource.
            for row in current["laws"]:
                if (row["resource"] == selected["resource"] and row["scope"] == selected["scope"]
                        and row["issuer"] == selected["issuer"]):
                    current_version = {"binding": row["binding"], "version_number": row["version_number"]}
                    break
        mark, = law_display_rows(organism_uuid, [selected], database_alias)
        return {"law": selected, "status": status, "display": mark, "earlier": earlier,
                "end_at": selected["effective_until"] if status != "In force now" else None,
                "current_version": current_version}
