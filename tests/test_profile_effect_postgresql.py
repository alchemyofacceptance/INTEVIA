"""PostgreSQL-only qualification guardians for S013 profile effect schema."""

from datetime import timedelta
from threading import Barrier, Event, Lock, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F
from django.test import TransactionTestCase
from django.utils import timezone

from core.models import (
    Identity,
    ProfileEffectProjectionDisposition,
    ProfileEffectProposalLineage,
    ProfileEffectProposalTransition,
)
from src.intevia.services.profile_effect_authority import (
    ProjectionAuthority,
    ProjectionAuthorityResponse,
    ProposalAuthority,
    ProposalAuthorityResponse,
)
from src.intevia.services.profile_effect_contract import (
    CreateServiceSubmissionProposalCommand,
    ProfileEffectProjectionDispositionCommand,
    ProfileEffectProposalCorrectionCommand,
    ProjectionState,
)
from src.intevia.services.profile_effect_read_service import ProfileEffectReadService
from src.intevia.services.profile_effect_service import (
    ProfileEffectCrossEpochConflict,
    ProfileEffectMalformedReplay,
    ProfileEffectPayloadConflict,
    ProfileEffectProjectionDispositionService,
    ProfileEffectProposalCorrectionService,
    ServiceSubmissionProfileEffectProposalService,
)
from src.intevia.services.service_activity_read_service import ServiceActivityReadService
from tests.test_profile_effect_service import (
    NOW,
    _DummyVisibilityProvider,
    _make_identity,
    _make_submitted_activity,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL S013 profile effect qualification guardian",
)


class _ProposalProvider:
    def authorise(self, *, request):
        return ProposalAuthorityResponse(
            database_alias=request.database_alias,
            actor_pk=request.actor_pk,
            actor_identity_id=request.actor_identity_id,
            actor_access_epoch=request.actor_access_epoch,
            action=request.action,
            target_fingerprint=request.target_fingerprint,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            evaluated_at=request.evaluated_at,
            authority_reference="AUTH-PROPOSAL-PG-001",
        )


class _ProjectionProvider:
    def authorise(self, *, request):
        return ProjectionAuthorityResponse(
            database_alias=request.database_alias,
            actor_pk=request.actor_pk,
            actor_identity_id=request.actor_identity_id,
            actor_access_epoch=request.actor_access_epoch,
            action=request.action,
            target_fingerprint=request.target_fingerprint,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            evaluated_at=request.evaluated_at,
            authority_reference="AUTH-PROJECTION-PG-001",
        )


class _ProposalAliasMismatchProvider:
    def authorise(self, *, request):
        return ProposalAuthorityResponse(
            database_alias="other",
            actor_pk=request.actor_pk,
            actor_identity_id=request.actor_identity_id,
            actor_access_epoch=request.actor_access_epoch,
            action=request.action,
            target_fingerprint=request.target_fingerprint,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            evaluated_at=request.evaluated_at,
            authority_reference="AUTH-PROPOSAL-PG-OTHER",
        )


class _RootWaitCorrectionService(ProfileEffectProposalCorrectionService):
    def __init__(self, *, waiting_on_root: Event, **kwargs):
        super().__init__(**kwargs)
        self._waiting_on_root = waiting_on_root

    def _lock_lineage(self, lineage_id):
        self._waiting_on_root.set()
        return super()._lock_lineage(lineage_id)


class _BlockingReplayProposalService(ServiceSubmissionProfileEffectProposalService):
    def __init__(self, *, replay_locked: Event, release_replay: Event, **kwargs):
        super().__init__(**kwargs)
        self._replay_locked = replay_locked
        self._release_replay = release_replay
        self._paused_once = False

    def _lock_dispositions_by_transition_for_replay(self, transitions):
        grouped = super()._lock_dispositions_by_transition_for_replay(transitions)
        if not self._paused_once:
            self._paused_once = True
            self._replay_locked.set()
            if not self._release_replay.wait(timeout=5):
                raise AssertionError("replay hold was not released")
        return grouped


class _ForcedCorrectionWinnerService(ProfileEffectProposalCorrectionService):
    def _try_integrity(self, *, write_fn, replay_fn, allowed_constraints):
        self.observed_constraints = allowed_constraints
        write_fn()
        return replay_fn()


class _ForcedProjectionWinnerService(ProfileEffectProjectionDispositionService):
    def _try_integrity(self, *, write_fn, replay_fn, allowed_constraints):
        self.observed_constraints = allowed_constraints
        write_fn()
        return replay_fn()


