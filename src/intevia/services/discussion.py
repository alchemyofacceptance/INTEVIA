"""DISC1 P1 board/category commands and pure audience-filtered reads.

D2 is interim until C1-65; historical acts keep their recorded authority.
Caller-owned outer COMMIT errors must be translated by that caller with
translate_refusal(): a returned service cannot catch a later transaction exit.
"""
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from django.db import IntegrityError, connections, transaction

from src.intevia.services.law_records import _records
from src.intevia.services.organism_command_support import (
    _lock_actor,
    _lock_registered_organism,
)
from src.intevia.services.organism_entitlement_service import (
    DISCUSSION_CATEGORY_CAPABILITY,
    entitlement_effective,
)

UNAVAILABLE = "This discussion is unavailable to you."
MESSAGES = {
    "DISC_POST_INPUT": "Supply a title of 1 to 90 characters, a topic, question or suggestion type, and Markdown text.",
    "DISC_POST_AUTHORITY": "You do not have permission to perform this act on this discussion. Suspended and restricted members cannot change discussions.",
    "DISC_CATEGORY_RETIRED": "This category is retired. Its history remains readable, but it accepts no new topics or replies.",
    "DISC_CLOSED": "This topic is closed. Its history remains readable, but it accepts no new replies.",
    "DISC_HIDDEN": "Restore this hidden post or topic before replying or editing it.",
    "DISC_POST_PREDECESSOR": "Choose the current post version before editing it.",
    "DISC_QUOTE": "Choose a readable earlier post version in this thread to quote.",
    "DISC_MODERATED": "A Coordinator has hidden this post. Only this board's effective Coordinator may restore it.",
    "DISC_UNAVAILABLE": UNAVAILABLE,
    "DISC_AUTHORITY": "This act requires the selected board's effective Coordinator. Suspended and restricted members cannot change discussions.",
    "DISC_CATEGORY_LIMIT": "This board already has nine or more custom categories. Subscriber access is needed to add another. Every existing category remains available for reading and posting after a subscription ends.",
    "DISC_PREDECESSOR": "Choose the current category version; a retired category cannot be changed and a new category needs a new identity.",
    "DISC_FUNCTIONAL": "INTEVIA's functional categories cannot be changed or retired through this route.",
    "DISC_WELCOME_OPEN": "Welcome is already open on this board.",
    "DISC_PROBATION": "The LO must permit probationary discussion before its Coordinator or a Circle Coordinator opens a probationary category.",
    "DISC_REQUEST_CONFLICT": "This request identity already records a different discussion act.",
    "DISC_INPUT": "Supply exact UUID references, true or false for probation access, and valid category fields: a name of 1 to 45 characters, text description, integer order and supported access level.",
    "DISC_RECORD": "The discussion record does not match its attributed act. No changes were committed.",
    "DISC_COMPLETE": "The discussion act could not complete with its authority and complete lineage. No changes were committed.",
}
CAPABILITY = DISCUSSION_CATEGORY_CAPABILITY


class DiscussionRefused(ValueError):
    def __init__(self, message=None, *, code="DISC_UNAVAILABLE"):
        self.code = code
        super().__init__(MESSAGES.get(code, UNAVAILABLE) if message is None else message)


class DiscussionUnavailable(PermissionError):
    def __init__(self, message=None):
        super().__init__(UNAVAILABLE)


def translate_refusal(error):
    code = getattr(getattr(error.__cause__, "diag", None), "message_primary", None)
    if code in MESSAGES:
        raise DiscussionRefused(code=code) from error
    raise error


def _rows(sql, params=()):
    with connections["default"].cursor() as cursor:
        cursor.execute(sql, params)
        return _records(cursor) if cursor.description else []


def _uuid(value):
    if type(value) is not UUID:
        raise DiscussionRefused(code="DISC_INPUT")
    return str(value)


def _read_uuid(value):
    if type(value) is not UUID:
        raise ValueError("Choose an exact UUID for the discussion reference.")
    return str(value)


def _board_id(organism, circle):
    return uuid5(NAMESPACE_URL, "intevia:discussion:board:" + str(organism) + ":" + str(circle))


def _actor(credential):
    # The shared identity gate's details do not become an existence oracle.
    try:
        return _lock_actor(database_alias="default", credential=credential, unavailable=DiscussionUnavailable)
    except DiscussionUnavailable:
        raise DiscussionUnavailable() from None


