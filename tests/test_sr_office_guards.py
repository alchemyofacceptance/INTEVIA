"""SR chunk C2 - the widened office guards: seed tests under the six test admission rules.

Migration 0052 widens two database guards on the office chain
(core_contextualroleassignmentstateevent) from COORDINATOR to every LO-scoped office code:
s2b_guard_office_attribution (0036 as 0044 installed it) and s2b_guard_assign_consent (0037).
Each test below names one claim, the obligation clause it witnesses (the v1.0 obligation list
v1.1, approved 29 Oct 2026), the layer it asserts, and - for a refusal - the mutation entry in
verification/mutations.py under which it must fail by assertion (ADMISSION class; qualified once
at admission by intent; the current route still executes every registry entry).

Design: SR_COMMISSION_OTHER_OFFICES_v0_7.md section E class B items 1-7 and ruling 4; the plan
row SR-C2 (LD_RETURN_SR_STAGE0_v1_0.md section 6, amendments v1.1 and v1.2); the C2 commission
v1.0 (US015-B-35) as amended by its base addendum v1.2 (US015-B-36). Human Governor: Carmian Owen.

How the lawful office chain is built here. The real routes still refuse every non-COORDINATOR
office act (the provider's DENIED; the role service's IL-8 - chunks C4 and C5 open them), so the
chain is written by direct inserts that reach the real safeguards: the nomination (PROPOSE, the
proposer's own act), the nominee's CONSENT written through 0037's consent guard, and the LO's
ASSIGN with a QUALIFIED authority decision produced by the production authority service
(S015Authority.qualify through a test provider, so that 0034's decision guard recomputes the
fingerprint and the s015d1 reference). Every refusal test is the lawful chain with exactly one
unlawful difference, so that removing the named safeguard admits the write and the test fails
at its own assertion (admission rule 3). Fixture definitions are pre-SR-shaped rows under the C1
latitude (sr_definition_guard_suspended): definition governance is C1's and C3's, not this
chunk's. Test 07 uses the office-attribution latitude indirectly through found_root
for historical setup only; its challenged nomination occurs after restoration.

Expected raw types with psycopg 3.3.6: count(*) -> int; text columns and
pg_proc.prosrc -> str; uuid columns -> uuid.UUID; boolean -> bool; timestamptz -> datetime
(aware); bigint id -> int; to_jsonb(row)::text -> str. The amended candidate awaits
ME PostgreSQL 17 verification and CI. The LD's PG16 logs cover the previous candidate only.
"""

import datetime as dt
import threading
import uuid
from dataclasses import asdict
from importlib import import_module
from queue import Queue
from threading import Barrier
from unittest import skipUnless

from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from core.models import (
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    OfficeAppointmentConsent,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    S015Authority,
    S015PostFoundingAuthorityResponse,
    authority_target_fingerprint,
    canonical_json_bytes,
)
from src.intevia.services.organism_command_support import _authority_request
from tests.s015_0021_support import (
    append_event,
    current_applied_core_head,
    database_now,
    drop_unset_check,
    empty_fp,
    first_line,
    found_nonroot,
    found_root,
    fresh,
    migrate_to_target_with_restore,
    refresh,
    seed_identity,
    sr_definition_guard_suspended,
    u,
)


POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "the office guards are PostgreSQL triggers")

HEAD_0051 = "0051_sr_role_definitions"
HEAD_0052 = "0052_sr_office_guard_widening"
EVENT_TABLE = "core_contextualroleassignmentstateevent"
TARGET_SCHEMA = "intevia.s015.organism-role-target.v1"

REFUSED_ATTRIBUTION = "S2B office attribution refused: LO decision attribution required"
REFUSED_CONSENT = "S2B assignment consent refused: current consent absent or withdrawn"
REFUSED_HELD = "S2B office attribution refused: office already held"
REFUSED_ENTITLEMENT = "S2B office attribution refused: custom role entitlement absent at receipt"
REFUSED_SUPERSEDED = "S2B office attribution refused: superseded definition"
REFUSED_RESERVED = "S2B office attribution refused: reserved root office"
REFUSED_FOUNDER = "C_FOUNDER_PROTECTED"


# ------------------------------------------------------------------ the test provider

class _ExactProvider:
    """Qualifies every request; renders the facts the two guards read from the decision
    (0037:191-210; 0036/0044 installed 142-163). A test stand-in for the organism authority
    provider interface only - the production provider still answers DENIED for these acts."""

    def evaluate_organism_authority(self, request):
        target = request.target or {}
        if request.action is OrganismAction.RECORD_OFFICE_CONSENT:
            assignment = ContextualRoleAssignment.objects.get(assignment_uuid=target["assignment_uuid"])
            facts = {
                "a": str(assignment.assignment_uuid),
                "g": str(assignment.role_definition.role_uuid),
                "k": target["event_action"],
                "p": str(target["proposal_event_uuid"]),
                "u": str(assignment.membership.membership_uuid),
            }
        elif request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE and target.get("event_action") in (
            "ASSIGN", "END", "REASSIGN",
        ):
            assignment = ContextualRoleAssignment.objects.get(assignment_uuid=target["assignment_uuid"])
            proposal = ContextualRoleAssignmentStateEvent.objects.filter(
                assignment=assignment, sequence=1, action="PROPOSE",
            ).first()
            consent = OfficeAppointmentConsent.objects.filter(
                assignment=assignment, action="CONSENT",
            ).order_by("-ordinal").first()
            facts = {
                "a": str(assignment.assignment_uuid),
                "g": str(assignment.role_definition.role_uuid),
                "k": target["event_action"],
            }
            if target["event_action"] == "ASSIGN":
                facts["p"] = str(proposal.event_uuid) if proposal else str(uuid.UUID(int=0))
                facts["u"] = "c:" + (str(consent.consent_uuid) if consent else str(uuid.UUID(int=0)))
        else:
            return S015PostFoundingAuthorityResponse(**asdict(request), authority_reference="sr-c2:fixture")
        return S015PostFoundingAuthorityResponse(
            **asdict(request),
            authority_reference="s015a1:" + canonical_json_bytes(facts).decode("utf-8"),
        )