@POSTGRESQL_ONLY
class ProfileEffectPostgreSQLCatalogueTests(TransactionTestCase):
    reset_sequences = True

    def test_s013_named_constraints_exist(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE connamespace = 'public'::regnamespace
                  AND conrelid IN (
                    'core_profileeffectproposallineage'::regclass,
                    'core_profileeffectproposaltransition'::regclass,
                    'core_profileeffectprojectiondisposition'::regclass
                  )
                """
            )
            constraint_names = {row[0] for row in cursor.fetchall()}

        expected_constraints = {
            "s013_pe_lineage_id_uniq",
            "s013_pe_lineage_semantic_uniq",
            "s013_pe_subject_proposer_ck",
            "s013_pe_subject_relation_ck",
            "s013_pe_effect_type_ck",
            "s013_pe_contract_version_ck",
            "s013_pe_source_sequence_positive_ck",
            "s013_pe_source_lineage_ref_ck",
            "s013_pe_source_qualification_ref_ck",
            "s013_pe_source_refs_nonempty_ck",
            "s013_pe_prop_sequence_uniq",
            "s013_pe_prop_actor_action_idem_uniq",
            "s013_pe_prop_lineage_ref_uniq",
            "s013_pe_prop_sequence_positive_ck",
            "s013_pe_prop_action_valid_ck",
            "s013_pe_prop_from_state_valid_ck",
            "s013_pe_prop_to_state_valid_ck",
            "s013_pe_prop_edge_valid_ck",
            "s013_pe_prop_payload_hex_ck",
            "s013_pe_prop_decision_ref_ck",
            "s013_pe_prop_lineage_ref_ck",
            "s013_pe_prop_refs_nonempty_ck",
            "s013_pe_proj_sequence_uniq",
            "s013_pe_proj_actor_action_idem_uniq",
            "s013_pe_proj_lineage_ref_uniq",
            "s013_pe_proj_sequence_positive_ck",
            "s013_pe_proj_action_valid_ck",
            "s013_pe_proj_from_state_valid_ck",
            "s013_pe_proj_to_state_valid_ck",
            "s013_pe_proj_edge_valid_ck",
            "s013_pe_proj_payload_hex_ck",
            "s013_pe_proj_decision_ref_ck",
            "s013_pe_proj_lineage_ref_ck",
            "s013_pe_proj_refs_nonempty_ck",
        }
        self.assertEqual(expected_constraints - constraint_names, set())

    def test_s013_named_indexes_exist(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename IN (
                    'core_profileeffectproposallineage',
                    'core_profileeffectproposaltransition',
                    'core_profileeffectprojectiondisposition'
                  )
                """
            )
            index_names = {row[0] for row in cursor.fetchall()}

        expected_indexes = {
            "s013_pe_subject_idx",
            "s013_pe_source_activity_idx",
            "s013_pe_source_lineage_idx",
            "s013_pe_prop_actor_idx",
            "s013_pe_prop_root_action_idx",
            "s013_pe_proj_actor_idx",
            "s013_pe_proj_prop_action_idx",
            "s013_pe_current_survivor_uniq",
            "s013_pe_head_proposal_uniq",
            "s013_pe_prop_initial_uniq",
            "s013_pe_prop_successor_uniq",
            "s013_pe_proj_initial_uniq",
            "s013_pe_proj_successor_uniq",
        }
        self.assertEqual(expected_indexes - index_names, set())

    def test_s013_identity_fk_targets_are_correct(self):
        expected_fk_pairs = {
            ("core_profileeffectproposallineage", "subject_id"),
            ("core_profileeffectproposallineage", "proposer_id"),
            ("core_profileeffectproposaltransition", "actor_id"),
            ("core_profileeffectprojectiondisposition", "actor_id"),
        }
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source.relname, attribute.attname
                FROM pg_constraint constraint_row
                JOIN pg_class source
                  ON source.oid = constraint_row.conrelid
                JOIN pg_class target
                  ON target.oid = constraint_row.confrelid
                JOIN LATERAL unnest(constraint_row.conkey) AS key(attnum)
                  ON TRUE
                JOIN pg_attribute attribute
                  ON attribute.attrelid = source.oid
                 AND attribute.attnum = key.attnum
                WHERE constraint_row.contype = 'f'
                  AND target.relname = 'core_identity'
                  AND source.relname LIKE 'core_profileeffect%'
                """
            )
            observed = set(cursor.fetchall())
        self.assertEqual(observed, expected_fk_pairs)
        self.assertEqual(len(observed), 4)

    def test_s013_identity_fk_delete_action_is_protect(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source.relname, attribute.attname, constraint_row.confdeltype
                FROM pg_constraint constraint_row
                JOIN pg_class source ON source.oid = constraint_row.conrelid
                JOIN pg_class target ON target.oid = constraint_row.confrelid
                JOIN LATERAL unnest(constraint_row.conkey) AS key(attnum) ON TRUE
                JOIN pg_attribute attribute
                  ON attribute.attrelid = source.oid AND attribute.attnum = key.attnum
                WHERE constraint_row.contype = 'f'
                  AND target.relname = 'core_identity'
                  AND source.relname IN (
                    'core_profileeffectproposallineage',
                    'core_profileeffectproposaltransition',
                    'core_profileeffectprojectiondisposition'
                  )
                """
            )
            rows = cursor.fetchall()

        self.assertEqual(len(rows), 4)
        for table, column, delete_action in rows:
            self.assertEqual(delete_action, "a", f"{table}.{column} FK should use PROTECT/NO ACTION")


