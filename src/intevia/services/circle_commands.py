"""Governed Circle commands. All externally supplied identifiers are scoped inside the registered LO lock."""

from contextlib import contextmanager
from hashlib import sha256
from uuid import uuid4, uuid5

from django.db import connections, transaction

from core import models
from src.intevia.services import scoped_determinations as scoped
from src.intevia.services.circle_participation import CircleAccessRefused
from src.intevia.services.clock import database_now
from src.intevia.services.organism_authority import authority_target_fingerprint
from src.intevia.services.organism_command_support import (
    _canonical_key,
    _empty_anchor_fingerprint,
    _lock_actor,
    _lock_registered_organism,
)
from src.intevia.services.organism_entitlement_service import (
    check_allowance,
    entitlement_effective,
)


def qualified_root(alias, lo, at):
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT public.r_circle_root_qualified(%s,%s,%s)",[lo.pk,at,at])
        return cursor.fetchone()[0] is True


def check_creation_allowance(alias, lo, at):
    check = check_allowance(lo.organism_id, "circles", 1, at, database_alias=alias)
    if check.status != "within":
        raise CircleAccessRefused(check.message)


def unavailable():
    raise CircleAccessRefused(
        "This request cannot use the selected Circle or membership. Check the organisation and selection."
    )


@contextmanager
def command_context(alias, credential, organism_uuid, *, require_member=False):
    with transaction.atomic(using=alias):
        actor = _lock_actor(
            database_alias=alias, credential=credential, unavailable=CircleAccessRefused
        )
        if require_member and not models.OrganismMembership.objects.using(alias).filter(
            living_organism__organism_id=organism_uuid, identity=actor, state="ACTIVE"
        ).exists():
            unavailable()
        _lock_registered_organism(alias, organism_uuid)
        lo = (
            models.LivingOrganism.objects.using(alias)
            .filter(organism_id=organism_uuid)
            .first()
        )
        if lo is None:
            unavailable()
        yield lo, actor, database_now(alias)


def target_circle(alias, lo, circle_uuid):
    circle = (
        models.Circle.objects.using(alias)
        .filter(parent_organism=lo, circle_uuid=circle_uuid)
        .first()
    )
    if circle is None:
        unavailable()
    return circle


def case_scope(lo, target, act, actor, beneficiary):
    return scoped.Scope(
        lo.organism_id,
        target,
        act,
        scoped.Type.EXECUTION_ELIGIBILITY_QUALIFICATION,
        actor.pk,
        beneficiary.pk,
        lo.founding_authority_basis.basis_uuid,
    )


def evidence(alias, lo, target, act, actor, beneficiary, at):
    scope = case_scope(lo, target, act, actor, beneficiary)
    source = scoped.qualified(alias, scope, at, at)
    with connections[alias].cursor() as cur:
        cur.execute(
            "SELECT public.circle_decision(%s,%s,%s,%s)",
            [lo.pk, source.determination_uuid, at, at],
        )
        (decision,) = cur.fetchone()
    if decision is None:
        raise CircleAccessRefused(
            "This act needs a current mandate covering this person and Circle. Check the governing instrument."
        )
    return source, decision


def _office_source(alias, query, params, target, act, actor, subject):
    """Resolve the holding at the cut, then bind its reference to this particular act."""
    with connections[alias].cursor() as cursor:
        cursor.execute(query, params)
        office, = cursor.fetchone()
        if office is None:
            unavailable()
        cursor.execute("SELECT public.circle_office_decision(%s,%s,%s,%s,%s)",
                       [office, target, act, actor.pk, subject.pk])
        return office, cursor.fetchone()[0]


def lo_source(alias, lo, target, act, actor, at):
    return _office_source(alias, "SELECT public.circle_lo_office(%s,%s,%s,%s)",
                          [lo.pk, actor.pk, at, at], target, act, actor, actor)


