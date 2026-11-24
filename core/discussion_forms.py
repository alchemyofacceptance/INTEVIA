"""Browser forms for the Board's START and REPLY commands."""
from uuid import UUID, uuid4

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import DatabaseError, IntegrityError
from django.db.transaction import atomic
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.crypto import salted_hmac
from django.views.decorators.http import require_safe

from core.cache import personal_response
from core.discussion_pages import _back_organisation, _read_page
from core.ui import TOPIC_TYPES
from core.ui_views import is_prefetch
from src.intevia.services import discussion, discussion_posts, member_navigation

FORM_SALT = "core.board-discussion.form.v1"
def help_line(context):
    """Name the nearest coordinator (capture AM): the Circle's short label when the refusal knows the Circle, else the organisation's."""
    label = ((context.get("circle") or {}).get("short_label") or "").strip()
    if not label:
        return "If you need help, contact an Organisation Coordinator."
    article = "an" if label[0].lower() in "aeiou" else "a"
    return f"If you need help, contact {article} {label} Coordinator."
REASONS = {
    "clarity_changed": (409, "The Clarity window or the wording has changed since you opened this form."),
    "conflict_basis": (400, "Give the reason for your conflict of interest in your own words."),
    "question_text": (400, "Enter the text of your question."),
    "form": (400, "The form no longer matches this session or discussion."),
    "csrf": (403, "The form's security check did not pass."),
    "method": (405, "This address accepts a submitted form only."),
    "preview": (400, "A preview cannot submit a discussion form."),
    "DISC_POST_INPUT": (400, "Enter a title of 1 to 90 characters for a new Topic, choose its type and enter your text."),
    "preparation_changed": (409, "The wording or the next step has changed since you opened the confirmation."),
    "office_type": (400, "Only the Topic type can be linked to an office."),
    "DISC_POST_AUTHORITY": (403, "Your current role does not allow this contribution."),
    "DISC_CATEGORY_RETIRED": (403, "This category has been retired and is open for reading."),
    "DISC_CLOSED": (409, "This Topic is closed to replies."),
    "DISC_HIDDEN": (403, "A hidden Topic cannot receive replies."),
    "DISC_OFFICE_SELECTION": (409, "The selected office definition is no longer a current Board office."),
    "DISC_QUOTE": (400, "Choose a readable earlier post version from this Topic."),
    "DISC_REQUEST_CONFLICT": (409, "This form already recorded a different contribution."),
}


VOTE_REASONS = {
    "DISC_CLOSED": "This Topic is closed.",
    "CQ_DISABLED": "Voting is switched off for this organisation.",
    "CQ_RESTRICTED": "Your membership permits reading only.",
    "CQ_PROBATION": "A probationary member cannot take this vote action.",
    "CQ_INITIATOR": "Only the author or a Coordinator permitted by this organisation can put this Suggestion forward.",
    "CQ_EXISTS": "This Suggestion has already been put to a vote.",
    "CQ_PREPARATION": "The vote is no longer within its preparation period.",
    "CQ_PREPARE_SUGGESTION": "Only the author can mark this Suggestion as prepared.",
    "CQ_PREPARE_OFFICE": "Only the officer or administrator authorised for this vote can mark it as prepared.",
    "CQ_VERSION": "The wording has changed since you opened this form.",
    "CQ_CLARITY_OFFICE": "Only the current Board Chair can take this action.",
    "CQ_FINISH_OFFICE": "Only the current Board Chair can open this vote.",
    "CQ_WITHDRAW_AUTHOR": "Only the author can withdraw this Suggestion.",
    "CQ_WITHDRAW_PHASE": "This Suggestion can no longer be withdrawn.",
    "CQ_CONFLICT_BASIS": "Give the reason for your conflict of interest in your own words.",
    "CQ_CONFLICT_REPEAT": "You have already stepped out of this decision.",
    "CQ_AUTHOR_CLEAR": "You are already clear as the author. You can edit the wording or reply to the discussion.",
    "CQ_CONFLICT_PERIOD": "This stage no longer accepts a conflict declaration.",
    "CQ_CLARITY_PERIOD": "This Suggestion is no longer in Clarity.",
    "CQ_ELECTORATE": "You are outside the eligible membership for this decision.",
    "CQ_WINDOW": "The Clarity period is still open and the conditions for this action have not been met.",
    "CQ_RESPONSE_DUE": "The time for responding to Clarity has ended.",
    "CQ_CDPT": "Too few eligible members have confirmed this wording.",
    "CQ_THREE": "At least three eligible members are needed to open the vote.",
    "CQ_LAW": "A provision on which this vote relies is no longer in force.",
    "CQ_CONFIRM_PERIOD": "This vote is no longer accepting Clarity responses.",
    "CQ_CONFIRM_ELIGIBLE": "You are not eligible to respond to this wording.",
    "CQ_RESPONSE_REPEAT": "You already said you were clear on this wording.",
    "CQ_QUESTION_REPEAT": "You already recorded a question about this wording.",
    "CQ_INIT_VERSION": "The opening post has changed since you opened this form.",
    "CQ_FULL_AUDIENCE": "The Suggestion must be visible to everyone eligible for this Board vote.",
    "CQ_SUGGESTION": "Only a Suggestion can be put to a Board vote.",
    "CQ_REQUEST": "This form already recorded a different vote action.",
    "CQ_INPUT": "The vote form is missing a valid reference.",
    "CQ_COMPLETE": "The service could not record the vote action with its authority.",
    "CQ_NOT_OPEN": "Voting on this Suggestion has not opened.",
    "CQ_VOTE_CLOSED": "This vote has closed, or a new vote has replaced the one this form was for.",
    "CQ_DUPLICATE": "You have already answered this vote. An answer is recorded once and cannot be changed.",
    "CQ_CHOICE": "Choose one of the answers offered.",
    "CQ_ABSTAIN_OFF": "Abstaining is not available for this vote.",
    "CQ_SAT_OUT": "You did not confirm that you were clear on this wording, so you sit out this vote.",
    "CQ_CONFLICTED": "You declared a conflict of interest on this Suggestion, so you cannot vote on it.",
    "CQ_PROBATION_VOTE": "Probationary members can discuss this Suggestion but cannot vote.",
}
# What the member can do next after a refused answer (the fourth of the five parts).
ANSWER_ACTIONS = {
    "CQ_VOTE_CLOSED": "Go back to the Topic to see where the vote stands. If a new vote has opened, you can answer it there.",
    "CQ_DUPLICATE": "Go back to the Topic to see your recorded answer.",
    "CQ_CHOICE": "Go back to the Topic, choose one answer and submit it again.",
}


