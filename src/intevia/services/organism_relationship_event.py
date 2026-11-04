"""Command adapter for the shared S015 event writer installed by C1-16."""
import json
from uuid import uuid4

from django.db import connections


def append_relationship_event(
    *, alias, anchor, head, action, state, actor, basis_id, decision_reference,
    evidence, command, fingerprint, received_at, composing_rule=None, until=None,
    invalidation_kind=None,
):
    """Append using the command's checked head and return its event UUID."""
    from core.models import AuthorityBasis, DeterminationContestCase, LivingOrganism, MembershipCondition

    token = {
        AuthorityBasis: "core_authoritybasis",
        LivingOrganism: "core_livingorganism",
        MembershipCondition: "core_membershipcondition",
        DeterminationContestCase: "core_determinationcontestcase",
    }[type(anchor)]
    metadata = {"kind": invalidation_kind} if type(anchor) is AuthorityBasis else {}
    event = uuid4()
    with connections[alias].cursor() as cursor:
        cursor.execute("""
            SELECT public.s015_append_event(
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                %s,%s,%s::integer,%s,%s,%s)
        """, [token, anchor.pk, action, state, command.effective_at, command.occurred_at, received_at,
              actor.pk, basis_id, decision_reference, evidence, command.request_reference, fingerprint,
              json.dumps(metadata), until, event, head.pk if head else None, head.sequence if head else None,
              actor.access_epoch if composing_rule is None else None, composing_rule, command.idempotency_key])
    return event