def _execute(actor, *, organism_uuid, circle_uuid, operation, request_uuid, **fields):
    board = _board_id(organism_uuid, circle_uuid)
    payload = {"organism": _uuid(organism_uuid), "circle": _uuid(circle_uuid) if circle_uuid is not None else None,
               "board": str(board), "operation": operation, "request": _uuid(request_uuid), **fields}
    stamp = _rows("SELECT statement_timestamp() AS stamp")[0]["stamp"]
    result = _rows("SELECT public.disc_execute(%s::jsonb,%s,%s,%s) AS result",
                   [json.dumps(payload), actor.pk, actor.access_epoch, stamp])[0]["result"]
    return {key: UUID(value) if key in {"board", "act"} else value for key, value in result.items()}


def _command(credential, **args):
    try:
        with transaction.atomic():
            actor = _actor(credential)
            return _execute(actor, **args)
    except IntegrityError as exc:
        translate_refusal(exc)


def _ensure_board(actor, *, organism_uuid, circle_uuid, request_uuid):
    result = _execute(actor, organism_uuid=organism_uuid, circle_uuid=circle_uuid,
                      operation="PROVISION", request_uuid=request_uuid)
    # Circle Welcome is never a provisioning side effect (HG P2c R1).
    if circle_uuid is None:
        facts = _rows("""SELECT public.disc_probation_open(b.organism_id,statement_timestamp()) AS allowed,
            public.disc_office(b.organism_id,NULL,%s,statement_timestamp()) IS NOT NULL AS coordinator,
            EXISTS(SELECT FROM public.core_disccategory c WHERE c.board_uuid=b.board_uuid
                AND c.functional_code='WELCOME') AS welcomed
            FROM public.core_discboard b WHERE b.board_uuid=%s""", [actor.pk, result["board"]])[0]
        if facts["allowed"] and facts["coordinator"] and not facts["welcomed"]:
            _execute(actor, organism_uuid=organism_uuid, circle_uuid=None,
                     operation="WELCOME", request_uuid=uuid5(request_uuid, "welcome"))
    return result


def ensure_board(*, credential, organism_uuid, circle_uuid=None, request_uuid):
    """Provision universal defaults; only an LO board can also provision Welcome."""
    try:
        with transaction.atomic():
            return _ensure_board(_actor(credential), organism_uuid=organism_uuid,
                                 circle_uuid=circle_uuid, request_uuid=request_uuid)
    except IntegrityError as exc:
        translate_refusal(exc)


def open_circle_welcome(*, credential, organism_uuid, circle_uuid, request_uuid):
    """Deliberate local OFFICE act, requiring LO probation permission (D7)."""
    _uuid(circle_uuid)
    _uuid(request_uuid)
    try:
        with transaction.atomic():
            actor = _actor(credential)
            _ensure_board(actor, organism_uuid=organism_uuid, circle_uuid=circle_uuid,
                          request_uuid=uuid5(request_uuid, "board"))
            return _execute(actor, organism_uuid=organism_uuid, circle_uuid=circle_uuid,
                            operation="WELCOME", request_uuid=request_uuid)
    except IntegrityError as exc:
        translate_refusal(exc)


def set_probation_access(*, credential, organism_uuid, enabled, request_uuid):
    """LO forum permission, not admission or an SE grant; one actor acquisition."""
    _uuid(request_uuid)
    if type(enabled) is not bool:
        raise DiscussionRefused(code="DISC_INPUT")
    try:
        with transaction.atomic():
            actor = _actor(credential)
            _ensure_board(actor, organism_uuid=organism_uuid, circle_uuid=None,
                          request_uuid=uuid5(request_uuid, "board"))
            result = _execute(actor, organism_uuid=organism_uuid, circle_uuid=None,
                              operation="PROBATION_ACCESS", request_uuid=request_uuid, enabled=enabled)
            if enabled:
                _ensure_board(actor, organism_uuid=organism_uuid, circle_uuid=None,
                              request_uuid=uuid5(request_uuid, "welcome-board"))
            return result
    except IntegrityError as exc:
        translate_refusal(exc)


def _category_fields(name, description, order, access):
    if (type(name) is not str or not name.strip() or not 1 <= len(name) <= 45
            or type(description) is not str or type(order) is not int
            or not -(2**31) <= order < 2**31 or type(access) is not str or access not in {"COORDINATORS", "MEMBERS", "PROBATIONARY"}):
        raise DiscussionRefused(code="DISC_INPUT")
    return {"name": name, "description": description, "order": order, "access": access}


def create_category(*, credential, organism_uuid, category_uuid, request_uuid, name,
                    description="", order=0, access="MEMBERS", circle_uuid=None):
    return _command(credential, organism_uuid=organism_uuid, circle_uuid=circle_uuid,
                    operation="CREATE_CATEGORY", request_uuid=request_uuid, category=_uuid(category_uuid),
                    **_category_fields(name, description, order, access))


