"""F0 placeholders and catalogue; never a source of product authority."""
import json
from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET
from core.cache import personal_response
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

@personal_response
@login_required(login_url="login")
@require_GET
def you(request):
    return render(request,"core/you.html")

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
