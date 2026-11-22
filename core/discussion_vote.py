"""Two plain Board vote commands on an admitted Suggestion."""
from datetime import timezone
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.formats import date_format

from core.cache import personal_response
from core import discussion_forms as forms
from core.ui_views import is_prefetch
from src.intevia.services import cq, cq_reads, discussion


def _utc(value):
    return date_format(value.astimezone(timezone.utc), "j F Y, H:i") + " UTC"


def _vote_card(vote):
    """Read the stage once for the status line, wording heading and edit note."""
    phase = vote["phase"]
    deadline = vote.get("deadline")
    read_at = vote.get("read_at")
    response = vote.get("response_deadline")
    deadline_passed = deadline is not None and read_at is not None and read_at >= deadline
    draft = "Wording to be decided"
    if phase in ("PREPARATION", "PREPARATION_DUE"):
        if phase == "PREPARATION_DUE" or deadline_passed:
            line = "Preparation deadline passed" + (" · " + _utc(deadline) if deadline else "")
        else:
            line = "Preparation" + (" · Deadline " + _utc(deadline) if deadline else "")
        return line, draft, phase == "PREPARATION"
    if phase == "CLARITY":
        if response and read_at and read_at >= response:
            return "Clarity response period ended", draft, False
        if deadline_passed:
            line = "Clarity period ended" + (" · Respond by " + _utc(response) if response else "")
            return line, draft, False
        line = "Clarity" + (" · Confirm by " + _utc(deadline) if deadline else "")
        return line, draft, deadline is not None and read_at is not None
    if phase == "VOTING":
        if deadline_passed:
            return "Voting closed · Awaiting result", "Wording voted on", False
        line = "Voting open" + (" · Closes " + _utc(deadline) if deadline else "")
        return line, "Wording being voted on", False
    if phase in ("RESULT", "CLOSED", "WITHDRAWN", "NO_BALLOT"):
        kind = vote.get("outcome")
        if phase == "WITHDRAWN":
            status = "Suggestion withdrawn"
        elif phase == "CLOSED" and kind != "NO_DECISION_BOARD_ACTIVATED":
            status = "Suggestion closed"
        elif phase == "NO_BALLOT" or vote.get("head_operation") == "NO_BALLOT":
            status = "No vote opened"
        elif kind in ("QUALIFIED", "NOT_QUALIFIED_VOTE", "NOT_QUALIFIED_TIE"):
            status = "Decided"
        else:
            status = "Vote ended"
        outcome = None if status == "No vote opened" else cq_reads.RESULT_LABELS.get(kind)
        outcome = outcome or cq_reads.NO_BALLOT_LABELS.get(kind)
        if kind == "NO_DECISION_BOARD_ACTIVATED":
            outcome = "The Board became active. No decision was reached."
        if outcome and ". " not in outcome:
            outcome = outcome.removesuffix(".")
        date = vote.get("effective_at")
        line = " · ".join(part for part in (status, _utc(date) if date else None, outcome) if part)
        heading = "Wording at closure"
        if vote.get("ballot_uuid"):
            heading = "Wording decided" if status == "Decided" else "Wording voted on"
        return line, heading, False
    line = {
        "CLARITY_GATE": "Clarity confirmations required",
        "RESPONSE_DUE": "Clarity response period ended",
        "VOTES_CAST": "Votes cast",
        "AWAITING_SETTLEMENT": "Voting closed · Awaiting result",
    }.get(phase, "Vote status unavailable")
    return line, "Wording voted on" if vote.get("ballot_uuid") else "Wording at closure", False


def vote_line(vote):
    """Return just the status line for callers that do not render the card."""
    return _vote_card(vote)[0]


def panel(request, org, circle, thread):
    """Return the admitted Suggestion reader's panel and any permitted command.

    A withheld read or a database failure supplies no panel. A known command
    refusal may still leave the reader the wording and vote line.
    """
    try:
        vote = cq.board_topic_vote(credential=request.user, organism_uuid=org,
                                   circle_uuid=circle, thread_uuid=thread,
                                   preview_prepared=request.GET.get("prepare") == "1")
    except DatabaseError:
        return None
    if vote and vote.get("phase"):
        vote["line"], vote["wording_heading"], vote["editable_wording"] = _vote_card(vote)
    if vote and vote.get("operation"):
        op = vote["operation"]
        if op == "INITIATE":
            vote["command"] = forms.issue_form(request, op, org, circle, thread=thread,
                                               version=vote["opening_version"])
            vote.update(route="discussion_put_forward", label="Put to a Board vote")
        else:
            topic = reverse("discussion_topic", args=(org, circle, thread))
            vote["prepare_url"] = topic + "?prepare=1#prepare"
            if "confirmation" in vote:
                preview = vote["confirmation"]
                vote["cancel_url"] = topic + "#vote"
                vote["command"] = forms.issue_form(request, op, org, circle, thread=thread, confirmation=preview)
                vote["no_vote_reason"] = cq_reads.NO_BALLOT_LABELS.get(preview["destination"], "The reason is unavailable.")
    return vote


@personal_response
@login_required(login_url="login")
def submit(request, org, circle, thread, operation):
    """Submit a signed Board command for its authorised reader and redirect to the Topic.

    The writer checks standing under lock. Invalid requests receive the five-part
    refusal or the private unavailable page when the Topic cannot be disclosed.
    """
    if request.method != "POST":
        return forms.refusal(request, "method")
    if is_prefetch(request) or any("prerender" in request.headers.get(key, "").lower()
                                   for key in ("Purpose", "Sec-Purpose")):
        return forms.refusal(request, "preview")
    context = {}
    try:
        data = forms._envelope(request, forms._one(request.POST, "vote_command"),
                               operation, org, circle, thread)
        if operation == "PREPARED" and not data.get("confirmation"):
            raise discussion.DiscussionRefused(code="form")
        key = UUID(data["request"])
        with transaction.atomic():
            # Take the writer's identity, request and organisation locks in its order.
            discussion._actor(request.user)
            cq._lock(key, org)
            _, context = forms._read_page(request.user, org, circle, thread)
            if context["topic"]["topic_type"] != "SUGGESTION" or context["posts"][0]["hidden"]:
                raise discussion.DiscussionUnavailable()
            args = dict(credential=request.user, organism_uuid=org, circle_uuid=circle,
                        thread_uuid=thread, request_uuid=key)
            if operation == "INITIATE":
                cq.put_forward(**args, version_uuid=UUID(data["version"]))
            else:
                cq.confirm_prepared(**args, confirmation=data["confirmation"])
    except discussion.DiscussionUnavailable:
        return forms._unavailable(request, org)
    except discussion.DiscussionRefused as exc:
        if exc.code == "DISC_UNAVAILABLE":
            return forms._unavailable(request, org)
        return forms.refusal(request, exc.code, context)
    except IntegrityError as error:
        try:
            cq.translate_refusal(error)
        except discussion.DiscussionUnavailable:
            return forms._unavailable(request, org)
        except discussion.DiscussionRefused as exc:
            if exc.code == "DISC_UNAVAILABLE":
                return forms._unavailable(request, org)
            return forms.refusal(request, exc.code, context)
        except IntegrityError:
            return forms.refusal(request, "unknown", context)
    except DatabaseError:
        return forms.refusal(request, "unknown", context)
    response = HttpResponseRedirect(reverse("discussion_topic", args=(org, circle, thread)) + "#vote")
    response.status_code = 303
    return response
