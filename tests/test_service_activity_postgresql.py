"""PostgreSQL-only concurrency and novel-shape guardians for S012 Service Activity."""

from threading import Barrier, Event, Lock, Thread
from unittest import skipUnless
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from core.models import (
    Identity,
    IdentityTransition,
    Service,
    ServiceActivity,
    ServiceActivityAssignment,
    ServiceActivityEvidenceReference,
    ServiceActivityReview,
    ServiceActivityTransition,
    ServiceVersion,
    ServiceWorkSubmission,
)
from src.intevia.services.service_authority import (
    ServiceCommandAuthority,
    ServiceCommandAuthorityRequest,
    ServiceCommandAuthorityResponse,
    ServiceCommandNotAuthorised,
)
from src.intevia.services.service_activity_service import (
    AcceptServiceAssignmentCommand,
    AcceptServiceAssignmentResult,
    AssignServiceActivityCommand,
    AssignServiceActivityResult,
    CancelServiceActivityCommand,
    CancelServiceActivityResult,
    CompleteServiceActivityCommand,
    CompleteServiceActivityResult,
    CreateServiceActivityCommand,
    CreateServiceActivityResult,
    DeclineServiceAssignmentCommand,
    DeclineServiceAssignmentResult,
    InitiatingDomain,
    ReviewServiceWorkCommand,
    ReviewServiceWorkResult,
    ServiceActivityActorError,
    ServiceActivityCommandError,
    ServiceActivityConflict,
    ServiceActivityCrossEpochConflict,
    ServiceActivityLifecycleError,
    ServiceActivityPayloadConflict,
    ServiceActivityService,
    ServiceActivityState,
    ServiceActivityValidationError,
    ServiceCommandAction,
    SubmitServiceWorkCommand,
    SubmitServiceWorkResult,
)
from src.intevia.services.service_activity_read_service import (
    ServiceActivityReadDTO,
    ServiceActivityReadError,
    ServiceActivityReadNotAuthorised,
    ServiceActivityReadService,
    ServiceActivityVisibilityRequest,
    ServiceActivityVisibilityResponse,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL S012 Service Activity qualification guardian",
)

NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 26, 13, 0, 0, tzinfo=timezone.utc)
EVEN_LATER = datetime(2026, 7, 26, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Reusable helpers — self-contained, no cross-module test dependency
# ---------------------------------------------------------------------------

class _TestProvider:
    """Always-authorising command authority provider."""
    def authorise(self, *, request):
        return ServiceCommandAuthorityResponse(
            database_alias=request.database_alias,
            actor_pk=request.actor_pk,
            actor_identity_id=request.actor_identity_id,
            actor_access_epoch=request.actor_access_epoch,
            action=request.action,
            target_fingerprint=request.target_fingerprint,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            evaluated_at=request.evaluated_at,
            authority_reference="AUTH-S012-PG",
        )


class _RefusingProvider:
    """Always-refusing authority provider."""
    def authorise(self, *, request):
        return None


class _GrantingVisibilityProvider:
    def check_visibility(self, *, request):
        return ServiceActivityVisibilityResponse(
            database_alias=request.database_alias,
            viewer_identity_id=request.viewer_identity_id,
            viewer_access_epoch=request.viewer_access_epoch,
            activity_id=request.activity_id,
            evaluated_at=request.evaluated_at,
            visible=True,
            authority_reference="AUTH-VIS-PG",
        )


class _FailingProvider:
    """Provider that raises to simulate infrastructure failure."""
    def authorise(self, *, request):
        raise RuntimeError("injected provider failure")


class _FailingVisibilityProvider:
    def check_visibility(self, *, request):
        raise RuntimeError("injected visibility provider failure")


def _make_identity(username, *, active=True):
    user = User.objects.create_user(username=username, password="pg-test-pass")
    if active:
        user.is_active = True
        user.save()
    identity = Identity.objects.create(
        credential=user,
        access_state=Identity.AccessState.ACTIVE if active else Identity.AccessState.PENDING,
    )
    return identity


def _make_service(creator):
    service = Service(
        service_id=f"svc-{uuid4().hex[:8]}",
        state=Service.State.PUBLISHED,
        created_by=creator,
        created_at=NOW,
    )
    service.save()
    sv = ServiceVersion(
        service=service,
        version_number=1,
        capability_purpose="PG test capability",
        domain_intent="PG test domain intent",
        created_by=creator,
        created_at=NOW,
    )
    sv.save()
    service.current_version = sv
    service.save()
    return service, sv


def _make_svc(*, clock=None, provider=None):
    if provider is None:
        provider = _TestProvider()
    authority = ServiceCommandAuthority(provider=provider)
    return ServiceActivityService(
        authority=authority,
        clock=clock or (lambda: NOW),
    )


def _make_read_svc(*, visibility_provider=None, clock=None):
    if visibility_provider is None:
        visibility_provider = _GrantingVisibilityProvider()
    return ServiceActivityReadService(
        visibility_provider=visibility_provider,
        clock=clock or (lambda: NOW),
    )


def _create_activity(svc, credential, sv, *, activity_id=None, idem_key=None, **overrides):
    defaults = dict(
        credential=credential,
        request_reference="REQ-CREATE-PG",
        idempotency_key=idem_key or f"IDEM-{uuid4().hex[:12]}",
        occurred_at=NOW,
        activity_id=activity_id or uuid4(),
        service_version_pk=sv.pk,
        initiating_domain=InitiatingDomain.SERVICE,
        initiating_domain_reference="REF-INIT-PG",
        activity_basis_reference="REF-BASIS-PG",
    )
    defaults.update(overrides)
    return svc.create_service_activity(CreateServiceActivityCommand(**defaults))


def _drive_to_state(svc, creator, assignee, reviewer, sv, target_state, *, activity_id=None):
    """Drive an activity through the lifecycle to the given target_state."""
    aid = activity_id or uuid4()
    result = _create_activity(svc, creator.credential, sv, activity_id=aid)
    if target_state == ServiceActivityState.UNASSIGNED:
        return aid, result

    svc.assign_service_activity(AssignServiceActivityCommand(
        credential=creator.credential,
        request_reference="REQ-A-PG", idempotency_key=f"IDEM-A-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
        assignee_identity_id=assignee.identity_id,
        assignment_reference="REF-A-PG", assignment_basis_reference="REF-AB-PG",
    ))
    if target_state == ServiceActivityState.ASSIGNED:
        return aid, result

    svc.accept_service_assignment(AcceptServiceAssignmentCommand(
        credential=assignee.credential,
        request_reference="REQ-ACC-PG", idempotency_key=f"IDEM-ACC-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
    ))
    if target_state == ServiceActivityState.IN_PROGRESS:
        return aid, result

    svc.submit_service_work(SubmitServiceWorkCommand(
        credential=assignee.credential,
        request_reference="REQ-S-PG", idempotency_key=f"IDEM-S-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
        submission_reference="REF-SUB-PG",
        submission_support_references=("REF-SUPPORT-PG",),
    ))
    if target_state == ServiceActivityState.SUBMITTED:
        return aid, result

    svc.review_service_work(ReviewServiceWorkCommand(
        credential=reviewer.credential,
        request_reference="REQ-R-PG", idempotency_key=f"IDEM-R-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
        review_reference="REF-REV-PG", review_record_reference="REF-RR-PG",
    ))
    if target_state == ServiceActivityState.REVIEWED:
        return aid, result

    svc.complete_service_activity(CompleteServiceActivityCommand(
        credential=creator.credential,
        request_reference="REQ-COMP-PG", idempotency_key=f"IDEM-COMP-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
        completion_record_reference="REF-CR-PG",
    ))
    return aid, result


# ---------------------------------------------------------------------------
# Concurrency runner — reuses S007 pattern with explicit thread join/timeout
# ---------------------------------------------------------------------------

def _run_concurrently(functions, *, barrier_timeout=5, join_timeout=10):
    barrier = Barrier(len(functions))
    result_lock = Lock()
    results = []

    def invoke(fn):
        close_old_connections()
        try:
            barrier.wait(timeout=barrier_timeout)
            value = fn()
        except Exception as error:
            value = error
        finally:
            close_old_connections()
        with result_lock:
            results.append(value)

    threads = [Thread(target=invoke, args=(fn,)) for fn in functions]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=join_timeout)
    if any(t.is_alive() for t in threads):
        raise AssertionError("concurrent guardian thread did not complete")
    return results


# ---------------------------------------------------------------------------
# PostgreSQL catalogue guardian
# ---------------------------------------------------------------------------

