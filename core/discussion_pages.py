"""Read the Board's discussion with the grants of the signed-in reader."""
from datetime import timezone

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_safe

from core.cache import personal_response
from core.vote_tests import outcome_word, result_tests
from core.ui import INSUFFICIENT_PARTICIPATION, danger_card, own_answer_card, result_ring, vote_status_line
from src.intevia.services import cq, cq_reads, discussion, discussion_posts, member_navigation


@transaction.atomic
def _read_page(credential, org, circle, thread):
    scope = {"credential": credential, "organism_uuid": org, "circle_uuid": circle}
    categories = discussion.board_categories(**scope, governance_only=True)["categories"]
    context = {"board_org": org, "board_circle": circle, "governance_board": True}
    pinned_office = None
    if thread is None:
        # Count only, for every open vote on this Board, in one statement.
        open_votes = cq.board_open_vote_counts(**scope)
        for category in categories:
            rows = discussion_posts.category_topics(**scope, category_uuid=category["category_uuid"])
            category["topics"] = [row for row in rows if row["title"] is not None]
            for row in category["topics"]:
                count = open_votes.get(row["thread_uuid"])
                if count:
                    row["vote_status"] = vote_status_line(count["answered"], count["eligible"])
            category["topic_count"] = len(category["topics"])
        context["categories"] = categories
        template = "core/board_discussion.html"
    else:
        detail = discussion_posts.read_thread(**scope, thread_uuid=thread)
        topic = detail["topic"]
        if topic["title"] is None:
            raise discussion.DiscussionUnavailable()
        category = next((row for row in categories if row["category_uuid"] == topic["category_uuid"]), None)
        if category is None:
            raise discussion.DiscussionUnavailable()
        pinned_office = topic.get("office_definition_uuid")
        posts = detail["posts"]
        for post in posts:
            if post.get("post_type") == "GROUP_GAZE" and not post["hidden"]:
                if post.get("count"):
                    post["time_word"], post["time_at"] = result_time(
                        post["count"]["kind"], post.get("effective_at"), post.get("recorded_at"))
                # A released post may link only to a result independently released to this reader.
                try:
                    released = cq_reads.result_post(**scope, thread_uuid=thread, result_uuid=post["post_uuid"],
                                                    governance_only=True, tally=True)
                except discussion.DiscussionUnavailable:
                    continue
                post["result_url"] = reverse("discussion_result", args=(org, circle, thread, post["post_uuid"]))
                # The closed vote's ring is drawn only from a result released to this reader (part PB-5).
                post["tally"] = released.get("tally")
        context.update(topic=topic, category=category, posts=posts)
        # The reader's own posts and the time, for the edit controls (part PB-4). Read here, with the
        # rest of the page's reads, so that building the page from this context reads nothing more.
        context["edit_state"] = discussion_posts.edit_standing(**scope, thread_uuid=thread)
        template = "core/discussion_topic.html"

    # A Board crumb has its own grant. Losing the parent link does not erase a released Topic.
    try:
        directory = member_navigation.member_navigation(credential=credential, organism_uuid=org, office_pin=pinned_office,
                                                        include_offices=False)
    except member_navigation.NavigationUnavailable:
        pass
    else:
        context["organisation"] = directory["organisation"]
        context["circle"] = next((row for row in directory["circles"] if row["circle_uuid"] == circle), None)
        if pinned_office is not None:
            context["office"] = directory.get("linked_office")
    return template, context


# The body whose members a Board vote's ring counts. These pages read the Board's discussion only.
RING_ELECTORATE = "Board members"


def _closed_vote_ring(context):
    """Once the Board's vote has a recorded result, give the vote section that result's ring.

    The ring is the result's own: the tally ``_read_page`` read for the result post this ballot
    produced, with the grants of this reader. A reader the result is not released to has no tally
    and receives no ring. This reads nothing itself.
    """
    vote = context.get("vote")
    if not vote or vote.get("phase") != "RESULT" or vote.get("ballot_uuid") is None or vote.get("election"):
        return
    ballot = str(vote["ballot_uuid"])
    post = next((row for row in reversed(context["posts"]) if row.get("post_type") == "GROUP_GAZE"
                 and not row["hidden"] and row.get("ballot") == ballot), None)
    if post is None:
        return
    if vote.get("danger"):
        # The red card's second sentence, from this result's own counts.
        vote["danger"] = danger_card(post.get("count"), RING_ELECTORATE)
    if post.get("tally"):
        vote["ring"] = result_ring(post["tally"], RING_ELECTORATE)


def _back_organisation(credential, org):
    """Name the address's organisation only to a reader admitted to it (capture AI); otherwise None."""
    try:
        return member_navigation.member_organisation_name(credential=credential, organism_uuid=org)
    except member_navigation.NavigationUnavailable:
        return None


def _page(request, org, circle, thread=None):
    try:
        template, context = _read_page(request.user, org, circle, thread)
    except discussion.DiscussionUnavailable:
        return render(request, "core/discussion_unavailable.html",
                      {"back_organisation": _back_organisation(request.user, org)}, status=404)
    if thread is not None:
        from core.discussion_forms import issue_form, reply_gate
        if context["topic"].get("topic_type") == "SUGGESTION":
            from core import discussion_vote
            try:
                context["vote"] = discussion_vote.panel(request, org, circle, thread)
            except discussion.DiscussionUnavailable:
                return render(request, "core/discussion_unavailable.html",
                              {"back_organisation": _back_organisation(request.user, org)}, status=404)
            _closed_vote_ring(context)
        if reply_gate(context) is None:
            context["reply_command"] = issue_form(request, "reply", org, circle, thread=thread)
        from core import discussion_edit
        discussion_edit.offer(request, context, org, circle, thread)
    return render(request, template, context)