def write_event(
    alias,
    anchor,
    actor,
    action,
    outcome,
    source,
    ref,
    event_uuid,
    at,
    *,
    composed=None,
    deadline=None,
    occurred=None,
    received=None,
):
    """Append to the bound anchor using its checked head and refresh its authoritative cache."""
    circle = isinstance(anchor, models.Circle)
    head = anchor.head_event if circle else anchor.head_state_event
    token = anchor._meta.db_table
    lo = anchor.parent_organism if circle else anchor.membership.living_organism
    key = "circle:" + str(event_uuid)
    fp = authority_target_fingerprint(
        {
            "anchor": str(anchor.circle_uuid if circle else anchor.assignment_uuid),
            "action": action,
            "evidence": ref,
            "source": source,
            "request": key,
        }
    )
    with connections[alias].cursor() as cur:
        cur.execute(
            """SELECT public.s015_append_event(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}',%s,
                         %s,%s,%s::integer,%s,%s,%s)""",
            [
                token,
                anchor.pk,
                action,
                outcome,
                at,
                occurred or at,
                received or at,
                actor.pk,
                lo.founding_authority_basis_id,
                source,
                ref,
                key,
                fp,
                deadline,
                event_uuid,
                head.pk if head else None,
                head.sequence if head else None,
                None if composed or action in {"CREATE_DORMANT", "ASSIGN", "ACTIVATE"} else actor.access_epoch,
                composed,
                key,
            ],
        )
        cur.execute(
            "SELECT public.s015_refresh_governed_cache(%s,%s,%s,clock_timestamp())",
            [token, anchor.pk, at],
        )
    return event_uuid


def form(
    *,
    credential,
    organism_uuid,
    circle_uuid,
    kind,
    purpose,
    request_uuid,
    alias="default",
    name=None,
):
    """Birth a GENERIC Circle atomically, or prepare a functional designation on its existing route."""
    if name is not None:
        name = _canonical_key(name, "name", 90)
        if kind != "GENERIC":
            raise CircleAccessRefused("Initial named birth currently requires an ordinary GENERIC Circle.")
    purpose = _canonical_key(purpose, "purpose", 240)
    if kind not in {"GENERIC", "APPLICATION", "FINANCE", "OPERATIONS"}:
        raise CircleAccessRefused(
            "Choose an ordinary Circle kind or use the existing GOVERNANCE creation route."
        )
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        ref = "circle-purpose:" + purpose
        previous = (
            models.CircleStateEvent.objects.using(alias)
            .filter(event_uuid=request_uuid)
            .first()
        )
        if previous:
            if (
                previous.circle.parent_organism_id,
                previous.circle.circle_uuid,
                previous.circle.kind,
                previous.action,
                previous.evidence_reference,
            ) == (lo.pk, circle_uuid, kind, "CREATE_DORMANT", ref):
                with connections[alias].cursor() as cur:
                    cur.execute(
                        "SELECT enterer_identity_id FROM core_circlestateevent WHERE id=%s",
                        [previous.pk],
                    )
                    if cur.fetchone()[0] == actor.pk:
                        description = models.CircleBirthDescription.objects.using(alias).filter(circle=previous.circle).first()
                        if (description.name if description else None) != name:
                            unavailable()
                        return previous.circle
            unavailable()
        act = "FORM:" + kind + ":" + sha256(purpose.encode()).hexdigest()
        if kind == "GENERIC":
            _, source = lo_source(alias, lo, circle_uuid, act, actor, at)
        else:
            _, source = evidence(alias, lo, circle_uuid, act, actor, actor, at)
        check_creation_allowance(alias, lo, at)
        if kind != "GENERIC":
            if not entitlement_effective(
                lo.organism_id, kind + "_CIRCLE", at=at, database_alias=alias
            ):
                raise CircleAccessRefused(
                    "This functional Circle needs the corresponding LO entitlement."
                )
            if (
                models.Circle.objects.using(alias)
                .filter(parent_organism=lo, kind=kind)
                .exists()
            ):
                raise CircleAccessRefused(
                    "This organisation already has the designated Circle. Use its existing record."
                )
        circle = models.Circle.objects.using(alias).create(
            circle_uuid=circle_uuid,
            parent_organism=lo,
            kind=kind,
            state=models.Circle.State.DORMANT,
            current_state_effective_at=at,
            state_known_at=at,
            founding_reference=source,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=alias,
                anchor_token="core_circle",
                anchor_uuid=circle_uuid,
            ),
        )
        write_event(
            alias,
            circle,
            actor,
            "CREATE_DORMANT",
            "DORMANT",
            source,
            ref,
            request_uuid,
            at,
        )
        if kind == "GENERIC":
            _seat_founder_and_activate(alias, lo, actor, credential, circle, request_uuid)
            circle.refresh_from_db(using=alias)
            if name is not None:
                models.CircleBirthDescription.objects.using(alias).create(circle=circle,
                    birth_event=models.CircleStateEvent.objects.using(alias).get(circle=circle,event_uuid=request_uuid),
                    name=name,purpose=purpose,enterer=actor,enterer_access_epoch=actor.access_epoch,effective_at=database_now(alias))
        return circle


