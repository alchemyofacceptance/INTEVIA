"""Shared fixtures and evidence helpers for SE entitlement tests."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import unittest

from django.db import connections, transaction
from core.models import OrganismEntitlementPolicyText
from src.intevia.services.organism_authority import S015AuthorityDenied


EVENT_LESS_ENTITLEMENT_DECLARATIONS: list[tuple[str, str]] = []

TEST_POLICY_TEXT = "TEST FIXTURE — beta offer policy text, not for production"
TEST_POLICY_VERSION = 1
TEST_POLICY_SHA256 = hashlib.sha256(TEST_POLICY_TEXT.encode("utf-8")).hexdigest()


class EventLessEntitlementRestorationError(RuntimeError):
    """Expose a guard-restoration failure without losing a fixture exception."""

    def __init__(
        self,
        *,
        using: str,
        fixture_error: BaseException | None,
        restoration_error: BaseException,
    ) -> None:
        self.using = using
        self.fixture_error = fixture_error
        self.restoration_error = restoration_error
        fixture_result = (
            "after fixture failure"
            if fixture_error is not None
            else "after fixture completion"
        )
        super().__init__(
            "failed to restore test-only trigger "
            "s015_organismentitlement_granted_guard "
            f"for database alias {using!r} {fixture_result}"
        )


def _current_test_id() -> str:
    frame = inspect.currentframe()
    if frame is None:
        raise RuntimeError("the test declaration has no active frame")
    frame = frame.f_back
    try:
        while frame is not None:
            case = frame.f_locals.get("self")
            if isinstance(case, unittest.TestCase):
                return case.id()
            frame = frame.f_back
    finally:
        del frame
    raise RuntimeError("event-less entitlement declaration is outside a test case")


@contextmanager
def declared_event_less_entitlement_rows(reason: str, *, using: str = "default"):
    """Create event-less test rows during exclusive setup on a disposable DB.

    Disabling the trigger affects the selected physical database, including any
    aliases or mirrors that target it. Callers must finish this fixture setup
    before other threads, workers, or connections can use that database. The
    trigger is disabled in autocommit, the fixture runs atomically, and the
    trigger is restored in autocommit after commit or rollback. If fixture
    execution fails and restoration succeeds, the original exception is
    re-raised unchanged. A restoration failure raises
    EventLessEntitlementRestorationError, retaining both errors. Process death
    can prevent restoration; discard that development database before another
    run. This test-only helper does not change K3-PROD.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("an event-less entitlement declaration needs a reason")

    active_connection = connections[using]
    with active_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_get_userbyid(relation.relowner), current_user
              FROM pg_class relation
              JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = 'public'
               AND relation.relname = 'core_organismentitlement'
               AND relation.relkind = 'r'
            """
        )
        ownership = cursor.fetchone()
    if ownership is None or ownership[0] != ownership[1]:
        raise AssertionError(
            "the test connection role must own core_organismentitlement "
            "to declare event-less rows"
        )

    if active_connection.in_atomic_block or not active_connection.get_autocommit():
        raise RuntimeError(
            "event-less entitlement declarations must run outside a transaction"
        )

    test_id = _current_test_id()
    trigger_disabled = False

    def restore_guard(fixture_error: BaseException | None) -> None:
        try:
            with active_connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE public.core_organismentitlement "
                    "ENABLE TRIGGER s015_organismentitlement_granted_guard"
                )
        except BaseException as restoration_error:
            raise EventLessEntitlementRestorationError(
                using=using,
                fixture_error=fixture_error,
                restoration_error=restoration_error,
            ) from restoration_error

    try:
        with active_connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE public.core_organismentitlement "
                "DISABLE TRIGGER s015_organismentitlement_granted_guard"
            )
            trigger_disabled = True
        EVENT_LESS_ENTITLEMENT_DECLARATIONS.append((test_id, reason))
        with transaction.atomic(using=using):
            yield
    except BaseException as fixture_error:
        if trigger_disabled:
            restore_guard(fixture_error)
        raise
    else:
        if trigger_disabled:
            restore_guard(None)


@contextmanager
def disclose_authority_refusal_code():
    """Add refusal-code notes to the original denial without replacing it."""
    try:
        yield
    except S015AuthorityDenied as exc:
        # HG_DECISION_SE_ATTEMPT_14_REPAIR_AND_MODE_20261012 requires the original exception and traceback.
        exc.add_note(
            f"refusal_code={exc.refusal_code!r}; "
            f"disclosed_refusal_code={exc.disclosed_refusal_code!r}"
        )
        raise


def ensure_test_policy_text() -> tuple[int, str]:
    record, _ = OrganismEntitlementPolicyText.objects.get_or_create(
        policy_version=TEST_POLICY_VERSION,
        defaults={
            "accepted_text": TEST_POLICY_TEXT,
            "text_sha256": TEST_POLICY_SHA256,
        },
    )
    if (
        record.accepted_text != TEST_POLICY_TEXT
        or record.text_sha256 != TEST_POLICY_SHA256
    ):
        raise RuntimeError("the labelled SE test policy fixture conflicts with its record")
    return record.policy_version, record.text_sha256


def add_test_coordinator(route, *, tag: str):
    """W-K2.C1: create a second active Coordinator through the supported role service."""
    import uuid

    from django.db import connection, transaction

    from core.models import Identity, OrganismRoleDefinition
    from src.intevia.services.organism_application_authority import (
        ProductionApplicationAuthorityProvider,
    )
    from src.intevia.services.organism_authority import S015Authority
    from src.intevia.services.organism_role_service import (
        OrganismRoleService,
        ProposeAssignmentCommand,
        TransitionAssignmentCommand,
    )
    from tests.s015_0021_support import (
        append_event,
        database_now,
        empty_fp,
        refresh,
        seed_identity,
    )

    membership_at = database_now()
    membership_uuid = uuid.uuid4()
    with transaction.atomic(), connection.cursor() as cursor:
        identity_id = seed_identity(cursor, f"sek2-coordinator-{tag}")
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
                identity_id,
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
            identity_id,
            route.organism.founding_authority_basis_id,
            f"sek2:membership:{tag}",
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

    identity = Identity.objects.select_related("credential").get(pk=identity_id)
    role = OrganismRoleDefinition.objects.get(
        living_organism=route.organism,
        code="COORDINATOR",
    )
    service = OrganismRoleService(
        authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
        clock=database_now,
    )
    proposal_at = database_now()
    proposal = service.propose_assignment(
        ProposeAssignmentCommand(
            credential=identity.credential,
            request_reference=f"sek2:{tag}:propose",
            idempotency_key=f"sek2:{tag}:propose:key",
            occurred_at=membership_at,
            effective_at=membership_at,
            received_at=proposal_at,
            membership_uuid=membership_uuid,
            role_definition_uuid=role.role_uuid,
            effective_until_state="NONE",
            effective_until=None,
        )
    )
    service.transition_assignment(
        TransitionAssignmentCommand(
            credential=route.founder.credential,
            request_reference=f"sek2:{tag}:assign",
            idempotency_key=f"sek2:{tag}:assign:key",
            occurred_at=membership_at,
            effective_at=membership_at,
            received_at=database_now(),
            assignment_uuid=proposal.assignment_uuid,
            action="ASSIGN",
            resulting_state="ACTIVE",
            effective_until_state="NONE",
            effective_until=None,
        )
    )
    return identity


class TestFixturePaidAvailabilitySource:
    def __init__(self, transition_reference: str | None) -> None:
        self._transition_reference = transition_reference

    def paid_availability_transition_reference(
        self,
        _living_organism_uuid,
        _effective_at,
        _known_at,
    ) -> str | None:
        return self._transition_reference
