"""Concurrency witnesses for PKT-LOCK-1 lock ordering and contention translation."""

from __future__ import annotations

from dataclasses import asdict
import json
import time
from queue import Empty
from threading import Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import (
    AuthorityDecision,
    CommandIntake,
    Identity,
    IdentityTransition,
    LivingOrganism,
    LivingOrganismEvent,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    OrganismCommandReceipt,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    OrganismEntitlementPolicyText,
    OrganismMembership,
    OrganismMembershipTransition,
    OrganismRoleDefinition,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_application_service import (
    GatewayDraftCommand,
    SubmitApplicationCommand,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015PostFoundingAuthorityRequest,
    S015PostFoundingAuthorityResponse,
)
from src.intevia.services.organism_command_support import canonical_timestamp, command_mutex_key
from src.intevia.services.organism_entitlement_service import (
    EntitlementElectionCommand,
    OrganismEntitlementService,
    S015AuthorityRefusalCode,
    S015EntitlementRefused,
    S015EntitlementUnavailable,
)
from src.intevia.services.organism_command_support import (
    S015CommandContended,
    _lock_actor,
)
from src.intevia.services.organism_membership_service import (
    OpenMembershipCommand,
    OrganismMembershipService,
)
from src.intevia.services.organism_governance_service import (
    DecideGovernanceSeatCommand,
    RequestGovernanceSeatCommand,
)
from src.intevia.services.organism_role_service import (
    OrganismRoleService,
    ProposeAssignmentCommand,
    TransitionAssignmentCommand,
)
from src.intevia.services.identity_service import IdentityLifecycleService
from tests.s015_0021_support import (
    append_event,
    empty_fp,
    refresh,
    register_test_learner,
    seed_identity,
)
from tests.s015_concurrency_support import (
    backend_pid,
    cleanup_workers,
    exact_wait,
    start_worker,
    wait_until_blocked,
)
from tests.sb2_route_support import birth, database_now, prepare_route
from tests.sbg_route_support import (
    advance_to_birthing,
    governance_arrangement_body,
    prepare_governance_route,
)
from tests.se_route_support import ensure_test_policy_text


def _gateway_draft_command(route, suffix: str) -> GatewayDraftCommand:
    now = database_now()
    return GatewayDraftCommand(
        credential=route.founder.credential,
        living_organism_id=route.organism.organism_id,
        action=OrganismAction.EFFECT_APPLICATION_QUESTION,
        request_reference=f"lock1:draft:{suffix}:{route.tag}",
        idempotency_key=f"lock1:draft:key:{suffix}:{route.tag}",
        occurred_at=now,
        effective_at=now,
        fields={"answer_format": "LONG_ANSWER"},
    )


def _election_command(route, actor, suffix: str) -> EntitlementElectionCommand:
    policy_version, policy_text_sha256 = ensure_test_policy_text()
    return EntitlementElectionCommand(
        credential=actor.credential,
        living_organism_uuid=route.organism.organism_id,
        request_reference=f"lock1:election:{suffix}:{route.tag}",
        idempotency_key=f"lock1:election:key:{suffix}:{route.tag}",
        occurred_at=database_now(),
        policy_version=policy_version,
        policy_text_sha256=policy_text_sha256,
    )


class _Lock1AllowProvider:
    def evaluate_organism_authority(
        self,
        request: S015PostFoundingAuthorityRequest,
    ) -> S015PostFoundingAuthorityResponse:
        return S015PostFoundingAuthorityResponse(
            **asdict(request),
            authority_reference=f"authority:lock1:{request.request_reference}",
        )


def _new_identity(tag: str) -> Identity:
    with transaction.atomic(), connection.cursor() as cursor:
        identity_pk = seed_identity(cursor, f"lock1-{tag}")
    return Identity.objects.select_related("credential").get(pk=identity_pk)


def _new_registered_identity(tag: str) -> Identity:
    return register_test_learner(
        username=f"lock1-{tag}-{uuid4().hex}",
        password="Q7!vN#2rW@5xK$9m",
        display_name=f"LOCK1 {tag}",
    )


def _open_membership_command(route, actor, target, suffix: str) -> OpenMembershipCommand:
    now = database_now()
    return OpenMembershipCommand(
        credential=actor.credential,
        request_reference=f"lock1:open:{suffix}:{route.tag}",
        idempotency_key=f"lock1:open:key:{suffix}:{route.tag}",
        occurred_at=now,
        effective_at=now,
        living_organism_id=route.organism.organism_id,
        member_identity_id=target.identity_id,
        reason_class="TEST_ONLY_REASON_OPEN",
    )


def _submit_application_command(route, applicant, suffix: str) -> SubmitApplicationCommand:
    now = database_now()
    return SubmitApplicationCommand(
        credential=applicant.credential,
        living_organism_id=route.organism.organism_id,
        request_reference=f"lock1:submit:{suffix}:{route.tag}",
        idempotency_key=f"lock1:submit:key:{suffix}:{route.tag}",
        occurred_at=now,
        effective_at=now,
        answer="PKT-LOCK-1 disposable witness submission",
    )


def _deactivate_identity(identity: Identity, tag: str) -> IdentityTransition:
    return IdentityLifecycleService.change_access(
        identity_id=identity.identity_id,
        action=IdentityTransition.Action.DEACTIVATE,
        requesting_actor=None,
        authority_reference=f"lock1:deactivate:{tag}",
        technical_executor="LOCK1_TEST",
        evidence_reference=f"lock1:deactivate-evidence:{tag}",
        reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
        correlation_id=uuid4(),
        occurred_at=database_now(),
    )


def _add_active_member(route, identity: Identity, tag: str):
    membership_at = database_now()
    membership_uuid = uuid4()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core_organismmembership (
                membership_uuid, state, created_at, current_state_effective_at,
                state_known_at, state_source_event_set_fingerprint, identity_id,
                living_organism_id
            ) VALUES (%s, 'ACTIVE', %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            [
                membership_uuid,
                membership_at,
                membership_at,
                membership_at,
                empty_fp(cursor, "core_organismmembership", membership_uuid),
                identity.pk,
                route.organism.pk,
            ],
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
            route.organism.founding_authority_basis_id,
            f"lock1:membership:{tag}",
            eff=membership_at,
            occ=membership_at,
            rec=membership_at,
            extra={"reason_class": "TEST"},
        )
        refresh(
            cursor,
            "core_organismmembership",
            membership_id,
            membership_at,
        )
    return membership_uuid, membership_at


def _add_authorized_coordinator(route, tag: str) -> Identity:
    identity = _new_registered_identity(f"coordinator-{tag}")
    membership_uuid, membership_at = _add_active_member(route, identity, tag)

    role = OrganismRoleDefinition.objects.get(
        living_organism=route.organism,
        code="COORDINATOR",
    )
    role_service = OrganismRoleService(
        authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
        clock=database_now,
    )
    proposal_at = database_now()
    proposal = role_service.propose_assignment(
        ProposeAssignmentCommand(
            credential=identity.credential,
            request_reference=f"lock1:{tag}:propose",
            idempotency_key=f"lock1:{tag}:propose:key",
            occurred_at=membership_at,
            effective_at=membership_at,
            membership_uuid=membership_uuid,
            role_definition_uuid=role.role_uuid,
            effective_until_state="NONE",
            effective_until=None,
        )
    )
    role_service.transition_assignment(
        TransitionAssignmentCommand(
            credential=route.founder.credential,
            request_reference=f"lock1:{tag}:assign",
            idempotency_key=f"lock1:{tag}:assign:key",
            occurred_at=membership_at,
            effective_at=membership_at,
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            effective_until=None,
        )
    )
    return identity


def _sqlstate(result) -> str | None:
    if not isinstance(result, Exception):
        return None
    return (
        getattr(result, "diagnostic_sqlstate", None)
        or getattr(getattr(result, "__cause__", None), "sqlstate", None)
    )


def _membership_tables_snapshot() -> dict[str, tuple[object, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename LIKE 'core_organismmembership%' "
            "ORDER BY tablename"
        )
        table_names = [row[0] for row in cursor.fetchall()]
        snapshots = {}
        for table_name in table_names:
            quoted_table = connection.ops.quote_name(table_name)
            cursor.execute(
                f"SELECT to_jsonb(row_data) FROM public.{quoted_table} AS row_data "
                "ORDER BY row_data.id"
            )
            snapshots[table_name] = tuple(row[0] for row in cursor.fetchall())
    return snapshots


def _lock1_state_snapshot() -> dict[str, tuple[object, ...]]:
    models = (
        LivingOrganism,
        LivingOrganismEvent,
        OrganismMembership,
        OrganismMembershipTransition,
        OrganismEntitlement,
        OrganismEntitlementEvent,
        OrganismCommandReceipt,
        AuthorityDecision,
    )
    snapshots = {}
    with connection.cursor() as cursor:
        for model in models:
            table_name = connection.ops.quote_name(model._meta.db_table)
            cursor.execute(
                f"SELECT to_jsonb(row_data) FROM public.{table_name} AS row_data "
                "ORDER BY row_data.id"
            )
            snapshots[model._meta.db_table] = tuple(
                row[0] for row in cursor.fetchall()
            )
    return snapshots


def _lo_beta_domain_snapshot(organism: LivingOrganism) -> dict[str, tuple[object, ...]]:
    """Return one LO's beta-domain rows, whole, for a zero-domain-write comparison.

    PKT-CLK-3 CLK3-2 (L-4): the LO row, its settings versions, its LO events, its
    entitlement anchors and their entitlement events. Scoped to the LO, so an Identity
    deactivation committed beside the command is not counted as a domain write.
    """
    entitlement_table = connection.ops.quote_name(OrganismEntitlement._meta.db_table)
    entitlement_pk = connection.ops.quote_name(OrganismEntitlement._meta.pk.column)
    entitlement_lo = connection.ops.quote_name(
        OrganismEntitlement._meta.get_field("living_organism").column
    )

    def column(model, field_name):
        return connection.ops.quote_name(model._meta.get_field(field_name).column)

    scoped = (
        (LivingOrganism, f"row_data.{connection.ops.quote_name(LivingOrganism._meta.pk.column)} = %s"),
        (LOApplicationSettingsVersion, f"row_data.{column(LOApplicationSettingsVersion, 'living_organism')} = %s"),
        (LivingOrganismEvent, f"row_data.{column(LivingOrganismEvent, 'living_organism')} = %s"),
        (OrganismEntitlement, f"row_data.{entitlement_lo} = %s"),
        (
            OrganismEntitlementEvent,
            f"row_data.{column(OrganismEntitlementEvent, 'entitlement')} IN "
            f"(SELECT {entitlement_pk} FROM public.{entitlement_table} WHERE {entitlement_lo} = %s)",
        ),
    )
    snapshots = {}
    with connection.cursor() as cursor:
        for model, condition in scoped:
            table_name = connection.ops.quote_name(model._meta.db_table)
            pk_column = connection.ops.quote_name(model._meta.pk.column)
            cursor.execute(
                f"SELECT to_jsonb(row_data) FROM public.{table_name} AS row_data "
                f"WHERE {condition} ORDER BY row_data.{pk_column}",
                [organism.pk],
            )
            snapshots[model._meta.db_table] = tuple(row[0] for row in cursor.fetchall())
    return snapshots


def _outcome(result) -> dict[str, str | None]:
    return {
        "class": "ok" if result == "ok" else type(result).__name__,
        "sqlstate": _sqlstate(result),
        "refusal_code": getattr(
            getattr(result, "refusal_code", None),
            "value",
            None,
        ),
        "disclosed_refusal_code": getattr(
            getattr(result, "disclosed_refusal_code", None),
            "value",
            None,
        ),
    }


def _wait_for_commit_or_finish(cursor, waiter_pid: int, blocker_pid: int, worker) -> bool:
    deadline = time.monotonic() + 10
    last_sample = None
    while time.monotonic() < deadline:
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            "SELECT %s::int = ANY(pg_blocking_pids(pid)), wait_event_type, wait_event, query "
            "FROM pg_stat_activity WHERE pid = %s::int",
            [blocker_pid, waiter_pid],
        )
        last_sample = cursor.fetchone()
        if last_sample is not None:
            blocked, wait_type, wait_event, query = last_sample
            if blocked and wait_type == "Lock" and "commit" in query.lower():
                wait_until_blocked(
                    cursor,
                    waiter_pid,
                    blocker_pid,
                    query_fragment="COMMIT",
                    timeout=1,
                )
                return True
        if not worker.thread.is_alive():
            return False
        time.sleep(0.01)
    raise AssertionError(
        f"Coordinator neither reached the expected COMMIT wait nor finished: {last_sample!r}"
    )


def _fk_catalogue_capture(cursor) -> list[dict[str, object]]:
    cursor.execute(
        """
        SELECT conrelid::regclass::text, confrelid::regclass::text, conname,
               condeferrable, condeferred
          FROM pg_constraint
         WHERE contype = 'f'
           AND (
               conrelid IN (
                   'core_livingorganism'::regclass,
                   'core_organismmembership'::regclass,
                   'core_identity'::regclass
               )
               OR confrelid IN (
                   'core_livingorganism'::regclass,
                   'core_organismmembership'::regclass,
                   'core_identity'::regclass
               )
           )
         ORDER BY 1, 2, 3
        """
    )
    return [
        {
            "table": row[0],
            "references": row[1],
            "constraint": row[2],
            "deferrable": row[3],
            "deferred": row[4],
        }
        for row in cursor.fetchall()
    ]


def _capture_wait(cursor, coordinator_pid: int, founder_pid: int, founder_pk: int):
    cursor.execute(
        "SELECT pid, state, wait_event_type, wait_event, query, pg_blocking_pids(pid) "
        "FROM pg_stat_activity WHERE pid = %s::int",
        [coordinator_pid],
    )
    activity = cursor.fetchone()
    cursor.execute(
        "SELECT locktype, relation::regclass::text, page, tuple, transactionid::text, mode, granted "
        "FROM pg_locks WHERE pid = %s::int AND locktype IN ('tuple', 'transactionid') "
        "ORDER BY locktype, relation, transactionid"
    , [coordinator_pid])
    locks = cursor.fetchall()
    exact = exact_wait(cursor, coordinator_pid)
    cursor.execute(
        "SELECT backend_xid::text FROM pg_stat_activity WHERE pid = %s::int",
        [founder_pid],
    )
    founder_xid = cursor.fetchone()[0]
    cursor.execute(
        "SELECT ctid::text FROM core_identity WHERE id = %s",
        [founder_pk],
    )
    founder_ctid = cursor.fetchone()[0]
    cursor.execute(
        "SELECT conname, condeferrable, condeferred "
        "FROM pg_constraint "
        "WHERE conrelid = 'core_livingorganism'::regclass "
        "AND contype = 'f' AND confrelid = 'core_identity'::regclass"
    )
    identity_constraints = cursor.fetchall()
    capture = {
        "activity": activity,
        "exact_wait": exact,
        "locks": locks,
        "founder_xid": founder_xid,
        "founder_ctid": founder_ctid,
        "identity_constraints": identity_constraints,
        "fk_catalogue": _fk_catalogue_capture(cursor),
    }
    return capture


@skipUnless(connection.vendor == "postgresql", "PKT-LOCK-1 witnesses require PostgreSQL")
class Lock1ActorLockOrderTests(TransactionTestCase):
    reset_sequences = True

    def _assert_workers_closed(self, workers):
        deadline = time.monotonic() + 30.0
        for worker in workers:
            worker.thread.join(timeout=max(0.0, deadline - time.monotonic()))
            self.assertFalse(
                worker.thread.is_alive(),
                "worker did not finish before backend-closure assertion",
            )
        worker_pids = [worker.pid() for worker in workers]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pid FROM pg_stat_activity WHERE pid = ANY(%s)",
                [worker_pids],
            )
            self.assertEqual(cursor.fetchall(), [])

    def _run_compatible_pair(self, suffix: str, *, capture_section_1_6: bool):
        route = prepare_route(tag=f"lock1-{suffix}-{uuid4().hex[:8]}")
        birth(route)
        coordinator = _add_authorized_coordinator(
            route,
            f"{suffix}-coordinator-{uuid4().hex}",
        )
        entitlement_service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        draft_command = _gateway_draft_command(route, suffix)
        election_command = _election_command(route, coordinator, suffix)
        before = (
            LOApplicationQuestionVersion.objects.filter(
                living_organism=route.organism,
            ).count(),
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
        )
        gate_key = command_mutex_key(
            actor_identity_id=route.founder.identity_id,
            action=draft_command.action,
            idempotency_key=draft_command.idempotency_key,
        )
        monitor_pid = backend_pid(connection)
        workers = []
        founder_result = None
        coordinator_result = None
        waited_at_commit = False
        section_capture = None
        cancellation = None
        context = None
        founder_worker = None
        coordinator_worker = None
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                founder_worker = start_worker(
                    lambda: route.application_service.save_gateway_draft(draft_command)
                )
                workers.append(founder_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        founder_worker.pid(),
                        monitor_pid,
                        query_fragment="pg_advisory_xact_lock",
                    )

                coordinator_worker = start_worker(
                    lambda: entitlement_service.activate_beta(election_command)
                )
                workers.append(coordinator_worker)
                with connection.cursor() as cursor:
                    waited_at_commit = _wait_for_commit_or_finish(
                        cursor,
                        coordinator_worker.pid(),
                        founder_worker.pid(),
                        coordinator_worker,
                    )
                    if waited_at_commit:
                        wait_until_blocked(
                            cursor,
                            coordinator_worker.pid(),
                            founder_worker.pid(),
                            query_fragment="COMMIT",
                        )
                        if capture_section_1_6:
                            section_capture = _capture_wait(
                                cursor,
                                coordinator_worker.pid(),
                                founder_worker.pid(),
                                route.founder.pk,
                            )
                            cursor.execute(
                                "SELECT pg_cancel_backend(%s::int)",
                                [coordinator_worker.pid()],
                            )
                            cancellation = cursor.fetchone()[0]
                if waited_at_commit and capture_section_1_6:
                    coordinator_result = coordinator_worker.result(timeout=15)
                    cause = getattr(coordinator_result, "__cause__", None)
                    context = getattr(getattr(cause, "diag", None), "context", None)
                    if context is None:
                        nested = getattr(cause, "__cause__", None)
                        context = getattr(getattr(nested, "diag", None), "context", None)
                elif not waited_at_commit:
                    coordinator_result = coordinator_worker.result(timeout=15)
        except BaseException:
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise

        if founder_worker is None or coordinator_worker is None:
            raise AssertionError("The compatible-pair workers were not both started")
        founder_result = founder_worker.result(timeout=30)
        if coordinator_result is None:
            coordinator_result = coordinator_worker.result(timeout=30)
        after = (
            LOApplicationQuestionVersion.objects.filter(
                living_organism=route.organism,
            ).count(),
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
        )
        if capture_section_1_6:
            print(
                "LOCK1_SECTION_1_6="
                + json.dumps(
                    {
                        "waited_at_commit": waited_at_commit,
                        "capture": section_capture,
                        "pg_cancel_backend": cancellation,
                        "ri_context": context,
                        "coordinator_outcome": _outcome(coordinator_result),
                        "founder_outcome": _outcome(founder_result),
                        "version_and_grant_delta": (
                            after[0] - before[0],
                            after[1] - before[1],
                        ),
                    },
                    default=str,
                    sort_keys=True,
                ),
                flush=True,
            )
        return {
            "waited_at_commit": waited_at_commit,
            "capture": section_capture,
            "cancellation": cancellation,
            "founder": founder_result,
            "coordinator": coordinator_result,
            "version_and_grant_delta": (
                after[0] - before[0],
                after[1] - before[1],
            ),
            "ri_context": context,
        }

    def test_w_lock_1_founder_draft_and_coordinator_opt_in_are_compatible(self):
        """Shows the founder draft and second-Coordinator opt-in can both succeed."""
        capture = self._run_compatible_pair("section-1-6", capture_section_1_6=True)
        pair = self._run_compatible_pair("compatible-pair", capture_section_1_6=False)
        print(
            "W-LOCK-1_OUTCOMES="
            + json.dumps(
                {
                    "waited_at_commit": pair["waited_at_commit"],
                    "founder": _outcome(pair["founder"]),
                    "coordinator": _outcome(pair["coordinator"]),
                    "version_and_grant_delta": pair["version_and_grant_delta"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if capture["waited_at_commit"]:
            self.assertIsNotNone(capture["capture"])
            self.assertTrue(capture["capture"]["fk_catalogue"])
            self.assertEqual(len(capture["capture"]["identity_constraints"]), 1)
            self.assertEqual(
                capture["capture"]["identity_constraints"][0][1:],
                (True, True),
            )
            self.assertEqual(capture["capture"]["exact_wait"]["waiting"]["locktype"], "transactionid")
            self.assertEqual(capture["capture"]["exact_wait"]["waiting"]["mode"], "ShareLock")
            self.assertEqual(capture["capture"]["exact_wait"]["tuple_relation"], "core_identity")
            self.assertTrue(capture["cancellation"])
            self.assertIsNotNone(capture["ri_context"])
            self.assertIn("FOR KEY SHARE OF x", capture["ri_context"])

        self.assertFalse(
            capture["waited_at_commit"],
            "the Coordinator must not wait on the founder's Identity at COMMIT",
        )
        self.assertEqual(capture["founder"], "ok")
        self.assertEqual(capture["coordinator"], "ok")
        self.assertEqual(capture["version_and_grant_delta"], (1, 1))
        self.assertFalse(pair["waited_at_commit"])
        self.assertEqual(pair["founder"], "ok")
        self.assertEqual(pair["coordinator"], "ok")
        self.assertEqual(pair["version_and_grant_delta"], (1, 1))

    def test_w_lock_1b_both_opt_ins_record_single_grant_and_loser(self):
        """Shows concurrent Coordinator opt-ins produce one grant and one recorded loser."""
        route = prepare_route(tag=f"lock1-both-opt-in-{uuid4().hex[:8]}")
        birth(route)
        coordinator = _add_authorized_coordinator(route, f"both-{uuid4().hex}")
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        founder_command = _election_command(route, route.founder, "founder")
        coordinator_command = _election_command(route, coordinator, "coordinator")
        tracked_models = (
            OrganismEntitlement,
            OrganismEntitlementEvent,
            OrganismEntitlementPolicyText,
            LOApplicationSettingsVersion,
            LivingOrganismEvent,
            OrganismCommandReceipt,
            AuthorityDecision,
            LivingOrganism,
        )
        before = {
            model.__name__: model.objects.count()
            for model in tracked_models
        }
        gate_key = command_mutex_key(
            actor_identity_id=route.founder.identity_id,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            idempotency_key=founder_command.idempotency_key,
        )
        monitor_pid = backend_pid(connection)
        founder_worker = coordinator_worker = None
        workers = []
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                founder_worker = start_worker(
                    lambda: service.activate_beta(founder_command)
                )
                workers.append(founder_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        founder_worker.pid(),
                        monitor_pid,
                        query_fragment="pg_advisory_xact_lock",
                    )
                coordinator_worker = start_worker(
                    lambda: service.activate_beta(coordinator_command)
                )
                workers.append(coordinator_worker)
                coordinator_worker.thread.join(timeout=15)
                if coordinator_worker.thread.is_alive():
                    with connection.cursor() as cursor:
                        wait_until_blocked(
                            cursor,
                            coordinator_worker.pid(),
                            founder_worker.pid(),
                            query_fragment="COMMIT",
                        )
        except BaseException:
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise

        founder_result = founder_worker.result(timeout=30)
        coordinator_result = coordinator_worker.result(timeout=30)
        grants = OrganismEntitlement.objects.filter(
            living_organism=route.organism,
            basis="BETA_OPT_IN",
        ).count()
        print(
            "W-LOCK-1b_OUTCOMES="
            + json.dumps(
                {
                    "founder": _outcome(founder_result),
                    "coordinator": _outcome(coordinator_result),
                    "grant_count": grants,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        self.assertNotEqual(_sqlstate(founder_result), "40P01")
        self.assertNotEqual(_sqlstate(coordinator_result), "40P01")
        self.assertEqual(grants, 1)
        self.assertEqual(coordinator_result, "ok")
        self.assertIsInstance(founder_result, S015EntitlementRefused)
        self.assertIs(
            founder_result.refusal_code,
            S015AuthorityRefusalCode.SE_ALREADY_BETA,
        )
        expected_additions = {
            "OrganismEntitlement": 1,
            "OrganismEntitlementEvent": 1,
            "OrganismEntitlementPolicyText": 0,
            "LOApplicationSettingsVersion": 1,
            "LivingOrganismEvent": 1,
            "OrganismCommandReceipt": 1,
            "AuthorityDecision": 1,
            "LivingOrganism": 0,
        }
        for model in tracked_models:
            self.assertEqual(
                model.objects.count() - before[model.__name__],
                expected_additions[model.__name__],
                model.__name__,
            )
        self.assertFalse(
            AuthorityDecision.objects.filter(
                request_reference=founder_command.request_reference,
            ).exists()
        )
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                actor=route.founder,
                action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
                idempotency_key=founder_command.idempotency_key,
            ).exists()
        )

    def test_w_lock_n_preserves_same_actor_serialization_and_lock_shape(self):
        """Shows same-actor commands serialize on the identity row with the required lock shape."""
        route = prepare_route(tag=f"lock1-same-actor-{uuid4().hex[:8]}")
        birth(route)
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        first = _election_command(route, route.founder, "same-actor-first")
        second = _election_command(route, route.founder, "same-actor-second")
        gate_key = command_mutex_key(
            actor_identity_id=route.founder.identity_id,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            idempotency_key=first.idempotency_key,
        )
        monitor_pid = backend_pid(connection)
        first_worker = second_worker = None
        workers = []
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                first_worker = start_worker(lambda: service.activate_beta(first))
                workers.append(first_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        first_worker.pid(),
                        monitor_pid,
                        query_fragment="pg_advisory_xact_lock",
                    )
                second_worker = start_worker(lambda: service.activate_beta(second))
                workers.append(second_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        second_worker.pid(),
                        first_worker.pid(),
                        query_fragment="FOR NO KEY UPDATE",
                    )
        except BaseException:
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise

        first_result = first_worker.result(timeout=30)
        second_result = second_worker.result(timeout=30)
        with transaction.atomic(), CaptureQueriesContext(connection) as captured:
            _lock_actor(
                database_alias="default",
                credential=route.founder.credential,
                unavailable=RuntimeError,
            )
        actor_lock_sql = " ".join(query["sql"] for query in captured.captured_queries)
        self.assertIn(
            'FOR NO KEY UPDATE OF "core_identity", "auth_user"',
            actor_lock_sql,
        )
        self.assertEqual(first_result, "ok")
        self.assertNotEqual(second_result, "ok")
        self.assertNotEqual(_sqlstate(second_result), "40P01")

    def test_w_lock_c_a_credential_flip_waits_until_actor_command_commits(self):
        """Shows a credential status change waits for the actor command to commit."""
        route = prepare_route(tag=f"lock1-credential-flip-{uuid4().hex[:8]}")
        birth(route)
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        founder_epoch = route.founder.access_epoch
        command = _election_command(route, route.founder, "credential-flip")
        gate_key = command_mutex_key(
            actor_identity_id=route.founder.identity_id,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            idempotency_key=command.idempotency_key,
        )
        monitor_pid = backend_pid(connection)
        command_worker = flip_worker = None
        workers = []

        def flip_credential():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE auth_user SET is_active = false WHERE id = %s",
                    [route.founder.credential_id],
                )

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                command_worker = start_worker(lambda: service.activate_beta(command))
                workers.append(command_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        command_worker.pid(),
                        monitor_pid,
                        query_fragment="pg_advisory_xact_lock",
                    )
                flip_worker = start_worker(flip_credential)
                workers.append(flip_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        flip_worker.pid(),
                        command_worker.pid(),
                        query_fragment="UPDATE",
                    )
        except BaseException:
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise

        command_result = command_worker.result(timeout=30)
        flip_result = flip_worker.result(timeout=30)
        route.founder.credential.refresh_from_db()
        self.assertEqual(command_result, "ok")
        self.assertEqual(flip_result, "ok")
        self.assertFalse(route.founder.credential.is_active)
        receipt = OrganismCommandReceipt.objects.get(
            actor=route.founder,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
            idempotency_key=command.idempotency_key,
        )
        authority_decision = AuthorityDecision.objects.get(
            decision_reference=receipt.authority_decision_reference,
        )
        self.assertEqual(authority_decision.actor_access_epoch, founder_epoch)
        self.assertEqual(
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
            1,
        )

    def test_w_lock_c_b_governed_deactivation_obeys_actor_lock_order_both_ways(self):
        """Shows governed deactivation and an actor command obey lock order in both interleavings."""
        route = prepare_route(tag=f"lock1-governed-deactivation-{uuid4().hex[:8]}")
        birth(route)
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        founder_epoch = route.founder.access_epoch
        command = _election_command(route, route.founder, "deactivation-first")
        gate_key = command_mutex_key(
            actor_identity_id=route.founder.identity_id,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            idempotency_key=command.idempotency_key,
        )
        monitor_pid = backend_pid(connection)
        command_worker = deactivation_worker = None
        workers = []
        deactivation_ready = Event()
        allow_deactivation_commit = Event()

        def deactivate_after_command():
            with transaction.atomic():
                _deactivate_identity(route.founder, "after-command")
                deactivation_ready.set()
                if not allow_deactivation_commit.wait(timeout=20):
                    raise AssertionError("deactivation commit gate timed out")

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                command_worker = start_worker(
                    lambda: service.activate_beta(command)
                )
                workers.append(command_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        command_worker.pid(),
                        monitor_pid,
                        query_fragment="pg_advisory_xact_lock",
                    )
                deactivation_worker = start_worker(deactivate_after_command)
                workers.append(deactivation_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        deactivation_worker.pid(),
                        command_worker.pid(),
                        query_fragment="FROM \"core_identity\"",
                    )
        except BaseException:
            allow_deactivation_commit.set()
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise

        if not deactivation_ready.wait(timeout=20):
            allow_deactivation_commit.set()
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            self.fail("deactivation did not resume after the command committed")
        command_result = command_worker.result(timeout=30)
        allow_deactivation_commit.set()
        deactivation_result = deactivation_worker.result(timeout=30)
        route.founder.refresh_from_db()
        route.founder.credential.refresh_from_db()
        self.assertEqual(command_result, "ok")
        self.assertEqual(deactivation_result, "ok")
        self.assertEqual(route.founder.access_state, Identity.AccessState.DEACTIVATED)
        self.assertFalse(route.founder.credential.is_active)
        self.assertEqual(route.founder.access_epoch, founder_epoch + 1)
        receipt = OrganismCommandReceipt.objects.get(
            actor=route.founder,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
            idempotency_key=command.idempotency_key,
        )
        authority_decision = AuthorityDecision.objects.get(
            decision_reference=receipt.authority_decision_reference,
        )
        self.assertEqual(authority_decision.actor_access_epoch, founder_epoch)
        self.assertNotEqual(_sqlstate(command_result), "40P01")
        self.assertNotEqual(_sqlstate(deactivation_result), "40P01")
        self._assert_workers_closed(workers)

        reverse_route = prepare_route(
            tag=f"lock1-governed-deactivation-reverse-{uuid4().hex[:8]}"
        )
        birth(reverse_route)
        reverse_service = OrganismEntitlementService(
            authority=reverse_route.birthing_service._authority,
            clock=database_now,
        )
        reverse_command = _election_command(
            reverse_route,
            reverse_route.founder,
            "deactivation-reverse",
        )
        deactivation_ready = Event()
        allow_deactivation_commit = Event()

        def hold_completed_deactivation():
            with transaction.atomic():
                _deactivate_identity(reverse_route.founder, "before-command")
                deactivation_ready.set()
                if not allow_deactivation_commit.wait(timeout=20):
                    raise AssertionError("reverse deactivation commit gate timed out")

        # PKT-CLK-3 CLK3-2 (L-4): the reverse LO's beta-domain rows before either worker.
        reverse_domain_before = _lo_beta_domain_snapshot(reverse_route.organism)
        self.assertEqual(len(reverse_domain_before[LivingOrganism._meta.db_table]), 1)
        deactivation_worker = start_worker(hold_completed_deactivation)
        workers = [deactivation_worker]
        try:
            self.assertTrue(deactivation_ready.wait(timeout=20))
            command_worker = start_worker(
                lambda: reverse_service.activate_beta(reverse_command)
            )
            workers.append(command_worker)
            with connection.cursor() as cursor:
                wait_until_blocked(
                    cursor,
                    command_worker.pid(),
                    deactivation_worker.pid(),
                    # PKT-CLK-3 CLK3-2 (L-4): activate_beta now admits first; the intake
                    # INSERT's foreign-key check on the actor's Identity row waits on the
                    # deactivation's lock, before the executor's actor lock is reached.
                    query_fragment="core_commandintake",
                )
        except BaseException:
            allow_deactivation_commit.set()
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise
        allow_deactivation_commit.set()
        deactivation_result = deactivation_worker.result(timeout=30)
        command_result = command_worker.result(timeout=30)
        self.assertEqual(deactivation_result, "ok")
        # The outcome predetermined from the contract: admission commits once the
        # deactivation has committed (the Identity row still exists), and the
        # executor's actor lock refuses the deactivated actor; nothing else is written.
        self.assertIsInstance(command_result, S015EntitlementUnavailable)
        self.assertEqual(str(command_result), "actor Identity is not active")
        self.assertNotIsInstance(command_result, S015CommandContended)
        self.assertNotEqual(_sqlstate(command_result), "40P01")
        self.assertEqual(
            CommandIntake.objects.filter(
                idempotency_key=reverse_command.idempotency_key,
            ).count(),
            1,
        )
        self.assertFalse(
            OrganismCommandReceipt.objects.filter(
                idempotency_key=reverse_command.idempotency_key,
            ).exists()
        )
        self.assertFalse(
            AuthorityDecision.objects.filter(
                request_reference=reverse_command.request_reference,
            ).exists()
        )
        # M1 (Advisor v0.2): no beta-domain record of the reverse LO was written.
        self.assertEqual(
            _lo_beta_domain_snapshot(reverse_route.organism),
            reverse_domain_before,
        )
        self._assert_workers_closed(workers)

    def test_w_lock_2a_open_membership_does_not_wait_on_applicants_identity(self):
        """Shows opening membership does not wait on the submitting applicant's identity row."""
        route = prepare_route(tag=f"lock1-open-submit-{uuid4().hex[:8]}")
        birth(route)
        applicant = _new_registered_identity(f"open-submit-{uuid4().hex}")
        submit_command = _submit_application_command(route, applicant, "open-submit")
        open_command = _open_membership_command(
            route,
            route.founder,
            applicant,
            "open-submit",
        )
        membership_service = OrganismMembershipService(
            authority=S015Authority(provider=_Lock1AllowProvider()),
            clock=database_now,
        )
        gate_key = command_mutex_key(
            actor_identity_id=applicant.identity_id,
            action=OrganismAction.SUBMIT_APPLICATION,
            idempotency_key=submit_command.idempotency_key,
        )
        intake_before = CommandIntake.objects.count()
        monitor_pid = backend_pid(connection)
        submit_worker = open_worker = None
        workers = []
        open_finished_before_release = False
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                submit_worker = start_worker(
                    lambda: route.application_service.submit_application(submit_command)
                )
                workers.append(submit_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        submit_worker.pid(),
                        monitor_pid,
                        query_fragment="pg_advisory_xact_lock",
                    )
                open_worker = start_worker(
                    lambda: membership_service.open_membership(open_command)
                )
                workers.append(open_worker)
                open_worker.thread.join(timeout=15)
                open_finished_before_release = not open_worker.thread.is_alive()
                if not open_finished_before_release:
                    with connection.cursor() as cursor:
                        wait_until_blocked(
                            cursor,
                            open_worker.pid(),
                            submit_worker.pid(),
                            query_fragment="FROM \"core_identity\"",
                        )
        except BaseException:
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise

        open_result = open_worker.result(timeout=30)
        submit_result = submit_worker.result(timeout=30)
        print(
            "W-LOCK-2a_OUTCOMES="
            + json.dumps(
                {
                    "open": _outcome(open_result),
                    "submit": _outcome(submit_result),
                    "open_finished_before_release": open_finished_before_release,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        self.assertTrue(open_finished_before_release)
        self.assertEqual(open_result, "ok")
        self.assertEqual(submit_result, "ok")
        self.assertEqual(CommandIntake.objects.count(), intake_before + 2)
        for command in (open_command, submit_command):
            self.assertEqual(
                CommandIntake.objects.filter(idempotency_key=command.idempotency_key).count(),
                1,
            )
        self.assertNotEqual(_sqlstate(open_result), "40P01")
        self.assertNotEqual(_sqlstate(submit_result), "40P01")
        self._assert_workers_closed(workers)

    def test_w_lock_2b_production_open_pair_has_authored_outcomes_and_clean_state(self):
        """Shows the production open pair returns authored outcomes and leaves state clean."""
        route = prepare_route(tag=f"lock1-open-pair-{uuid4().hex[:8]}")
        birth(route)
        second_actor = _new_registered_identity(f"open-pair-b-{uuid4().hex}")
        _add_active_member(route, second_actor, f"open-pair-b-{uuid4().hex}")
        third_identity = _new_identity(f"open-pair-c-{uuid4().hex}")
        first_command = _open_membership_command(
            route,
            route.founder,
            second_actor,
            "open-pair-a",
        )
        second_command = _open_membership_command(
            route,
            second_actor,
            third_identity,
            "open-pair-b",
        )
        service = OrganismMembershipService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=database_now,
        )
        gate_key = command_mutex_key(
            actor_identity_id=second_actor.identity_id,
            action=OrganismAction.TRANSITION_MEMBERSHIP,
            idempotency_key=second_command.idempotency_key,
        )
        before = (
            _membership_tables_snapshot(),
            OrganismMembership.objects.filter(living_organism=route.organism).count(),
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
        )
        intake_before = CommandIntake.objects.count()
        monitor_pid = backend_pid(connection)
        first_worker = second_worker = None
        workers = []
        first_finished_before_release = False
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                second_worker = start_worker(
                    lambda: service.open_membership(second_command)
                )
                workers.append(second_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        second_worker.pid(),
                        monitor_pid,
                        query_fragment="pg_advisory_xact_lock",
                    )
                first_worker = start_worker(
                    lambda: service.open_membership(first_command)
                )
                workers.append(first_worker)
                first_worker.thread.join(timeout=15)
                first_finished_before_release = not first_worker.thread.is_alive()
                if not first_finished_before_release:
                    with connection.cursor() as cursor:
                        wait_until_blocked(
                            cursor,
                            first_worker.pid(),
                            second_worker.pid(),
                            query_fragment="FROM \"core_identity\"",
                        )
        except BaseException:
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise

        first_result = first_worker.result(timeout=30)
        second_result = second_worker.result(timeout=30)
        after = (
            _membership_tables_snapshot(),
            OrganismMembership.objects.filter(living_organism=route.organism).count(),
            OrganismEntitlement.objects.filter(
                living_organism=route.organism,
                basis="BETA_OPT_IN",
            ).count(),
        )
        print(
            "W-LOCK-2b_OUTCOMES="
            + json.dumps(
                {
                    "first": _outcome(first_result),
                    "second": _outcome(second_result),
                    "finished_before_release": first_finished_before_release,
                    "membership_and_grant_delta": (
                        after[1] - before[1],
                        after[2] - before[2],
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        self.assertTrue(first_finished_before_release)
        self.assertEqual(
            _outcome(first_result)["refusal_code"],
            "C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED",
        )
        self.assertEqual(
            _outcome(second_result)["refusal_code"],
            "C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED",
        )
        self.assertNotEqual(_sqlstate(first_result), "40P01")
        self.assertNotEqual(_sqlstate(second_result), "40P01")
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1] - before[1], 0)
        self.assertEqual(after[2] - before[2], 0)
        # Both refusals follow admission, so each command keeps exactly its intake row.
        self.assertEqual(CommandIntake.objects.count(), intake_before + 2)
        for command in (first_command, second_command):
            self.assertEqual(
                CommandIntake.objects.filter(idempotency_key=command.idempotency_key).count(),
                1,
            )
        self._assert_workers_closed(workers)

    def test_w_lock_3_seat_decision_does_not_lock_requesters_identity(self):
        """Shows a seat decision locks the request row without locking the requester's identity."""
        tag = f"lock1-seat-decision-{uuid4().hex[:8]}"
        effective_from = database_now()
        arrangement = governance_arrangement_body(
            seat_id="founder",
            effective_from=effective_from,
        )
        arrangement["seats"].append(
            dict(arrangement["seats"][0], seat_id="secondary")
        )
        governance_route = prepare_governance_route(
            tag=tag,
            body=arrangement,
        )
        advance_to_birthing(governance_route.route, tag=tag)
        birth(governance_route.route)
        requester = _add_authorized_coordinator(
            governance_route.route,
            f"seat-requester-{uuid4().hex}",
        )
        request_key = f"lock1:seat-request:key:{tag}"
        request_result = governance_route.service.request_governance_seat(
            RequestGovernanceSeatCommand(
                credential=requester.credential,
                living_organism_uuid=governance_route.organism.organism_id,
                circle_uuid=governance_route.circle_uuid,
                seat_rule_uuid=governance_route.rule_uuid,
                seat_id="secondary",
                request_reference=f"lock1:seat-request:{tag}",
                idempotency_key=request_key,
                effective_at=database_now(),
            )
        )
        self.assertFalse(CommandIntake.objects.filter(idempotency_key=request_key).exists())
        entitlement_service = OrganismEntitlementService(
            authority=governance_route.route.birthing_service._authority,
            clock=database_now,
        )
        existing_grant = _election_command(
            governance_route.route,
            requester,
            "seat-decision-existing",
        )
        entitlement_service.activate_beta(existing_grant)
        # PKT-CLK-3 CLK3-2: ACTIVATE_BETA is admitted; exactly one intake row per key.
        self.assertEqual(
            CommandIntake.objects.filter(idempotency_key=existing_grant.idempotency_key).count(),
            1,
        )
        second_command = _election_command(
            governance_route.route,
            requester,
            "seat-decision-second",
        )
        decision_command = DecideGovernanceSeatCommand(
            credential=governance_route.founder.credential,
            living_organism_uuid=governance_route.organism.organism_id,
            request_reference=f"lock1:seat-decision:{tag}",
            idempotency_key=f"lock1:seat-decision:key:{tag}",
            outcome="ACCEPT",
            request_uuid=request_result.request_uuid,
            effective_at=database_now(),
        )
        gate_key = command_mutex_key(
            actor_identity_id=requester.identity_id,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            idempotency_key=second_command.idempotency_key,
        )
        monitor_pid = backend_pid(connection)
        second_worker = decision_worker = None
        workers = []
        decision_finished_before_release = False
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                second_worker = start_worker(
                    lambda: entitlement_service.activate_beta(second_command)
                )
                workers.append(second_worker)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        second_worker.pid(),
                        monitor_pid,
                        query_fragment="pg_advisory_xact_lock",
                    )
                decision_worker = start_worker(
                    lambda: governance_route.service.decide_governance_seat(
                        decision_command
                    )
                )
                workers.append(decision_worker)
                decision_worker.thread.join(timeout=15)
                decision_finished_before_release = not decision_worker.thread.is_alive()
                if not decision_finished_before_release:
                    with connection.cursor() as cursor:
                        wait_until_blocked(
                            cursor,
                            decision_worker.pid(),
                            second_worker.pid(),
                            query_fragment="FROM \"core_identity\"",
                        )
        except BaseException:
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise

        decision_result = decision_worker.result(timeout=30)
        second_result = second_worker.result(timeout=30)
        print(
            "W-LOCK-3_OUTCOMES="
            + json.dumps(
                {
                    "decision": _outcome(decision_result),
                    "requester_command": _outcome(second_result),
                    "decision_finished_before_release": decision_finished_before_release,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        self.assertTrue(decision_finished_before_release)
        self.assertEqual(decision_result, "ok")
        self.assertIsInstance(second_result, Exception)
        self.assertNotEqual(_sqlstate(decision_result), "40P01")
        self.assertNotEqual(_sqlstate(second_result), "40P01")
        self.assertEqual(
            CommandIntake.objects.filter(idempotency_key=second_command.idempotency_key).count(),
            1,
        )
        self._assert_workers_closed(workers)

    def test_w_lock_t1_deadlock_translates_only_after_command_rollback(self):
        """Shows an outermost deadlock becomes T-LOCK-1 only after command rollback."""
        route = prepare_route(tag=f"lock1-t1-{uuid4().hex[:8]}")
        birth(route)
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        command = _election_command(route, route.founder, "t1")
        intake_before = CommandIntake.objects.count()
        founder_membership = OrganismMembership.objects.get(
            living_organism=route.organism,
            identity=route.founder,
        )
        before = _lock1_state_snapshot()
        membership_locked = Event()
        request_organism_lock = Event()
        monitor_pid = backend_pid(connection)

        def hold_membership_then_request_organism():
            with connection.cursor() as cursor:
                cursor.execute("SET deadlock_timeout = '10s'")
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM core_organismmembership WHERE id = %s FOR UPDATE",
                        [founder_membership.pk],
                    )
                membership_locked.set()
                if not request_organism_lock.wait(timeout=20):
                    raise AssertionError("T1 harness never requested the organism lock")
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM core_livingorganism WHERE id = %s FOR UPDATE",
                        [route.organism.pk],
                    )

        def command_with_fast_deadlock_detection():
            with connection.cursor() as cursor:
                cursor.execute("SET deadlock_timeout = '100ms'")
            try:
                return service.activate_beta(command)
            except S015CommandContended:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() != (1,):
                        raise AssertionError("command connection did not recover after rollback")
                raise

        holder_worker = command_worker = None
        workers = []
        try:
            holder_worker = start_worker(hold_membership_then_request_organism)
            workers.append(holder_worker)
            self.assertTrue(membership_locked.wait(timeout=10))
            command_worker = start_worker(command_with_fast_deadlock_detection)
            workers.append(command_worker)
            with connection.cursor() as cursor:
                wait_until_blocked(
                    cursor,
                    command_worker.pid(),
                    holder_worker.pid(),
                    query_fragment="FROM \"core_organismmembership\"",
                )
            request_organism_lock.set()
            command_worker.thread.join(timeout=15)
        except BaseException:
            active = [worker for worker in workers if worker.thread.is_alive()]
            if active:
                cleanup_workers(active)
            raise

        command_result = command_worker.result(timeout=30)
        holder_result = holder_worker.result(timeout=30)
        after = _lock1_state_snapshot()
        print(
            "W-LOCK-T1_OUTCOMES="
            + json.dumps(
                {
                    "command": _outcome(command_result),
                    "harness": _outcome(holder_result),
                    "state_unchanged": before == after,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        self.assertIsInstance(command_result, S015CommandContended)
        # PKT-CLK-3 CLK3-2 (R-FIX-01): admitted, so the contention names the intake's T.
        self.assertEqual(CommandIntake.objects.count(), intake_before + 1)
        intake = CommandIntake.objects.get(
            actor=route.founder,
            entry="ACTIVATE_BETA",
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(command_result.received_at, intake.received_at)
        self.assertEqual(
            str(command_result),
            "Your request could not be completed because it conflicted with another database operation. "
            "This attempt wrote no governed record; INTEVIA kept a record that your request was received "
            f"at {canonical_timestamp(intake.received_at)}. Please wait a moment before sending the same "
            "request again; it is judged as of that time. If this keeps happening, note the time it "
            "occurred; it needs to be checked.",
        )
        self.assertEqual(command_result.diagnostic_sqlstate, "40P01")
        self.assertIsInstance(command_result.__cause__, OperationalError)
        self.assertEqual(
            getattr(command_result.__cause__.__cause__, "sqlstate", None),
            "40P01",
        )
        self.assertEqual(holder_result, "ok")
        self.assertEqual(before, after)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone(), (1,))
        self._assert_workers_closed(workers)

    def test_w_lock_t2_lock_timeout_translates_as_contention(self):
        """Shows a lock timeout translates to contention while preserving SQLSTATE and cause."""
        route = prepare_route(tag=f"lock1-t2-{uuid4().hex[:8]}")
        birth(route)
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        command = _election_command(route, route.founder, "t2")
        before = _lock1_state_snapshot()
        monitor_pid = backend_pid(connection)

        def command_with_lock_timeout():
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '200ms'")
            return service.activate_beta(command)

        worker = None
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM core_livingorganism WHERE id = %s FOR UPDATE",
                        [route.organism.pk],
                    )
                worker = start_worker(command_with_lock_timeout)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        worker.pid(),
                        monitor_pid,
                        query_fragment="s015_lock_organisms",
                    )
                worker.thread.join(timeout=10)
        except BaseException:
            if worker is not None and worker.thread.is_alive():
                cleanup_workers([worker])
            raise

        result = worker.result(timeout=30)
        after = _lock1_state_snapshot()
        self.assertIsInstance(result, S015CommandContended)
        self.assertEqual(result.diagnostic_sqlstate, "55P03")
        self.assertIsInstance(result.__cause__, OperationalError)
        self.assertEqual(
            getattr(result.__cause__.__cause__, "sqlstate", None),
            "55P03",
        )
        self.assertEqual(before, after)
        self._assert_workers_closed([worker])

    def test_w_lock_t3_serialization_conflict_translates_as_contention(self):
        """Shows a serialization conflict translates to the authored contention outcome."""
        route = prepare_route(tag=f"lock1-t3-{uuid4().hex[:8]}")
        birth(route)
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        command = _election_command(route, route.founder, "t3")
        before = _lock1_state_snapshot()
        gate_key = command_mutex_key(
            actor_identity_id=route.founder.identity_id,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
            idempotency_key=command.idempotency_key,
        )
        monitor_pid = backend_pid(connection)

        def command_at_repeatable_read():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET default_transaction_isolation = 'repeatable read'"
                )
            return service.activate_beta(command)

        worker = None
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [gate_key])
                worker = start_worker(command_at_repeatable_read)
                with connection.cursor() as cursor:
                    wait_until_blocked(
                        cursor,
                        worker.pid(),
                        monitor_pid,
                        query_fragment="pg_advisory_xact_lock",
                    )
                    cursor.execute(
                        "UPDATE core_livingorganism SET created_at = created_at WHERE id = %s",
                        [route.organism.pk],
                    )
        except BaseException:
            if worker is not None and worker.thread.is_alive():
                cleanup_workers([worker])
            raise

        result = worker.result(timeout=30)
        after = _lock1_state_snapshot()
        self.assertIsInstance(result, S015CommandContended)
        self.assertEqual(result.diagnostic_sqlstate, "40001")
        self.assertIsInstance(result.__cause__, OperationalError)
        self.assertEqual(
            getattr(result.__cause__.__cause__, "sqlstate", None),
            "40001",
        )
        self.assertEqual(before, after)
        self._assert_workers_closed([worker])

    def test_w_lock_t4_unrelated_operational_error_stays_raw(self):
        """Shows an unrelated OperationalError stays raw and leaves state unchanged."""
        route = prepare_route(tag=f"lock1-t4-{uuid4().hex[:8]}")
        birth(route)
        service = OrganismEntitlementService(
            authority=route.birthing_service._authority,
            clock=database_now,
        )
        command = _election_command(route, route.founder, "t4")
        before = _lock1_state_snapshot()
        execute_activate_beta = service._execute_activate_beta

        def execute_with_statement_timeout(*args, **kwargs):
            # PKT-CLK-3 CLK3-2 (W-LOCK-T4): since G1, activate_beta admits the request
            # before its executor runs. The 1 ms statement timeout is set at the
            # executor's entry, after admission has committed, so the unrelated
            # OperationalError arises inside the executor as this witness intends,
            # never in admission (attempt 74, 24 Oct 2026: the timeout hit admission).
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '1ms'")
            return execute_activate_beta(*args, **kwargs)

        def command_with_statement_timeout():
            with patch.object(
                service,
                "_execute_activate_beta",
                side_effect=execute_with_statement_timeout,
            ):
                return service.activate_beta(command)

        worker = start_worker(command_with_statement_timeout)
        result = worker.result(timeout=30)
        after = _lock1_state_snapshot()
        print(
            "W-LOCK-T4_OUTCOME="
            + json.dumps(_outcome(result), sort_keys=True),
            flush=True,
        )
        self.assertIsInstance(result, OperationalError)
        self.assertNotIsInstance(result, S015CommandContended)
        self.assertEqual(_sqlstate(result), "57014")
        self.assertEqual(before, after)
        self.assertEqual(
            CommandIntake.objects.filter(idempotency_key=command.idempotency_key).count(),
            1,
        )
        self._assert_workers_closed([worker])

    def test_w_lock_t5_nested_outer_transaction_keeps_operational_error_raw(self):
        """Shows an outer atomic block prevents translation of a nested OperationalError."""
        route = prepare_route(tag=f"lock1-t5-{uuid4().hex[:8]}")
        birth(route)
        target = _new_identity(f"t5-target-{uuid4().hex}")
        service = OrganismMembershipService(
            authority=S015Authority(provider=_Lock1AllowProvider()),
            clock=database_now,
        )
        command = _open_membership_command(route, route.founder, target, "t5")
        from src.intevia.services.command_intake import ActorRef, BoundRequest, IntakeEntry

        bound = BoundRequest.for_test(
            IntakeEntry.OPEN_MEMBERSHIP,
            command,
            received_at=database_now(),
            actor=ActorRef(
                pk=route.founder.pk,
                identity_id=route.founder.identity_id,
                access_epoch=route.founder.access_epoch,
            ),
        )

        class _SyntheticPostgresError(Exception):
            sqlstate = "40P01"

        def raise_operational_error(*args, **kwargs):
            try:
                raise _SyntheticPostgresError()
            except _SyntheticPostgresError as driver_error:
                raise OperationalError("simulated PostgreSQL deadlock") from driver_error

        with transaction.atomic():
            with patch.object(
                service,
                "_execute_open",
                side_effect=raise_operational_error,
            ):
                with self.assertRaises(OperationalError) as raised:
                    service._execute_open_membership(command, bound)
            self.assertNotIsInstance(raised.exception, S015CommandContended)
            self.assertEqual(
                getattr(raised.exception.__cause__, "sqlstate", None),
                "40P01",
            )
            self.assertTrue(connection.in_atomic_block)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                self.assertEqual(cursor.fetchone(), (1,))