def _qualified_decision(*, actor_id, membership_id, action, target, request_tag, at, living_organism_uuid,
                        assignment_uuid=None):
    """One QUALIFIED decision row for `target`, written by the production authority service inside
    the caller's atomic block. Returns (authority_decision_reference, authority_reference)."""
    actor = Identity.objects.get(pk=actor_id)
    actor_membership = OrganismMembership.objects.select_related("head_transition").get(pk=membership_id)
    decision = S015Authority(provider=_ExactProvider(), database_alias="default").qualify(_authority_request(
        database_alias="default",
        actor=actor,
        actor_membership=actor_membership,
        actor_role_head_refs=(),
        action=action,
        request_reference=f"sr-c2:request:{request_tag}",
        idempotency_key=f"sr-c2:idem:{request_tag}",
        received_at=at,
        target_fingerprint=authority_target_fingerprint(target),
        effective_at=at,
        target=target,
        living_organism_id=uuid.UUID(str(living_organism_uuid)),
        assignment_uuid=uuid.UUID(str(assignment_uuid)) if assignment_uuid else None,
    ))
    return decision.authority_decision_reference, decision.authority_reference


# ------------------------------------------------------------------ fixture writers

def _add_member(cur, ids, tag, at):
    """A second ACTIVE member of the LO with its own identity and a sequence-1 JOIN chain
    (a test-only fixture; the landed admission routes are S3's)."""
    member_identity = seed_identity(cur, tag)
    m_uuid = u(f"membership:{tag}")
    cur.execute(
        "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, "
        "state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
        "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
        (m_uuid, at, at, empty_fp(cur, "core_organismmembership", m_uuid), member_identity, ids["lo"]),
    )
    membership_id = cur.fetchone()[0]
    append_event(
        cur, "core_organismmembershiptransition", membership_id, 1, "JOIN", None, "ACTIVE",
        member_identity, ids["basis"], tag, extra={"reason_class": "TEST_ONLY_ADMISSION"},
        eff=at, occ=at, rec=at,
    )
    refresh(cur, "core_organismmembership", membership_id, at)
    return membership_id, member_identity, uuid.UUID(m_uuid)


def _add_definition(cur, ids, code, *, kind=None, scope="LIVING_ORGANISM", version=1, active=True):
    """A pre-SR-shaped office definition row under the C1 latitude: C1's sr_00 would require the
    SR attribution a C3 command supplies; the office-chain guards this module examines read only
    code, scope, active and - for the CUSTOM entitlement check alone - kind. kind is left NULL
    (the pre-SR shape, as the 0021 fixtures write it) except where a test needs 'CUSTOM'; a
    BOARD_OFFICE kind would owe its OFFICE_BASIS citation to C1's deferred sr_30, which is C3's
    command's to write."""
    r_uuid = u(f"role:{ids['lo']}:{scope}:{code}:{version}")
    with sr_definition_guard_suspended(cur):
        cur.execute(
            "INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, "
            "living_organism_id, kind) VALUES (%s, %s, %s, %s, %s, now(), %s, %s) RETURNING id",
            (r_uuid, code, scope, version, active, ids["lo"], kind),
        )
        definition_id = cur.fetchone()[0]
    return definition_id, uuid.UUID(r_uuid)


def _anchor(cur, membership_id, definition_id, at, *, state="PROPOSED", circle_id=None):
    a_uuid = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO core_contextualroleassignment (assignment_uuid, state, created_at, current_state_effective_at, "
        "state_known_at, state_source_event_set_fingerprint, membership_id, role_definition_id, circle_id) "
        "VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s) RETURNING id",
        (a_uuid, state, at, at, empty_fp(cur, "core_contextualroleassignment", a_uuid), membership_id, definition_id, circle_id),
    )
    return cur.fetchone()[0], uuid.UUID(a_uuid)


def _lo_uuid(cur, lo_id):
    cur.execute("SELECT organism_id FROM core_livingorganism WHERE id = %s", (lo_id,))
    return cur.fetchone()[0]


def _propose(cur, ids, *, proposer_id, proposer_membership_id, nominee_membership_id, nominee_membership_uuid,
             definition_id, definition_uuid, at, tag, decision=True):
    """The nomination: a PROPOSED anchor and the proposer's own PROPOSE event (sequence 1), with a
    QUALIFIED decision whose target names the organism, the nominee's membership and the
    definition (installed decision check 142-163). Returns (assignment_id, assignment_uuid, proposal_id)."""
    lo_uuid = _lo_uuid(cur, ids["lo"])
    assignment_id, assignment_uuid = _anchor(cur, nominee_membership_id, definition_id, at)
    overrides = {}
    if decision:
        target = {
            "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            "event_action": "PROPOSE",
            "living_organism_id": str(lo_uuid),
            "membership_uuid": str(nominee_membership_uuid),
            "resulting_state": "PROPOSED",
            "role_definition_uuid": str(definition_uuid),
            "schema": TARGET_SCHEMA,
        }
        reference, authority_reference = _qualified_decision(
            actor_id=proposer_id, membership_id=proposer_membership_id,
            action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE, target=target,
            request_tag=f"{tag}:propose", at=at, living_organism_uuid=lo_uuid,
        )
        overrides = {"authority_decision_reference": reference, "evidence_reference": authority_reference}
    proposal_id, _recorded = append_event(
        cur, EVENT_TABLE, assignment_id, 1, "PROPOSE", None, "PROPOSED", proposer_id, ids["basis"],
        f"{tag}:propose", eff=at, occ=at, rec=at, overrides=overrides,
    )
    refresh(cur, "core_contextualroleassignment", assignment_id, at)
    return assignment_id, assignment_uuid, proposal_id