def _binding(request, operation, org, circle, thread):
    session = salted_hmac(FORM_SALT, request.session.session_key or "").hexdigest()
    return [str(request.user.pk), session, operation, str(org), str(circle), str(thread or "")]


def issue_form(request, operation, org, circle, *, thread=None, office=None, version=None, confirmation=None, window=None,
               ballot=None):
    return signing.dumps({
        "binding": _binding(request, operation, org, circle, thread),
        "request": str(uuid4()), "thread": str(thread or uuid4()), "post": str(uuid4()),
        **({"version": str(version)} if version is not None else {}),
        **({"window": str(window)} if window is not None else {}),
        **({"ballot": str(ballot)} if ballot is not None else {}),
        **({"confirmation": confirmation} if confirmation is not None else {}),
        "office": str(office) if office is not None else None,
    }, salt=FORM_SALT)


def _envelope(request, token, operation, org, circle, thread):
    try:
        data = signing.loads(token, salt=FORM_SALT)
        if data["binding"] != _binding(request, operation, org, circle, thread):
            raise ValueError
        return data
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise discussion.DiscussionRefused(code="form") from None


def _unavailable(request, org):
    return render(request, "core/discussion_unavailable.html",
                  {"back_organisation": _back_organisation(request.user, org)}, status=404)