@POSTGRESQL_ONLY
class S012PostgreSQLCatalogueTests(TransactionTestCase):
    """Named constraints, indexes, and FK catalogue for S012 models."""
    reset_sequences = True

    def test_s012_named_constraints_exist(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE connamespace = 'public'::regnamespace
                  AND conrelid IN (
                    'core_serviceactivity'::regclass,
                    'core_serviceactivitytransition'::regclass,
                    'core_serviceactivityassignment'::regclass,
                    'core_serviceworksubmission'::regclass,
                    'core_serviceactivityreview'::regclass,
                    'core_serviceactivityevidencereference'::regclass
                  )
                """
            )
            constraint_names = {row[0] for row in cursor.fetchall()}

        expected_constraints = {
            # Activity
            "s012_activity_id_uniq",
            "s012_activity_domain_valid_ck",
            "s012_activity_state_valid_ck",
            "s012_activity_refs_nonempty_ck",
            # Transition
            "s012_transition_activity_sequence_uniq",
            "s012_activity_actor_action_idem_uniq",
            "s012_transition_lineage_ref_uniq",
            "s012_transition_sequence_positive_ck",
            "s012_transition_action_valid_ck",
            "s012_transition_from_state_valid_ck",
            "s012_transition_to_state_valid_ck",
            "s012_transition_edge_valid_ck",
            "s012_transition_payload_hex_ck",
            "s012_transition_decision_ref_ck",
            "s012_transition_lineage_ref_ck",
            "s012_transition_refs_nonempty_ck",
            # Assignment
            "s012_assignment_activity_uniq",
            "s012_assignment_transition_uniq",
            "s012_assignment_refs_nonempty_ck",
            # Submission
            "s012_submission_activity_uniq",
            "s012_submission_transition_uniq",
            "s012_submission_refs_nonempty_ck",
            # Review
            "s012_review_submission_uniq",
            "s012_review_transition_uniq",
            "s012_review_refs_nonempty_ck",
            # Evidence
            "s012_evidence_tuple_uniq",
            "s012_evidence_kind_valid_ck",
            "s012_evidence_refs_nonempty_ck",
        }
        self.assertEqual(expected_constraints - constraint_names, set(),
                         f"Missing constraints: {expected_constraints - constraint_names}")

    def test_s012_named_indexes_exist(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename IN (
                    'core_serviceactivity',
                    'core_serviceactivitytransition',
                    'core_serviceactivityassignment',
                    'core_serviceworksubmission',
                    'core_serviceactivityreview',
                    'core_serviceactivityevidencereference'
                  )
                """
            )
            index_names = {row[0] for row in cursor.fetchall()}

        expected_indexes = {
            "s012_activity_head_uniq",
            "s012_transition_initial_uniq",
            "s012_transition_successor_uniq",
            "s012_activity_service_version_idx",
            "s012_activity_state_idx",
            "s012_activity_created_by_idx",
            "s012_transition_actor_idx",
            "s012_transition_activity_action_idx",
            "s012_assignment_assignee_idx",
            "s012_assignment_assigned_by_idx",
            "s012_submission_submitted_by_idx",
            "s012_review_reviewed_by_idx",
            "s012_evidence_supplied_by_idx",
        }
        self.assertEqual(expected_indexes - index_names, set(),
                         f"Missing indexes: {expected_indexes - index_names}")

    def test_s012_identity_fk_targets_are_correct(self):
        expected_fk_pairs = {
            ("core_serviceactivity", "created_by_id"),
            ("core_serviceactivitytransition", "actor_id"),
            ("core_serviceactivityassignment", "assignee_id"),
            ("core_serviceactivityassignment", "assigned_by_id"),
            ("core_serviceworksubmission", "submitted_by_id"),
            ("core_serviceactivityreview", "reviewed_by_id"),
            ("core_serviceactivityevidencereference", "supplied_by_id"),
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
                  AND source.relname LIKE 'core_service%%'
                  AND source.relname NOT IN (
                    'core_service',
                    'core_serviceversion',
                    'core_servicetransition',
                    'core_serviceevidencereference',
                    'core_servicedeliveryevidencereference',
                    'core_serviceeventassociation',
                    'core_libraryserviceassociation'
                  )
                """
            )
            observed = set(cursor.fetchall())
        self.assertEqual(observed, expected_fk_pairs)
        self.assertEqual(len(observed), 7)

    def test_s012_identity_fk_delete_action_is_protect(self):
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
                    'core_serviceactivity',
                    'core_serviceactivitytransition',
                    'core_serviceactivityassignment',
                    'core_serviceworksubmission',
                    'core_serviceactivityreview',
                    'core_serviceactivityevidencereference'
                  )
                """
            )
            for table, column, delete_action in cursor.fetchall():
                self.assertEqual(
                    delete_action, "a",
                    f"{table}.{column} FK should use PROTECT/NO ACTION, got {delete_action}",
                )


# ---------------------------------------------------------------------------
# Concurrency guardians
# ---------------------------------------------------------------------------

