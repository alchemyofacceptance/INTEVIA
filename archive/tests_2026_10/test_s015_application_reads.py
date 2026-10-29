from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timedelta, timezone
from unittest import skipUnless
from uuid import uuid4

from django.db import connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import (
    Application,
    ApplicationLineageEvent,
    ContextualRoleAssignment,
    Identity,
    IdentityTransition,
    OrganismCommandReceipt,
    OrganismMembership,
)
from src.intevia.services.organism_application_reads import OrganismApplicationReader
from src.intevia.services.organism_application_service import (
    ClaimApplicationCommand,
    DecideApplicationCommand,
    OrganismApplicationService,
    S015ApplicationRefused,
    SubmitApplicationCommand,
    WithdrawApplicationCommand,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015OwnApplicantAuthorityRequest,
    S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityResponse,
)
from tests.s015_0021_support import (
    T0,
    append_event,
    database_now,
    empty_fp,
    fresh,
    refresh,
    seed_identity,
)
from tests.sb2_route_support import birth, prepare_route, route_fixture_state, route_test_now


NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)

_support_append_event = append_event
_support_refresh = refresh


def append_event(*args, **kwargs):
    kwargs.setdefault("rec", NOW)
    return _support_append_event(*args, **kwargs)


def refresh(cur, table, aggregate_id, state_at=None):
    return _support_refresh(
        cur, table, aggregate_id, NOW if state_at is None else state_at
    )
POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "S3-FU application reads require PostgreSQL")


class _Provider:
    """TEST-ONLY provider substitute for application-read service witnesses."""

    def evaluate_organism_authority(self, request):
        fields = asdict(request)
        if isinstance(request, S015OwnApplicantAuthorityRequest):
            return S015OwnApplicantAuthorityResponse(**fields, authority_reference="s3:test:applicant")
        return S015PostFoundingAuthorityResponse(**fields, authority_reference="s3:test:coordinator")