def _consent(cur, ids, *, nominee_identity_id, nominee_membership_id, nominee_membership_uuid, assignment_id,
             assignment_uuid, proposal_id, at, tag, action="CONSENT", ordinal=1):
    """Write a nominee consent-history act through the real 0037 guard.

    The supplied action/ordinal also permits a lawful next WITHDRAW to challenge
    the late-write prohibition after appointment; all other prerequisites hold.
    """
    cur.execute("SELECT event_uuid FROM public.core_contextualroleassignmentstateevent WHERE id = %s", (proposal_id,))
    proposal_uuid = cur.fetchone()[0]
    cur.execute("SELECT access_epoch FROM public.core_identity WHERE id = %s", (nominee_identity_id,))
    epoch = cur.fetchone()[0]
    lo_uuid = _lo_uuid(cur, ids["lo"])
    target = {
        "action": OrganismAction.RECORD_OFFICE_CONSENT.value,
        "assignment_uuid": str(assignment_uuid),
        "event_action": action,
        "living_organism_id": str(lo_uuid),
        "membership_uuid": str(nominee_membership_uuid),
        "proposal_event_uuid": str(proposal_uuid),
        "schema": TARGET_SCHEMA,
    }
    reference, _authority_reference = _qualified_decision(
        actor_id=nominee_identity_id, membership_id=nominee_membership_id,
        action=OrganismAction.RECORD_OFFICE_CONSENT, target=target,
        request_tag=f"{tag}:consent", at=at, living_organism_uuid=lo_uuid, assignment_uuid=assignment_uuid,
    )
    consent_uuid = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO core_officeappointmentconsent (consent_uuid, action, ordinal, actor_state, actor_capacity, "
        "actor_access_epoch, authority_decision_reference, request_reference, idempotency_key, occurred_at, "
        "effective_at, received_at, recorded_at, actor_id, assignment_id, enterer_id, nominee_membership_id, "
        "proposal_event_id) VALUES (%s, %s, %s, 'PARTY', 'STANDING', %s, %s, %s, %s, %s, %s, %s, NULL, "
        "%s, %s, %s, %s, %s) RETURNING id",
        (consent_uuid, action, ordinal, epoch, reference, f"sr-c2:request:{tag}:consent-row", f"sr-c2:idem:{tag}:consent-row",
         at, at, at, nominee_identity_id, assignment_id, nominee_identity_id, nominee_membership_id, proposal_id),
    )
    return cur.fetchone()[0]


def _lo_transition(cur, ids, *, enterer_id, enterer_membership_id, assignment_id, assignment_uuid, predecessor_id,
                   sequence, action, prior_state, resulting_state, at, tag, decision=True, refresh_cache=True):
    """The LO's act on the chain (ASSIGN, END or REASSIGN): actor LIVING_ORGANISM, entered by
    `enterer_id`, with a QUALIFIED decision whose target names the assignment (installed 157-160).
    Returns the event id."""
    lo_uuid = _lo_uuid(cur, ids["lo"])
    overrides = {
        "actor_state": "LIVING_ORGANISM", "actor_id": None, "actor_access_epoch": None,
        "actor_capacity": "STANDING", "enterer_identity_id": enterer_id,
    }
    if decision:
        target = {
            "action": OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
            "assignment_uuid": str(assignment_uuid),
            "event_action": action,
            "living_organism_id": str(lo_uuid),
            "resulting_state": resulting_state,
            "schema": TARGET_SCHEMA,
        }
        reference, authority_reference = _qualified_decision(
            actor_id=enterer_id, membership_id=enterer_membership_id,
            action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE, target=target,
            request_tag=f"{tag}:{action.lower()}", at=at, living_organism_uuid=lo_uuid, assignment_uuid=assignment_uuid,
        )
        overrides.update({"authority_decision_reference": reference, "evidence_reference": authority_reference})
    event_id, _recorded = append_event(
        cur, EVENT_TABLE, assignment_id, sequence, action, prior_state, resulting_state, enterer_id, ids["basis"],
        f"{tag}:{action.lower()}", pred_id=predecessor_id, eff=at, occ=at, rec=at, overrides=overrides,
    )
    if refresh_cache:
        refresh(cur, "core_contextualroleassignment", assignment_id, at)
    return event_id


def _later(at, seconds):
    return at + dt.timedelta(seconds=seconds)


# ------------------------------------------------------------------ the tests