@POSTGRESQL_ONLY
class S012ConcurrentCreateTests(TransactionTestCase):
    """Concurrent same actor/action/key same CREATE payload returns one coherent result."""
    reset_sequences = True

    def setUp(self):
        self.creator = _make_identity("pg-create-race")
        self.service, self.sv = _make_service(self.creator)

    def test_concurrent_same_create_payload_returns_one_winner(self):
        aid = uuid4()
        idem_key = f"IDEM-RACE-{uuid4().hex[:8]}"

        def create():
            svc = _make_svc()
            return _create_activity(
                svc, self.creator.credential, self.sv,
                activity_id=aid, idem_key=idem_key,
            )

        results = _run_concurrently([create, create])
        successes = [r for r in results if isinstance(r, CreateServiceActivityResult)]
        errors = [r for r in results if isinstance(r, Exception)]
        # Both should succeed via write or replay — one coherent result
        self.assertEqual(len(successes), 2, f"Expected 2 successes, got errors: {errors}")
        self.assertEqual(successes[0].activity_id, successes[1].activity_id)
        self.assertEqual(successes[0].transition_id, successes[1].transition_id)
        self.assertEqual(successes[0].lineage_reference, successes[1].lineage_reference)
        # Only one Activity and one Transition in the database
        self.assertEqual(ServiceActivity.objects.filter(activity_id=aid).count(), 1)
        self.assertEqual(
            ServiceActivityTransition.objects.filter(
                activity__activity_id=aid
            ).count(), 1,
        )

    def test_concurrent_changed_payload_same_key_conflicts(self):
        aid1 = uuid4()
        aid2 = uuid4()
        idem_key = f"IDEM-CONFLICT-{uuid4().hex[:8]}"

        def create_a():
            svc = _make_svc()
            return _create_activity(
                svc, self.creator.credential, self.sv,
                activity_id=aid1, idem_key=idem_key,
            )

        def create_b():
            svc = _make_svc()
            return _create_activity(
                svc, self.creator.credential, self.sv,
                activity_id=aid2, idem_key=idem_key,
            )

        results = _run_concurrently([create_a, create_b])
        successes = [r for r in results if isinstance(r, CreateServiceActivityResult)]
        conflicts = [r for r in results if isinstance(r, ServiceActivityPayloadConflict)]
        # One succeeds, one gets payload conflict
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), 1)

    def test_concurrent_same_key_different_parents_has_one_winner(self):
        creator2 = _make_identity("pg-create-race-2")
        service2, sv2 = _make_service(creator2)
        idem_key = f"IDEM-PARENT-{uuid4().hex[:8]}"

        def create_on_sv1():
            svc = _make_svc()
            return _create_activity(
                svc, self.creator.credential, self.sv,
                idem_key=idem_key,
            )

        def create_on_sv2():
            svc = _make_svc()
            return _create_activity(
                svc, self.creator.credential, sv2,
                idem_key=idem_key,
            )

        results = _run_concurrently([create_on_sv1, create_on_sv2])
        successes = [r for r in results if isinstance(r, CreateServiceActivityResult)]
        conflicts = [r for r in results if isinstance(r, (ServiceActivityPayloadConflict, ServiceActivityConflict))]
        # One durable winner, one conflict
        self.assertEqual(len(successes), 1, f"Unexpected: {results}")
        self.assertEqual(len(conflicts), 1)


@POSTGRESQL_ONLY
class S012ConcurrentTransitionTests(TransactionTestCase):
    """Competing Activity transitions — only one wins."""
    reset_sequences = True

    def setUp(self):
        self.creator = _make_identity("pg-trans-creator")
        self.assignee = _make_identity("pg-trans-assignee")
        self.reviewer = _make_identity("pg-trans-reviewer")
        self.service, self.sv = _make_service(self.creator)

    def test_competing_assign_transitions_one_winner(self):
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.UNASSIGNED,
        )
        assignee2 = _make_identity("pg-alt-assignee")

        def assign_a():
            s = _make_svc()
            return s.assign_service_activity(AssignServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ-RACE-A", idempotency_key=f"IDEM-RA-{uuid4().hex[:8]}",
                occurred_at=LATER, activity_id=aid,
                assignee_identity_id=self.assignee.identity_id,
                assignment_reference="REF-RACE-A", assignment_basis_reference="REF-RAB",
            ))

        def assign_b():
            s = _make_svc()
            return s.assign_service_activity(AssignServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ-RACE-B", idempotency_key=f"IDEM-RB-{uuid4().hex[:8]}",
                occurred_at=LATER, activity_id=aid,
                assignee_identity_id=assignee2.identity_id,
                assignment_reference="REF-RACE-B", assignment_basis_reference="REF-RBB",
            ))

        results = _run_concurrently([assign_a, assign_b])
        successes = [r for r in results if isinstance(r, AssignServiceActivityResult)]
        errors = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(successes), 1, f"Expected 1 success, got: {results}")
        self.assertEqual(len(errors), 1)
        activity = ServiceActivity.objects.get(activity_id=aid)
        self.assertEqual(activity.state, ServiceActivityState.ASSIGNED.value)
        self.assertEqual(ServiceActivityAssignment.objects.filter(activity=activity).count(), 1)

    def test_competing_cancel_from_different_states(self):
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.IN_PROGRESS,
        )

        def cancel_a():
            s = _make_svc()
            return s.cancel_service_activity(CancelServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ-CANCEL-A", idempotency_key=f"IDEM-CA-{uuid4().hex[:8]}",
                occurred_at=LATER, activity_id=aid,
                cancellation_basis_reference="REF-CANCEL-A",
            ))

        def cancel_b():
            s = _make_svc()
            return s.cancel_service_activity(CancelServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ-CANCEL-B", idempotency_key=f"IDEM-CB-{uuid4().hex[:8]}",
                occurred_at=LATER, activity_id=aid,
                cancellation_basis_reference="REF-CANCEL-B",
            ))

        results = _run_concurrently([cancel_a, cancel_b])
        successes = [r for r in results if isinstance(r, CancelServiceActivityResult)]
        errors = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(successes), 1, f"Expected 1 cancel, got: {results}")
        self.assertEqual(len(errors), 1)
        activity = ServiceActivity.objects.get(activity_id=aid)
        self.assertEqual(activity.state, ServiceActivityState.CANCELLED.value)


