"""PKT-B slice S2 — office appointments and authorised endings. PostgreSQL, disposable test database.

Commission: COPILOT_COMMISSION_PKT_B_S2_v1_1.md with the Human Governor's rulings of 29 September
2026 (IL-7 provenance, IL-8, no reason input, COORDINATOR-only). Test ids R1–R19 map to the
commission's §5 table plus the ruling's additions; R16 is S1's own suite, run unchanged by the
Human Governor — this slice edits no S1 behaviour, only the shared-helper move of §2.

Every fixture label and token is visibly test-only (u21g: tags, TEST_ONLY_* markers). The
setUp/tearDown pair mirrors S1's harness: the landed s015_platform_root_unset_ck is dropped for
fixture latitude and restored with the landed NOT VALID pattern, though this suite founds no root.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from threading import Barrier
from uuid import UUID, uuid4
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import (
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    LOGovernanceConfigurationVersion,
    OfficeAppointmentConsent,
    OrganismCommandReceipt,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from s015_0021_support import (
    T0, append_event, drop_unset_check, empty_fp, found_nonroot, refresh, seed_identity, u,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityMalformed,
    S015AuthorityRefusalCode,
    S015AuthorityUnavailable,
    S015PostFoundingAuthorityRefusal,
    S015PostFoundingAuthorityRequest,
    S015PostFoundingAuthorityResponse,
)
from src.intevia.services.organism_command_support import (
    ReplayResultType,
    ReplayViewer,
    S015CommandReplayMismatch,
    WithheldField,
    _authority_request,
    authority_target_fingerprint,
    serve_replay,
    withheld_fields,
)
from src.intevia.services.organism_office_consent_service import OfficeConsentCommandResult
from src.intevia.services.organism_membership_service import (
    S015OperationNotYetImplemented,
)
from src.intevia.services.organism_role_service import (
    AssignmentCommandResult,
    OrganismRoleService,
    ProposeAssignmentCommand,
    S015AssignmentAuthorityUnavailable,
    S015AssignmentEdgeRefused,
    S015AssignmentMembershipRefused,
    S015AssignmentStateUnused,
    S015AssignmentTermRefused,
    TransitionAssignmentCommand,
    _validate_assignment_edge,
)
from tests.s015_concurrency_support import backend_pid

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
FAR_FUTURE = datetime(2999, 1, 1, tzinfo=timezone.utc)  # later than any real recorded_at, so the four-time check refuses it on every run
POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "role command slice is PostgreSQL-only")


def _event_for_result(result):
    return ContextualRoleAssignmentStateEvent.objects.get(
        assignment__assignment_uuid=result.assignment_uuid,
        lineage_reference=result.lineage_reference,
    )


def _assignment_for_result(result):
    return ContextualRoleAssignment.objects.get(assignment_uuid=result.assignment_uuid)


def _event_id_for_result(result):
    return _event_for_result(result).pk


def _subject_marker(field):
    return WithheldField(field=field, audience="SUBJECT")


class _RollbackHeldTransaction(Exception):
    pass


class _ConcurrencyWorker:
    def __init__(self, *, stop_event, abort_event):
        self.stop_event = stop_event
        self.abort_event = abort_event
        self.ready = threading.Event()
        self.thread = None
        self.pid = None
        self.outcome = None


def _set_local_concurrency_timeouts():
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SET LOCAL statement_timeout = '10s'")


def _assert_no_other_database_sessions(testcase):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND backend_type = 'client backend'"
        )
        remaining = cursor.fetchone()[0]
    testcase.assertEqual(remaining, 0, "concurrency cleanup left a database session connected")


def _register_no_other_sessions_cleanup(testcase):
    testcase.addCleanup(_assert_no_other_database_sessions, testcase)


def _finish_concurrency_workers(testcase, workers, *, stop_events, abort_events):
    for event in stop_events:
        event.set()
    for event in abort_events:
        event.set()

    for worker in workers:
        worker.thread.join(timeout=10)

    live_workers = [worker for worker in workers if worker.thread.is_alive()]
    for worker in live_workers:
        if worker.pid is None:
            continue
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_cancel_backend(%s::int)", [worker.pid])
            cursor.fetchone()

    for worker in live_workers:
        worker.thread.join(timeout=10)

    testcase.assertFalse(
        [worker.thread.name for worker in workers if worker.thread.is_alive()],
        "concurrency worker remained alive after cancellation",
    )
    _assert_no_other_database_sessions(testcase)


def _run_with_external_locks(testcase, acquire_locks, observe, *, holder_release_event=None):
    holder = _ConcurrencyWorker(
        stop_event=holder_release_event if holder_release_event is not None else threading.Event(),
        abort_event=threading.Event(),
    )
    workers = [holder]
    operation_workers = []

    def hold_locks():
        close_old_connections()
        try:
            with transaction.atomic():
                _set_local_concurrency_timeouts()
                holder.pid = backend_pid(connection)
                with connection.cursor() as cursor:
                    acquire_locks(cursor)
                holder.ready.set()
                if not holder.stop_event.wait(timeout=20):
                    raise TimeoutError("external lock holder was not released")
                raise _RollbackHeldTransaction()
        except _RollbackHeldTransaction:
            holder.outcome = ("rolled_back", None)
        except Exception as exc:
            holder.outcome = ("error", exc)
        finally:
            holder.ready.set()
            connection.close()

    holder.thread = threading.Thread(target=hold_locks, daemon=True)
    holder.thread.start()

    def start_worker(operation):
        worker = _ConcurrencyWorker(
            stop_event=threading.Event(),
            abort_event=threading.Event(),
        )
        workers.append(worker)
        operation_workers.append(worker)

        def run():
            close_old_connections()
            try:
                worker.pid = backend_pid(connection)
                worker.ready.set()
                if worker.stop_event.is_set():
                    raise _RollbackHeldTransaction()
                worker.outcome = ("ok", operation())
            except _RollbackHeldTransaction:
                worker.outcome = ("rolled_back", None)
            except Exception as exc:
                worker.outcome = ("error", exc)
            finally:
                worker.ready.set()
                connection.close()

        worker.thread = threading.Thread(target=run, daemon=True)
        worker.thread.start()
        if not worker.ready.wait(timeout=5):
            raise TimeoutError("operation worker did not report its backend")
        return worker

    def run_bounded(operation):
        with transaction.atomic():
            _set_local_concurrency_timeouts()
            try:
                return operation()
            except Exception as exc:
                return exc

    completed = False
    try:
        testcase.assertTrue(holder.ready.wait(timeout=5), "external lock holder did not acquire its locks")
        testcase.assertEqual(holder.outcome, None, "external lock holder failed before acquiring its locks")
        observed = observe(holder.pid, start_worker, run_bounded)
        completed = True
        return observed
    finally:
        _finish_concurrency_workers(
            testcase,
            workers,
            stop_events=[worker.stop_event for worker in workers],
            abort_events=[
                holder.abort_event,
                *([] if completed else [worker.abort_event for worker in operation_workers]),
            ],
        )


def _run_bounded_concurrency_worker(testcase, operation):
    worker = _ConcurrencyWorker(
        stop_event=threading.Event(),
        abort_event=threading.Event(),
    )
    workers = [worker]

    def run():
        close_old_connections()
        try:
            with transaction.atomic():
                _set_local_concurrency_timeouts()
                worker.pid = backend_pid(connection)
                worker.ready.set()
                if worker.stop_event.is_set():
                    raise _RollbackHeldTransaction()
                result = operation()
                if worker.abort_event.is_set():
                    raise _RollbackHeldTransaction()
            worker.outcome = ("ok", result)
        except _RollbackHeldTransaction:
            worker.outcome = ("rolled_back", None)
        except Exception as exc:
            worker.outcome = ("error", exc)
        finally:
            worker.ready.set()
            connection.close()

    worker.thread = threading.Thread(target=run, daemon=True)
    worker.thread.start()
    try:
        testcase.assertTrue(worker.ready.wait(timeout=5), "operation worker did not report its backend")
        worker.thread.join(timeout=12)
    finally:
        _finish_concurrency_workers(
            testcase,
            workers,
            stop_events=[worker.stop_event],
            abort_events=[worker.abort_event],
        )
    return worker.outcome


class S015HistoricalAuthorityReferenceReplayTests(SimpleTestCase):
    def test_services_and_tests_do_not_expose_lo_internal_token(self):
        root = Path(__file__).resolve().parents[1]
        forbidden_token = "LO_" + "INTERNAL"
        checked_directories = (
            root / "src" / "intevia" / "services",
            root / "tests",
        )
        offenders = [
            path.relative_to(root).as_posix()
            for directory in checked_directories
            for path in sorted(directory.rglob("*.py"))
            if forbidden_token in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])

    def test_pk_valued_history_is_withheld_and_uuid_history_is_unchanged(self):
        base = {
            "assignment_uuid": uuid4(),
            "proposal_event_uuid": uuid4(),
            "consent_uuid": uuid4(),
            "action": "CONSENT",
            "ordinal": 1,
            "recorded_at": T0,
            "lineage_reference": "lineage:historical-reference",
            "authority_decision_reference": "decision:historical-reference",
        }
        viewer = ReplayViewer(identity_pk=1, identity_id=uuid4(), now=T0)
        result_type = ReplayResultType(OfficeConsentCommandResult, "default")

        legacy_reference = 's015a1:{"a":17,"e":"application:3"}'
        legacy = OfficeConsentCommandResult(**base, authority_reference=legacy_reference)
        served_legacy = serve_replay(legacy, viewer=viewer, result_type=result_type)
        self.assertEqual(served_legacy.authority_reference, _subject_marker("authority_reference"))

        uuid_reference = (
            's015a1:{"a":"550e8400-e29b-41d4-a716-446655440000",'
            '"e":"550e8400-e29b-41d4-a716-446655440000:3"}'
        )
        current = OfficeConsentCommandResult(**base, authority_reference=uuid_reference)
        served_current = serve_replay(current, viewer=viewer, result_type=result_type)
        self.assertEqual(served_current.authority_reference, uuid_reference)


class _ExactProvider:
    def evaluate_organism_authority(self, request: S015PostFoundingAuthorityRequest):
        if request.action is OrganismAction.RECORD_OFFICE_CONSENT:
            assignment = ContextualRoleAssignment.objects.get(
                assignment_uuid=request.target["assignment_uuid"],
            )
            proposal = ContextualRoleAssignmentStateEvent.objects.get(
                event_uuid=request.target["proposal_event_uuid"],
            )
            facts = {
                "a": str(assignment.assignment_uuid),
                "g": str(assignment.role_definition.role_uuid),
                "k": request.target["event_action"],
                "p": str(proposal.event_uuid),
                "t": "NOMINEE_OWN_ACT",
                "u": str(assignment.membership.membership_uuid),
            }
        elif (
            request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE
            and request.target.get("event_action") == "ASSIGN"
        ):
            assignment = ContextualRoleAssignment.objects.get(
                assignment_uuid=request.target["assignment_uuid"],
            )
            proposal = ContextualRoleAssignmentStateEvent.objects.get(
                assignment=assignment,
                sequence=1,
                action="PROPOSE",
            )
            consent = OfficeAppointmentConsent.objects.filter(
                assignment=assignment,
                action="CONSENT",
            ).order_by("-ordinal").first()
            consent_reference = (
                "e:" + str(proposal.event_uuid)
                if proposal.actor_id == assignment.membership.identity_id
                else "c:" + str(consent.consent_uuid)
            )
            facts = {
                "a": str(assignment.assignment_uuid),
                "g": str(assignment.role_definition.role_uuid),
                "k": "ASSIGN",
                "p": str(proposal.event_uuid),
                "u": consent_reference,
            }
        else:
            return S015PostFoundingAuthorityResponse(
                **asdict(request),
                authority_reference="authority:fixture",
            )
        from src.intevia.services.organism_authority import canonical_json_bytes

        return S015PostFoundingAuthorityResponse(
            **asdict(request),
            authority_reference="s015a1:" + canonical_json_bytes(facts).decode("utf-8"),
        )


class _CountingProvider:
    def __init__(self, provider):
        self.provider = provider
        self.requests = []

    def evaluate_organism_authority(self, request):
        self.requests.append(request)
        return self.provider.evaluate_organism_authority(request)


class _DeniedProvider:
    def evaluate_organism_authority(self, request: S015PostFoundingAuthorityRequest):
        return S015PostFoundingAuthorityRefusal(
            **asdict(request),
            refusal_code=S015AuthorityRefusalCode.DENIED,
        )


class _UnavailableProvider:
    def evaluate_organism_authority(self, request: S015PostFoundingAuthorityRequest):
        raise RuntimeError("offline")


class _MalformedProvider:
    def evaluate_organism_authority(self, request: S015PostFoundingAuthorityRequest):
        payload = asdict(request)
        payload["request_reference"] = "request:wrong"
        return S015PostFoundingAuthorityResponse(
            **payload,
            authority_reference="authority:fixture",
        )


def _add_member(cur, ids, tag, state="ACTIVE"):
    """A second member of the LO with its own identity and a sequence-1 membership chain.
    Test-only fixture; the landed admission routes are S3's."""
    member_pk = seed_identity(cur, tag)
    m_uuid = u(f"membership:{tag}")
    cur.execute(
        "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
        "VALUES (%s, %s, now(), %s, %s, %s, %s, %s) RETURNING id",
        (m_uuid, state, T0, T0, empty_fp(cur, "core_organismmembership", m_uuid), member_pk, ids["lo"]),
    )
    membership_id = cur.fetchone()[0]
    append_event(
        cur, "core_organismmembershiptransition", membership_id, 1, "JOIN", None, state,
        member_pk, ids["basis"], tag, extra={"reason_class": "TEST_ONLY_ADMISSION"},
    )
    refresh(cur, "core_organismmembership", membership_id)
    return membership_id, member_pk, UUID(m_uuid)


def _add_role_definition(cur, ids, tag, code, scope="LIVING_ORGANISM"):
    """An active role definition on the LO. Test-only fixture; definition governance is not S2's."""
    r_uuid = u(f"role:{tag}:{code}")
    cur.execute(
        "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) "
        "VALUES (%s, %s, %s, 1, true, now(), %s) RETURNING id",
        (r_uuid, code, scope, ids["lo"]),
    )
    return cur.fetchone()[0], UUID(r_uuid)


def _assignment_at_state(test_case, ids, membership_id, role_definition_id, tag):
    """A test office built through governed proposal, consent, and LO assignment records."""
    assignment_uuid = UUID(u(f"assignment:{tag}"))
    actor = Identity.objects.get(pk=ids["identity"])
    actor_membership = OrganismMembership.objects.select_related("head_transition").get(pk=ids["membership"])
    nominee = OrganismMembership.objects.get(pk=membership_id)
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO core_contextualroleassignment (assignment_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, membership_id, role_definition_id) "
                "VALUES (%s, 'PROPOSED', now(), %s, %s, %s, %s, %s) RETURNING id",
                (str(assignment_uuid), T0, T0, empty_fp(cur, "core_contextualroleassignment", str(assignment_uuid)), membership_id, role_definition_id),
            )
            assignment_id = cur.fetchone()[0]
            cur.execute(
                "SELECT lo.organism_id, r.role_uuid FROM core_livingorganism lo "
                "JOIN core_organismmembership m ON m.living_organism_id = lo.id "
                "JOIN core_organismroledefinition r ON r.id = %s WHERE m.id = %s",
                (role_definition_id, membership_id),
            )
            organism_uuid, role_uuid = cur.fetchone()
            propose_target = {
                "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
                "event_action": "PROPOSE",
                "living_organism_id": str(organism_uuid),
                "membership_uuid": str(nominee.membership_uuid),
                "resulting_state": "PROPOSED",
                "role_definition_uuid": str(role_uuid),
                "schema": "intevia.s015.organism-role-target.v1",
            }
            propose_decision = S015Authority(
                provider=_ExactProvider(), database_alias="default",
            ).qualify(_authority_request(
                database_alias="default",
                actor=actor,
                actor_membership=actor_membership,
                actor_role_head_refs=(),
                action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
                request_reference=f"request:{tag}:propose",
                idempotency_key=f"idem:{tag}:propose",
                received_at=T0,
                target_fingerprint=authority_target_fingerprint(propose_target),
                effective_at=T0,
                target=propose_target,
                living_organism_id=UUID(str(organism_uuid)),
            ))
            proposal_id, _ = append_event(
                cur,
                "core_contextualroleassignmentstateevent",
                assignment_id,
                1,
                "PROPOSE",
                None,
                "PROPOSED",
                actor.pk,
                ids["basis"],
                tag,
                overrides={
                    "authority_decision_reference": propose_decision.authority_decision_reference,
                    "evidence_reference": propose_decision.authority_reference,
                },
            )
            refresh(cur, "core_contextualroleassignment", assignment_id)

    test_case._record_test_consent(
        provider=_ExactProvider(),
        actor=Identity.objects.get(pk=nominee.identity_id),
        assignment_uuid=assignment_uuid,
        label=tag,
        effective_at=T0,
    )

    with transaction.atomic():
        with connection.cursor() as cur:
            assignment_target = {
                "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
                "assignment_uuid": str(assignment_uuid),
                "event_action": "ASSIGN",
                "living_organism_id": str(organism_uuid),
                "resulting_state": "ACTIVE",
                "schema": "intevia.s015.organism-role-target.v1",
            }
            assign_decision = S015Authority(
                provider=_ExactProvider(), database_alias="default",
            ).qualify(_authority_request(
                database_alias="default",
                actor=actor,
                actor_membership=actor_membership,
                actor_role_head_refs=(),
                action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
                request_reference=f"request:{tag}:assign",
                idempotency_key=f"idem:{tag}:assign",
                received_at=T0,
                target_fingerprint=authority_target_fingerprint(assignment_target),
                effective_at=T0,
                target=assignment_target,
                living_organism_id=UUID(str(organism_uuid)),
                assignment_uuid=assignment_uuid,
            ))
            cur.execute(
                "SELECT id FROM core_contextualroleassignmentstateevent "
                "WHERE assignment_id = %s AND sequence = 1",
                [assignment_id],
            )
            proposal_id = cur.fetchone()[0]
            append_event(
                cur,
                "core_contextualroleassignmentstateevent",
                assignment_id,
                2,
                "ASSIGN",
                "PROPOSED",
                "ACTIVE",
                actor.pk,
                ids["basis"],
                tag,
                pred_id=proposal_id,
                overrides={
                    "actor_state": "LIVING_ORGANISM",
                    "actor_id": None,
                    "actor_access_epoch": None,
                    "actor_capacity": "STANDING",
                    "enterer_identity_id": actor.pk,
                    "authority_decision_reference": assign_decision.authority_decision_reference,
                    "evidence_reference": assign_decision.authority_reference,
                },
            )
            refresh(cur, "core_contextualroleassignment", assignment_id)
    return assignment_id, assignment_uuid


