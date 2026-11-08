"""Operating Circle witnesses using the installed LO, S instrument and seat services."""

from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_commands as commands
from src.intevia.services import circle_participation as places
from tests import s015_0021_support as fixtures
from tests import test_c2_determinations as findings
from tests import test_sr_office_actions as offices
from tests.test_c1_rejoin import _attempt


class CircleOperationsTests(TransactionTestCase):
    def setUp(self):
        self.fx = offices.SROfficeActionTests()
        self.fx.setUp()
        self.lo = self.fx.organism
        self.actor = self.fx.actor
        self.subject = self.lo.founding_authority_basis
        self.args = {
            "credential": self.actor.credential,
            "organism_uuid": self.lo.organism_id,
        }
        self.instrument = findings.ScopedFindingTests()
        with transaction.atomic(), connection.cursor() as cur:
            self.instrument.verifier = fixtures.seed_identity(cur, uuid4().hex)
            self.instrument.issuer = fixtures.seed_identity(cur, uuid4().hex)

    def grant(self, target, act, actor=None, beneficiary=None):
        instrument = self.instrument
        instrument.scope = commands.case_scope(
            self.lo, target, act, actor or self.actor, beneficiary or self.actor
        )
        instrument.basis = instrument.fresh_basis()
        at = fixtures.database_now()
        instrument.end = at + timedelta(days=2)
        instrument.grant = instrument.mandate(at)
        # Independent inputs: the instrument owner supplies the act mandate;
        # the unrelated verifier supplies the S finding. The command issues neither.
        from src.intevia.services.organism_authority import canonical_timestamp

        with transaction.atomic(), connection.cursor() as cur:
            _, pk = fixtures.seed_authority(
                cur,
                uuid4().hex,
                instrument.issuer,
                scope_fingerprint=instrument.scope.fingerprint(),
            )
        delegated = models.AuthorityBasis.objects.get(pk=pk)
        models.AuthorityDerivationEdge.objects.create(
            principal=delegated.authority_principal,
            root_principal=self.subject.authority_principal,
            authority_reference="fixture:circle-instrument",
            evidence_reference="fixture:adopted-authority",
        )
        models.GovernedDetermination.objects.create(
            determination_type=models.GovernedDetermination.DeterminationType.CONSTITUTIONAL_AUTHORITY_QUALIFICATION,
            determiner_id=delegated.issuer_id,
            tested_actor_id=instrument.scope.tested_actor_id,
            safeguard_beneficiary_id=instrument.scope.beneficiary_id,
            relied_upon_authority_principal=delegated.authority_principal,
            evidence_reference=delegated.instrument_reference,
            method_reference="CIRCLES:authority:1|"
            + str(delegated.basis_uuid)
            + "|"
            + canonical_timestamp(instrument.end),
            scope_fingerprint=instrument.scope.fingerprint(),
            independence_state="INDEPENDENT",
            result="QUALIFIED",
            occurred_at=at,
            effective_at=at,
            received_at=at,
        )
        return instrument.record()

    def form(self, founder=None):
        """Use the actual birth route, without preparing any grant or standing record."""
        founder = founder or self.fx.founder_member
        request = uuid4()
        args = dict(credential=founder.identity.credential, organism_uuid=self.lo.organism_id,
                    circle_uuid=uuid4(), kind="GENERIC", purpose="Catalogue local history", request_uuid=request)
        circle = commands.form(**args)
        self.assertEqual(commands.form(**args).pk, circle.pk)
        self.assertEqual(circle.state, "ACTIVE")
        self.assertFalse(models.CircleJoinRequest.objects.filter(circle=circle).exists())
        self.assertEqual(models.CircleParticipation.objects.get(circle=circle).birth_event.event_uuid, request)
        return circle

    def request(self, circle, member):
        return commands.request_place(
            credential=member.identity.credential,
            organism_uuid=self.lo.organism_id,
            circle_uuid=circle.circle_uuid,
            membership_uuid=member.membership_uuid,
            request_uuid=uuid4(),
            consent="member-request",
        )

    def seat(self, circle, member):
        req = self.request(circle, member)
        return commands.admit(
            **self.args, circle_uuid=circle.circle_uuid, request_uuid=req.request_uuid
        )

    def application(self, request, member, outcome):
        return commands.answer_application(credential=member.identity.credential, organism_uuid=self.lo.organism_id,
            circle_uuid=request.circle.circle_uuid, request_uuid=request.request_uuid, outcome=outcome)

    def appoint(self, circle, approver, place, *, refused_approvers=()):
        office_uuid = uuid4()
        args = dict(credential=approver.identity.credential, organism_uuid=self.lo.organism_id,
                    circle_uuid=circle.circle_uuid)
        self.fx.roles.nominate_circle_coordinator(**args, participation_uuid=place.participation_uuid,
            assignment_uuid=office_uuid, request_uuid=uuid4())
        office = models.ContextualRoleAssignment.objects.get(assignment_uuid=office_uuid)
        call = dict(**args, assignment_uuid=office_uuid, request_uuid=uuid4())
        with self.assertRaisesRegex(places.CircleAccessRefused, "current consent"):
            self.fx.roles.appoint_circle_coordinator(**call)
        self.fx.consent.record_office_consent(self.fx._consent_command(place.membership, office))
        if not refused_approvers:
            def substituted():
                with transaction.atomic(), connection.cursor() as cur:
                    from src.intevia.services.organism_role_service import _circle_mandate
                    at = fixtures.database_now()
                    source = _circle_mandate("default", self.lo, circle, f"APPOINT:{office_uuid}:NONE",
                                              approver.identity, place.membership.identity, at, office_uuid)
                    response = models.OfficeAppointmentConsent.objects.get(assignment=office, action="CONSENT")
                    commands.write_event("default", office, approver.identity, "ASSIGN", "ACTIVE", source,
                        f"consent:{response.consent_uuid}|standing:{uuid4()}", uuid4(), at)
                    cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    transaction.set_rollback(True)
            err, _ = _attempt(substituted)
            self.assertIsInstance(err, IntegrityError)
            self.assertIn("CIRCLE_COORDINATOR_CONSENT_OR_STANDING", str(err))
        for outsider in refused_approvers:
            wrong = dict(call, credential=outsider.identity.credential, request_uuid=uuid4())
            with self.assertRaises(places.CircleAccessRefused):
                self.fx.roles.appoint_circle_coordinator(**wrong)
        err, event = _attempt(lambda: self.fx.roles.appoint_circle_coordinator(**call))
        self.assertIsNone(err, f"The Coordinator of this Circle must be able to appoint its consenting member: {type(err).__name__}")
        self.assertEqual(self.fx.roles.appoint_circle_coordinator(**call), event)
        return office

    def test_two_places_one_person_foreign_membership_hidden(self):
        first, second = self.form(), self.form()
        member = self.fx.founder_member
        one, two = (models.CircleParticipation.objects.get(circle=item, membership=member) for item in (first, second))
        cut = fixtures.database_now()
        self.assertNotEqual(
            one.participation_uuid,
            two.participation_uuid,
            "Each Circle must record its own place.",
        )
        self.assertEqual(
            places.people_in_places(
                "default", self.lo, [first.circle_uuid, second.circle_uuid], cut, cut
            ),
            (self.actor.identity_id,),
            "Two Circle places must supply one person to a common decision.",
        )
        foreign = offices.SROfficeActionTests()
        foreign.setUp()

        def attempt_circle(circle_uuid):
            return _attempt(
                lambda: commands.request_place(
                    credential=foreign.actor.credential,
                    organism_uuid=self.lo.organism_id,
                    circle_uuid=circle_uuid,
                    membership_uuid=foreign.founder_member.membership_uuid,
                    request_uuid=uuid4(),
                    consent="member-request",
                )
            )[0]

        actual, missing = attempt_circle(first.circle_uuid), attempt_circle(uuid4())
        self.assertIsInstance(
            actual,
            places.CircleAccessRefused,
            "A foreign membership must refuse before seat details.",
        )
        self.assertEqual(
            (type(actual), str(actual)),
            (type(missing), str(missing)),
            "A foreign member must not discover the Circle.",
        )

        # A real Circle office in one LO is indistinguishable from an unknown office in the other LO.
        office_uuid = models.ContextualRoleAssignment.objects.get(circle=first, membership=member).assignment_uuid
        def try_office(target):
            return _attempt(lambda: foreign.roles.appoint_circle_coordinator(credential=foreign.actor.credential,
                organism_uuid=foreign.organism.organism_id, circle_uuid=first.circle_uuid,
                assignment_uuid=target, request_uuid=uuid4(), effective_until_state="NONE"))[0]

        wrong, absent = try_office(office_uuid), try_office(uuid4())
        self.assertIsInstance(wrong, places.CircleAccessRefused)
        self.assertEqual((type(wrong), str(wrong)), (type(absent), str(absent)))
        sibling, _ = _attempt(lambda: self.fx.roles.appoint_circle_coordinator(
            **self.args, circle_uuid=second.circle_uuid, assignment_uuid=office_uuid,
            request_uuid=uuid4(), effective_until_state="NONE"))
        self.assertEqual((type(sibling), str(sibling)), (type(absent), str(absent)),
                         "A sibling Circle cannot use this nomination.")

        applicant = self.fx._member()
        lo_definition = models.OrganismRoleDefinition.objects.get(living_organism=self.lo, code="COORDINATOR",
                                                                   scope="LIVING_ORGANISM", active=True)
        outsider, _ = self.fx._appoint(lo_definition)
        req = self.request(first, applicant)
        with self.assertRaises(places.CircleAccessRefused):
            self.application(req, outsider, "ACCEPT")
        declined = self.application(req, member, "DECLINE")
        self.assertEqual((declined.outcome, declined.settings_basis), ("DECLINE", "mode:APPLICATION"))
        fresh = self.request(first, applicant)
        with self.assertRaises(places.CircleAccessRefused):
            self.application(fresh, member, "WITHDRAWN")
        withdrawn = self.application(fresh, applicant, "WITHDRAWN")
        self.assertEqual((withdrawn.outcome, withdrawn.actor_id), ("WITHDRAWN", applicant.identity_id))
        self.assertEqual(self.application(fresh, applicant, "WITHDRAWN").pk, withdrawn.pk)
        with self.assertRaises(places.CircleAccessRefused):
            self.application(fresh, member, "ACCEPT")
        self.assertFalse(models.CircleParticipation.objects.filter(circle=first, membership=applicant).exists())

    def test_admission_direct_write_requires_source_and_rolls_back(self):
        circle = self.form()
        member = self.fx._member()
        request = self.request(circle, member)
        # Populate valid columns from a completed office decision, but bind this
        # insert to another applicant’s request.
        other = self.fx._member()
        original = self.seat(circle, other).decision
        count = models.CircleParticipation.objects.count()

        def insert_decision(decision=None):
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(
                    """INSERT INTO core_circleadmissiondecision (
                     decision_uuid,request_id,route,constitutional_route,deciding_party,deciding_identity_id,self_decision,
                     outcome,authority_decision_reference,settings_basis,seat_rule_uuid,seat_id,actor_state,actor_capacity,
                     entry_mode,enterer_identity_id,composing_rule_state,composer_state,occurred_at,effective_at,received_at)
                    SELECT %s,%s,route,constitutional_route,deciding_party,deciding_identity_id,false,outcome,
                      coalesce(%s,authority_decision_reference),coalesce(%s,settings_basis),%s,%s,actor_state,actor_capacity,
                      entry_mode,enterer_identity_id,composing_rule_state,composer_state,statement_timestamp(),
                      statement_timestamp(),statement_timestamp() FROM core_circleadmissiondecision WHERE id=%s""",
                    [
                        uuid4(),
                        request.pk,
                        decision,
                        None,
                        request.seat_rule_uuid,
                        request.seat_id,
                        original.pk,
                    ],
                )
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")

        err, _ = _attempt(insert_decision)
        self.assertIsInstance(
            err,
            IntegrityError,
            "A genuine office decision for another request must not admit this applicant.",
        )
        self.assertIn(
            "CIRCLE_ADMISSION_SOURCE",
            str(err),
            "The exact source check must detect the substitution.",
        )
        at = fixtures.database_now()
        _, decision = commands._office_source("default", "SELECT public.circle_current_office(%s,%s,%s,%s)",
            [circle.pk, self.actor.pk, at, at], request.request_uuid, "ACCEPT", self.actor, member.identity)
        err, _ = _attempt(lambda: insert_decision(decision))
        self.assertIsInstance(
            err,
            IntegrityError,
            "A valid decision with its place missing must roll back.",
        )
        self.assertIn(
            "CIRCLE_ADMISSION_INCOMPLETE",
            str(err),
            "The new completeness guard must cause the rollback.",
        )
        self.assertFalse(
            models.CircleAdmissionDecision.objects.filter(request=request).exists(),
            "Rollback must discard the decision.",
        )
        self.assertEqual(
            models.CircleParticipation.objects.count(),
            count,
            "Rollback must discard the resulting place.",
        )

        # Functional admission replay belongs to the person who decided this request.
        self.fx._custom_entitlement()
        target, purpose = uuid4(), "Maintain the LO accounts"
        self.grant(target, "FORM:FINANCE:" + sha256(purpose.encode()).hexdigest())
        finance = commands.form(**self.args, circle_uuid=target, kind="FINANCE", purpose=purpose, request_uuid=uuid4())
        application = self.request(finance, member)
        act = "ADMIT:" + str(application.request_uuid)
        self.grant(target, act, beneficiary=member.identity)
        replay = dict(circle_uuid=target, request_uuid=application.request_uuid)
        place = commands.admit(**self.args, **replay)
        self.assertEqual(commands.admit(**self.args, **replay).pk, place.pk)
        self.grant(target, act, actor=other.identity, beneficiary=member.identity)
        error, _ = _attempt(lambda: commands.admit(credential=other.identity.credential,
            organism_uuid=self.lo.organism_id, **replay))
        self.assertIsInstance(error, places.CircleAccessRefused)
        self.assertEqual(models.CircleAdmissionDecision.objects.get(request=application).pk, place.decision_id)

    def test_circle_departure_direct_write_and_atomicity(self):
        circle = self.form()
        member = self.fx._member()
        person = member.identity
        own = dict(credential=person.credential, organism_uuid=self.lo.organism_id)
        place = self.seat(circle, member)
        sibling = self.seat(self.form(), member)

        def direct():
            now = fixtures.database_now()
            with transaction.atomic():
                models.CircleParticipationEnd.objects.create(
                    participation=place,
                    actor_state="PARTY",
                    actor=person,
                    enterer_identity=person,
                    actor_access_epoch=person.access_epoch,
                    actor_capacity="STANDING",
                    entry_mode="TRANSPORT",
                    composer_state="NA_TRANSPORT",
                    composing_rule_state="NA_TRANSPORT",
                    composed_from_event_uuid=uuid4(),
                    occurred_at=now,
                    effective_at=now,
                    received_at=now,
                )

        err, _ = _attempt(direct)
        self.assertIsInstance(
            err,
            IntegrityError,
            "An isolated voluntary ending must fail at the database.",
        )
        self.assertIn(
            "CIRCLE_LEAVE_SOURCE",
            str(err),
            "The missing personal leave event must be the cause.",
        )
        args = dict(
            **own,
            circle_uuid=circle.circle_uuid,
            participation_uuid=place.participation_uuid,
            request_uuid=uuid4(),
            confirmed=True,
        )

        def incomplete():
            now = fixtures.database_now()
            with transaction.atomic(), connection.cursor() as cur:
                circle.refresh_from_db()
                commands.write_event(
                    "default",
                    circle,
                    person,
                    "LEAVE_CIRCLE",
                    circle.state,
                    "s015d1:" + "0" * 64,
                    "place:" + str(place.participation_uuid),
                    args["request_uuid"],
                    now,
                )
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")

        err, _ = _attempt(incomplete)
        self.assertIsInstance(
            err,
            IntegrityError,
            "An incomplete personal departure must abort the transaction.",
        )
        self.assertIn(
            "CIRCLE_LEAVE_INCOMPLETE",
            str(err),
            "The missing participation ending must be identified.",
        )
        self.assertFalse(
            models.CircleParticipationEnd.objects.filter(participation=place).exists(),
            "Rollback must preserve the place.",
        )
        self.assertFalse(
            models.CircleStateEvent.objects.filter(
                event_uuid=args["request_uuid"]
            ).exists(),
            "Rollback must discard the leave act.",
        )
        ending = commands.leave(**args)
        self.assertEqual(
            commands.leave(**args).pk,
            ending.pk,
            "Retry must return the same Circle ending.",
        )
        member.refresh_from_db()
        self.assertEqual(
            member.state,
            models.OrganismMembership.State.ACTIVE,
            "Leaving this Circle must preserve LO membership.",
        )
        self.assertEqual(
            places.my_places(**own),
            (
                places.Place(
                    sibling.circle.circle_uuid,
                    member.membership_uuid,
                    sibling.participation_uuid,
                ),
            ),
            "Leaving one Circle must preserve the other Circle place.",
        )

        from src.intevia.services import (
            organism_membership_service as membership_service,
        )
        from tests.test_c1b_departure import DepartureTests

        route = membership_service.OrganismMembershipService(
            authority=self.fx.authority, clock=fixtures.database_now
        )
        # Leave the sibling with one participant and no Coordinator. The parent can
        # first join-and-take, or later appoint that participant with their own consent.
        sibling_circle = sibling.circle
        founder = self.fx.founder_member
        def leave_place(target):
            return commands.leave(credential=target.membership.identity.credential, organism_uuid=self.lo.organism_id,
                circle_uuid=target.circle.circle_uuid, participation_uuid=target.participation_uuid, request_uuid=uuid4(), confirmed=True)
        leave_place(models.CircleParticipation.objects.get(circle=sibling_circle, birth_event__isnull=False))
        self.assertEqual(commands.operating_status(**own, circle_uuid=sibling_circle.circle_uuid), "WITHOUT_COORDINATOR")
        outsider = self.fx._member()
        with self.assertRaises(places.CircleAccessRefused):
            commands.recover_circle(credential=outsider.identity.credential, organism_uuid=self.lo.organism_id,
                circle_uuid=sibling_circle.circle_uuid, request_uuid=uuid4())
        # A genuine LO holder cannot create a vacancy-only place on its own.
        parent = models.ContextualRoleAssignment.objects.get(membership=founder, circle__isnull=True, role_definition__code="COORDINATOR")
        with self.assertRaisesRegex(IntegrityError, "CIRCLE_RESEAT_INCOMPLETE"), transaction.atomic(), connection.cursor() as cur:
            at = fixtures.database_now()
            models.CircleParticipation.objects.create(circle=sibling_circle, membership=founder, recovery_office=parent,
                seat_id=str(founder.membership_uuid), seat_rule_uuid=self.subject.basis_uuid, actor_state="LIVING_ORGANISM",
                actor_capacity="STANDING", entry_mode="TRANSPORT", enterer_identity=self.actor,
                composing_rule_state="NA_TRANSPORT", composer_state="NA_TRANSPORT",
                occurred_at=at,effective_at=at,received_at=at)
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
        recovery = dict(**self.args, circle_uuid=sibling_circle.circle_uuid, request_uuid=uuid4())
        parent_office = commands.recover_circle(**recovery)
        self.assertEqual(commands.recover_circle(**recovery), parent_office)
        joined = models.CircleParticipation.objects.get(circle=sibling_circle, recovery_office__isnull=False)
        self.assertEqual(joined.membership_id, founder.pk)
        self.assertIsNone(joined.decision_id)
        assigned = models.ContextualRoleAssignmentStateEvent.objects.get(assignment__assignment_uuid=parent_office,action="ASSIGN")
        self.assertTrue(assigned.evidence_reference.endswith("|CB1"))
        with self.assertRaises(places.CircleAccessRefused):
            commands.recover_circle(**own, circle_uuid=sibling_circle.circle_uuid, request_uuid=uuid4())
        leave_place(joined)
        member_office = self.appoint(sibling_circle, founder, sibling)
        self.assertTrue(models.ContextualRoleAssignmentStateEvent.objects.get(assignment=member_office,action="ASSIGN")
                        .evidence_reference.endswith("|CB1"))
        self.assertEqual(commands.operating_status(**own, circle_uuid=sibling_circle.circle_uuid), "ACTIVE")
        route.leave_organism(DepartureTests().command(member))
        cascade = models.CircleParticipationEnd.objects.get(participation=sibling)
        self.assertEqual(
            (cascade.actor_state, cascade.composing_rule.code),
            ("NONE", "U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION"),
            "A later LO departure must still end the remaining place under its original U-3 rule.",
        )
        self.assertEqual(
            models.CircleParticipationEnd.objects.get(participation=place).pk,
            ending.pk,
            "The LO cascade must retain the earlier voluntary ending.",
        )

        sibling_circle.refresh_from_db()
        self.assertEqual((sibling_circle.state, sibling_circle.head_event.action), ("CLOSED", "DISSOLVE_CIRCLE"))
        self.assertEqual(sibling_circle.head_event.evidence_reference,
                         f"last-place:{sibling.participation_uuid}|cause:{cascade.composed_from_event_uuid}")
        from src.intevia.services.organism_entitlement_service import _measure_usage
        def used():
            return _measure_usage(self.lo.organism_id, "circles", fixtures.database_now(), "default", known_at=None).counted
        before = used()
        last = models.CircleParticipation.objects.get(circle=circle, birth_event__isnull=False)
        with self.assertRaisesRegex(IntegrityError, "CIRCLE_DISSOLUTION_SOURCE"), transaction.atomic():
            circle.refresh_from_db()
            commands.write_event("default", circle, self.actor, "DISSOLVE_CIRCLE", "CLOSED", "s015d1:"+"0"*64,
                "last-place:"+str(last.participation_uuid)+"|cause:"+str(uuid4()), uuid4(), fixtures.database_now(),
                composed="CIRCLES:L5:LAST_MEMBER")
        with transaction.atomic():
            leave_place(last)
            circle.refresh_from_db()
            self.assertEqual(circle.state, "CLOSED")
            transaction.set_rollback(True)
        circle.refresh_from_db()
        self.assertEqual(circle.state, "ACTIVE")
        self.assertFalse(models.CircleParticipationEnd.objects.filter(participation=last).exists())
        final = leave_place(last)
        circle.refresh_from_db()
        self.assertEqual((circle.state, circle.head_event.action, used()), ("CLOSED", "DISSOLVE_CIRCLE", before - 1))
        self.assertEqual(circle.head_event.evidence_reference, f"last-place:{last.participation_uuid}|cause:{final.composed_from_event_uuid}")

    def test_owned_work_waits_for_an_authorised_parent(self):
        from src.intevia.services import circle_work as work

        from unittest.mock import patch
        from src.intevia.services.organism_role_service import OrganismRoleService

        nominate = OrganismRoleService.nominate_circle_coordinator
        def before_first_office(service, **kwargs):
            circle = models.Circle.objects.get(circle_uuid=kwargs["circle_uuid"])
            opening = models.CircleStateEvent.objects.get(circle=circle, sequence=1)
            event_uuid = uuid4()
            with self.assertRaisesRegex(IntegrityError, "CIRCLE_G4_ACTIVE_EDGE"), transaction.atomic(), connection.cursor() as cur:
                at = fixtures.database_now()
                fixtures.append_event(cur, "core_circlestateevent", circle.pk, 2, "OPEN_WORK", "ACTIVE", "ACTIVE",
                    self.actor.pk, self.subject.pk, uuid4().hex, pred_id=opening.pk, eff=at, occ=at, rec=at)
            with transaction.atomic(), connection.cursor() as cur:
                at = fixtures.database_now()
                act = f"ACTIVATE:{opening.event_uuid}:{event_uuid}"
                office, decision = commands.lo_source("default", self.lo, circle.circle_uuid, act, self.actor, at)
                def direct():
                    fixtures.append_event(cur, "core_circlestateevent", circle.pk, 2, "ACTIVATE", "DORMANT", "ACTIVE",
                        self.actor.pk, self.subject.pk, uuid4().hex, pred_id=opening.pk, eff=at, occ=at, rec=at,
                        event_uuid=event_uuid, overrides={"actor_state":"LIVING_ORGANISM","actor_id":None,
                        "actor_access_epoch":None, "authority_decision_reference":decision,
                        "evidence_reference":f"formation:{opening.event_uuid}|office:{office}"})
                err, _ = _attempt(direct)
                transaction.set_rollback(True)
            self.assertIsInstance(err, IntegrityError, "The direct writer activated an uncovered Circle.")
            self.assertIn("CIRCLE_G4_COVERAGE", str(err))
            self.assertFalse(models.CircleStateEvent.objects.filter(event_uuid=event_uuid).exists())
            return nominate(service, **kwargs)

        with patch.object(OrganismRoleService, "nominate_circle_coordinator", before_first_office):
            circle = self.form()
        successor = self.fx._member()
        successor_place = self.seat(circle, successor)
        founder_place = models.CircleParticipation.objects.get(circle=circle, birth_event__isnull=False)
        uid = uuid4()
        role = "service:library:cataloguer@1"
        title = "Catalogue the donated collection"
        ref = work.opened_reference(title, role)
        self.grant(
            circle.circle_uuid,
            "OPEN_WORK:" + str(uid) + ":" + sha256(ref.encode()).hexdigest(),
        )
        key = work.open_work(
            **self.args,
            circle_uuid=circle.circle_uuid,
            work_uuid=uid,
            title=title,
            role_reference=role,
        )
        task = models.CircleStateEvent.objects.get(circle=circle, event_uuid=uid, action="OPEN_WORK")
        read = dict(**self.args, circle_uuid=circle.circle_uuid, work_uuid=uid)
        from src.intevia.services.scoped_determinations import QualificationRefused
        with self.assertRaises(QualificationRefused):
            work.claim_work(**read, request_uuid=uuid4())
        # SERVICE:v1 is a separate, labelled S fixture. The production opening and both
        # appointments use their own records. A successor does not inherit this finding.
        self.grant(circle.circle_uuid, "CIRCLE_SERVICE:"+role)
        # The work opening already owns this event UUID. A qualified claimant must get a controlled refusal.
        error, _ = _attempt(lambda: work.claim_work(**read, request_uuid=task.event_uuid))
        self.assertIsInstance(error, places.CircleAccessRefused)
        self.assertFalse(models.CircleStateEvent.objects.filter(circle=circle, action="CLAIM_WORK").exists())
        first_claim = work.claim_work(**read, request_uuid=uuid4())
        self.assertEqual(work.parent_work(**read)["status"], "CLAIMED")
        commands.leave(**self.args, circle_uuid=circle.circle_uuid, participation_uuid=founder_place.participation_uuid,
                       request_uuid=uuid4(), confirmed=True)
        waiting = work.parent_work(**read)
        self.assertEqual((key, waiting["work"], waiting["circle"], waiting["title"], waiting["status"]),
                         (uid, uid, circle.circle_uuid, title, "WAITING_FOR_PARENT_GOVERNANCE"),
                         "The original Circle-owned work must wait visibly when its last holder leaves.")
        with self.assertRaises(places.CircleAccessRefused):
            work.claim_work(**read, request_uuid=uuid4())
        own = dict(credential=successor.identity.credential, organism_uuid=self.lo.organism_id, circle_uuid=circle.circle_uuid)
        self.assertEqual(commands.operating_status(**own), "WITHOUT_COORDINATOR")
        replacement = commands.recover_circle(**own, request_uuid=uuid4())
        assigned = models.ContextualRoleAssignmentStateEvent.objects.get(assignment__assignment_uuid=replacement,action="ASSIGN")
        self.assertTrue(assigned.evidence_reference.endswith("|RC1"))
        self.assertEqual(places.coordinator_memberships("default", circle, fixtures.database_now(), fixtures.database_now()),
                         {successor.membership_uuid})
        self.assertEqual(work.parent_work(**read)["status"], "AVAILABLE")
        with self.assertRaises(QualificationRefused):
            work.claim_work(**own, work_uuid=uid, request_uuid=uuid4())
        self.grant(circle.circle_uuid,"CIRCLE_SERVICE:"+role,actor=successor.identity,beneficiary=successor.identity)
        second_args = dict(**own, work_uuid=uid, request_uuid=uuid4())
        second_claim = work.claim_work(**second_args)
        self.assertEqual(work.claim_work(**second_args), second_claim)
        held = work.parent_work(**read)
        self.assertEqual((held["status"], held["rule"], held["work"]), ("CLAIMED",task.authority_decision_reference,uid))
        commands.leave(**own, participation_uuid=successor_place.participation_uuid, request_uuid=uuid4(),confirmed=True)
        closed = work.parent_work(**read)
        self.assertEqual((closed["status"],closed["work"],closed["rule"]), ("CLOSED_UNFINISHED",uid,held["rule"]))
        self.assertEqual(set(models.CircleStateEvent.objects.filter(circle=circle,action="CLAIM_WORK",
            evidence_reference="work:"+str(uid)).values_list("event_uuid",flat=True)), {first_claim,second_claim})
        self.assertEqual(models.CircleStateEvent.objects.filter(circle=circle,action="OPEN_WORK").count(),1)
        with self.assertRaises(places.CircleAccessRefused):
            work.claim_work(**own, work_uuid=uid, request_uuid=uuid4())

    def test_circle_coordinator_operating_journey(self):
        """CIR-08: B holds an office in this Circle only. D holds an LO office only."""
        from unittest.mock import patch

        original = self.fx._snapshot()
        append = commands.write_event
        def substitute_place(alias, anchor, actor, action, outcome, source, ref, event_uuid, at, **kwargs):
            if action == "ASSIGN":
                parts = ref.split("|")
                self.assertEqual(parts[0], "ord-standing:1")
                parts[2] = "place:" + str(uuid4())
                ref = "|".join(parts)
            return append(alias, anchor, actor, action, outcome, source, ref, event_uuid, at, **kwargs)

        with patch.object(commands, "write_event", substitute_place):
            refused, _ = _attempt(self.form)
        self.assertIsInstance(refused, IntegrityError, "The assignment guard must reject substituted founder evidence.")
        self.assertIn("CIRCLE_COORDINATOR_CONSENT_OR_STANDING", str(refused))
        self.assertEqual(self.fx._snapshot(), original, "Invalid first standing must roll back the whole birth.")
        a = self.fx.founder_member
        d, _ = self.fx._appoint(models.OrganismRoleDefinition.objects.get(living_organism=self.lo, code="COORDINATOR",
                                                                          scope="LIVING_ORGANISM", active=True))
        circle = self.form()
        founder_place = models.CircleParticipation.objects.get(circle=circle, membership=a)
        assigned = models.ContextualRoleAssignmentStateEvent.objects.get(assignment__circle=circle, action="ASSIGN")
        issuer = models.ContextualRoleAssignment.objects.get(membership=a, circle__isnull=True, role_definition__code="COORDINATOR")
        self.assertEqual(assigned.evidence_reference.split("|")[:4], ["ord-standing:1", str(issuer.assignment_uuid),
            "place:"+str(founder_place.participation_uuid), str(assigned.predecessor.event_uuid)])
        self.assertEqual(assigned.evidence_reference.split("|")[-1], str(assigned.event_uuid))
        cut = fixtures.database_now()
        self.assertEqual(places.coordinator_memberships("default", circle, cut, cut), {a.membership_uuid})
        nominee = self.fx._member()
        place = self.seat(circle, nominee)
        office = self.appoint(circle, a, place)
        target = self.fx._member()
        pending = self.request(circle, target)
        new_place = self.application(pending, nominee, "ACCEPT")
        self.assertEqual(new_place.decision.deciding_identity_id, nominee.identity_id)
        sibling = self.form(d)
        sibling_only = self.fx._member()
        req = self.request(sibling, sibling_only)
        sibling_place = self.application(req, d, "ACCEPT")
        self.appoint(sibling, d, sibling_place)
        for foreign_office in (d, sibling_only):
            with self.assertRaises(places.CircleAccessRefused):
                self.fx.roles.nominate_circle_coordinator(credential=foreign_office.identity.credential,
                    organism_uuid=self.lo.organism_id, circle_uuid=circle.circle_uuid,
                    participation_uuid=new_place.participation_uuid, assignment_uuid=uuid4(), request_uuid=uuid4())
        # A direct SQL fault in the approval guard must be seen as an assertion, never as a fixture error.
        next_office = self.appoint(circle, nominee, new_place, refused_approvers=(d, sibling_only))
        later = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=next_office, action="ASSIGN")
        consent = models.OfficeAppointmentConsent.objects.get(assignment=next_office, action="CONSENT")
        self.assertEqual(later.evidence_reference.split("|")[:4], ["ord-standing:1", str(office.assignment_uuid),
            "place:"+str(new_place.participation_uuid), str(consent.consent_uuid)])
        self.assertEqual(later.evidence_reference.split("|")[-1], str(later.event_uuid))
        definitions = models.OrganismRoleDefinition.objects.filter(living_organism=self.lo, code="COORDINATOR", active=True)
        self.assertEqual({(r.scope, r.definition_version, r.office_label) for r in definitions}, {
            ("CIRCLE", 1, "Circle Coordinator"), ("LIVING_ORGANISM", 1, "LO Coordinator"),
        }, "The same Coordinator code must have one current version in each context.")
        with connection.cursor() as cur:
            cur.execute("""SELECT indexrelid::regclass::text,
                       ARRAY(SELECT pg_get_indexdef(indexrelid,n,true) FROM generate_series(1,indnkeyatts) n),
                       pg_get_expr(indpred,indrelid)
                       FROM pg_index WHERE indrelid='core_organismroledefinition'::regclass AND indisunique
                       AND indexrelid::regclass::text IN ('s015_role_definition_version_uniq','s015_0021_role_active_uniq',
                                                         'cir_other_role_version_uniq','cir_other_role_active_uniq')""")
            indexes = {name: (cols, predicate) for name, cols, predicate in cur.fetchall()}
        for name, coordinated, versioned in (
            ("s015_role_definition_version_uniq", True, True), ("s015_0021_role_active_uniq", True, False),
            ("cir_other_role_version_uniq", False, True), ("cir_other_role_active_uniq", False, False),
        ):
            with self.subTest(index=name):
                self.assertIn(name, indexes)
                cols, predicate = indexes[name]
                self.assertEqual(cols, ["living_organism_id", "code"] + (["scope"] if coordinated else [])
                                 + (["definition_version"] if versioned else []))
                self.assertIn("COORDINATOR", predicate)
                self.assertEqual("active" in predicate, not versioned)

        # Each ordinary key must still refuse independently across scopes. The source definition is
        # created through the existing governed service. Only the attempted violating rows are raw.
        _, other_role = self.fx._define()
        for version, active, expected in ((other_role.definition_version, False, "cir_other_role_version_uniq"),
                                          (other_role.definition_version + 1, True, "cir_other_role_active_uniq")):
            with self.assertRaises(IntegrityError) as refused, transaction.atomic(), connection.cursor() as cur:
                cur.execute("""INSERT INTO core_organismroledefinition
                    (role_uuid,living_organism_id,code,scope,definition_version,active,created_at)
                    SELECT %s,living_organism_id,code,'CIRCLE',%s,%s,statement_timestamp()
                    FROM core_organismroledefinition WHERE id=%s""", [uuid4(), version, active, other_role.pk])
            self.assertEqual(refused.exception.__cause__.diag.constraint_name, expected,
                             "The original scope-independent rule for other role codes must refuse this write.")

        at = fixtures.database_now()
        self.assertEqual(places.coordinator_memberships("default", circle, at, at),
                         {a.membership_uuid, nominee.membership_uuid, target.membership_uuid})
        with connection.cursor() as cur:
            cur.execute("""SELECT membership_id FROM public.c2_occupants(%s,%s,%s)
                WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR'""", [self.lo.pk, at, at])
            self.assertEqual({row[0] for row in cur.fetchall()}, {a.pk, d.pk})
            cur.execute("""SELECT actor_state,enterer_identity_id,effective_until_state,effective_until
                FROM core_contextualroleassignmentstateevent WHERE assignment_id=%s AND action='ASSIGN'""", [office.pk])
            self.assertEqual(cur.fetchone(), ("LIVING_ORGANISM", self.actor.pk, "NONE", None))
        # The founder is attributable on both chains. Another member cannot replay this birth.
        opening = models.CircleStateEvent.objects.get(circle=circle, sequence=1)
        with self.assertRaises(places.CircleAccessRefused):
            commands.form(credential=nominee.identity.credential, organism_uuid=self.lo.organism_id,
                          circle_uuid=circle.circle_uuid, kind="GENERIC", purpose="Catalogue local history",
                          request_uuid=opening.event_uuid)
        active = circle.head_event
        self.assertEqual((active.action, active.prior_state, active.resulting_state), ("ACTIVATE","DORMANT","ACTIVE"))
        # A legitimately ACTIVE predecessor must support the next governed same-state act.
        from src.intevia.services import circle_work as work
        task_uuid = uuid4()
        title, role = "Catalogue the Circle records", "service:library:cataloguer@1"
        task_ref = work.opened_reference(title, role)
        self.grant(circle.circle_uuid, f"OPEN_WORK:{task_uuid}:" + sha256(task_ref.encode()).hexdigest())
        work.open_work(**self.args, circle_uuid=circle.circle_uuid, work_uuid=task_uuid, title=title, role_reference=role)
        task = models.CircleStateEvent.objects.get(event_uuid=task_uuid)
        self.assertEqual((task.predecessor_id, task.prior_state, task.resulting_state), (active.pk, "ACTIVE", "ACTIVE"))

        from django.apps import apps
        from importlib import import_module
        g4 = import_module("core.migrations.0061_circle_qualified_activation")
        with self.assertRaisesRegex(RuntimeError, "A Circle activation is recorded"):
            with connection.schema_editor(atomic=True) as editor:
                g4.reverse(apps, editor)
        circle.refresh_from_db()
        self.assertEqual((circle.state, circle.head_event_id), ("ACTIVE", task.pk))

        # Exercise 0060's own rollback preflight without unapplying any later part's migration.
        migration = import_module("core.migrations.0060_circle_coordinators")
        with self.assertRaisesRegex(RuntimeError, "Circle Coordinator definition is recorded"):
            with connection.schema_editor(atomic=True) as editor:
                migration.reverse(apps, editor)
        office.refresh_from_db()
        self.assertEqual(office.state, "ACTIVE", "A refused rollback must retain the appointment.")

    test_circle_coordinator_operating_journey.ISOLATION_FULL_CAPTURE = True