def _seat_founder_and_activate(alias, lo, actor, credential, circle, opening_uuid):
    member = models.OrganismMembership.objects.using(alias).get(living_organism=lo, identity=actor, state="ACTIVE")
    at = database_now(alias)
    opening = models.CircleStateEvent.objects.using(alias).get(circle=circle, event_uuid=opening_uuid)
    place = models.CircleParticipation.objects.using(alias).create(
        circle=circle, membership=member, birth_event=opening, seat_id=str(member.membership_uuid),
        seat_rule_uuid=lo.founding_authority_basis.basis_uuid, actor_state="LIVING_ORGANISM",
        actor_capacity="STANDING", entry_mode="TRANSPORT", enterer_identity=actor,
        composing_rule_state="NA_TRANSPORT", composer_state="NA_TRANSPORT",
        effective_at=at, occurred_at=at, received_at=at)
    _take_circle_office(alias, lo, credential, circle, place, uuid5(opening.event_uuid, "first-circle-coordinator"))
    activate(credential=credential, organism_uuid=lo.organism_id, circle_uuid=circle.circle_uuid,
             request_uuid=uuid4(), alias=alias)


def _take_circle_office(alias, lo, credential, circle, place, office_uuid):
    from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
    from src.intevia.services.organism_authority import S015Authority
    from src.intevia.services.organism_role_service import OrganismRoleService

    roles = OrganismRoleService(database_alias=alias, clock=lambda: database_now(alias),
        authority=S015Authority(provider=ProductionApplicationAuthorityProvider(database_alias=alias), database_alias=alias))
    args = dict(credential=credential, organism_uuid=lo.organism_id, circle_uuid=circle.circle_uuid)
    roles.nominate_circle_coordinator(**args, participation_uuid=place.participation_uuid,
                                      assignment_uuid=office_uuid, request_uuid=uuid5(office_uuid, "propose"))
    roles.appoint_circle_coordinator(**args, assignment_uuid=office_uuid, request_uuid=uuid5(office_uuid, "assign"))
    return office_uuid


