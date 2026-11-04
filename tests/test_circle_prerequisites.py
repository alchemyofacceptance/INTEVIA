"""Circle prerequisites carried on real formation, seating and receipt routes."""
from dataclasses import replace
from uuid import uuid4

from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_participation as circles
from src.intevia.services import organism_governance_service as governance
from src.intevia.services.organism_command_support import S015CommandReplayMismatch
from tests import s015_0021_support as fixtures
from tests import test_sr_office_actions as offices
from tests.test_c1_rejoin import _attempt


class CirclePrerequisiteTests(TransactionTestCase):
    def setUp(self):
        self.fx = offices.SROfficeActionTests()
        self.fx.setUp()

    def test_purpose_is_bound_to_dormant_creation(self):
        fx = self.fx
        service = governance.OrganismGovernanceService(clock=fixtures.database_now, authority=fx.authority)
        key = uuid4().hex
        command = governance.CreateGovernanceCircleCommand(
            credential=fx.actor.credential, living_organism_uuid=fx.organism.organism_id,
            request_reference="circle-create:" + key, idempotency_key=key, purpose="Oversee trustee handovers",
        )
        err, result = _attempt(lambda: service.create_governance_circle(command))
        self.assertIsNone(err, f"The authorised founder must be able to record a dormant Circle's purpose: {err}")
        event = models.CircleStateEvent.objects.get(event_uuid=result.event_uuid)
        self.assertEqual((event.circle.circle_uuid, event.resulting_state, event.evidence_reference),
                         (result.record_uuid, models.Circle.State.DORMANT, "circle-purpose:Oversee trustee handovers"),
                         "The original Circle event must carry the submitted purpose and stay dormant.")
        self.assertEqual(service.create_governance_circle(command).event_uuid, event.event_uuid,
                         "An identical retry must identify the original Circle event.")
        err, _ = _attempt(lambda: service.create_governance_circle(replace(command, purpose="Issue payments")))
        self.assertIsInstance(err, S015CommandReplayMismatch,
                              "A retry under the same key with another purpose must refuse as changed content.")
        self.assertEqual(models.CircleStateEvent.objects.filter(circle=event.circle).count(), 1,
                         "A changed or repeated request must leave a single formation event.")

        # The real dormant GOVERNANCE creation and enabled setting cannot stand in for a Birthing
        # choice, a later whole-membership decision or operative voting. Raw storage must hold this.
        from django.db import IntegrityError, connection, transaction
        with self.assertRaisesRegex(IntegrityError, "CIRCLE_G4_GOVERNANCE_AUTHORITY"), transaction.atomic(), connection.cursor() as cur:
            at = fixtures.database_now()
            fixtures.append_event(cur, "core_circlestateevent", event.circle_id, 2, "ACTIVATE", "DORMANT", "ACTIVE",
                                  fx.actor.pk, fx.organism.founding_authority_basis_id, uuid4().hex,
                                  pred_id=event.pk, eff=at, occ=at, rec=at,
                                  overrides={"actor_state": "LIVING_ORGANISM", "actor_id": None, "actor_access_epoch": None,
                                             "authority_decision_reference": event.authority_decision_reference,
                                             "evidence_reference": event.evidence_reference})
        event.circle.refresh_from_db()
        self.assertEqual((event.circle.state, event.circle.head_event_id), ("DORMANT", event.pk))

    def test_member_can_read_only_their_own_circle_place(self):
        fx = self.fx
        seated, created = fx._seat_founder()
        other = fx._member()
        own = circles.my_places(credential=fx.actor.credential, organism_uuid=fx.organism.organism_id)
        self.assertEqual(own, (circles.Place(created.record_uuid, fx.founder_member.membership_uuid,
                                             seated.participation_uuid),),
                         "The founder must find their own recorded GOVERNANCE place.")
        self.assertEqual(circles.my_places(credential=other.identity.credential, organism_uuid=fx.organism.organism_id),
                         (), "Another active LO member must not receive the founder's Circle place.")

    def test_service_finding_names_its_role_and_confers_no_circle_office(self):
        # The S fixture supplies an external, independent mandate. The product
        # reader cannot mint one. All identities, the LO and seat are real rows.
        from datetime import timedelta

        from django.db import connection, transaction

        from src.intevia.services import scoped_determinations as scoped
        from tests import test_c2_determinations as findings

        fx = self.fx
        _, created = fx._seat_founder()
        circle = models.Circle.objects.get(circle_uuid=created.record_uuid)
        subject = models.AuthorityBasis.objects.get(pk=fx.ids["basis"])
        role = "service:library:cataloguer@1"
        instrument = findings.ScopedFindingTests()
        instrument.scope = circles.service_role_scope(circle, fx.founder_member, role, subject)
        with transaction.atomic(), connection.cursor() as cursor:
            instrument.verifier = fixtures.seed_identity(cursor, uuid4().hex)
            instrument.issuer = fixtures.seed_identity(cursor, uuid4().hex)
        instrument.basis = instrument.fresh_basis()
        now = fixtures.database_now()
        instrument.end = now + timedelta(days=2)
        instrument.grant = instrument.mandate(now)
        finding = instrument.record()
        cut = fixtures.database_now()

        def read(ref):
            return circles.qualified_service_role("default", circle, fx.founder_member, ref, subject, cut, cut)

        err, result = _attempt(lambda: read(role))
        self.assertIsNone(err, f"The participant's exact SERVICE finding must resolve: {err}")
        self.assertEqual(result.pk, finding.pk, "The reader must return the independently recorded finding.")
        err, _ = _attempt(lambda: read("service:education:tutor@1"))
        self.assertIsInstance(err, scoped.QualificationRefused,
                              "A finding for cataloguing must not qualify the same person as a tutor.")
        self.assertEqual(circles.coordinator_memberships("default", circle, cut, cut), frozenset(),
                         "An LO Coordinator with a service qualification and a Board seat holds no Circle office by them.")
