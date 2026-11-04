"""Circle participation and coverage facts at the supplied effective and knowledge cuts.

Only my_places is a reader for a person. The other functions are internal inputs
to a governed act. They neither establish an audience nor authorise the act.
"""
from dataclasses import dataclass
from uuid import UUID

from django.db import connections, transaction

from src.intevia.services import scoped_determinations as scoped
from src.intevia.services.clock import database_now
from src.intevia.services.organism_command_support import _canonical_key, _lock_actor


@dataclass(frozen=True)
class Place:
    circle: UUID
    membership: UUID
    participation: UUID


class CircleAccessRefused(ValueError):
    pass


# One predicate for a recorded place. An old period remains closed if the
# person subsequently rejoins the LO. Presentations do not inspect cached state.
LIVE_PLACES = """
    SELECT c.id AS circle_id, c.circle_uuid, m.id AS membership_id, m.membership_uuid,
           m.identity_id, p.participation_uuid
    FROM core_circle c
    JOIN core_livingorganism lo ON lo.id=c.parent_organism_id
    JOIN core_circleparticipation p ON p.circle_id=c.id
    JOIN core_organismmembership m ON m.id=p.membership_id AND m.living_organism_id=lo.id
    WHERE lo.organism_id=%(lo)s AND p.effective_at<=%(at)s
      AND p.received_at<=%(known)s AND p.recorded_at<=%(known)s
      AND public.s015_fold_effective_state('core_organismmembership',m.id,%(at)s,%(known)s)='ACTIVE'
      AND NOT EXISTS (
        SELECT FROM core_circleparticipationend e WHERE e.participation_id=p.id
          AND e.effective_at<=%(at)s AND e.received_at<=%(known)s AND e.recorded_at<=%(known)s
      )
"""


def my_places(*, credential, organism_uuid, alias="default"):
    """Return only the signed-in person's own place references in this LO."""
    with transaction.atomic(using=alias):
        actor = _lock_actor(database_alias=alias, credential=credential, unavailable=CircleAccessRefused)
        at = database_now(alias)
        with connections[alias].cursor() as cursor:
            cursor.execute(f"""
                WITH places AS ({LIVE_PLACES})
                SELECT circle_uuid, membership_uuid, participation_uuid FROM places
                WHERE identity_id=%(actor)s ORDER BY circle_uuid, participation_uuid
            """, {"lo": organism_uuid, "at": at, "known": at, "actor": actor.pk})
            return tuple(Place(*row) for row in cursor.fetchall())


def coordinator_memberships(alias, circle, at, known):
    """Exact-circle qualified holders. C2's LO-wide aggregation loses assignment identity.

    GENERIC holders have a verified assignment record and a continuing exact place.
    Other designations retain the separate scoped finding.
    """
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT member FROM public.circle_holders(%s,%s,%s)", [circle.pk, at, known])
        return frozenset(row[0] for row in cursor.fetchall())


def service_role_scope(circle, member, role_reference, basis):
    """Name an exact SERVICE role/version in the independent instrument's scope.

    The SERVICE instrument owns the reference. A reference supplied without a
    separate scoped finding cannot satisfy qualified_service_role.
    """
    if circle.parent_organism_id != member.living_organism_id:
        raise CircleAccessRefused(
            "This qualification needs membership in the Circle's organisation. Check the selected membership."
        )
    role = _canonical_key(role_reference, "service role version reference", 255)
    return scoped.Scope(circle.parent_organism.organism_id, circle.circle_uuid,
                        "CIRCLE_SERVICE:" + role, scoped.Type.EXECUTION_ELIGIBILITY_QUALIFICATION,
                        member.identity_id, member.identity_id, basis.basis_uuid)


def qualified_service_role(alias, circle, member, role_reference, basis, at, known):
    """Resolve a service-role finding for a live participant at this cut."""
    scope = service_role_scope(circle, member, role_reference, basis)
    with connections[alias].cursor() as cursor:
        cursor.execute(f"""
            WITH places AS ({LIVE_PLACES})
            SELECT EXISTS (SELECT FROM places WHERE circle_id=%(circle)s AND membership_id=%(member)s)
        """, {"lo": scope.organism, "at": at, "known": known, "circle": circle.pk, "member": member.pk})
        if not cursor.fetchone()[0]:
            raise CircleAccessRefused(
                "This role needs a qualifying place in the selected Circle. Check the admission record."
            )
    return scoped.qualified(alias, scope, at, known)


def people_in_places(alias, lo, circle_uuids, at, known):
    """Internal identity inputs for a common decision. Its owner still qualifies each voter."""
    with connections[alias].cursor() as cursor:
        cursor.execute(f"""
            WITH places AS ({LIVE_PLACES})
            SELECT DISTINCT i.identity_id FROM places JOIN core_identity i ON i.id=places.identity_id
            WHERE places.circle_uuid=ANY(%(circles)s::uuid[])
        """, {"lo": lo.organism_id, "circles": list(circle_uuids), "at": at, "known": known})
        return tuple(row[0] for row in cursor.fetchall())
