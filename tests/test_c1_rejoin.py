"""C1a witnesses, prepared for the owned PostgreSQL 17 clone route.

Fixture authority is deliberately supplied, as in the O-24 service witnesses.
These tests prove service/history behavior under that qualification, not the
production provider's founding, registration or Coordinator qualification.
No provider/network; no privileges or trigger bypass introduced by this file.
"""

import threading
from dataclasses import asdict, replace
from queue import Queue
from uuid import uuid4

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.models import (
    Application, ApplicationDecision, ApplicationDecisionEffect, ContextualRoleAssignment, Identity,
    IdentityTransition, LivingOrganism, MembershipCondition, OrganismMembership,
    OrganismMembershipTransition, OrganismRoleDefinition,
)
from src.intevia.services.organism_application_service import (
    ClaimApplicationCommand, DecideApplicationCommand, OrganismApplicationService,
    S015ApplicationRefused, SubmitApplicationCommand,
)
from src.intevia.services.organism_authority import (
    S015Authority, S015OwnApplicantAuthorityRequest, S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityResponse,
)
from src.intevia.services.organism_command_support import S015CommandReplayMismatch
from src.intevia.services.organism_membership_service import (
    OrganismMembershipService, TransitionMembershipCommand,
)
from src.intevia.services.organism_role_service import OrganismRoleService, ProposeAssignmentCommand
from tests.s015_0021_support import append_event, database_now, found_nonroot, seed_identity, seed_test_gateway


class _C1FixtureAuthority:
    def evaluate_organism_authority(self, request):
        response = (
            S015OwnApplicantAuthorityResponse
            if isinstance(request, S015OwnApplicantAuthorityRequest)
            else S015PostFoundingAuthorityResponse
        )
        return response(**asdict(request), authority_reference="c1:fixture:qualified")


def _attempt(call):
    """Expose unexpected service/database errors as explicit witness outcomes.

    Only the act under test uses this capture. Fixture failures remain ERRORs.
    An exception cannot satisfy a success assertion or an expected refusal of
    a different type. The TAR runner requires FAIL, never ERROR or a skip.
    """
    try:
        return None, call()
    except Exception as exc:
        return exc, None


class RejoinTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        if connection.vendor != "postgresql":
            self.fail("C1 membership witnesses require the commissioned PostgreSQL 17 clone route.")
        with transaction.atomic(), connection.cursor() as cursor:
            self.ids = found_nonroot(cursor, tag="c1-" + uuid4().hex)
            seed_test_gateway(cursor, self.ids, include_governance_configuration=True)
            applicant_id = seed_identity(cursor, "c1-" + uuid4().hex)
        self.lo = LivingOrganism.objects.get(pk=self.ids["lo"])
        self.coordinator = Identity.objects.get(pk=self.ids["identity"])
        self.applicant = Identity.objects.get(pk=applicant_id)
        with transaction.atomic():
            previous = None
            for action, prior, state, reason in (
                (IdentityTransition.Action.PROVISION, None, Identity.AccessState.PENDING,
                 IdentityTransition.ReasonCategory.PROVISIONING),
                (IdentityTransition.Action.ACTIVATE, Identity.AccessState.PENDING,
                 Identity.AccessState.ACTIVE, IdentityTransition.ReasonCategory.GOVERNED_ACTIVATION),
            ):
                previous = IdentityTransition.objects.create(
                    identity=self.applicant, action=action, prior_state=prior,
                    resulting_state=state, authority_reference="c1:fixture:identity",
                    technical_executor="C1_TEST", evidence_reference="c1:fixture:history",
                    reason_category=reason, correlation_id=uuid4(), occurred_at=database_now(),
                    previous_transition=previous,
                )
        self.authority = S015Authority(provider=_C1FixtureAuthority())
        self.service = OrganismApplicationService(authority=self.authority, clock=database_now)
        self.first_application = self._submit(self._submission())
        self._accept(self.first_application)
        self.membership = OrganismMembership.objects.get(identity=self.applicant, living_organism=self.lo)

    def _keys(self):
        token = uuid4().hex
        return {"request_reference": "c1:req:" + token, "idempotency_key": "c1:key:" + token}

    def _submission(self, effective_at=None):
        now = database_now()
        return SubmitApplicationCommand(
            credential=self.applicant.credential, living_organism_id=self.lo.organism_id,
            occurred_at=now, effective_at=effective_at or now, answer="C1 fixture application",
            **self._keys(),
        )

    def _submit(self, command):
        result = self.service.submit_application(command)
        return Application.objects.get(application_uuid=result.application_uuid)

    def _claim(self, application):
        now = database_now()
        self.service.claim_application(ClaimApplicationCommand(
            credential=self.coordinator.credential, application_uuid=application.application_uuid,
            occurred_at=now, effective_at=now, **self._keys(),
        ))

    def _decide_accept(self, application):
        now = database_now()
        self.accept_command = DecideApplicationCommand(
            credential=self.coordinator.credential, application_uuid=application.application_uuid,
            occurred_at=now, effective_at=now, outcome="ACCEPT", **self._keys(),
        )
        return self.service.decide_application(self.accept_command)

    def _accept(self, application):
        self._claim(application)
        return self._decide_accept(application)

    def _leave(self):
        now = database_now()
        OrganismMembershipService(authority=self.authority, clock=database_now).transition_membership(
            TransitionMembershipCommand(
                credential=self.applicant.credential, membership_uuid=self.membership.membership_uuid,
                occurred_at=now, effective_at=now, resulting_state="ENDED",
                reason_class="VOLUNTARY", **self._keys(),
            )
        )
        self.membership.refresh_from_db()
        return OrganismMembershipTransition.objects.get(pk=self.membership.head_transition_id)

    def _events(self):
        return OrganismMembershipTransition.objects.filter(membership=self.membership).order_by("sequence")

    def test_01_new_period_preserves_old_chain(self):
        """C1-01: a new application creates a distinct period on the unchanged anchor."""
        ended = self._leave()
        old = list(self._events().values())
        original_effect = ApplicationDecisionEffect.objects.get(decision__application=self.first_application)
        original_transition = original_effect.transition_id
        command = self._submission()
        second = self._submit(command)
        error, accepted = _attempt(lambda: self._accept(second))
        self.assertIsNone(error, f"Accepting the new C1 application should open its second period; got {error}.")
        replay = self.service.decide_application(self.accept_command)
        self.assertEqual(replay, accepted, "Exact replay of the second-period acceptance must return its recorded result.")
        mismatch, _ = _attempt(lambda: self.service.decide_application(replace(self.accept_command, outcome="DECLINE")))
        self.assertIs(type(mismatch), S015CommandReplayMismatch,
                      f"Changing the accepted request under its existing key must refuse without a new period; got {mismatch}.")
        original_effect.refresh_from_db()
        self.assertEqual(original_effect.transition_id, original_transition,
                         "The first application's decision effect must still name its original admission transition.")
        self.membership.refresh_from_db()
        events = list(self._events())
        self.assertEqual(list(self._events().filter(sequence__lte=ended.sequence).values()), old,
                         "Rejoining must preserve every old membership event and commitment unchanged.")
        self.assertEqual([(e.prior_state, e.resulting_state) for e in events[-2:]],
                         [("ENDED", "PROPOSED"), ("PROPOSED", "ACTIVE")],
                         "C1 rejoin must append a new proposal and admission, not reactivate the old period.")
        self.assertEqual(events[-2].evidence_reference, f"application:{second.application_uuid}",
                         "The new period must name the new application, not the previous application.")
        self.assertEqual(OrganismMembership.objects.filter(identity=self.applicant, living_organism=self.lo).count(), 1,
                         "Rejoining the same LO must retain one durable membership anchor.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.s015_fold_effective_state(%s, %s, %s, %s)",
                           ["core_organismmembership", self.membership.pk, ended.effective_at, database_now()])
            self.assertEqual(cursor.fetchone()[0], "ENDED",
                             "Historical membership at the old ending must remain ENDED after rejoin.")

    def test_02_application_at_ending_time_refuses(self):
        """C1-02: an application effective at the recorded ending must refuse."""
        ended = self._leave()
        command = self._submission(effective_at=ended.effective_at)
        error, _ = _attempt(lambda: self._submit(command))
        self.assertIs(type(error), S015ApplicationRefused,
                      f"Rejoining at the exact ending time must refuse the application. Received {error}.")
        self.assertIn("must take effect after the previous membership ends", str(error),
                      "The refusal must explain that the new application must take effect after the ending.")
        self.assertEqual(Application.objects.filter(applicant=self.applicant, living_organism=self.lo).count(), 1,
                         "An application at the ending time must leave only the original application.")

    def test_03_old_condition_blocks_acceptance(self):
        """C1-03: a late condition dependency refuses admission without reviving history."""
        ended = self._leave()
        second = self._submit(self._submission())
        self._claim(second)
        with transaction.atomic(), connection.cursor() as cursor:
            condition = MembershipCondition.objects.create(
                condition_kind="MENTORSHIP", subject_membership=self.membership,
                mentor_membership_id=self.ids["membership"],
            )
            at = database_now()
            append_event(
                cursor, "core_membershipconditionstateevent", condition.pk, 1,
                "OPEN", None, "ACTIVE", self.coordinator.pk, self.ids["basis"],
                uuid4().hex, eff=at, occ=at, rec=at,
            )
        error, _ = _attempt(lambda: self._decide_accept(second))
        self.assertIs(type(error), S015ApplicationRefused,
                      f"C1a must refuse admission with old condition history, including a link added after submission; got {error}.")
        self.assertIn("condition or mentor history", str(error),
                      "The acceptance refusal must identify the membership's condition or mentor history.")
        self.assertFalse(ApplicationDecision.objects.filter(application=second).exists(),
                         "A refused condition-dependent admission must roll back its acceptance decision.")
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.head_transition_id, ended.pk,
                         "Refused C1a admission must retain the ended head without a half-created period.")

    def test_04_unfinished_office_is_not_inherited(self):
        """C1-04: an unfinished old office anchor cannot become a new-period nomination."""
        definition = OrganismRoleDefinition.objects.get(
            living_organism=self.lo, code="COORDINATOR", active=True,
        )
        now = database_now()
        roles = OrganismRoleService(authority=self.authority, clock=database_now)
        roles.propose_assignment(ProposeAssignmentCommand(
            credential=self.coordinator.credential, membership_uuid=self.membership.membership_uuid,
            role_definition_uuid=definition.role_uuid, occurred_at=now, effective_at=now,
            effective_until_state="NOT_ESTABLISHED", effective_until=None, **self._keys(),
        ))
        self._leave()
        nomination = ContextualRoleAssignment.objects.get(
            membership=self.membership, role_definition=definition,
        )
        self.assertEqual(nomination.head_state_event.resulting_state, "PROPOSED",
                         "The Coordinator's nomination must still be proposed after the applicant leaves.")
        error, _ = _attempt(lambda: self._submit(self._submission()))
        self.assertIs(type(error), S015ApplicationRefused,
                      f"An unfinished old office must prevent C1a rejoin instead of becoming a new nomination; got {error}.")
        self.assertIn("nomination without a recorded ending", str(error),
                      "The submission refusal must identify the unfinished office or nomination.")
        self.assertEqual(Application.objects.filter(applicant=self.applicant, living_organism=self.lo).count(), 1,
                         "The old-office refusal must create no new application or membership period.")

    def test_05_competing_rejoins_leave_one_application(self):
        """C1-05: competing new requests leave one open application and one second period."""
        self._leave()
        commands = [self._submission(), self._submission()]
        barrier = threading.Barrier(2)
        results = Queue()

        def submit(command):
            close_old_connections()
            try:
                local_service = OrganismApplicationService(
                    authority=S015Authority(provider=_C1FixtureAuthority()), clock=database_now,
                )
                barrier.wait(timeout=10)
                error, result = _attempt(lambda: local_service.submit_application(command))
                results.put((error, result))
            except Exception as exc:
                results.put((exc, None))
            finally:
                close_old_connections()

        threads = [threading.Thread(target=submit, args=(command,), daemon=True) for command in commands]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(t.is_alive() for t in threads),
                         "Competing C1 rejoin requests must finish within the fixture's bounded wait.")
        outcomes = [results.get_nowait() for _ in threads]
        self.assertEqual(sum(error is None for error, _ in outcomes), 1,
                         f"Exactly one competing rejoin must submit; observed errors {[error for error, _ in outcomes]}.")
        self.assertEqual(sum(type(error) is S015ApplicationRefused for error, _ in outcomes), 1,
                         "The losing request must be an explicit application refusal, not a database or worker error.")
        second = Application.objects.get(applicant=self.applicant, living_organism=self.lo, decision__isnull=True)
        error, _ = _attempt(lambda: self._accept(second))
        self.assertIsNone(error, f"The winning rejoin application must still be admissible; got {error}.")
        self.assertEqual(self._events().filter(prior_state="ENDED", resulting_state="PROPOSED").count(), 1,
                         "Competing requests must produce one second-period boundary, never two.")