def refusal(request, code, context=None, entered=None, swept=False):
    context = dict(context or {})
    match = request.resolver_match
    noun = "reply" if match and match.url_name == "discussion_reply" else "Topic"
    answer = bool(match and match.url_name == "discussion_answer")
    vote = match and (match.url_name in ("discussion_put_forward", "discussion_prepare", "discussion_confirm_clarity",
                                         "discussion_answer", "discussion_record_result")
                      or (match.url_name or "").startswith("discussion_clarity_"))
    if vote:
        noun = "answer" if answer else "vote action"
    title = f"Your {noun} could not be recorded."
    if code == "unknown":
        title = "Your change could not be confirmed."
        status, why = 503, "The service could not confirm the outcome. The cause is unknown."
        effect = "A vote action may already be recorded." if vote else "A contribution may already be recorded."
        if answer:
            effect = "Your answer may already be recorded."
        if match and match.url_name == "discussion_start":
            action = "Check the Board discussion for your Topic before trying the same submission again."
        else:
            action = "Check the Topic before trying the same submission again."
    else:
        default = (409, "The vote service refused this action." if vote else "The discussion service refused this contribution.")
        status, why = REASONS.get(code, default)
        if vote:
            why = VOTE_REASONS.get(code, why)
            if code == "CQ_PREPARE_SUGGESTION" and context.get("governance_board"):
                why = "Only the author or the current Board Chair can mark this Suggestion as prepared."
        effect = "This request recorded no new vote action." if vote else "This request recorded no new contribution."
        action = "Reopen the page and check the current wording and available actions."
        if answer:
            effect = "This request recorded no answer."
            action = ANSWER_ACTIONS.get(code, action)
    if code in ("method", "csrf", "preview", "form"):
        title = "The form could not be submitted."
    if code == "DISC_POST_AUTHORITY" and context.get("category"):
        why = ("Only a Board Coordinator can start an announcement here."
               if noun == "Topic" and context["category"].get("functional_code") == "ANNOUNCEMENTS"
               else "This category is open to you for reading.")
    if code in ("DISC_QUOTE", "DISC_POST_INPUT", "office_type") and context.get("command"):
        action = "Correct the form below and submit it again."
        context["correctable"] = True
    if code == "CQ_VERSION" and vote and context.get("topic"):
        action = "Review the current wording before you respond again."
        context["wording_url"] = reverse("discussion_topic", args=(
            context["board_org"], context["board_circle"], context["topic"]["thread_uuid"])) + "#vote"
    if code == "office_type":
        action = "Submit the Topic below to keep the office link."
    if code == "DISC_POST_INPUT":
        if match and match.url_name == "discussion_clarity_question":
            why = "Enter your question as plain text."
        elif noun == "reply":
            why = "Enter the text of your reply."
        elif context.get("office"):
            why = "Enter a title of 1 to 90 characters and the text of your Topic."

    if swept:
        # Capture O: refused after the due sweep began, when earlier due work may already be recorded.
        title = "Not all due work could be recorded."
        effect = "Some due work may already have been recorded before this stopped."
        action = "Reopen the Topic to check where the vote stands."
    context.update(title=title, why=why, effect=effect, action=action, help_text=help_line(context), entered=entered or {})
    response = render(request, "core/discussion_refusal.html", context, status=status)
    if code == "method":
        response["Allow"] = "POST"
    return response


def _categories(credential, org, circle):
    return discussion.board_categories(credential=credential, organism_uuid=org, circle_uuid=circle,
                                       governance_only=True)["categories"]


def office_start_url(credential, org, circle, pin):
    """Offer the office action when at least one current Board category admits START."""
    try:
        categories = _categories(credential, org, circle)
    except discussion.DiscussionUnavailable:
        return None
    allowed = [row for row in categories if row["can_start_topic"]]
    if not allowed:
        return None
    preferred = next((row for row in allowed if row["functional_code"] == "DISCUSSION"), allowed[0])
    url = reverse("discussion_start_form", args=(org, circle))
    return f"{url}?category={preferred['category_uuid']}&office={pin}"


def _start_context(request, org, circle, category_id, office_id, allow_prior=False):
    categories = _categories(request.user, org, circle)
    selected = next((row for row in categories if str(row["category_uuid"]) == category_id), None)
    if category_id and selected is None:
        raise discussion.DiscussionUnavailable()
    try:
        pin = UUID(office_id) if office_id else None
    except ValueError:
        raise discussion.DiscussionUnavailable() from None
    try:
        directory = member_navigation.member_navigation(credential=request.user, organism_uuid=org, circle_uuid=circle,
                                                        office_pin=pin if allow_prior else None)
    except member_navigation.NavigationUnavailable:
        directory = {"board_offices": [], "organisation": None, "circles": []}
    office = next((row for row in directory["board_offices"] if str(row["office_definition_uuid"]) == office_id), None)
    if office_id and not office:
        # Only a released family destination admits an older pin to the service.
        # The service recognises an identical receipt or refuses a new stale START.
        if not allow_prior or not directory.get("linked_office"):
            raise discussion.DiscussionUnavailable()
        office = {"office_definition_uuid": pin, "label": directory["linked_office"]["label"]}
    return {
        "board_org": org, "board_circle": circle, "organisation": directory["organisation"],
        "circle": next((row for row in directory["circles"] if row["circle_uuid"] == circle), None),
        "category": selected, "office": office, "offices": directory["board_offices"], "types": TOPIC_TYPES.items(),
        "categories": [row for row in categories if row["can_start_topic"]],
    }


def _start_gate(context):
    selected = context["category"]
    if (selected and not selected["can_start_topic"]) or not context["categories"]:
        code = "DISC_CATEGORY_RETIRED" if selected and selected["retired"] else "DISC_POST_AUTHORITY"
        raise discussion.DiscussionRefused(code=code)


