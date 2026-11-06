"""D1 law-only member disclosure at the governing exact version; D2 act times."""

from django.db import connections, transaction

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
                          statement_timestamp(),statement_timestamp()
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
                    SELECT statement_timestamp() AS evaluated_at
                      FROM public.core_livingorganism lo
                     WHERE lo.organism_id=%s
                       AND EXISTS (
                           SELECT FROM public.core_organismmembership m
                            WHERE m.living_organism_id=lo.id AND m.identity_id=%s
                              AND public.s015_fold_effective_state(
                                  'core_organismmembership',m.id,
                                  statement_timestamp(),statement_timestamp()
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
