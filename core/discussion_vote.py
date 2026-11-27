"""The Board vote card and its member commands on an admitted Suggestion."""
from datetime import timezone
from uuid import UUID, uuid5

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format

from core.cache import personal_response
from core import discussion_forms as forms
from core import publication_vote
from core.law_pages import policy_display_parts
from core.vote_tests import progress_line, vote_rule_rows
from core.ui import INSUFFICIENT_PARTICIPATION, ballot_choice_labels, count_ring, danger_card, own_answer_card, vote_status_line
from core.ui_views import is_prefetch
from src.intevia.services import cq, cq_reads, discussion, discussion_posts


def _utc(value):
    return date_format(value.astimezone(timezone.utc), "j F Y, H:i") + " UTC"


def _minute(value):
    return value.astimezone(timezone.utc).replace(second=0, microsecond=0)


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
            # The ruled render's words (HG capture H, 2b): say when voting closed.
            return "Voting closed" + (" · Closed " + _utc(deadline) if deadline else " · Awaiting result"), "Wording voted on", False
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
        if kind == INSUFFICIENT_PARTICIPATION and not vote.get("election"):
            # Capture AQ1: the red card above the vote card says this, so the status line does not repeat it.
            # An election has no red card (capture BW3), so its line keeps its outcome.
            outcome = None
        if outcome and ". " not in outcome:
            outcome = outcome.removesuffix(".")
        date = vote.get("effective_at")
        if vote.get("ballot_uuid") and status in ("Decided", "Vote ended"):
            # HG capture Z of 24 Nov 2026, Q2 (C1-403): a vote was held, so each time is named.
            # The result's stamp is when it was recorded; that is shown only when its minute
            # differs from the close of voting.
            closed = vote.get("closed_at")
            if isinstance(closed, str):
                closed = parse_datetime(closed)
            times = ["Voting closed " + _utc(closed)] if closed else []
            if date and (closed is None or _minute(date) != _minute(closed)):
                times.append("Result recorded " + _utc(date))
            line = " · ".join([part for part in (status, outcome) if part] + times)
        else:
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


# INTEVIA's line for a member who reads an open vote they cannot answer. A declared
# conflict already has its own line in the same box.
BALLOT_NOTES = {
    "CQ_SAT_OUT": "You did not confirm that you were clear on this wording, so you sit out this vote.",
    "CQ_PROBATION_VOTE": "Probationary members can discuss this Suggestion but cannot vote.",
}


def _ballot_state(vote, request, org, circle, thread):
    """Add the count-only status, then one of: the member's own answer, the ballot form or its button.

    The form is signed for the one ballot the page read, so an answer can never
    move to a later attempt. A member who cannot answer receives no button.
    """
    ballot = vote["ballot"]
    live = vote["read_at"] < vote["deadline"]
    if live:
        vote["status_line"] = vote_status_line(ballot["answered"], ballot["eligible"])
        vote["ring"] = count_ring(ballot["answered"], ballot["eligible"], vote["electorate_label"])
        # Part PB-6d (capture AY3): the rule this vote will be decided by, from the ballot's own record, and
        # how many more must answer. Only while voting is open; the result's summary takes over after it.
        rows = vote_rule_rows(ballot["eligible"], ballot.get("required"), ballot.get("rule"), vote["electorate_label"])
        if rows:
            vote["rule_rows"] = rows
            vote["progress"] = progress_line(ballot["answered"], ballot["required"])
    labels = ballot_choice_labels()
    if ballot.get("own_answer"):
        vote["own_answer_label"] = labels[ballot["own_answer"]]
        # The card's wording follows the deadline, also while the result is still due (capture BK2; Astra's P2-2).
        vote["own_answer_card"] = own_answer_card(ballot["own_answer"], voting_open=live)
    elif live and ballot.get("may_answer"):
        topic = reverse("discussion_topic", args=(org, circle, thread))
        if request.GET.get("ballot") == "answer":
            vote["ballot_form"] = {
                "url": reverse("discussion_answer", args=(org, circle, thread)),
                "token": forms.issue_form(request, "ANSWER", org, circle, thread=thread, ballot=ballot["ballot_uuid"]),
                "options": [(option, labels[option]) for option in ballot["options"]],
                "cancel": topic + "#vote",
            }
        else:
            vote["answer_url"] = topic + "?ballot=answer#ballot-form"
    elif live:
        vote["ballot_note"] = BALLOT_NOTES.get(ballot.get("answer_refusal"))
    if vote.get("result_due"):
        vote["due_url"] = reverse("discussion_record_result", args=(org, circle, thread))
        vote["due_command"] = forms.issue_form(request, "RECORD_DUE", org, circle, thread=thread)


# INTEVIA's words after "Record the result". The rule's catch-up works across all of
# the person's organisations, so the page says so. Interim until the Clock's wake
# is qualified (owed row C1-228); then due results record themselves.
DUE_RECORDED = ("Due results are now recorded. This covered every vote and deadline that was due "
                "in all the organisations you belong to, not only this Topic.")
DUE_NOTHING = "Nothing was due. The result may already have been recorded."


