"""Interim display names for already admitted organisation and Circle reads."""

from django.db import connection
from django.db.transaction import TransactionManagementError

from core.models import CircleBirthDescription, LOFoundingContentVersion


_CIRCLE_NAMES = {
    "GOVERNANCE": ("Board of Trustees", "Board"),
    "APPLICATION": ("Applications", "Applications"),
    "FINANCE": ("Finance", "Finance"),
    "OPERATIONS": ("Operations", "Operations"),
}


def full_name_and_short_label(*, owner_id, cut, circle=None):
    """Use the enclosing read's admitted owner, optional Circle row and time cut.

    The caller admits the reader and selects the Circle before calling this
    helper. Only the caller's permitted payload may expose the resulting names.
    """
    if not connection.in_atomic_block:
        raise TransactionManagementError("Resolve display names inside the admitted read's transaction.")

    if circle is None:
        name = LOFoundingContentVersion.objects.filter(
            living_organism_id=owner_id,
            version=1,
            received_at__lte=cut,
            effective_at__lte=cut,
            recorded_at__lte=cut,
        ).values_list("name", flat=True).first()
        fallback = "This organisation"
    else:
        if circle.parent_organism_id != owner_id:
            raise ValueError("The Circle does not belong to the admitted organisation.")
        if circle.kind in _CIRCLE_NAMES:
            return _CIRCLE_NAMES[circle.kind]
        if circle.kind != "GENERIC":
            raise ValueError("Unrecognised Circle kind.")
        name = CircleBirthDescription.objects.filter(
            circle_id=circle.pk,
            circle__parent_organism_id=owner_id,
            effective_at__lte=cut,
            recorded_at__lte=cut,
        ).values_list("name", flat=True).first()
        fallback = "Circle"

    label = name if name and name.strip() else fallback
    return label, label


def office_label(code):
    """Render the admitted office's code for display."""
    return " ".join("INTEVIA" if word.upper() == "INTEVIA" else word.title() for word in code.split("_"))


def adopting_body_name(issuer, organisation, organisation_name, route, board_name):
    """Name the adopting body from the visible route and the page's admitted names."""
    if issuer != organisation:
        return "Inherited from INTEVIA"
    if route == "Board vote" and board_name:
        return f"{board_name} · {organisation_name}"
    return organisation_name