@POSTGRESQL_ONLY
class SROfficeGuardTests(TransactionTestCase):
    """Precondition-to-fixture map (practice 153), shared by the tests:

    - an LO with its founding Coordinator (ACTIVE membership, COORDINATOR office)  -> found_nonroot
      (the 0021 founding fixture on the lawful route; root=False, so no reserved office is written);
    - a second ACTIVE member with a JOIN chain                                     -> _add_member;
    - an LO-scoped office definition (pre-SR-shaped; kind CUSTOM where the entitlement
      check reads it), active or superseded, or a CIRCLE-scoped one                                           -> _add_definition
      (pre-SR-shaped under the C1 latitude);
    - a nomination with a QUALIFIED PROPOSE decision                               -> _propose;
    - the nominee's current CONSENT through 0037's guard                           -> _consent;
    - the LO's ASSIGN / END with a QUALIFIED decision and LO attribution           -> _lo_transition;
    - a platform root (for the reserved-office refusal)                            -> found_root after
      drop_unset_check (0021's labelled root scaffold, as the 0021 PlatformRoot tests use it).
    Every write is refreshed into the governed cache so that a chain that is lawful commits; a
    refusal rolls the whole attempt back, and the state before and after is compared whole.
    """

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.tag = fresh()
        # The chain's coordinates: one database reading, set back so that the few-second offsets the
        # chain uses (_later) stay before every database-assigned recorded_at (received <= recorded).
        self.at = database_now() - dt.timedelta(minutes=5)
        with transaction.atomic(), connection.cursor() as cur:
            self.ids = found_nonroot(cur, tag=f"src2-{self.tag}")
            self.member_id, self.member_identity, self.member_membership_uuid = _add_member(
                cur, self.ids, f"src2-member-{self.tag}", self.at,
            )
            self.treasurer_id, self.treasurer_uuid = _add_definition(cur, self.ids, "TEST_ONLY_TREASURER")
        self.founder_id = self.ids["identity"]
        self.founder_membership_id = self.ids["membership"]

    # -- helpers --------------------------------------------------------------

    def _state(self):
        """Full deterministic rows of the four affected tables, plus count summaries.

        The text cast avoids assuming a driver's JSON decoder. This is bounded
        rollback evidence, not a snapshot of the entire application or sequences.
        """
        with connection.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM core_contextualroleassignment), "
                "(SELECT count(*) FROM core_contextualroleassignmentstateevent), "
                "(SELECT count(*) FROM core_officeappointmentconsent), "
                "(SELECT count(*) FROM core_authoritydecision), "
                "(SELECT array_agg(a.id ORDER BY a.id) FROM core_contextualroleassignment a WHERE a.state = 'ACTIVE')"
            )
            counts = cur.fetchone()
            rows = []
            for table in (
                "core_contextualroleassignment", "core_contextualroleassignmentstateevent",
                "core_officeappointmentconsent", "core_authoritydecision",
            ):
                cur.execute(f"SELECT to_jsonb(r)::text FROM public.{table} r ORDER BY r.id")
                rows.append((table, tuple(row[0] for row in cur.fetchall())))
            return counts + (tuple(rows),)

    def _refuses(self, fn, *, contains):
        before = self._state()
        with self.assertRaises(DatabaseError) as refused:
            with transaction.atomic(), connection.cursor() as cur:
                fn(cur)
        message = first_line(refused.exception)
        self.assertIn(contains, message, message)
        self.assertEqual(self._state(), before, "a refused office act writes nothing")
        return message

    def _commits(self, fn):
        """Make a rejected lawful challenged act an explicit assertion failure.

        Fixture setup in setUp still raises ERROR; this wrapper does not relabel
        arbitrary exceptions or let an unrelated refusal satisfy a negative test.
        """
        try:
            with transaction.atomic(), connection.cursor() as cur:
                return fn(cur)
        except DatabaseError as exc:
            self.fail(f"lawful office act unexpectedly refused: {first_line(exc)}")

    def _nominate(self, cur, *, definition_id, definition_uuid, nominee_membership_id, nominee_membership_uuid,
                  proposer_id, proposer_membership_id, at, tag, consent):
        assignment_id, assignment_uuid, proposal_id = _propose(
            cur, self.ids, proposer_id=proposer_id, proposer_membership_id=proposer_membership_id,
            nominee_membership_id=nominee_membership_id, nominee_membership_uuid=nominee_membership_uuid,
            definition_id=definition_id, definition_uuid=definition_uuid, at=at, tag=tag,
        )
        if consent:
            nominee = OrganismMembership.objects.get(pk=nominee_membership_id)
            _consent(
                cur, self.ids, nominee_identity_id=nominee.identity_id, nominee_membership_id=nominee_membership_id,
                nominee_membership_uuid=nominee_membership_uuid, assignment_id=assignment_id,
                assignment_uuid=assignment_uuid, proposal_id=proposal_id, at=_later(at, 1), tag=tag,
            )
        return assignment_id, assignment_uuid, proposal_id

    def _nominate_member_for_treasurer(self, cur, at, tag, *, consent=True):
        return self._nominate(
            cur, definition_id=self.treasurer_id, definition_uuid=self.treasurer_uuid,
            nominee_membership_id=self.member_id, nominee_membership_uuid=self.member_membership_uuid,
            proposer_id=self.founder_id, proposer_membership_id=self.founder_membership_id, at=at, tag=tag,
            consent=consent,
        )

    def _assign(self, cur, assignment_id, assignment_uuid, proposal_id, at, tag, *, enterer_id=None,
                enterer_membership_id=None, decision=True):
        return _lo_transition(
            cur, self.ids, enterer_id=enterer_id or self.founder_id,
            enterer_membership_id=enterer_membership_id or self.founder_membership_id,
            assignment_id=assignment_id, assignment_uuid=assignment_uuid, predecessor_id=proposal_id, sequence=2,
            action="ASSIGN", prior_state="PROPOSED", resulting_state="ACTIVE", at=at, tag=tag, decision=decision,
        )

    def _installed_body(self, function_name):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT p.prosrc FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = %s",
                (function_name,),
            )
            rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        return rows[0][0]

    # -- 1. the attribution gate (W-2, attribution half) --------------------------

    def test_sr_c2_01_non_coordinator_assign_as_party_act_refused_by_attribution_guard(self):
        """Claim: a non-COORDINATOR LO office ASSIGN written as a party's own act, without LO decision
        attribution, is refused by the widened attribution guard and writes nothing.
        Obligation: O-18 (an office comes only by explicit appointment - the LO's recorded decision);
        not an independent cross-LO or mismatched-decision witness. Layer: database (0052's gate). Mutation:
        sr-c2-attribution-gate (ADMISSION) - the COORDINATOR-only gate restored admits the write."""
        at = self.at

        def attempt(cur):
            assignment_id, _assignment_uuid = _anchor(cur, self.member_id, self.treasurer_id, at, state="ACTIVE")
            append_event(cur, EVENT_TABLE, assignment_id, 1, "ASSIGN", None, "ACTIVE", self.member_identity,
                         self.ids["basis"], f"{self.tag}:party-assign", eff=at, occ=at, rec=at)
            refresh(cur, "core_contextualroleassignment", assignment_id, at)

        self._refuses(attempt, contains=REFUSED_ATTRIBUTION)

    # -- 2. the consent gate (W-2, consent half) ----------------------------------

    def test_sr_c2_02_non_coordinator_assign_without_consent_refused_by_consent_guard(self):
        """Claim: a non-COORDINATOR LO office ASSIGN with LO attribution and a matching QUALIFIED
        decision, but without the nominee's current consent, is refused by the widened assign-consent
        guard and writes nothing. Obligation: O-18 - consent before appointment, 0037's binding
        requirement (the nominee's CONSENT precedes the LO's ASSIGN). Layer: database (0052's
        consent gate). Also, a WITHDRAW appended after a lawful appointment is refused by 0037.
        Mutations: sr-c2-consent-gate and sr-c2-late-consent (separately, ADMISSION)."""
        at = self.at

        def attempt(cur):
            assignment_id, assignment_uuid, proposal_id = self._nominate_member_for_treasurer(
                cur, at, f"{self.tag}:no-consent", consent=False,
            )
            self._assign(cur, assignment_id, assignment_uuid, proposal_id, _later(at, 2), f"{self.tag}:no-consent")

        self._refuses(attempt, contains=REFUSED_CONSENT)

        def appointed(cur):
            nomination = self._nominate_member_for_treasurer(cur, _later(at, 10), f"{self.tag}:late")
            self._assign(cur, *nomination, _later(at, 12), f"{self.tag}:late")
            return nomination

        assignment_id, assignment_uuid, proposal_id = self._commits(appointed)

        def late_withdrawal(cur):
            _consent(
                cur, self.ids, nominee_identity_id=self.member_identity,
                nominee_membership_id=self.member_id, nominee_membership_uuid=self.member_membership_uuid,
                assignment_id=assignment_id, assignment_uuid=assignment_uuid, proposal_id=proposal_id,
                at=_later(at, 13), tag=f"{self.tag}:late-withdraw", action="WITHDRAW", ordinal=2,
            )

        self._refuses(late_withdrawal, contains="S2B office consent after appointment refused")

    # -- 3. the lawful chain commits; the Coordinator-only conditions are verbatim (W-1 at C2; W-3) --

    def test_sr_c2_03_lawful_non_coordinator_chain_commits_and_coordinator_conditions_verbatim(self):
        """Claim: the same chain with the nominee's consent recorded - PROPOSE, CONSENT, the LO's
        ASSIGN with its decision - is admitted by both widened guards and leaves one ACTIVE office
        (the positive control every refusal here differs from by one thing; W-1's C2 intent); and the
        installed attribution body keeps the founding self-ASSIGN branch's and the founder-protection
        block's `v_role_code = 'COORDINATOR'` verbatim (W-3; the archived guard-body readers' intent).
        Obligation: O-18; O-10 kept as a boundary. Layer: database.
        Mutation: sr-c2-lawful-chain-overrefusal (ADMISSION)."""
        at = self.at
        before = self._state()

        def chain(cur):
            assignment_id, assignment_uuid, proposal_id = self._nominate_member_for_treasurer(cur, at, f"{self.tag}:lawful")
            self._assign(cur, assignment_id, assignment_uuid, proposal_id, _later(at, 2), f"{self.tag}:lawful")
            return assignment_id

        assignment_id = self._commits(chain)
        after = self._state()
        self.assertEqual(after[0] - before[0], 1)   # one anchor
        self.assertEqual(after[1] - before[1], 2)   # PROPOSE and ASSIGN
        self.assertEqual(after[2] - before[2], 1)   # one consent row
        self.assertEqual(after[3] - before[3], 3)   # three QUALIFIED decisions
        office = ContextualRoleAssignment.objects.get(pk=assignment_id)
        self.assertEqual(office.state, "ACTIVE")
        with connection.cursor() as cur:
            cur.execute(
                "SELECT action, actor_state, actor_id, enterer_identity_id FROM public.core_contextualroleassignmentstateevent "
                "WHERE id = %s", (office.head_state_event_id,),
            )
            action, actor_state, actor_id, enterer_id = cur.fetchone()  # str, str, None, int
        self.assertEqual((action, actor_state, actor_id, enterer_id), ("ASSIGN", "LIVING_ORGANISM", None, self.founder_id))

        body = self._installed_body("s2b_guard_office_attribution")
        migration = import_module("core.migrations.0052_sr_office_guard_widening")
        self.assertEqual(body, migration.NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY)
        self.assertEqual(body.count("           AND v_role_code = 'COORDINATOR'\n"), 1)
        self.assertIn(
            "    IF NEW.action IN ('END', 'REASSIGN')\n"
            "       AND v_role_code = 'COORDINATOR'\n"
            "       AND v_role_scope = 'LIVING_ORGANISM'\n",
            body,
        )
        self.assertIn("RAISE EXCEPTION 'C_FOUNDER_PROTECTED'", body)
        self.assertNotIn("IF v_role_code IS DISTINCT FROM 'COORDINATOR'", body)

    # -- 4. founder protection stays Coordinator-only (W-3) -----------------------

    def test_sr_c2_04_founder_held_non_coordinator_office_not_caught_by_founder_protection(self):
        """Claim: before Birthing, another member's LO-attributed END of the founder's pre-SR-shaped
        appointment is admitted (founder protection does not catch a non-COORDINATOR office), while
        the same member's END of the founder's original COORDINATOR appointment is refused
        C_FOUNDER_PROTECTED. Obligation: O-10 kept as a regression boundary, not expanded (O-18 for
        the admitted END). The fixture definition has kind=NULL, not BOARD_OFFICE.
        Layer: database. Mutation: sr-c2-founder-protection (ADMISSION)."""
        at = self.at
        founder_membership_uuid = OrganismMembership.objects.get(pk=self.founder_membership_id).membership_uuid

        def founder_office(cur):
            assignment_id, assignment_uuid, proposal_id = self._nominate(
                cur, definition_id=self.treasurer_id, definition_uuid=self.treasurer_uuid,
                nominee_membership_id=self.founder_membership_id, nominee_membership_uuid=founder_membership_uuid,
                proposer_id=self.member_identity, proposer_membership_id=self.member_id, at=at,
                tag=f"{self.tag}:founder-office", consent=True,
            )
            assign_id = self._assign(cur, assignment_id, assignment_uuid, proposal_id, _later(at, 2),
                                     f"{self.tag}:founder-office")
            return assignment_id, assignment_uuid, assign_id

        assignment_id, assignment_uuid, assign_id = self._commits(founder_office)

        def end_non_coordinator_office(cur):
            return _lo_transition(
                cur, self.ids, enterer_id=self.member_identity, enterer_membership_id=self.member_id,
                assignment_id=assignment_id, assignment_uuid=assignment_uuid, predecessor_id=assign_id, sequence=3,
                action="END", prior_state="ACTIVE", resulting_state="ENDED", at=_later(at, 4),
                tag=f"{self.tag}:end-board",
            )

        self._commits(end_non_coordinator_office)
        self.assertEqual(ContextualRoleAssignment.objects.get(pk=assignment_id).state, "ENDED")

        coordinator_id = self.ids["assignments"]["COORDINATOR"]
        coordinator = ContextualRoleAssignment.objects.get(pk=coordinator_id)
        self.assertEqual(coordinator.state, "ACTIVE")

        def end_coordinator(cur):
            _lo_transition(
                cur, self.ids, enterer_id=self.member_identity, enterer_membership_id=self.member_id,
                assignment_id=coordinator_id, assignment_uuid=coordinator.assignment_uuid,
                predecessor_id=coordinator.head_state_event_id, sequence=2, action="END", prior_state="ACTIVE",
                resulting_state="ENDED", at=_later(at, 6), tag=f"{self.tag}:end-coordinator",
            )

        self._refuses(end_coordinator, contains=REFUSED_FOUNDER)

    # -- 5. already held (W-11 guard half, H48) -----------------------------------

    def test_sr_c2_05_second_assign_while_office_held_refused(self):
        """Claim: with the member's TEST_ONLY_TREASURER appointment ACTIVE, a second nomination and
        consented ASSIGN of the same member to the same code is refused "office already held" and
        writes nothing. Obligation: O-18 (a fresh appointment is a new assignment - never a second
        concurrent holding of one office). Layer: database (0052's H48 already-held check on the
        same membership and role code; scope and organism bindings are examined in test 08). Also
        refuse two ASSIGNs in one transaction before either cache is refreshed. Not covered: a
        holding recorded earlier with a later effective time (watch item W-C2-1, for C4/C5). Mutations: sr-c2-already-held and
        sr-c2-held-cache-only (separately, ADMISSION)."""
        at = self.at

        def first(cur):
            assignment_id, assignment_uuid, proposal_id = self._nominate_member_for_treasurer(cur, at, f"{self.tag}:held-1")
            self._assign(cur, assignment_id, assignment_uuid, proposal_id, _later(at, 2), f"{self.tag}:held-1")

        self._commits(first)

        def second(cur):
            assignment_id, assignment_uuid, proposal_id = self._nominate_member_for_treasurer(
                cur, _later(at, 10), f"{self.tag}:held-2",
            )
            self._assign(cur, assignment_id, assignment_uuid, proposal_id, _later(at, 12), f"{self.tag}:held-2")

        self._refuses(second, contains=REFUSED_HELD)

        delayed_id, delayed_uuid = self._commits(
            lambda cur: _add_definition(cur, self.ids, "TEST_ONLY_DELAYED_REFRESH"),
        )

        def delayed_refresh(cur):
            nominations = [self._nominate(
                cur, definition_id=delayed_id, definition_uuid=delayed_uuid,
                nominee_membership_id=self.member_id, nominee_membership_uuid=self.member_membership_uuid,
                proposer_id=self.founder_id, proposer_membership_id=self.founder_membership_id,
                at=_later(at, 20 + index), tag=f"{self.tag}:delayed-{index}", consent=True,
            ) for index in range(2)]
            for index, (assignment_id, assignment_uuid, proposal_id) in enumerate(nominations):
                _lo_transition(
                    cur, self.ids, enterer_id=self.founder_id,
                    enterer_membership_id=self.founder_membership_id,
                    assignment_id=assignment_id, assignment_uuid=assignment_uuid,
                    predecessor_id=proposal_id, sequence=2, action="ASSIGN",
                    prior_state="PROPOSED", resulting_state="ACTIVE", at=_later(at, 25),
                    tag=f"{self.tag}:delayed-{index}", refresh_cache=False,
                )
            # Under the cache-only defect both inserts reach here. Refresh before
            # commit so an unrelated cache/fold mismatch cannot kill the witness.
            for assignment_id, _assignment_uuid, _proposal_id in nominations:
                refresh(cur, "core_contextualroleassignment", assignment_id, _later(at, 25))

        self._refuses(delayed_refresh, contains=REFUSED_HELD)

    # -- 6. the item-5 PROPOSE checks ---------------------------------------------

    def test_sr_c2_06_propose_refused_without_custom_entitlement_and_on_superseded_version(self):
        """Claim: (a) a PROPOSE on a CUSTOM definition when CUSTOM_ROLES is not effective for the LO
        at the event's received_at is refused "custom role entitlement absent at receipt"; (b) a
        PROPOSE naming an inactive pre-SR-shaped definition is refused "superseded
        definition"; each writes nothing. SR v0.7 section E item 5; related to O-22,
        but not evidence that definition columns are immutable. The CUSTOM gate
        follows the same design. Layer: database. Separate ADMISSION mutations:
        sr-c2-propose-entitlement and sr-c2-propose-superseded, one defect per run."""
        at = self.at

        def definitions(cur):
            custom_id, custom_uuid = _add_definition(cur, self.ids, "TEST_ONLY_ADJUNCT", kind="CUSTOM")
            old_id, old_uuid = _add_definition(cur, self.ids, "TEST_ONLY_SECRETARY", version=1, active=False)
            _add_definition(cur, self.ids, "TEST_ONLY_SECRETARY", version=2, active=True)
            return custom_id, custom_uuid, old_id, old_uuid

        custom_id, custom_uuid, old_id, old_uuid = self._commits(definitions)
        with connection.cursor() as cur:
            cur.execute("SELECT public.s015_entitlement_effective(%s, 'CUSTOM_ROLES', %s)", (_lo_uuid(cur, self.ids["lo"]), at))
            self.assertFalse(cur.fetchone()[0], "the fixture LO holds no CUSTOM_ROLES entitlement")

        # Two cases in sequence (no subTest: the route judges a mutation target by the test's own result).
        for label, definition_id, definition_uuid, expected in (
            ("custom", custom_id, custom_uuid, REFUSED_ENTITLEMENT),
            ("superseded", old_id, old_uuid, REFUSED_SUPERSEDED),
        ):
            self._refuses(
                lambda cur, d=definition_id, du=definition_uuid, tag=f"{self.tag}:{label}": self._nominate(
                    cur, definition_id=d, definition_uuid=du, nominee_membership_id=self.member_id,
                    nominee_membership_uuid=self.member_membership_uuid, proposer_id=self.founder_id,
                    proposer_membership_id=self.founder_membership_id, at=at, tag=tag, consent=False,
                ),
                contains=expected,
            )

    # -- 7. the reserved root offices (ruling 4) ----------------------------------

    def test_sr_c2_07_reserved_root_office_act_refused_at_the_guard(self):
        """Claim: on the platform root itself, the fixture founder's nomination of a member
        to a reserved INTEVIA_ office is refused "reserved root office" at the guard and writes nothing
        (ruling 4: SR's acts never fill an INTEVIA_ code; the route remains P2/GENESIS). Obligation:
        O-14-related route restriction only; not root uniqueness or reserved-definition placement.
        Historical root setup uses the attribution seam; the challenged nomination does not.
        Layer: database (0052's head check, before any branch). Mutation: sr-c2-reserved-root
        (ADMISSION) - the check removed lets the nomination through.
        Isolation: the root scaffold drops s015_platform_root_unset_ck, which this
        TransactionTestCase's flush does not restore. The whole test therefore runs in one outer
        transaction that is always rolled back, so the dropped constraint, the root and its member
        are undone together and the next test starts from the template's schema (C2 run 2, CP-A).
        The fixture writes and the refused nomination run in savepoints inside it; the refusal is
        BEFORE INSERT (s2b_10_attribution), so it is reached exactly as before. The root scaffold's and
        its member's commit-time (deferred) checks are not exercised here: the same scaffold commits
        under the 0021 PlatformRoot tests on this base, and the same member fixture commits in setUp."""
        at = self.at
        with transaction.atomic():
            with transaction.atomic(), connection.cursor() as cur:
                drop_unset_check(cur)
                root = found_root(cur, tag=f"src2-root-{self.tag}")
            root_member_id, _root_member_identity, root_member_uuid = self._commits(
                lambda cur: _add_member(cur, root, f"src2-root-member-{self.tag}", at),
            )
            steward_id = root["roles"]["INTEVIA_STEWARD"]
            with connection.cursor() as cur:
                cur.execute("SELECT role_uuid FROM core_organismroledefinition WHERE id = %s", (steward_id,))
                steward_uuid = cur.fetchone()[0]

            def nominate_steward(cur):
                _propose(
                    cur, root, proposer_id=root["identity"], proposer_membership_id=root["membership"],
                    nominee_membership_id=root_member_id, nominee_membership_uuid=root_member_uuid,
                    definition_id=steward_id, definition_uuid=steward_uuid, at=at, tag=f"{self.tag}:steward",
                )

            self._refuses(nominate_steward, contains=REFUSED_RESERVED)
            transaction.set_rollback(True)


    # -- 8. the widened bodies are bound to scope and organism (lessons 1 and 2) --

    def test_sr_c2_08_circle_scope_unguarded_and_other_holdings_do_not_count_as_held(self):
        """Claim: (a) a CIRCLE-scoped assignment is not guarded by the widened bodies (S4-C's): a
        party-written CIRCLE office commits; (b) the same person's ACTIVE holding of the same code in
        another organism does not make this LO's ASSIGN "already held" - the H48 lookup is bound to
        this LO's membership, which belongs to one organism only (and to LO scope; one LO cannot define one code in two scopes,
        s015_role_definition_version_uniq, so that clause has no same-LO witness). Obligation: O-21.
        Layer: database. Mutation: sr-c2-held-cross-organism (ADMISSION).
        This does not challenge borrowed authority/consent. The 0021 Structure
        tests remain the negative cross-organism and scope-binding regressions."""
        at = self.at

        def circle(cur):
            # (a) a CIRCLE-scoped office held by the member, party-written (S4-C's scope, unguarded here)
            c_uuid = u(f"circle:{self.tag}")
            cur.execute(
                "INSERT INTO core_circle (circle_uuid, state, created_at, current_state_effective_at, state_known_at, "
                "state_source_event_set_fingerprint, founding_reference, parent_organism_id) "
                "VALUES (%s, 'DORMANT', now(), %s, %s, %s, %s, %s) RETURNING id",
                (c_uuid, at, at, empty_fp(cur, "core_circle", c_uuid), f"sr-c2:circle:{self.tag}", self.ids["lo"]),
            )
            circle_id = cur.fetchone()[0]
            append_event(cur, "core_circlestateevent", circle_id, 1, "CREATE_DORMANT", None, "DORMANT",
                         self.founder_id, self.ids["basis"], f"{self.tag}:circle", eff=at, occ=at, rec=at)
            refresh(cur, "core_circle", circle_id, at)
            circle_definition_id, _ = _add_definition(cur, self.ids, "TEST_ONLY_CIRCLE_SCRIBE", scope="CIRCLE")
            circle_assignment_id, _ = _anchor(cur, self.member_id, circle_definition_id, at, state="ACTIVE", circle_id=circle_id)
            append_event(cur, EVENT_TABLE, circle_assignment_id, 1, "ASSIGN", None, "ACTIVE", self.member_identity,
                         self.ids["basis"], f"{self.tag}:circle-assign", eff=at, occ=at, rec=at)
            refresh(cur, "core_contextualroleassignment", circle_assignment_id, at)
            return circle_assignment_id

        def other_lo(cur):
            # (b) the same person ACTIVE as TEST_ONLY_TREASURER in another organism (its own lawful chain);
            # a separate commit: writes to two organisms in one transaction take the canonical lock order.
            other = found_nonroot(cur, tag=f"src2-other-{self.tag}")
            other_membership_uuid = u(f"membership:src2-other-member-{self.tag}")
            cur.execute(
                "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, "
                "state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                (other_membership_uuid, at, at, empty_fp(cur, "core_organismmembership", other_membership_uuid),
                 self.member_identity, other["lo"]),
            )
            other_membership_id = cur.fetchone()[0]
            append_event(cur, "core_organismmembershiptransition", other_membership_id, 1, "JOIN", None, "ACTIVE",
                         self.member_identity, other["basis"], f"{self.tag}:other-join",
                         extra={"reason_class": "TEST_ONLY_ADMISSION"}, eff=at, occ=at, rec=at)
            refresh(cur, "core_organismmembership", other_membership_id, at)
            other_treasurer_id, other_treasurer_uuid = _add_definition(cur, other, "TEST_ONLY_TREASURER")
            assignment_id, assignment_uuid, proposal_id = _propose(
                cur, other, proposer_id=other["identity"], proposer_membership_id=other["membership"],
                nominee_membership_id=other_membership_id, nominee_membership_uuid=uuid.UUID(other_membership_uuid),
                definition_id=other_treasurer_id, definition_uuid=other_treasurer_uuid, at=at, tag=f"{self.tag}:other",
            )
            _consent(cur, other, nominee_identity_id=self.member_identity, nominee_membership_id=other_membership_id,
                     nominee_membership_uuid=uuid.UUID(other_membership_uuid), assignment_id=assignment_id,
                     assignment_uuid=assignment_uuid, proposal_id=proposal_id, at=_later(at, 1), tag=f"{self.tag}:other")
            _lo_transition(cur, other, enterer_id=other["identity"], enterer_membership_id=other["membership"],
                           assignment_id=assignment_id, assignment_uuid=assignment_uuid, predecessor_id=proposal_id,
                           sequence=2, action="ASSIGN", prior_state="PROPOSED", resulting_state="ACTIVE",
                           at=_later(at, 2), tag=f"{self.tag}:other")
            return assignment_id

        circle_assignment_id = self._commits(circle)
        other_assignment_id = self._commits(other_lo)
        self.assertEqual(ContextualRoleAssignment.objects.get(pk=circle_assignment_id).state, "ACTIVE")
        self.assertEqual(ContextualRoleAssignment.objects.get(pk=other_assignment_id).state, "ACTIVE")

        def this_lo(cur):
            assignment_id, assignment_uuid, proposal_id = self._nominate_member_for_treasurer(
                cur, _later(at, 10), f"{self.tag}:this-lo",
            )
            self._assign(cur, assignment_id, assignment_uuid, proposal_id, _later(at, 12), f"{self.tag}:this-lo")
            return assignment_id

        this_assignment_id = self._commits(this_lo)
        self.assertEqual(ContextualRoleAssignment.objects.get(pk=this_assignment_id).state, "ACTIVE")

    # -- 9. the migration (W-13) -------------------------------------------------

    def test_sr_c2_09_migration_0052_forward_reverse_clean_and_populated_reverse_refused(self):
        """Claim: from a clean state 0052 reverses to 0051 restoring both installed bodies exactly
        (0044's attribution body; 0037's consent body) and re-applies to SR's bodies, the
        non-internal trigger count unchanged throughout; with one non-COORDINATOR LO office written,
        the reverse is refused naming the count and deletes nothing. Obligation: O-53 (a schema
        change refuses an unsafe reversal) - a partial witness, recorded as such. Layer: migration.
        Mutation: sr-c2-attribution-gate witnesses sensitivity of the initial installed-body
        assertion only. Reverse behaviour has a separate source-fault qualification kit."""
        migration = import_module("core.migrations.0052_sr_office_guard_widening")

        def facts():
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND NOT t.tgisinternal"
                )
                triggers = cur.fetchone()[0]
            return {
                "head": current_applied_core_head(connection)[0][1],
                "attribution": self._installed_body("s2b_guard_office_attribution"),
                "consent": self._installed_body("s2b_guard_assign_consent"),
                "triggers": triggers,
            }

        at_head = facts()
        self.assertIn(("core", HEAD_0052), MigrationRecorder(connection).applied_migrations())
        self.assertEqual(at_head["attribution"], migration.NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY)
        self.assertEqual(at_head["consent"], migration.NEW_S2B_GUARD_ASSIGN_CONSENT_BODY)
        migrate_to_target_with_restore(self, ("core", HEAD_0052))
        MigrationExecutor(connection).migrate([("core", HEAD_0051)])
        at_0051 = facts()
        self.assertEqual(at_0051["head"], HEAD_0051)
        self.assertEqual(at_0051["attribution"], migration.PREVIOUS_S2B_GUARD_OFFICE_ATTRIBUTION_BODY)
        self.assertEqual(at_0051["consent"], migration.PREVIOUS_S2B_GUARD_ASSIGN_CONSENT_BODY)
        self.assertEqual(at_0051["triggers"], at_head["triggers"])
        self.restore_received_migrations()
        again = facts()
        self.assertEqual(again, at_head)

        at = self.at

        def office(cur):
            assignment_id, assignment_uuid, proposal_id = self._nominate_member_for_treasurer(cur, at, f"{self.tag}:populated")
            self._assign(cur, assignment_id, assignment_uuid, proposal_id, _later(at, 2), f"{self.tag}:populated")

        self._commits(office)
        before = self._state()
        with self.assertRaises(RuntimeError) as refused:
            MigrationExecutor(connection).migrate([("core", HEAD_0051)])
        message = str(refused.exception)
        self.assertIn("SR 0052 reverse refused", message)
        self.assertIn("1 non-COORDINATOR LO-scoped assignment(s) carrying 2 state event(s)", message)
        self.assertEqual(facts(), at_head)
        self.assertEqual(self._state(), before)

    # -- 10. concurrency (threaded) ------------------------------------------------

    def test_sr_c2_10_concurrent_assigns_of_one_office_leave_one_active_appointment(self):
        """Claim: two LO ASSIGNs of the same member to the same office, from two consented
        nominations in two connections released together, leave exactly one ACTIVE appointment:
        the second is serialised by the owning-organism lock and refused "office already held".
        SR H48 / O-18-related holding exclusion across two assignment chains; this
        is not an O-39 single-history no-fork witness. Layer: database, threaded.
        Mutation: sr-c2-already-held (ADMISSION)."""
        at = self.at

        def nominations(cur):
            first = self._nominate_member_for_treasurer(cur, at, f"{self.tag}:race-1")
            second = self._nominate_member_for_treasurer(cur, _later(at, 1), f"{self.tag}:race-2")
            return first, second

        first, second = self._commits(nominations)
        barrier = Barrier(2, timeout=30)
        outcomes = Queue()

        def contender(label, nomination, seconds):
            try:
                barrier.wait()
                assignment_id, assignment_uuid, proposal_id = nomination
                with transaction.atomic(), connection.cursor() as cur:
                    self._assign(cur, assignment_id, assignment_uuid, proposal_id, _later(at, seconds), f"{self.tag}:{label}")
                outcomes.put((label, "committed", None))
            except DatabaseError as exc:
                outcomes.put((label, "refused", first_line(exc)))
            except Exception as exc:  # reported, never swallowed
                outcomes.put((label, "error", repr(exc)))
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=contender, args=("race-1", first, 5)),
            threading.Thread(target=contender, args=("race-2", second, 5)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        results = {}
        while not outcomes.empty():
            label, outcome, detail = outcomes.get()
            results[label] = (outcome, detail)
        self.assertEqual(len(results), 2, results)
        self.assertEqual(sorted(outcome for outcome, _ in results.values()), ["committed", "refused"], results)
        refused_detail = next(detail for outcome, detail in results.values() if outcome == "refused")
        self.assertIn(REFUSED_HELD, refused_detail)
        active = ContextualRoleAssignment.objects.filter(
            membership_id=self.member_id, role_definition_id=self.treasurer_id, state="ACTIVE",
        ).count()
        self.assertEqual(active, 1)
        self.assertEqual(
            ContextualRoleAssignmentStateEvent.objects.filter(
                assignment__membership_id=self.member_id, assignment__role_definition_id=self.treasurer_id, action="ASSIGN",
            ).count(),
            1,
        )
