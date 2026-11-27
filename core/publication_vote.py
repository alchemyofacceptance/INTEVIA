"""Signed publication and adoption choices on the Board author’s Suggestion."""

from uuid import UUID

from django.core import signing
from django.shortcuts import render
from django.urls import reverse

from core import adoption_vote, discussion_forms as forms
from core.law_pages import policy_display_parts
from src.intevia.services import cq, discussion, policy_publication as publication


def review_token(request, org, circle, thread, opening, reference, number):
    """Sign the public coordinates of one Policy for a read-only review on this Topic."""
    return forms.issue_form(request, "INITIATE", org, circle, thread=thread, version=opening,
                            confirmation=dict(intent="publish", step="review", resource=reference, number=number))


def sign_choices(request, vote, org, circle, thread):
    """Issue an independent signed intent for each admitted publication or adoption."""
    for row in vote.get("publications", ()):
        row["token"] = review_token(request, org, circle, thread, vote["opening_version"],
                                    row["reference"], row["version_number"])

    for row in vote.get("adoptions", ()):
        row["token"] = forms.issue_form(
            request, "INITIATE", org, circle, thread=thread, version=vote["opening_version"],
            confirmation=dict(intent="adopt", step="review", resource=row["reference"],
                              number=row["version_number"], predecessor=str(row["binding"])))


def submit(request, context, scope, actor, data):
    """Review without writing, or submit the exact instrument confirmed in this session.

    The caller holds the identity, request and organisation locks across this read,
    writer call and commit. A replay takes its exact instrument to the same writer.
    """
    review = data["confirmation"]
    if review.get("intent") not in ("publish", "adopt") or review.get("step") not in ("review", "confirm"):
        raise discussion.DiscussionRefused(code="form")
    confirm = review["step"] == "confirm"
    if confirm:
        recorded = publication.recorded_attachment(scope, actor, data["version"], review)
        if recorded is not None:
            context["policy_vote_writing"] = True
            cq.put_forward(**scope, version_uuid=UUID(data["version"]), attachment=recorded)
            return None
    org, circle, thread = scope["organism_uuid"], scope["circle_uuid"], scope["thread_uuid"]
    panel = cq.board_topic_vote(credential=request.user, organism_uuid=org, circle_uuid=circle,
                                thread_uuid=thread, include_publications=True)
    if context["topic"].get("closed"):
        raise discussion.DiscussionRefused(code="DISC_CLOSED")
    if panel and panel.get("phase"):
        raise discussion.DiscussionRefused(code="CQ_EXISTS")
    if not panel or "publications" not in panel:
        raise discussion.DiscussionRefused(code="publication_authority")
    if str(panel["opening_version"]) != data["version"]:
        raise discussion.DiscussionRefused(code="CQ_INIT_VERSION")
    if review["intent"] == "adopt":
        return adoption_vote.submit(request, context, scope, data, panel)
    reference, number = review["resource"], review["number"]
    entry = next((row for row in panel["publications"] if row["reference"] == reference), None)
    offered = entry is not None and entry["version_number"] == number
    version = publication.exact(org, reference, number, offered=offered)
    if version["current"] and not version["declared"]:
        raise discussion.DiscussionRefused(code="publication_claims")
    if not offered or not version["current"]:
        if not confirm:
            raise discussion.DiscussionRefused(code="publication_old")
        # Start a fresh review, even if the old version no longer has an eligible
        # replacement. In that case the review explains its status and offers the Topic.
        next_number = entry["version_number"] if entry else number
        context["review_again"] = review_token(request, org, circle, thread, panel["opening_version"],
                                              reference, next_number)
        raise discussion.DiscussionRefused(code="publication_changed")
    instrument = publication.attachment(org, reference, number, version["wording"])
    fingerprint = publication.fingerprint(instrument)
    if confirm and fingerprint != review["fingerprint"]:
        context["review_again"] = review_token(request, org, circle, thread, panel["opening_version"], reference, number)
        raise discussion.DiscussionRefused(code="publication_changed")
    if confirm:
        context["publication_review_token"] = review_token(request, org, circle, thread,
                                                         panel["opening_version"], reference, number)
        context["policy_vote_writing"] = True
        cq.put_forward(**scope, version_uuid=panel["opening_version"], attachment=instrument)
        return None
    # Preserve the signed proposal key across refreshes and duplicate confirmations.
    token = signing.dumps(dict(data, confirmation=dict(review, step="confirm", fingerprint=fingerprint)),
                          salt=forms.FORM_SALT)
    return render(request, "core/publication_vote_review.html", dict(
        context, command=token, title=entry["title"], reference=reference, number=number,
        motion=panel["motion"], parts=policy_display_parts(request.user, org, version["wording"], title=entry["title"]),
        back=reverse("discussion_topic", args=(org, circle, thread)) + "#vote"))
