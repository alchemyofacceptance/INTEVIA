"""Review and confirm one published local Policy on the author's Suggestion."""

from uuid import UUID, uuid5

from django.core import signing
from django.shortcuts import render
from django.urls import reverse

from core import discussion_forms as forms
from core.law_pages import policy_display_parts
from src.intevia.services import cq, discussion, policy_adoption, policy_publication


def current_policy_url(org, reference):
    """Return the governing version, or the organisation's law list if the Policy has ended."""
    current = policy_adoption.current(org, reference)
    if current:
        return reverse("organisation_law_version", args=(org, current["binding"], current["version_number"]))
    return reverse("organisation_laws", args=(org,))


def submit(request, context, scope, data, panel):
    """Use only signed coordinates. Build the proposal on review and submit exactly that proposal."""
    review = data["confirmation"]
    org, circle, thread = scope["organism_uuid"], scope["circle_uuid"], scope["thread_uuid"]
    reference = review["resource"]
    entry = next((row for row in panel.get("adoptions", ()) if row["reference"] == reference
                  and row["version_number"] == review["number"] and str(row["binding"]) == review["predecessor"]), None)
    if entry is None:
        context["current_policy_url"] = current_policy_url(org, reference)
        raise discussion.DiscussionRefused(code="adoption_changed")
    context["current_policy_url"] = reverse("organisation_law_version", args=(org, entry["binding"], entry["previous_number"]))
    text = policy_adoption.wording(org, reference, review["number"])
    if review["step"] == "review":
        # Reopening this review keeps its proposal. Separate signed choices have separate request IDs.
        review = dict(review, proposal=str(uuid5(UUID(data["request"]), "adoption")))
    instrument = policy_adoption.attachment(org, review, text)
    fingerprint = policy_publication.fingerprint(instrument)
    if review["step"] == "confirm":
        if fingerprint != review["fingerprint"]:
            raise discussion.DiscussionRefused(code="adoption_changed")
        context["policy_vote_writing"] = True
        try:
            cq.put_forward(**scope, version_uuid=panel["opening_version"], attachment=instrument)
        except discussion.DiscussionRefused as refusal:
            if refusal.code == "CQ_ATTACHMENT":
                context["current_policy_url"] = current_policy_url(org, reference)
            raise
        return None
    token = signing.dumps(dict(data, confirmation=dict(review, step="confirm", fingerprint=fingerprint)),
                          salt=forms.FORM_SALT)
    return render(request, "core/adoption_vote_review.html", dict(
        context, command=token, policy=entry, motion=panel["motion"],
        parts=policy_display_parts(request.user, org, text, title=entry["title"]),
        back=reverse("discussion_topic", args=(org, circle, thread)) + "#vote"))
