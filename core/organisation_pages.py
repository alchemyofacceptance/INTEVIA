"""Organisation selection and member Circle pages."""
from uuid import uuid4

from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.cache import personal_response
from core.office_switch import failed_settings_read, settings_read_method_refusal
from core.ui_views import PAGES
from src.intevia.services import care_assistance, circle_commands, circle_participation, member_navigation as navigation
from src.intevia.services.organism_membership_service import OrganismMembershipReader


def _memberships(request):
    rows = OrganismMembershipReader().list_own_memberships(request.intevia_identity)
    return [row.organism_id for row in rows]


def _chips(credential, memberships, selected=None):
    result = []
    for org in memberships:
        if selected and org == selected["organism_uuid"]:
            result.append(selected)
            continue
        try:
            result.append(navigation.member_organisation_name(credential=credential, organism_uuid=org))
        except navigation.NavigationUnavailable:
            # Membership can end between the list and the independent label read.
            continue
    return result


@personal_response
@require_http_methods(["GET", "HEAD"])
def entry(request):
    memberships = _memberships(request) if request.user.is_authenticated else []
    if len(memberships) == 1:
        return redirect("organisation_home", org=memberships[0])
    chips = _chips(request.user, memberships)
    if len(chips) == 1:
        return redirect("organisation_home", org=chips[0]["organism_uuid"])
    if chips:
        return render(request, "core/organisation_chooser.html", {"organisation_chips": chips})
    title, heading, message = PAGES["organisation"]
    return render(request, "core/ui_placeholder.html", {
        "section": "organisation", "title": title, "heading": heading, "message": message,
    })


def _status(credential, org, circle):
    if circle["kind"] != "GENERIC":
        return None
    try:
        detail = circle_commands.operating_status_detail(
            credential=credential, organism_uuid=org, circle_uuid=circle["circle_uuid"], participants_only=True,
        )
    except circle_participation.CircleAccessRefused:
        return None
    return {"state": detail["state"].replace("_", " ").capitalize(), "deadline": detail["deadline"]}


def _excerpt(text):
    if len(text) <= 120:
        return ""
    # Reserve one character for the ellipsis. Preserve a complete last word when possible.
    prefix = text[:119]
    if not text[119].isspace() and not prefix[-1].isspace():
        words = prefix.rsplit(None, 1)
        if len(words) == 2:
            prefix = words[0]
    return prefix.rstrip() + "…"


def _member_page(request, org, circle=None):
    memberships = _memberships(request)
    if org not in memberships:
        return render(request, "core/organisation_unavailable.html", status=404)
    try:
        # Keep this owner's lock across the directory, participation and status reads.
        with transaction.atomic():
            page = navigation.member_navigation(credential=request.user, organism_uuid=org, circle_uuid=circle,
                                                include_discussion=circle is not None, include_settings_link=circle is None)
            selected = page.get("selected_circle")
            status = _status(request.user, org, selected) if selected else None
    except navigation.NavigationUnavailable:
        return render(request, "core/organisation_unavailable.html", status=404)

    owner = page["organisation"]
    context = {"organisation": owner, "organisation_chips": _chips(request.user, memberships, owner)}
    if circle is None:
        context.update(circles=page["circles"], show_settings=page.get("show_settings", False))
        return render(request, "core/organisation_home.html", context)
    if selected["kind"] == "GOVERNANCE":
        context["board_offices"] = page["board_offices"]
        context["discussion_available"] = page.get("discussion_available", False)
        context["discussion_topic_count"] = page.get("discussion_topic_count")
    description = selected["description"]
    text = description["text"] if description else ""
    excerpt = _excerpt(text) if description and description["source"] == "birth_purpose" else ""
    context.update(circle=selected, status=status, description=text, excerpt=excerpt)
    return render(request, "core/circle_page.html", context)


@personal_response
@login_required(login_url="login")
@require_http_methods(["GET", "HEAD"])
def home(request, org):
    return _member_page(request, org)


@personal_response
@login_required(login_url="login")
def organisation_settings(request, org):
    """Show the CARE choice and its released attribution to an admitted member."""
    if request.method not in ("GET", "HEAD"):
        return settings_read_method_refusal(request)

    scope = {"credential": request.user, "organism_uuid": org}
    try:
        with transaction.atomic():
            setting = care_assistance.read_organisation_assistance(**scope)
            owner = navigation.member_organisation_name(**scope)
    except (care_assistance.SettingRefused, navigation.NavigationUnavailable):
        return render(request, "core/organisation_unavailable.html", status=404)
    except DatabaseError:
        return failed_settings_read(request)
    context = {"organisation": owner, "setting": setting}
    if setting["can_change"]:
        context["request_uuid"] = uuid4()
    return render(request, "core/organisation_settings.html", context)


@personal_response
@login_required(login_url="login")
@require_http_methods(["GET", "HEAD"])
def circle_page(request, org, circle):
    return _member_page(request, org, circle)