class S015R2InstalledSchemaEvidenceTests(SimpleTestCase):
    databases = {"default"}

    def test_role_actor_nullability_and_capacity_guard_match_0028(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'core_contextualroleassignmentstateevent' "
                "AND column_name IN ('actor_id', 'actor_access_epoch') "
                "ORDER BY column_name"
            )
            nullability = cursor.fetchall()
            cursor.execute(
                "SELECT pg_get_functiondef('public.s015_0022_guard_capacity()'::regprocedure)"
            )
            capacity_body = cursor.fetchone()[0].replace(" ", "")
        self.assertEqual(
            nullability,
            [("actor_access_epoch", "YES"), ("actor_id", "YES")],
        )
        self.assertIn("NEW.actor_stateIN('PARTY','LIVING_ORGANISM')", capacity_body)
        self.assertIn("NEW.actor_capacity<>'STANDING'", capacity_body)
        self.assertIn("NEW.actor_stateNOTIN('PARTY','LIVING_ORGANISM')", capacity_body)
        self.assertIn("NEW.actor_capacity<>'NA_NO_PARTY'", capacity_body)

    def test_capacity_guard_census_covers_every_governed_event_table(self):
        expected_tables = {
            "core_livingorganismevent",
            "core_circlestateevent",
            "core_organismmembershiptransition",
            "core_contextualroleassignmentstateevent",
            "core_membershipconditionstateevent",
            "core_authorityinvalidationevent",
            "core_determinationcontestevent",
            "core_coverageassessment",
            "core_restrictedcontinuityevent",
            "core_visibilitygrantstateevent",
            "core_obligationstateevent",
            "core_planningclassificationevent",
            "core_applicationlineageevent",
        }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.relname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE t.tgfoid = 'public.s015_0022_guard_capacity()'::regprocedure "
                "AND NOT t.tgisinternal ORDER BY c.relname"
            )
            installed_tables = {row[0] for row in cursor.fetchall()}
        self.assertEqual(installed_tables, expected_tables)


