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
        "This request left the setting unchanged.", "Return to organisation settings to see the current choice.",
        "Ask the Organisation Coordinator if a change is needed.",
    ),
    "CARE_SETTING_INPUT": (
        400, "Your choice could not be recorded.", "The form needs a valid choice and its original references.",
        "This request left the setting unchanged.", "Reopen organisation settings and choose On or Off.",
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
    "read_failed": (
        503, "Organisation settings could not be shown.", "The setting could not be read.",
        "This page offers no change.", "Try opening organisation settings again.", "If this continues, contact support.",
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
        "This request saved nothing.", "Open organisation settings and choose On or Off.",
        "Contact support if a fresh form is refused.",
    ),
    "unknown": (
        503, "Your change could not be confirmed.", "The service could not return the result.",
        "The current choice must be checked.", "Reopen organisation settings and check the current choice before submitting again.",
        "Contact support if the outcome is still unclear.",
    ),
}


def _refusal(request, code, next_url=None, choice=None):
    status, title, why, effect, action, help_text = _REFUSALS[code]
    return render(request, "core/office_switch_refusal.html", {
        "title": title, "why": why, "effect": effect, "action": action, "help_text": help_text, "next_url": next_url,
        "choice": choice,
    }, status=status)


def failed_settings_read(request):
    match = request.resolver_match
    org = match.kwargs.get("org") if match else None
    next_url = reverse("organisation_settings", args=(org,)) if org is not None else None
    return _refusal(request, "read_failed", next_url=next_url)


@login_required(login_url="login")
def settings_read_method_refusal(request):
    """Refuse an unsupported method on the settings read before opening any record."""
    response = _refusal(request, "method")
    response["Allow"] = "GET, HEAD"
    return response


def csrf_failure(request, reason=""):
    from core.organisation_pages import organisation_settings

    match = request.resolver_match
    # An unsafe method on the settings read has no form or token to validate.
    if match and match.func is organisation_settings and request.method not in ("GET", "HEAD"):
        return settings_read_method_refusal(request)
    # Personal form failures use the five-part refusal. Cache policy belongs to the middleware.
    if match and (match.url_name in ("discussion_start", "discussion_reply", "discussion_put_forward",
                                     "discussion_edit", "discussion_prepare", "discussion_confirm_clarity",
                                     "discussion_answer", "discussion_record_result")
                  or (match.url_name or "").startswith("discussion_clarity_")):
        from core.discussion_forms import refusal
        return refusal(request, "csrf")
    if match and match.func in (you, change_assistance):
        return _refusal(request, "csrf")
    return django_csrf_failure(request, reason=reason)


@personal_response
@login_required(login_url="login")
def change_assistance(request, org):
    from src.intevia.services import care_assistance as care

    settings_url = reverse("organisation_settings", args=(org,))
    if request.method != "POST":
        response = _refusal(request, "method", settings_url)
        response["Allow"] = "POST"
        return response
    if is_prefetch(request):
        return _refusal(request, "speculative", settings_url)
    retained = None
    try:
        # The admission read finishes its own transaction before the durable command.
        try:
            setting = care.read_organisation_assistance(credential=request.user, organism_uuid=org)
        except DatabaseError:
            return failed_settings_read(request)
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
        if exc.code == "CARE_SETTING_UNAVAILABLE":
            return render(request, "core/organisation_unavailable.html", status=404)
        return _refusal(request, exc.code, settings_url, retained if exc.code in (
            "CARE_SETTING_INPUT", "CARE_SETTING_PREDECESSOR", "CARE_SETTING_REPLAY"
        ) else None)
    except DatabaseError:
        return _refusal(request, "unknown", settings_url)

    if receipt["status"] in ("already on", "already off"):
        messages.info(request, f'Assistance is {receipt["status"]}.')
    return redirect(settings_url)
