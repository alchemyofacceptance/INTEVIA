"""The Board vote card and its member commands on an admitted Suggestion."""
from datetime import timezone
from uuid import UUID, uuid5

from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.formats import date_format

from core.cache import personal_response
from core import discussion_forms as forms
from core.ui_views import is_prefetch
from src.intevia.services import cq, cq_reads, discussion, discussion_posts


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


CLARITY_LABELS = {
    "PROCEED": "Proceed to vote",
    "EXTEND": "Extend Clarity",
    "CONFLICT": "Declare a conflict",
    "CLOSE": "Close without a vote",
    "WITHDRAW": "Withdraw Suggestion",
}
CLARITY_PROMPTS = {
    "CLOSE": ("Close without a vote?", "Clarity will end. No ballot will open on this Suggestion."),
    "WITHDRAW": ("Withdraw this Suggestion?", "Your Suggestion will be recorded as withdrawn. No ballot will open."),
}


def _clarity_forms(vote, request, org, circle, thread):
    """Build the admitted choices and sign the selected conflict or stop form."""
    topic = reverse("discussion_topic", args=(org, circle, thread))
    selected = request.GET.get("clarity")
    controls = []
    admitted = vote.get("clarity_actions", ())
    for operation, label in CLARITY_LABELS.items():
        if operation not in admitted:
            continue
        slug = operation.lower()
        control = {"operation": operation, "label": label,
                   "url": reverse("discussion_clarity_" + slug, args=(org, circle, thread))}
        needs_prompt = operation in CLARITY_PROMPTS
        if needs_prompt or operation == "CONFLICT":
            anchor = "#clarity-confirmation" if needs_prompt else "#clarity-input"
            control["preview_url"] = topic + "?clarity=" + slug + anchor
        else:
            control["variant"] = "secondary" if operation == "EXTEND" else "primary"
        if "preview_url" not in control or selected == slug:
            control["token"] = forms.issue_form(
                request, operation, org, circle, thread=thread, window=vote["clarity_window"],
                version=vote["clarity_version"], confirmation=operation if needs_prompt else None,
            )
        if needs_prompt and selected == slug:
            heading, effect = CLARITY_PROMPTS[operation]
            vote["clarity_prompt"] = {**control, "heading": heading, "effect": effect, "cancel": topic + "#vote"}
        elif operation == "CONFLICT" and selected == slug:
            vote["member_form"] = {**control, "field": "basis", "field_label": "Reason for your conflict of interest",
                                   "submit": "Declare conflict", "cancel": topic + "#vote"}
        else:
            controls.append(control)
    vote["clarity_controls"] = controls


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
        body_kind = vote.get("body_kind", "BOARD")
        members, office = {
            "BOARD": ("Board members", "Board Chair"),
            "CIRCLE": ("Circle members", "Circle Coordinator"),
            "COMMITTEE": ("committee members", "Circle Coordinator"),
        }[body_kind]
        vote["electorate_label"] = "voters" if vote.get("election") else members
        vote["office_label"] = office
        if vote.get("clarity_actions"):
            _clarity_forms(vote, request, org, circle, thread)
        if vote.get("confirm_version") is not None:
            vote["confirm_command"] = forms.issue_form(
                request, "CONFIRM", org, circle, thread=thread, version=vote["confirm_version"])
        if vote.get("question_version") is not None:
            topic = reverse("discussion_topic", args=(org, circle, thread))
            vote["question_url"] = topic + "?clarity=question#clarity-input"
            if request.GET.get("clarity") == "question":
                vote["member_form"] = {
                    "operation": "QUESTION", "field": "markdown", "field_label": "Your question",
                    "submit": "Submit question", "cancel": topic + "#vote",
                    "url": reverse("discussion_clarity_question", args=(org, circle, thread)),
                    "token": forms.issue_form(request, "QUESTION", org, circle, thread=thread,
                                              version=vote["question_version"]),
                }
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
        if operation in CLARITY_LABELS:
            if not data.get("window") or not data.get("version"):
                raise discussion.DiscussionRefused(code="form")
            if operation in CLARITY_PROMPTS and data.get("confirmation") != operation:
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
            elif operation == "CONFIRM":
                cq.confirm_clarity(**args, version_uuid=UUID(data["version"]))
            elif operation == "QUESTION":
                denied = forms.reply_gate(context)
                if denied:
                    raise discussion.DiscussionRefused(code=denied)
                markdown = forms._one(request.POST, "markdown")
                if not markdown.strip():
                    raise discussion.DiscussionRefused(code="question_text")
                cq.raise_clarity_question(**args, version_uuid=UUID(data["version"]))
                discussion_posts.reply(
                    **(args | {"request_uuid": uuid5(key, "clarity-question-reply")}),
                    post_uuid=UUID(data["post"]), markdown=markdown,
                    category_uuid=context["category"]["category_uuid"],
                )
            elif operation in CLARITY_LABELS:
                cq.check_clarity_action(**args, operation=operation, window=UUID(data["window"]),
                                        version=UUID(data["version"]))
                if operation == "CONFLICT":
                    basis = forms._one(request.POST, "basis", "")
                    if not basis.strip():
                        raise discussion.DiscussionRefused(code="conflict_basis")
                    cq.declare_conflict(**args, basis=basis)
                elif operation == "PROCEED":
                    cq.finish_clarity(**args)
                elif operation == "EXTEND":
                    cq.extend_clarity(**args)
                elif operation == "CLOSE":
                    cq.close_without_vote(**args)
                else:
                    cq.withdraw(**args)
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