def recover_circle(*, credential, organism_uuid, circle_uuid, request_uuid, alias="default"):
    """An eligible participant volunteers, or an LO Coordinator joins and takes the vacant office."""
    from src.intevia.services.organism_command_support import _canonical_uuid

    key = _canonical_uuid(request_uuid, "request_uuid")
    assignment_uuid = uuid5(key, "recover-circle-coordinator")
    with command_context(alias, credential, organism_uuid, require_member=True) as (lo, actor, at):
        circle = target_circle(alias, lo, circle_uuid)
        member = models.OrganismMembership.objects.using(alias).filter(living_organism=lo, identity=actor, state="ACTIVE").first()
        if member is None or circle.kind != "GENERIC" or (lo.platform_root and not qualified_root(alias,lo,at)):
            unavailable()
        existing = models.ContextualRoleAssignment.objects.using(alias).filter(assignment_uuid=assignment_uuid).first()
        if existing:
            if (existing.circle_id == circle.pk and existing.membership_id == member.pk
                    and models.ContextualRoleAssignmentStateEvent.objects.using(alias).filter(
                    assignment=existing, action="ASSIGN", event_uuid=uuid5(assignment_uuid, "assign"),
                    evidence_reference__regex=r"\|(CB1|RC1)$").exists()):
                return assignment_uuid
            unavailable()
        with connections[alias].cursor() as cursor:
            cursor.execute("""SELECT public.circle_lo_office(%s,%s,%s,%s),public.circle_vacant(%s,NULL,%s,%s),
                              public.circle_direct_volunteer(%s,%s,%s)""",
                           [lo.pk,actor.pk,at,at,circle.pk,at,at,lo.pk,at,at])
            authority, vacant, direct = cursor.fetchone()
            cursor.execute("""SELECT p.id FROM public.core_circleparticipation p WHERE p.circle_id=%s AND p.membership_id=%s
                            AND public.circle_office_place(%s,%s,'place:'||p.participation_uuid,%s,%s)""",
                           [circle.pk,member.pk,circle.pk,member.pk,at,at])
            current = cursor.fetchone()
        if not vacant or (not current and not authority):
            unavailable()
        if not direct and not authority:
            raise CircleAccessRefused("This volunteer appointment needs a governed CQ vote.")
        if current:
            place = models.CircleParticipation.objects.using(alias).get(pk=current[0])
        else:
            office = models.ContextualRoleAssignment.objects.using(alias).get(assignment_uuid=authority)
            place = models.CircleParticipation.objects.using(alias).create(
                circle=circle, membership=member, recovery_office=office, seat_id=str(member.membership_uuid),
                seat_rule_uuid=lo.founding_authority_basis.basis_uuid, actor_state="LIVING_ORGANISM",
                actor_capacity="STANDING", entry_mode="TRANSPORT", enterer_identity=actor,
                composing_rule_state="NA_TRANSPORT", composer_state="NA_TRANSPORT",
                occurred_at=at,effective_at=at,received_at=at)
        return _take_circle_office(alias, lo, credential, circle, place, assignment_uuid)


def operating_status(*, credential, organism_uuid, circle_uuid, alias="default"):
    """The current participant or an effective parent Coordinator can see the Circle's operating state."""
    with command_context(alias, credential, organism_uuid, require_member=True) as (lo, actor, at):
        circle = target_circle(alias, lo, circle_uuid)
        if circle.kind != "GENERIC" or (lo.platform_root and not qualified_root(alias,lo,at)):
            unavailable()
        with connections[alias].cursor() as cur:
            cur.execute("""SELECT public.circle_lo_office(%s,%s,%s,%s) IS NOT NULL OR EXISTS(
                SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=%s AND m.identity_id=%s
                AND public.circle_place_at(%s,m.id,%s,%s)), EXISTS(SELECT FROM public.circle_holders(%s,%s,%s)),
                public.circle_vacant(%s,NULL,%s,%s)""",
                [lo.pk,actor.pk,at,at, lo.pk,actor.pk,circle.pk,at,at, circle.pk,at,at, circle.pk,at,at])
            allowed, held, vacancy = cur.fetchone()
        if not allowed:
            unavailable()
        if circle.state == "ACTIVE" and not held:
            return "WITHOUT_COORDINATOR" if vacancy else "COORDINATOR_STANDING_UNCONFIRMED"
        return circle.state


