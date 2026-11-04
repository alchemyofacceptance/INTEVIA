"""Append C1 outcomes to installed chains, retaining their commitment preimages."""

from uuid import uuid4

from django.db import connections

from src.intevia.services.organism_authority import authority_target_fingerprint
from src.intevia.services.organism_command_support import _resulting_temporal_basis


def append_relationship_event(
    *,
    alias,
    anchor,
    head,
    action,
    state,
    actor,
    basis_id,
    decision_reference,
    evidence,
    command,
    fingerprint,
    received_at,
    composing_rule=None,
    until=None,
    invalidation_kind=None,
):
    from core.models import AuthorityBasis, DeterminationContestCase, LivingOrganism, MembershipCondition

    table, fk = {
        AuthorityBasis: ("core_authorityinvalidationevent", "basis_id"),
        LivingOrganism: ("core_livingorganismevent", "living_organism_id"),
        MembershipCondition: ("core_membershipconditionstateevent", "condition_id"),
        DeterminationContestCase: ("core_determinationcontestevent", "case_id"),
    }[type(anchor)]
    event = uuid4()
    composed = composing_rule is not None
    effective_at = command.effective_at
    kind, reference = _resulting_temporal_basis(
        payload_fingerprint=fingerprint,
        effective_at=effective_at,
        received_at=received_at,
    )
    with connections[alias].cursor() as cursor:
        cursor.execute(
            "SELECT public.s015_0022_digest('s015p1', public.s015_0022_parts_preimage(%s,%s))",
            [table, event],
        )
        (parts,) = cursor.fetchone()
        previous = None
        if head is not None:
            cursor.execute(
                f"SELECT l1_commitment FROM public.{table} WHERE id=%s", [head.pk]
            )
            (previous,) = cursor.fetchone()
        prior = None
        if head is not None:
            cursor.execute(
                f"SELECT resulting_state FROM public.{table} WHERE {fk}=%s "
                "AND (effective_at,occurred_at,received_at) <= (%s,%s,%s) "
                "ORDER BY effective_at DESC,occurred_at DESC,received_at DESC,recorded_at DESC,"
                "sequence DESC,event_uuid DESC LIMIT 1",
                [anchor.pk, effective_at, command.occurred_at, received_at],
            )
            row = cursor.fetchone()
            prior = row[0] if row else None
        columns = {
            fk: anchor.pk,
            "event_uuid": event,
            "sequence": head.sequence + 1 if head else 1,
            "predecessor_id": head.pk if head else None,
            "predecessor_sequence": head.sequence if head else None,
            "action": action,
            "prior_state": prior,
            "resulting_state": state,
            "actor_state": "NONE" if composed else "PARTY",
            "actor_id": None if composed else actor.pk,
            "actor_access_epoch": None if composed else actor.access_epoch,
            "actor_capacity": "NA_NO_PARTY" if composed else "STANDING",
            "enterer_identity_id": actor.pk,
            "entry_mode": "COMPOSED" if composed else "TRANSPORT",
            "composing_rule_state": "REFERENCE" if composed else "NA_TRANSPORT",
            "composing_rule_reference": composing_rule,
            "composer_state": "REFERENCE" if composed else "NA_TRANSPORT",
            "composer_identity_id": actor.pk if composed else None,
            "effective_until_state": "FIXED" if until else "NONE",
            "effective_until": until,
            "authority_basis_id": basis_id,
            "authority_decision_reference": decision_reference,
            "evidence_reference": evidence,
            "request_reference": command.request_reference,
            "idempotency_key": command.idempotency_key,
            "payload_fingerprint": fingerprint,
            "lineage_reference": "s015l1:"
            + authority_target_fingerprint(
                {"event": str(event), "request": fingerprint}
            ),
            "temporal_basis_kind": kind,
            "temporal_basis_reference": reference,
            "effective_at": effective_at,
            "occurred_at": command.occurred_at,
            "received_at": received_at,
            "predecessor_l1_commitment": previous,
            "parts_commitment": parts,
        }
        if type(anchor) is AuthorityBasis:
            columns["kind"] = invalidation_kind
        cursor.execute(
            f"INSERT INTO public.{table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})",
            list(columns.values()),
        )
        if type(anchor) is LivingOrganism:
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache('core_livingorganism',id,"
                "greatest(current_state_effective_at,least(%s,clock_timestamp())),clock_timestamp()) "
                "FROM public.core_livingorganism WHERE id=%s",
                [effective_at, anchor.pk],
            )
    return event
