"""HTTP adapter for the Organisation Coordinator's existing CARE setting command."""
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.csrf import csrf_failure as django_csrf_failure

from core.cache import personal_response
from core.ui_views import is_prefetch, you


# What happened, why, effect, next action, and who can help.
_REFUSALS = {
    "CARE_SETTING_AUTHORITY": (
        403, "Your choice was refused.", "Only the Organisation Coordinator can change this setting.",
        "This request left the setting unchanged.", "Return to the office record to see the current choice.",
        "Ask the Organisation Coordinator if a change is needed.",
    ),
    "CARE_SETTING_INPUT": (
        400, "Your choice could not be recorded.", "The form needs a valid choice and its original references.",
        "This request left the setting unchanged.", "Reopen the office record and choose On or Off.",
        "If a fresh form fails, contact support.",
    ),
    "CARE_SETTING_PREDECESSOR": (
        409, "Your choice could not be recorded.", "The organisation made another choice after you opened the form.",
        "This request left that choice in place.", "Reload the current choice and decide again.",
        "The Organisation Coordinator can submit a fresh choice.",
    ),
    "CARE_SETTING_REPLAY": (
        409, "Your choice could not be recorded.", "This form reference has already been used for a different action.",
        "This request left the setting unchanged.", "Reopen the form before trying again.",
        "Contact support if a fresh form has the same problem.",
    ),
    "CARE_SETTING_UNAVAILABLE": (
        404, "This page is unavailable.", "This link does not open a record in your current view.",
        "This request made no change.", "Open your organisation and choose a record from there.",
        "Ask your Organisation Coordinator about access.",
    ),
    "csrf": (
        403, "The form could not be submitted.", "Its security check did not pass.",
        "This request saved nothing.", "Reopen the page and submit a fresh form.",
        "Contact support if the new form is refused.",
    ),
    "method": (
        405, "This request could not be used.", "This address accepts a different kind of request.",
        "This request saved nothing.", "Open the page and use its links or form.",
        "Contact support if a link keeps bringing you here.",
    ),
    "speculative": (
        400, "This request could not be used.", "A preview cannot submit a choice.",
        "This request saved nothing.", "Open the office record and choose On or Off.",
        "Contact support if a fresh form is refused.",
    ),
    "unknown": (
        503, "Your change could not be confirmed.", "The service could not return the result.",
        "The current choice must be checked.", "Reopen the page and check the current choice before submitting again.",
        "Contact support if the outcome is still unclear.",
    ),
}


def _refusal(request, code, next_url=None, choice=None):
    status, title, why, effect, action, help_text = _REFUSALS[code]
    return render(request, "core/office_switch_refusal.html", {
        "title": title, "why": why, "effect": effect, "action": action, "help_text": help_text, "next_url": next_url,
        "choice": choice,
    }, status=status)


def csrf_failure(request, reason=""):
    match = request.resolver_match
    # These two personal forms use the five-part refusal. Cache policy belongs to the middleware.
    if match and match.url_name in ("discussion_start", "discussion_reply"):
        from core.discussion_forms import refusal
        return refusal(request, "csrf")
    if match and match.func in (you, change_assistance):
        return _refusal(request, "csrf")
    return django_csrf_failure(request, reason=reason)


@personal_response
@login_required(login_url="login")
def change_assistance(request, org, office):
    from src.intevia.services import care_assistance as care

    office_url = reverse("office_page", args=(org, office))
    if request.method != "POST":
        response = _refusal(request, "method", office_url)
        response["Allow"] = "POST"
        return response
    if is_prefetch(request):
        return _refusal(request, "speculative", office_url)
    retained = None
    try:
        # Bind the submitted office to the caller's permitted record. The command itself
        # decides authority and replay using the session credential and submitted predecessor.
        setting = care.read_assistance(credential=request.user, organism_uuid=org, office_definition_uuid=office)
        try:
            choice, = request.POST.getlist("enabled")
            if choice not in ("on", "off"):
                raise ValueError
            if setting["can_change"]:
                retained = choice.title()
            prior, = request.POST.getlist("predecessor")
            key, = request.POST.getlist("request_uuid")
            predecessor = UUID(prior) if prior else None
            request_uuid = UUID(key)
        except ValueError:
            raise care.SettingRefused("CARE_SETTING_INPUT") from None
        receipt = care.set_assistance(credential=request.user, organism_uuid=org, enabled=choice == "on",
                                      predecessor=predecessor, request_uuid=request_uuid)
    except care.SettingRefused as exc:
        next_url = reverse("f0-organisation") if exc.code == "CARE_SETTING_UNAVAILABLE" else office_url
        return _refusal(request, exc.code, next_url, retained if exc.code in (
            "CARE_SETTING_INPUT", "CARE_SETTING_PREDECESSOR", "CARE_SETTING_REPLAY"
        ) else None)
    except DatabaseError:
        return _refusal(request, "unknown", office_url)

    if receipt["status"] in ("already on", "already off"):
        messages.info(request, f'Assistance is {receipt["status"]}.')
    return redirect(office_url)