def _record_due(request, org, circle, thread, context):
    """Record what is due, only for a reader of this Topic and only from a submitted form.

    The rule's own catch-up decides what is due and commits each journey itself,
    so it runs outside this view's transaction. Reading a page never calls it.
    """
    _, released = forms._read_page(request.user, org, circle, thread)
    context.update(released)
    if context["topic"]["topic_type"] != "SUGGESTION" or context["posts"][0]["hidden"]:
        raise discussion.DiscussionUnavailable()
    try:
        recorded = cq.catch_up(credential=request.user) == "Due work recorded."
    except discussion.DiscussionRefused as exc:
        if exc.code == "DISC_UNAVAILABLE":
            raise
        # Capture O: the sweep records each piece of due work in its own transaction, so a refusal
        # from here on may follow work already recorded. The receipt must not say nothing was recorded.
        return forms.refusal(request, exc.code, context, swept=True)
    except DatabaseError:
        # Capture Q (D2): an unknown outcome after the sweep began takes capture O's form, with the unknown reason.
        return forms.refusal(request, "unknown", context, swept=True)
    messages.info(request, DUE_RECORDED if recorded else DUE_NOTHING)
    response = HttpResponseRedirect(reverse("discussion_topic", args=(org, circle, thread)) + "#vote")
    response.status_code = 303
    return response


def panel(request, org, circle, thread):
    """Return the admitted Suggestion reader's panel and any permitted command.

    A withheld read or a database failure supplies no panel. A known command
    refusal may still leave the reader the wording and vote line.
    """
    try:
        vote = cq.board_topic_vote(credential=request.user, organism_uuid=org,
                                   circle_uuid=circle, thread_uuid=thread,
                                   preview_prepared=request.GET.get("prepare") == "1", include_publications=True)
        if vote and vote.get("policy_kind"):
            vote["instrument_parts"] = policy_display_parts(request.user, org, vote.pop("instrument_text"))
    except DatabaseError:
        return None
    if vote and vote.get("phase"):
        vote["line"], vote["wording_heading"], vote["editable_wording"] = _vote_card(vote)
        if vote.get("outcome") == INSUFFICIENT_PARTICIPATION and not vote.get("election"):
            # The page adds the counts from the result post once it has read it (discussion_pages._closed_vote_ring).
            # An election never takes the red card (HG capture BW of 25 Nov 2026, BW3).
            vote["danger"] = danger_card()
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
        if vote.get("ballot"):
            _ballot_state(vote, request, org, circle, thread)
        elif vote.get("decided_own_answer"):
            # Capture N: after the result, the member's own answer stays on their page.
            vote["own_answer_label"] = ballot_choice_labels()[vote["decided_own_answer"]]
            vote["own_answer_card"] = own_answer_card(vote["decided_own_answer"], voting_open=False)
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
            publication_vote.sign_choices(request, vote, org, circle, thread)
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
        if operation == "ANSWER" and not data.get("ballot"):
            raise discussion.DiscussionRefused(code="form")
        if operation in CLARITY_LABELS:
            if not data.get("window") or not data.get("version"):
                raise discussion.DiscussionRefused(code="form")
            if operation in CLARITY_PROMPTS and data.get("confirmation") != operation:
                raise discussion.DiscussionRefused(code="form")
        if operation == "RECORD_DUE":
            return _record_due(request, org, circle, thread, context)
        if operation == "INITIATE" and "confirmation" in data:
            if not isinstance(data["confirmation"], dict):
                raise discussion.DiscussionRefused(code="form")
            context["policy_vote_intent"] = data["confirmation"].get("intent")
        key = UUID(data["request"])
        with transaction.atomic():
            # Take the writer's identity, request and organisation locks in its order.
            actor = discussion._actor(request.user)
            cq._lock(key, org)
            _, released = forms._read_page(request.user, org, circle, thread)
            context.update(released)
            if context["topic"]["topic_type"] != "SUGGESTION" or context["posts"][0]["hidden"]:
                raise discussion.DiscussionUnavailable()
            args = dict(credential=request.user, organism_uuid=org, circle_uuid=circle,
                        thread_uuid=thread, request_uuid=key)
            if operation == "INITIATE":
                if "policy_vote_intent" in context:
                    reviewed = publication_vote.submit(request, context, args, actor, data)
                    if reviewed is not None:
                        return reviewed
                else:
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
            elif operation == "ANSWER":
                choice = forms._one(request.POST, "choice", "")
                if choice not in cq.BALLOT_OPTIONS:
                    raise discussion.DiscussionRefused(code="CQ_CHOICE")
                signed = UUID(data["ballot"])
                # Capture N: under the submission lock, a form for a ballot the Topic has since
                # replaced is refused, even when it would replay its own earlier receipt.
                current = cq.board_topic_vote(credential=request.user, organism_uuid=org,
                                              circle_uuid=circle, thread_uuid=thread)
                if current is None:
                    raise discussion.DiscussionUnavailable()
                if current.get("ballot_uuid") != signed:
                    raise discussion.DiscussionRefused(code="CQ_VOTE_CLOSED")
                # The ballot the form was signed for, never the Topic's latest one.
                cq.answer(**args, ballot_uuid=signed, choice=choice)
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
    if context.get("policy_vote_intent") == "adopt":
        messages.success(request, f"Adoption of version {data['confirmation']['number']} was put to a Board vote.")
    elif context.get("policy_vote_intent") == "publish":
        messages.success(request, f"Publication of version {data['confirmation']['number']} was put to a Board vote.")
    response = HttpResponseRedirect(reverse("discussion_topic", args=(org, circle, thread)) + "#vote")
    response.status_code = 303
    return response