@POSTGRESQL_ONLY
class S015ApplicationReadTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tag = f"application-read-{fresh()}"
        self.route = prepare_route(tag=self.tag)
        birth(self.route)
        self.ids, self.gateway = route_fixture_state(self.route)
        self.route_times = self.ids["recorded_times"]
        global T0, NOW
        T0 = self.route_times.founding_recorded_at
        NOW = route_test_now(self.route)
        self.coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self.provider = _Provider()
        self.authority = S015Authority(provider=self.provider)
        self.service = OrganismApplicationService(authority=self.authority, clock=lambda: NOW)
        self.reader = OrganismApplicationReader(authority=self.authority, clock=lambda: NOW)

    def _activate(self, identity):
        # TEST-ONLY identity history establishing an active reader for this witness.
        provision = IdentityTransition.objects.create(
            identity=identity,
            action=IdentityTransition.Action.PROVISION,
            prior_state=None,
            resulting_state=Identity.AccessState.PENDING,
            authority_reference="s3:test:provision",
            technical_executor="S3_TEST",
            evidence_reference="s3:test:identity-history",
            reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
            correlation_id=uuid4(),
            occurred_at=T0,
        )
        return IdentityTransition.objects.create(
            identity=identity,
            action=IdentityTransition.Action.ACTIVATE,
            prior_state=Identity.AccessState.PENDING,
            resulting_state=Identity.AccessState.ACTIVE,
            authority_reference="s3:test:activate",
            technical_executor="S3_TEST",
            evidence_reference="s3:test:identity-history",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
            previous_transition=provision,
            occurred_at=NOW,
        )

    def _new_identity(self, label):
        # TEST-ONLY identity fixture creation for the audience witnesses.
        with transaction.atomic(), connection.cursor() as cursor:
            identity_id = seed_identity(cursor, f"{label}-{self.tag}-{fresh()}")
        identity = Identity.objects.select_related("credential").get(pk=identity_id)
        self._activate(identity)
        return identity

    def _application(self, applicant=None, predecessor=None):
        applicant = applicant or self._new_identity("applicant")
        token = fresh()
        result = self.service.submit_application(SubmitApplicationCommand(
            credential=applicant.credential,
            living_organism_id=self._organism_uuid(),
            request_reference=f"s3:read:submit:{token}",
            idempotency_key=f"s3:read:submit:{token}",
            occurred_at=NOW,
            effective_at=NOW,
            predecessor_reference=None if predecessor is None else predecessor.application_uuid,
            answer="Test-only applicant answer",
        ))
        return applicant, Application.objects.get(application_uuid=result.application_uuid)

    def _organism_uuid(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT organism_id FROM public.core_livingorganism WHERE id = %s", [self.ids["lo"]])
            return cursor.fetchone()[0]

    def _withdraw(self, applicant, application):
        token = fresh()
        return self.service.withdraw_application(WithdrawApplicationCommand(
            credential=applicant.credential,
            application_uuid=application.application_uuid,
            request_reference=f"s3:read:withdraw:{token}",
            idempotency_key=f"s3:read:withdraw:{token}",
            occurred_at=NOW,
            effective_at=NOW,
        ))

    def _claim(self, application, coordinator=None):
        coordinator = coordinator or self.coordinator
        token = fresh()
        return self.service.claim_application(ClaimApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"s3:read:claim:{token}",
            idempotency_key=f"s3:read:claim:{token}",
            occurred_at=NOW,
            effective_at=NOW,
        ))

    def _accept(self, application, coordinator=None):
        coordinator = coordinator or self.coordinator
        self._claim(application, coordinator)
        token = fresh()
        return self.service.decide_application(DecideApplicationCommand(
            credential=coordinator.credential,
            application_uuid=application.application_uuid,
            request_reference=f"s3:read:decide:{token}",
            idempotency_key=f"s3:read:decide:{token}",
            occurred_at=NOW,
            effective_at=NOW,
            outcome="ACCEPT",
        ))

    def _ordinary_member(self, identity):
        # TEST-ONLY active membership construction; deliberately creates no contextual office.
        membership_uuid = uuid4()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.core_organismmembership (membership_uuid, state, created_at, "
                "current_state_effective_at, state_known_at, state_source_event_set_fingerprint, "
                "identity_id, living_organism_id) VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) "
                "RETURNING id",
                [membership_uuid, T0, NOW, empty_fp(cursor, "core_organismmembership", membership_uuid),
                 identity.pk, self.ids["lo"]],
            )
            membership_id = cursor.fetchone()[0]
            append_event(
                cursor,
                "core_organismmembershiptransition",
                membership_id,
                1,
                "JOIN",
                None,
                "ACTIVE",
                identity.pk,
                self.ids["basis"],
                f"read-member:{self.tag}:{fresh()}",
                eff=T0,
                occ=T0,
                rec=NOW,
                extra={"reason_class": "TEST"},
            )
            refresh(cursor, "core_organismmembership", membership_id, T0)
        return OrganismMembership.objects.get(pk=membership_id)

    def _foreign_coordinator(self):
        foreign_tag = f"{self.tag}-foreign-{fresh()}"
        foreign = prepare_route(tag=foreign_tag)
        return foreign.founder

    def _end_coordinator_office(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_role_service import (
            OrganismRoleService, TransitionAssignmentCommand,
        )

        office_id = self.ids["assignments"]["COORDINATOR"]
        office = ContextualRoleAssignment.objects.get(pk=office_id)

        def database_now():
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                return cursor.fetchone()[0]

        service = OrganismRoleService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=database_now,
        )
        service.transition_assignment(TransitionAssignmentCommand(
            credential=self.coordinator.credential,
            request_reference=f"read-office-end:{self.tag}:{fresh()}",
            idempotency_key=f"read-office-end:key:{self.tag}:{fresh()}",
            occurred_at=NOW,
            effective_at=NOW,
            assignment_uuid=office.assignment_uuid,
            action="END",
            resulting_state="ENDED",
        ))

    def _deactivate(self, identity):
        # TEST-ONLY identity transition used to witness the inactive-reader refusal.
        previous = IdentityTransition.objects.filter(identity=identity).order_by("-occurred_at", "-pk").first()
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            deactivated_at = cursor.fetchone()[0]
        IdentityTransition.objects.create(
            identity=identity,
            action=IdentityTransition.Action.DEACTIVATE,
            prior_state=Identity.AccessState.ACTIVE,
            resulting_state=Identity.AccessState.DEACTIVATED,
            authority_reference="s3:test:deactivate",
            technical_executor="S3_TEST",
            evidence_reference="s3:test:identity-history",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
            previous_transition=previous,
            occurred_at=deactivated_at,
        )

    def test_ra_01_current_coordinator_reads_answered_version(self):
        applicant, application = self._application()
        view = self.reader.read_application(application.application_uuid, self.coordinator)
        self.assertEqual(view.answer, application.answer)
        self.assertEqual(view.question_text, application.question_version.question_text)
        self.assertEqual(view.answer_format, application.question_version.answer_format)

    def test_ra_02_ordinary_member_is_refused(self):
        _, application = self._application()
        self.reader.read_application(application.application_uuid, self.coordinator)
        member = self._new_identity("ordinary-member")
        self._ordinary_member(member)
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU read refused: not in the application's audience",
        ):
            self.reader.read_application(application.application_uuid, member)

    def test_ra_03_applicant_reads_own_frozen_view(self):
        applicant, application = self._application()
        view = self.reader.read_application(application.application_uuid, applicant)
        with self.assertRaises(FrozenInstanceError):
            setattr(view, "answer", "changed")

    def test_ra_04_other_learner_is_refused(self):
        applicant, application = self._application()
        self.reader.read_application(application.application_uuid, applicant)
        other_learner = self._new_identity("other-learner")
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU read refused: not in the application's audience",
        ):
            self.reader.read_application(application.application_uuid, other_learner)

    def test_ra_05_coordinator_of_different_lo_is_refused(self):
        _, application = self._application()
        self.reader.read_application(application.application_uuid, self.coordinator)
        foreign_coordinator = self._foreign_coordinator()
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU read refused: not in the application's audience",
        ):
            self.reader.read_application(application.application_uuid, foreign_coordinator)

    def test_ra_06_ended_coordinator_office_is_refused_now(self):
        _, application = self._application()
        self.reader.read_application(application.application_uuid, self.coordinator)
        self._end_coordinator_office()
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU read refused: not in the application's audience",
        ):
            self.reader.read_application(application.application_uuid, self.coordinator)

    def test_ra_07_applicant_and_coordinator_read_withdrawn_application(self):
        applicant, application = self._application()
        self._withdraw(applicant, application)
        self.reader.read_application(application.application_uuid, self.coordinator)
        self.reader.read_application(application.application_uuid, applicant)

    def test_ra_08_inactive_applicant_is_refused(self):
        applicant, application = self._application()
        self.reader.read_application(application.application_uuid, applicant)
        self._deactivate(applicant)
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU read refused: reader not active",
        ):
            self.reader.read_application(application.application_uuid, applicant)

    def test_rh_01_coordinator_reads_ordered_submission_and_claim_events(self):
        _, application = self._application()
        self._claim(application)
        history = self.reader.read_application_history(application.application_uuid, self.coordinator)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT e.sequence, e.action, e.actor_state, e.entry_mode, "
                "e.authority_decision_reference, e.evidence_reference "
                "FROM public.core_applicationlineageevent e "
                "JOIN public.core_applicationlineage l ON l.id = e.application_lineage_id "
                "WHERE l.application_id = %s ORDER BY e.sequence",
                [application.pk],
            )
            stored = cursor.fetchall()
        self.assertEqual([row.sequence for row in history], [row[0] for row in stored])
        self.assertEqual(
            [(row.action, row.actor_state, row.entry_mode, row.authority_decision_reference, row.evidence_reference)
             for row in history],
            [tuple(row[1:]) for row in stored],
        )

    def test_rh_03_ordinary_member_is_refused(self):
        _, application = self._application()
        self.reader.read_application_history(application.application_uuid, self.coordinator)
        member = self._new_identity("history-member")
        self._ordinary_member(member)
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU read refused: not in the application's audience",
        ):
            self.reader.read_application_history(application.application_uuid, member)

    def test_rh_04_applicant_reads_frozen_history_row(self):
        applicant, application = self._application()
        history = self.reader.read_application_history(application.application_uuid, applicant)
        with self.assertRaises(FrozenInstanceError):
            setattr(history[0], "action", "changed")

    def test_rh_05_non_applicant_learner_is_refused(self):
        applicant, application = self._application()
        self.reader.read_application_history(application.application_uuid, applicant)
        other_learner = self._new_identity("other-history-learner")
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU read refused: not in the application's audience",
        ):
            self.reader.read_application_history(application.application_uuid, other_learner)

    def test_rh_06_linked_application_grants_no_audience_right(self):
        learner_y = self._new_identity("learner-y")
        application_y, withdrawn_y = self._application(learner_y)
        self._withdraw(application_y, withdrawn_y)
        _, linked_application_y = self._application(learner_y, predecessor=withdrawn_y)

        learner_z = self._new_identity("learner-z")
        application_z, withdrawn_z = self._application(learner_z)
        self._withdraw(application_z, withdrawn_z)
        self.reader.read_application_history(withdrawn_y.application_uuid, learner_y)
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU read refused: not in the application's audience",
        ):
            self.reader.read_application_history(withdrawn_z.application_uuid, learner_y)
        self.assertEqual(linked_application_y.predecessor_reference_id, withdrawn_y.pk)

    def test_rh_07_severed_applicant_is_returned_as_severed(self):
        applicant, application = self._application()
        # The shared seed creates this row; deleting it records the severance.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.core_identityresolution WHERE identity_id = %s",
                [applicant.pk],
            )
        history = self.reader.read_application_history(application.application_uuid, self.coordinator)
        applicant_actor = next(row.actor for row in history if row.action == "SUBMIT_APPLICATION")
        self.assertEqual(applicant_actor.resolution, "SEVERED")
        self.assertIsNone(applicant_actor.display_name)

    def test_rh_08_accepted_history_returns_recorded_evidence_only(self):
        _, application = self._application()
        self._accept(application)
        history = self.reader.read_application_history(application.application_uuid, self.coordinator)
        accepted = next(row for row in history if row.action == "DECIDE_APPLICATION")
        stored = ApplicationLineageEvent.objects.get(
            application_lineage__application=application,
            sequence=accepted.sequence,
        )
        self.assertEqual(accepted.evidence_reference, stored.evidence_reference)
        self.assertFalse(hasattr(accepted, "membership_chain"))

    def test_rh_09_evidence_is_not_dereferenced(self):
        _, application = self._application()
        self._accept(application)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT e.evidence_reference FROM public.core_applicationlineageevent e "
                "JOIN public.core_applicationlineage l ON l.id = e.application_lineage_id "
                "WHERE l.application_id = %s ORDER BY e.sequence",
                [application.pk],
            )
            stored_references = [row[0] for row in cursor.fetchall()]
        with CaptureQueriesContext(connection) as captured:
            history = self.reader.read_application_history(application.application_uuid, self.coordinator)
        self.assertEqual([row.evidence_reference for row in history], stored_references)
        referenced_tables = (
            "core_applicationdecision",
            "core_csaassignment",
            "core_applicationwithdrawal",
        )
        touched = [
            query["sql"] for query in captured.captured_queries
            if any(table in query["sql"].lower() for table in referenced_tables)
        ]
        self.assertEqual(touched, [])

    def test_rh_10_former_coordinator_is_refused_now(self):
        _, application = self._application()
        self._claim(application)
        self.reader.read_application_history(application.application_uuid, self.coordinator)
        self._end_coordinator_office()
        with self.assertRaisesMessage(
            S015ApplicationRefused,
            "S3FU read refused: not in the application's audience",
        ):
            self.reader.read_application_history(application.application_uuid, self.coordinator)

    def test_ro_01_reads_issue_only_selects_and_change_no_rows(self):
        _, application = self._application()
        watched = {
            "core_application": Application,
            "core_applicationlineageevent": ApplicationLineageEvent,
            "core_organismcommandreceipt": OrganismCommandReceipt,
        }
        before = {table: model.objects.count() for table, model in watched.items()}
        with CaptureQueriesContext(connection) as captured:
            self.reader.read_application(application.application_uuid, self.coordinator)
            self.reader.read_application_history(application.application_uuid, self.coordinator)
        self.assertTrue(captured.captured_queries)
        self.assertTrue(all(
            query["sql"].lstrip().upper().startswith("SELECT")
            for query in captured.captured_queries
        ))
        self.assertEqual(
            {table: model.objects.count() for table, model in watched.items()},
            before,
        )