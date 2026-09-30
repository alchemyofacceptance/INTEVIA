"""S3 2b PostgreSQL Coordinator route and admission witnesses (Human Governor executes)."""

import json
import threading
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Barrier, Event
from unittest import skipUnless
from uuid import UUID, uuid4

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from core.models import (
    Application, ApplicationDecision, ApplicationDecisionEffect, ApplicationEngagement,
    ApplicationLineage, ApplicationLineageEvent, ApplicationWithdrawal,
    CSAAssignment, CSAAssignmentEnd, Identity, IdentityTransition,
    OrganismCommandReceipt, OrganismMembership, OrganismMembershipTransition,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_service import (
    ClaimApplicationCommand, DecideApplicationCommand, OrganismApplicationService,
    ReassignApplicationCommand, S015ApplicationAuthorityUnavailable,
    S015ApplicationNotFound, S015ApplicationRefused,
    SubmitApplicationCommand, WithdrawApplicationCommand, _append_application_event,
)
from src.intevia.services.organism_authority import (
    S015Authority, S015AuthorityDenied, S015AuthorityRefusalCode,
    S015OwnApplicantAuthorityRequest, S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityRefusal, S015PostFoundingAuthorityResponse,
)
from src.intevia.services.organism_command_support import S015CommandReplayMismatch
from src.intevia.services.organism_membership_service import S015OperationNotYetImplemented
from tests.s015_0021_support import T0, append_event, current_core_leaf, empty_fp, found_nonroot, fresh, refresh, seed_identity, u
from tests.test_s015_0024_commitment_preimages import digest, l1_preimage, parts_preimage


NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
POSTGRES = skipUnless(connection.vendor == "postgresql", "S3 2b requires PostgreSQL")
SETTINGS = "DEFAULT (no settings store): cq_active=No, application_sole_participant_delegation=No"


class _Provider:
    def __init__(self, denied=False):
        self.denied = denied
        self.requests = []

    def evaluate_organism_authority(self, request):
        self.requests.append(request)
        fields = asdict(request)
        if isinstance(request, S015OwnApplicantAuthorityRequest):
            return S015OwnApplicantAuthorityResponse(**fields, authority_reference="s3:test:applicant")
        if self.denied:
            return S015PostFoundingAuthorityRefusal(**fields, refusal_code=S015AuthorityRefusalCode.DENIED)
        return S015PostFoundingAuthorityResponse(**fields, authority_reference="s3:test:coordinator")


@POSTGRES
class ApplicationDecisionTests(TransactionTestCase):
    def setUp(self):
        self.tag = f"s3-decision-{fresh()}"
        with transaction.atomic(), connection.cursor() as cursor:
            self.ids = found_nonroot(cursor, tag=self.tag)
            applicant_pk = seed_identity(cursor, f"applicant-{self.tag}")
            cursor.execute("SELECT organism_id FROM core_livingorganism WHERE id = %s", [self.ids["lo"]])
            self.organism_uuid = cursor.fetchone()[0]
        self.applicant = Identity.objects.select_related("credential").get(pk=applicant_pk)
        self.coordinator = Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self.provider = _Provider()
        IdentityTransition.objects.create(
            identity=self.applicant, action=IdentityTransition.Action.PROVISION,
            prior_state=None, resulting_state="pending", authority_reference="s3:test:provision",
            technical_executor="S3_TEST", evidence_reference="s3:test:history",
            reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
            correlation_id=uuid4(), occurred_at=T0,
        )
        IdentityTransition.objects.create(
            identity=self.applicant, action=IdentityTransition.Action.ACTIVATE,
            prior_state="pending", resulting_state="active", authority_reference="s3:test:activate",
            technical_executor="S3_TEST", evidence_reference="s3:test:history",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(), occurred_at=T0 + timedelta(hours=1),
            previous_transition=IdentityTransition.objects.get(identity=self.applicant, action="provision"),
        )

    def service(self, provider=None, clock=None):
        return OrganismApplicationService(
            authority=S015Authority(provider=provider or self.provider),
            clock=clock or (lambda: datetime.now(timezone.utc)),
        )

    def submit(self):
        return self.service().submit_application(SubmitApplicationCommand(
            credential=self.applicant.credential, living_organism_id=self.organism_uuid,
            request_reference=f"s3:submit:{self.tag}", idempotency_key=f"s3:submit:key:{self.tag}",
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        ))

    def application(self):
        return Application.objects.get(pk=self.submit().application_id)

    def command(self, application, *, actor=None, kind="claim", outcome="ACCEPT", suffix="first"):
        fields = dict(
            credential=(actor or self.coordinator).credential, application_uuid=application.application_uuid,
            request_reference=f"s3:{kind}:{suffix}:{self.tag}",
            idempotency_key=f"s3:{kind}:key:{suffix}:{self.tag}",
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        )
        if kind == "decide":
            return DecideApplicationCommand(**fields, outcome=outcome)
        if kind == "reassign":
            return ReassignApplicationCommand(**fields)
        return ClaimApplicationCommand(**fields)

    def second_coordinator(self):
        with transaction.atomic(), connection.cursor() as cursor:
            other_pk = seed_identity(cursor, f"coordinator2-{self.tag}")
            member_uuid = u(f"member:coordinator2:{self.tag}")
            cursor.execute(
                "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, "
                "state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                [member_uuid, T0, T0, empty_fp(cursor, "core_organismmembership", member_uuid),
                 other_pk, self.ids["lo"]],
            )
            member_id = cursor.fetchone()[0]
            append_event(cursor, "core_organismmembershiptransition", member_id, 1, "JOIN", None,
                         "ACTIVE", other_pk, self.ids["basis"], f"second:{self.tag}",
                         extra={"reason_class": "TEST"})
            refresh(cursor, "core_organismmembership", member_id)
            office_uuid = u(f"office:coordinator2:{self.tag}")
            cursor.execute(
                "INSERT INTO core_contextualroleassignment (assignment_uuid, state, created_at, "
                "current_state_effective_at, state_known_at, state_source_event_set_fingerprint, "
                "membership_id, role_definition_id) VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                [office_uuid, T0, T0, empty_fp(cursor, "core_contextualroleassignment", office_uuid),
                 member_id, self.ids["roles"]["COORDINATOR"]],
            )
            office_id = cursor.fetchone()[0]
            append_event(cursor, "core_contextualroleassignmentstateevent", office_id, 1,
                         "ASSIGN", None, "ACTIVE", other_pk, self.ids["basis"], f"office2:{self.tag}")
            refresh(cursor, "core_contextualroleassignment", office_id)
        return Identity.objects.select_related("credential").get(pk=other_pk)

    def claimed(self):
        application = self.application()
        self.service().claim_application(self.command(application))
        return application

    def test_self_claim_and_engagement_one(self):
        application = self.application()
        result = self.service().claim_application(self.command(application))
        claim = CSAAssignment.objects.get(application=application)
        engagement = ApplicationEngagement.objects.get(application=application)
        self.assertEqual((claim.actor_id, claim.assignee_membership_id, claim.assignee_office_id),
                         (self.coordinator.pk, self.ids["membership"], self.ids["assignments"]["COORDINATOR"]))
        self.assertEqual(claim.completion_due_at, NOW + timedelta(days=27))
        self.assertEqual((engagement.engagement_ordinal, engagement.route, engagement.deciding_party_id),
                         (1, "LO_CSA", self.ids["lo"]))
        self.assertEqual((engagement.engagement_reference, engagement.settings_basis),
                         (str(claim.assignment_uuid), SETTINGS))
        self.assertEqual((result.completion_due_at, result.completion_due_source),
                         (claim.completion_due_at, "DEFAULT (no settings store)"))

    def test_non_coordinator_cannot_claim(self):
        application = self.application()
        with self.assertRaises(S015ApplicationAuthorityUnavailable):
            self.service().claim_application(self.command(application, actor=self.applicant))
        self.assertFalse(CSAAssignment.objects.exists())

    def test_second_claim_refused(self):
        application = self.claimed()
        with self.assertRaises(S015ApplicationRefused):
            self.service().claim_application(self.command(application, suffix="again"))

    def test_claim_denial_precedes_state_refusal(self):
        application = self.claimed()
        with self.assertRaises(S015AuthorityDenied):
            self.service(_Provider(denied=True)).claim_application(self.command(application, suffix="denied"))

    def test_claim_on_withdrawn_application_refused(self):
        application = self.application()
        self.service().withdraw_application(WithdrawApplicationCommand(
            credential=self.applicant.credential, application_uuid=application.application_uuid,
            request_reference="s3:withdraw:claim", idempotency_key="s3:withdraw:claim",
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        ))
        with self.assertRaises(S015ApplicationRefused):
            self.service().claim_application(self.command(application))

    def test_reassignment_ends_and_replaces_with_engagement_two(self):
        application = self.claimed()
        other = self.second_coordinator()
        first = CSAAssignment.objects.get(application=application)
        result = self.service().reassign_application(self.command(application, actor=other, kind="reassign"))
        ending = CSAAssignmentEnd.objects.get(assignment=first)
        replacement = CSAAssignment.objects.get(replaces=first)
        engagement = ApplicationEngagement.objects.get(application=application, engagement_ordinal=2)
        self.assertEqual((replacement.actor_id, replacement.assignee_membership.identity_id), (other.pk, other.pk))
        self.assertEqual((engagement.engagement_reference, engagement.basis_reference),
                         (str(replacement.assignment_uuid), str(ending.ending_uuid)))
        self.assertEqual(result.record_uuid, replacement.assignment_uuid)

    def test_stale_service_clock_sees_committed_claim_for_decision(self):
        application = self.claimed()
        claim = CSAAssignment.objects.get(application=application)
        service = self.service(clock=lambda: claim.recorded_at - timedelta(days=1))
        service.decide_application(self.command(application, kind="decide", outcome="DECLINE"))
        self.assertEqual(ApplicationDecision.objects.get(application=application).assignment_reference_id, claim.pk)

    def test_stale_service_clock_sees_committed_claim_for_reassignment(self):
        application = self.claimed()
        claim = CSAAssignment.objects.get(application=application)
        other = self.second_coordinator()
        service = self.service(clock=lambda: claim.recorded_at - timedelta(days=1))
        service.reassign_application(self.command(application, actor=other, kind="reassign"))
        self.assertEqual(CSAAssignmentEnd.objects.get().assignment_id, claim.pk)
        self.assertEqual(CSAAssignment.objects.get(replaces=claim).actor_id, other.pk)

    def test_stale_service_clock_cannot_hide_committed_ending(self):
        application = self.claimed()
        claim = CSAAssignment.objects.get(application=application)
        ending = CSAAssignmentEnd.objects.create(
            assignment=claim, actor=self.coordinator, enterer=self.coordinator,
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        )
        ending.refresh_from_db()
        service = self.service(clock=lambda: ending.recorded_at - timedelta(days=1))
        with self.assertRaisesMessage(S015ApplicationRefused, "no current Coordinator claim"):
            service.decide_application(self.command(application, kind="decide", outcome="DECLINE"))
        self.assertFalse(ApplicationDecision.objects.filter(application=application).exists())

    def test_same_coordinator_can_reassign_to_new_self_claim(self):
        application = self.claimed()
        first = CSAAssignment.objects.get(application=application)
        new_effective_at = NOW + timedelta(minutes=1)
        result = self.service().reassign_application(replace(
            self.command(application, kind="reassign"), effective_at=new_effective_at,
        ))
        ending = CSAAssignmentEnd.objects.get(assignment=first)
        replacement = CSAAssignment.objects.get(replaces=first)
        engagement = ApplicationEngagement.objects.get(application=application, engagement_ordinal=2)
        self.assertNotEqual(first.pk, replacement.pk)
        self.assertEqual((replacement.actor_id, replacement.assignee_membership_id),
                         (self.coordinator.pk, first.assignee_membership_id))
        self.assertEqual((engagement.engagement_reference, engagement.basis_reference),
                         (str(replacement.assignment_uuid), str(ending.ending_uuid)))
        self.assertEqual((replacement.completion_due_at, result.completion_due_at),
                         (new_effective_at + timedelta(days=27),) * 2)

    def test_decline_has_no_effect(self):
        application = self.claimed()
        self.service().decide_application(self.command(application, kind="decide", outcome="DECLINE"))
        self.assertEqual(ApplicationDecision.objects.get(application=application).outcome, "DECLINE")
        self.assertFalse(ApplicationDecisionEffect.objects.exists())
        self.assertFalse(OrganismMembership.objects.filter(identity=self.applicant).exists())

    def test_application_chain_replays_five_acts_and_independent_commitments(self):
        submission = self.submit()
        application = Application.objects.get(pk=submission.application_id)
        claim = self.service().claim_application(self.command(application))
        replacement = self.service().reassign_application(self.command(application, kind="reassign"))
        decided = self.service().decide_application(self.command(application, kind="decide"))
        lineage = ApplicationLineage.objects.get(application=application)
        events = list(ApplicationLineageEvent.objects.filter(application_lineage=lineage).order_by("sequence"))
        self.assertEqual([row.action for row in events], [
            "SUBMIT_APPLICATION", "CLAIM_APPLICATION", "REASSIGN_APPLICATION",
            "DECIDE_APPLICATION",
        ])
        self.assertEqual([row.resulting_state for row in events], [
            "SUBMITTED", "IN_REVIEW", "IN_REVIEW", "ACCEPTED",
        ])
        self.assertEqual([row.prior_state for row in events], [None, "SUBMITTED", "IN_REVIEW", "IN_REVIEW"])
        self.assertEqual([row.actor_id for row in events], [
            self.applicant.pk, self.coordinator.pk, self.coordinator.pk, None,
        ])
        self.assertEqual([row.authority_decision_reference for row in events], [
            submission.authority_decision_reference, claim.authority_decision_reference,
            replacement.authority_decision_reference, decided.authority_decision_reference,
        ])
        decision = ApplicationDecision.objects.get(application=application)
        self.assertEqual([row.evidence_reference for row in events], [
            f"application:{application.application_uuid}", f"csa-assignment:{claim.record_uuid}",
            f"csa-assignment:{replacement.record_uuid}", f"application-decision:{decision.decision_uuid}",
        ])
        self.assertEqual(lineage.head_event_id, events[-1].pk)
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.s015_fold_effective_state(%s, %s, %s, clock_timestamp())",
                           ["core_applicationlineage", lineage.pk, NOW])
            self.assertEqual(cursor.fetchone()[0], "ACCEPTED")
            cursor.execute(
                "SELECT a.attname, a.atttypid::regtype::text FROM pg_catalog.pg_attribute a "
                "WHERE a.attrelid = 'public.core_applicationlineageevent'::regclass AND a.attnum > 0 "
                "AND NOT a.attisdropped ORDER BY a.attname COLLATE \"C\""
            )
            inventory = [{"column": name, "pg_type": {"character varying": "varchar", "timestamp with time zone": "timestamptz"}.get(kind, kind)}
                         for name, kind in cursor.fetchall()]
            for event in events:
                cursor.execute("SELECT to_jsonb(t), t.l1_commitment, t.parts_commitment, "
                               "t.actor_state, t.actor_capacity, t.enterer_identity_id, t.l2_commitment "
                               "FROM public.core_applicationlineageevent t WHERE id = %s", [event.pk])
                row, l1, parts, actor_state, capacity, enterer, l2 = cursor.fetchone()
                row = json.loads(row) if isinstance(row, str) else row
                self.assertIsNotNone(row["recorded_at"])
                self.assertEqual(l1, digest("s015r1", l1_preimage("core_applicationlineageevent", inventory, row)))
                self.assertEqual(parts, digest("s015p1", parts_preimage("core_applicationlineageevent", event.event_uuid, [])))
                self.assertIsNone(l2)
                self.assertEqual(capacity, "STANDING")
                self.assertEqual(actor_state, "LIVING_ORGANISM" if event.actor_id is None else "PARTY")
                self.assertEqual(enterer, self.applicant.pk if event.sequence == 1 else self.coordinator.pk)

    def test_declined_application_leaves_terminal_lineage(self):
        application = self.claimed()
        self.service().decide_application(self.command(application, kind="decide", outcome="DECLINE"))
        lineage = ApplicationLineage.objects.get(application=application)
        self.assertEqual(lineage.head_event.resulting_state, "DECLINED")
        self.assertEqual(ApplicationLineageEvent.objects.filter(application_lineage=lineage).count(), 3)

    def test_care_actor_and_composed_entry_are_not_round_two_routes(self):
        application = self.claimed()
        event = ApplicationLineage.objects.get(application=application).head_event
        with connection.cursor() as cursor:
            cursor.execute("SELECT attname FROM pg_attribute WHERE attrelid = "
                           "'public.core_applicationlineageevent'::regclass "
                           "AND attnum > 0 AND NOT attisdropped AND attname <> 'id' ORDER BY attnum")
            columns = [name for (name,) in cursor.fetchall()]
        for changes in (
            {"actor_state": "'CARE'"},
            {"entry_mode": "'COMPOSED'", "composing_rule_state": "'NOT_NAMED'",
             "composer_state": "'NOT_ESTABLISHED'"},
        ):
            with self.subTest(changes=changes):
                expressions = [changes.get(column, "NULL" if column in ("recorded_at", "l1_commitment")
                                           else "%s::uuid" if column == "event_uuid" else f"t.{column}")
                               for column in columns]
                with self.assertRaisesMessage(IntegrityError, "S3FU application actor/entry refused"):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(
                            f"INSERT INTO public.core_applicationlineageevent ({', '.join(columns)}) "
                            f"SELECT {', '.join(expressions)} FROM public.core_applicationlineageevent t WHERE t.id = %s",
                            [uuid4(), event.pk],
                        )

    def test_lo_permission_and_capacity_refuse_independently(self):
        application = self.claimed()
        decided = self.service().decide_application(self.command(application, kind="decide"))
        decision = ApplicationDecision.objects.get(application=application)

        def repeat_decision(effective_at):
            with transaction.atomic():
                _append_application_event(
                    database_alias="default", application=application,
                    action=OrganismAction.DECIDE_APPLICATION, resulting_state="ACCEPTED",
                    actor=None, enterer=self.coordinator, actor_access_epoch=None,
                    authority_decision_reference=decided.authority_decision_reference,
                    evidence_reference=f"application-decision:{decision.decision_uuid}",
                    request_reference=f"s3:guard:{self.tag}", idempotency_key=f"s3:guard:{self.tag}",
                    payload_fingerprint="0" * 64, occurred_at=effective_at,
                    effective_at=effective_at, received_at=effective_at,
                )

        with self.assertRaisesMessage(IntegrityError, "S3FU LO capacity refused"):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("ALTER TABLE public.core_livingorganismevent DISABLE TRIGGER USER")
                    cursor.execute("DELETE FROM public.core_livingorganismevent WHERE id = %s",
                                   [self.ids["events"]["core_livingorganismevent"]])
                repeat_decision(NOW)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = "
                           "'public.core_livingorganismevent'::regclass AND NOT tgisinternal AND tgenabled <> 'O'")
            self.assertEqual(cursor.fetchone()[0], 0)
        dormant_at = NOW + timedelta(minutes=1)
        with transaction.atomic(), connection.cursor() as cursor:
            dormant_id, _ = append_event(
                cursor, "core_livingorganismevent", self.ids["lo"], 2, "DORMANT",
                "ACTIVE", "DORMANT", self.coordinator.pk, self.ids["basis"], fresh(),
                pred_id=self.ids["events"]["core_livingorganismevent"],
                eff=dormant_at, occ=dormant_at, rec=dormant_at,
            )
            refresh(cursor, "core_livingorganism", self.ids["lo"], dormant_at)
        with self.assertRaisesMessage(IntegrityError, "S3FU LO admission not permitted"):
            repeat_decision(dormant_at + timedelta(seconds=1))
        closed_at = NOW + timedelta(minutes=2)
        with transaction.atomic(), connection.cursor() as cursor:
            append_event(
                cursor, "core_livingorganismevent", self.ids["lo"], 3, "CLOSE",
                "DORMANT", "CLOSED", self.coordinator.pk, self.ids["basis"], fresh(),
                pred_id=dormant_id, eff=closed_at, occ=closed_at, rec=closed_at,
            )
            refresh(cursor, "core_livingorganism", self.ids["lo"], closed_at)
        with self.assertRaisesMessage(IntegrityError, "S3FU LO capacity refused"):
            repeat_decision(closed_at + timedelta(seconds=1))
        self.assertEqual(ApplicationLineageEvent.objects.filter(application_lineage__application=application).count(), 3)

    def test_restricted_continuity_accept_is_temporary_implementation_limit(self):
        application = self.claimed()
        with transaction.atomic(), connection.cursor() as cursor:
            append_event(
                cursor, "core_livingorganismevent", self.ids["lo"], 2, "RESTRICT",
                "ACTIVE", "RESTRICTED_CONTINUITY", self.coordinator.pk,
                self.ids["basis"], fresh(),
                pred_id=self.ids["events"]["core_livingorganismevent"],
                eff=NOW, occ=NOW, rec=NOW,
            )
            refresh(cursor, "core_livingorganism", self.ids["lo"], NOW)
        with self.assertRaises(S015OperationNotYetImplemented) as refused:
            self.service().decide_application(self.command(application, kind="decide"))
        self.assertIn("S3-IL-RC", str(refused.exception))
        self.assertFalse(ApplicationDecision.objects.filter(application=application).exists())

    def test_non_assignee_cannot_decide(self):
        application = self.claimed()
        other = self.second_coordinator()
        with self.assertRaises(S015ApplicationRefused):
            self.service().decide_application(self.command(application, actor=other, kind="decide"))
        self.assertFalse(ApplicationDecision.objects.exists())

    def test_decision_after_withdrawal_refused(self):
        application = self.claimed()
        self.service().withdraw_application(WithdrawApplicationCommand(
            credential=self.applicant.credential, application_uuid=application.application_uuid,
            request_reference="s3:withdraw:decision", idempotency_key="s3:withdraw:decision",
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        ))
        with self.assertRaises(S015ApplicationRefused):
            self.service().decide_application(self.command(application, kind="decide"))

    def test_reassignment_changes_deciding_assignee(self):
        application = self.claimed()
        other = self.second_coordinator()
        self.service().reassign_application(self.command(application, actor=other, kind="reassign"))
        with self.assertRaises(S015ApplicationRefused):
            self.service().decide_application(self.command(application, kind="decide"))
        self.service().decide_application(self.command(application, actor=other, kind="decide", suffix="new"))
        self.assertEqual(ApplicationDecision.objects.get().executor_id, other.pk)

    def test_accept_creates_proposal_admission_and_effect(self):
        application = self.claimed()
        self.service().decide_application(self.command(application, kind="decide"))
        member = OrganismMembership.objects.get(identity=self.applicant, living_organism_id=self.ids["lo"])
        proposal, admission = OrganismMembershipTransition.objects.filter(membership=member).order_by("sequence")
        decision = ApplicationDecision.objects.get(application=application)
        proposed, admitted = self._chain_columns(proposal.pk), self._chain_columns(admission.pk)
        self.assertEqual((proposal.sequence, proposed["actor_state"], proposal.actor_id, proposed["enterer_identity_id"],
                          proposal.reason_class, proposal.effective_at),
                         (1, "PARTY", self.applicant.pk, self.coordinator.pk, "APPLICATION", application.effective_at))
        self.assertEqual((admission.sequence, admission.prior_state, admission.resulting_state,
                  admitted["actor_state"], admission.actor_id, admission.actor_access_epoch),
                         (2, "PROPOSED", "ACTIVE", "LIVING_ORGANISM", None, None))
        self.assertEqual((admitted["actor_capacity"], admitted["entry_mode"], admitted["composer_state"],
                  admitted["enterer_identity_id"], admission.reason_class),
                    ("STANDING", "TRANSPORT", "NA_TRANSPORT", self.coordinator.pk, "ADMISSION"))
        self.assertEqual(admission.evidence_reference, f"application-decision:{decision.decision_uuid}")
        self.assertEqual(ApplicationDecisionEffect.objects.get(decision=decision).transition_id, admission.pk)

    def test_admission_lineage_reaches_both_authorities(self):
        submission = self.submit()
        application = Application.objects.get(pk=submission.application_id)
        self.service().claim_application(self.command(application))
        decision_result = self.service().decide_application(self.command(application, kind="decide"))
        decision = ApplicationDecision.objects.get(application=application)
        effect = ApplicationDecisionEffect.objects.get(decision=decision)
        admission = effect.transition
        proposal = admission.predecessor
        decision_receipt = OrganismCommandReceipt.objects.get(
            actor=self.coordinator, action=OrganismAction.DECIDE_APPLICATION.value,
        )
        decision_digest = decision_result.authority_decision_reference
        self.assertEqual((admission.authority_decision_reference, decision.authority_decision_reference,
                          decision_receipt.authority_decision_reference), (decision_digest,) * 3)
        self.assertEqual(effect.transition_id, admission.pk)

        self.assertIsNotNone(proposal)
        reference_kind, separator, reference_uuid = proposal.evidence_reference.partition(":")
        self.assertEqual((reference_kind, separator), ("application", ":"))
        linked_application = Application.objects.get(application_uuid=UUID(reference_uuid))
        self.assertEqual(linked_application.pk, application.pk)
        self.assertEqual(linked_application.applicant_id, proposal.actor_id)
        submission_receipt = OrganismCommandReceipt.objects.get(
            actor_id=linked_application.applicant_id,
            action=OrganismAction.SUBMIT_APPLICATION.value,
            result_payload__application_uuid=str(linked_application.application_uuid),
        )
        self.assertEqual(submission_receipt.authority_decision_reference,
                         submission.authority_decision_reference)
        self.assertNotEqual(submission_receipt.authority_decision_reference, decision_digest)
        self.assertEqual((linked_application.authority_decision_reference, linked_application.authority_reference,
                          linked_application.actor_access_epoch),
                         (submission.authority_decision_reference, submission.authority_reference,
                          self.applicant.access_epoch))
        self.assertEqual((proposal.authority_decision_reference, proposal.actor_access_epoch),
                         (linked_application.authority_decision_reference, linked_application.actor_access_epoch))

    def test_proposal_retains_submission_epoch_after_applicant_epoch_changes(self):
        submission = self.submit()
        application = Application.objects.get(pk=submission.application_id)
        submission_epoch = application.actor_access_epoch
        Identity.objects.filter(pk=self.applicant.pk).update(access_epoch=submission_epoch + 1)
        self.assertEqual(Identity.objects.get(pk=self.applicant.pk).access_epoch, submission_epoch + 1)
        self.service().claim_application(self.command(application))
        self.service().decide_application(self.command(application, kind="decide"))
        proposal = OrganismMembershipTransition.objects.get(
            membership__identity=self.applicant, membership__living_organism_id=self.ids["lo"], sequence=1,
        )
        self.assertEqual((proposal.authority_decision_reference, proposal.actor_access_epoch),
                         (submission.authority_decision_reference, submission_epoch))

    def test_accept_existing_proposed_membership_has_only_activation(self):
        application = self.claimed()
        member_uuid = u(f"proposed:{self.tag}")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, "
                "state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, 'PROPOSED', now(), %s, %s, %s, %s, %s) RETURNING id",
                [member_uuid, T0, T0, empty_fp(cursor, "core_organismmembership", member_uuid),
                 self.applicant.pk, self.ids["lo"]],
            )
            member_id = cursor.fetchone()[0]
            append_event(cursor, "core_organismmembershiptransition", member_id, 1,
                         "JOIN", None, "PROPOSED", self.applicant.pk, self.ids["basis"],
                         f"proposed:{self.tag}", extra={"reason_class": "APPLICATION"})
            refresh(cursor, "core_organismmembership", member_id)
        self.service().decide_application(self.command(application, kind="decide"))
        self.assertEqual(list(OrganismMembershipTransition.objects.filter(membership_id=member_id)
                              .values_list("resulting_state", flat=True)), ["PROPOSED", "ACTIVE"])

    def test_four_responsibilities_live_in_distinct_records(self):
        application = self.claimed()
        result = self.service().decide_application(self.command(application, kind="decide"))
        decision = ApplicationDecision.objects.get(application=application)
        transition = ApplicationDecisionEffect.objects.get(decision=decision).transition
        receipt = OrganismCommandReceipt.objects.get(action=OrganismAction.DECIDE_APPLICATION.value)
        claim = CSAAssignment.objects.get(application=application)
        stored = self._chain_columns(transition.pk)
        self.assertEqual((stored["actor_state"], transition.actor_id, transition.membership.living_organism_id),
                         ("LIVING_ORGANISM", None, self.ids["lo"]))
        self.assertEqual((decision.route, decision.deciding_party_id, decision.assignment_reference_id,
                          decision.responsible_coordinator_id, decision.responsible_capacity,
                          decision.qualifying_office_id, decision.authority_decision_reference),
                         ("LO_CSA", self.ids["lo"], claim.pk, self.coordinator.pk, "COORDINATOR",
                          claim.assignee_office_id, result.authority_decision_reference))
        self.assertEqual(receipt.actor_id, self.coordinator.pk)
        self.assertEqual((stored["enterer_identity_id"], decision.enterer_id), (self.coordinator.pk,) * 2)

    def test_membership_cache_matches_fold(self):
        application = self.claimed()
        self.service().decide_application(self.command(application, kind="decide"))
        member = OrganismMembership.objects.get(identity=self.applicant)
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.s015_fold_effective_state(%s, %s, %s, %s)",
                           ["core_organismmembership", member.pk, member.current_state_effective_at, member.state_known_at])
            self.assertEqual(member.state, cursor.fetchone()[0])
        self.assertEqual(member.state, "ACTIVE")

    def test_replay_and_mismatched_request(self):
        application = self.application()
        command = self.command(application)
        first = self.service().claim_application(command)
        self.assertEqual(self.service().claim_application(command), first)
        with self.assertRaises(S015CommandReplayMismatch):
            self.service().claim_application(replace(command, request_reference="s3:changed"))
        self.assertEqual(CSAAssignment.objects.count(), 1)

    def test_status_lifecycle_and_coordinates(self):
        application = self.application()
        service = self.service()
        future = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertEqual(service.application_status(application, T0, future), "NOT_PRESENT")
        self.assertEqual(service.application_status(application, future, future), "WAITING")
        service.claim_application(self.command(application))
        self.assertEqual(service.application_status(application, future, future), "PENDING")
        first = CSAAssignment.objects.get(application=application)
        ending = CSAAssignmentEnd.objects.create(
            assignment=first, actor=self.coordinator, enterer=self.coordinator,
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        )
        self.assertEqual(service.application_status(application, future, future), "UNABLE")
        other = self.second_coordinator()
        office = other.organism_memberships.get(living_organism_id=self.ids["lo"]).contextual_role_assignments.get()
        replacement = CSAAssignment.objects.create(
            application=application, replaces=first, assignee_membership=office.membership,
            assignee_office=office, actor=other, enterer=other,
            completion_due_at=NOW + timedelta(days=27), occurred_at=NOW,
            effective_at=NOW, received_at=NOW,
        )
        ApplicationEngagement.objects.create(
            application=application, engagement_ordinal=2, route="LO_CSA",
            deciding_party_id=self.ids["lo"], engagement_reference=str(replacement.assignment_uuid),
            basis_reference=str(ending.ending_uuid), settings_basis=SETTINGS,
            actor=other, enterer=other, occurred_at=NOW, effective_at=NOW, received_at=NOW,
        )
        self.assertEqual(service.application_status(application, future, future), "PENDING")
        service.decide_application(replace(
            self.command(application, actor=other, kind="decide"),
            effective_at=NOW + timedelta(minutes=1),
        ))
        self.assertEqual(service.application_status(application, NOW, future), "PENDING")
        self.assertEqual(service.application_status(application, future, future), "DECIDED")

    def test_status_withdrawn(self):
        application = self.application()
        self.service().withdraw_application(WithdrawApplicationCommand(
            credential=self.applicant.credential, application_uuid=application.application_uuid,
            request_reference="s3:status:withdraw", idempotency_key="s3:status:withdraw",
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        ))
        future = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertEqual(self.service().application_status(application, future, future), "WITHDRAWN")

    def test_predecessor_uses_new_application_record_time_not_service_clock(self):
        previous = self.application()
        self.service().withdraw_application(WithdrawApplicationCommand(
            credential=self.applicant.credential, application_uuid=previous.application_uuid,
            request_reference="s3:n1:withdraw", idempotency_key="s3:n1:withdraw",
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        ))
        service = OrganismApplicationService(
            authority=S015Authority(provider=_Provider()), clock=lambda: NOW,
        )
        result = service.submit_application(SubmitApplicationCommand(
            credential=self.applicant.credential, living_organism_id=self.organism_uuid,
            predecessor_reference=previous.application_uuid,
            request_reference="s3:n1:reapply", idempotency_key="s3:n1:reapply",
            occurred_at=NOW, effective_at=NOW, received_at=NOW,
        ))
        self.assertEqual(Application.objects.get(pk=result.application_id).predecessor_reference_id, previous.pk)

    def test_cq_and_circle_routes_report_limits(self):
        application = self.application()
        for route, limit in (("LO_CQ", "S3-IL-CQ"), ("CIRCLE_CSA", "S3-IL-CIRCLE")):
            ApplicationEngagement.objects.create(
                application=application, engagement_ordinal=1, route=route,
                deciding_party_id=self.ids["lo"], engagement_reference="s3:fixture",
                settings_basis=SETTINGS, actor=self.coordinator, enterer=self.coordinator,
                occurred_at=NOW, effective_at=NOW, received_at=NOW,
            )
            with self.assertRaises(S015OperationNotYetImplemented) as error:
                self.service().claim_application(self.command(application, suffix=route))
            self.assertEqual(error.exception.limit_id, limit)
            # Immutable fixtures are isolated by a fresh application for the next route.
            if route == "LO_CQ":
                application = Application.objects.create(
                    applicant=self.applicant, living_organism_id=self.ids["lo"],
                    actor=self.applicant, enterer=self.applicant,
                    authority_decision_reference="s015d1:" + "a" * 64,
                    authority_reference="s3:test:fixture", actor_access_epoch=self.applicant.access_epoch,
                    occurred_at=NOW, effective_at=NOW, received_at=NOW,
                )

    def test_il_ord_claim_application(self):
        """IL-ORD: an unknown UUID returns not-found before the denying provider is asked."""
        self._assert_il_ord("claim")

    def test_il_ord_reassign_application(self):
        """IL-ORD: an unknown UUID returns not-found before the denying provider is asked."""
        self._assert_il_ord("reassign")

    def test_il_ord_decide_application(self):
        """IL-ORD: an unknown UUID returns not-found before the denying provider is asked."""
        self._assert_il_ord("decide")

    def _assert_il_ord(self, kind):
        application = self.application()
        provider = _Provider(denied=True)
        method = getattr(self.service(provider), f"{kind}_application")
        unknown = self.command(application, kind=kind)
        with self.assertRaises(S015ApplicationNotFound):
            method(replace(unknown, application_uuid=uuid4()))
        self.assertFalse(provider.requests)
        with self.assertRaises(S015AuthorityDenied):
            method(unknown)
        self.assertFalse(CSAAssignment.objects.exists())
        self.assertFalse(ApplicationDecision.objects.exists())
        self.assertFalse(OrganismCommandReceipt.objects.filter(action__in=(
            "CLAIM_APPLICATION", "REASSIGN_APPLICATION", "DECIDE_APPLICATION",
        )).exists())

    def test_two_decisions_race_to_one(self):
        application = self.claimed()
        self._race(application, "decide", "decide")
        self.assertEqual(ApplicationDecision.objects.count(), 1)

    def test_decision_and_withdrawal_race_decision_first(self):
        application = self.claimed()
        self._race(application, "decide", "withdraw")
        self.assertEqual(ApplicationDecision.objects.count() + ApplicationWithdrawal.objects.count(), 1)

    def test_decision_and_withdrawal_race_withdrawal_first(self):
        application = self.claimed()
        self._race(application, "withdraw", "decide")
        self.assertEqual(ApplicationDecision.objects.count() + ApplicationWithdrawal.objects.count(), 1)

    def _race(self, application, first, second):
        barrier = Barrier(2)
        results = Queue()

        def worker(kind, suffix):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if kind == "withdraw":
                    command = WithdrawApplicationCommand(
                        credential=self.applicant.credential, application_uuid=application.application_uuid,
                        request_reference=f"race:withdraw:{suffix}", idempotency_key=f"race:withdraw:{suffix}",
                        occurred_at=NOW, effective_at=NOW, received_at=NOW,
                    )
                    self.service(_Provider()).withdraw_application(command)
                else:
                    self.service(_Provider()).decide_application(self.command(
                        application, kind="decide", suffix=suffix,
                    ))
                results.put("ok")
            except Exception as exc:
                results.put(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(kind, str(index)), daemon=True)
                   for index, kind in enumerate((first, second))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
        outcomes = [results.get_nowait(), results.get_nowait()]
        self.assertEqual(outcomes.count("ok"), 1, outcomes)
        self.assertIsInstance(next(outcome for outcome in outcomes if outcome != "ok"),
                              (IntegrityError, S015ApplicationRefused))

    def test_g2_ending_witness_on_both_schemas(self):
        """0025 admission of an ended assignee is the defect witness, not desired behaviour."""
        self._migration_witness("end")

    def test_g1_withdrawal_witness_on_both_schemas(self):
        """Both schema versions refuse a decision after a withdrawal regardless of record time."""
        self._migration_witness("withdraw")

    def _migration_witness(self, terminator):
        old_head = [("core", "0025_s015_application_routes")]
        new_head = [("core", "0026_s015_application_admission_time")]
        restore_head = current_core_leaf(connection)
        try:
            for head in (old_head, new_head):
                # A fresh executor per step: a reused one plans from its loader's applied set, fixed when it was built.
                MigrationExecutor(connection).migrate(head)
                self._assert_schema_in_force(post_lock_time=head == new_head)
                # Positive control on this very schema: the identical direct decision, with no ending and no withdrawal,
                # is admitted. So the row's shape and B-G9's other terms hold here, and the only difference between this
                # control and the witness below is the terminator A commits while B waits.
                control = self._fresh_claim()
                with transaction.atomic(), connection.cursor() as cursor:
                    self._direct_decision(cursor, control, CSAAssignment.objects.get(application=control))
                self.assertTrue(ApplicationDecision.objects.filter(application=control).exists(),
                                "control: the identical decision without a terminator must be admitted on this schema")
                application = self._fresh_claim()
                outcome = self._blocked_insert_witness(application, terminator)
                if terminator == "end":
                    if head == old_head:
                        self.assertIs(outcome, True,
                                      "0025 witnesses the defect: the ended assignee's decision is admitted")
                    else:
                        self._assert_refused_by(outcome, "S3 CSA completion authority refused")
                else:
                    self._assert_refused_by(outcome, "S3 terminal exclusion")
                    self.assertEqual(ApplicationWithdrawal.objects.filter(application=application).count(), 1)
                    self.assertFalse(ApplicationDecision.objects.filter(application=application).exists())
        finally:
            self._test_only_cleanup_migration_witness_rows()
            MigrationExecutor(connection).migrate(restore_head)
            self._assert_schema_in_force(post_lock_time=True)
            self.assertIn(restore_head[0], MigrationRecorder(connection).applied_migrations())
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name IN ('core_application', 'core_applicationwithdrawal') "
                    "AND column_name IN ('authority_decision_reference', 'authority_reference', 'actor_access_epoch') "
                    "AND is_nullable = 'NO'"
                )
                self.assertEqual(cursor.fetchone()[0], 6)

    def _test_only_cleanup_migration_witness_rows(self):
        """Test-only fixture cleanup before 0027's locked-empty preflight; never used by production."""
        tables = (
            "core_applicationdecisioneffect", "core_applicationdecision", "core_applicationwithdrawal",
            "core_applicationengagement", "core_csaassignmentend", "core_csaassignment", "core_application",
        )
        # One transaction: on any failure PostgreSQL rolls back the DISABLE with everything else, so no guard is left disabled.
        with transaction.atomic(), connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"ALTER TABLE public.{table} DISABLE TRIGGER USER")
            for table in tables:
                cursor.execute(f"DELETE FROM public.{table}")
            # The deletions queue deferred foreign-key checks; fire them now, or ALTER TABLE refuses ("pending trigger events").
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            for table in reversed(tables):
                cursor.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER USER")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE c.relname = ANY(%s) AND NOT t.tgisinternal AND t.tgenabled <> 'O'",
                [list(tables)],
            )
            self.assertEqual(cursor.fetchall(), [])

    def _chain_columns(self, transition_id):
        """Read the 0022 chain columns the Django model does not expose, from the stored row itself."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, actor_capacity, entry_mode, composer_state, enterer_identity_id "
                "FROM public.core_organismmembershiptransition WHERE id = %s", [transition_id],
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row, "the membership transition must exist")
        return dict(zip(("actor_state", "actor_capacity", "entry_mode", "composer_state", "enterer_identity_id"), row))

    def _assert_schema_in_force(self, *, post_lock_time):
        """Observe, do not assume, the schema a leg runs on: the migration record AND the installed guard definitions."""
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertEqual(("core", "0026_s015_application_admission_time") in applied, post_lock_time,
                         "the migration record must match the schema this leg witnesses")
        with connection.cursor() as cursor:
            for name in ("s3_claim_guard", "s3_end_guard", "s3_engagement_guard", "s3_terminal_lock"):
                cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [f"public.{name}()"])
                body = cursor.fetchone()[0]
                self.assertEqual("NEW.recorded_at := clock_timestamp();" in body, post_lock_time,
                                 f"{name}'s installed body must match the schema this leg witnesses")

    def _assert_refused_by(self, outcome, message):
        """A refusal counts only if it is the named guard's: any other failure fails the test with its diagnostics."""
        self.assertIsInstance(outcome, IntegrityError, f"expected the database refusal {message!r}, got {outcome!r}")
        self.assertIn(message, str(outcome), f"refused, but not by {message!r}: {outcome}")

    def _fresh_claim(self):
        application_uuid = uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_application (application_uuid, applicant_id, living_organism_id, actor_id, "
                "enterer_id, occurred_at, effective_at, received_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                [str(application_uuid), self.applicant.pk, self.ids["lo"], self.applicant.pk,
                 self.applicant.pk, NOW, NOW, NOW],
            )
            application_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO core_csaassignment (assignment_uuid, application_id, assignee_membership_id, "
                "assignee_office_id, actor_id, enterer_id, completion_due_at, occurred_at, effective_at, received_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                [str(uuid4()), application_id, self.ids["membership"], self.ids["assignments"]["COORDINATOR"],
                 self.coordinator.pk, self.coordinator.pk, NOW + timedelta(days=27), NOW, NOW, NOW],
            )
            claim_id = cursor.fetchone()[0]
            cursor.execute("SELECT assignment_uuid FROM core_csaassignment WHERE id = %s", [claim_id])
            assignment_uuid = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO core_applicationengagement (application_id, engagement_ordinal, route, "
                "deciding_party_id, engagement_reference, settings_basis, actor_id, enterer_id, "
                "occurred_at, effective_at, received_at) "
                "VALUES (%s, 1, 'LO_CSA', %s, %s, %s, %s, %s, %s, %s, %s)",
                [application_id, self.ids["lo"], str(assignment_uuid), SETTINGS,
                 self.coordinator.pk, self.coordinator.pk, NOW, NOW, NOW],
            )
        application = Application(pk=application_id, application_uuid=application_uuid)
        return application

    def _direct_decision(self, cursor, application, claim):
        cursor.execute(
            "INSERT INTO core_applicationdecision (decision_uuid, application_id, route, "
            "deciding_party_id, outcome, assignment_reference_id, responsible_coordinator_id, "
            "responsible_capacity, qualifying_office_id, authority_decision_reference, executor_id, "
            "enterer_id, occurred_at, effective_at, received_at) VALUES "
            "(%s, %s, 'LO_CSA', %s, 'DECLINE', %s, %s, 'COORDINATOR', %s, %s, %s, %s, %s, %s, %s)",
            [str(uuid4()), application.pk, self.ids["lo"], claim.pk,
             self.coordinator.pk, claim.assignee_office_id, "s015d1:" + "a" * 64,
             self.coordinator.pk, self.coordinator.pk, NOW, NOW, NOW],
        )

    def _blocked_insert_witness(self, application, terminator):
        """Return True if B's decision was admitted; otherwise the exception that refused it, for the caller to assert."""
        claim = CSAAssignment.objects.get(application=application)
        outcomes = Queue()
        backend = Queue()
        started = Event()

        def insert_decision():
            close_old_connections()
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend.put(cursor.fetchone()[0])
                    started.set()
                    self._direct_decision(cursor, application, claim)
                outcomes.put(True)
            except Exception as exc:  # returned with its diagnostics; never reduced to a bare False
                outcomes.put(exc)
            finally:
                connection.close()

        thread = threading.Thread(target=insert_decision, daemon=True)
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT public.s3_lock_application(%s)", [application.pk])
                thread.start()
                self.assertTrue(started.wait(timeout=10))
                pid = backend.get(timeout=10)
                deadline = time.monotonic() + 10
                observed_lock = False
                while time.monotonic() < deadline:
                    # pg_stat_activity is snapshotted at first access within a transaction; A polls inside its own
                    # transaction, so clear the snapshot before each poll or it would keep reading B's first state.
                    cursor.execute("SELECT pg_stat_clear_snapshot()")
                    cursor.execute(
                        "SELECT wait_event_type = 'Lock' AND query LIKE %s FROM pg_stat_activity WHERE pid = %s",
                        ["%INSERT INTO core_applicationdecision%", pid],
                    )
                    row = cursor.fetchone()
                    if row is not None and row[0]:
                        observed_lock = True
                        break
                self.assertTrue(observed_lock, "decision INSERT must be observed waiting in its lock guard")
                if terminator == "end":
                    CSAAssignmentEnd.objects.create(
                        assignment=claim, actor=self.coordinator, enterer=self.coordinator,
                        occurred_at=NOW, effective_at=NOW, received_at=NOW,
                    )
                else:
                    cursor.execute(
                        "INSERT INTO core_applicationwithdrawal (application_id, actor_id, enterer_id, "
                        "occurred_at, effective_at, received_at) VALUES (%s, %s, %s, %s, %s, %s)",
                        [application.pk, self.applicant.pk, self.applicant.pk, NOW, NOW, NOW],
                    )
        finally:
            # A's transaction has ended here — committed, or rolled back by a failure — so its lock is released and B can
            # finish. Join B on every path, before the caller restores the schema.
            if thread.ident is not None:
                thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "the decision worker must finish once A's lock is released")
        outcome = outcomes.get_nowait()
        if outcome is not True and not isinstance(outcome, IntegrityError):
            raise outcome
        self.assertEqual(ApplicationDecision.objects.filter(application=application).exists(), outcome is True)
        return outcome