@POSTGRESQL_ONLY
class ProfileEffectPostgreSQLNovelShapeTests(TransactionTestCase):
    reset_sequences = True

    @staticmethod
    def run_concurrently(functions):
        barrier = Barrier(len(functions))
        result_lock = Lock()
        results = []

        def invoke(function):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                value = function()
            except Exception as error:  # pragma: no cover
                value = error
            finally:
                close_old_connections()
            with result_lock:
                results.append(value)

        threads = [Thread(target=invoke, args=(function,)) for function in functions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        if any(thread.is_alive() for thread in threads):
            raise AssertionError("concurrent guardian did not complete")
        return results

    def setUp(self):
        self.subject = _make_identity("profile-effect-pg-subject")
        self.activity = _make_submitted_activity(self.subject)
        self.other_activity = _make_submitted_activity(self.subject)
        self.read_service = ServiceActivityReadService(
            visibility_provider=_DummyVisibilityProvider(),
            clock=lambda: NOW,
        )
        self.proposal_service = ServiceSubmissionProfileEffectProposalService(
            authority=ProposalAuthority(provider=_ProposalProvider()),
            read_service=self.read_service,
            clock=lambda: NOW,
        )
        self.correction_service = ProfileEffectProposalCorrectionService(
            authority=ProposalAuthority(provider=_ProposalProvider()),
            clock=lambda: NOW,
        )
        self.projection_service = ProfileEffectProjectionDispositionService(
            authority=ProjectionAuthority(provider=_ProjectionProvider()),
            clock=lambda: NOW,
        )
        self.subject_read_service = ProfileEffectReadService(clock=lambda: NOW)

    def _create_command(self, *, activity_id, request_reference, idempotency_key, minute):
        return CreateServiceSubmissionProposalCommand(
            credential=self.subject.credential,
            actor_access_epoch=self.subject.access_epoch,
            activity_id=activity_id,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=NOW + timedelta(minutes=minute),
        )

    def _create_receipt(self, *, suffix, activity_id=None, minute=10):
        return self.proposal_service.create_service_submission_proposal(
            self._create_command(
                activity_id=activity_id or self.activity.activity_id,
                request_reference=f"REQ-PG-CREATE-{suffix}",
                idempotency_key=f"IDEM-PG-CREATE-{suffix}",
                minute=minute,
            )
        )

    def _correction_command(self, *, lineage_id, head_pk, head_ref, suffix, minute):
        return ProfileEffectProposalCorrectionCommand(
            credential=self.subject.credential,
            actor_access_epoch=self.subject.access_epoch,
            lineage_id=lineage_id,
            expected_head_transition_pk=head_pk,
            expected_head_lineage_reference=head_ref,
            request_reference=f"REQ-PG-{suffix}",
            idempotency_key=f"IDEM-PG-{suffix}",
            occurred_at=NOW + timedelta(minutes=minute),
        )

    def _projection_command(
        self,
        *,
        lineage_id,
        proposal_pk,
        proposal_ref,
        disposition_pk,
        disposition_ref,
        suffix,
        minute,
    ):
        return ProfileEffectProjectionDispositionCommand(
            credential=self.subject.credential,
            actor_access_epoch=self.subject.access_epoch,
            lineage_id=lineage_id,
            expected_proposal_transition_pk=proposal_pk,
            expected_proposal_lineage_reference=proposal_ref,
            expected_disposition_pk_or_null=disposition_pk,
            expected_disposition_lineage_reference_or_null=disposition_ref,
            request_reference=f"REQ-PG-{suffix}",
            idempotency_key=f"IDEM-PG-{suffix}",
            occurred_at=NOW + timedelta(minutes=minute),
        )

    def test_concurrent_create_produces_one_winner_and_one_exact_replay(self):
        command = self._create_command(
            activity_id=self.activity.activity_id,
            request_reference="REQ-PG-CONCURRENT-CREATE-001",
            idempotency_key="IDEM-PG-CONCURRENT-CREATE-001",
            minute=10,
        )
        results = self.run_concurrently(
            [
                lambda: self.proposal_service.create_service_submission_proposal(command),
                lambda: self.proposal_service.create_service_submission_proposal(command),
            ]
        )
        receipts = [value for value in results if hasattr(value, "lineage_id")]
        self.assertEqual(len(receipts), 2)
        self.assertEqual(ProfileEffectProposalLineage.objects.count(), 1)
        self.assertEqual(ProfileEffectProposalTransition.objects.count(), 1)
        self.assertEqual({receipt.replayed for receipt in receipts}, {False, True})

    def test_same_key_different_payload_fails_closed(self):
        original = self._create_command(
            activity_id=self.activity.activity_id,
            request_reference="REQ-PG-PAYLOAD-001",
            idempotency_key="IDEM-PG-PAYLOAD-001",
            minute=10,
        )
        self.proposal_service.create_service_submission_proposal(original)

        with self.assertRaises(ProfileEffectPayloadConflict):
            self.proposal_service.create_service_submission_proposal(
                self._create_command(
                    activity_id=self.activity.activity_id,
                    request_reference="REQ-PG-PAYLOAD-CHANGED-001",
                    idempotency_key="IDEM-PG-PAYLOAD-001",
                    minute=11,
                )
            )

    def test_exact_replay_racing_different_payload_same_key_returns_replay_and_conflict(self):
        original = self._create_command(
            activity_id=self.activity.activity_id,
            request_reference="REQ-PG-PAYLOAD-RACE-001",
            idempotency_key="IDEM-PG-PAYLOAD-RACE-001",
            minute=10,
        )
        created = self.proposal_service.create_service_submission_proposal(original)
        changed = self._create_command(
            activity_id=self.activity.activity_id,
            request_reference="REQ-PG-PAYLOAD-RACE-CHANGED-001",
            idempotency_key="IDEM-PG-PAYLOAD-RACE-001",
            minute=11,
        )

        results = self.run_concurrently(
            [
                lambda: self.proposal_service.create_service_submission_proposal(original),
                lambda: self.proposal_service.create_service_submission_proposal(changed),
            ]
        )

        receipts = [value for value in results if hasattr(value, "proposal_transition_pk")]
        errors = [value for value in results if isinstance(value, Exception)]
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0].replayed)
        self.assertEqual(receipts[0].proposal_transition_pk, created.proposal_transition_pk)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ProfileEffectPayloadConflict)
        self.assertEqual(ProfileEffectProposalLineage.objects.count(), 1)
        self.assertEqual(ProfileEffectProposalTransition.objects.count(), 1)

    def test_supersede_replay_reconstructs_historical_target(self):
        created = self._create_receipt(suffix="SUPERSEDE-REPLAY-001")
        command = self._correction_command(
            lineage_id=created.lineage_id,
            head_pk=created.proposal_transition_pk,
            head_ref=created.proposal_lineage_reference,
            suffix="SUPERSEDE-REPLAY-001",
            minute=11,
        )
        original = self.correction_service.supersede_profile_effect_proposal(command)
        replayed = self.correction_service.supersede_profile_effect_proposal(command)
        self.assertFalse(original.replayed)
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.proposal_transition_pk, original.proposal_transition_pk)

    def test_void_replay_reconstructs_historical_target_after_no_current_survivor(self):
        created = self._create_receipt(suffix="VOID-REPLAY-001")
        command = self._correction_command(
            lineage_id=created.lineage_id,
            head_pk=created.proposal_transition_pk,
            head_ref=created.proposal_lineage_reference,
            suffix="VOID-REPLAY-001",
            minute=11,
        )
        original = self.correction_service.void_profile_effect_proposal(command)
        replayed = self.correction_service.void_profile_effect_proposal(command)
        self.assertFalse(original.replayed)
        self.assertTrue(replayed.replayed)
        self.assertFalse(replayed.has_current_survivor)

    def test_correction_named_constraint_winner_callback_reloads_complete_aggregate(self):
        created = self._create_receipt(suffix="FORCED-CORRECTION-WINNER-001")
        command = self._correction_command(
            lineage_id=created.lineage_id,
            head_pk=created.proposal_transition_pk,
            head_ref=created.proposal_lineage_reference,
            suffix="FORCED-CORRECTION-WINNER-001",
            minute=11,
        )
        service = _ForcedCorrectionWinnerService(
            authority=ProposalAuthority(provider=_ProposalProvider()),
            clock=lambda: NOW,
        )
        with patch.object(
            service,
            "_resolve_correction_replay",
            wraps=service._resolve_correction_replay,
        ) as resolver:
            receipt = service.supersede_profile_effect_proposal(command)
        self.assertTrue(receipt.replayed)
        self.assertEqual(receipt.proposal_transition_pk, resolver.call_args.kwargs["existing"].pk)
        self.assertTrue(resolver.call_args.kwargs["transitions"])
        self.assertTrue(resolver.call_args.kwargs["grouped"])
        self.assertEqual(len(resolver.call_args.kwargs["transitions"]), 2)
        self.assertIn("s013_pe_prop_actor_action_idem_uniq", service.observed_constraints)

    def test_projection_named_constraint_winner_callback_reloads_complete_aggregate(self):
        created = self._create_receipt(suffix="FORCED-PROJECTION-WINNER-001")
        command = self._projection_command(
            lineage_id=created.lineage_id,
            proposal_pk=created.proposal_transition_pk,
            proposal_ref=created.proposal_lineage_reference,
            disposition_pk=None,
            disposition_ref=None,
            suffix="FORCED-PROJECTION-WINNER-001",
            minute=11,
        )
        service = _ForcedProjectionWinnerService(
            authority=ProjectionAuthority(provider=_ProjectionProvider()),
            clock=lambda: NOW,
        )
        with patch.object(
            service,
            "_resolve_projection_replay",
            wraps=service._resolve_projection_replay,
        ) as resolver:
            receipt = service.authorise_profile_effect_projection(command)
        self.assertTrue(receipt.replayed)
        self.assertEqual(receipt.projection_disposition_pk, resolver.call_args.kwargs["existing"].pk)
        self.assertTrue(resolver.call_args.kwargs["transitions"])
        self.assertTrue(resolver.call_args.kwargs["grouped"])
        self.assertTrue(resolver.call_args.kwargs["grouped"][created.proposal_transition_pk])
        self.assertIn("s013_pe_proj_actor_action_idem_uniq", service.observed_constraints)

    def test_unknown_integrity_constraint_propagates_without_winner_recovery(self):
        error = IntegrityError("unknown constraint")
        with patch.object(
            self.correction_service,
            "_check_constraint_name",
            return_value="s013_unknown_constraint",
        ):
            with self.assertRaises(IntegrityError) as raised:
                self.correction_service._try_integrity(
                    write_fn=lambda: (_ for _ in ()).throw(error),
                    replay_fn=lambda: self.fail("unknown constraint entered replay"),
                    allowed_constraints=frozenset({"s013_pe_prop_sequence_uniq"}),
                )
        self.assertIs(raised.exception, error)

    def test_unknown_projection_constraint_propagates_without_winner_recovery(self):
        error = IntegrityError("unknown projection constraint")
        with patch.object(
            self.projection_service,
            "_check_constraint_name",
            return_value="s013_unknown_projection_constraint",
        ):
            with self.assertRaises(IntegrityError) as raised:
                self.projection_service._try_integrity(
                    write_fn=lambda: (_ for _ in ()).throw(error),
                    replay_fn=lambda: self.fail("unknown constraint entered replay"),
                    allowed_constraints=frozenset({"s013_pe_proj_sequence_uniq"}),
                )
        self.assertIs(raised.exception, error)

    def test_projection_replays_cover_authorise_decline_and_withdraw_after_state_advances(self):
        first = self._create_receipt(suffix="AUTH-REPLAY-001")
        auth_command = self._projection_command(
            lineage_id=first.lineage_id,
            proposal_pk=first.proposal_transition_pk,
            proposal_ref=first.proposal_lineage_reference,
            disposition_pk=None,
            disposition_ref=None,
            suffix="AUTH-REPLAY-001",
            minute=11,
        )
        original_auth = self.projection_service.authorise_profile_effect_projection(auth_command)
        withdrawn = self.projection_service.withdraw_profile_effect_projection(
            self._projection_command(
                lineage_id=first.lineage_id,
                proposal_pk=first.proposal_transition_pk,
                proposal_ref=first.proposal_lineage_reference,
                disposition_pk=original_auth.projection_disposition_pk,
                disposition_ref=original_auth.projection_lineage_reference,
                suffix="WITHDRAW-AFTER-AUTH-001",
                minute=12,
            )
        )
        replayed_auth = self.projection_service.authorise_profile_effect_projection(auth_command)

        second = self._create_receipt(
            suffix="DECLINE-REPLAY-001",
            activity_id=self.other_activity.activity_id,
            minute=20,
        )
        decline_command = self._projection_command(
            lineage_id=second.lineage_id,
            proposal_pk=second.proposal_transition_pk,
            proposal_ref=second.proposal_lineage_reference,
            disposition_pk=None,
            disposition_ref=None,
            suffix="DECLINE-REPLAY-001",
            minute=21,
        )
        original_decline = self.projection_service.decline_profile_effect_projection(
            decline_command
        )
        self.projection_service.authorise_profile_effect_projection(
            self._projection_command(
                lineage_id=second.lineage_id,
                proposal_pk=second.proposal_transition_pk,
                proposal_ref=second.proposal_lineage_reference,
                disposition_pk=original_decline.projection_disposition_pk,
                disposition_ref=original_decline.projection_lineage_reference,
                suffix="AUTHORISE-AFTER-DECLINE-001",
                minute=22,
            )
        )
        replayed_decline = self.projection_service.decline_profile_effect_projection(
            decline_command
        )

        third = self.projection_service.authorise_profile_effect_projection(
            self._projection_command(
                lineage_id=first.lineage_id,
                proposal_pk=first.proposal_transition_pk,
                proposal_ref=first.proposal_lineage_reference,
                disposition_pk=withdrawn.projection_disposition_pk,
                disposition_ref=withdrawn.projection_lineage_reference,
                suffix="REAUTHORISE-AFTER-WITHDRAW-001",
                minute=13,
            )
        )
        replayed_withdraw = self.projection_service.withdraw_profile_effect_projection(
            self._projection_command(
                lineage_id=first.lineage_id,
                proposal_pk=first.proposal_transition_pk,
                proposal_ref=first.proposal_lineage_reference,
                disposition_pk=original_auth.projection_disposition_pk,
                disposition_ref=original_auth.projection_lineage_reference,
                suffix="WITHDRAW-AFTER-AUTH-001",
                minute=12,
            )
        )

        self.assertTrue(replayed_auth.replayed)
        self.assertEqual(replayed_auth.projection_disposition_pk, original_auth.projection_disposition_pk)
        self.assertTrue(replayed_decline.replayed)
        self.assertEqual(replayed_decline.projection_disposition_pk, original_decline.projection_disposition_pk)
        self.assertTrue(replayed_withdraw.replayed)
        self.assertEqual(replayed_withdraw.projection_disposition_pk, withdrawn.projection_disposition_pk)
        self.assertEqual(third.to_state, ProjectionState.AUTHORISED)

    def test_projection_replay_after_later_supersede_stays_bound_to_historical_transition(self):
        created = self._create_receipt(suffix="PROJ-SUPERSEDE-001")
        projection_command = self._projection_command(
            lineage_id=created.lineage_id,
            proposal_pk=created.proposal_transition_pk,
            proposal_ref=created.proposal_lineage_reference,
            disposition_pk=None,
            disposition_ref=None,
            suffix="PROJ-SUPERSEDE-001",
            minute=11,
        )
        original = self.projection_service.authorise_profile_effect_projection(
            projection_command
        )
        successor = self.correction_service.supersede_profile_effect_proposal(
            self._correction_command(
                lineage_id=created.lineage_id,
                head_pk=created.proposal_transition_pk,
                head_ref=created.proposal_lineage_reference,
                suffix="PROJ-SUPERSEDE-002",
                minute=12,
            )
        )
        replayed = self.projection_service.authorise_profile_effect_projection(
            projection_command
        )
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.proposal_transition_pk, original.proposal_transition_pk)
        self.assertNotEqual(replayed.proposal_transition_pk, successor.proposal_transition_pk)

    def test_concurrent_supersede_and_void_do_not_branch_lineage(self):
        created = self._create_receipt(suffix="CONCURRENT-CORRECTION-001")
        supersede = self._correction_command(
            lineage_id=created.lineage_id,
            head_pk=created.proposal_transition_pk,
            head_ref=created.proposal_lineage_reference,
            suffix="CONCURRENT-SUPERSEDE-001",
            minute=11,
        )
        void = self._correction_command(
            lineage_id=created.lineage_id,
            head_pk=created.proposal_transition_pk,
            head_ref=created.proposal_lineage_reference,
            suffix="CONCURRENT-VOID-001",
            minute=11,
        )
        results = self.run_concurrently(
            [
                lambda: self.correction_service.supersede_profile_effect_proposal(supersede),
                lambda: self.correction_service.void_profile_effect_proposal(void),
            ]
        )
        successes = [value for value in results if hasattr(value, "proposal_transition_pk")]
        self.assertEqual(len(successes), 1)
        self.assertEqual(ProfileEffectProposalTransition.objects.count(), 2)
        final_root = ProfileEffectProposalLineage.objects.get(lineage_id=created.lineage_id)
        self.assertEqual(final_root.head_proposal_transition.sequence, 2)

    def test_concurrent_authorise_and_decline_do_not_branch_disposition_lineage(self):
        created = self._create_receipt(suffix="CONCURRENT-PROJECTION-001")
        authorise = self._projection_command(
            lineage_id=created.lineage_id,
            proposal_pk=created.proposal_transition_pk,
            proposal_ref=created.proposal_lineage_reference,
            disposition_pk=None,
            disposition_ref=None,
            suffix="CONCURRENT-AUTHORISE-001",
            minute=11,
        )
        decline = self._projection_command(
            lineage_id=created.lineage_id,
            proposal_pk=created.proposal_transition_pk,
            proposal_ref=created.proposal_lineage_reference,
            disposition_pk=None,
            disposition_ref=None,
            suffix="CONCURRENT-DECLINE-001",
            minute=11,
        )
        results = self.run_concurrently(
            [
                lambda: self.projection_service.authorise_profile_effect_projection(authorise),
                lambda: self.projection_service.decline_profile_effect_projection(decline),
            ]
        )
        successes = [value for value in results if hasattr(value, "projection_disposition_pk")]
        self.assertEqual(len(successes), 1)
        self.assertEqual(ProfileEffectProjectionDisposition.objects.count(), 1)

    def test_replay_withdraw_can_run_concurrently_with_reauthorise_after_withdrawal(self):
        created = self._create_receipt(suffix="WITHDRAW-REAUTHORISE-001")
        authorised = self.projection_service.authorise_profile_effect_projection(
            self._projection_command(
                lineage_id=created.lineage_id,
                proposal_pk=created.proposal_transition_pk,
                proposal_ref=created.proposal_lineage_reference,
                disposition_pk=None,
                disposition_ref=None,
                suffix="WITHDRAW-REAUTHORISE-AUTH-001",
                minute=11,
            )
        )
        withdraw = self._projection_command(
            lineage_id=created.lineage_id,
            proposal_pk=created.proposal_transition_pk,
            proposal_ref=created.proposal_lineage_reference,
            disposition_pk=authorised.projection_disposition_pk,
            disposition_ref=authorised.projection_lineage_reference,
            suffix="WITHDRAW-REAUTHORISE-WITHDRAW-001",
            minute=12,
        )
        withdrawn = self.projection_service.withdraw_profile_effect_projection(withdraw)
        reauthorise = self._projection_command(
            lineage_id=created.lineage_id,
            proposal_pk=created.proposal_transition_pk,
            proposal_ref=created.proposal_lineage_reference,
            disposition_pk=withdrawn.projection_disposition_pk,
            disposition_ref=withdrawn.projection_lineage_reference,
            suffix="WITHDRAW-REAUTHORISE-NEW-001",
            minute=13,
        )
        results = self.run_concurrently(
            [
                lambda: self.projection_service.withdraw_profile_effect_projection(withdraw),
                lambda: self.projection_service.authorise_profile_effect_projection(reauthorise),
            ]
        )
        receipts = [value for value in results if hasattr(value, "projection_disposition_pk")]
        self.assertEqual(len(receipts), 2)
        self.assertEqual(ProfileEffectProjectionDisposition.objects.count(), 3)
        self.assertEqual(
            ProfileEffectProjectionDisposition.objects.order_by("sequence").last().to_state,
            ProjectionState.AUTHORISED.value,
        )

    def test_access_epoch_change_attempt_waiting_on_root_lock_serialises_and_stale_epoch_fails(self):
        created = self._create_receipt(suffix="ROOT-EPOCH-001")
        command = self._correction_command(
            lineage_id=created.lineage_id,
            head_pk=created.proposal_transition_pk,
            head_ref=created.proposal_lineage_reference,
            suffix="ROOT-EPOCH-001",
            minute=11,
        )
        root_locked = Event()
        release_root = Event()
        waiting_on_root = Event()
        correction_finished = Event()
        update_finished = Event()
        correction_results = []
        update_results = []

        service = _RootWaitCorrectionService(
            authority=ProposalAuthority(provider=_ProposalProvider()),
            waiting_on_root=waiting_on_root,
            clock=lambda: NOW,
        )

        def hold_root_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    ProfileEffectProposalLineage.objects.select_for_update().get(
                        lineage_id=created.lineage_id
                    )
                    root_locked.set()
                    if not release_root.wait(timeout=5):
                        raise AssertionError("root lock was not released")
            finally:
                close_old_connections()

        def run_correction():
            close_old_connections()
            try:
                correction_results.append(service.supersede_profile_effect_proposal(command))
            except Exception as error:  # pragma: no cover
                correction_results.append(error)
            finally:
                correction_finished.set()
                close_old_connections()

        def bump_epoch():
            close_old_connections()
            try:
                with transaction.atomic():
                    update_results.append(
                        Identity.objects.filter(pk=self.subject.pk).update(
                            access_epoch=F("access_epoch") + 1
                        )
                    )
            except Exception as error:  # pragma: no cover
                update_results.append(error)
            finally:
                update_finished.set()
                close_old_connections()

        locker = Thread(target=hold_root_lock)
        locker.start()
        self.assertTrue(root_locked.wait(timeout=5))

        contender = Thread(target=run_correction)
        contender.start()
        self.assertTrue(waiting_on_root.wait(timeout=5))
        self.assertFalse(correction_finished.is_set())

        epoch_updater = Thread(target=bump_epoch)
        epoch_updater.start()
        try:
            epoch_updater.join(timeout=0.2)
            self.assertFalse(update_finished.is_set())
        finally:
            release_root.set()

        locker.join(timeout=5)
        contender.join(timeout=5)
        epoch_updater.join(timeout=5)

        self.assertEqual(len(correction_results), 1)
        self.assertFalse(isinstance(correction_results[0], Exception))
        self.assertEqual(update_results, [1])
        self.subject.refresh_from_db()
        self.assertEqual(self.subject.access_epoch, 1)
        with self.assertRaises(ProfileEffectCrossEpochConflict):
            service.supersede_profile_effect_proposal(command)

    def test_create_replay_contending_with_correction_serialises_on_shared_identity_and_root(self):
        created = self._create_receipt(suffix="REPLAY-CORRECTION-001")
        replay_command = self._create_command(
            activity_id=self.activity.activity_id,
            request_reference="REQ-PG-CREATE-REPLAY-CORRECTION-001",
            idempotency_key="IDEM-PG-CREATE-REPLAY-CORRECTION-001",
            minute=10,
        )
        correction_command = self._correction_command(
            lineage_id=created.lineage_id,
            head_pk=created.proposal_transition_pk,
            head_ref=created.proposal_lineage_reference,
            suffix="REPLAY-CORRECTION-001",
            minute=11,
        )
        replay_locked = Event()
        release_replay = Event()
        correction_done = Event()
        replay_results = []
        correction_results = []

        replay_service = _BlockingReplayProposalService(
            authority=ProposalAuthority(provider=_ProposalProvider()),
            read_service=self.read_service,
            replay_locked=replay_locked,
            release_replay=release_replay,
            clock=lambda: NOW,
        )

        def run_replay():
            close_old_connections()
            try:
                replay_results.append(
                    replay_service.create_service_submission_proposal(replay_command)
                )
            except Exception as error:  # pragma: no cover
                replay_results.append(error)
            finally:
                close_old_connections()

        def run_correction():
            close_old_connections()
            try:
                correction_results.append(
                    self.correction_service.supersede_profile_effect_proposal(correction_command)
                )
            except Exception as error:  # pragma: no cover
                correction_results.append(error)
            finally:
                correction_done.set()
                close_old_connections()

        replay_thread = Thread(target=run_replay)
        replay_thread.start()
        self.assertTrue(replay_locked.wait(timeout=5))

        correction_thread = Thread(target=run_correction)
        correction_thread.start()
        correction_thread.join(timeout=0.2)
        self.assertFalse(correction_done.is_set())

        release_replay.set()
        replay_thread.join(timeout=5)
        correction_thread.join(timeout=5)

        self.assertEqual(len(replay_results), 1)
        self.assertEqual(len(correction_results), 1)
        self.assertTrue(replay_results[0].replayed)
        self.assertEqual(
            replay_results[0].proposal_transition_pk,
            created.proposal_transition_pk,
        )
        self.assertFalse(correction_results[0].replayed)
        self.assertEqual(ProfileEffectProposalTransition.objects.count(), 2)
        final_root = ProfileEffectProposalLineage.objects.get(lineage_id=created.lineage_id)
        self.assertEqual(
            final_root.head_proposal_transition_id,
            correction_results[0].proposal_transition_pk,
        )

    def test_authority_mismatch_after_source_locks_leaves_zero_residue(self):
        service = ServiceSubmissionProfileEffectProposalService(
            authority=ProposalAuthority(provider=_ProposalAliasMismatchProvider()),
            read_service=self.read_service,
            clock=lambda: NOW,
        )
        with self.assertRaises(Exception):
            service.create_service_submission_proposal(
                self._create_command(
                    activity_id=self.activity.activity_id,
                    request_reference="REQ-PG-ALIAS-MISMATCH-001",
                    idempotency_key="IDEM-PG-ALIAS-MISMATCH-001",
                    minute=10,
                )
            )
        self.assertEqual(ProfileEffectProposalLineage.objects.count(), 0)
        self.assertEqual(ProfileEffectProposalTransition.objects.count(), 0)

    def test_forced_rollback_after_root_creation_leaves_zero_rows(self):
        def fail_save(instance, *args, **kwargs):
            raise RuntimeError("forced transition failure")

        with patch.object(ProfileEffectProposalTransition, "save", new=fail_save):
            with self.assertRaises(RuntimeError):
                self.proposal_service.create_service_submission_proposal(
                    self._create_command(
                        activity_id=self.activity.activity_id,
                        request_reference="REQ-PG-ROLLBACK-001",
                        idempotency_key="IDEM-PG-ROLLBACK-001",
                        minute=10,
                    )
                )

        self.assertEqual(ProfileEffectProposalLineage.objects.count(), 0)
        self.assertEqual(ProfileEffectProposalTransition.objects.count(), 0)

    def test_readback_rejects_privileged_survivor_corruption(self):
        created = self._create_receipt(suffix="READBACK-CORRUPT-001")
        self.correction_service.void_profile_effect_proposal(
            self._correction_command(
                lineage_id=created.lineage_id,
                head_pk=created.proposal_transition_pk,
                head_ref=created.proposal_lineage_reference,
                suffix="READBACK-CORRUPT-001",
                minute=11,
            )
        )
        ProfileEffectProposalLineage.objects.filter(lineage_id=created.lineage_id).update(
            has_current_survivor=True
        )

        with self.assertRaises(ProfileEffectMalformedReplay):
            self.subject_read_service.read_subject_profile_effect_lineage(
                credential=self.subject.credential,
                viewer_access_epoch=self.subject.access_epoch,
                lineage_id=created.lineage_id,
            )

    def test_readback_rejects_cross_root_transition_and_disposition_corruption(self):
        first = self._create_receipt(suffix="CROSS-ROOT-001")
        second = self._create_receipt(
            suffix="CROSS-ROOT-002",
            activity_id=self.other_activity.activity_id,
            minute=20,
        )
        second_disposition = self.projection_service.authorise_profile_effect_projection(
            self._projection_command(
                lineage_id=second.lineage_id,
                proposal_pk=second.proposal_transition_pk,
                proposal_ref=second.proposal_lineage_reference,
                disposition_pk=None,
                disposition_ref=None,
                suffix="CROSS-ROOT-DISPOSITION-001",
                minute=21,
            )
        )
        second_successor = self.correction_service.supersede_profile_effect_proposal(
            self._correction_command(
                lineage_id=second.lineage_id,
                head_pk=second.proposal_transition_pk,
                head_ref=second.proposal_lineage_reference,
                suffix="CROSS-ROOT-SUCCESSOR-001",
                minute=22,
            )
        )
        first_root = ProfileEffectProposalLineage.objects.get(lineage_id=first.lineage_id)
        second_root = ProfileEffectProposalLineage.objects.get(lineage_id=second.lineage_id)

        ProfileEffectProposalTransition.objects.filter(
            pk=second_successor.proposal_transition_pk
        ).update(lineage_id=first_root.pk)
        with self.assertRaises(ProfileEffectMalformedReplay):
            self.subject_read_service.read_subject_profile_effect_lineage(
                credential=self.subject.credential,
                viewer_access_epoch=self.subject.access_epoch,
                lineage_id=first.lineage_id,
            )

        ProfileEffectProposalTransition.objects.filter(
            pk=second_successor.proposal_transition_pk
        ).update(lineage_id=second_root.pk)
        ProfileEffectProjectionDisposition.objects.filter(
            pk=second_disposition.projection_disposition_pk
        ).update(proposal_transition_id=first_root.head_proposal_transition_id)
        with self.assertRaises(ProfileEffectMalformedReplay):
            self.subject_read_service.read_subject_profile_effect_lineage(
                credential=self.subject.credential,
                viewer_access_epoch=self.subject.access_epoch,
                lineage_id=first.lineage_id,
            )

    def test_readback_rejects_disposition_linked_to_non_current_proposal(self):
        created = self._create_receipt(suffix="NON-CURRENT-DISPOSITION-001")
        initial_disposition = self.projection_service.authorise_profile_effect_projection(
            self._projection_command(
                lineage_id=created.lineage_id,
                proposal_pk=created.proposal_transition_pk,
                proposal_ref=created.proposal_lineage_reference,
                disposition_pk=None,
                disposition_ref=None,
                suffix="NON-CURRENT-DISPOSITION-001",
                minute=11,
            )
        )
        successor = self.correction_service.supersede_profile_effect_proposal(
            self._correction_command(
                lineage_id=created.lineage_id,
                head_pk=created.proposal_transition_pk,
                head_ref=created.proposal_lineage_reference,
                suffix="NON-CURRENT-DISPOSITION-002",
                minute=12,
            )
        )
        successor_disposition = self.projection_service.authorise_profile_effect_projection(
            self._projection_command(
                lineage_id=created.lineage_id,
                proposal_pk=successor.proposal_transition_pk,
                proposal_ref=successor.proposal_lineage_reference,
                disposition_pk=None,
                disposition_ref=None,
                suffix="NON-CURRENT-DISPOSITION-003",
                minute=13,
            )
        )
        successor_withdrawal = self.projection_service.withdraw_profile_effect_projection(
            self._projection_command(
                lineage_id=created.lineage_id,
                proposal_pk=successor.proposal_transition_pk,
                proposal_ref=successor.proposal_lineage_reference,
                disposition_pk=successor_disposition.projection_disposition_pk,
                disposition_ref=successor_disposition.projection_lineage_reference,
                suffix="NON-CURRENT-DISPOSITION-004",
                minute=14,
            )
        )

        ProfileEffectProjectionDisposition.objects.filter(
            pk=successor_withdrawal.projection_disposition_pk
        ).update(
            proposal_transition_id=created.proposal_transition_pk,
            sequence=2,
            previous_disposition_id=initial_disposition.projection_disposition_pk,
        )

        with self.assertRaises(ProfileEffectMalformedReplay):
            self.subject_read_service.read_subject_profile_effect_lineage(
                credential=self.subject.credential,
                viewer_access_epoch=self.subject.access_epoch,
                lineage_id=created.lineage_id,
            )


