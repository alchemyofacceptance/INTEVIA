"""Circle-owned work, its authority and the authorised parent's waiting-work read.

An opening is the immutable work item on its Circle's chain. Its title, original
instrument and required SERVICE role/version are fixed when the item is issued.
"""

import json
from hashlib import sha256

from django.db import connections

from core import models
from src.intevia.services import circle_commands as commands
from src.intevia.services import circle_participation as places
from src.intevia.services.organism_command_support import _canonical_key


def opened_reference(title, role):
    body = json.dumps(
        [
            _canonical_key(title, "work", 200),
            _canonical_key(role, "service role version", 200),
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return _canonical_key(body, "work description and role", 255)


def open_work(
    *,
    credential,
    organism_uuid,
    circle_uuid,
    work_uuid,
    title,
    role_reference,
    alias="default",
):
    ref = opened_reference(title, role_reference)
    with commands.command_context(alias, credential, organism_uuid) as (lo, actor, at):
        circle = commands.target_circle(alias, lo, circle_uuid)
        past = (
            models.CircleStateEvent.objects.using(alias)
            .filter(event_uuid=work_uuid)
            .first()
        )
        if past:
            if (
                past.circle_id,
                past.actor_id,
                past.action,
                past.evidence_reference,
            ) == (circle.pk, actor.pk, "OPEN_WORK", ref):
                return past.event_uuid
            commands.unavailable()
        if circle.state == "CLOSED":
            raise places.CircleAccessRefused("This Circle has dissolved. Its work history is closed.")
        _, source = commands.evidence(
            alias,
            lo,
            circle_uuid,
            "OPEN_WORK:" + str(work_uuid) + ":" + sha256(ref.encode()).hexdigest(),
            actor,
            actor,
            at,
        )
        return commands.write_event(
            alias, circle, actor, "OPEN_WORK", circle.state, source, ref, work_uuid, at
        )


def work_row(alias, circle, work_uuid):
    work = (
        models.CircleStateEvent.objects.using(alias)
        .filter(circle=circle, event_uuid=work_uuid, action="OPEN_WORK")
        .first()
    )
    if not work:
        commands.unavailable()
    return work


def latest_claim(alias, circle, work_uuid):
    return (
        models.CircleStateEvent.objects.using(alias)
        .filter(
            circle=circle,
            action="CLAIM_WORK",
            evidence_reference="work:" + str(work_uuid),
        )
        .order_by("-sequence")
        .first()
    )


def _still_claimed(alias, lo, circle, prior, holders, at):
    if prior is None:
        return False
    if circle.kind == "GENERIC":
        with connections[alias].cursor() as cursor:
            cursor.execute("SELECT public.circle_claim_current(%s,%s,%s,%s)", [circle.pk, prior.pk, at, at])
            return cursor.fetchone()[0]
    return models.OrganismMembership.objects.using(alias).filter(
        living_organism=lo, identity_id=prior.actor_id, membership_uuid__in=holders).exists()


def claim_work(
    *, credential, organism_uuid, circle_uuid, work_uuid, request_uuid, alias="default"
):
    with commands.command_context(alias, credential, organism_uuid) as (lo, actor, at):
        circle = commands.target_circle(alias, lo, circle_uuid)
        holders = places.coordinator_memberships(alias, circle, at, at)
        member = (
            models.OrganismMembership.objects.using(alias)
            .filter(living_organism=lo, identity=actor, membership_uuid__in=holders)
            .first()
        )
        if member is None:
            commands.unavailable()
        work = work_row(alias, circle, work_uuid)
        prior = latest_claim(alias, circle, work_uuid)
        if prior and prior.event_uuid == request_uuid and prior.actor_id == actor.pk:
            return prior.event_uuid
        if models.CircleStateEvent.objects.using(alias).filter(event_uuid=request_uuid).exists():
            commands.unavailable()
        if circle.state != "ACTIVE":
            raise places.CircleAccessRefused(
                "The Circle must complete qualified activation before this work can be claimed."
            )
        if _still_claimed(alias, lo, circle, prior, holders, at):
            raise places.CircleAccessRefused(
                "A current Circle Coordinator already holds this work. Check its assignment."
            )
        _, role = json.loads(work.evidence_reference)
        finding = places.qualified_service_role(
            alias, circle, member, role, work.authority_basis, at, at
        )
        return commands.write_event(
            alias,
            circle,
            actor,
            "CLAIM_WORK",
            "ACTIVE",
            "s015d1:" + finding.scope_fingerprint,
            "work:" + str(work_uuid),
            request_uuid,
            at,
        )


def parent_work(*, credential, organism_uuid, circle_uuid, work_uuid, alias="default"):
    """Current, narrowly authorised parent read. Looking at a held task grants no execution right."""
    with commands.command_context(alias, credential, organism_uuid) as (lo, actor, at):
        circle = commands.target_circle(alias, lo, circle_uuid)
        if circle.kind == "GENERIC":
            commands.lo_source(alias, lo, circle_uuid, "READ_WORK:" + str(work_uuid), actor, at)
        else:
            commands.evidence(alias, lo, circle_uuid, "READ_WORK:" + str(work_uuid), actor, actor, at)
        work = work_row(alias, circle, work_uuid)
        holders = places.coordinator_memberships(alias, circle, at, at)
        prior = latest_claim(alias, circle, work_uuid)
        status = "WAITING_FOR_PARENT_GOVERNANCE"
        if circle.kind == "GENERIC" and circle.state == "CLOSED":
            status = "CLOSED_UNFINISHED"
        elif holders and circle.state == "ACTIVE":
            status = "AVAILABLE"
            if _still_claimed(alias, lo, circle, prior, holders, at):
                status = "CLAIMED"
        return {
            "circle": circle.circle_uuid,
            "work": work.event_uuid,
            "title": json.loads(work.evidence_reference)[0],
            "status": status,
            "rule": work.authority_decision_reference,
        }