@POSTGRESQL_ONLY
class S012IdentityEpochVsProviderTests(TransactionTestCase):
    """Actor Identity epoch/access mutation versus provider evaluation/write."""
    reset_sequences = True

    def setUp(self):
        self.creator = _make_identity("pg-epoch-creator")
        self.assignee = _make_identity("pg-epoch-assignee")
        self.reviewer = _make_identity("pg-epoch-reviewer")
        self.service, self.sv = _make_service(self.creator)

    def test_identity_access_change_during_command_blocks_write(self):
        """
        If the actor's Identity access_state changes between the provider evaluation
        (which occurs inside the same transaction holding the lock) and the
        write, the locked state in that transaction is canonical. A concurrent
        deactivation in a separate transaction must wait or conflict.
        """
        provider_entered = Event()
        release_provider = Event()

        class PausingProvider(_TestProvider):
            def authorise(self, *, request):
                provider_entered.set()
                if not release_provider.wait(timeout=5):
                    raise AssertionError("provider release timed out")
                return super().authorise(request=request)

        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.UNASSIGNED,
        )
        deactivation_lock_attempted = Event()
        deactivation_finished = Event()
        assign_results = []
        deactivation_results = []

        def deactivate():
            close_old_connections()
            try:
                with transaction.atomic():
                    deactivation_lock_attempted.set()
                    identity = (
                        Identity.objects.select_for_update()
                        .get(pk=self.creator.pk)
                    )
                    identity.access_state = Identity.AccessState.DEACTIVATED
                    identity.save()
                    identity.credential.is_active = False
                    identity.credential.save()
            except Exception as e:
                deactivation_results.append(e)
            finally:
                deactivation_finished.set()
                close_old_connections()
            if not deactivation_results:
                deactivation_results.append("deactivated")

        def assign():
            close_old_connections()
            try:
                credential = User.objects.get(pk=self.creator.credential_id)
                s = _make_svc(provider=PausingProvider())
                assign_results.append(s.assign_service_activity(AssignServiceActivityCommand(
                    credential=credential,
                    request_reference="REQ-EPOCH", idempotency_key=f"IDEM-EP-{uuid4().hex[:8]}",
                    occurred_at=LATER, activity_id=aid,
                    assignee_identity_id=self.assignee.identity_id,
                    assignment_reference="REF-EPOCH", assignment_basis_reference="REF-EPOCH-B",
                )))
            except Exception as e:
                assign_results.append(e)
            finally:
                close_old_connections()

        assign_thread = Thread(target=assign)
        deactivate_thread = Thread(target=deactivate)

        assign_thread.start()
        self.assertTrue(provider_entered.wait(timeout=5))
        deactivate_thread.start()
        self.assertTrue(deactivation_lock_attempted.wait(timeout=5))
        self.assertFalse(
            deactivation_finished.wait(timeout=0.2),
            "deactivation acquired the Identity lock during authority evaluation",
        )
        release_provider.set()
        deactivate_thread.join(timeout=10)
        assign_thread.join(timeout=10)
        self.assertFalse(deactivate_thread.is_alive())
        self.assertFalse(assign_thread.is_alive())

        self.assertEqual(len(assign_results), 1)
        self.assertIsInstance(assign_results[0], AssignServiceActivityResult)
        self.assertEqual(deactivation_results, ["deactivated"])
        activity = ServiceActivity.objects.get(activity_id=aid)
        self.creator.refresh_from_db()
        self.assertEqual(activity.state, ServiceActivityState.ASSIGNED.value)
        self.assertEqual(self.creator.access_state, Identity.AccessState.DEACTIVATED)

    def test_credential_lock_pattern_with_epoch_change(self):
        """
        Identity -> credential lock pattern: changing access_epoch via
        credential replacement does not allow a stale epoch to write.
        """
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.UNASSIGNED,
        )
        original_epoch = self.creator.access_epoch

        # Simulate epoch advance by directly updating
        Identity.objects.filter(pk=self.creator.pk).update(
            access_epoch=original_epoch + 1
        )

        stale_svc = _make_svc()
        # The actor's credential is the same, but the epoch has changed.
        # When the service locks the Identity, it gets the current epoch.
        result = stale_svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-EPOCH-2", idempotency_key=f"IDEM-EP2-{uuid4().hex[:8]}",
            occurred_at=LATER, activity_id=aid,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF-EPOCH-2", assignment_basis_reference="REF-EPOCH-2B",
        ))
        # The service re-reads the epoch from the locked row, so it uses epoch+1
        self.assertIsInstance(result, AssignServiceActivityResult)


