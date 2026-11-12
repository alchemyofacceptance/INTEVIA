"""The CQ consumer of the TIMED EVENTS engine: the rule acts a CQ thread's deadlines owe.

Each purpose is the landed rule act for one head, made with the request key the
landed catch-up uses (``uuid5(head, "DISC2:<suffix>")``), so the engine and a manual
caller meet on the same key and the same act. The key is tried, without waiting,
before the LO, in the landed order. The act is made through the CQ service's public
entry for rule acts (``cq.record_rule_act``), on the engine's own connection.

PREPARATION_DUE and RESPONSE_DUE are registered in the engine's registry (migration
0093). SETTLE is not: a settlement that applies makes its implementation in the same
transaction, and that implementation takes locks the engine cannot try without
waiting. It stays on its landed route (``cq.catch_up``). The code for it here is
unreachable until a migration admits the purpose.
"""

from uuid import UUID, uuid5

from django.db import IntegrityError, transaction

from core.models import Identity
from src.intevia.services import cq
from src.intevia.services import timed_events as te

# purpose -> (request key suffix, the argument that names the head, or None)
ACTS = {
    "PREPARATION_DUE": ("preparation-due", None),
    "RESPONSE_DUE": ("response-due", "window"),
    "SETTLE": ("settle", "ballot"),
}
# Refusals a blocked election gives when its ballot is settled. The landed catch-up
# passes over an item refused with one of these; so does the engine (read at source,
# services/cq.py, catch_up).
BLOCKED_ELECTION = frozenset(cq.ELECTION_METHOD_MESSAGES) | {
    "ELECT_EMPTY", "ELECT_CHANGED", "ELECT_CITATION", "ELECT_OCCUPIED", "ELECT_TERM", "ELECT_HANDOVER",
    "ELECT_HANDOVER_UNKNOWN", "ELECT_GOVERNANCE", "ELECT_VACANCY_BOARD", "ELECT_VACANCY_LINK",
    "ELECT_VACANCY_NOMINEE", "ELECT_VACANCY_ROUTE", "ELECT_VACANCY_HOLD"}
# Refusals the landed CQ writer and its completeness check give for a rule act.
RULE_ACT_REFUSALS = ("DISC_UNAVAILABLE", "CQ_PREPARATION", "CQ_RESPONSE_DUE", "CQ_REQUEST", "CQ_COMPLETE",
                     "CQ_RESTRICTED", "CQ_PROBATION", "CQ_INPUT")


def _key(purpose, occurrence):
    return uuid5(UUID(occurrence), "DISC2:" + ACTS[purpose][0])


def try_request_key(*, alias, organism_id, item):
    """Before the LO: try the landed request lock for this act's key. False means a caller holds it."""
    key = _key(item["purpose"], item["occurrence"])
    return bool(te._rows(alias, "SELECT pg_try_advisory_xact_lock(hashtextextended('DISC1:request:'||%s::text,0)) AS held",
                         [str(key)])[0]["held"])


def rule_act(*, alias, organism_id, enterer_id, purpose, source_ref, occurrence, due_at, known):
    """Make the rule act this head owes, as the landed catch-up makes it."""
    head = te._rows(alias, """SELECT lo.organism_id AS owner, h.board_uuid, h.thread_uuid
        FROM public.cq_phases h JOIN public.core_livingorganism lo ON lo.id=h.organism_id
        WHERE h.organism_id=%s AND h.thread_uuid::text=%s AND h.act_uuid::text=%s""",
                    [organism_id, source_ref, occurrence])
    if not head:
        return te.Opening(te.NOT_DUE)
    key = _key(purpose, occurrence)
    named = ACTS[purpose][1]
    actor = Identity.objects.using(alias).get(pk=enterer_id)
    try:
        with transaction.atomic(using=alias):
            cq.record_rule_act(alias=alias, actor=actor, organism_uuid=head[0]["owner"], board_uuid=head[0]["board_uuid"],
                               thread_uuid=head[0]["thread_uuid"], operation=purpose, key=key,
                               args={named: occurrence} if named else {}, received=known)
    except IntegrityError as error:
        code = getattr(getattr(error.__cause__, "diag", None), "message_primary", None)
        if purpose == "SETTLE" and code in BLOCKED_ELECTION:
            return te.Opening(te.BLOCKED, code=code)
        raise
    # A settlement another route made has its own id; it is matched by its ballot.
    effect = te._rows(alias, """SELECT a.act_uuid::text AS effect, public.te_made_here('core_cqact',a.act_uuid::text) AS made
        FROM public.core_cqact a WHERE a.organism_id=%s AND a.operation=%s
        AND (a.act_uuid=%s OR (%s='SETTLE' AND a.request->>'ballot'=%s))""",
                      [organism_id, purpose, key, purpose, occurrence])
    if not effect:
        return te.Opening(te.NOT_DUE)
    return te.Opening(te.OPENED if effect[0]["made"] else te.FOUND_DONE, effect_ref=effect[0]["effect"])


for _purpose in ACTS:
    te.register("CQ", _purpose, rule_act, before_lo=try_request_key,
                refusals=(*RULE_ACT_REFUSALS, *(BLOCKED_ELECTION if _purpose == "SETTLE" else ())))
