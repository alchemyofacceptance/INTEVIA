"""Member destinations in one organisation at one post-lock cut."""

from uuid import UUID

from django.db import transaction

from core.models import CircleBirthDescription, CircleStateEvent, OrganismMembership, OrganismRoleDefinition
from src.intevia.services import _record_names, discussion, discussion_posts
from src.intevia.services._office_family import office_family, office_family_head
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


def _linked_office(offices, owner_id, cut, pin):
    """Select only from released destinations, retaining a directly admitted historical pin."""
    by_definition = {row["office_definition_uuid"]: row for row in offices}
    if pin in by_definition:
        return by_definition[pin]
    if not offices:
        return None
    code = OrganismRoleDefinition.objects.filter(living_organism_id=owner_id, role_uuid=pin,
                                                kind="BOARD_OFFICE", scope="LIVING_ORGANISM").values_list("code", flat=True).first()
    if code is None:
        return None
    head = office_family_head(owner_id=owner_id, code=code, requested=pin, at=cut)
    return by_definition.get(head["role_uuid"]) if head else None


def _board_topic_count(credential, organism_uuid, circle_uuid):
    """Count the Topics this reader may open in the Board's discussion (HG capture AG of 24 Nov 2026, AG1).

    The same reads as the Board discussion list: the categories this reader's audience admits, then each
    category's Topics, counting only those whose title this reader may see. A count only; no title, name
    or text leaves this function. Raises DiscussionUnavailable when the reader may not see the discussion.
    """
    scope = {"credential": credential, "organism_uuid": organism_uuid, "circle_uuid": circle_uuid}
    categories = discussion.board_categories(**scope, governance_only=True)["categories"]
    return sum(1 for category in categories
               for row in discussion_posts.category_topics(**scope, category_uuid=category["category_uuid"])
               if row["title"] is not None)


def member_navigation(*, credential, organism_uuid, circle_uuid=None, include_discussion=False, office_pin=None,
                      include_offices=True, include_settings_link=False):
    """Read member destinations and the optional selected Circle's introduction at one cut.

    include_offices=False skips the Board's office list for a page that shows none (box 9 Advisor, part 2,
    finding 1); a pinned office still reads it, so the pin is validated as before. The home
    requests settings-link visibility from this admission cut without reading CARE.
    """
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

        if not include_offices and office_pin is None:
            offices = []
        elif state == "PROBATIONARY":
            pins = {pin for birth in births for pin in reachable[birth.circle_id]}
            offices = [{"office_definition_uuid": pin, "label": "Office record"} for pin in sorted(pins)]
        elif any(circle["kind"] == "GOVERNANCE" for circle in circles):
            offices = _board_offices(owner_id, cut)
        else:
            offices = []

        page = {"evaluated_at": cut,
                "organisation": _organisation_name(organism_uuid, owner_id, cut),
                "circles": circles, "board_offices": offices}
        if include_settings_link:
            page["show_settings"] = state != "PROBATIONARY"
        if office_pin is not None:
            page["linked_office"] = _linked_office(offices, owner_id, cut, office_pin)
        if selected is not None:
            page["selected_circle"] = selected
            if include_discussion and selected["kind"] == "GOVERNANCE":
                try:
                    destinations = discussion_posts.visible_boards(credential=credential)
                except discussion.DiscussionUnavailable:
                    destinations = ()
                page["discussion_available"] = any(
                    row["organism_uuid"] == organism_uuid and row["circle_uuid"] == circle_uuid
                    for row in destinations
                )
                if page["discussion_available"]:
                    try:
                        page["discussion_topic_count"] = _board_topic_count(credential, organism_uuid, circle_uuid)
                    except discussion.DiscussionUnavailable:
                        # The discussion closed to this reader between the two reads: no row and no count.
                        page["discussion_available"] = False
        return page
