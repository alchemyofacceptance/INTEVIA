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
    "form": (400, "The form no longer matches this session or discussion."),
    "csrf": (403, "The form's security check did not pass."),
    "method": (405, "This address accepts a submitted form only."),
    "preview": (400, "A preview cannot submit a discussion form."),
    "DISC_POST_INPUT": (400, "Enter a title of 1 to 90 characters for a new Topic, choose its type and enter your text."),
    "office_type": (400, "Only the Topic type can be linked to an office."),
    "DISC_POST_AUTHORITY": (403, "Your current role does not allow this contribution."),
    "DISC_CATEGORY_RETIRED": (403, "This category has been retired and is open for reading."),
    "DISC_CLOSED": (409, "This Topic is closed to replies."),
    "DISC_HIDDEN": (403, "A hidden Topic cannot receive replies."),
    "DISC_OFFICE_SELECTION": (409, "The selected office definition is no longer a current Board office."),
    "DISC_QUOTE": (400, "Choose a readable earlier post version from this Topic."),
    "DISC_REQUEST_CONFLICT": (409, "This form already recorded a different contribution."),
}


def _binding(request, operation, org, circle, thread):
    session = salted_hmac(FORM_SALT, request.session.session_key or "").hexdigest()
    return [str(request.user.pk), session, operation, str(org), str(circle), str(thread or "")]


def issue_form(request, operation, org, circle, *, thread=None, office=None):
    return signing.dumps({
        "binding": _binding(request, operation, org, circle, thread),
        "request": str(uuid4()), "thread": str(thread or uuid4()), "post": str(uuid4()),
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


def refusal(request, code, context=None, entered=None):
    context = dict(context or {})
    match = request.resolver_match
    noun = "reply" if match and match.url_name == "discussion_reply" else "Topic"
    title = f"Your {noun} could not be recorded."
    if code == "unknown":
        title = "Your change could not be confirmed."
        status, why = 503, "The service could not confirm the outcome. The cause is unknown."
        effect = "A contribution may already be recorded."
        action = "Check the Topic before trying the same submission again."
    else:
        default = (409, "The discussion service refused this contribution.")
        status, why = REASONS.get(code, default)
        effect = "This request recorded no new contribution."
        action = "Reopen the page and check the current wording and available actions."
    if code in ("method", "csrf", "preview", "form"):
        title = "The form could not be submitted."
    if code == "DISC_POST_AUTHORITY" and context.get("category"):
        why = ("Only a Board Coordinator can start an announcement here."
               if noun == "Topic" and context["category"].get("functional_code") == "ANNOUNCEMENTS"
               else "This category is open to you for reading.")
    if code in ("DISC_QUOTE", "DISC_POST_INPUT", "office_type") and context.get("command"):
        action = "Correct the form below and submit it again."
        context["correctable"] = True
    if code == "office_type":
        action = "Submit the Topic below to keep the office link."
    if code == "DISC_POST_INPUT":
        if noun == "reply":
            why = "Enter the text of your reply."
        elif context.get("office"):
            why = "Enter a title of 1 to 90 characters and the text of your Topic."

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