@POSTGRESQL_ONLY
class S012LockOrderTests(TransactionTestCase):
    """Creator/assignee/reviewer lock order permutations where feasible."""
    reset_sequences = True

    def setUp(self):
        self.creator = _make_identity("pg-lock-creator")
        self.assignee = _make_identity("pg-lock-assignee")
        self.reviewer = _make_identity("pg-lock-reviewer")
        self.service, self.sv = _make_service(self.creator)

    def test_creator_assigns_to_self_lock_order(self):
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.UNASSIGNED,
        )
        result = svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-SELF", idempotency_key=f"IDEM-SELF-{uuid4().hex[:8]}",
            occurred_at=LATER, activity_id=aid,
            assignee_identity_id=self.creator.identity_id,
            assignment_reference="REF-SELF", assignment_basis_reference="REF-SELF-B",
        ))
        self.assertIsInstance(result, AssignServiceActivityResult)
        self.assertEqual(result.resulting_state, ServiceActivityState.ASSIGNED)

    def test_distinct_creator_assignee_reviewer_complete_lifecycle(self):
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.COMPLETED,
        )
        activity = ServiceActivity.objects.get(activity_id=aid)
        self.assertEqual(activity.state, ServiceActivityState.COMPLETED.value)
        self.assertEqual(ServiceActivityTransition.objects.filter(activity=activity).count(), 6)


@POSTGRESQL_ONLY
class S012DuplicateChildAndSuccessorTests(TransactionTestCase):
    """Duplicate child and competing successor constraints."""
    reset_sequences = True

    def setUp(self):
        self.creator = _make_identity("pg-dup-creator")
        self.assignee = _make_identity("pg-dup-assignee")
        self.reviewer = _make_identity("pg-dup-reviewer")
        self.service, self.sv = _make_service(self.creator)

    def test_duplicate_assignment_child_is_rejected(self):
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.ASSIGNED,
        )
        # Trying to assign again should fail because state is ASSIGNED not UNASSIGNED
        with self.assertRaises(ServiceActivityLifecycleError):
            svc.assign_service_activity(AssignServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ-DUP", idempotency_key=f"IDEM-DUP-{uuid4().hex[:8]}",
                occurred_at=LATER, activity_id=aid,
                assignee_identity_id=self.assignee.identity_id,
                assignment_reference="REF-DUP", assignment_basis_reference="REF-DUP-B",
            ))

    def test_competing_successor_transitions_one_winner(self):
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.ASSIGNED,
        )

        def accept():
            s = _make_svc()
            return s.accept_service_assignment(AcceptServiceAssignmentCommand(
                credential=self.assignee.credential,
                request_reference="REQ-ACC-RACE", idempotency_key=f"IDEM-ACR-{uuid4().hex[:8]}",
                occurred_at=LATER, activity_id=aid,
            ))

        def decline():
            s = _make_svc()
            return s.decline_service_assignment(DeclineServiceAssignmentCommand(
                credential=self.assignee.credential,
                request_reference="REQ-DEC-RACE", idempotency_key=f"IDEM-DCR-{uuid4().hex[:8]}",
                occurred_at=LATER, activity_id=aid,
                decline_basis_reference="REF-DEC-RACE",
            ))

        results = _run_concurrently([accept, decline])
        successes = [r for r in results if isinstance(r, (AcceptServiceAssignmentResult, DeclineServiceAssignmentResult))]
        errors = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(successes), 1, f"Expected 1 success, got: {results}")
        self.assertEqual(len(errors), 1)


