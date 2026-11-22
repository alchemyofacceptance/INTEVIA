"""Read the Board's discussion with the grants of the signed-in reader."""
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from core.cache import personal_response
from src.intevia.services import cq_reads, discussion, discussion_posts, member_navigation


@transaction.atomic
def _read_page(credential, org, circle, thread):
    scope = {"credential": credential, "organism_uuid": org, "circle_uuid": circle}
    categories = discussion.board_categories(**scope, governance_only=True)["categories"]
    context = {"board_org": org, "board_circle": circle}
    pinned_office = None
    if thread is None:
        for category in categories:
            rows = discussion_posts.category_topics(**scope, category_uuid=category["category_uuid"])
            category["topics"] = [row for row in rows if row["title"] is not None]
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
                # A released post may link only to a result independently released to this reader.
                try:
                    cq_reads.result_post(**scope, thread_uuid=thread, result_uuid=post["post_uuid"], governance_only=True)
                except discussion.DiscussionUnavailable:
                    continue
                post["result_url"] = reverse("discussion_result", args=(org, circle, thread, post["post_uuid"]))
        context.update(topic=topic, category=category, posts=posts)
        template = "core/discussion_topic.html"

    # A Board crumb has its own grant. Losing the parent link does not erase a released Topic.
    try:
        directory = member_navigation.member_navigation(credential=credential, organism_uuid=org, office_pin=pinned_office)
    except member_navigation.NavigationUnavailable:
        pass
    else:
        context["organisation"] = directory["organisation"]
        context["circle"] = next((row for row in directory["circles"] if row["circle_uuid"] == circle), None)
        if pinned_office is not None:
            context["office"] = directory.get("linked_office")
    return template, context


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
        if reply_gate(context) is None:
            context["reply_command"] = issue_form(request, "reply", org, circle, thread=thread)
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



@personal_response
@login_required(login_url="login")
@require_safe
@transaction.atomic
def discussion_result(request, org, circle, thread, result):
    """Open the released result directly, including for an unseated organisation member."""
    scope = dict(credential=request.user, organism_uuid=org, circle_uuid=circle)
    try:
        record = cq_reads.result_post(**scope, thread_uuid=thread, result_uuid=result, governance_only=True)
    except discussion.DiscussionUnavailable:
        return render(request, "core/discussion_unavailable.html",
                      {"back_organisation": _back_organisation(request.user, org)}, status=404)
    context = {"result": record}
    try:
        nav = member_navigation.member_navigation(**scope)
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
    return render(request, "core/discussion_result.html", context)