def revise_category(*, credential, organism_uuid, category_uuid, predecessor, request_uuid,
                    name, description, order, access, circle_uuid=None):
    return _command(credential, organism_uuid=organism_uuid, circle_uuid=circle_uuid,
                    operation="REVISE_CATEGORY", request_uuid=request_uuid, category=_uuid(category_uuid),
                    predecessor=_uuid(predecessor), **_category_fields(name, description, order, access))


def retire_category(*, credential, organism_uuid, category_uuid, predecessor, request_uuid, circle_uuid=None):
    return _command(credential, organism_uuid=organism_uuid, circle_uuid=circle_uuid,
                    operation="RETIRE_CATEGORY", request_uuid=request_uuid, category=_uuid(category_uuid),
                    predecessor=_uuid(predecessor))


_NO_CATEGORY = object()


def _read_owner(organism_uuid, circle_uuid):
    """After authentication, validate owner inputs before any object lookup."""
    _read_uuid(organism_uuid)
    if circle_uuid is not None:
        _read_uuid(circle_uuid)


def _audience(actor, organism_uuid, circle_uuid, category_uuid=_NO_CATEGORY):
    # Omitted category is valid for a board read; explicit None is not a UUID.
    _read_owner(organism_uuid, circle_uuid)
    if category_uuid is not _NO_CATEGORY:
        _read_uuid(category_uuid)
    query = """SELECT b.*,public.disc_member_state(b.organism_id,%s,statement_timestamp()) AS member_state,
        public.disc_office(b.organism_id,b.circle_id,%s,statement_timestamp()) AS office
        FROM public.core_discboard b JOIN public.core_livingorganism lo ON lo.id=b.organism_id
        LEFT JOIN public.core_circle c ON c.id=b.circle_id
        WHERE lo.organism_id=%s AND c.circle_uuid IS NOT DISTINCT FROM %s::uuid
          AND (public.disc_audience(b.organism_id,b.circle_id,%s,'MEMBERS',statement_timestamp())
            OR public.disc_audience(b.organism_id,b.circle_id,%s,'PROBATIONARY',statement_timestamp()))"""
    params = [actor.pk, actor.pk, organism_uuid, circle_uuid, actor.pk, actor.pk]
    rows = _rows(query, params)
    if not rows:
        raise DiscussionUnavailable()
    _lock_registered_organism("default", organism_uuid)
    rows = _rows(query, params)
    if not rows:
        raise DiscussionUnavailable()
    return rows[0]


def board_categories(*, credential, organism_uuid, circle_uuid=None):
    """Pure read, including retired categories; provision through ensure_board first."""
    with transaction.atomic():
        actor = _actor(credential)
        board = _audience(actor, organism_uuid, circle_uuid)
        rows = _rows("""SELECT c.*,public.disc_member_state(b.organism_id,%s,statement_timestamp())='ACTIVE'
              OR public.disc_member_state(b.organism_id,%s,statement_timestamp())='PROBATIONARY' AS posting_state,
            public.disc_office(b.organism_id,b.circle_id,%s,statement_timestamp()) IS NOT NULL AS coordinator
            FROM public.disc_categories c JOIN public.core_discboard b ON b.board_uuid=c.board_uuid
            WHERE b.board_uuid=%s AND public.disc_audience(b.organism_id,b.circle_id,%s,c.access_level,statement_timestamp())
            ORDER BY c.sort_order,c.name,c.category_uuid""", [actor.pk, actor.pk, actor.pk, board["board_uuid"], actor.pk])
        for row in rows:
            allowed = row.pop("posting_state") and not row["retired"]
            coordinator = row.pop("coordinator")
            row["can_reply"] = allowed
            row["can_start_topic"] = allowed and (row["functional_code"] != "ANNOUNCEMENTS" or coordinator)
        return {"board": board["board_uuid"], "categories": rows,
                "subscriber_categories": entitlement_effective(organism_uuid, CAPABILITY)}


def category_history(*, credential, organism_uuid, category_uuid, circle_uuid=None):
    with transaction.atomic():
        actor = _actor(credential)
        board = _audience(actor, organism_uuid, circle_uuid, category_uuid)
        rows = _rows("""SELECT v.*,a.actor_id,a.authority_source,a.received_at,a.effective_at,a.recorded_at,
            a.fingerprint,e.lineage_reference FROM public.disc_categories current
            JOIN public.core_discboard b ON b.board_uuid=current.board_uuid
            JOIN public.core_disccategoryversion v ON v.category_uuid=current.category_uuid
            JOIN public.core_discact a ON a.act_uuid=v.act_uuid
            JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
            WHERE current.category_uuid=%s AND current.board_uuid=%s
              AND public.disc_audience(b.organism_id,b.circle_id,%s,current.access_level,statement_timestamp())
            ORDER BY v.version_number""", [category_uuid, board["board_uuid"], actor.pk])
        if not rows:
            raise DiscussionUnavailable()
        return rows
