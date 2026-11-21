"""F0 placeholders and catalogue; never a source of product authority."""
import json
import re
from pathlib import Path
from uuid import uuid4
from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods
from core.cache import personal_response
from core.models import Identity
from core.ui import DOMAINS, participation

PAGES = {
    "explore": ("Explore", "Explore the spaces open to you", "Organisation browsing arrives with peek 1; the wider domains arrive with peek 6."),
    "organisation": ("INTEVIA", "INTEVIA", "Organisation screens arrive with peek 1. Public domain journeys arrive with their later website slices."),
    "history": ("History", "Your record history", "The combined history view arrives in a later website slice. For now, open an Event to read the registration or attendance history available to you. Visitor access to public-marked records arrives with the privacy slice; this page grants no new access."),
}

@personal_response
@require_GET
def placeholder(request, section):
    if section not in PAGES:
        raise Http404
    title, heading, message = PAGES[section]
    return render(request,"core/ui_placeholder.html",{"title":title,"heading":heading,"message":message,"section":section})

def is_prefetch(request):
    # Either purpose header can mark a speculative request, including prefetch;prerender.
    return any(
        "prefetch" in re.split(r"[;,\s]+", request.headers.get(header, "").casefold())
        for header in ("Sec-Purpose", "Purpose")
    )


@personal_response
@login_required(login_url="login")
@require_http_methods(["GET", "HEAD"])
def office_page(request, org, office):
    """Render one office read for the credential bound by the session middleware."""
    from src.intevia.services.care_assistance import SettingRefused
    from src.intevia.services.office_record import office_record

    prefetch = is_prefetch(request)
    try:
        page = office_record(credential=request.user, organism_uuid=org, office_definition_uuid=office,
                             present_care=request.method == "GET" and not prefetch)
    except SettingRefused as exc:
        if exc.code != "CARE_SETTING_UNAVAILABLE":
            raise
        return render(request, "core/office_unavailable.html", status=404)
    setting = page.get("care", {})
    forms = []
    if setting.get("can_change"):
        if setting["enabled"]:
            forms.append(("off", "Turn assistance off", uuid4(), "text-command"))
        else:
            forms.append(("on", "Turn assistance on", uuid4(), "primary"))
    organisation_url = board_url = start_topic_url = office_board = None
    facts = page["office"]
    if facts.get("organisation_short_label"):
        from src.intevia.services import member_navigation
        try:
            destinations = member_navigation.member_navigation(credential=request.user, organism_uuid=org)
        except member_navigation.NavigationUnavailable:
            pass  # A readable office does not itself grant a directory destination.
        else:
            organisation_url = reverse("organisation_home", args=(destinations["organisation"]["organism_uuid"],))
            board = next((c for c in destinations["circles"] if c["kind"] == "GOVERNANCE"), None)
            if board:
                office_board = board["circle_uuid"]
                if facts.get("board_short_label"):
                    board_url = reverse("circle_page", args=(org, office_board))
                pin = facts.get("current_definition") or facts.get("requested_definition")
                if pin and any(row["office_definition_uuid"] == pin for row in destinations["board_offices"]):
                    from core.discussion_forms import office_start_url
                    start_topic_url = office_start_url(request.user, org, office_board, pin)
    return render(request, "core/office_page.html", {
        "office_org": org, "office_board": office_board, "start_topic_url": start_topic_url,
        "organisation_url": organisation_url, "board_url": board_url,
        "record": page, "switch_forms": forms,
        "switch_url": reverse("office_switch", args=(org, office)) if forms else None,
    })


@personal_response
@login_required(login_url="login")
@require_http_methods(["GET", "POST"])
def you(request):
    context = {"colour_choices": Identity.ColourMode.choices}
    if request.method == "POST":
        submitted = request.POST.getlist("colour_mode")
        if len(submitted) != 1 or submitted[0] not in Identity.ColourMode.values:
            context["appearance_error"] = "Choose one of the three colour modes."
            return render(request, "core/you.html", context, status=400)

        identity = request.intevia_identity
        saved = Identity.objects.filter(
            pk=identity.pk, credential=request.user, access_epoch=identity.access_epoch,
            access_state=Identity.AccessState.ACTIVE,
        ).update(colour_mode=submitted[0])
        if not saved:
            context["appearance_error"] = "Your session has changed. Sign in again to save your appearance."
            return render(request, "core/you.html", context, status=403)
        return redirect("f0-you")
    return render(request, "core/you.html", context)

@personal_response
@require_GET
def domain(request, scope, domain):
    if scope not in {"you","organisation"} or domain not in DOMAINS:
        raise Http404
    if scope == "you" and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), login_url=reverse("login"))
    if scope == "organisation" and domain == "events":
        return redirect("event-list")
    return render(request,"core/ui_placeholder.html",{
        "title":DOMAINS[domain],"heading":DOMAINS[domain],
        "message":"This view arrives with its domain website slice (peek 6). F0 does not show or create domain records here.",
        "section":scope,"domain_placeholder":True,
    })

class CatalogueForm(forms.Form):
    display_name = forms.CharField(label="Your name",help_text="A fictional example; nothing is saved.")

@personal_response
@require_GET
def catalogue(request):
    if not settings.INTEVIA_DESIGN_CATALOGUE:
        raise Http404
    clean=CatalogueForm(initial={"display_name":"Alex Morgan"},prefix="clean")
    invalid=CatalogueForm({"invalid-display_name":"Alex Morgan"},prefix="invalid")
    invalid.is_valid();invalid.add_error("display_name","Example refusal: choose a different display name. Your entry is kept.")
    members=[{"name":name,"role":"Member","answer":state} for name,state in (
        ("Alexandra Morgan-Sinclair","consented"),("Amina Mokoena","declined"),("Wei-Lin Chen","abstained"),
        ("Santiago Fernández de la Cruz","pending"),("Harper O’Neill","consented"),("Lena Svensson","pending"),
        ("Oliver Adeyemi","consented"),("Nora van der Meer","pending"),("Elliot Patel","consented"),
        ("Rafael Costa","pending"))]
    reference=json.loads((Path(__file__).parent/"static/core/theme-reference.json").read_text())
    return render(request,"core/catalogue.html",{
        "clean_form":clean,"invalid_form":invalid,"variants":["primary","secondary","success","danger"],
        "votes":[participation(members[:9]),participation(members),participation(counts={"consented":42,"declined":18,"abstained":10,"pending":50},anonymous=True),participation([])],
        "reference":reference, "catalogue_domains": True, "refusal_preview": request.GET.get("example") == "refusal",
    })
