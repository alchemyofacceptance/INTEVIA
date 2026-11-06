"""DISC1 post commands and pure reads. Typed Markdown is data, never rendered HTML.

An outer-transaction caller translates deferred commit errors with
 discussion.translate_refusal; no returned function can catch that later exit.
"""
import hashlib
import json
from uuid import UUID

from django.db import IntegrityError, transaction

from src.intevia.services import discussion as disc


def body_fingerprint(markdown):
    if type(markdown) is not str or "\x00" in markdown:
        raise disc.DiscussionRefused(code="DISC_POST_INPUT")
    return hashlib.sha256(markdown.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def _command(*, credential, organism_uuid, category_uuid, thread_uuid, request_uuid,
             operation, circle_uuid=None, post_uuid=None, markdown=None, **fields):
    try:
        with transaction.atomic():
            actor = disc._actor(credential)
            payload = {"board": str(disc._board_id(organism_uuid, circle_uuid)),
                       "organism": disc._uuid(organism_uuid), "circle": disc._uuid(circle_uuid) if circle_uuid is not None else None,
                       "category": disc._uuid(category_uuid), "thread": disc._uuid(thread_uuid),
                       "post": disc._uuid(post_uuid) if operation in {"START", "REPLY", "EDIT", "HIDE", "RESTORE"} else None,
                       "request": disc._uuid(request_uuid), "operation": operation, **fields}
            if operation in {"START", "REPLY", "EDIT"}:
                payload["body_fingerprint"] = body_fingerprint(markdown)
            stamp = disc._rows("SELECT statement_timestamp() AS stamp")[0]["stamp"]
            result = disc._rows("SELECT public.discp_execute(%s::jsonb,%s,%s,%s,%s) AS result",
                               [json.dumps(payload), markdown, actor.pk, actor.access_epoch, stamp])[0]["result"]
            return {key: UUID(value) if value is not None else None for key, value in result.items()}
    except IntegrityError as exc:
        disc.translate_refusal(exc)


def start_topic(*, credential, organism_uuid, category_uuid, thread_uuid, post_uuid, request_uuid,
                title, markdown, topic_type="TOPIC", circle_uuid=None):
    if type(title) is not str or not title.strip() or "\x00" in title or not 1 <= len(title) <= 90 or type(topic_type) is not str or topic_type not in {"TOPIC", "QUESTION", "SUGGESTION"}:
        raise disc.DiscussionRefused(code="DISC_POST_INPUT")
    return _command(credential=credential, organism_uuid=organism_uuid, category_uuid=category_uuid,
                    thread_uuid=thread_uuid, post_uuid=post_uuid, request_uuid=request_uuid, operation="START",
                    circle_uuid=circle_uuid, title=title, markdown=markdown, type=topic_type, quote=None)


def reply(*, credential, organism_uuid, category_uuid, thread_uuid, post_uuid, request_uuid,
          markdown, quote_version=None, circle_uuid=None):
    return _command(credential=credential, organism_uuid=organism_uuid, category_uuid=category_uuid,
                    thread_uuid=thread_uuid, post_uuid=post_uuid, request_uuid=request_uuid, operation="REPLY",
                    circle_uuid=circle_uuid, markdown=markdown, quote=disc._uuid(quote_version) if quote_version is not None else None)


_KEEP_QUOTE = object()


def edit_post(*, credential, organism_uuid, category_uuid, thread_uuid, post_uuid, predecessor,
              request_uuid, markdown, quote_version=_KEEP_QUOTE, circle_uuid=None):
    """Omit quote_version to retain it; pass None to remove it explicitly."""
    quote = {} if quote_version is _KEEP_QUOTE else {"quote": disc._uuid(quote_version) if quote_version is not None else None}
    return _command(credential=credential, organism_uuid=organism_uuid, category_uuid=category_uuid,
                    thread_uuid=thread_uuid, post_uuid=post_uuid, request_uuid=request_uuid, operation="EDIT",
                    circle_uuid=circle_uuid, markdown=markdown, predecessor=disc._uuid(predecessor),
                    **quote)


def moderate_thread(*, credential, organism_uuid, category_uuid, thread_uuid, request_uuid, operation, circle_uuid=None):
    """PIN, UNPIN, CLOSE or REOPEN; each requires this board's effective office."""
    if operation not in {"PIN", "UNPIN", "CLOSE", "REOPEN"}:
        raise disc.DiscussionRefused(code="DISC_POST_INPUT")
    return _command(credential=credential, organism_uuid=organism_uuid, category_uuid=category_uuid,
                    thread_uuid=thread_uuid, request_uuid=request_uuid, operation=operation, circle_uuid=circle_uuid)


def set_post_hidden(*, credential, organism_uuid, category_uuid, thread_uuid, post_uuid, request_uuid,
                    hidden, as_coordinator=False, circle_uuid=None):
    """Author layer by default. An explicit Coordinator restoration clears both layers."""
    if type(hidden) is not bool or type(as_coordinator) is not bool:
        raise disc.DiscussionRefused(code="DISC_POST_INPUT")
    return _command(credential=credential, organism_uuid=organism_uuid, category_uuid=category_uuid,
                    thread_uuid=thread_uuid, post_uuid=post_uuid, request_uuid=request_uuid,
                    operation="HIDE" if hidden else "RESTORE", as_coordinator=as_coordinator, circle_uuid=circle_uuid)


def visible_boards(*, credential):
    with transaction.atomic():
        actor = disc._actor(credential)
        rows = disc._rows("""SELECT b.board_uuid,lo.organism_id AS organism_uuid,c.circle_uuid
          FROM public.core_discboard b JOIN public.core_livingorganism lo ON lo.id=b.organism_id
          LEFT JOIN public.core_circle c ON c.id=b.circle_id
          WHERE EXISTS(SELECT FROM public.disc_categories cat WHERE cat.board_uuid=b.board_uuid
            AND public.disc_audience(b.organism_id,b.circle_id,%s,cat.access_level,statement_timestamp()))
          ORDER BY lo.organism_id,c.circle_uuid NULLS FIRST""", [actor.pk])
        if not rows:
            raise disc.DiscussionUnavailable()
        return rows


def _category(actor, organism_uuid, circle_uuid, category_uuid):
    board = disc._audience(actor, organism_uuid, circle_uuid, category_uuid)
    rows = disc._rows("""SELECT c.* FROM public.disc_categories c JOIN public.core_discboard b USING(board_uuid)
      WHERE c.category_uuid=%s AND c.board_uuid=%s
      AND public.disc_audience(b.organism_id,b.circle_id,%s,c.access_level,statement_timestamp())""",
                     [category_uuid, board["board_uuid"], actor.pk])
    if not rows:
        raise disc.DiscussionUnavailable()
    return board, rows[0]


def category_topics(*, credential, organism_uuid, category_uuid, circle_uuid=None):
    with transaction.atomic():
        actor = disc._actor(credential)
        board, _ = _category(actor, organism_uuid, circle_uuid, category_uuid)
        return disc._rows("""SELECT t.thread_uuid,t.topic_type,t.record_kind,t.created_at,t.actor_id,t.pinned,t.closed,
          p.coordinator_hidden AS hidden,
          CASE WHEN %s OR NOT p.coordinator_hidden THEN t.title END AS title
          FROM public.discp_threads t JOIN public.discp_posts p ON p.thread_uuid=t.thread_uuid AND p.post_number=1
          WHERE t.category_uuid=%s ORDER BY t.pinned DESC,t.created_sequence DESC,t.thread_uuid""",
                         [board["office"] is not None, category_uuid])


# The quote is dereferenced at read time using the quoted post's CURRENT hide
# state, while its text is the EXACT quoted version. Hiding never copies text.
POST_SELECT = """SELECT p.post_uuid,p.thread_uuid,p.author_id,p.post_number,
 p.received_at,p.effective_at,p.recorded_at,v.version_uuid,v.version_number,v.predecessor,
 v.body_fingerprint,v.quote_version,a.actor_id AS version_author,a.received_at AS version_received_at,
 a.effective_at AS version_effective_at,a.recorded_at AS version_recorded_at,a.authority_source,
 first.coordinator_hidden AS topic_hidden,
 (p.author_hidden OR p.coordinator_hidden OR first.coordinator_hidden) AS hidden,
 CASE WHEN %s OR NOT (p.author_hidden OR p.coordinator_hidden OR first.coordinator_hidden)
   THEN body.markdown END AS markdown,
 CASE WHEN q.post_uuid IS NULL THEN NULL ELSE
   (q.author_hidden OR q.coordinator_hidden OR first.coordinator_hidden) END AS quote_hidden,
 CASE WHEN %s OR NOT (p.author_hidden OR p.coordinator_hidden OR first.coordinator_hidden
   OR q.author_hidden OR q.coordinator_hidden) THEN quoted.markdown END AS quoted_markdown
 FROM public.discp_posts p JOIN public.discp_posts first ON first.thread_uuid=p.thread_uuid AND first.post_number=1
 JOIN public.core_discpostversion v ON v.post_uuid=p.post_uuid
 JOIN public.core_discbody body ON body.version_uuid=v.version_uuid
 JOIN public.core_discpostact a ON a.act_uuid=v.act_uuid
 LEFT JOIN public.core_discpostversion qv ON qv.version_uuid=v.quote_version
 LEFT JOIN public.discp_posts q ON q.post_uuid=qv.post_uuid
 LEFT JOIN public.core_discbody quoted ON quoted.version_uuid=v.quote_version
 """


def _thread(actor, organism_uuid, circle_uuid, thread_uuid):
    disc._read_owner(organism_uuid, circle_uuid)
    disc._read_uuid(thread_uuid)
    rows = disc._rows("SELECT category_uuid FROM public.core_discthread WHERE thread_uuid=%s", [thread_uuid])
    if not rows:
        raise disc.DiscussionUnavailable()
    board, category = _category(actor, organism_uuid, circle_uuid, rows[0]["category_uuid"])
    return board, category


def read_thread(*, credential, organism_uuid, thread_uuid, circle_uuid=None):
    with transaction.atomic():
        actor = disc._actor(credential)
        board, category = _thread(actor, organism_uuid, circle_uuid, thread_uuid)
        coordinator = board["office"] is not None
        posts = disc._rows(POST_SELECT + " WHERE p.thread_uuid=%s AND v.version_uuid=p.version_uuid ORDER BY p.post_number",
                           [coordinator, coordinator, thread_uuid])
        topic = disc._rows("SELECT * FROM public.discp_threads WHERE thread_uuid=%s", [thread_uuid])[0]
        if posts[0]["topic_hidden"] and not coordinator:
            topic["title"] = None
        return {"topic": topic, "posts": posts, "category_retired": category["retired"]}


def post_history(*, credential, organism_uuid, post_uuid, circle_uuid=None):
    with transaction.atomic():
        actor = disc._actor(credential)
        disc._read_owner(organism_uuid, circle_uuid)
        disc._read_uuid(post_uuid)
        rows = disc._rows("SELECT thread_uuid FROM public.core_discpost WHERE post_uuid=%s", [post_uuid])
        if not rows:
            raise disc.DiscussionUnavailable()
        board, _ = _thread(actor, organism_uuid, circle_uuid, rows[0]["thread_uuid"])
        coordinator = board["office"] is not None
        return disc._rows(POST_SELECT + " WHERE p.post_uuid=%s ORDER BY v.version_number", [coordinator, coordinator, post_uuid])


def moderation_history(*, credential, organism_uuid, thread_uuid, circle_uuid=None):
    """Metadata only: who hid/restored or pinned/closed, when and under which source."""
    with transaction.atomic():
        actor = disc._actor(credential)
        _thread(actor, organism_uuid, circle_uuid, thread_uuid)
        return disc._rows("""SELECT a.act_uuid,a.operation,a.actor_id,a.authority_source,a.payload->>'post' AS post_uuid,
          a.received_at,a.effective_at,a.recorded_at,a.author_hidden,a.coordinator_hidden,a.pinned,a.closed
          FROM public.core_discpostact a JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
          WHERE a.payload->>'thread'=%s AND a.operation IN ('HIDE','RESTORE','PIN','UNPIN','CLOSE','REOPEN')
          ORDER BY e.sequence""", [str(thread_uuid)])
