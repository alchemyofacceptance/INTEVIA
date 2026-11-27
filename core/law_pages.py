"""Member law pages. Exact-version release precedes every display of law text."""

import re

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render
from django.views.decorators.http import require_safe

from core.cache import personal_response
from src.intevia.services import library_laws, member_navigation, policy_work
from src.intevia.services.office_record import adoption_disclosure
from src.intevia.services.policy_provenance import board_publication

# Match the two line-start locator forms in 0067, public.l_clauses.
_LOCATOR = r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*"
_MARKER = re.compile(rf"^[ \t]*(?:\[#({_LOCATOR})\]|§({_LOCATOR})(?=\s|$))[ \t]?", re.MULTILINE)


def law_text_parts(content):
    """Split presentation labels from adopted text, preserving lines and unmarked prose."""
    body = content.replace("\r\n", "\n").replace("\r", "\n")
    # A BOM is ignored for recognition only, just as on the first line of l_clauses.
    offset = 1 if body.startswith("\ufeff") else 0
    matches = list(_MARKER.finditer(body[offset:]))
    if not matches:
        return [(None, content)] if body[offset:].strip() else []
    parts = []
    prefix = body[:offset + matches[0].start()].removesuffix("\n")
    if prefix[offset:].strip():
        parts.append((None, prefix))
    for index, match in enumerate(matches):
        last = index + 1 == len(matches)
        end = len(body) if last else offset + matches[index + 1].start() - 1
        parts.append((match.group(1) or match.group(2), body[offset + match.end():end]))
    return parts


def law_display_parts(credential, org, content):
    """Prepare both law cards; render the payment clause only through its own reader grant."""
    parts = []
    for locator, words in law_text_parts(content):
        if locator == "payment-result":
            payment = policy_work.cited_payment(credential, org, words)
            parts.append((locator, payment))
        else:
            parts.append((locator, words))
    return parts


def split_policy_heading(content, title=None):
    """Separate the exact opening title line from the wording a reader may edit or see."""
    heading, body = library_laws.law_heading(content)
    if heading is None or (title is not None and heading != title):
        return "", content
    return content[:len(content) - len(body)], body


def policy_display_parts(credential, org, content, title=None):
    """Render the Policy body with the same clause and payment grants as the law page."""
    _, body = split_policy_heading(content, title)
    return law_display_parts(credential, org, body)


def _unavailable(request):
    return render(request, "core/organisation_unavailable.html", status=404)


@personal_response
@login_required(login_url="login")
@require_safe
def law_list(request, org):
    """Show released laws in the law engine's class order."""
    scope = dict(credential=request.user, organism_uuid=org)
    try:
        with transaction.atomic():
            release = library_laws.our_laws(**scope)
            rows = library_laws.law_display_rows(org, release["laws"])
            groups = library_laws.law_class_groups(rows)
            owner = member_navigation.member_organisation_name(**scope)
    except (library_laws.LawReadUnavailable, member_navigation.NavigationUnavailable):
        return _unavailable(request)
    return render(request, "core/organisation_laws.html", {
        "organisation": owner, "groups": groups, "catalogue_domains": True,
    })


@personal_response
@login_required(login_url="login")
@require_safe
def law_version(request, org, binding, version_number):
    """Show the one adoption this address names, with the same private refusal as the home."""
    scope = dict(credential=request.user, organism_uuid=org)
    try:
        with transaction.atomic():
            page = library_laws.our_law_version_page(
                **scope, binding=binding, version_number=version_number)
            owner = member_navigation.member_organisation_name(**scope)
            row = page["law"]
            access = policy_work.revision_access(request.user, org, row, page["status"])
            mark = page["display"]
            heading, remaining = library_laws.law_heading(row["content"])
            content = remaining if heading == mark["title"] else row["content"]
            parts = law_display_parts(request.user, row["issuer"], content)
            adoption = adoption_disclosure(request.user, org, row, board_name_allowed=True,
                                           end_at=page["end_at"])
            publication = board_publication(request.user, org, row)
    except (library_laws.LawReadUnavailable, member_navigation.NavigationUnavailable):
        return _unavailable(request)
    class_label = mark["display_class"] or mark["class_name"]
    named = mark["title"] != class_label or (heading == class_label and heading != mark["reference"])
    page_title = f'{class_label}: {mark["title"]}' if named else class_label
    return render(request, "core/organisation_law_version.html", {
        "can_revise": bool(access and access["authorised"] and access["current"]),
        "organisation": owner, "catalogue_domains": True, "mark": mark, "earlier": page["earlier"],
        "title": page_title, "version": row["version_number"],
        "is_current": page["status"] == "In force now", "status": page["status"],
        "current_version": page["current_version"], "adoption": adoption, "publication": publication,
        "parts": parts,
    })