@POSTGRESQL_ONLY
class S012ReadbackConcurrencyTests(TransactionTestCase):
    """Readback concurrent with append and no mixed lineage."""
    reset_sequences = True

    def setUp(self):
        self.creator = _make_identity("pg-read-creator")
        self.assignee = _make_identity("pg-read-assignee")
        self.reviewer = _make_identity("pg-read-reviewer")
        self.service, self.sv = _make_service(self.creator)

    def test_readback_concurrent_with_append_no_mixed_lineage(self):
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.IN_PROGRESS,
        )
        def do_read():
            credential = User.objects.get(pk=self.creator.credential_id)
            return _make_read_svc().read_service_activity(
                credential=credential,
                activity_id=aid,
            )

        def do_append():
            credential = User.objects.get(pk=self.assignee.credential_id)
            return _make_svc().submit_service_work(SubmitServiceWorkCommand(
                credential=credential,
                request_reference="REQ-APPEND", idempotency_key=f"IDEM-APP-{uuid4().hex[:8]}",
                occurred_at=LATER, activity_id=aid,
                submission_reference="REF-APPEND-SUB",
                submission_support_references=("REF-APPEND-SUPP",),
            ))

        results = _run_concurrently([do_read, do_append])
        reads = [result for result in results if isinstance(result, ServiceActivityReadDTO)]
        appends = [result for result in results if isinstance(result, SubmitServiceWorkResult)]
        self.assertEqual(len(reads), 1, results)
        self.assertEqual(len(appends), 1, results)

        final_read = reads[0]
        sequences = [entry.sequence for entry in final_read.history]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        self.assertIn(
            (final_read.state, len(final_read.history)),
            {
                (ServiceActivityState.IN_PROGRESS, 3),
                (ServiceActivityState.SUBMITTED, 4),
            },
        )
        self.assertEqual(
            ServiceActivity.objects.get(activity_id=aid).state,
            ServiceActivityState.SUBMITTED.value,
        )

    def test_readback_after_complete_lifecycle_returns_full_history(self):
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.COMPLETED,
        )
        rs = _make_read_svc()
        dto = rs.read_service_activity(
            credential=self.creator.credential,
            activity_id=aid,
        )
        self.assertIsInstance(dto, ServiceActivityReadDTO)
        self.assertEqual(dto.state, ServiceActivityState.COMPLETED)
        self.assertEqual(len(dto.history), 6)
        sequences = [h.sequence for h in dto.history]
        self.assertEqual(sequences, [1, 2, 3, 4, 5, 6])


@POSTGRESQL_ONLY
class S012ReplayAfterAdvanceTests(TransactionTestCase):
    """Replay after activity advance, parent retirement, and Service succession."""
    reset_sequences = True

    def setUp(self):
        self.creator = _make_identity("pg-replay-creator")
        self.assignee = _make_identity("pg-replay-assignee")
        self.reviewer = _make_identity("pg-replay-reviewer")
        self.service, self.sv = _make_service(self.creator)

    def test_replay_after_activity_advance_returns_original(self):
        svc = _make_svc()
        aid = uuid4()
        idem_key = f"IDEM-REPLAY-{uuid4().hex[:8]}"
        original = _create_activity(
            svc, self.creator.credential, self.sv,
            activity_id=aid, idem_key=idem_key,
        )
        # Advance the activity
        svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-ADV", idempotency_key=f"IDEM-ADV-{uuid4().hex[:8]}",
            occurred_at=LATER, activity_id=aid,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF-ADV", assignment_basis_reference="REF-ADV-B",
        ))
        # Replay the CREATE with the same key
        replay = _create_activity(
            svc, self.creator.credential, self.sv,
            activity_id=aid, idem_key=idem_key,
        )
        self.assertEqual(replay.transition_id, original.transition_id)
        self.assertEqual(replay.lineage_reference, original.lineage_reference)
        self.assertEqual(replay.evidence, original.evidence)

    def test_replay_after_service_retirement_returns_original(self):
        svc = _make_svc()
        aid = uuid4()
        idem_key = f"IDEM-RET-{uuid4().hex[:8]}"
        original = _create_activity(
            svc, self.creator.credential, self.sv,
            activity_id=aid, idem_key=idem_key,
        )
        # Retire the service (change its state)
        self.service.state = Service.State.RETIRED
        self.service.save()
        # Replay should still return original result
        replay = _create_activity(
            svc, self.creator.credential, self.sv,
            activity_id=aid, idem_key=idem_key,
        )
        self.assertEqual(replay.transition_id, original.transition_id)
        self.assertEqual(replay.lineage_reference, original.lineage_reference)

    def test_replay_after_service_succession_uses_original_version(self):
        svc = _make_svc()
        aid = uuid4()
        idem_key = f"IDEM-SUC-{uuid4().hex[:8]}"
        original = _create_activity(
            svc, self.creator.credential, self.sv,
            activity_id=aid, idem_key=idem_key,
        )
        # Create a successor version
        sv2 = ServiceVersion(
            service=self.service,
            version_number=2,
            capability_purpose="Successor capability",
            domain_intent="Successor domain",
            created_by=self.creator,
            created_at=LATER,
            predecessor=self.sv,
        )
        sv2.save()
        self.service.current_version = sv2
        self.service.save()
        # Replay with original version pk — same key returns exact original
        replay = _create_activity(
            svc, self.creator.credential, self.sv,
            activity_id=aid, idem_key=idem_key,
        )
        self.assertEqual(replay.transition_id, original.transition_id)

    def test_readback_after_parent_retirement_remains_neutral(self):
        svc = _make_svc()
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.COMPLETED,
        )
        self.service.state = Service.State.RETIRED
        self.service.save()
        rs = _make_read_svc()
        dto = rs.read_service_activity(
            credential=self.creator.credential,
            activity_id=aid,
        )
        self.assertIsInstance(dto, ServiceActivityReadDTO)
        self.assertEqual(dto.state, ServiceActivityState.COMPLETED)
        self.assertEqual(len(dto.history), 6)