def request_place(
    *,
    credential,
    organism_uuid,
    circle_uuid,
    membership_uuid,
    request_uuid,
    consent,
    alias="default",
):
    consent = _canonical_key(consent, "consent", 255)
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        member = (
            models.OrganismMembership.objects.using(alias)
            .filter(living_organism=lo, identity=actor, membership_uuid=membership_uuid)
            .first()
        )
        if not member:
            unavailable()
        circle = target_circle(alias, lo, circle_uuid)
        prior = (
            models.CircleJoinRequest.objects.using(alias)
            .filter(request_uuid=request_uuid)
            .first()
        )
        if prior:
            if (
                prior.circle_id,
                prior.membership_id,
                prior.consent_reference,
                prior.initiated_by,
            ) == (circle.pk, member.pk, consent, "CIRCLE_MEMBER"):
                return prior
            unavailable()
        with connections[alias].cursor() as cur:
            cur.execute(
                "SELECT public.circle_eligible(%s,%s,%s,%s)",
                [circle.pk, member.pk, at, at],
            )
            if not cur.fetchone()[0] or circle.kind == "GOVERNANCE":
                raise CircleAccessRefused(
                    "This membership cannot request an ordinary place here. Check eligibility or the Board seat route."
                )
        return models.CircleJoinRequest.objects.using(alias).create(
            request_uuid=request_uuid,
            circle=circle,
            membership=member,
            initiated_by="CIRCLE_MEMBER",
            consent_reference=consent,
            enterer_identity=actor,
            seat_rule_uuid=lo.founding_authority_basis.basis_uuid,
            seat_id=str(member.membership_uuid),
            occurred_at=at,
            effective_at=at,
            received_at=at,
        )


def admit(*, credential, organism_uuid, circle_uuid, request_uuid, alias="default"):
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        req = (
            models.CircleJoinRequest.objects.using(alias)
            .select_related("circle", "membership__identity")
            .filter(
                circle__parent_organism=lo,
                circle__circle_uuid=circle_uuid,
                request_uuid=request_uuid,
                initiated_by="CIRCLE_MEMBER",
            )
            .first()
        )
        if not req:
            unavailable()
        if req.circle.kind == "GENERIC":
            return _application_answer(alias, req, actor, "ACCEPT", at)
        prior = models.CircleAdmissionDecision.objects.using(alias).filter(request=req).first()
        if prior is not None:
            if prior.deciding_identity_id != actor.pk:
                unavailable()
            return models.CircleParticipation.objects.using(alias).get(decision=prior)
        finding, source = evidence(
            alias,
            lo,
            circle_uuid,
            "ADMIT:" + str(request_uuid),
            actor,
            req.membership.identity,
            at,
        )
        common = dict(
            seat_id=req.seat_id,
            seat_rule_uuid=req.seat_rule_uuid,
            actor_state="LIVING_ORGANISM",
            actor_capacity="STANDING",
            entry_mode="TRANSPORT",
            enterer_identity=actor,
            composing_rule_state="NA_TRANSPORT",
            composer_state="NA_TRANSPORT",
            occurred_at=at,
            effective_at=at,
            received_at=at,
        )
        decision = models.CircleAdmissionDecision.objects.using(alias).create(
            request=req,
            route="ORDINARY",
            constitutional_route="INSTRUMENT",
            deciding_party="INSTRUMENT",
            deciding_identity=actor,
            self_decision=actor.pk == req.membership.identity_id,
            outcome="ACCEPT",
            authority_decision_reference=source,
            settings_basis="finding:" + str(finding.determination_uuid),
            **common,
        )
        return models.CircleParticipation.objects.using(alias).create(
            circle=req.circle, membership=req.membership, decision=decision, **common
        )


def answer_application(*, credential, organism_uuid, circle_uuid, request_uuid, outcome, alias="default"):
    """Append one answer to an application. ACCEPT also creates the person's place."""
    if outcome not in {"ACCEPT", "DECLINE", "WITHDRAWN"}:
        raise CircleAccessRefused("Choose an application outcome.")
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        request = models.CircleJoinRequest.objects.using(alias).select_related("circle", "membership__identity").filter(
            circle__parent_organism=lo, circle__kind="GENERIC", circle__circle_uuid=circle_uuid,
            request_uuid=request_uuid, initiated_by="CIRCLE_MEMBER").first()
        if request is None:
            unavailable()
        return _application_answer(alias, request, actor, outcome, at)


