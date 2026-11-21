"""Member destinations in one organisation at one post-lock cut."""

from uuid import UUID

from django.db import transaction

from core.models import CircleBirthDescription, CircleStateEvent, OrganismMembership, OrganismRoleDefinition
from src.intevia.services import _record_names, discussion, discussion_posts
from src.intevia.services._office_family import office_family
from src.intevia.services.clock import database_now
from src.intevia.services.organism_command_support import _lock_actor, _lock_registered_organism


class NavigationUnavailable(PermissionError):
    def __init__(self, *args):
        super().__init__("This organisation is unavailable to you.")


def _probation_destinations(owner_id, reader_id, cut):
    """Return reachable Circle IDs and exact pinned offices, including superseded pins."""
    coordinator = "public.disc_office(b.organism_id,b.circle_id,%(reader)s,%(cut)s) IS NOT NULL"
    rows = discussion._rows(f"""
        WITH categories AS (
            SELECT b.organism_id, b.circle_id, c.category_uuid
              FROM public.core_discboard b
              JOIN public.disc_categories c ON c.board_uuid=b.board_uuid
             WHERE b.organism_id=%(owner)s AND b.circle_id IS NOT NULL
               AND public.disc_audience(b.organism_id,b.circle_id,%(reader)s,c.access_level,%(cut)s)
        )
        SELECT circle_id, NULL AS role_uuid FROM categories
        UNION
        SELECT b.circle_id, d.role_uuid
          FROM categories b
          JOIN public.core_discthread t ON t.category_uuid=b.category_uuid
          JOIN public.discp_posts opening ON opening.thread_uuid=t.thread_uuid AND opening.post_number=1
          JOIN public.core_organismroledefinition d ON d.role_uuid=t.office_definition_uuid
         WHERE d.living_organism_id=b.organism_id AND d.scope='LIVING_ORGANISM' AND d.kind='BOARD_OFFICE'
           AND d.received_at<=%(cut)s AND d.effective_at<=%(cut)s AND d.recorded_at<=%(cut)s
           AND opening.received_at<=%(cut)s AND opening.effective_at<=%(cut)s AND opening.recorded_at<=%(cut)s
           AND (public.disc_audience(b.organism_id,b.circle_id,%(reader)s,'MEMBERS',%(cut)s)
             OR public.disc_audience(b.organism_id,b.circle_id,%(reader)s,'PROBATIONARY',%(cut)s))
           AND {discussion_posts._title_visible_sql(coordinator)}
    """, {"owner": owner_id, "reader": reader_id, "cut": cut})
    pins_by_circle = {}
    for row in rows:
        pins = pins_by_circle.setdefault(row["circle_id"], set())
        if row["role_uuid"] is not None:
            pins.add(row["role_uuid"])
    return pins_by_circle


def _board_offices(owner_id, cut):
    roles = OrganismRoleDefinition.objects.filter(
        living_organism_id=owner_id, scope="LIVING_ORGANISM", kind="BOARD_OFFICE", active=True,
        effective_at__lte=cut, received_at__lte=cut, recorded_at__lte=cut,
    ).order_by("code", "role_uuid").values_list("role_uuid", "code")
    offices = []
    for definition, code in roles:
        family = office_family(owner_id=owner_id, code=code, requested=definition, at=cut)
        if family is not None:
            current, _, tenure = family
            row = {"office_definition_uuid": current, "label": _record_names.office_label(code),
                   "vacant": any(term["state"] == "vacant" for term in tenure)}
            ongoing = next((term for term in tenure if term["state"] == "ongoing"), {})
            if ongoing.get("holder_name"):
                row["holder_name"] = ongoing["holder_name"]
            offices.append(row)
    return offices


