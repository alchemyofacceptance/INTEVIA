"""Published v1.0 condition rules and the evidence read by their production authority."""

from base64 import b64decode, urlsafe_b64encode
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.db import connections
from core.models import ContextualRoleAssignmentStateEvent, OrganismMembership
from src.intevia.services.organism_membership_periods import period_at

MENTORSHIP_RULE = UUID("ff39b6e1-d171-4d4b-8dda-48be14f8a624")
PROBATION_RULE = UUID("c90880a2-3cbd-493e-a5fa-f76f51a29dd4")
RULES = {"MENTORSHIP": MENTORSHIP_RULE, "PROBATION": PROBATION_RULE}
RULE_VERSION = 1


def encoded(data):
    return urlsafe_b64encode(data).decode("ascii").rstrip("=")


def instant(value):
    micros = (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    return micros.to_bytes(8, "big", signed=True)


def decode_reference(reference):
    """Decode the recording decision's q fact: activation:1:facts:cuts.

    Facts and cuts are unpadded base64url. Facts start with four 16-byte UUIDs
    in network order: rule, Coordinator assignment, assignment head, subject
    period start. A one-byte flag (0/1) precedes the optional mentor period UUID.
    BY_ENTITLEMENT_V1_0 then adds the grant UUID and its effective instant.
    Cuts are request effective, receipt and execution instants. All instants are
    signed big-endian int64 microseconds since the UTC Unix epoch. An older
    probation reference can contain a mentor even though new records cannot.
    """
    activation, version, facts_text, cuts_text = reference.split(":")
    if version != "1" or activation not in {"BY_DEFAULT_V1_0", "BY_ENTITLEMENT_V1_0"}:
        raise ValueError("Unknown condition reference activation or layout version.")

    def unpad_decode(text):
        data = b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
        if encoded(data) != text:
            raise ValueError("Invalid base64url in condition reference.")
        return data

    def as_time(raw):
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            microseconds=int.from_bytes(raw, "big", signed=True)
        )

    facts = unpad_decode(facts_text)
    cuts = unpad_decode(cuts_text)
    if len(facts) < 65 or facts[64] not in (0, 1) or len(cuts) != 24:
        raise ValueError("Invalid condition reference layout.")
    has_mentor = bool(facts[64])
    has_grant = activation == "BY_ENTITLEMENT_V1_0"
    grant_offset = 81 if has_mentor else 65
    if len(facts) != grant_offset + (24 if has_grant else 0) or (
        not has_grant and not has_mentor
    ):
        raise ValueError("Condition reference fields do not match the activation.")
    return {
        "activation": activation,
        "layout_version": 1,
        "rule_uuid": UUID(bytes=facts[:16]),
        "assignment_uuid": UUID(bytes=facts[16:32]),
        "head_event_uuid": UUID(bytes=facts[32:48]),
        "subject_start": UUID(bytes=facts[48:64]),
        "mentor_start": UUID(bytes=facts[65:81]) if has_mentor else None,
        "entitlement_uuid": UUID(bytes=facts[grant_offset : grant_offset + 16])
        if has_grant
        else None,
        "entitlement_effective_at": as_time(facts[-8:]) if has_grant else None,
        "effective_at": as_time(cuts[:8]),
        "received_at": as_time(cuts[8:16]),
        "executed_at": as_time(cuts[16:]),
    }


def probation_grant(alias, organism_id, at, known):
    """Identify the granting row using 0046's eligibility predicate and the knowledge cut."""
    with connections[alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT entitlement_uuid, effective_at FROM public.core_organismentitlement
             WHERE living_organism_id=%s AND capabilities ? 'PROBATION'
               AND recorded_at<=%s AND effective_at<=%s
               AND (effective_until IS NULL OR %s<effective_until OR
                    (source='PAID' AND %s<effective_until+interval '7 days'))
             ORDER BY effective_at DESC, recorded_at DESC, entitlement_uuid LIMIT 1
        """,
            [organism_id, known, at, at, at],
        )
        return cursor.fetchone()


class V10ConditionQualificationSource:
    def __init__(self, provider):
        self.provider = provider

    def reference_at(self, request, *, effective_at, known_at):
        from src.intevia.services.organism_application_authority import (
            _fold_state,
            _latest_transition,
        )

        alias = self.provider.database_alias
        target = request.target
        kind = target["kind"]
        rule = RULES[kind]
        if (
            target.get("rule_uuid") != str(rule)
            or target.get("rule_version") != RULE_VERSION
            or target.get("capacity") != "LO_COORDINATOR"
        ):
            return None
        members = OrganismMembership.objects.using(alias).filter(
            living_organism__organism_id=request.living_organism_id
        )
        actor = members.filter(identity_id=request.actor_pk).first()
        if (
            not actor
            or _fold_state(
                alias, "core_organismmembership", actor.pk, effective_at, known_at
            )
            != "ACTIVE"
        ):
            return None
        cut = replace(request, effective_at=effective_at, evaluated_at=known_at)
        office = self.provider._effective_coordinator_office(cut, actor)
        if office is None:
            return None
        head = _latest_transition(
            alias,
            ContextualRoleAssignmentStateEvent,
            "assignment_id",
            office.pk,
            effective_at,
            known_at,
        )
        subject = members.filter(membership_uuid=target["subject_uuid"]).first()
        if not subject:
            return None
        subject_start = period_at(subject.pk, effective_at, known_at, alias)
        if not subject_start or str(subject_start) != target.get("subject_start"):
            return None
        mentor_start = None
        if kind == "MENTORSHIP":
            mentor = members.filter(membership_uuid=target["mentor_uuid"]).first()
            if (
                not mentor
                or mentor.pk == subject.pk
                or _fold_state(
                    alias, "core_organismmembership", mentor.pk, effective_at, known_at
                )
                != "ACTIVE"
            ):
                return None
            mentor_start = period_at(mentor.pk, effective_at, known_at, alias)
            if not mentor_start or str(mentor_start) != target.get("mentor_start"):
                return None
        # Fixed-width identifiers keep the complete, reversible reference below the
        # installed 255-character authority-reference limit without changing schema.
        fields = (
            rule.bytes
            + office.assignment_uuid.bytes
            + head.event_uuid.bytes
            + subject_start.bytes
        )
        fields += bytes([mentor_start is not None]) + (
            mentor_start.bytes if mentor_start else b""
        )
        activation = "BY_DEFAULT_V1_0"
        if kind == "PROBATION":
            if (
                _fold_state(
                    alias, "core_organismmembership", subject.pk, effective_at, known_at
                )
                != "PROBATIONARY"
            ):
                return None
            grant = probation_grant(
                alias, actor.living_organism_id, effective_at, known_at
            )
            if grant is None:
                return None
            activation = "BY_ENTITLEMENT_V1_0"
            fields += grant[0].bytes + instant(grant[1])
        return f"{activation}:1:{encoded(fields)}"