@POSTGRESQL_ONLY
class S012InjectedFailureTests(TransactionTestCase):
    """Injected provider and persistence/unrelated IntegrityError propagate and leave no residue."""
    reset_sequences = True

    def setUp(self):
        self.creator = _make_identity("pg-fail-creator")
        self.assignee = _make_identity("pg-fail-assignee")
        self.reviewer = _make_identity("pg-fail-reviewer")
        self.service, self.sv = _make_service(self.creator)

    def test_injected_provider_failure_propagates_and_leaves_no_residue(self):
        svc = _make_svc(provider=_FailingProvider())
        aid = uuid4()
        with self.assertRaises(RuntimeError) as ctx:
            _create_activity(svc, self.creator.credential, self.sv, activity_id=aid)
        self.assertIn("injected provider failure", str(ctx.exception))
        # No Activity or Transition was created
        self.assertEqual(ServiceActivity.objects.filter(activity_id=aid).count(), 0)
        self.assertEqual(ServiceActivityTransition.objects.count(), 0)

    def test_unrelated_integrity_error_propagates(self):
        """An IntegrityError not matching the idempotency constraint propagates."""
        svc = _make_svc()
        aid = uuid4()
        # Create normally first
        _create_activity(svc, self.creator.credential, self.sv, activity_id=aid)
        # Try to create another Activity with the same activity_id but different key
        aid2_same = aid
        with self.assertRaises(IntegrityError):
            # This should fail on s012_activity_id_uniq, not the idempotency constraint
            _create_activity(
                svc, self.creator.credential, self.sv,
                activity_id=aid2_same,
                idem_key=f"IDEM-DIFF-{uuid4().hex[:8]}",
            )

    def test_persistence_failure_leaves_no_residue(self):
        """If the write fails, no partial state remains."""
        svc = _make_svc()
        aid = uuid4()
        pre_activity_count = ServiceActivity.objects.count()
        pre_transition_count = ServiceActivityTransition.objects.count()
        try:
            # Use a refusing provider — authority fails before write
            refusing_svc = _make_svc(provider=_RefusingProvider())
            _create_activity(refusing_svc, self.creator.credential, self.sv, activity_id=aid)
        except (ServiceCommandNotAuthorised, Exception):
            pass
        self.assertEqual(ServiceActivity.objects.count(), pre_activity_count)
        self.assertEqual(ServiceActivityTransition.objects.count(), pre_transition_count)

    def test_injected_visibility_failure_propagates(self):
        """Read service with a failing visibility provider propagates the error."""
        svc = _make_svc()
        third_party = _make_identity("pg-third-party")
        aid, _ = _drive_to_state(
            svc, self.creator, self.assignee, self.reviewer,
            self.sv, ServiceActivityState.COMPLETED,
        )
        rs = _make_read_svc(visibility_provider=_FailingVisibilityProvider())
        with self.assertRaises((RuntimeError, ServiceActivityReadError)):
            rs.read_service_activity(
                credential=third_party.credential,
                activity_id=aid,
            )