def _circle_description(birth, full_name, cut):
    """Project the introduction for this admitted Circle at the navigation cut."""
    if birth.circle.kind == "GOVERNANCE":
        return {"source": "board_interface", "text": (
            f"The {full_name} is the organisation's Governance Circle. "
            "Here you can find its offices and the records you can read."
        )}
    if birth.circle.kind == "GENERIC":
        purpose = CircleBirthDescription.objects.filter(
            circle_id=birth.circle_id, birth_event_id=birth.pk, effective_at__lte=cut, recorded_at__lte=cut,
        ).values_list("purpose", flat=True).first()
        if purpose and purpose.strip():
            return {"source": "birth_purpose", "text": purpose}
    return None


def _admitted_scope(credential, organism_uuid):
    if getattr(credential, "pk", None) is None or not isinstance(organism_uuid, UUID):
        raise NavigationUnavailable()
    actor = _lock_actor(database_alias="default", credential=credential, unavailable=NavigationUnavailable)
    # An anchor locates the lock. Only effective membership after the lock admits the read.
    owner_id = OrganismMembership.objects.filter(
        living_organism__organism_id=organism_uuid, identity=actor,
    ).values_list("living_organism_id", flat=True).first()
    if owner_id is None:
        raise NavigationUnavailable()
    _lock_registered_organism("default", organism_uuid)
    cut = database_now()
    state = discussion._rows("SELECT public.disc_member_state(%s,%s,%s) AS state",
                             [owner_id, actor.pk, cut])[0]["state"]
    if state not in ("ACTIVE", "SUSPENDED", "RESTRICTED", "PROBATIONARY"):
        raise NavigationUnavailable()

    return actor, owner_id, cut, state


def _organisation_name(organism_uuid, owner_id, cut):
    full, short = _record_names.full_name_and_short_label(owner_id=owner_id, cut=cut)
    return {"organism_uuid": organism_uuid, "full_name": full, "short_label": short}


def member_organisation_name(*, credential, organism_uuid):
    """Read just the admitted label for an organisation chip."""
    with transaction.atomic():
        _, owner_id, cut, _ = _admitted_scope(credential, organism_uuid)
        return _organisation_name(organism_uuid, owner_id, cut)


def member_navigation(*, credential, organism_uuid, circle_uuid=None):
    """Read member destinations and the optional selected Circle's introduction at one cut."""
    with transaction.atomic():
        actor, owner_id, cut, state = _admitted_scope(credential, organism_uuid)
        births = CircleStateEvent.objects.filter(
            circle__parent_organism_id=owner_id, sequence=1, received_at__lte=cut,
            effective_at__lte=cut, recorded_at__lte=cut,
        ).select_related("circle").only(
            "circle", "circle__circle_uuid", "circle__kind", "circle__parent_organism_id",
        ).order_by("circle__circle_uuid")
        if state == "PROBATIONARY":
            reachable = _probation_destinations(owner_id, actor.pk, cut)
            births = births.filter(circle_id__in=reachable.keys())

        if circle_uuid is not None and not any(b.circle.circle_uuid == circle_uuid for b in births):
            raise NavigationUnavailable()

        circles = []
        selected = None
        for birth in births:
            circle = birth.circle
            full, short = _record_names.full_name_and_short_label(owner_id=owner_id, cut=cut, circle=circle)
            destination = {"circle_uuid": circle.circle_uuid, "kind": circle.kind,
                           "full_name": full, "short_label": short}
            circles.append(destination)
            if circle.circle_uuid == circle_uuid:
                selected = {**destination, "description": _circle_description(birth, full, cut)}

        if state == "PROBATIONARY":
            pins = {pin for birth in births for pin in reachable[birth.circle_id]}
            offices = [{"office_definition_uuid": pin, "label": "Office record"} for pin in sorted(pins)]
        elif any(circle["kind"] == "GOVERNANCE" for circle in circles):
            offices = _board_offices(owner_id, cut)
        else:
            offices = []

        page = {"evaluated_at": cut,
                "organisation": _organisation_name(organism_uuid, owner_id, cut),
                "circles": circles, "board_offices": offices}
        if selected is not None:
            page["selected_circle"] = selected
        return page
