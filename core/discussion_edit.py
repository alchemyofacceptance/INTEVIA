"""The edit page for a member's own post (the posts box, part PB-4).

Rulings of 24 Nov 2026: captures AC (Q8), AI (AI4), AJ (AJ1, AJ2) and AM (AM2).

- A Suggestion's opening post: "Edit wording", for its author, until the service's lock closes it
  (cq_edit_lock: voting open, clarity reached, a Clarity period ended, or closed without a vote).
  The stored lock does not name a withdrawal, so the save itself refuses a fresh edit of withdrawn
  wording (``_refuse_withdrawn_wording``; HG capture BJ of 25 Nov 2026, BJ3).
- A reply, and the opening post of a Topic or Question: "Edit", for its author, for 15 minutes after
  posting. Text only; a Topic's title is never edited here.

INTERIM (capture AM2): the 15 minutes are held by this module's route check, ``_gate``, and by nothing
else. ``discussion_posts.edit_post`` and the database accept an author's edit of a reply at any time.
A database guard replaces this check in the December plan; edits recorded before then stay as recorded.

Fail closed (Gemini D3, correction PC1-07). Where the vote read gives a Suggestion no panel, its author is
offered no "Edit wording" and the form is refused, in the refusal page's default words. The save is still
left to the service's own lock.

A repeated save (Gemini D2, correction PC1-06). A form that recorded its version inside the 15 minutes and
is sent again after them is left to the edit service, which answers an identical repeat with its receipt.

Who sees which refusal (item 205). A reader who cannot open the Topic, or who asks for a post that is
not a readable discussion post of it, receives the private unavailable page. A reader of the Topic who
is not the post's author receives the five-part refusal "Only a post's author can edit it". The author
receives the five-part refusal that names the lock, the 15 minutes or the stale version.
"""
from datetime import timezone
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, IntegrityError
from django.db.transaction import atomic
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from core import discussion_forms as forms
from core.cache import personal_response
from core.ui_views import is_prefetch
from src.intevia.services import cq, discussion, discussion_posts, member_navigation
from src.intevia.services.organism_command_support import _lock_registered_organism

CLARITY_LINE = ("This Suggestion is in Clarity. Board members who said they were clear will be asked "
                "to confirm again on the new wording.")


def _operation(post):
    """The form's identity names the one post, so a signed form can never save to another."""
    return "edit:" + str(post)


def _kind(context, post):
    if post["post_number"] != 1:
        return "reply"
    return "wording" if context["topic"].get("topic_type") == "SUGGESTION" else "opening"


def _until(post):
    return post["effective_at"] + discussion_posts.EDIT_WINDOW


def _gate(context, post, state, *, saving=False):
    """Return the refusal code for this reader and post, or None when the edit may go to the service.

    On a save, a Suggestion's wording is left to the service's own lock, which names its reason,
    and to ``_refuse_withdrawn_wording`` for the one ending that lock does not name.
    """
    if post["post_uuid"] not in state["own"]:
        return "DISC_POST_AUTHORITY"
    if post["hidden"]:
        return "DISC_HIDDEN"
    if _kind(context, post) == "wording":
        if saving:
            return None
        vote = context.get("vote")
        if vote is None:
            # Gemini D3 (correction PC1-07): the vote read gave no panel (withheld, or a database failure), so
            # the page cannot tell whether the wording is locked. It fails closed: no control, and no form.
            return "edit_wording_unread"
        if not vote.get("phase") or vote.get("editable_wording"):
            return None
        return "CQ_VOTE_LOCKED" if vote.get("ballot") or vote.get("ballot_uuid") or vote["phase"] == "VOTING" \
            else "edit_wording_ended"
    # INTERIM (capture AM2): the route's 15-minute check; see the module's opening words.
    return None if state["read_at"] < _until(post) else "edit_window"


def _recorded(request, org, circle, post, request_uuid):
    """Whether this form's request has already recorded a version of the post. A read; it records nothing."""
    history = discussion_posts.post_history(credential=request.user, organism_uuid=org, post_uuid=post,
                                            circle_uuid=circle)
    return any(row["version_uuid"] == request_uuid for row in history)


def offer(request, context, org, circle, thread):
    """On the Topic page, give each post this reader may edit its control: "Edit wording" or "Edit".

    The reader's standing is the one the page's own read took (``_read_page``); this reads nothing
    itself, and a page context without it offers no control.
    """
    state = context.get("edit_state")
    if state is None:
        return
    for post in context["posts"]:
        if post.get("post_type") == "GROUP_GAZE" or post.get("markdown") is None:
            continue
        if _gate(context, post, state) is None:
            post["edit"] = {
                "url": reverse("discussion_edit_form", args=(org, circle, thread, post["post_uuid"])),
                "label": "Edit wording" if _kind(context, post) == "wording" else "Edit",
            }


def _target(request, org, circle, thread, post, context):
    """Read the Topic as this reader and find the post; the private unavailable page otherwise."""
    _, released = forms._read_page(request.user, org, circle, thread)
    context.update(released)
    if context["topic"].get("topic_type") == "SUGGESTION":
        from core import discussion_vote
        context["vote"] = discussion_vote.panel(request, org, circle, thread)
    target = next((row for row in context["posts"] if row.get("post_type") != "GROUP_GAZE"
                   and row.get("post_uuid") == post and row.get("markdown") is not None), None)
    if target is None or context.get("edit_state") is None:
        raise discussion.DiscussionUnavailable()
    return target, context["edit_state"]


