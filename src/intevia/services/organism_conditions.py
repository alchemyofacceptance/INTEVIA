"""Period-bound offers, mentor responses and Coordinator-recorded probation."""

from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.db.models import Q

from core.models import (
    AuthorityDecision,
    Identity,
    IntakeEntry,
    LivingOrganism,
    MembershipCondition,
    MembershipConditionStateEvent,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from email_foundation.contracts import MailRefused, Origin, templates
from email_foundation.service import request_message
from src.intevia.services.organism_application_authority import _fold_state
from src.intevia.services.clock import database_now
from src.intevia.services.command_intake import (
    IntakeShape,
    _resolve_actor,
    admit_request,
)
from src.intevia.services.organism_authority import authority_target_fingerprint
from src.intevia.services.organism_command_support import (
    ReplayViewer,
    S015CommandReceiptMalformed,
    _OrganismCommandService,
    _canonical_key,
    _canonical_uuid,
    _lock_actor,
    _lock_actor_membership_context,
    _lock_registered_organism,
    _validate_timestamp,
    acquire_organism_command_mutex,
    authority_atomic,
)
from src.intevia.services.organism_membership_periods import bound_periods, period_at
from src.intevia.services.organism_relationship_event import append_relationship_event


class ConditionRefused(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ConditionCommand:
    credential: object
    organism_uuid: UUID
    subject_uuid: UUID
    subject_start: UUID
    kind: str
    rule_uuid: UUID
    rule_version: int
    capacity: str
    evidence_reference: str
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    mentor_uuid: UUID | None = None
    mentor_start: UUID | None = None
    replaces: UUID | None = None
    cq_outcome_uuid: UUID | None = None


@dataclass(frozen=True, slots=True)
class AcceptMentorOfferCommand:
    credential: object
    organism_uuid: UUID
    condition_uuid: UUID
    mentor_start: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime


@dataclass(frozen=True, slots=True)
class DeclineMentorOfferCommand(AcceptMentorOfferCommand):
    """Declining is a distinct request even when every supplied field is identical."""


TERMINAL = {"SUPERSEDED", "ENDED", "DECLINED", "OFFER_CLOSED"}
RESPONSES = {AcceptMentorOfferCommand: "ACCEPT", DeclineMentorOfferCommand: "DECLINE"}


def condition_shape(command):
    if type(command) in RESPONSES:
        target = {
            key: str(_canonical_uuid(getattr(command, key), key))
            for key in ("organism_uuid", "condition_uuid", "mentor_start")
        }
        target.update(
            operation=RESPONSES[type(command)],
            kind="MENTORSHIP",
            action=OrganismAction.TRANSITION_MEMBERSHIP_CONDITION.value,
        )
        return IntakeShape(
            _canonical_key(command.idempotency_key, "idempotency_key", 120),
            _canonical_key(command.request_reference, "request_reference", 128),
            target,
            {
                key: _validate_timestamp(getattr(command, key), key)
                for key in ("occurred_at", "effective_at")
            },
            (),
            None,
        )
    if type(command) is not ConditionCommand:
        raise TypeError("A condition command is required.")
    if (
        command.kind not in {"MENTORSHIP", "PROBATION"}
        or type(command.rule_version) is not int
        or command.rule_version < 1
    ):
        raise ConditionRefused(
            "The condition type or rule version is invalid. No condition changed. Check the rule."
        )
    if command.kind == "PROBATION" and (
        command.mentor_uuid is not None or command.mentor_start is not None
    ):
        raise ConditionRefused(
            "A probation record cannot name a mentor. No condition changed. Offer a mentorship separately."
        )
    target = {
        "action": OrganismAction.TRANSITION_MEMBERSHIP_CONDITION.value,
        "kind": command.kind,
        "rule_version": command.rule_version,
        "capacity": _canonical_key(command.capacity, "capacity", 96),
        "evidence_reference": _canonical_key(
            command.evidence_reference, "evidence_reference", 255
        ),
    }
    for key in (
        "organism_uuid",
        "subject_uuid",
        "subject_start",
        "rule_uuid",
        "mentor_uuid",
        "mentor_start",
        "replaces",
        "cq_outcome_uuid",
    ):
        value = getattr(command, key)
        if (
            key in {"mentor_uuid", "mentor_start", "replaces", "cq_outcome_uuid"}
            and value is None
        ):
            target[key] = None
        else:
            target[key] = str(_canonical_uuid(value, key))
    if (command.mentor_uuid is None) != (command.mentor_start is None):
        raise ConditionRefused(
            "A mentor must name one membership and its period. No condition changed. Check both references."
        )
    return IntakeShape(
        _canonical_key(command.idempotency_key, "idempotency_key", 120),
        _canonical_key(command.request_reference, "request_reference", 128),
        target,
        {
            key: _validate_timestamp(getattr(command, key), key)
            for key in ("occurred_at", "effective_at")
        },
        (),
        None,
    )


def mentor_notices(condition, event, recipients, name):
    """Request once per derived recipient, within the originating transaction."""
    if (name, 1) not in templates():
        return "UNREGISTERED"
    statuses = []
    for recipient in recipients:
        try:
            with transaction.atomic():
                intent = request_message(
                    category="NOTICE",
                    # The caller already holds the organism lock. Until I-3 has
                    # a compatible recipient check, EM-a always refuses here
                    # before taking any recipient lock.
                    organism_lock_held=True,
                    recipient=recipient,
                    template=name,
                    version=1,
                    context={"record_id": str(condition.condition_uuid)},
                    origin=Origin(
                        f"condition-event:{event}",
                        condition.subject_membership.living_organism.organism_id,
                    ),
                    request_key=f"c1b:{event}:{recipient.identity_id}",
                )
        except MailRefused:
            statuses.append("REFUSED")
        else:
            statuses.append(f"REQUESTED:{intent.intent_id}")
    return ",".join(statuses)


@dataclass(frozen=True)
class ConditionResult:
    condition_uuid: str
    event_uuid: str
    authority_decision_reference: str
    lineage_reference: str
    notice: str

    def receipt_payload(self):
        return asdict(self)

    @classmethod
    def replay_field_audiences(cls, result):
        # Only opaque identifiers and the status of the request made by this actor.
        return {}


class MembershipConditionService(_OrganismCommandService):
    _action = OrganismAction.TRANSITION_MEMBERSHIP_CONDITION
    _authority_unavailable = ConditionRefused
    _not_found = ConditionRefused

    def _replay_result(self, payload):
        try:
            result = ConditionResult(**payload)
            UUID(result.event_uuid)
            UUID(result.condition_uuid)
            return result
        except (ValueError, TypeError, AttributeError) as exc:
            raise S015CommandReceiptMalformed() from exc

    def _verify_replay_result(self, result):
        if (
            not MembershipConditionStateEvent.objects.using(self._alias)
            .filter(
                condition__condition_uuid=result.condition_uuid,
                event_uuid=result.event_uuid,
                lineage_reference=result.lineage_reference,
                authority_decision_reference=result.authority_decision_reference,
            )
            .exists()
        ):
            raise S015CommandReceiptMalformed()

    def record(self, command):
        if type(command) is not ConditionCommand:
            raise TypeError("Recording a condition needs a ConditionCommand.")
        return self._execute(command)

    def accept_offer(self, command):
        if type(command) is not AcceptMentorOfferCommand:
            raise TypeError("Accepting needs an AcceptMentorOfferCommand.")
        return self._execute(command)

    def decline_offer(self, command):
        if type(command) is not DeclineMentorOfferCommand:
            raise TypeError("Declining needs a DeclineMentorOfferCommand.")
        return self._execute(command)

    def _execute(self, command):
        entry = IntakeEntry.TRANSITION_MEMBERSHIP_CONDITION
        shape = condition_shape(command)
        bound = admit_request(
            self._alias, entry, _resolve_actor(self._alias, command.credential), shape
        )
        with authority_atomic(self._alias, received_at=bound.received_at):
            actor = _lock_actor(
                database_alias=self._alias,
                credential=command.credential,
                unavailable=ConditionRefused,
            )
            bound.verify(command, actor, entry, self._alias)
            acquire_organism_command_mutex(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                action=self._action,
                idempotency_key=command.idempotency_key,
            )
            keys = dict(
                actor=actor,
                request_reference=command.request_reference,
                idempotency_key=command.idempotency_key,
                payload_fingerprint=bound.request_fingerprint,
            )
            replay = self._attempt_replay(
                **keys, viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock())
            )
            if replay is not None:
                return replay
            _lock_registered_organism(self._alias, command.organism_uuid)
            lo = (
                LivingOrganism.objects.using(self._alias)
                .select_for_update()
                .filter(organism_id=command.organism_uuid)
                .first()
            )
            if (
                lo is None
                or lo.state != "ACTIVE"
                or command.effective_at > bound.received_at
            ):
                raise ConditionRefused(
                    "This condition needs an active organisation and an effective date already reached. "
                    "No condition changed. Check the organisation and date."
                )
            actor_member, roles = _lock_actor_membership_context(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                living_organism_id=lo.organism_id,
                unavailable=ConditionRefused,
            )
            if type(command) in RESPONSES:
                return self._respond(
                    command, shape, bound, actor, actor_member, roles, lo, keys
                )
            if command.cq_outcome_uuid is not None:
                raise ConditionRefused(
                    "Probation completion is unavailable until basic CQ supplies a qualifying outcome. "
                    "No condition or membership changed. Keep the recorded outcome for basic CQ."
                )
            name = (
                "c1.mentor_assignment"
                if command.replaces is None
                else "c1.mentor_replacement"
            )
            members = (
                OrganismMembership.objects.using(self._alias)
                .filter(
                    living_organism=lo,
                    membership_uuid__in=(command.subject_uuid, command.mentor_uuid),
                )
                .select_for_update(of=("self",))
                .order_by("pk")
            )
            by_uuid = {member.membership_uuid: member for member in members}
            subject = by_uuid.get(command.subject_uuid)
            mentor = by_uuid.get(command.mentor_uuid)
            now = database_now(self._alias)

            def eligible(member, start, states):
                return (
                    member is not None
                    and member.state in states
                    and _fold_state(
                        self._alias,
                        "core_organismmembership",
                        member.pk,
                        command.effective_at,
                        bound.received_at,
                    )
                    in states
                    and period_at(
                        member.pk, command.effective_at, bound.received_at, self._alias
                    )
                    == start
                    and period_at(member.pk, now, now, self._alias) == start
                )

            if not eligible(
                subject,
                command.subject_start,
                {"PROBATIONARY"}
                if command.kind == "PROBATION"
                else {"ACTIVE", "PROBATIONARY"},
            ):
                raise ConditionRefused(
                    "The subject is outside the named eligible membership period. No condition changed. Check the membership history."
                )
            if command.kind == "MENTORSHIP" or command.mentor_uuid is not None:
                if (
                    not eligible(mentor, command.mentor_start, {"ACTIVE"})
                    or mentor.pk == subject.pk
                ):
                    raise ConditionRefused(
                        "A mentor must be another eligible current member of this organisation in the named period. "
                        "No condition changed. Check the proposed mentor."
                    )
            # The trusted reader resolves the published rule, recorder and eligible periods.
            # No command-supplied eligibility, CQ count, or boolean approval enters this decision.
            decision = self._evaluate_authority(
                actor=actor,
                actor_membership=actor_member,
                actor_role_refs=roles,
                request_reference=command.request_reference,
                idempotency_key=command.idempotency_key,
                target=shape.target,
                target_fingerprint=authority_target_fingerprint(shape.target),
                effective_at=command.effective_at,
                received_at=bound.received_at,
                living_organism_id=lo.organism_id,
            )
            current = []
            for row in (
                MembershipCondition.objects.using(self._alias)
                .select_for_update(of=("self",))
                .filter(subject_membership=subject, condition_kind=command.kind)
                .select_related("head_state_event")
                .order_by("pk")
            ):
                original, _ = bound_periods(row, self._alias)
                if original is None:
                    raise ConditionRefused(
                        "A prior condition has no provable period. No condition changed. Its origin needs reconciliation."
                    )
                if (
                    original == command.subject_start
                    and row.head_state_event.resulting_state not in TERMINAL
                ):
                    current.append(row)
            if any(
                row.head_state_event.resulting_state == "OFFERED" for row in current
            ):
                raise ConditionRefused(
                    "A mentor offer is already open. No condition changed. The offered mentor can respond."
                )
            if [row.condition_uuid for row in current] != (
                [command.replaces] if command.replaces else []
            ) or any(
                row.head_state_event.effective_at > command.effective_at
                or row.head_state_event.recorded_at > bound.received_at
                for row in current
            ):
                raise ConditionRefused(
                    "The current condition differs from the one named for replacement. No condition changed. Read the current condition and retry."
                )
            kwargs = dict(
                alias=self._alias,
                actor=actor,
                basis_id=lo.founding_authority_basis_id,
                decision_reference=decision.authority_decision_reference,
                command=command,
                fingerprint=bound.request_fingerprint,
                received_at=bound.received_at,
            )
            new = MembershipCondition.objects.using(self._alias).create(
                condition_kind=command.kind,
                subject_membership=subject,
                mentor_membership=mentor,
            )
            if current and command.kind == "PROBATION":
                append_relationship_event(
                    anchor=current[0],
                    head=current[0].head_state_event,
                    action="REPLACE",
                    state="SUPERSEDED",
                    evidence=f"condition:{new.condition_uuid}",
                    **kwargs,
                )
            event = append_relationship_event(
                anchor=new,
                head=None,
                action="OFFER" if command.kind == "MENTORSHIP" else "OPEN",
                state="OFFERED" if command.kind == "MENTORSHIP" else "ACTIVE",
                evidence=command.evidence_reference,
                **kwargs,
            )
            if bound_periods(new, self._alias) != (
                command.subject_start,
                command.mentor_start,
            ):
                raise ConditionRefused(
                    "The condition origin could not be bound to the requested periods. No condition changed. Check the dates."
                )
            row = MembershipConditionStateEvent.objects.using(self._alias).get(
                event_uuid=event
            )
            notice = (
                mentor_notices(new, event, [mentor.identity], name)
                if mentor
                else "NONE"
            )
            result = ConditionResult(
                str(new.condition_uuid),
                str(event),
                decision.authority_decision_reference,
                row.lineage_reference,
                notice,
            )
            return self._finalise(**keys, result=result, received_at=bound.received_at)

    def _respond(self, command, shape, bound, actor, actor_member, roles, lo, keys):
        offer = (
            MembershipCondition.objects.using(self._alias)
            .select_for_update(of=("self",))
            .select_related(
                "head_state_event", "mentor_membership", "subject_membership"
            )
            .filter(
                condition_uuid=command.condition_uuid,
                condition_kind="MENTORSHIP",
                subject_membership__living_organism=lo,
            )
            .first()
        )
        head = offer.head_state_event if offer else None
        now = database_now(self._alias)
        if (
            head is None
            or head.resulting_state != "OFFERED"
            or offer.mentor_membership_id != actor_member.pk
            or bound_periods(offer, self._alias)[1] != command.mentor_start
            or head.recorded_at > bound.received_at
            or head.effective_at > command.effective_at
            or not offer_periods_current(
                offer, command.effective_at, bound.received_at, self._alias
            )
            or not offer_periods_current(offer, now, now, self._alias)
        ):
            raise ConditionRefused(
                "This offer is unavailable to this mentor. Nothing changed. Read your open offers."
            )
        decision = self._evaluate_authority(
            actor=actor,
            actor_membership=actor_member,
            actor_role_refs=roles,
            request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,
            target=shape.target,
            target_fingerprint=authority_target_fingerprint(shape.target),
            effective_at=command.effective_at,
            received_at=bound.received_at,
            living_organism_id=lo.organism_id,
        )
        kwargs = dict(
            alias=self._alias,
            actor=actor,
            basis_id=lo.founding_authority_basis_id,
            decision_reference=decision.authority_decision_reference,
            command=command,
            fingerprint=bound.request_fingerprint,
            received_at=bound.received_at,
        )
        accepting = type(command) is AcceptMentorOfferCommand
        if accepting:
            offered = AuthorityDecision.objects.using(self._alias).get(
                decision_reference=head.authority_decision_reference
            )
            replaces = offered.target["replaces"]
            if replaces:
                prior = (
                    MembershipCondition.objects.using(self._alias)
                    .select_for_update(of=("self",))
                    .filter(
                        condition_uuid=replaces,
                        condition_kind="MENTORSHIP",
                        subject_membership_id=offer.subject_membership_id,
                    )
                    .select_related("head_state_event")
                    .first()
                )
                if (
                    not prior
                    or prior.head_state_event.resulting_state
                    not in {"ACTIVE", "MENTOR_UNRESOLVED"}
                    or prior.head_state_event.effective_at > command.effective_at
                    or prior.head_state_event.recorded_at > bound.received_at
                    or bound_periods(prior, self._alias)[0]
                    != bound_periods(offer, self._alias)[0]
                ):
                    raise ConditionRefused(
                        "The earlier mentoring arrangement changed. Nothing changed by this request. Ask the Coordinator to check it, or accept again with a current date."
                    )
                append_relationship_event(
                    anchor=prior,
                    head=prior.head_state_event,
                    action="REPLACE",
                    state="SUPERSEDED",
                    evidence=f"condition:{offer.condition_uuid}",
                    **kwargs,
                )
        event = append_relationship_event(
            anchor=offer,
            head=head,
            action=RESPONSES[type(command)],
            state="ACTIVE" if accepting else "DECLINED",
            evidence=f"offer:{head.event_uuid}",
            **kwargs,
        )
        row = MembershipConditionStateEvent.objects.using(self._alias).get(
            event_uuid=event
        )
        result = ConditionResult(
            str(offer.condition_uuid),
            str(event),
            decision.authority_decision_reference,
            row.lineage_reference,
            "NONE",
        )
        return self._finalise(**keys, result=result, received_at=bound.received_at)

    def own_open_offers(self, *, credential):
        """Discover only this credential's actionable offers; no condition UUID is required."""
        rows = (
            MembershipCondition.objects.using(self._alias)
            .filter(
                condition_kind="MENTORSHIP",
                head_state_event__resulting_state="OFFERED",
                subject_membership__living_organism__state="ACTIVE",
                mentor_membership__identity__credential=credential,
                mentor_membership__identity__credential__is_active=True,
                mentor_membership__identity__access_state=Identity.AccessState.ACTIVE,
                mentor_membership__state="ACTIVE",
            )
            .order_by("condition_uuid")
        )
        now = database_now(self._alias)
        return tuple(
            row.condition_uuid
            for row in rows
            if offer_periods_current(row, now, now, self._alias)
        )

    def own_status(self, *, credential, condition_uuid):
        """Only the authenticated subject or current period-bound mentor can read this status."""
        now = database_now(self._alias)
        row = (
            MembershipCondition.objects.using(self._alias)
            .filter(condition_uuid=condition_uuid)
            .select_related(
                "head_state_event", "subject_membership", "mentor_membership"
            )
            .first()
        )
        actor = (
            Identity.objects.using(self._alias)
            .filter(
                credential=credential,
                credential__is_active=True,
                access_state=Identity.AccessState.ACTIVE,
            )
            .first()
        )
        if row and actor:
            subject, mentor = bound_periods(row, self._alias)
            if actor.pk == row.subject_membership.identity_id or (
                mentor
                and row.mentor_membership_id
                and actor.pk == row.mentor_membership.identity_id
                and period_at(row.mentor_membership_id, now, now, self._alias) == mentor
                and (
                    row.head_state_event.resulting_state == "ACTIVE"
                    or (
                        row.head_state_event.resulting_state == "OFFERED"
                        and offer_periods_current(row, now, now, self._alias)
                    )
                )
            ):
                if subject is None or (row.mentor_membership_id and mentor is None):
                    return "UNRESOLVED_PERIOD"
                if row.head_state_event.resulting_state in TERMINAL:
                    return row.head_state_event.resulting_state
                if (
                    period_at(row.subject_membership_id, now, now, self._alias)
                    != subject
                ):
                    return "ENDED"
                if (
                    mentor
                    and period_at(row.mentor_membership_id, now, now, self._alias)
                    != mentor
                ):
                    return "MENTOR_UNRESOLVED"
                return row.head_state_event.resulting_state
        raise ConditionRefused(
            "This condition is unavailable to this reader. Nothing changed. Check your membership."
        )


def offer_periods_current(row, at, known, alias):
    subject, mentor = bound_periods(row, alias)
    return bool(
        subject
        and mentor
        and period_at(row.subject_membership_id, at, known, alias) == subject
        and period_at(row.mentor_membership_id, at, known, alias) == mentor
        and _fold_state(
            alias, "core_organismmembership", row.mentor_membership_id, at, known
        )
        == "ACTIVE"
    )


def qualified_offer(request, actor_member, now, alias):
    """The effect transaction holds the LO mutex. Admit only the offer's own mentor at both cuts."""
    target = request.target
    offer = (
        MembershipCondition.objects.using(alias)
        .filter(
            condition_uuid=target["condition_uuid"],
            condition_kind="MENTORSHIP",
            subject_membership__living_organism_id=actor_member.living_organism_id,
            mentor_membership=actor_member,
            head_state_event__resulting_state="OFFERED",
            head_state_event__recorded_at__lte=request.evaluated_at,
            head_state_event__effective_at__lte=request.effective_at,
        )
        .select_related("head_state_event")
        .first()
    )
    if (
        offer
        and str(bound_periods(offer, alias)[1]) == target["mentor_start"]
        and offer_periods_current(
            offer, request.effective_at, request.evaluated_at, alias
        )
        and offer_periods_current(offer, now, now, alias)
    ):
        return offer
    return None


def end_departing_relationships(departure, transition):
    from src.intevia.services.organism_membership_service import (
        S015MembershipEdgeRefused,
    )

    member = departure.member
    alias = departure.roles._alias
    # The newly inserted ENDED event is deliberately outside the origin's knowledge cut.
    departing_period = period_at(
        member.pk, transition.effective_at, departure.received_at, alias
    )
    rows = (
        MembershipCondition.objects.using(alias)
        .select_for_update(of=("self",))
        .filter(Q(subject_membership=member) | Q(mentor_membership=member))
        .select_related("head_state_event")
        .order_by("pk")
    )
    for row in rows:
        head = row.head_state_event
        if head.resulting_state in TERMINAL:
            continue
        subject_start, mentor_start = bound_periods(row, alias)
        subject = row.subject_membership_id == member.pk
        linked_start = subject_start if subject else mentor_start
        if linked_start is None:
            raise S015MembershipEdgeRefused(
                "Leaving was refused because a linked condition has no provable period. "
                "Membership and condition history are unchanged. The condition origin needs reconciliation."
            )
        if linked_start != departing_period or (
            not subject and head.resulting_state == "MENTOR_UNRESOLVED"
        ):
            continue
        if head.recorded_at > departure.received_at:
            raise S015MembershipEdgeRefused(
                "A condition changed after your request was received. Your membership and relationships "
                "are unchanged. Check the condition and submit a fresh leave request."
            )
        if head.effective_at > transition.effective_at:
            raise S015MembershipEdgeRefused(
                "A condition change is dated after your leaving date. Your membership and relationships "
                "are unchanged. Check the condition date and confirm a leave on or after that date."
            )
        state = "ENDED" if subject else "MENTOR_UNRESOLVED"
        if head.resulting_state == "OFFERED":
            state = "OFFER_CLOSED"
        append_relationship_event(
            alias=alias,
            anchor=row,
            head=head,
            action="MEMBER_LEFT" if subject else "MENTOR_LEFT",
            state=state,
            actor=departure.actor,
            basis_id=transition.authority_basis_id,
            decision_reference=transition.authority_decision_reference,
            evidence=f"membership-ending:{transition.event_uuid}",
            command=departure.command,
            fingerprint=departure.fingerprint,
            received_at=departure.received_at,
            composing_rule="C0:O-21:v1.2",
        )