def _application_answer(alias, req, actor, outcome, at):
    prior = models.CircleAdmissionDecision.objects.using(alias).filter(request=req).first()
    if prior:
        if (prior.outcome, prior.deciding_identity_id) != (outcome, actor.pk):
            unavailable()
        return models.CircleParticipation.objects.using(alias).get(decision=prior) if outcome == "ACCEPT" else prior
    common = dict(seat_id=req.seat_id, seat_rule_uuid=req.seat_rule_uuid, actor_capacity="STANDING",
                  entry_mode="TRANSPORT", enterer_identity=actor, composing_rule_state="NA_TRANSPORT",
                  composer_state="NA_TRANSPORT", occurred_at=at, effective_at=at, received_at=at)
    if outcome == "WITHDRAWN":
        if actor.pk != req.membership.identity_id:
            unavailable()
        with connections[alias].cursor() as cur:
            cur.execute("SELECT public.circle_office_decision(%s,%s,'WITHDRAW',%s,%s)",
                        [req.request_uuid, req.circle.circle_uuid, actor.pk, actor.pk])
            source, = cur.fetchone()
        party = dict(actor_state="PARTY", actor=actor, actor_access_epoch=actor.access_epoch, deciding_party="APPLICANT")
    else:
        _, source = _office_source(alias, "SELECT public.circle_current_office(%s,%s,%s,%s)",
                                   [req.circle_id, actor.pk, at, at], req.request_uuid, outcome, actor, req.membership.identity)
        party = dict(actor_state="LIVING_ORGANISM", deciding_party="office:CIRCLE_COORDINATOR")
    decision = models.CircleAdmissionDecision.objects.using(alias).create(
        request=req, route="APPLICATION_MODE", constitutional_route="APPLICATION", deciding_identity=actor,
        self_decision=actor.pk == req.membership.identity_id, outcome=outcome, settings_basis="mode:APPLICATION",
        authority_decision_reference=source, **party, **common)
    if outcome != "ACCEPT":
        return decision
    return models.CircleParticipation.objects.using(alias).create(
        circle=req.circle, membership=req.membership, decision=decision, actor_state="LIVING_ORGANISM", **common)


def leave(
    *,
    credential,
    organism_uuid,
    circle_uuid,
    participation_uuid,
    request_uuid,
    confirmed,
    alias="default",
):
    if confirmed is not True:
        raise CircleAccessRefused(
            "Confirm that you want to leave this Circle. Your LO membership continues."
        )
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        place = (
            models.CircleParticipation.objects.using(alias)
            .select_related("membership", "circle")
            .filter(
                participation_uuid=participation_uuid,
                circle__parent_organism=lo,
                circle__circle_uuid=circle_uuid,
                membership__identity=actor,
            )
            .first()
        )
        if not place:
            unavailable()
        ended = (
            models.CircleParticipationEnd.objects.using(alias)
            .filter(participation=place)
            .first()
        )
        if ended:
            if (
                ended.composed_from_event_uuid == request_uuid
                and ended.actor_id == actor.pk
            ):
                return ended
            raise CircleAccessRefused(
                "This Circle place has already ended. Check your current places."
            )
        circle = place.circle
        source = "s015d1:" + authority_target_fingerprint(
            {
                "leave": str(participation_uuid),
                "actor": str(actor.identity_id),
                "confirmed": True,
                "request": str(request_uuid),
            }
        )
        write_event(
            alias,
            circle,
            actor,
            "LEAVE_CIRCLE",
            circle.state,
            source,
            "place:" + str(participation_uuid),
            request_uuid,
            at,
        )
        offices = (
            models.ContextualRoleAssignment.objects.using(alias)
            .select_for_update(of=("self",))
            .select_related("head_state_event")
            .filter(circle=circle, membership=place.membership)
            .order_by("pk")
        )
        for office in offices:
            if office.head_state_event.effective_at > at:
                raise CircleAccessRefused(
                    "A later office change is already recorded. Resolve its timing before leaving."
                )
            if office.head_state_event.resulting_state != "ENDED":
                write_event(
                    alias,
                    office,
                    actor,
                    "END",
                    "ENDED",
                    source,
                    "circle-leave:" + str(request_uuid),
                    uuid5(request_uuid, str(office.assignment_uuid)),
                    at,
                    composed="CIRCLES:G2:SELF_LEAVE",
                )
        return models.CircleParticipationEnd.objects.using(alias).create(
            participation=place,
            actor_state="PARTY",
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            actor_capacity="STANDING",
            entry_mode="TRANSPORT",
            enterer_identity=actor,
            composing_rule_state="NA_TRANSPORT",
            composer_state="NA_TRANSPORT",
            composed_from_event_uuid=request_uuid,
            effective_at=at,
            received_at=at,
            occurred_at=at,
        )