def _words(context, post):
    """The page's words, as ruled (captures AC Q8, AJ1 and AJ2)."""
    kind = _kind(context, post)
    saving = f"Saving records Version {post['version_number'] + 1}."
    if kind == "wording":
        clarity = (context.get("vote") or {}).get("phase") == "CLARITY"
        return {"heading": "Edit wording", "place": "Opening post", "field_label": "Wording",
                "terms": "You can change the wording until voting opens. " + saving,
                "clarity": CLARITY_LINE if clarity else None}
    noun = "reply" if kind == "reply" else "opening post"
    until = _until(post).astimezone(timezone.utc)
    return {"heading": "Edit your reply" if kind == "reply" else "Edit your post",
            "place": f"Post {post['post_number']}" if kind == "reply" else "Opening post",
            "field_label": "Your " + noun,
            "terms": (f"You can edit your {noun} until {until:%H:%M} UTC, 15 minutes after you posted it. "
                      f"{saving} Earlier versions stay on the record."),
            "clarity": None}


@personal_response
@login_required(login_url="login")
@require_safe
def edit_form(request, org, circle, thread, post):
    """Offer the signed edit form to the post's author while the edit is still open."""
    context = {}
    try:
        with atomic():
            target, state = _target(request, org, circle, thread, post, context)
    except (discussion.DiscussionUnavailable, member_navigation.NavigationUnavailable):
        return forms._unavailable(request, org)
    denied = _gate(context, target, state)
    if denied:
        return forms.refusal(request, denied, context)
    topic_url = reverse("discussion_topic", args=(org, circle, thread))
    context["edit"] = _words(context, target) | {
        "markdown": target["markdown"],
        "save_url": reverse("discussion_edit", args=(org, circle, thread, post)),
        "cancel_url": f"{topic_url}#post-{post}",
        # The form carries the version it was opened on: a stale form is refused by the service.
        "command": forms.issue_form(request, _operation(post), org, circle, thread=thread,
                                    version=target["version_uuid"]),
    }
    return render(request, "core/discussion_edit.html", context)


def _refuse_withdrawn_wording(request, org, circle, thread, post, request_uuid):
    """Refuse a fresh edit of a withdrawn Suggestion's wording (HG capture BJ of 25 Nov 2026, BJ3).

    A form opened before its author withdrew the Suggestion could otherwise still save: the stored
    lock (cq_edit_lock) refuses for an opened vote, elapsed Clarity deadlines and a closure, and those
    refusals stay the service's own, but it does not name a withdrawal. Then the opening post would
    hold one wording and the withdrawal record another.

    The organisation's lock is taken first. It is the lock the withdrawal and the edit service take, so
    from here to the end of the save's transaction the phase read below cannot change. A save this same
    form completed earlier is left to the edit service, which answers an identical repeat with its
    receipt and a different one with its own refusal.
    """
    _lock_registered_organism("default", org)
    vote = cq.board_topic_vote(credential=request.user, organism_uuid=org, circle_uuid=circle, thread_uuid=thread)
    if not vote or vote.get("phase") != "WITHDRAWN":
        return
    if _recorded(request, org, circle, post, request_uuid):
        return
    raise discussion.DiscussionRefused(code="edit_wording_ended")


def _save(request, org, circle, thread, post, data, context):
    """Recheck the post and the reader, then let the edit service record the new version once."""
    target, state = _target(request, org, circle, thread, post, context)
    token = forms._envelope(request, forms._one(data, "command"), _operation(post), org, circle, thread)
    if not token.get("version"):
        raise discussion.DiscussionRefused(code="form")
    markdown = forms._one(data, "markdown")
    denied = _gate(context, target, state, saving=True)
    if denied == "edit_window" and _recorded(request, org, circle, post, UUID(token["request"])):
        # Gemini D2 (correction PC1-06): this form already recorded its version inside the 15 minutes, so a
        # repeat after them is left to the edit service, which answers an identical repeat with its receipt
        # and a different one with its own refusal, as for wording (``_refuse_withdrawn_wording``).
        denied = None
    if denied:
        raise discussion.DiscussionRefused(code=denied)
    if not markdown.strip():
        raise discussion.DiscussionRefused(code="DISC_POST_INPUT")
    if _kind(context, target) == "wording":
        _refuse_withdrawn_wording(request, org, circle, thread, post, UUID(token["request"]))
    return discussion_posts.edit_post(
        credential=request.user, organism_uuid=org, circle_uuid=circle,
        category_uuid=context["category"]["category_uuid"], thread_uuid=thread, post_uuid=post,
        predecessor=UUID(token["version"]), request_uuid=UUID(token["request"]), markdown=markdown)


@personal_response
@login_required(login_url="login")
def save(request, org, circle, thread, post):
    """Save a genuine edit form once, using its original request and version identities."""
    if request.method != "POST":
        return forms.refusal(request, "method")
    if is_prefetch(request) or any("prerender" in request.headers.get(key, "").lower()
                                   for key in ("Purpose", "Sec-Purpose")):
        return forms.refusal(request, "preview")
    context = {}
    entered = {"markdown": request.POST.get("markdown", "")}
    try:
        try:
            with atomic():
                _save(request, org, circle, thread, post, request.POST, context)
        except IntegrityError as exc:
            discussion.translate_refusal(exc)
    except (discussion.DiscussionUnavailable, member_navigation.NavigationUnavailable):
        return forms._unavailable(request, org)
    except discussion.DiscussionRefused as exc:
        if exc.code == "DISC_UNAVAILABLE":
            return forms._unavailable(request, org)
        return forms.refusal(request, exc.code, context, entered)
    except DatabaseError:
        return forms.refusal(request, "unknown", context, entered)
    response = HttpResponseRedirect(reverse("discussion_topic", args=(org, circle, thread)) + f"#post-{post}")
    response.status_code = 303
    return response