@personal_response
@login_required(login_url="login")
@require_safe
def board_discussion(request, org, circle):
    """Show the Board's categories and the Topics this reader may open; read only."""
    return _page(request, org, circle)


@personal_response
@login_required(login_url="login")
@require_safe
def discussion_topic(request, org, circle, thread):
    """Show one Topic's posts at their recorded versions to a reader who may open it; read only."""
    return _page(request, org, circle, thread)


# INTEVIA's words on a carried decision with no act attached (HG capture Z of 24 Nov 2026, Q4).
NO_ACTION = ("This vote had no action attached. INTEVIA recorded the Board's decision and carried out "
             "nothing further. INTEVIA does not move money.")
# The outcomes in which the body decided the wording; in any other, the wording was only voted on.
_DECIDED = ("QUALIFIED", "NOT_QUALIFIED_VOTE", "NOT_QUALIFIED_TIE")


def result_time_word(kind):
    """The name of a result's time (HG capture AH of 24 Nov 2026, Q15), set here once for both pages.

    A decision that carried or was defeated "Took effect". A result with no decision changed
    nothing, so its time is when the "Result recorded".
    """
    return "Took effect" if kind in _DECIDED else "Result recorded"


def result_time(kind, effective_at, recorded_at):
    """The name and the instant of a result's time, set here once for the Topic and the result page.

    HG capture BW of 25 Nov 2026, BW1: every "Result recorded" label shows the time the result was
    recorded. "Took effect" keeps the result's effective time. A record that carries no recorded
    time falls back to its effective time.
    """
    word = result_time_word(kind)
    if word == "Took effect" or recorded_at is None:
        return word, effective_at
    return word, recorded_at


def _minute(stamp):
    return stamp.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _result_context(record):
    """Give the result page its times, its wording heading and INTEVIA's no-action words.

    The act's facts hold the closing time as ISO 8601 text, while its stamps are datetimes, so
    the text is parsed before it is shown or compared. "Result recorded" is given only when its
    minute differs from the closing time's (capture Z, Q2). A result with no ballot had no voting
    to close, so it has neither row.
    """
    closed = record.get("closed_at") if record.get("ballot") is not None else None
    if isinstance(closed, str):
        closed = parse_datetime(closed)
    recorded = record.get("recorded_at")
    differs = closed is not None and recorded is not None and _minute(recorded) != _minute(closed)
    time_word, first_time = result_time(record["count"]["kind"], record["effective_at"], recorded)
    decided = time_word == "Took effect"
    return {
        # The first time row (``times.took_effect``) is named by ``time_word``. For a result with no decision
        # it is the "Result recorded" row and carries the recorded time, so there is no second row of that name.
        "time_word": time_word,
        "times": {"took_effect": first_time,
                  "voting_closed": closed, "result_recorded": recorded if differs and decided else None},
        "wording_heading": "Wording decided" if record["count"]["kind"] in _DECIDED else "Wording voted on",
        "no_action": NO_ACTION if record.get("carried_no_attached_act") else None,
        # The closed vote's ring (part PB-5), from the result's own tally.
        "ring": result_ring(record["tally"], RING_ELECTORATE) if record.get("tally") else None,
        # Captures AP1 and AQ4: the red card above the Decision card; its Outcome row then reads "No decision".
        # Never for an election, whose Outcome row keeps its own stored sentence (captures AT1 and BW3).
        "danger": (danger_card(record["count"], RING_ELECTORATE)
                   if record["count"]["kind"] == INSUFFICIENT_PARTICIPATION
                   and record.get("event_kind", "DECISION") == "DECISION" else None),
        # Capture AR2: the member's own answer at the foot of the Decision card, as on the Topic.
        "own_answer_card": (own_answer_card(record["own_answer"], voting_open=False)
                            if record.get("own_answer") else None),
        # Capture AT: "How this result was reached", from the result's stored count and its ballot's recorded
        # rule; and the Outcome row's word. Both None for an election or a result with no ballot.
        "tests": (result_tests(record["count"], record.get("rule"), RING_ELECTORATE)
                  if record.get("ballot") is not None and record.get("event_kind", "DECISION") == "DECISION" else None),
        "outcome_word": (outcome_word(record["count"]["kind"])
                         if record.get("ballot") is not None and record.get("event_kind", "DECISION") == "DECISION" else None),
    }


@personal_response
@login_required(login_url="login")
@require_safe
@transaction.atomic
def discussion_result(request, org, circle, thread, result):
    """Open the released result directly, including for an unseated organisation member."""
    scope = dict(credential=request.user, organism_uuid=org, circle_uuid=circle)
    try:
        record = cq_reads.result_post(**scope, thread_uuid=thread, result_uuid=result, governance_only=True,
                                      detail=True)
    except discussion.DiscussionUnavailable:
        return render(request, "core/discussion_unavailable.html",
                      {"back_organisation": _back_organisation(request.user, org)}, status=404)
    context = {"result": record, **_result_context(record)}
    try:
        nav = member_navigation.member_navigation(**scope, include_offices=False)
        context.update(organisation=nav["organisation"],
                       circle=next((row for row in nav["circles"] if row["circle_uuid"] == circle), None))
    except member_navigation.NavigationUnavailable:
        pass
    try:
        detail = discussion_posts.read_thread(**scope, thread_uuid=thread)
    except discussion.DiscussionUnavailable:
        pass
    else:
        if detail["topic"]["title"] is not None:
            context["topic_url"] = reverse("discussion_topic", args=(org, circle, thread))
            # Capture AW1: the result page names its Topic, only to a reader who may open the Topic.
            context["topic_title"] = detail["topic"]["title"]
    return render(request, "core/discussion_result.html", context)