@personal_response
@login_required(login_url="login")
@require_safe
def start_form(request, org, circle):
    """Offer the signed form only for a released category and office."""
    context = None
    try:
        context = _start_context(request, org, circle, request.GET.get("category"), request.GET.get("office"))
        _start_gate(context)
    except (discussion.DiscussionUnavailable, member_navigation.NavigationUnavailable):
        return _unavailable(request, org)
    except discussion.DiscussionRefused as exc:
        return refusal(request, exc.code, context)
    office = context["office"]
    context["command"] = issue_form(request, "start", org, circle,
                                    office=office["office_definition_uuid"] if office else None)
    return render(request, "core/discussion_start.html", context)


def reply_gate(context):
    if context["topic"]["closed"]:
        return "DISC_CLOSED"
    if context["category"]["retired"]:
        return "DISC_CATEGORY_RETIRED"
    if not context["category"]["can_reply"]:
        return "DISC_POST_AUTHORITY"
    return None


def _one(data, name, default=None):
    values = data.getlist(name)
    if len(values) == 1:
        return values[0]
    if not values and default is not None:
        return default
    raise discussion.DiscussionRefused(code="form")


def _dispatch(request, org, circle, thread, data, context):
    """Recheck the target and write while the service's organisation lock is held."""
    if thread:
        _, released = _read_page(request.user, org, circle, thread)
        context.update(released)
    operation = "reply" if thread else "start"
    token = _envelope(request, _one(data, "command"), operation, org, circle, thread)
    args = dict(credential=request.user, organism_uuid=org, circle_uuid=circle,
                thread_uuid=UUID(token["thread"]), post_uuid=UUID(token["post"]),
                request_uuid=UUID(token["request"]), markdown=_one(data, "markdown"))
    if thread:
        context["command"] = _one(data, "command")
        denied = reply_gate(context)
        if denied:
            raise discussion.DiscussionRefused(code=denied)
        if not args["markdown"].strip():
            raise discussion.DiscussionRefused(code="DISC_POST_INPUT")
        quote = _one(data, "quote", "")
        try:
            version = UUID(quote) if quote else None
        except ValueError:
            raise discussion.DiscussionRefused(code="DISC_QUOTE") from None
        return discussion_posts.reply(**args, category_uuid=context["category"]["category_uuid"],
                                      quote_version=version)

    category = _one(data, "category")
    if not category:
        raise discussion.DiscussionRefused(code="form")
    selected_office = _one(data, "office", "")
    if token["office"] and selected_office and selected_office != token["office"]:
        raise discussion.DiscussionRefused(code="form")
    office_id = token["office"] or selected_office
    context.update(_start_context(request, org, circle, category, office_id, True))
    _start_gate(context)
    context["command"] = _one(data, "command")
    topic_type = _one(data, "type")
    if office_id and topic_type != "TOPIC":
        raise discussion.DiscussionRefused(code="office_type")
    if not args["markdown"].strip():
        raise discussion.DiscussionRefused(code="DISC_POST_INPUT")
    return discussion_posts.start_topic(
        **args, category_uuid=context["category"]["category_uuid"], title=_one(data, "title"),
        topic_type=topic_type, office_definition_uuid=UUID(office_id) if office_id else None,
    )


@personal_response
@login_required(login_url="login")
def submit(request, org, circle, thread=None):
    """Dispatch a genuine form once using its original command and record identities."""
    data = request.POST if request.method == "POST" else None
    if data is None:
        return refusal(request, "method")
    if is_prefetch(request) or any("prerender" in request.headers.get(k, "").lower()
                                   for k in ("Purpose", "Sec-Purpose")):
        return refusal(request, "preview")
    context = {}
    entered = {key: data.get(key, "") for key in ("title", "markdown", "type", "quote")}
    try:
        try:
            with atomic():
                receipt = _dispatch(request, org, circle, thread, data, context)
        except IntegrityError as exc:
            discussion.translate_refusal(exc)
    except (discussion.DiscussionUnavailable, member_navigation.NavigationUnavailable):
        return _unavailable(request, org)
    except discussion.DiscussionRefused as exc:
        if exc.code == "DISC_UNAVAILABLE":
            return _unavailable(request, org)
        return refusal(request, exc.code, context, entered)
    except DatabaseError:
        return refusal(request, "unknown", context, entered)
    url = reverse("discussion_topic", args=(org, circle, receipt["thread"]))
    response = HttpResponseRedirect(f"{url}#post-{receipt['post']}")
    response.status_code = 303
    return response