@POSTGRESQL_ONLY
class ProfileEffectPostgreSQLMigrationRehearsalTests(TransactionTestCase):
    migrate_from = ("core", "0016_s012_service_activity_orchestration")
    migrate_to = ("core", "0017_s013_profile_effect")

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.received_leaf_targets = tuple(executor.loader.graph.leaf_nodes())
        apps = self.migrate(self.migrate_from)
        User = apps.get_model("auth", "User")
        Identity = apps.get_model("core", "Identity")
        Service = apps.get_model("core", "Service")
        ServiceVersion = apps.get_model("core", "ServiceVersion")
        ServiceActivity = apps.get_model("core", "ServiceActivity")
        ServiceActivityTransition = apps.get_model("core", "ServiceActivityTransition")
        ServiceActivityEvidenceReference = apps.get_model("core", "ServiceActivityEvidenceReference")

        occurred_at = timezone.now()
        credential = User.objects.create(username="s013-pg-migration", password="unused")
        identity = Identity.objects.create(credential=credential, access_state="active")
        service = Service.objects.create(
            service_id="migration:s013:service",
            state="published",
            created_by=identity,
            created_at=occurred_at,
        )
        version = ServiceVersion.objects.create(
            service=service,
            version_number=1,
            capability_purpose="Migration rehearsal",
            domain_intent="Migration rehearsal",
            created_by=identity,
            created_at=occurred_at,
        )
        service.current_version = version
        service.save()
        activity = ServiceActivity.objects.create(
            activity_id="11111111-1111-4111-8111-111111111111",
            service_version=version,
            initiating_domain="service",
            initiating_domain_reference="MIGRATE-S013-001",
            state="unassigned",
            created_by=identity,
            created_at=occurred_at,
        )
        transition = ServiceActivityTransition.objects.create(
            activity=activity,
            sequence=1,
            previous_transition=None,
            action="CREATE",
            from_state=None,
            to_state="unassigned",
            actor=identity,
            actor_access_epoch=0,
            authority_reference="AUTH-S012-MIGRATION",
            authority_decision_reference="s012d1:" + "a" * 64,
            authority_evaluated_at=occurred_at,
            request_reference="REQ-S012-MIGRATION",
            idempotency_key="IDEM-S012-MIGRATION",
            payload_fingerprint="b" * 64,
            occurred_at=occurred_at,
            lineage_reference="s012l1:" + "c" * 64,
        )
        activity.head_transition = transition
        activity.save()
        ServiceActivityEvidenceReference.objects.create(
            transition=transition,
            evidence_kind="activity_basis",
            reference="EVIDENCE-S012-MIGRATION",
            supplied_by=identity,
            authority_reference="AUTH-S012-EVIDENCE",
            occurred_at=occurred_at,
        )
        self.activity_pk = activity.pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.received_leaf_targets)
        super().tearDown()

    def test_forward_reverse_reapply_preserves_preexisting_s012_rows(self):
        apps = self.migrate(self.migrate_to)
        ServiceActivity = apps.get_model("core", "ServiceActivity")
        self.assertTrue(ServiceActivity.objects.filter(pk=self.activity_pk).exists())
        self.assertIn("core_profileeffectproposallineage", connection.introspection.table_names())

        apps = self.migrate(self.migrate_from)
        ServiceActivity = apps.get_model("core", "ServiceActivity")
        self.assertTrue(ServiceActivity.objects.filter(pk=self.activity_pk).exists())
        self.assertNotIn("core_profileeffectproposallineage", connection.introspection.table_names())

        apps = self.migrate(self.migrate_to)
        ServiceActivity = apps.get_model("core", "ServiceActivity")
        self.assertTrue(ServiceActivity.objects.filter(pk=self.activity_pk).exists())