@POSTGRESQL_ONLY
class S015RoleCommandTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        _register_no_other_sessions_cleanup(self)
        with connection.cursor() as cur:
            drop_unset_check(cur)

    def tearDown(self):
        with connection.cursor() as cur:
            cur.execute(
                "ALTER TABLE public.core_livingorganism ADD CONSTRAINT s015_platform_root_unset_ck CHECK (NOT platform_root) NOT VALID"
            )
        super().tearDown()

    def _service(self, provider):
        return OrganismRoleService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: NOW,
            database_alias="default",
        )

    def _setup_organism(self, label: str = "role"):
        """A non-root LO: founder member holding the founding COORDINATOR office (IL-7's protected
        shape: the office's first event was written by the LO's founding transaction)."""
        with transaction.atomic():
            with connection.cursor() as cur:
                ids = found_nonroot(cur, label)
        founder = Identity.objects.get(pk=ids["identity"])
        with connection.cursor() as cur:
            cur.execute("SELECT organism_id FROM core_livingorganism WHERE id = %s", (ids["lo"],))
            organism_id = cur.fetchone()[0]
            cur.execute("SELECT membership_uuid FROM core_organismmembership WHERE id = %s", (ids["membership"],))
            founder_membership_uuid = UUID(str(cur.fetchone()[0]))
            cur.execute(
                "SELECT assignment_uuid FROM core_contextualroleassignment WHERE id = %s",
                (ids["assignments"]["COORDINATOR"],),
            )
            founding_assignment_uuid = UUID(str(cur.fetchone()[0]))
            cur.execute("SELECT role_uuid FROM core_organismroledefinition WHERE id = %s", (ids["roles"]["COORDINATOR"],))
            coordinator_role_uuid = UUID(str(cur.fetchone()[0]))
        ids["organism_uuid"] = UUID(str(organism_id))
        ids["founder_membership_uuid"] = founder_membership_uuid
        ids["founding_assignment_uuid"] = founding_assignment_uuid
        ids["coordinator_role_uuid"] = coordinator_role_uuid
        return ids, founder

    def _database_now(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            return cursor.fetchone()[0]

    def _add_active_member(self, ids, label):
        with transaction.atomic():
            with connection.cursor() as cur:
                membership_id, member_pk, member_membership_uuid = _add_member(cur, ids, f"{label}-member")
        member = Identity.objects.get(pk=member_pk)
        return membership_id, member, member_membership_uuid

    def _counts(self):
        return (
            ContextualRoleAssignment.objects.count(),
            ContextualRoleAssignmentStateEvent.objects.count(),
            OrganismCommandReceipt.objects.count(),
        )

    def _assert_lo_recorded_act(self, event, enterer):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, actor_capacity, enterer_identity_id "
                "FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [event.pk],
            )
            actor_state, actor_capacity, enterer_identity_id = cursor.fetchone()
        self.assertIsNone(event.actor_id)
        self.assertIsNone(event.actor_access_epoch)
        self.assertEqual(actor_state, "LIVING_ORGANISM")
        self.assertEqual(actor_capacity, "STANDING")
        self.assertEqual(enterer_identity_id, enterer.pk)

    def _append_lo_close(self, ids, founder, label, effective_at):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, sequence, resulting_state FROM public.core_livingorganismevent "
                "WHERE living_organism_id = %s ORDER BY sequence DESC LIMIT 1",
                [ids["lo"]],
            )
            predecessor_id, sequence, prior_state = cursor.fetchone()
            self.assertEqual(prior_state, "ACTIVE")
            return append_event(
                cursor,
                "core_livingorganismevent",
                ids["lo"],
                sequence + 1,
                "CLOSE",
                "ACTIVE",
                "CLOSED",
                founder.pk,
                ids["basis"],
                label,
                pred_id=predecessor_id,
                eff=effective_at,
                occ=effective_at,
                rec=NOW,
            )

    def _enable_cq(self, ids):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_loapplicationgateway (living_organism_id) VALUES (%s)",
                [ids["lo"]],
            )
            configuration = LOGovernanceConfigurationVersion.objects.create(
                living_organism_id=ids["lo"],
                actor_id=ids["identity"],
                enterer_id=ids["identity"],
                occurred_at=T0,
                effective_at=T0,
                received_at=T0,
                cq_active=True,
                inherit_portal_cq_settings=False,
                require_cq_to_change_lo_settings=False,
                require_cq_to_change_application_question=False,
            )
            cursor.execute(
                "SELECT id, sequence, resulting_state FROM core_livingorganismevent "
                "WHERE living_organism_id = %s ORDER BY sequence DESC LIMIT 1",
                [ids["lo"]],
            )
            predecessor_id, sequence, state = cursor.fetchone()
            append_event(
                cursor,
                "core_livingorganismevent",
                ids["lo"],
                sequence + 1,
                OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION.value,
                state,
                state,
                ids["identity"],
                ids["basis"],
                "cq-configuration",
                pred_id=predecessor_id,
                eff=T0,
                occ=T0,
                rec=T0,
                extra={
                    "actor_state": "LIVING_ORGANISM",
                    "actor_id": None,
                    "actor_access_epoch": None,
                    "actor_capacity": "STANDING",
                    "enterer_identity_id": ids["identity"],
                    "evidence_reference": "governance-configuration-version:" + str(configuration.version_uuid),
                },
            )
            refresh(cursor, "core_livingorganism", ids["lo"], T0)

    def test_founding_coordinator_party_assignment_is_constitutive(self):
        ids, founder = self._setup_organism("founding-party-assignment")
        event = ContextualRoleAssignmentStateEvent.objects.get(
            pk=ids["assignment_events"]["COORDINATOR"]
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, actor_capacity, enterer_identity_id "
                "FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [event.pk],
            )
            actor_state, actor_capacity, enterer_identity_id = cursor.fetchone()
        self.assertEqual(event.action, "ASSIGN")
        self.assertEqual(actor_state, "PARTY")
        self.assertEqual(event.actor_id, founder.pk)
        self.assertEqual(event.actor_access_epoch, founder.access_epoch)
        self.assertEqual(actor_capacity, "STANDING")
        self.assertEqual(enterer_identity_id, founder.pk)

    def test_office_appointment_consent_schema_and_guard_bindings(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'core_officeappointmentconsent'"
            )
            columns = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                "SELECT tgname, p.proname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE NOT t.tgisinternal AND c.relname IN "
                "('core_officeappointmentconsent', 'core_contextualroleassignmentstateevent') "
                "AND (tgname LIKE 's2b_%' OR tgname = 's015_0037_officeappointmentconsent_truncate') "
                "ORDER BY tgname"
            )
            guards = cursor.fetchall()
        self.assertTrue({
            "assignment_id", "proposal_event_id", "nominee_membership_id", "action", "ordinal",
            "actor_id", "actor_state", "actor_capacity", "actor_access_epoch", "enterer_id",
            "authority_decision_reference", "request_reference", "idempotency_key", "occurred_at",
            "effective_at", "received_at", "recorded_at",
        }.issubset(columns))
        self.assertEqual(guards, [
            ("s015_0037_officeappointmentconsent_truncate", "s015_refuse_row_mutation"),
            ("s2b_00_capacity", "s2b_guard_lo_capacity"),
            ("s2b_10_attribution", "s2b_guard_office_attribution"),
            ("s2b_20_assign_consent", "s2b_guard_assign_consent"),
            ("s2b_office_consent_append_only", "s015_refuse_row_mutation"),
            ("s2b_office_consent_insert", "s2b_guard_office_consent"),
        ])

    def test_production_provider_qualifies_coordinator_proposal(self):
        import json
        from core.models import AuthorityDecision
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        ids, founder = self._setup_organism("production-provider-proposal")
        _, nominee, nominee_membership_uuid = self._add_active_member(ids, "production-provider-proposal")
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            at = cursor.fetchone()[0]
        result = self._service(ProductionApplicationAuthorityProvider()).propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=nominee_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:production-provider-proposal",
                idempotency_key="idem:production-provider-proposal",
                occurred_at=at,
                effective_at=at,
                received_at=at,
            )
        )

        decision = AuthorityDecision.objects.get(
            decision_reference=result.authority_decision_reference,
        )
        event = _event_for_result(result)
        self.assertEqual(decision.action, OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value)
        self.assertEqual(decision.target["event_action"], "PROPOSE")
        self.assertEqual(decision.target["living_organism_id"], str(ids["organism_uuid"]))
        self.assertEqual(decision.target["membership_uuid"], str(nominee_membership_uuid))
        policy_facts = json.loads(decision.authority_reference[len("s015a1:"):])
        self.assertEqual(policy_facts["k"], "C")
        self.assertEqual(policy_facts["d"], "C")
        resolved_facts = {(fact["relation"], fact["reference"]) for fact in decision.facts_read}
        self.assertIn(
            ("core_organismroledefinition", str(ids["coordinator_role_uuid"])),
            resolved_facts,
        )
        self.assertIn(("s2b_coordinator_nomination_policy", "C"), resolved_facts)
        self.assertIn(("s2b_coordinator_nomination_policy_default", "C"), resolved_facts)
        self.assertIn(("s2b_coordinator_nomination_entitlement", None), resolved_facts)
        self.assertEqual(event.action, "PROPOSE")
        self.assertEqual(event.authority_decision_reference, decision.decision_reference)

    def test_production_provider_records_nominee_consent_and_replays(self):
        from core.models import AuthorityDecision
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )

        ids, founder = self._setup_organism("production-office-consent")
        _, nominee, nominee_membership_uuid = self._add_active_member(ids, "production-office-consent")
        Identity.objects.filter(pk=founder.pk).update(access_epoch=founder.access_epoch + 1)
        founder.refresh_from_db()
        provider = _CountingProvider(ProductionApplicationAuthorityProvider())
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            at = cursor.fetchone()[0]
        proposal = self._service(provider).propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=nominee_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:production-office-consent:propose",
                idempotency_key="idem:production-office-consent:propose",
                occurred_at=at,
                effective_at=at,
                received_at=at,
            )
        )
        proposal_event = _event_for_result(proposal)
        proposal_decision = AuthorityDecision.objects.get(
            decision_reference=proposal.authority_decision_reference,
        )
        self.assertEqual(proposal_decision.actor_identity_id, founder.identity_id)
        self.assertEqual(proposal_event.actor_access_epoch, founder.access_epoch)
        Identity.objects.filter(pk=nominee.pk).update(access_epoch=nominee.access_epoch + 1)
        nominee.refresh_from_db()
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            consent_at = cursor.fetchone()[0]
        consent_command = RecordOfficeConsentCommand(
            credential=nominee.credential,
            request_reference="request:production-office-consent:consent",
            idempotency_key="idem:production-office-consent:consent",
            occurred_at=consent_at,
            effective_at=consent_at,
            received_at=consent_at,
            assignment_uuid=proposal.assignment_uuid,
            proposal_event_uuid=proposal_event.event_uuid,
            action="CONSENT",
        )
        consent_service = OfficeAppointmentConsentService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: at,
            database_alias="default",
        )
        calls_before = len(provider.requests)
        decisions_before = AuthorityDecision.objects.count()
        receipts_before = OrganismCommandReceipt.objects.count()
        result = consent_service.record_office_consent(consent_command)
        self.assertEqual(len(provider.requests), calls_before + 1)
        self.assertEqual(AuthorityDecision.objects.count(), decisions_before + 1)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts_before + 1)
        replay = consent_service.record_office_consent(consent_command)
        self.assertEqual(len(provider.requests), calls_before + 1)
        self.assertEqual(AuthorityDecision.objects.count(), decisions_before + 1)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts_before + 1)
        consent = OfficeAppointmentConsent.objects.get(consent_uuid=result.consent_uuid)
        decision = AuthorityDecision.objects.get(decision_reference=result.authority_decision_reference)
        self.assertEqual(replay, result)
        self.assertEqual(consent.actor_id, nominee.pk)
        self.assertEqual(consent.enterer_id, nominee.pk)
        self.assertEqual(consent.actor_access_epoch, nominee.access_epoch)
        self.assertEqual(consent.actor_state, "PARTY")
        self.assertEqual(consent.actor_capacity, "STANDING")
        self.assertEqual(consent.ordinal, 1)
        self.assertIsNotNone(consent.recorded_at)
        self.assertEqual(decision.target["event_action"], "CONSENT")
        self.assertEqual(decision.target["assignment_uuid"], str(proposal.assignment_uuid))
        self.assertEqual(decision.target["proposal_event_uuid"], str(proposal_event.event_uuid))
        self.assertEqual(decision.actor_identity_id, nominee.identity_id)
        fact_references = {(fact["relation"], fact["reference"]) for fact in decision.facts_read}
        self.assertTrue({
            ("core_contextualroleassignment", str(proposal.assignment_uuid)),
            ("core_contextualroleassignmentstateevent", str(proposal_event.event_uuid)),
            ("core_organismmembership", str(nominee_membership_uuid)),
            ("core_organismroledefinition", str(ids["coordinator_role_uuid"])),
        }.issubset(fact_references))
        self.assertEqual(
            OrganismCommandReceipt.objects.filter(
                actor=nominee,
                action=OrganismAction.RECORD_OFFICE_CONSENT.value,
                idempotency_key=consent_command.idempotency_key,
            ).count(),
            1,
        )

        def acquire_register_lock(cursor):
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [ids["organism_uuid"]],
            )

        def observe_replay(_holder_pid, _start_worker, run_bounded):
            replay_result = run_bounded(lambda: consent_service.record_office_consent(consent_command))
            self.assertEqual(replay_result, result)
            if os.environ.get("INTEVIA_SELFTEST_FORCE_CONCURRENCY_FAILURE") == "1":
                self.fail("forced concurrency cleanup self-test failure")
            return replay_result

        locked_replay = _run_with_external_locks(self, acquire_register_lock, observe_replay)
        self.assertEqual(locked_replay, result)
        self.assertEqual(len(provider.requests), calls_before + 1)
        self.assertEqual(AuthorityDecision.objects.count(), decisions_before + 1)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts_before + 1)

    def test_replay_preserves_the_subject_view_after_the_coordinators_office_ends(self):
        from core.models import AuthorityDecision
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )

        ids, founder = self._setup_organism("lost-office-replay")
        _, nominee, nominee_membership_uuid = self._add_active_member(ids, "lost-office-replay")
        _, former_member, former_membership_uuid = self._add_active_member(ids, "lost-office-replay-second")
        provider = _CountingProvider(ProductionApplicationAuthorityProvider())
        service = self._service(provider)

        def database_now():
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                return cursor.fetchone()[0]

        proposal_at = database_now()
        proposal = service.propose_assignment(self._propose_command(
            actor=founder,
            membership_uuid=nominee_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:lost-office-replay:propose",
            idempotency_key="idem:lost-office-replay:propose",
            occurred_at=proposal_at,
            effective_at=proposal_at,
            received_at=proposal_at,
        ))
        proposal_event = _event_for_result(proposal)
        consent_service = OfficeAppointmentConsentService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=database_now,
            database_alias="default",
        )
        consent_at = database_now()
        consent_service.record_office_consent(RecordOfficeConsentCommand(
            credential=nominee.credential,
            request_reference="request:lost-office-replay:consent",
            idempotency_key="idem:lost-office-replay:consent",
            occurred_at=consent_at,
            effective_at=proposal_at,
            received_at=consent_at,
            assignment_uuid=proposal.assignment_uuid,
            proposal_event_uuid=proposal_event.event_uuid,
            action="CONSENT",
        ))

        assign_at = database_now()
        assign_command = self._transition_command(
            actor=founder,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            request_reference="request:lost-office-replay:assign",
            idempotency_key="idem:lost-office-replay:assign",
            occurred_at=assign_at,
            effective_at=proposal_at,
            received_at=assign_at,
        )
        fresh_result = service.transition_assignment(assign_command)
        event = _event_for_result(fresh_result)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, actor_capacity, actor_id, enterer_identity_id, request_reference "
                "FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [event.pk],
            )
            actor_state, actor_capacity, actor_id, enterer_id, request_reference = cursor.fetchone()
        self.assertEqual((actor_state, actor_capacity, actor_id, enterer_id),
                         ("LIVING_ORGANISM", "STANDING", None, founder.pk))
        self.assertEqual(request_reference, assign_command.request_reference)
        receipt = OrganismCommandReceipt.objects.get(
            actor=founder,
            action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            idempotency_key=assign_command.idempotency_key,
        )
        self.assertEqual(receipt.request_reference, assign_command.request_reference)
        self.assertEqual(
            AuthorityDecision.objects.get(
                decision_reference=fresh_result.authority_decision_reference,
            ).actor_identity_id,
            founder.identity_id,
        )

        provider_calls = len(provider.requests)
        decisions = AuthorityDecision.objects.count()
        receipts = OrganismCommandReceipt.objects.count()
        replay = service.transition_assignment(assign_command)
        self.assertEqual(replay, fresh_result)
        self.assertEqual(len(provider.requests), provider_calls)
        self.assertEqual(AuthorityDecision.objects.count(), decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts)

        stored_result = AssignmentCommandResult.from_receipt_payload(
            receipt.result_payload,
            database_alias="default",
        )
        holder_view = serve_replay(
            stored_result,
            viewer=ReplayViewer(nominee.pk, nominee.identity_id, database_now()),
            result_type=ReplayResultType(AssignmentCommandResult, "default"),
        )
        current_assignment = ContextualRoleAssignment.objects.get(
            assignment_uuid=proposal.assignment_uuid,
        )
        self.assertEqual(holder_view.resulting_state, current_assignment.state)
        self.assertEqual(holder_view.event_sequence, event.sequence)
        self.assertEqual(holder_view.current_state_effective_at, current_assignment.current_state_effective_at)
        self.assertEqual(holder_view.state_known_at, current_assignment.state_known_at)
        self.assertEqual(
            holder_view.state_source_event_set_fingerprint,
            current_assignment.state_source_event_set_fingerprint,
        )

        end_at = database_now()
        service.transition_assignment(self._transition_command(
            actor=founder,
            assignment_uuid=ids["founding_assignment_uuid"],
            action="END",
            resulting_state="ENDED",
            request_reference="request:lost-office-replay:end",
            idempotency_key="idem:lost-office-replay:end",
            occurred_at=end_at,
            effective_at=end_at,
            received_at=end_at,
        ))

        membership = OrganismMembership.objects.get(
            membership_uuid=former_membership_uuid,
        )
        from src.intevia.services.organism_membership_service import (
            OrganismMembershipService,
            TransitionMembershipCommand,
        )

        membership_service = OrganismMembershipService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=database_now,
            database_alias="default",
        )
        leave_at = database_now()
        membership_service.transition_membership(TransitionMembershipCommand(
            credential=former_member.credential,
            request_reference="request:lost-office-replay:leave",
            idempotency_key="idem:lost-office-replay:leave",
            occurred_at=leave_at,
            effective_at=leave_at,
            received_at=leave_at,
            membership_uuid=membership.membership_uuid,
            resulting_state="ENDED",
            reason_class="TEST_ONLY_REASON_LEAVE",
        ))

        provider_calls = len(provider.requests)
        decisions = AuthorityDecision.objects.count()
        receipts = OrganismCommandReceipt.objects.count()
        replay_after_office_end = service.transition_assignment(assign_command)
        self.assertEqual(replay_after_office_end, fresh_result)
        self.assertEqual(replay_after_office_end, replay)
        expected_markers = withheld_fields(fresh_result)
        self.assertEqual(withheld_fields(replay_after_office_end), expected_markers)
        for marker in expected_markers:
            self.assertTrue(hasattr(replay_after_office_end, marker.field))
            self.assertIsInstance(getattr(replay_after_office_end, marker.field), WithheldField)
            self.assertEqual(getattr(replay_after_office_end, marker.field), marker)
        self.assertEqual(len(provider.requests), provider_calls)
        self.assertEqual(AuthorityDecision.objects.count(), decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts)

        former_member_view = serve_replay(
            stored_result,
            viewer=ReplayViewer(former_member.pk, former_member.identity_id, database_now()),
            result_type=ReplayResultType(AssignmentCommandResult, "default"),
        )
        former_coordinator_view = serve_replay(
            stored_result,
            viewer=ReplayViewer(founder.pk, founder.identity_id, database_now()),
            result_type=ReplayResultType(AssignmentCommandResult, "default"),
        )
        self.assertEqual(
            repr(withheld_fields(former_member_view)).encode("utf-8"),
            repr(withheld_fields(former_coordinator_view)).encode("utf-8"),
        )

    def test_w_r4a_reports_end_and_reassign_lock_scope(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        def collect(action):
            label = f"w-r4a-{action.lower()}"
            ids, founder, _member, _setup_service, _proposal, assigned = self._propose_then_assign(label=label)
            service = self._service(ProductionApplicationAuthorityProvider(database_alias="default"))
            transition_at = self._database_now()
            command = self._transition_command(
                actor=founder,
                assignment_uuid=assigned.assignment_uuid,
                action=action,
                resulting_state="ENDED",
                request_reference=f"request:{label}:transition",
                idempotency_key=f"idem:{label}:transition",
                occurred_at=transition_at,
                effective_at=NOW,
                received_at=transition_at,
            )
            observations = []

            def capture_observations(execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                normalized_sql = sql.casefold()
                if (
                    normalized_sql.lstrip().startswith("select")
                    and "core_contextualroleassignment" in normalized_sql
                    and "core_livingorganism" in normalized_sql
                ):
                    if not observations and "for update" not in normalized_sql:
                        phase = "pre-replay"
                    elif len(observations) == 1 and "for update" in normalized_sql:
                        phase = "post-replay"
                    else:
                        return result
                    main_pid = backend_pid(connection)
                    rendered_sql = connection.ops.last_executed_query(
                        context["cursor"].cursor,
                        sql,
                        params,
                    )

                    def relation_modes():
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SELECT mode, granted FROM pg_locks "
                                "WHERE pid = %s AND locktype = 'relation' "
                                "AND relation = 'public.core_livingorganism'::regclass "
                                "ORDER BY mode, granted",
                                [main_pid],
                            )
                            return tuple(cursor.fetchall())

                    def row_probe():
                        try:
                            with transaction.atomic(), connection.cursor() as cursor:
                                cursor.execute(
                                    "SELECT id FROM public.core_livingorganism "
                                    "WHERE id = %s FOR UPDATE NOWAIT",
                                    [ids["lo"]],
                                )
                                if cursor.fetchone() is None:
                                    raise AssertionError("row probe found no target organism row")
                            return "ACQUIRED", None
                        except DatabaseError as error:
                            driver_error = error.__cause__
                            if driver_error is None:
                                raise AssertionError(
                                    f"row probe raised {type(error).__name__} without an underlying "
                                    f"driver error: {error}"
                                ) from error
                            sqlstate = getattr(driver_error, "sqlstate", None)
                            if sqlstate is None:
                                sqlstate = getattr(driver_error, "pgcode", None)
                            if sqlstate == "55P03":
                                return "LOCK_NOT_AVAILABLE", sqlstate
                            raise AssertionError(
                                f"row probe failed with {type(driver_error).__name__} "
                                f"(SQLSTATE {sqlstate!r}): {driver_error}"
                            ) from error

                    modes = _run_bounded_concurrency_worker(self, relation_modes)
                    probe = _run_bounded_concurrency_worker(self, row_probe)
                    self.assertEqual(modes[0], "ok", repr(modes[1]))
                    self.assertEqual(probe[0], "ok", repr(probe[1]))
                    self.assertIn(
                        probe[1][0],
                        {"ACQUIRED", "LOCK_NOT_AVAILABLE"},
                        repr(probe[1]),
                    )
                    observations.append((phase, sql, rendered_sql, modes[1], probe[1]))
                return result

            with transaction.atomic():
                with CaptureQueriesContext(connection) as captured:
                    with connection.execute_wrapper(capture_observations):
                        service.transition_assignment(command)
                    matching_queries = [
                        query["sql"]
                        for query in captured.captured_queries
                        if query["sql"].lstrip().casefold().startswith("select")
                        and "core_contextualroleassignment" in query["sql"].casefold()
                        and "core_livingorganism" in query["sql"].casefold()
                    ]
                    target_queries = [
                        next(sql for sql in matching_queries if "for update" not in sql.casefold()),
                        next(sql for sql in matching_queries if "for update" in sql.casefold()),
                    ]
                    self.assertEqual(len(target_queries), 2)
                    self.assertEqual(target_queries, [item[2] for item in observations])

            self.assertEqual([item[0] for item in observations], ["pre-replay", "post-replay"])
            for phase, sql, _rendered_sql, modes, probe in observations:
                print(
                    f"W-R4a action={action} read={phase} sql={sql} "
                    f"relation_modes={modes!r} row_probe={probe!r}"
                )

        collect("END")
        collect("REASSIGN")

    def test_closed_organism_replays_for_nonmember_and_refuses_a_fresh_act(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_membership_service import (
            OrganismMembershipService,
            TransitionMembershipCommand,
        )

        ids, founder = self._setup_organism("closed-replay-nonmember")
        _, coordinator, coordinator_membership_uuid = self._add_active_member(
            ids,
            "closed-replay-nonmember-coordinator",
        )
        _, _nominee, nominee_membership_uuid = self._add_active_member(
            ids,
            "closed-replay-nonmember-nominee",
        )
        _, _fresh_nominee, fresh_membership_uuid = self._add_active_member(
            ids,
            "closed-replay-nonmember-fresh-nominee",
        )
        provider = ProductionApplicationAuthorityProvider(database_alias="default")
        service = self._service(provider)
        proposal = service.propose_assignment(self._propose_command(
            actor=founder,
            membership_uuid=coordinator_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:closed-replay-nonmember:propose",
            idempotency_key="idem:closed-replay-nonmember:propose",
            occurred_at=NOW,
            effective_at=NOW,
            received_at=self._database_now(),
        ))
        self._record_test_consent(
            provider=provider,
            actor=coordinator,
            assignment_uuid=proposal.assignment_uuid,
            label="closed-replay-nonmember",
            effective_at=NOW,
        )
        assign_command = self._transition_command(
            actor=founder,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            request_reference="request:closed-replay-nonmember:assign",
            idempotency_key="idem:closed-replay-nonmember:assign",
            effective_until_state="NONE",
            occurred_at=NOW,
            effective_at=NOW,
            received_at=self._database_now(),
        )
        fresh_result = service.transition_assignment(assign_command)
        service.transition_assignment(self._transition_command(
            actor=founder,
            assignment_uuid=ids["founding_assignment_uuid"],
            action="END",
            resulting_state="ENDED",
            request_reference="request:closed-replay-nonmember:end-founder-office",
            idempotency_key="idem:closed-replay-nonmember:end-founder-office",
            occurred_at=NOW,
            effective_at=NOW,
            received_at=self._database_now(),
        ))
        membership_service = OrganismMembershipService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: NOW,
            database_alias="default",
        )
        membership_service.transition_membership(TransitionMembershipCommand(
            credential=founder.credential,
            request_reference="request:closed-replay-nonmember:leave",
            idempotency_key="idem:closed-replay-nonmember:leave",
            occurred_at=NOW,
            effective_at=NOW,
            received_at=self._database_now(),
            membership_uuid=ids["founder_membership_uuid"],
            resulting_state="ENDED",
            reason_class="TEST_ONLY_REASON_LEAVE",
        ))
        self._append_lo_close(ids, founder, "closed-replay-nonmember-close", NOW)
        with transaction.atomic(), connection.cursor() as cursor:
            refresh(cursor, "core_livingorganism", ids["lo"], NOW)

        replay = service.transition_assignment(assign_command)
        self.assertEqual(replay.assignment_uuid, fresh_result.assignment_uuid)
        self.assertEqual(replay.event_action, fresh_result.event_action)
        markers = withheld_fields(replay)
        self.assertTrue(markers)
        for marker in markers:
            self.assertTrue(hasattr(replay, marker.field))
            self.assertIsInstance(getattr(replay, marker.field), WithheldField)
            self.assertEqual(getattr(replay, marker.field), marker)

        with self.assertRaises(S015AuthorityDenied) as closed_refusal:
            service.propose_assignment(self._propose_command(
                actor=coordinator,
                membership_uuid=fresh_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:closed-replay-nonmember:fresh",
                idempotency_key="idem:closed-replay-nonmember:fresh",
                occurred_at=NOW,
                effective_at=NOW,
                received_at=self._database_now(),
            ))
        self.assertEqual(
            closed_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.C_LO_CLOSED,
        )

    def test_w_t2_s_self_departure_serializes_with_active_member_act_both_orders(self):
        from core.models import AuthorityDecision
        from src.intevia.services import organism_office_consent_service as consent_module
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_membership_service import (
            OrganismMembershipService,
            TransitionMembershipCommand,
        )
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
            S015OfficeConsentAuthorityUnavailable,
        )

        def prepare_case(label):
            ids, founder = self._setup_organism(label)
            _, member, membership_uuid = self._add_active_member(ids, label)
            provider = ProductionApplicationAuthorityProvider(database_alias="default")
            proposal_at = self._database_now()
            proposal = self._service(provider).propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=membership_uuid,
                    role_definition_uuid=ids["coordinator_role_uuid"],
                    request_reference=f"request:{label}:propose",
                    idempotency_key=f"idem:{label}:propose",
                    occurred_at=proposal_at,
                    effective_at=proposal_at,
                    received_at=proposal_at,
                )
            )
            proposal_event = _event_for_result(proposal)
            consent_service = OfficeAppointmentConsentService(
                authority=S015Authority(provider=provider, database_alias="default"),
                clock=self._database_now,
                database_alias="default",
            )
            membership_service = OrganismMembershipService(
                authority=S015Authority(provider=provider, database_alias="default"),
                clock=self._database_now,
                database_alias="default",
            )
            return ids, member, membership_uuid, proposal, proposal_event, consent_service, membership_service

        def make_consent(label, member, proposal, proposal_event, *, action):
            at = self._database_now()
            return RecordOfficeConsentCommand(
                credential=member.credential,
                request_reference=f"request:{label}:consent",
                idempotency_key=f"idem:{label}:consent",
                occurred_at=at,
                effective_at=at,
                received_at=at,
                assignment_uuid=proposal.assignment_uuid,
                proposal_event_uuid=proposal_event.event_uuid,
                action=action,
            )

        def make_departure(label, member, membership_uuid):
            at = self._database_now()
            return TransitionMembershipCommand(
                credential=member.credential,
                request_reference=f"request:{label}:leave",
                idempotency_key=f"idem:{label}:leave",
                occurred_at=at,
                effective_at=at,
                received_at=at,
                membership_uuid=membership_uuid,
                resulting_state="ENDED",
                reason_class="TEST_ONLY_REASON_LEAVE",
            )

        (
            ids,
            member,
            membership_uuid,
            proposal,
            proposal_event,
            consent_service,
            membership_service,
        ) = prepare_case("w-t2-s-act-first")
        assignment = ContextualRoleAssignment.objects.get(assignment_uuid=proposal.assignment_uuid)
        self.assertEqual(assignment.state, "PROPOSED")
        consent = make_consent(
            "w-t2-s-act-first",
            member,
            proposal,
            proposal_event,
            action="DECLINE",
        )
        departure = make_departure("w-t2-s-act-first", member, membership_uuid)
        membership_checked = threading.Event()
        release_act = threading.Event()
        abort_departure = threading.Event()
        departure_query_started = threading.Event()
        act_identity_queries = []
        departure_identity_queries = []
        act_handle = _ConcurrencyWorker(stop_event=release_act, abort_event=threading.Event())
        departure_handle = _ConcurrencyWorker(
            stop_event=threading.Event(),
            abort_event=abort_departure,
        )
        workers = [act_handle]
        original_membership_context = consent_module._lock_actor_membership_context

        def hold_after_active_membership_check(*args, **kwargs):
            result = original_membership_context(*args, **kwargs)
            self.assertEqual(result[0].state, "ACTIVE")
            membership_checked.set()
            if not release_act.wait(timeout=15):
                raise TimeoutError("W-T2-S did not release the act transaction")
            return result

        def capture_act_identity_lock(execute, sql, params, many, context):
            normalized_sql = sql.casefold()
            if "core_identity" in normalized_sql and "for update" in normalized_sql:
                act_identity_queries.append((sql, tuple(params or ())))
            return execute(sql, params, many, context)

        def run_act_first():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '15s'")
                    cursor.execute("SET statement_timeout = '20s'")
                act_handle.pid = backend_pid(connection)
                act_handle.ready.set()
                with connection.execute_wrapper(capture_act_identity_lock):
                    with patch.object(
                        consent_module,
                        "_lock_actor_membership_context",
                        side_effect=hold_after_active_membership_check,
                    ):
                        result = consent_service.record_office_consent(consent)
                act_handle.outcome = ("ok", result)
            except Exception as error:
                act_handle.outcome = ("error", error)
            finally:
                act_handle.ready.set()
                connection.close()

        def capture_departure_identity_lock(execute, sql, params, many, context):
            normalized_sql = sql.casefold()
            if "core_identity" in normalized_sql and "for update" in normalized_sql:
                departure_identity_queries.append((sql, tuple(params or ())))
                departure_query_started.set()
            return execute(sql, params, many, context)

        def run_departure():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '15s'")
                    cursor.execute("SET statement_timeout = '20s'")
                departure_handle.pid = backend_pid(connection)
                departure_handle.ready.set()
                if abort_departure.is_set():
                    raise _RollbackHeldTransaction()
                with connection.execute_wrapper(capture_departure_identity_lock):
                    result = membership_service.transition_membership(departure)
                departure_handle.outcome = ("ok", result)
            except _RollbackHeldTransaction:
                departure_handle.outcome = ("aborted", None)
            except Exception as error:
                departure_handle.outcome = ("error", error)
            finally:
                departure_handle.ready.set()
                connection.close()

        act_handle.thread = threading.Thread(target=run_act_first, daemon=True)
        wait_evidence = None
        try:
            act_handle.thread.start()
            self.assertTrue(act_handle.ready.wait(timeout=5), "act worker did not report its backend")
            self.assertTrue(
                membership_checked.wait(timeout=10),
                f"act did not reach its active-membership check: {act_handle.outcome!r}",
            )
            self.assertTrue(act_identity_queries, "act did not execute its core_identity FOR UPDATE")

            departure_handle.thread = threading.Thread(target=run_departure, daemon=True)
            workers.append(departure_handle)
            departure_handle.thread.start()
            self.assertTrue(
                departure_handle.ready.wait(timeout=5),
                "self-departure worker did not report its backend",
            )
            self.assertTrue(
                departure_query_started.wait(timeout=5),
                f"self-departure did not reach its core_identity FOR UPDATE: {departure_handle.outcome!r}",
            )

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT wait_event_type, wait_event, query, pg_blocking_pids(pid) "
                        "FROM pg_stat_activity WHERE pid = %s",
                        [departure_handle.pid],
                    )
                    wait_evidence = cursor.fetchone()
                if (
                    wait_evidence is not None
                    and wait_evidence[0] == "Lock"
                    and act_handle.pid in wait_evidence[3]
                ):
                    break
                time.sleep(0.01)

            self.assertIsNotNone(wait_evidence, "waiting self-departure disappeared from pg_stat_activity")
            self.assertEqual(wait_evidence[0], "Lock")
            self.assertIn(act_handle.pid, wait_evidence[3])
            self.assertIn("core_identity", wait_evidence[2].casefold())
            self.assertIn("for update", wait_evidence[2].casefold())
            self.assertEqual(
                act_identity_queries[0][1],
                departure_identity_queries[0][1],
                "act and departure did not lock the same actor identity",
            )
            self.assertTrue(
                act_handle.thread.is_alive(),
                "act transaction ended before the waiting departure was observed",
            )
            print(
                "W-T2-S order=ACT_FIRST "
                f"act_identity_lock_sql={act_identity_queries[0][0]} "
                f"waiting_statement={wait_evidence[2]} "
                f"wait_event={wait_evidence[0]}/{wait_evidence[1]} "
                f"blocking_pids={wait_evidence[3]!r}"
            )

            release_act.set()
            act_handle.thread.join(timeout=30)
            departure_handle.thread.join(timeout=30)
            self.assertFalse(act_handle.thread.is_alive(), "act worker did not finish")
            self.assertFalse(departure_handle.thread.is_alive(), "self-departure did not finish after act commit")
            self.assertEqual(act_handle.outcome[0], "ok", repr(act_handle.outcome[1]))
            self.assertEqual(departure_handle.outcome[0], "ok", repr(departure_handle.outcome[1]))
            self.assertEqual(act_handle.outcome[1].action, "DECLINE")
            self.assertEqual(departure_handle.outcome[1].resulting_state, "ENDED")
            consent_row = OfficeAppointmentConsent.objects.get(
                consent_uuid=act_handle.outcome[1].consent_uuid,
            )
            self.assertEqual(consent_row.action, "DECLINE")
            assignment.refresh_from_db()
            self.assertEqual(assignment.state, "PROPOSED")
            member_membership = OrganismMembership.objects.get(membership_uuid=membership_uuid)
            self.assertEqual(member_membership.state, "ENDED")
        finally:
            release_act.set()
            abort_departure.set()
            _finish_concurrency_workers(
                self,
                workers,
                stop_events=[release_act],
                abort_events=[abort_departure],
            )

        (
            ids,
            member,
            membership_uuid,
            proposal,
            proposal_event,
            consent_service,
            membership_service,
        ) = prepare_case("w-t2-s-departure-first")
        assignment = ContextualRoleAssignment.objects.get(assignment_uuid=proposal.assignment_uuid)
        self.assertEqual(assignment.state, "PROPOSED")
        departure = make_departure("w-t2-s-departure-first", member, membership_uuid)
        departure_result = membership_service.transition_membership(departure)
        self.assertEqual(departure_result.resulting_state, "ENDED")
        member_membership = OrganismMembership.objects.get(membership_uuid=membership_uuid)
        self.assertEqual(member_membership.state, "ENDED")

        consent = make_consent(
            "w-t2-s-departure-first",
            member,
            proposal,
            proposal_event,
            action="DECLINE",
        )
        consent_count = OfficeAppointmentConsent.objects.filter(assignment=assignment).count()
        decision_count = AuthorityDecision.objects.count()
        receipt_count = OrganismCommandReceipt.objects.count()
        with self.assertRaises(S015OfficeConsentAuthorityUnavailable) as refused:
            consent_service.record_office_consent(consent)
        self.assertEqual(str(refused.exception), "actor active membership is required")
        self.assertEqual(OfficeAppointmentConsent.objects.filter(assignment=assignment).count(), consent_count)
        self.assertEqual(AuthorityDecision.objects.count(), decision_count)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipt_count)
        print(
            "W-T2-S order=DEPARTURE_FIRST "
            f"departure={departure_result.resulting_state} "
            f"act_refusal={type(refused.exception).__name__} "
            f"consent_rows={consent_count} decisions_unchanged=True receipts_unchanged=True"
        )

    def test_nonmember_proposal_refuses_without_waiting_on_register_lock(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_membership_service import (
            OrganismMembershipService,
            TransitionMembershipCommand,
        )

        ids, founder = self._setup_organism("nonmember-proposal-register-lock")
        _, nonmember, nonmember_membership_uuid = self._add_active_member(
            ids,
            "nonmember-proposal-register-lock-leaver",
        )
        _, nominee, nominee_membership_uuid = self._add_active_member(
            ids,
            "nonmember-proposal-register-lock-nominee",
        )
        provider = _CountingProvider(ProductionApplicationAuthorityProvider(database_alias="default"))
        membership_service = OrganismMembershipService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: NOW,
            database_alias="default",
        )
        membership_service.transition_membership(TransitionMembershipCommand(
            credential=nonmember.credential,
            request_reference="request:nonmember-proposal-register-lock:leave",
            idempotency_key="idem:nonmember-proposal-register-lock:leave",
            occurred_at=NOW,
            effective_at=NOW,
            received_at=self._database_now(),
            membership_uuid=nonmember_membership_uuid,
            resulting_state="ENDED",
            reason_class="TEST_ONLY_REASON_LEAVE",
        ))
        role_service = self._service(provider)
        command = self._propose_command(
            actor=nonmember,
            membership_uuid=nominee_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:nonmember-proposal-register-lock:propose",
            idempotency_key="idem:nonmember-proposal-register-lock:propose",
            occurred_at=NOW,
            effective_at=NOW,
            received_at=self._database_now(),
        )

        def acquire_register_lock(cursor):
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [ids["organism_uuid"]],
            )

        refusal = _run_with_external_locks(
            self,
            acquire_register_lock,
            lambda _holder_pid, _start_worker, run_bounded: run_bounded(
                lambda: role_service.propose_assignment(command),
            ),
        )
        self.assertIsInstance(refusal, S015AssignmentAuthorityUnavailable)
        self.assertEqual(str(refusal), "actor active membership is required")

    def test_nonmember_assignment_refuses_without_waiting_on_register_lock(self):
        from core.models import AuthorityDecision
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_membership_service import (
            OrganismMembershipService,
            TransitionMembershipCommand,
        )

        ids, founder = self._setup_organism("nonmember-assignment-register-lock")
        _, nominee, nominee_membership_uuid = self._add_active_member(
            ids,
            "nonmember-assignment-register-lock-nominee",
        )
        _, nonmember, nonmember_membership_uuid = self._add_active_member(
            ids,
            "nonmember-assignment-register-lock-leaver",
        )
        provider = _CountingProvider(ProductionApplicationAuthorityProvider(database_alias="default"))
        role_service = self._service(provider)
        proposal = role_service.propose_assignment(self._propose_command(
            actor=founder,
            membership_uuid=nominee_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:nonmember-assignment-register-lock:propose",
            idempotency_key="idem:nonmember-assignment-register-lock:propose",
            occurred_at=NOW,
            effective_at=NOW,
            received_at=self._database_now(),
        ))
        membership_service = OrganismMembershipService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: NOW,
            database_alias="default",
        )
        membership_service.transition_membership(TransitionMembershipCommand(
            credential=nonmember.credential,
            request_reference="request:nonmember-assignment-register-lock:leave",
            idempotency_key="idem:nonmember-assignment-register-lock:leave",
            occurred_at=NOW,
            effective_at=NOW,
            received_at=self._database_now(),
            membership_uuid=nonmember_membership_uuid,
            resulting_state="ENDED",
            reason_class="TEST_ONLY_REASON_LEAVE",
        ))
        command = self._transition_command(
            actor=nonmember,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            request_reference="request:nonmember-assignment-register-lock:assign",
            idempotency_key="idem:nonmember-assignment-register-lock:assign",
            effective_until_state="NONE",
            occurred_at=NOW,
            effective_at=NOW,
            received_at=self._database_now(),
        )
        provider_calls = len(provider.requests)
        decisions = AuthorityDecision.objects.count()
        receipts = OrganismCommandReceipt.objects.count()

        def acquire_register_lock(cursor):
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [ids["organism_uuid"]],
            )

        refusal = _run_with_external_locks(
            self,
            acquire_register_lock,
            lambda _holder_pid, _start_worker, run_bounded: run_bounded(
                lambda: role_service.transition_assignment(command),
            ),
        )
        self.assertIsInstance(refusal, S015AssignmentAuthorityUnavailable)
        self.assertEqual(str(refusal), "actor active membership is required")
        self.assertEqual(len(provider.requests), provider_calls)
        self.assertEqual(AuthorityDecision.objects.count(), decisions)
        self.assertEqual(OrganismCommandReceipt.objects.count(), receipts)

    def test_production_assign_names_current_nominee_consent(self):
        import json
        from core.models import AuthorityDecision
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )

        ids, founder = self._setup_organism("production-consented-assign")
        _, nominee, nominee_membership_uuid = self._add_active_member(ids, "production-consented-assign")
        provider = ProductionApplicationAuthorityProvider()
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            proposal_at = cursor.fetchone()[0]
        proposal_result = self._service(provider).propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=nominee_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:production-consented-assign:propose",
                idempotency_key="idem:production-consented-assign:propose",
                occurred_at=proposal_at,
                effective_at=proposal_at,
                received_at=proposal_at,
            )
        )
        proposal_event = _event_for_result(proposal_result)
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            consent_at = cursor.fetchone()[0]
        consent_service = OfficeAppointmentConsentService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: consent_at,
            database_alias="default",
        )
        with self.assertRaises(S015AuthorityDenied) as outsider_refusal:
            consent_service.record_office_consent(RecordOfficeConsentCommand(
                credential=founder.credential,
                request_reference="request:production-consented-assign:outsider-consent",
                idempotency_key="idem:production-consented-assign:outsider-consent",
                occurred_at=consent_at,
                effective_at=proposal_at,
                received_at=consent_at,
                assignment_uuid=proposal_result.assignment_uuid,
                proposal_event_uuid=proposal_event.event_uuid,
                action="CONSENT",
            ))
        self.assertIs(outsider_refusal.exception.refusal_code, S015AuthorityRefusalCode.DENIED)
        consent_result = consent_service.record_office_consent(RecordOfficeConsentCommand(
            credential=nominee.credential,
            request_reference="request:production-consented-assign:consent",
            idempotency_key="idem:production-consented-assign:consent",
            occurred_at=consent_at,
            effective_at=proposal_at,
            received_at=consent_at,
            assignment_uuid=proposal_result.assignment_uuid,
            proposal_event_uuid=proposal_event.event_uuid,
            action="CONSENT",
        ))
        self.assertEqual(
            _assignment_for_result(proposal_result).state,
            "PROPOSED",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            assign_at = cursor.fetchone()[0]
        assigned = self._service(provider).transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=proposal_result.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                effective_until_state="NONE",
                request_reference="request:production-consented-assign:assign",
                idempotency_key="idem:production-consented-assign:assign",
                occurred_at=assign_at,
                effective_at=assign_at,
                received_at=assign_at,
            )
        )
        decision = AuthorityDecision.objects.get(
            decision_reference=assigned.authority_decision_reference,
        )
        event = _event_for_result(assigned)
        facts = json.loads(decision.authority_reference[len("s015a1:"):])
        fact_references = {(fact["relation"], fact["reference"]) for fact in decision.facts_read}
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, actor_id, actor_access_epoch, actor_capacity, enterer_identity_id "
                "FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [event.pk],
            )
            actor_state, actor_id, actor_epoch, actor_capacity, enterer_identity_id = cursor.fetchone()
        self.assertEqual(facts["a"], str(proposal_result.assignment_uuid))
        self.assertEqual(facts["p"], str(proposal_event.event_uuid))
        self.assertEqual(facts["u"], "c:" + str(consent_result.consent_uuid))
        self.assertIn(("core_officeappointmentconsent", str(consent_result.consent_uuid)), fact_references)
        self.assertEqual(event.action, "ASSIGN")
        self.assertEqual(actor_state, "LIVING_ORGANISM")
        self.assertIsNone(actor_id)
        self.assertIsNone(actor_epoch)
        self.assertEqual(actor_capacity, "STANDING")
        self.assertEqual(enterer_identity_id, founder.pk)

    def test_self_proposal_is_the_assign_consent_reference(self):
        import json

        from core.models import AuthorityDecision
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )

        def database_now():
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                return cursor.fetchone()[0]

        ids, founder = self._setup_organism("self-proposal-consent")
        _, nominee, nominee_membership_uuid = self._add_active_member(ids, "self-proposal-consent")
        provider = ProductionApplicationAuthorityProvider()
        proposal_at = database_now()
        proposal = self._service(provider).propose_assignment(self._propose_command(
            actor=nominee,
            membership_uuid=nominee_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:self-proposal-consent:propose",
            idempotency_key="idem:self-proposal-consent:propose",
            occurred_at=proposal_at,
            effective_at=proposal_at,
            received_at=proposal_at,
        ))
        proposal_event = _event_for_result(proposal)
        assign_at = database_now()
        assigned = self._service(provider).transition_assignment(self._transition_command(
            actor=founder,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            request_reference="request:self-proposal-consent:assign",
            idempotency_key="idem:self-proposal-consent:assign",
            effective_until_state="NONE",
            occurred_at=assign_at,
            effective_at=proposal_at,
            received_at=assign_at,
        ))
        decision = AuthorityDecision.objects.get(
            decision_reference=assigned.authority_decision_reference,
        )
        facts = json.loads(decision.authority_reference[len("s015a1:"):])
        fact_references = {(fact["relation"], fact["reference"]) for fact in decision.facts_read}
        self.assertEqual(facts["u"], "e:" + str(proposal_event.event_uuid))
        self.assertIn(
            ("core_contextualroleassignmentstateevent", str(proposal_event.event_uuid)),
            fact_references,
        )
        self.assertFalse(OfficeAppointmentConsent.objects.filter(assignment=_assignment_for_result(proposal)).exists())

        with self.assertRaises(S015AuthorityDenied) as late_withdrawal:
            OfficeAppointmentConsentService(
                authority=S015Authority(provider=provider, database_alias="default"),
                clock=database_now,
                database_alias="default",
            ).record_office_consent(RecordOfficeConsentCommand(
                credential=nominee.credential,
                request_reference="request:self-proposal-consent:late-withdrawal",
                idempotency_key="idem:self-proposal-consent:late-withdrawal",
                occurred_at=database_now(),
                effective_at=proposal_at,
                received_at=database_now(),
                assignment_uuid=proposal.assignment_uuid,
                proposal_event_uuid=proposal_event.event_uuid,
                action="WITHDRAW",
            ))
        self.assertIs(late_withdrawal.exception.refusal_code, S015AuthorityRefusalCode.DENIED)
        self.assertFalse(OfficeAppointmentConsent.objects.filter(assignment=_assignment_for_result(proposal)).exists())

    def test_self_proposal_withdrawal_blocks_assignment(self):
        from core.models import AuthorityDecision
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )

        def database_now():
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                return cursor.fetchone()[0]

        ids, founder = self._setup_organism("self-proposal-withdrawal")
        _, nominee, nominee_membership_uuid = self._add_active_member(ids, "self-proposal-withdrawal")
        provider = ProductionApplicationAuthorityProvider()
        proposal_at = database_now()
        proposal = self._service(provider).propose_assignment(self._propose_command(
            actor=nominee,
            membership_uuid=nominee_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:self-proposal-withdrawal:propose",
            idempotency_key="idem:self-proposal-withdrawal:propose",
            occurred_at=proposal_at,
            effective_at=proposal_at,
            received_at=proposal_at,
        ))
        proposal_event = _event_for_result(proposal)
        role_service = self._service(provider)
        self.assertEqual(
            [row.assignment_uuid for row in role_service.open_nominations(
                ids["organism_uuid"], effective_at=proposal_at, known_at=proposal_event.recorded_at,
            )],
            [proposal.assignment_uuid],
        )
        withdrawal_at = database_now()
        withdrawal = OfficeAppointmentConsentService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=database_now,
            database_alias="default",
        ).record_office_consent(RecordOfficeConsentCommand(
            credential=nominee.credential,
            request_reference="request:self-proposal-withdrawal:withdraw",
            idempotency_key="idem:self-proposal-withdrawal:withdraw",
            occurred_at=withdrawal_at,
            effective_at=proposal_at,
            received_at=withdrawal_at,
            assignment_uuid=proposal.assignment_uuid,
            proposal_event_uuid=proposal_event.event_uuid,
            action="WITHDRAW",
        ))
        assign_at = database_now()
        with self.assertRaises(S015AuthorityDenied) as assignment_refusal:
            self._service(provider).transition_assignment(self._transition_command(
                actor=founder,
                assignment_uuid=proposal.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                request_reference="request:self-proposal-withdrawal:assign",
                idempotency_key="idem:self-proposal-withdrawal:assign",
                effective_until_state="NONE",
                occurred_at=assign_at,
                effective_at=proposal_at,
                received_at=assign_at,
            ))
        self.assertIs(
            assignment_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN,
        )
        decision = AuthorityDecision.objects.get(
            decision_reference=assignment_refusal.exception.authority_decision_reference,
        )
        self.assertIn(
            ("core_officeappointmentconsent", str(withdrawal.consent_uuid)),
            {(fact["relation"], fact["reference"]) for fact in decision.facts_read},
        )
        terminal_consent = OfficeAppointmentConsent.objects.get(consent_uuid=withdrawal.consent_uuid)
        self.assertEqual(
            role_service.open_nominations(
                ids["organism_uuid"],
                effective_at=proposal_at,
                known_at=terminal_consent.recorded_at,
            ),
            [],
        )
        self.assertEqual(
            list(ContextualRoleAssignmentStateEvent.objects.filter(
                assignment=_assignment_for_result(proposal),
            ).order_by("sequence").values_list("resulting_state", flat=True)),
            ["PROPOSED"],
        )

    def test_office_consent_is_database_timed_append_only_and_reverse_protected(self):
        from importlib import import_module

        ids, founder, nominee, service, proposal, assigned = self._propose_then_assign(
            label="consent-append-only",
        )
        consent = OfficeAppointmentConsent.objects.get(assignment=_assignment_for_result(proposal))
        self.assertLessEqual(consent.effective_at, consent.occurred_at)
        self.assertLessEqual(consent.occurred_at, consent.received_at)
        self.assertLessEqual(consent.received_at, consent.recorded_at)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OfficeAppointmentConsent.objects.filter(pk=consent.pk).update(
                    request_reference="request:mutated-consent",
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OfficeAppointmentConsent.objects.filter(pk=consent.pk).delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("TRUNCATE TABLE public.core_officeappointmentconsent")

        migration = import_module("core.migrations.0037_s2b_office_appointment_consent")
        with connection.schema_editor() as schema_editor:
            with self.assertRaisesRegex(RuntimeError, "reverse refused: consent or LO appointments exist"):
                migration.reverse(None, schema_editor)
        self.assertTrue(OfficeAppointmentConsent.objects.filter(pk=consent.pk).exists())

    def test_decline_and_withdraw_are_terminal_for_consent_and_assignment(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )

        for terminal_action in ("DECLINE", "WITHDRAW"):
            with self.subTest(terminal_action=terminal_action):
                label = f"terminal-{terminal_action.lower()}"
                ids, founder = self._setup_organism(label)
                _, nominee, nominee_membership_uuid = self._add_active_member(ids, label)
                provider = ProductionApplicationAuthorityProvider()
                with connection.cursor() as cursor:
                    cursor.execute("SELECT clock_timestamp()")
                    proposal_at = cursor.fetchone()[0]
                proposal = self._service(provider).propose_assignment(
                    self._propose_command(
                        actor=founder,
                        membership_uuid=nominee_membership_uuid,
                        role_definition_uuid=ids["coordinator_role_uuid"],
                        request_reference=f"request:{label}:propose",
                        idempotency_key=f"idem:{label}:propose",
                        occurred_at=proposal_at,
                        effective_at=proposal_at,
                        received_at=proposal_at,
                    )
                )
                proposal_event = _event_for_result(proposal)
                consent_service = OfficeAppointmentConsentService(
                    authority=S015Authority(provider=provider, database_alias="default"),
                    clock=lambda: proposal_at,
                    database_alias="default",
                )
                if terminal_action == "WITHDRAW":
                    self._record_test_consent(
                        provider=provider,
                        actor=nominee,
                        assignment_uuid=proposal.assignment_uuid,
                        label=label,
                        effective_at=proposal_at,
                    )
                with connection.cursor() as cursor:
                    cursor.execute("SELECT clock_timestamp()")
                    terminal_at = cursor.fetchone()[0]
                consent_service.record_office_consent(RecordOfficeConsentCommand(
                    credential=nominee.credential,
                    request_reference=f"request:{label}:{terminal_action.lower()}",
                    idempotency_key=f"idem:{label}:{terminal_action.lower()}",
                    occurred_at=terminal_at,
                    effective_at=proposal_at,
                    received_at=terminal_at,
                    assignment_uuid=proposal.assignment_uuid,
                    proposal_event_uuid=proposal_event.event_uuid,
                    action=terminal_action,
                ))
                with connection.cursor() as cursor:
                    cursor.execute("SELECT clock_timestamp()")
                    later = cursor.fetchone()[0]
                with self.assertRaises(S015AuthorityDenied) as consent_refusal:
                    consent_service.record_office_consent(RecordOfficeConsentCommand(
                        credential=nominee.credential,
                        request_reference=f"request:{label}:late-consent",
                        idempotency_key=f"idem:{label}:late-consent",
                        occurred_at=later,
                        effective_at=proposal_at,
                        received_at=later,
                        assignment_uuid=proposal.assignment_uuid,
                        proposal_event_uuid=proposal_event.event_uuid,
                        action="CONSENT",
                    ))
                self.assertIs(
                    consent_refusal.exception.refusal_code,
                    S015AuthorityRefusalCode.C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN,
                )
                with self.assertRaises(S015AuthorityDenied) as assignment_refusal:
                    self._service(provider).transition_assignment(
                        self._transition_command(
                            actor=founder,
                            assignment_uuid=proposal.assignment_uuid,
                            action="ASSIGN",
                            resulting_state="ACTIVE",
                            effective_until_state="NONE",
                            request_reference=f"request:{label}:assign",
                            idempotency_key=f"idem:{label}:assign",
                            occurred_at=later,
                            effective_at=proposal_at,
                            received_at=later,
                        )
                    )
                self.assertIs(
                    assignment_refusal.exception.refusal_code,
                    S015AuthorityRefusalCode.C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN,
                )
                expected_actions = ["DECLINE"] if terminal_action == "DECLINE" else ["CONSENT", "WITHDRAW"]
                self.assertEqual(
                    list(OfficeAppointmentConsent.objects.filter(assignment=_assignment_for_result(proposal))
                         .values_list("action", flat=True)),
                    expected_actions,
                )
                self.assertEqual(
                    list(ContextualRoleAssignmentStateEvent.objects.filter(
                        assignment=_assignment_for_result(proposal),
                    ).order_by("sequence").values_list("resulting_state", flat=True)),
                    ["PROPOSED"],
                )

    def test_ordinary_party_assign_is_refused_by_attribution_guard(self):
        ids, founder = self._setup_organism("ordinary-party-assignment")
        with self.assertRaisesRegex(IntegrityError, "LO decision attribution required"):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    append_event(
                        cursor,
                        "core_contextualroleassignmentstateevent",
                        ids["assignments"]["COORDINATOR"],
                        2,
                        "ASSIGN",
                        "PROPOSED",
                        "ACTIVE",
                        founder.pk,
                        ids["basis"],
                        "ordinary-party-assignment",
                        pred_id=ids["assignment_events"]["COORDINATOR"],
                    )

    def test_role_chain_capacity_accepts_lo_standing_and_guard_refuses_lo_na(self):
        ids, founder = self._setup_organism("lo-capacity-row")
        assignment_uuid = ids["founding_assignment_uuid"]
        target = {
            "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            "assignment_uuid": str(assignment_uuid),
            "event_action": "END",
            "living_organism_id": str(ids["organism_uuid"]),
            "resulting_state": "ENDED",
            "schema": "intevia.s015.organism-role-target.v1",
        }
        membership = OrganismMembership.objects.select_related("head_transition").get(
            pk=ids["membership"]
        )
        with transaction.atomic():
            request = _authority_request(
                database_alias="default",
                actor=founder,
                actor_membership=membership,
                actor_role_head_refs=(),
                action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
                request_reference="request:lo-capacity-row:end",
                idempotency_key="idem:lo-capacity-row:end",
                received_at=NOW,
                target_fingerprint=authority_target_fingerprint(target),
                effective_at=NOW,
                target=target,
                living_organism_id=ids["organism_uuid"],
                assignment_uuid=assignment_uuid,
            )
            decision = S015Authority(
                provider=_ExactProvider(), database_alias="default"
            ).qualify(request)
            with connection.cursor() as cursor:
                event_id, _ = append_event(
                    cursor,
                    "core_contextualroleassignmentstateevent",
                    ids["assignments"]["COORDINATOR"],
                    2,
                    "END",
                    "ACTIVE",
                    "ENDED",
                    founder.pk,
                    ids["basis"],
                    "lo-capacity-row",
                    pred_id=ids["assignment_events"]["COORDINATOR"],
                    overrides={
                        "actor_state": "LIVING_ORGANISM",
                        "actor_id": None,
                        "actor_access_epoch": None,
                        "actor_capacity": "STANDING",
                        "enterer_identity_id": founder.pk,
                        "authority_decision_reference": decision.authority_decision_reference,
                        "evidence_reference": decision.authority_reference,
                    },
                )
                refresh(cursor, "core_contextualroleassignment", ids["assignments"]["COORDINATOR"])
                cursor.execute(
                    "SELECT actor_state, actor_id, actor_access_epoch, actor_capacity "
                    "FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                    [event_id],
                )
                self.assertEqual(cursor.fetchone(), ("LIVING_ORGANISM", None, None, "STANDING"))
                cursor.execute(
                    "CREATE TEMP TABLE s2b_capacity_probe "
                    "(actor_state varchar(15), actor_capacity varchar(16)) ON COMMIT DROP"
                )
                cursor.execute(
                    "CREATE TRIGGER s2b_capacity_probe_guard AFTER INSERT ON s2b_capacity_probe "
                    "FOR EACH ROW EXECUTE FUNCTION public.s015_0022_guard_capacity()"
                )
                cursor.execute(
                    "INSERT INTO s2b_capacity_probe VALUES ('LIVING_ORGANISM', 'STANDING')"
                )
                with self.assertRaisesRegex(
                    IntegrityError, "acting party rows must be STANDING"
                ):
                    cursor.execute(
                        "INSERT INTO s2b_capacity_probe VALUES ('LIVING_ORGANISM', 'NA_NO_PARTY')"
                    )

    def test_role_chain_capacity_checks_allow_only_standing_lo_actors(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'public.core_contextualroleassignmentstateevent'::regclass "
                "AND conname = 's015_0022_contextualroleassignmentstateevent_actor_capacity_state_ck'"
            )
            capacity_check = cursor.fetchone()[0]
            cursor.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'public.core_contextualroleassignmentstateevent'::regclass "
                "AND conname = 's015_0022_contextualroleassignmentstateevent_actor_state_vocab_ck'"
            )
            actor_state_check = cursor.fetchone()[0]
        self.assertIn("'LIVING_ORGANISM'", actor_state_check)
        self.assertIn("'LIVING_ORGANISM'", capacity_check)
        self.assertIn("'STANDING'", capacity_check)

    def test_s2b_guards_are_separate_and_capacity_runs_first(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT t.tgname, p.proname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE c.relname = 'core_contextualroleassignmentstateevent' "
                "AND t.tgname IN ('s2b_00_capacity', 's2b_10_attribution') "
                "ORDER BY t.tgname"
            )
            role_guards = cursor.fetchall()
            cursor.execute(
                "SELECT t.tgname, p.proname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE c.relname = 'core_organismmembershiptransition' "
                "AND t.tgname = 's2b_10_membership_proposed_end'"
            )
            membership_guard = cursor.fetchone()
        self.assertEqual(role_guards, [
            ("s2b_00_capacity", "s2b_guard_lo_capacity"),
            ("s2b_10_attribution", "s2b_guard_office_attribution"),
        ])
        self.assertEqual(
            membership_guard,
            ("s2b_10_membership_proposed_end", "s2b_guard_membership_proposed_end_actor"),
        )

    def _propose_command(self, *, actor, membership_uuid, role_definition_uuid, request_reference, idempotency_key, effective_until_state="NONE", effective_until=None, occurred_at=NOW, effective_at=NOW, received_at=NOW):
        return ProposeAssignmentCommand(
            credential=actor.credential,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            membership_uuid=membership_uuid,
            role_definition_uuid=role_definition_uuid,
            effective_until_state=effective_until_state,
            effective_until=effective_until,
        )

    def _transition_command(self, *, actor, assignment_uuid, action, resulting_state, request_reference, idempotency_key, effective_until_state=None, effective_until=None, occurred_at=None, effective_at=NOW, received_at=None):
        if occurred_at is None or received_at is None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                current_time = cursor.fetchone()[0]
            occurred_at = current_time if occurred_at is None else occurred_at
            received_at = current_time if received_at is None else received_at
        return TransitionAssignmentCommand(
            credential=actor.credential,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            assignment_uuid=assignment_uuid,
            action=action,
            resulting_state=resulting_state,
            effective_until_state=effective_until_state,
            effective_until=effective_until,
        )

    def _record_test_consent(self, *, provider, actor, assignment_uuid, label, effective_at=NOW):
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )

        proposal = ContextualRoleAssignmentStateEvent.objects.get(
            assignment__assignment_uuid=assignment_uuid,
            sequence=1,
            action="PROPOSE",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            at = cursor.fetchone()[0]
        service = OfficeAppointmentConsentService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: at,
            database_alias="default",
        )
        return service.record_office_consent(RecordOfficeConsentCommand(
            credential=actor.credential,
            request_reference=f"request:{label}:consent",
            idempotency_key=f"idem:{label}:consent",
            occurred_at=at,
            effective_at=effective_at,
            received_at=at,
            assignment_uuid=assignment_uuid,
            proposal_event_uuid=proposal.event_uuid,
            action="CONSENT",
        ))

    def _propose_then_assign(self, *, label="role", effective_until_state="NONE", effective_until=None, at=NOW):
        ids, founder = self._setup_organism(label)
        _, member, member_membership_uuid = self._add_active_member(ids, label)
        provider = _ExactProvider()
        service = self._service(provider)
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=member_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference=f"request:{label}:propose",
                idempotency_key=f"idem:{label}:propose",
                effective_until_state=effective_until_state,
                effective_until=effective_until,
                occurred_at=at,
                effective_at=at,
                received_at=at,
            )
        )
        proposal_event = _event_for_result(propose_result)
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            consent_at = cursor.fetchone()[0]
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )

        OfficeAppointmentConsentService(
            authority=S015Authority(provider=provider, database_alias="default"),
            clock=lambda: consent_at,
            database_alias="default",
        ).record_office_consent(RecordOfficeConsentCommand(
            credential=member.credential,
            request_reference=f"request:{label}:consent",
            idempotency_key=f"idem:{label}:consent",
            occurred_at=consent_at,
            effective_at=at,
            received_at=consent_at,
            assignment_uuid=propose_result.assignment_uuid,
            proposal_event_uuid=proposal_event.event_uuid,
            action="CONSENT",
        ))
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            assign_at = cursor.fetchone()[0]
        assign_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=propose_result.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                request_reference=f"request:{label}:assign",
                idempotency_key=f"idem:{label}:assign",
                # the appointing decision supplies the term (§3.3)
                effective_until_state=effective_until_state,
                effective_until=effective_until,
                occurred_at=assign_at,
                effective_at=at,
                received_at=assign_at,
            )
        )
        return ids, founder, member, service, propose_result, assign_result

    # R1 — propose, then appoint (ASSIGN): sequences 1, 2; state ACTIVE; two receipts; the term on ASSIGN.
    def test_propose_then_appoint_commits_and_refreshes_cache(self):
        until = NOW + timedelta(days=180)
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(
            effective_until_state="FIXED", effective_until=until,
        )
        assignment = _assignment_for_result(assign_result)
        events = list(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment).order_by("sequence")
        )
        receipts = list(
            OrganismCommandReceipt.objects.filter(actor=founder, action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value)
        )

        self.assertEqual(propose_result.resulting_state, _subject_marker("resulting_state"))
        self.assertEqual(assign_result.resulting_state, _subject_marker("resulting_state"))
        self.assertEqual(assign_result.event_uuid, _subject_marker("event_uuid"))
        self.assertEqual(assign_result.event_sequence, _subject_marker("event_sequence"))
        self.assertEqual(assignment.state, "ACTIVE")
        self.assertEqual(assignment.head_state_event_id, _event_id_for_result(assign_result))
        self.assertEqual([event.sequence for event in events], [1, 2])
        self.assertEqual([event.action for event in events], ["PROPOSE", "ASSIGN"])
        self.assertIsNone(events[0].predecessor_id)
        self.assertEqual(events[1].predecessor_id, events[0].id)
        self.assertEqual(len(receipts), 2)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT effective_until_state, effective_until FROM public.core_contextualroleassignmentstateevent WHERE id = ANY(%s) ORDER BY sequence",
                ([event.id for event in events],),
            )
            propose_term, assign_term = cur.fetchall()
        # the PROPOSE row establishes no term (a chain row's effective_until states when that
        # event's effect ends); the proposed term is proposal content in the fingerprints
        self.assertEqual(propose_term, ("NOT_ESTABLISHED", None))
        # the appointing decision supplies the term, written on the ASSIGN row
        self.assertEqual(assign_term, ("FIXED", until))
        self.assertEqual(assign_result.effective_until_state, "FIXED")
        self.assertEqual(assign_result.effective_until, until)
        # the propose result and receipt describe the written row (the Reviewer's S2 F4)
        self.assertEqual(propose_result.effective_until_state, "NOT_ESTABLISHED")
        self.assertIsNone(propose_result.effective_until)
        propose_receipts = [r for r in receipts if r.result_payload["event_action"] == "PROPOSE"]
        self.assertEqual(len(propose_receipts), 1)
        self.assertEqual(propose_receipts[0].result_payload["effective_until_state"], "NOT_ESTABLISHED")
        self.assertIsNone(propose_receipts[0].result_payload["effective_until"])

    def test_writer_attributes_proposal_to_party_and_appointment_to_organism(self):
        ids, _founder = self._setup_organism("writer-attribution")
        _, nominee, nominee_membership_uuid = self._add_active_member(ids, "writer-attribution")
        service = self._service(_ExactProvider())
        proposal = service.propose_assignment(self._propose_command(
            actor=nominee,
            membership_uuid=nominee_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:writer-attribution:propose",
            idempotency_key="idem:writer-attribution:propose",
        ))
        appointment = service.transition_assignment(self._transition_command(
            actor=nominee,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            request_reference="request:writer-attribution:assign",
            idempotency_key="idem:writer-attribution:assign",
            effective_until_state="NONE",
        ))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT action, actor_state, actor_id, actor_access_epoch, actor_capacity, enterer_identity_id "
                "FROM public.core_contextualroleassignmentstateevent WHERE id = ANY(%s) ORDER BY sequence",
                [[_event_id_for_result(proposal), _event_id_for_result(appointment)]],
            )
            events = cursor.fetchall()
        self.assertEqual(events, [
            ("PROPOSE", "PARTY", nominee.pk, nominee.access_epoch, "STANDING", nominee.pk),
            ("ASSIGN", "LIVING_ORGANISM", None, None, "STANDING", nominee.pk),
        ])

    # R2 — the term: FIXED with a value; NONE without; FIXED with effective_until <= effective_at refused before any write.
    def test_term_validation_refused_before_any_write(self):
        ids, founder = self._setup_organism("term")
        _, member, member_membership_uuid = self._add_active_member(ids, "term")
        service = self._service(_ExactProvider())
        base = dict(
            actor=founder,
            membership_uuid=member_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
        )
        before = self._counts()
        with self.assertRaises(S015AssignmentTermRefused):
            service.propose_assignment(
                self._propose_command(
                    **base,
                    request_reference="request:term:fixed-missing",
                    idempotency_key="idem:term:fixed-missing",
                    effective_until_state="FIXED",
                    effective_until=None,
                )
            )
        with self.assertRaises(S015AssignmentTermRefused):
            service.propose_assignment(
                self._propose_command(
                    **base,
                    request_reference="request:term:not-later",
                    idempotency_key="idem:term:not-later",
                    effective_until_state="FIXED",
                    effective_until=NOW,
                )
            )
        with self.assertRaises(S015AssignmentTermRefused):
            service.propose_assignment(
                self._propose_command(
                    **base,
                    request_reference="request:term:none-with-value",
                    idempotency_key="idem:term:none-with-value",
                    effective_until_state="NONE",
                    effective_until=NOW + timedelta(days=1),
                )
            )
        self.assertEqual(self._counts(), before)

        fixed = service.propose_assignment(
            self._propose_command(
                **base,
                request_reference="request:term:fixed",
                idempotency_key="idem:term:fixed",
                effective_until_state="FIXED",
                effective_until=NOW + timedelta(days=90),
            )
        )
        open_ended = service.propose_assignment(
            self._propose_command(
                **base,
                request_reference="request:term:none",
                idempotency_key="idem:term:none",
                effective_until_state="NONE",
            )
        )
        self.assertEqual(fixed.resulting_state, _subject_marker("resulting_state"))
        self.assertEqual(open_ended.resulting_state, _subject_marker("resulting_state"))
        self.assertIsNone(open_ended.effective_until)
        after_proposals = self._counts()

        # the appointing decision supplies the term: required for ASSIGN, refused for END and
        # REASSIGN — before any write
        assign_base = dict(
            actor=founder,
            assignment_uuid=fixed.assignment_uuid,
            request_reference="request:term:assign",
            idempotency_key="idem:term:assign",
        )
        with self.assertRaises(S015AssignmentTermRefused):
            service.transition_assignment(
                self._transition_command(**assign_base, action="ASSIGN", resulting_state="ACTIVE")
            )
        with self.assertRaises(S015AssignmentTermRefused):
            service.transition_assignment(
                self._transition_command(
                    **assign_base, action="ASSIGN", resulting_state="ACTIVE",
                    effective_until_state="FIXED", effective_until=NOW,
                )
            )
        with self.assertRaises(S015AssignmentTermRefused):
            service.transition_assignment(
                self._transition_command(
                    **assign_base, action="END", resulting_state="ENDED",
                    effective_until_state="NONE",
                )
            )
        self.assertEqual(self._counts(), after_proposals)
        self.assertEqual(_assignment_for_result(fixed).state, "PROPOSED")
        self.assertEqual(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment=_assignment_for_result(fixed)).count(), 1,
        )

    # R3 — the no-tolling value in the ASSIGN payload and the receipt; a changed term under the
    # same ASSIGN key is a replay mismatch. The tolling value is not a command input (it is the
    # design default), so the reachable mismatch is the changed term; the value itself is asserted
    # on the receipt.
    def test_tolling_snapshot_recorded_and_term_change_mismatches(self):
        until = NOW + timedelta(days=30)
        ids, founder = self._setup_organism("tolling")
        _, member, member_membership_uuid = self._add_active_member(ids, "tolling")
        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=member_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:tolling:propose",
                idempotency_key="idem:tolling:propose",
            )
        )
        self._record_test_consent(
            provider=service._authority.provider,
            actor=member,
            assignment_uuid=propose_result.assignment_uuid,
            label="tolling",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            assign_at = cursor.fetchone()[0]
        assign_command = self._transition_command(
            actor=founder,
            assignment_uuid=propose_result.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            request_reference="request:tolling:assign",
            idempotency_key="idem:tolling:assign",
            effective_until_state="FIXED",
            effective_until=until,
            occurred_at=assign_at,
            received_at=assign_at,
        )
        assign_result = service.transition_assignment(assign_command)
        self.assertEqual(assign_result.suspension_tolls_term, _subject_marker("suspension_tolls_term"))
        self.assertEqual(assign_result.suspension_tolls_term_source, _subject_marker("suspension_tolls_term_source"))
        self.assertEqual(assign_result.effective_until_state, "FIXED")
        self.assertEqual(assign_result.effective_until, until)
        receipt = OrganismCommandReceipt.objects.get(
            actor=founder,
            action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            idempotency_key="idem:tolling:assign",
        )
        self.assertEqual(receipt.result_payload["suspension_tolls_term"], "NO")
        self.assertEqual(receipt.result_payload["suspension_tolls_term_source"], "DEFAULT (no settings store)")
        self.assertEqual(receipt.result_payload["effective_until_state"], "FIXED")
        event = _event_for_result(assign_result)
        self.assertEqual(event.payload_fingerprint, receipt.payload_fingerprint)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT effective_until_state, effective_until FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                (_event_id_for_result(assign_result),),
            )
            self.assertEqual(cur.fetchone(), ("FIXED", until))

        # the identical appointing decision under the same key replays the first result
        same = service.transition_assignment(assign_command)
        self.assertEqual(same, assign_result)
        # a changed term under the same key mismatches
        with self.assertRaises(S015CommandReplayMismatch):
            service.transition_assignment(
                self._transition_command(
                    actor=founder,
                    assignment_uuid=propose_result.assignment_uuid,
                    action="ASSIGN",
                    resulting_state="ACTIVE",
                    request_reference="request:tolling:assign",
                    idempotency_key="idem:tolling:assign",
                    effective_until_state="FIXED",
                    effective_until=until + timedelta(days=1),
                    occurred_at=assign_command.occurred_at,
                    effective_at=assign_command.effective_at,
                    received_at=assign_command.received_at,
                )
            )

    # R4 — END by an authorised decision; history intact.
    def test_end_by_authorised_decision_keeps_history(self):
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(label="end")
        end_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=assign_result.assignment_uuid,
                action="END",
                resulting_state="ENDED",
                request_reference="request:end:end",
                idempotency_key="idem:end:end",
            )
        )
        self.assertEqual(end_result.resulting_state, _subject_marker("resulting_state"))
        self.assertEqual(end_result.event_sequence, _subject_marker("event_sequence"))
        self.assertEqual(end_result.event_uuid, _subject_marker("event_uuid"))
        assignment = _assignment_for_result(assign_result)
        events = list(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment).order_by("sequence")
        )
        self.assertEqual(assignment.state, "ENDED")
        self.assertEqual([event.action for event in events], ["PROPOSE", "ASSIGN", "END"])
        self.assertEqual([event.prior_state for event in events], [None, "PROPOSED", "ACTIVE"])
        self.assertEqual([event.resulting_state for event in events], ["PROPOSED", "ACTIVE", "ENDED"])

    # R5 — REASSIGN ends the office; a fresh appointment is a new anchor.
    def test_reassign_ends_and_fresh_appointment_is_new_anchor(self):
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(label="reassign")
        reassign_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=assign_result.assignment_uuid,
                action="REASSIGN",
                resulting_state="ENDED",
                request_reference="request:reassign:end",
                idempotency_key="idem:reassign:end",
            )
        )
        self.assertEqual(reassign_result.resulting_state, _subject_marker("resulting_state"))
        self.assertEqual(reassign_result.effective_until, _subject_marker("effective_until"))
        self.assertEqual(reassign_result.effective_until_state, _subject_marker("effective_until_state"))
        self.assertEqual(reassign_result.event_action, "REASSIGN")
        assignment = _assignment_for_result(assign_result)
        self.assertEqual(assignment.state, "ENDED")
        # a fresh appointment is a new anchor (U21g-6): no uniqueness constraint blocks it
        membership = OrganismMembership.objects.get(identity=member, living_organism_id=ids["lo"])
        fresh_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=membership.membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:reassign:fresh",
                idempotency_key="idem:reassign:fresh",
            )
        )
        self.assertEqual(fresh_result.resulting_state, _subject_marker("resulting_state"))
        self.assertNotEqual(fresh_result.assignment_uuid, assign_result.assignment_uuid)

    # R6 — edges outside the decided set, including PROPOSED -> ENDED and every PROBATIONARY edge,
    # are refused; no row.
    def test_undecided_edges_and_tokens_refused_without_write(self):
        ids, founder = self._setup_organism("edge")
        _, member, member_membership_uuid = self._add_active_member(ids, "edge")
        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=member_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:edge:propose",
                idempotency_key="idem:edge:propose",
            )
        )
        self._record_test_consent(
            provider=service._authority.provider,
            actor=member,
            assignment_uuid=propose_result.assignment_uuid,
            label="edge",
        )
        assignment_uuid = propose_result.assignment_uuid
        before = self._counts()
        # PROPOSED -> ENDED: a proposal is not declined by any decided edge
        with self.assertRaises(S015AssignmentEdgeRefused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="END", resulting_state="ENDED",
                    request_reference="request:edge:decline", idempotency_key="idem:edge:decline",
                )
            )
        # no decided edge enters PROBATIONARY
        with self.assertRaises(S015AssignmentEdgeRefused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="ASSIGN", resulting_state="PROBATIONARY",
                    effective_until_state="NONE",
                    request_reference="request:edge:probationary", idempotency_key="idem:edge:probationary",
                )
            )
        # PROPOSE is propose_assignment's act, refused as a transition input before authority
        with self.assertRaises(S015AssignmentEdgeRefused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="PROPOSE", resulting_state="PROPOSED",
                    request_reference="request:edge:repropose", idempotency_key="idem:edge:repropose",
                )
            )
        # an undecided action token, refused before authority
        with self.assertRaises(S015AssignmentEdgeRefused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="PROMOTE", resulting_state="ACTIVE",
                    request_reference="request:edge:promote", idempotency_key="idem:edge:promote",
                )
            )
        # an unused state token, refused before authority
        with self.assertRaises(S015AssignmentStateUnused):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assignment_uuid, action="END", resulting_state="PRIVACY_TRANSFORMED",
                    request_reference="request:edge:privacy", idempotency_key="idem:edge:privacy",
                )
            )
        self.assertEqual(self._counts(), before)

    # R6b — an office standing at PROBATIONARY (a test-only fixture; no decided edge enters it)
    # has no decided out-edge: END, REASSIGN and ASSIGN out of it are refused; no row (the
    # Reviewer's S2 F1).
    def test_probationary_office_out_edges_refused_without_write(self):
        ids, founder = self._setup_organism("probedge")
        before = self._counts()
        for action, resulting_state, term_state in (
            ("END", "ENDED", None),
            ("REASSIGN", "ENDED", None),
            ("ASSIGN", "ACTIVE", "NONE"),
        ):
            with self.assertRaises(S015AssignmentEdgeRefused):
                _validate_assignment_edge(
                    current_state="PROBATIONARY",
                    action=action,
                    target_state=resulting_state,
                )
        self.assertEqual(self._counts(), before)

    # R7 — IL-4: suspension on an ACTIVE Coordinator office is refused; no row.
    def test_role_suspension_refused_from_active_coordinator_il4(self):
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(label="suspend")
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-4, S2b"):
            service.transition_assignment(
                self._transition_command(
                    actor=founder,
                    assignment_uuid=assign_result.assignment_uuid,
                    action="SUSPEND",
                    resulting_state="SUSPENDED",
                    request_reference="request:suspend:on",
                    idempotency_key="idem:suspend:on",
                )
            )
        self.assertEqual(self._counts(), before)
        # The service-level off direction cannot arise for Coordinator offices until SUSPEND is implemented (SB).

    def test_suspended_non_coordinator_fixture_reaches_il4_off_edge(self):
        ids, founder = self._setup_organism("il4-suspended-fixture")
        tag = "il4-suspended-fixture"
        fixture_at = T0 + timedelta(hours=1)
        with transaction.atomic(), connection.cursor() as cursor:
            role_id, _ = _add_role_definition(cursor, ids, tag, "TEST_ONLY_ARCHIVIST")
            assignment_uuid = u(f"assignment:{tag}:archivist")
            cursor.execute(
                "INSERT INTO core_contextualroleassignment "
                "(assignment_uuid, state, created_at, current_state_effective_at, state_known_at, "
                "state_source_event_set_fingerprint, membership_id, role_definition_id) "
                "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                (
                    assignment_uuid,
                    T0,
                    T0,
                    empty_fp(cursor, "core_contextualroleassignment", assignment_uuid),
                    ids["membership"],
                    role_id,
                ),
            )
            assignment_id = cursor.fetchone()[0]
            first_event_id = append_event(
                cursor,
                "core_contextualroleassignmentstateevent",
                assignment_id,
                1,
                "ASSIGN",
                None,
                "ACTIVE",
                founder.pk,
                ids["basis"],
                f"{tag}:assign",
            )[0]
            append_event(
                cursor,
                "core_contextualroleassignmentstateevent",
                assignment_id,
                2,
                "SUSPEND",
                "ACTIVE",
                "SUSPENDED",
                founder.pk,
                ids["basis"],
                f"{tag}:suspend",
                pred_id=first_event_id,
                eff=fixture_at,
                occ=fixture_at,
                rec=fixture_at,
            )
            refresh(cursor, "core_contextualroleassignment", assignment_id, fixture_at)

        assignment = ContextualRoleAssignment.objects.select_related("role_definition").get(pk=assignment_id)
        self.assertEqual(assignment.role_definition.code, "TEST_ONLY_ARCHIVIST")
        self.assertEqual(assignment.state, "SUSPENDED")
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-4, S2b"):
            _validate_assignment_edge(
                current_state=assignment.state,
                action="SUSPEND",
                target_state="ACTIVE",
            )

    def test_coordinator_suspend_event_is_refused_as_unmapped_office_act(self):
        ids, founder = self._setup_organism("suspend-unmapped-guard")
        before = self._counts()
        with self.assertRaisesRegex(IntegrityError, "S2B office attribution refused: unmapped office act"):
            with transaction.atomic(), connection.cursor() as cursor:
                append_event(
                    cursor,
                    "core_contextualroleassignmentstateevent",
                    ids["assignments"]["COORDINATOR"],
                    2,
                    "SUSPEND",
                    "ACTIVE",
                    "SUSPENDED",
                    founder.pk,
                    ids["basis"],
                    "suspend-unmapped-guard",
                    pred_id=ids["assignment_events"]["COORDINATOR"],
                    eff=NOW,
                    occ=NOW,
                    rec=NOW,
                )
        self.assertEqual(self._counts(), before)

    # R8 — IL-6: a Circle-scoped proposal refused; no row.
    def test_circle_scoped_office_refused_il6(self):
        ids, founder = self._setup_organism("circle")
        _, member, member_membership_uuid = self._add_active_member(ids, "circle")
        with transaction.atomic():
            with connection.cursor() as cur:
                _, circle_role_uuid = _add_role_definition(cur, ids, "circle", "TEST_ONLY_CIRCLE_SCRIBE", scope="CIRCLE")
        service = self._service(_ExactProvider())
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-6, S4"):
            service.propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=member_membership_uuid,
                    role_definition_uuid=circle_role_uuid,
                    request_reference="request:circle:propose",
                    idempotency_key="idem:circle:propose",
                )
            )
        self.assertEqual(self._counts(), before)

    # R9 — IL-3: an out-of-order office event refused; no row.
    def test_out_of_order_event_raises_il3_and_writes_nothing(self):
        past = NOW - timedelta(hours=2)
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(
            label="out-of-order", at=past,
        )
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, "IL-3"):
            service.transition_assignment(
                self._transition_command(
                    actor=founder,
                    assignment_uuid=assign_result.assignment_uuid,
                    action="END",
                    resulting_state="ENDED",
                    request_reference="request:out-of-order:end",
                    idempotency_key="idem:out-of-order:end",
                    occurred_at=NOW - timedelta(hours=3),
                    effective_at=NOW - timedelta(hours=3),
                    received_at=NOW,
                )
            )
        assignment = _assignment_for_result(assign_result)
        self.assertEqual(assignment.state, "ACTIVE")
        self.assertEqual(self._counts(), before)

    # R10a — the founding assignment is identified by its provenance; another authorised actor's
    # END and REASSIGN are refused with IL-7. No row, no receipt.
    def test_founder_original_office_protected_from_other_actors_il7(self):
        ids, founder = self._setup_organism("founder-guard")
        _, other, _ = self._add_active_member(ids, "founder-guard")
        service = self._service(_ExactProvider())
        founding_uuid = ids["founding_assignment_uuid"]
        before = self._counts()
        for action in ("END", "REASSIGN"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-7, SB") as caught:
                    service.transition_assignment(
                        self._transition_command(
                            actor=other,
                            assignment_uuid=founding_uuid,
                            action=action,
                            resulting_state="ENDED",
                            request_reference=f"request:founder-guard:{action.lower()}",
                            idempotency_key=f"idem:founder-guard:{action.lower()}",
                        )
                    )
                message = str(caught.exception)
                self.assertIn("not yet built", message)
                self.assertNotIn("not birthed", message)
        self.assertEqual(self._counts(), before)
        assignment = ContextualRoleAssignment.objects.get(assignment_uuid=founding_uuid)
        self.assertEqual(assignment.state, "ACTIVE")

    # R10b — the founder's own END of that assignment is admitted, subject to authority.
    def test_founder_own_ending_of_original_office_admitted(self):
        ids, founder = self._setup_organism("founder-end")
        service = self._service(_ExactProvider())
        result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=ids["founding_assignment_uuid"],
                action="END",
                resulting_state="ENDED",
                request_reference="request:founder-end:end",
                idempotency_key="idem:founder-end:end",
            )
        )
        self.assertEqual(result.resulting_state, "ENDED")
        self.assertEqual(result.event_sequence, 2)
        assignment = ContextualRoleAssignment.objects.get(assignment_uuid=ids["founding_assignment_uuid"])
        events = list(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment).order_by("sequence")
        )
        self.assertEqual(assignment.state, "ENDED")
        self.assertEqual([event.action for event in events], ["ASSIGN", "END"])

    def test_production_provider_allows_founder_to_end_own_founding_coordinator_office(self):
        from core.models import AuthorityDecision
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        ids, founder = self._setup_organism("founder-production-end")
        service = self._service(ProductionApplicationAuthorityProvider())
        result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=ids["founding_assignment_uuid"],
                action="END",
                resulting_state="ENDED",
                request_reference="request:founder-production-end:end",
                idempotency_key="idem:founder-production-end:end",
            )
        )

        event = _event_for_result(result)
        decision = AuthorityDecision.objects.get(
            decision_reference=result.authority_decision_reference,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_state, actor_capacity, enterer_identity_id "
                "FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [event.pk],
            )
            actor_state, actor_capacity, enterer_identity_id = cursor.fetchone()

        self.assertEqual(result.resulting_state, "ENDED")
        self.assertEqual(result.event_sequence, 2)
        self.assertEqual(decision.action, OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value)
        self.assertEqual(decision.target["event_action"], "END")
        self.assertEqual(decision.target["living_organism_id"], str(ids["organism_uuid"]))
        self.assertEqual(decision.actor_identity_id, founder.identity_id)
        self.assertEqual(decision.outcome, "QUALIFIED")
        self.assertIsNone(decision.refusal_code)
        self.assertEqual(event.authority_decision_reference, decision.decision_reference)
        self.assertEqual(event.prior_state, "ACTIVE")
        self.assertEqual(event.resulting_state, "ENDED")
        self.assertIsNone(event.actor_id)
        self.assertIsNone(event.actor_access_epoch)
        self.assertEqual(actor_state, "LIVING_ORGANISM")
        self.assertEqual(actor_capacity, "STANDING")
        self.assertEqual(enterer_identity_id, founder.pk)

    def test_capacity_refusals_use_founding_and_closure_coordinates(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        provider = ProductionApplicationAuthorityProvider()

        def assert_refusal(ids, actor, label, expected_code, effective_at=NOW):
            before = self._counts()
            with self.assertRaises(S015AuthorityDenied) as caught:
                self._service(provider).transition_assignment(
                    self._transition_command(
                        actor=actor,
                        assignment_uuid=ids["founding_assignment_uuid"],
                        action="END",
                        resulting_state="ENDED",
                        request_reference=f"request:{label}:end",
                        idempotency_key=f"idem:{label}:end",
                        effective_at=effective_at,
                    )
                )
            self.assertEqual(caught.exception.refusal_code, expected_code)
            self.assertEqual(self._counts(), before)

        ids, founder = self._setup_organism("capacity-not-founded-at-coordinate")
        assert_refusal(
            ids,
            founder,
            "capacity-not-founded-at-coordinate",
            S015AuthorityRefusalCode.C_LO_NO_CAPACITY,
            effective_at=T0 - timedelta(microseconds=1),
        )

        ids, founder = self._setup_organism("capacity-closed-at-coordinate")
        self._append_lo_close(ids, founder, "capacity-closed-at-coordinate", NOW)
        assert_refusal(
            ids,
            founder,
            "capacity-closed-at-coordinate",
            S015AuthorityRefusalCode.C_LO_CLOSED,
        )

        ids, founder = self._setup_organism("capacity-closed-before-coordinate")
        self._append_lo_close(
            ids,
            founder,
            "capacity-closed-before-coordinate",
            NOW - timedelta(microseconds=1),
        )
        assert_refusal(
            ids,
            founder,
            "capacity-closed-before-coordinate",
            S015AuthorityRefusalCode.C_LO_NO_CAPACITY,
        )

        ids, founder = self._setup_organism("capacity-present-permission-refused")
        _, other, _ = self._add_active_member(ids, "capacity-present-permission-refused")
        assert_refusal(
            ids,
            other,
            "capacity-present-permission-refused",
            S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR,
        )

    def test_production_provider_reassign_records_lo_act_and_refuses_non_coordinator(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        ids, founder, member, _, _, assigned = self._propose_then_assign(label="production-reassign")
        _, bystander, _ = self._add_active_member(ids, "production-reassign-bystander")
        service = self._service(ProductionApplicationAuthorityProvider())
        before = self._counts()
        with self.assertRaises(S015AuthorityDenied) as refused:
            service.transition_assignment(self._transition_command(
                actor=bystander,
                assignment_uuid=assigned.assignment_uuid,
                action="REASSIGN",
                resulting_state="ENDED",
                request_reference="request:production-reassign:refused",
                idempotency_key="idem:production-reassign:refused",
            ))
        self.assertEqual(refused.exception.refusal_code, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
        self.assertEqual(self._counts(), before)

        result = service.transition_assignment(self._transition_command(
            actor=founder,
            assignment_uuid=assigned.assignment_uuid,
            action="REASSIGN",
            resulting_state="ENDED",
            request_reference="request:production-reassign:permitted",
            idempotency_key="idem:production-reassign:permitted",
        ))
        event = _event_for_result(result)
        self.assertEqual(event.action, "REASSIGN")
        self.assertEqual(event.prior_state, "ACTIVE")
        self.assertEqual(event.resulting_state, "ENDED")
        self._assert_lo_recorded_act(event, founder)

    def test_production_provider_ends_another_office_only_for_effective_coordinator(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        ids, founder, member, _, _, assigned = self._propose_then_assign(label="production-other-end")
        _, bystander, _ = self._add_active_member(ids, "production-other-end-bystander")
        service = self._service(ProductionApplicationAuthorityProvider())
        before = self._counts()
        with self.assertRaises(S015AuthorityDenied) as refused:
            service.transition_assignment(self._transition_command(
                actor=bystander,
                assignment_uuid=assigned.assignment_uuid,
                action="END",
                resulting_state="ENDED",
                request_reference="request:production-other-end:refused",
                idempotency_key="idem:production-other-end:refused",
            ))
        self.assertEqual(refused.exception.refusal_code, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
        self.assertEqual(self._counts(), before)

        result = service.transition_assignment(self._transition_command(
            actor=founder,
            assignment_uuid=assigned.assignment_uuid,
            action="END",
            resulting_state="ENDED",
            request_reference="request:production-other-end:permitted",
            idempotency_key="idem:production-other-end:permitted",
        ))
        event = _event_for_result(result)
        self.assertEqual(event.action, "END")
        self.assertEqual(event.prior_state, "ACTIVE")
        self.assertEqual(event.resulting_state, "ENDED")
        self._assert_lo_recorded_act(event, founder)

    def test_production_provider_preserves_il7_for_another_coordinator(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        ids, founder, member, _, _, _ = self._propose_then_assign(label="production-il7")
        service = self._service(ProductionApplicationAuthorityProvider())
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-7, SB"):
            service.transition_assignment(self._transition_command(
                actor=member,
                assignment_uuid=ids["founding_assignment_uuid"],
                action="END",
                resulting_state="ENDED",
                request_reference="request:production-il7:end",
                idempotency_key="idem:production-il7:end",
            ))
        self.assertEqual(self._counts(), before)

    def test_cq_active_refuses_assign_for_sole_coordinator_and_other_office_end(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        ids, founder = self._setup_organism("cq-sole-assign")
        _, nominee, nominee_membership_uuid = self._add_active_member(ids, "cq-sole-assign")
        fixture_service = self._service(_ExactProvider())
        proposal = fixture_service.propose_assignment(self._propose_command(
            actor=founder,
            membership_uuid=nominee_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:cq-sole-assign:propose",
            idempotency_key="idem:cq-sole-assign:propose",
        ))
        self._record_test_consent(
            provider=fixture_service._authority.provider,
            actor=nominee,
            assignment_uuid=proposal.assignment_uuid,
            label="cq-sole-assign",
        )
        self._enable_cq(ids)
        self.assertEqual(
            ContextualRoleAssignment.objects.filter(
                membership__living_organism_id=ids["lo"],
                role_definition__code="COORDINATOR",
                state="ACTIVE",
            ).count(),
            1,
        )
        production_service = self._service(ProductionApplicationAuthorityProvider())
        before = self._counts()
        with self.assertRaises(S015AuthorityDenied) as assign_refusal:
            production_service.transition_assignment(self._transition_command(
                actor=founder,
                assignment_uuid=proposal.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                effective_until_state="NONE",
                request_reference="request:cq-sole-assign:assign",
                idempotency_key="idem:cq-sole-assign:assign",
            ))
        self.assertEqual(assign_refusal.exception.refusal_code, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
        self.assertEqual(self._counts(), before)

        ids, founder, member, _, _, assigned = self._propose_then_assign(label="cq-other-end")
        self._enable_cq(ids)
        before = self._counts()
        with self.assertRaises(S015AuthorityDenied) as end_refusal:
            self._service(ProductionApplicationAuthorityProvider()).transition_assignment(
                self._transition_command(
                    actor=founder,
                    assignment_uuid=assigned.assignment_uuid,
                    action="END",
                    resulting_state="ENDED",
                    request_reference="request:cq-other-end:end",
                    idempotency_key="idem:cq-other-end:end",
                )
            )
        self.assertEqual(end_refusal.exception.refusal_code, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
        self.assertEqual(self._counts(), before)

    def test_no_coordinator_uses_k2_recovery_limit_for_propose_and_assign(self):
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider

        def database_now():
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                return cursor.fetchone()[0]

        ids, founder = self._setup_organism("coordinator-recovery-limit")
        _, member, member_membership_uuid = self._add_active_member(ids, "coordinator-recovery-limit")
        service = self._service(ProductionApplicationAuthorityProvider())
        proposal_at = database_now()
        pending = service.propose_assignment(self._propose_command(
            actor=founder,
            membership_uuid=member_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:coordinator-recovery:propose",
            idempotency_key="idem:coordinator-recovery:propose",
            occurred_at=proposal_at,
            effective_at=proposal_at,
            received_at=proposal_at,
        ))
        ended_at = database_now()
        self._service(_ExactProvider()).transition_assignment(self._transition_command(
            actor=founder,
            assignment_uuid=ids["founding_assignment_uuid"],
            action="END",
            resulting_state="ENDED",
            request_reference="request:coordinator-recovery:end",
            idempotency_key="idem:coordinator-recovery:end",
            occurred_at=ended_at,
            effective_at=ended_at,
            received_at=ended_at,
        ))

        with self.assertRaises(S015AuthorityDenied) as proposal_refusal:
            proposal_at = database_now()
            service.propose_assignment(self._propose_command(
                actor=member,
                membership_uuid=member_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:coordinator-recovery:self-propose",
                idempotency_key="idem:coordinator-recovery:self-propose",
                occurred_at=proposal_at,
                effective_at=proposal_at,
                received_at=proposal_at,
            ))
        self.assertEqual(
            proposal_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED,
        )
        self.assertEqual(
            str(proposal_refusal.exception),
            "the required recovery route is not yet implemented",
        )

        with self.assertRaises(S015AuthorityDenied) as assignment_refusal:
            assign_at = database_now()
            service.transition_assignment(self._transition_command(
                actor=founder,
                assignment_uuid=pending.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                request_reference="request:coordinator-recovery:assign",
                idempotency_key="idem:coordinator-recovery:assign",
                effective_until_state="NONE",
                occurred_at=assign_at,
                effective_at=assign_at,
                received_at=assign_at,
            ))
        self.assertEqual(
            assignment_refusal.exception.refusal_code,
            S015AuthorityRefusalCode.C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED,
        )
        self.assertEqual(
            str(assignment_refusal.exception),
            "the required recovery route is not yet implemented",
        )
        self.assertEqual(
            list(ContextualRoleAssignmentStateEvent.objects.filter(
                assignment=_assignment_for_result(pending),
            ).order_by("sequence").values_list("action", flat=True)),
            ["PROPOSE"],
        )

    # R10c — a denied authority on the same attempt gets the authority error, not IL-7.
    def test_denied_authority_preempts_founder_protection(self):
        ids, founder = self._setup_organism("founder-denied")
        _, other, _ = self._add_active_member(ids, "founder-denied")
        denied_service = self._service(_DeniedProvider())
        before = self._counts()
        with self.assertRaises(S015AuthorityDenied):
            denied_service.transition_assignment(
                self._transition_command(
                    actor=other,
                    assignment_uuid=ids["founding_assignment_uuid"],
                    action="END",
                    resulting_state="ENDED",
                    request_reference="request:founder-denied:end",
                    idempotency_key="idem:founder-denied:end",
                )
            )
        self.assertEqual(self._counts(), before)

    # R10e — protection is read from the first event, so an anchor UPDATE cannot lift it: refresh
    # the founding office's cache (which updates the anchor row), then another authorised actor's
    # END is still refused with IL-7 and nothing is written.
    def test_founder_protection_survives_anchor_updates(self):
        ids, founder = self._setup_organism("founder-refresh")
        _, other, _ = self._add_active_member(ids, "founder-refresh")
        service = self._service(_ExactProvider())
        founding_assignment_id = ids["assignments"]["COORDINATOR"]
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                    ["core_contextualroleassignment", founding_assignment_id, T0],
                )
        assignment = ContextualRoleAssignment.objects.get(pk=founding_assignment_id)
        self.assertEqual(assignment.state, "ACTIVE")
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-7, SB"):
            service.transition_assignment(
                self._transition_command(
                    actor=other,
                    assignment_uuid=ids["founding_assignment_uuid"],
                    action="END",
                    resulting_state="ENDED",
                    request_reference="request:founder-refresh:end",
                    idempotency_key="idem:founder-refresh:end",
                )
            )
        self.assertEqual(self._counts(), before)

    # R10d — a later fresh COORDINATOR appointment held by the founder is not protected.
    def test_later_appointment_held_by_founder_is_not_protected(self):
        ids, founder = self._setup_organism("founder-later")
        _, other, _ = self._add_active_member(ids, "founder-later")
        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=ids["founder_membership_uuid"],
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:founder-later:propose",
                idempotency_key="idem:founder-later:propose",
            )
        )
        assign_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=propose_result.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                effective_until_state="NONE",
                request_reference="request:founder-later:assign",
                idempotency_key="idem:founder-later:assign",
            )
        )
        end_result = service.transition_assignment(
            self._transition_command(
                actor=other,
                assignment_uuid=assign_result.assignment_uuid,
                action="END",
                resulting_state="ENDED",
                request_reference="request:founder-later:end",
                idempotency_key="idem:founder-later:end",
            )
        )
        self.assertEqual(end_result.resulting_state, _subject_marker("resulting_state"))
        assignment = _assignment_for_result(assign_result)
        self.assertEqual(assignment.state, "ENDED")
        # the founding assignment itself is untouched
        founding = ContextualRoleAssignment.objects.get(assignment_uuid=ids["founding_assignment_uuid"])
        self.assertEqual(founding.state, "ACTIVE")

    # R11 — replay returns the first result; mismatch refused.
    def test_replay_returns_first_result(self):
        ids, founder = self._setup_organism("replay")
        _, member, member_membership_uuid = self._add_active_member(ids, "replay")
        service = self._service(_ExactProvider())
        propose_command = self._propose_command(
            actor=founder,
            membership_uuid=member_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:replay:propose",
            idempotency_key="idem:replay:propose",
        )
        first = service.propose_assignment(propose_command)
        second = service.propose_assignment(propose_command)
        self.assertIsInstance(first, AssignmentCommandResult)
        self.assertEqual(first, second)
        self.assertIsInstance(first.event_uuid, UUID)
        self.assertEqual(first.resulting_state, _subject_marker("resulting_state"))
        self.assertFalse(hasattr(first, "assignment_id"))
        self.assertFalse(hasattr(first, "event_id"))
        proposal_receipt = OrganismCommandReceipt.objects.get(
            actor=founder,
            action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            idempotency_key="idem:replay:propose",
        )
        self.assertNotIn("assignment_id", proposal_receipt.result_payload)
        self.assertNotIn("event_id", proposal_receipt.result_payload)
        self.assertIn("event_uuid", proposal_receipt.result_payload)
        self.assertEqual(ContextualRoleAssignment.objects.count(), 2)
        self._record_test_consent(
            provider=service._authority.provider,
            actor=member,
            assignment_uuid=first.assignment_uuid,
            label="replay",
        )
        self.assertEqual(OrganismCommandReceipt.objects.count(), 2)
        assign_command = self._transition_command(
            actor=founder,
            assignment_uuid=first.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            request_reference="request:replay:assign",
            idempotency_key="idem:replay:assign",
        )
        first_assign = service.transition_assignment(assign_command)
        second_assign = service.transition_assignment(assign_command)
        self.assertEqual(first_assign, second_assign)
        expected_withheld = {
            _subject_marker("resulting_state"),
            _subject_marker("event_uuid"),
            _subject_marker("event_sequence"),
            _subject_marker("current_state_effective_at"),
            _subject_marker("state_known_at"),
            _subject_marker("state_source_event_set_fingerprint"),
            _subject_marker("suspension_tolls_term"),
            _subject_marker("suspension_tolls_term_source"),
        }
        self.assertEqual(set(withheld_fields(first_assign)), expected_withheld)
        self.assertEqual(first_assign.resulting_state, _subject_marker("resulting_state"))
        self.assertEqual(first_assign.event_uuid, _subject_marker("event_uuid"))
        self.assertEqual(OrganismCommandReceipt.objects.count(), 3)
        self.assertEqual(
            ContextualRoleAssignmentStateEvent.objects.filter(assignment=_assignment_for_result(first)).count(),
            2,
        )

    # R12 — authority denied, unavailable or malformed: no row, no receipt.
    def test_authority_failures_refuse_without_write(self):
        ids, founder = self._setup_organism("authority")
        _, member, member_membership_uuid = self._add_active_member(ids, "authority")
        propose_command = self._propose_command(
            actor=founder,
            membership_uuid=member_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:authority:propose",
            idempotency_key="idem:authority:propose",
        )
        failures = [
            (_DeniedProvider(), S015AuthorityDenied),
            (_UnavailableProvider(), S015AuthorityUnavailable),
            (_MalformedProvider(), S015AuthorityMalformed),
        ]
        before = self._counts()
        for provider, exc_type in failures:
            with self.subTest(exc_type=exc_type.__name__):
                with self.assertRaises(exc_type):
                    self._service(provider).propose_assignment(propose_command)
        self.assertEqual(self._counts(), before)

        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(propose_command)
        transition_command = self._transition_command(
            actor=founder,
            assignment_uuid=propose_result.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            request_reference="request:authority:assign",
            idempotency_key="idem:authority:assign",
        )
        mid = self._counts()
        for provider, exc_type in failures:
            with self.subTest(exc_type=f"transition:{exc_type.__name__}"):
                with self.assertRaises(exc_type):
                    self._service(provider).transition_assignment(transition_command)
        self.assertEqual(self._counts(), mid)

    # R13 — atomic failure leaves nothing; two sessions, one row.
    def test_atomic_failure_leaves_nothing(self):
        ids, founder = self._setup_organism("atomic-failure")
        _, member, member_membership_uuid = self._add_active_member(ids, "atomic-failure")
        service = self._service(_ExactProvider())
        before = self._counts()
        with self.assertRaises(IntegrityError):
            service.propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=member_membership_uuid,
                    role_definition_uuid=ids["coordinator_role_uuid"],
                    request_reference="request:atomic-failure:propose",
                    idempotency_key="idem:atomic-failure:propose",
                    effective_at=NOW,
                    received_at=FAR_FUTURE,
                )
            )
        self.assertEqual(self._counts(), before)

    def test_concurrent_same_request_replays_once(self):
        ids, founder = self._setup_organism("concurrency")
        _, member, member_membership_uuid = self._add_active_member(ids, "concurrency")
        command_kwargs = dict(
            membership_uuid=member_membership_uuid,
            role_definition_uuid=ids["coordinator_role_uuid"],
            request_reference="request:concurrency:propose",
            idempotency_key="idem:concurrency:propose",
        )
        gate = Barrier(2)
        results: Queue[tuple[str, object]] = Queue()

        def worker():
            close_old_connections()
            worker_actor = Identity.objects.get(pk=founder.pk)
            service = self._service(_ExactProvider())
            try:
                gate.wait(timeout=10)
                result = service.propose_assignment(
                    self._propose_command(actor=worker_actor, **command_kwargs)
                )
                results.put(("ok", result))
            except Exception as exc:  # pragma: no cover - thread assertion channel
                results.put(("err", exc))
            finally:
                connection.close()

        thread_one = threading.Thread(target=worker, daemon=True)
        thread_two = threading.Thread(target=worker, daemon=True)
        thread_one.start()
        thread_two.start()
        thread_one.join(timeout=30)
        thread_two.join(timeout=30)
        self.assertFalse(thread_one.is_alive())
        self.assertFalse(thread_two.is_alive())

        collected = [results.get(timeout=5) for _ in range(2)]
        self.assertTrue(all(kind == "ok" for kind, _ in collected))
        self.assertEqual(collected[0][1], collected[1][1])
        self.assertEqual(ContextualRoleAssignment.objects.count(), 2)
        self.assertEqual(ContextualRoleAssignmentStateEvent.objects.count(), 2)
        self.assertEqual(OrganismCommandReceipt.objects.count(), 1)

    def test_withdraw_and_assign_serialize_both_orders(self):
        from core.models import AuthorityDecision
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
            RecordOfficeConsentCommand,
        )

        def database_now():
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                return cursor.fetchone()[0]

        def run_order(*, first_action, label):
            ids, founder = self._setup_organism(label)
            _, nominee, nominee_membership_uuid = self._add_active_member(ids, label)
            provider = ProductionApplicationAuthorityProvider()
            proposal_at = database_now()
            proposal_result = self._service(provider).propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=nominee_membership_uuid,
                    role_definition_uuid=ids["coordinator_role_uuid"],
                    request_reference=f"request:{label}:propose",
                    idempotency_key=f"idem:{label}:propose",
                    occurred_at=proposal_at,
                    effective_at=proposal_at,
                    received_at=proposal_at,
                )
            )
            proposal_event = ContextualRoleAssignmentStateEvent.objects.get(
                pk=_event_id_for_result(proposal_result),
            )
            self._record_test_consent(
                provider=provider,
                actor=nominee,
                assignment_uuid=proposal_result.assignment_uuid,
                label=label,
                effective_at=proposal_at,
            )
            receipts_before_race = OrganismCommandReceipt.objects.count()

            first_inserted = threading.Event()
            release_first = threading.Event()
            second_started = threading.Event()
            stop_second = threading.Event()
            second_authorized = threading.Event()
            outcomes: Queue[tuple[str, str, object]] = Queue()

            def command_for(action, actor, received_at, action_provider=None):
                active_provider = action_provider or provider
                if action == "ASSIGN":
                    command = self._transition_command(
                        actor=actor,
                        assignment_uuid=proposal_result.assignment_uuid,
                        action="ASSIGN",
                        resulting_state="ACTIVE",
                        request_reference=f"request:{label}:assign",
                        idempotency_key=f"idem:{label}:assign",
                        effective_until_state="NONE",
                        occurred_at=received_at,
                        effective_at=proposal_at,
                        received_at=received_at,
                    )
                    return self._service(active_provider).transition_assignment(command)
                consent_service = OfficeAppointmentConsentService(
                    authority=S015Authority(provider=active_provider, database_alias="default"),
                    clock=lambda: received_at,
                    database_alias="default",
                )
                return consent_service.record_office_consent(RecordOfficeConsentCommand(
                    credential=actor.credential,
                    request_reference=f"request:{label}:withdraw",
                    idempotency_key=f"idem:{label}:withdraw",
                    occurred_at=received_at,
                    effective_at=proposal_at,
                    received_at=received_at,
                    assignment_uuid=proposal_result.assignment_uuid,
                    proposal_event_uuid=proposal_event.event_uuid,
                    action="WITHDRAW",
                ))

            def first_worker():
                close_old_connections()
                held = False
                target_table = (
                    "core_officeappointmentconsent"
                    if first_action == "WITHDRAW"
                    else "core_contextualroleassignmentstateevent"
                )
                worker_handle = worker_handles[0]

                def hold_after_insert(execute, sql, params, many, context):
                    nonlocal held
                    result = execute(sql, params, many, context)
                    normalized_sql = sql.lower()
                    if not held and "insert into" in normalized_sql and target_table in normalized_sql:
                        held = True
                        first_inserted.set()
                        if not release_first.wait(timeout=20):
                            raise TimeoutError("race witness did not release first transaction")
                    return result

                try:
                    worker_handle.pid = backend_pid(connection)
                    worker_handle.ready.set()
                    actor = Identity.objects.get(pk=(nominee.pk if first_action == "WITHDRAW" else founder.pk))
                    with connection.execute_wrapper(hold_after_insert):
                        result = command_for(first_action, actor, database_now())
                    outcomes.put(("first", "ok", result))
                except Exception as exc:  # pragma: no cover - thread assertion channel
                    outcomes.put(("first", "err", exc))
                finally:
                    connection.close()

            second_action = "ASSIGN" if first_action == "WITHDRAW" else "WITHDRAW"

            class TrackingProvider:
                def evaluate_organism_authority(self, request):
                    second_authorized.set()
                    return provider.evaluate_organism_authority(request)

            second_provider = TrackingProvider()

            def second_worker():
                close_old_connections()
                worker_handle = worker_handles[1]
                try:
                    worker_handle.pid = backend_pid(connection)
                    worker_handle.ready.set()
                    second_started.set()
                    actor = Identity.objects.get(pk=(founder.pk if second_action == "ASSIGN" else nominee.pk))
                    if stop_second.is_set():
                        raise _RollbackHeldTransaction()
                    result = command_for(second_action, actor, database_now(), second_provider)
                    outcomes.put(("second", "ok", result))
                except _RollbackHeldTransaction:
                    outcomes.put(("second", "err", _RollbackHeldTransaction()))
                except Exception as exc:  # pragma: no cover - thread assertion channel
                    outcomes.put(("second", "err", exc))
                finally:
                    connection.close()

            first_handle = _ConcurrencyWorker(stop_event=release_first, abort_event=threading.Event())
            worker_handles = [first_handle]
            first_handle.thread = threading.Thread(target=first_worker, daemon=True)
            workers = [first_handle]
            second_thread = None
            first_handle.thread.start()
            try:
                self.assertTrue(first_inserted.wait(timeout=10))
                second_handle = _ConcurrencyWorker(stop_event=stop_second, abort_event=threading.Event())
                worker_handles.append(second_handle)
                workers.append(second_handle)
                second_thread = threading.Thread(target=second_worker, daemon=True)
                second_handle.thread = second_thread
                second_thread.start()
                self.assertTrue(second_started.wait(timeout=10))
                blocked = False
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [second_handle.pid])
                        blocked = bool(cursor.fetchone()[0])
                    if blocked:
                        break
                    time.sleep(0.01)
                self.assertTrue(blocked, "second command did not block behind the LO lock")
                self.assertFalse(second_authorized.is_set())
                release_first.set()
                first_handle.thread.join(timeout=30)
                second_thread.join(timeout=30)
                self.assertFalse(first_handle.thread.is_alive())
                self.assertFalse(second_thread.is_alive())
                collected = {
                    name: (kind, value)
                    for name, kind, value in (outcomes.get(timeout=5) for _ in range(2))
                }
                self.assertEqual(collected["first"][0], "ok", repr(collected["first"][1]))
                self.assertEqual(collected["second"][0], "err")
                self.assertIsInstance(collected["second"][1], S015AuthorityDenied)
                self.assertEqual(OrganismCommandReceipt.objects.count(), receipts_before_race + 1)

                assignment = ContextualRoleAssignment.objects.get(
                    assignment_uuid=proposal_result.assignment_uuid,
                )
                consent_rows = list(OfficeAppointmentConsent.objects.filter(
                    assignment=assignment,
                ).order_by("ordinal"))
                role_events = list(ContextualRoleAssignmentStateEvent.objects.filter(
                    assignment=assignment,
                ).order_by("sequence"))
                refusal = collected["second"][1]
                refusal_decision = AuthorityDecision.objects.get(
                    decision_reference=refusal.authority_decision_reference,
                )
                first_decision = AuthorityDecision.objects.get(
                    decision_reference=collected["first"][1].authority_decision_reference,
                )
                first_refs = {
                    (fact["relation"], fact["reference"])
                    for fact in first_decision.facts_read
                }
                refusal_refs = {
                    (fact["relation"], fact["reference"])
                    for fact in refusal_decision.facts_read
                }

                if first_action == "WITHDRAW":
                    self.assertIn(("core_officeappointmentconsent", str(consent_rows[0].consent_uuid)), first_refs)
                    self.assertEqual(refusal.refusal_code, S015AuthorityRefusalCode.C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN)
                    self.assertEqual(assignment.state, "PROPOSED")
                    self.assertEqual([row.action for row in consent_rows], ["CONSENT", "WITHDRAW"])
                    self.assertEqual([event.action for event in role_events], ["PROPOSE"])
                    self.assertIn(("core_officeappointmentconsent", str(consent_rows[-1].consent_uuid)), refusal_refs)
                else:
                    assigned_event = role_events[-1]
                    self.assertIn(("core_officeappointmentconsent", str(consent_rows[0].consent_uuid)), first_refs)
                    self.assertEqual(refusal.refusal_code, S015AuthorityRefusalCode.DENIED)
                    self.assertEqual(assignment.state, "ACTIVE")
                    self.assertEqual([row.action for row in consent_rows], ["CONSENT"])
                    self.assertEqual([event.action for event in role_events], ["PROPOSE", "ASSIGN"])
                    self.assertIn(("core_officeappointmentconsent", str(consent_rows[-1].consent_uuid)), refusal_refs)
                    self.assertIn(("core_contextualroleassignmentstateevent", str(assigned_event.event_uuid)), refusal_refs)
            finally:
                _finish_concurrency_workers(
                    self,
                    workers,
                    stop_events=[first_handle.stop_event, stop_second],
                    abort_events=[],
                )

        run_order(first_action="WITHDRAW", label="withdraw-first")
        run_order(first_action="ASSIGN", label="assign-first")

    # R14 — cache equals the fold after each command.
    def test_cache_equals_fold_after_each_command(self):
        ids, founder = self._setup_organism("cache")
        _, member, member_membership_uuid = self._add_active_member(ids, "cache")
        service = self._service(_ExactProvider())
        propose_result = service.propose_assignment(
            self._propose_command(
                actor=founder,
                membership_uuid=member_membership_uuid,
                role_definition_uuid=ids["coordinator_role_uuid"],
                request_reference="request:cache:propose",
                idempotency_key="idem:cache:propose",
            )
        )
        self._record_test_consent(
            provider=service._authority.provider,
            actor=member,
            assignment_uuid=propose_result.assignment_uuid,
            label="cache",
        )

        def fold_check(assignment_pk, expected_state, expected_count):
            assignment = ContextualRoleAssignment.objects.get(pk=assignment_pk)
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT projection, eligible_count, served_from, fingerprint FROM public.s015_effective_state('core_contextualroleassignment', %s, %s, %s)",
                    (assignment.pk, assignment.current_state_effective_at, assignment.state_known_at),
                )
                projection, eligible_count, served_from, fingerprint = cur.fetchone()
            self.assertEqual(projection, expected_state)
            self.assertEqual(projection, assignment.state)
            self.assertEqual(eligible_count, expected_count)
            self.assertEqual(served_from, "CACHE_REQUALIFIED")
            self.assertEqual(fingerprint, assignment.state_source_event_set_fingerprint)

        fold_check(_assignment_for_result(propose_result).pk, "PROPOSED", 1)
        assign_result = service.transition_assignment(
            self._transition_command(
                actor=founder,
                assignment_uuid=propose_result.assignment_uuid,
                action="ASSIGN",
                resulting_state="ACTIVE",
                effective_until_state="NONE",
                request_reference="request:cache:assign",
                idempotency_key="idem:cache:assign",
            )
        )
        fold_check(_assignment_for_result(assign_result).pk, "ACTIVE", 2)

    # R15 — assignment_in_effect: in effect before effective_until, not at or after it; the recorded
    # state stays ACTIVE; a NONE term stays in effect.
    def test_assignment_in_effect_reads_the_term(self):
        until = NOW + timedelta(hours=2)
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(
            label="in-effect", effective_until_state="FIXED", effective_until=until,
        )
        assignment = _assignment_for_result(assign_result)
        self.assertTrue(service.assignment_in_effect(assignment, NOW))
        self.assertFalse(service.assignment_in_effect(assignment, until))
        self.assertFalse(service.assignment_in_effect(assignment, until + timedelta(hours=1)))
        assignment.refresh_from_db()
        self.assertEqual(assignment.state, "ACTIVE")

        ids2, founder2, member2, service2, propose2, assign2 = self._propose_then_assign(label="in-effect-open")
        open_assignment = _assignment_for_result(assign2)
        self.assertTrue(service2.assignment_in_effect(open_assignment, NOW + timedelta(days=365)))

    # R17 — a denied authority asking for an undecided edge or a limit gets the authority error,
    # not the edge or limit error, and no write (the founder half is R10c).
    def test_denied_authority_preempts_edge_and_limit_refusals(self):
        ids, founder, member, service, propose_result, assign_result = self._propose_then_assign(label="order")
        denied = self._service(_DeniedProvider())
        with transaction.atomic():
            with connection.cursor() as cur:
                _, archivist_uuid = _add_role_definition(cur, ids, "order", "TEST_ONLY_ARCHIVIST")
        member_membership_uuid = OrganismMembership.objects.get(identity=member, living_organism_id=ids["lo"]).membership_uuid
        before = self._counts()
        # an undecided edge
        with self.assertRaises(S015AuthorityDenied):
            denied.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assign_result.assignment_uuid, action="END", resulting_state="PROPOSED",
                    request_reference="request:order:edge", idempotency_key="idem:order:edge",
                )
            )
        # a limit (IL-4 suspension)
        with self.assertRaises(S015AuthorityDenied):
            denied.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=assign_result.assignment_uuid, action="SUSPEND", resulting_state="SUSPENDED",
                    request_reference="request:order:suspend", idempotency_key="idem:order:suspend",
                )
            )
        # a limit on propose (IL-8, an organism-defined office other than COORDINATOR)
        with self.assertRaises(S015AuthorityDenied):
            denied.propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=member_membership_uuid,
                    role_definition_uuid=archivist_uuid,
                    request_reference="request:order:il8",
                    idempotency_key="idem:order:il8",
                )
            )
        self.assertEqual(self._counts(), before)

    # R18 — a role definition other than COORDINATOR is refused with IL-8 (reserved and
    # organism-defined offices alike). No row.
    def test_non_coordinator_role_refused_il8(self):
        ids, founder = self._setup_organism("reserved")
        _, member, member_membership_uuid = self._add_active_member(ids, "reserved")
        with transaction.atomic():
            with connection.cursor() as cur:
                _, archivist_uuid = _add_role_definition(cur, ids, "reserved", "TEST_ONLY_ARCHIVIST")
        service = self._service(_ExactProvider())
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-8, SR"):
            service.propose_assignment(
                self._propose_command(
                    actor=founder,
                    membership_uuid=member_membership_uuid,
                    role_definition_uuid=archivist_uuid,
                    request_reference="request:reserved:propose",
                    idempotency_key="idem:reserved:propose",
                )
            )
        self.assertEqual(self._counts(), before)

    # R18b — IL-8 binds transitions too: an ACTIVE office on an organism-defined, non-COORDINATOR
    # role definition (a test-only fixture) cannot be ended through S2; no row (the Reviewer's S2 F2).
    def test_non_coordinator_office_transition_refused_il8(self):
        ids, founder = self._setup_organism("otheroffice")
        membership_id, member, _ = self._add_active_member(ids, "otheroffice")
        with connection.cursor() as cur:
            archivist_id, _ = _add_role_definition(cur, ids, "otheroffice", "TEST_ONLY_ARCHIVIST")
        _, office_uuid = _assignment_at_state(
            self, ids, membership_id, archivist_id, "otheroffice:fixture",
        )
        service = self._service(_ExactProvider())
        before = self._counts()
        with self.assertRaisesRegex(S015OperationNotYetImplemented, r"IL-8, SR"):
            service.transition_assignment(
                self._transition_command(
                    actor=founder, assignment_uuid=office_uuid, action="END", resulting_state="ENDED",
                    request_reference="request:otheroffice:end", idempotency_key="idem:otheroffice:end",
                )
            )
        self.assertEqual(self._counts(), before)

    # R19 — a COORDINATOR proposal on a non-current membership is refused as design policy
    # (⟨E2.114⟩: PROBATIONARY holds no COORDINATOR office; ⟨C.037⟩: only ACTIVE is current). No row.
    def test_non_current_membership_holds_no_coordinator_office(self):
        ids, founder = self._setup_organism("probation")
        service = self._service(_ExactProvider())
        for state in ("PROBATIONARY", "PROPOSED", "ENDED"):
            with self.subTest(state=state):
                with transaction.atomic():
                    with connection.cursor() as cur:
                        _, _, member_membership_uuid = _add_member(cur, ids, f"probation-{state.lower()}", state=state)
                before = self._counts()
                with self.assertRaises(S015AssignmentMembershipRefused):
                    service.propose_assignment(
                        self._propose_command(
                            actor=founder,
                            membership_uuid=member_membership_uuid,
                            role_definition_uuid=ids["coordinator_role_uuid"],
                            request_reference=f"request:probation:{state.lower()}",
                            idempotency_key=f"idem:probation:{state.lower()}",
                        )
                    )
                self.assertEqual(self._counts(), before)