def activate(*, credential, organism_uuid, circle_uuid, request_uuid, alias="default"):
    """Complete the ruled birth using authority bound to its opening and this activation."""
    from src.intevia.services.organism_command_support import _canonical_uuid

    request_uuid = _canonical_uuid(request_uuid, "request_uuid")
    with command_context(alias, credential, organism_uuid, require_member=True) as (lo, actor, at):
        circle = target_circle(alias, lo, circle_uuid)
        events = models.CircleStateEvent.objects.using(alias)
        with connections[alias].cursor() as cur:
            # The institutional actor and human enterer are separate physical columns.
            cur.execute("""SELECT circle_id,action,resulting_state,enterer_identity_id FROM public.core_circlestateevent
                           WHERE event_uuid=%s""", [request_uuid])
            prior = cur.fetchone()
        if prior is not None:
            if prior == (circle.pk, "ACTIVATE", "ACTIVE", actor.pk):
                return request_uuid
            unavailable()
        opening = events.filter(circle=circle, sequence=1).first()
        if opening is None:
            unavailable()
        act = f"ACTIVATE:{opening.event_uuid}:{request_uuid}"
        if circle.kind == "GENERIC":
            office, decision = lo_source(alias, lo, circle.circle_uuid, act, actor, at)
            ref = f"formation:{opening.event_uuid}|office:{office}"
        else:
            try:
                finding, decision = evidence(alias, lo, circle.circle_uuid, act, actor, actor, at)
            except (scoped.QualificationRefused, CircleAccessRefused):
                unavailable()
            ref = f"formation:{opening.event_uuid}|finding:{finding.determination_uuid}"
        with connections[alias].cursor() as cur:
            cur.execute("SELECT public.circle_activation_problem(%s,%s,%s,%s,%s,%s,%s,%s)",
                        [circle.pk, actor.pk, request_uuid, decision, ref, at, at, at])
            problem, = cur.fetchone()
        if problem is not None:
            reasons = {
                "CIRCLE_G4_TIME": "The activation times do not establish the required order.",
                "CIRCLE_G4_FORMATION": "This Circle needs its governed formation and recorded purpose.",
                "CIRCLE_G4_LATER_EVENT": "A later change is already scheduled on this Circle.",
                "CIRCLE_G4_STATE": "Activation requires a dormant or eligible Circle.",
                "CIRCLE_G4_AUTHORITY": "Activation needs the authority for this Circle’s birth.",
                "CIRCLE_G4_PLACE": "Activation needs a current member place in this Circle.",
                "CIRCLE_G4_COVERAGE": "Appoint a qualified Coordinator in this Circle before activating it.",
                "CIRCLE_G4_OPERATIVE_VOTING": "The Birthing choice is recorded. Activation still needs operative voting.",
                "CIRCLE_G4_GOVERNANCE_AUTHORITY": "GOVERNANCE needs its Birthing choice or whole-membership decision and operative voting.",
                "CIRCLE_G4_DESIGNATION_AUTHORITY": "This functional Circle needs its founding choice or membership decision.",
            }
            raise CircleAccessRefused(reasons[problem])
        return write_event(alias, circle, actor, "ACTIVATE", "ACTIVE", decision, ref, request_uuid, at)
