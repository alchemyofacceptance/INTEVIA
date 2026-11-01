"""SR-H: five database witnesses and three pure contract witnesses.

All challenged database writes retain the installed guards. Real registration,
founding and configuration use C5's fixture; custom access uses C3's labelled
beta composer, not a subscription-expiry claim. C2 supplies guarded nomination,
consent and event writers, never its definition-suspension helper. The test-only
qualified decision provider deliberately represents a direct writer who can
record a self-consistent decision; it is not evidence of human permission.
Each negative attempt compares complete rows of the affected tables after its
transaction rolls back. No assertion claims a snapshot of sequences or every
application table. See the accompanying TAR ledger for types and boundaries.
"""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase

from core import models
from core.organism_contract import OrganismAction
from src.intevia.services import organism_role_definition_service as definitions
from src.intevia.services import organism_office_consent_service as consent
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import S015Authority, S015PostFoundingAuthorityRefusal
from src.intevia.services.organism_command_support import S015CommandReceiptMalformed
import tests.test_sr_definitions as c3
import tests.test_sr_office_actions as c5
import tests.test_sr_office_guards as c2
import tests.s015_0021_support as fixtures

COORDINATOR_REFUSAL = "SR definition refused: the Coordinator office is established at founding and cannot be changed here"
BOARD_REFUSAL = "SR definition refused: board offices are permanent; amend the definition instead of retiring the office"
KIND_REFUSAL = "SR definition refused: an office must keep its original kind"
STALE_REFUSAL = "S2B office attribution refused: superseded definition"
SUCCESSOR_REFUSAL = "SR definition refused: a later version must supersede the latest version of this office"


class SROfficeSafeguardTests(TransactionTestCase):
    """Use lawful stored definitions and fresh independent fixtures for each claim."""
    reset_sequences = True
    _start_organism = c5.SROfficeActionTests._start_organism
    _command = c3.SRDefinitionCommandTests._command
    _success = c3.SRDefinitionCommandTests._success
    _define = c3.SRDefinitionCommandTests._define
    _define_custom = c3.SRDefinitionCommandTests._define_custom
    _custom_entitlement = c3.SRDefinitionCommandTests._custom_entitlement
    _snapshot = c3.SRDefinitionCommandTests._snapshot

    def setUp(self):
        super().setUp()
        self._start_organism()

    def _refused(self, attempt, expected, description):
        """Assert the named database refusal and complete rollback of affected rows."""
        before = self._snapshot()
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                attempt(cursor)
        except DatabaseError as exc:
            primary = str(exc).strip().splitlines()[0]
            self.assertEqual(primary, expected,
                msg=f"{description}: expected the named database safeguard, but received {primary!r}; another error does not prove this rule.")
        else:
            self.fail(f"{description}: the database should refuse this office change, but it accepted it.")
        self.assertEqual(self._snapshot(), before,
            msg=f"{description}: a refused attempt should leave all compared office, membership, decision and request rows unchanged; stored rows changed.")

    def _decision(self, content, action, row=None):
        """Store self-consistent QUALIFIED evidence through 0034, with a test provider."""
        at = fixtures.database_now()
        target = c3._definition_target(self.organism.organism_id, content, action, row)
        reference, _ = c2._qualified_decision(
            actor_id=self.actor.pk, membership_id=self.founder_member.pk,
            action=OrganismAction.RECORD_ROLE_DEFINITION, target=target,
            request_tag="srh:" + uuid4().hex, at=at,
            living_organism_uuid=self.organism.organism_id,
        )
        return reference, at

    def _write_version(self, content, reference, at, prior=None):
        """Use the established row writer directly, without the service/provider refusal."""
        values = {name: getattr(content, name) for name in content.__dataclass_fields__}
        return definitions._write_definition_version(
            database_alias="default", organism=self.organism, supersedes=prior,
            enterer=self.actor, authority_decision_reference=reference,
            occurred_at=at, effective_at=at, received_at=at, **values,
        )

    def _retire(self, row, reference, at):
        """A direct SQL UPDATE with the same columns as the recorded retirement writer."""
        with connection.cursor() as cursor:
            cursor.execute("""UPDATE core_organismroledefinition
                SET active=false, retired_by_decision_reference=%s,
                    retired_effective_at=%s, retired_received_at=%s, retirement_reason=%s
                WHERE id=%s""", [reference, at, at, "Test retirement", row.pk])

    def test_01_stale_assign_is_refused_by_database(self):
        """O-18/O-22: a consented nomination cannot be appointed against a replaced version."""
        command, old = self._define()
        tag = "srh-stale:" + uuid4().hex
        with transaction.atomic(), connection.cursor() as cursor:
            member_id, nominee_id, member_uuid = c2._add_member(cursor, self.ids, tag, fixtures.database_now())
            assignment_id, assignment_uuid, proposal_id = c2._propose(
                cursor, self.ids, proposer_id=self.actor.pk, proposer_membership_id=self.founder_member.pk,
                nominee_membership_id=member_id, nominee_membership_uuid=member_uuid,
                definition_id=old.pk, definition_uuid=old.role_uuid, at=fixtures.database_now(), tag=tag,
            )
            c2._consent(cursor, self.ids, nominee_identity_id=nominee_id,
                nominee_membership_id=member_id, nominee_membership_uuid=member_uuid,
                assignment_id=assignment_id, assignment_uuid=assignment_uuid, proposal_id=proposal_id,
                at=fixtures.database_now(), tag=tag)
        # Lawful amendment preserves the board office and its pending old-version nomination.
        amended = self._success(self._command("AMEND", row=old, content=command.content))
        old.refresh_from_db()
        self.assertFalse(old.active,
            msg="Preparing an outdated appointment should replace the nominated definition; the old version is still active.")
        self.assertNotEqual(amended.role_definition_uuid, old.role_uuid,
            msg="Amending the board office should create a new immutable version; the old identifier was returned.")

        def assign(cursor):
            c2._lo_transition(cursor, self.ids, enterer_id=self.actor.pk,
                enterer_membership_id=self.founder_member.pk, assignment_id=assignment_id,
                assignment_uuid=assignment_uuid, predecessor_id=proposal_id, sequence=2,
                action="ASSIGN", prior_state="PROPOSED", resulting_state="ACTIVE",
                at=fixtures.database_now(), tag=tag)
        self._refused(assign, STALE_REFUSAL, "Appointing this member under the superseded board-office definition")

    def test_02_coordinator_definition_cannot_be_replaced_or_retired(self):
        """O-22: another office's decision cannot create Coordinator v2 or deactivate Coordinator v1."""
        command, other = self._define()
        coordinator = models.OrganismRoleDefinition.objects.get(
            living_organism=self.organism, code="COORDINATOR", active=True)
        reference = other.authority_decision_reference
        at = other.received_at
        content = definitions.DefinitionContent(code="COORDINATOR", kind="COORDINATOR")
        self._refused(lambda _cursor: self._write_version(content, reference, at),
            COORDINATOR_REFUSAL, "Creating a second Coordinator definition using another office's decision")
        self._refused(lambda _cursor: self._retire(coordinator, reference, at),
            COORDINATOR_REFUSAL, "Deactivating the founding Coordinator definition using another office's decision")
        self.assertTrue(models.OrganismRoleDefinition.objects.get(pk=coordinator.pk).active,
            msg="The founding Coordinator should remain available after the two refused changes; its definition is inactive.")

    def test_03_board_retirement_is_refused_but_custom_retirement_commits(self):
        """O-22 plus HG permanence ruling: vacant board offices remain; a custom role may retire."""
        command, board = self._define()
        def retire_board(_cursor):
            reference, at = self._decision(command.content, "RETIRE", board)
            self._retire(board, reference, at)
        self._refused(retire_board, BOARD_REFUSAL, "Retiring the vacant board office with self-consistent decision evidence")
        _custom_command, custom = self._define_custom()
        self._success(self._command("RETIRE", row=custom))
        custom.refresh_from_db()
        self.assertFalse(custom.active,
            msg="Retiring an unheld custom role with proper authority should succeed; the custom definition remains active.")
        self.assertIsNotNone(custom.retired_by_decision_reference,
            msg="Retiring the custom role should preserve its retirement decision; the reference is missing.")

    def test_04_successor_kind_is_fixed_in_both_directions(self):
        """O-22 plus HG kind ruling: neither board-to-custom nor custom-to-board amendment is admitted."""
        board_command, board = self._define()
        custom_command, custom = self._define_custom()
        for prior, original, changed in (
            (board, board_command.content, replace(board_command.content, kind="CUSTOM", nomination_basis="CUSTOM", provisions=())),
            (custom, custom_command.content, replace(custom_command.content, kind="BOARD_OFFICE", nomination_basis="SILENT",
                provisions=(definitions.DefinitionProvision("OFFICE_BASIS", "library:srh-test#office", "v1"),))),
        ):
            with self.subTest(original_kind=prior.kind):
                def change_kind(_cursor):
                    reference, at = self._decision(changed, "AMEND", prior)
                    models.OrganismRoleDefinition.objects.filter(pk=prior.pk).update(active=False)
                    self._write_version(changed, reference, at, prior)
                self._refused(change_kind, KIND_REFUSAL, f"Changing the {prior.kind} office into a different kind")
                # Positive pairing: same-kind amendment remains lawful with all deferred checks enabled.
                result = self._success(self._command("AMEND", row=prior, content=original))
                successor = models.OrganismRoleDefinition.objects.get(role_uuid=result.role_definition_uuid)
                self.assertEqual(successor.kind, prior.kind,
                    msg="A lawful amendment should preserve the office's kind in its new version; the stored kind differs.")


    def test_08_retired_code_cannot_restart_without_latest_predecessor(self):
        """O-22/HG Q2: a retired custom office cannot restart as a board office without its latest predecessor."""
        command, custom = self._define_custom()
        self._success(self._command("RETIRE", row=custom))
        custom.refresh_from_db()
        self.assertFalse(custom.active,
            msg="Preparing the retired-office witness should end the custom definition; it is still active.")
        self.assertIsNotNone(custom.retired_by_decision_reference,
            msg="Preparing the retired-office witness should record a retirement decision; that decision is missing.")
        content = replace(command.content, kind="BOARD_OFFICE", nomination_basis="SILENT",
            provisions=(definitions.DefinitionProvision("OFFICE_BASIS", "library:srh-test#office", "v1"),))

        def restart(_cursor):
            at = fixtures.database_now()
            target = c3._definition_target(self.organism.organism_id, content, "DEFINE")
            target["definition_version"] = custom.definition_version + 1
            reference, _ = c2._qualified_decision(
                actor_id=self.actor.pk, membership_id=self.founder_member.pk,
                action=OrganismAction.RECORD_ROLE_DEFINITION, target=target,
                request_tag="srh-successor:" + uuid4().hex, at=at,
                living_organism_uuid=self.organism.organism_id,
            )
            self._write_version(content, reference, at, prior=None)

        self._refused(restart, SUCCESSOR_REFUSAL,
            "Re-creating the retired custom office as a board office without linking the latest definition")


class SRHSafeguardPureTests(SimpleTestCase):
    def test_05_provider_refuses_reserved_amend_and_retire_targets(self):
        """O-14 boundary: the real definition qualifier refuses reserved AMEND/RETIRE targets without a stored reserved row."""
        provider = ProductionApplicationAuthorityProvider()
        organism_id = uuid4()
        row = SimpleNamespace(role_uuid=uuid4(), definition_version=1)
        content = definitions.DefinitionContent(code="INTEVIA_TEST_RESERVED",
            provisions=(definitions.DefinitionProvision("OFFICE_BASIS", "library:srh-test#reserved", "v1"),))
        for action in ("AMEND", "RETIRE"):
            with self.subTest(action=action):
                target = c3._definition_target(organism_id, content, action, row)
                self.assertTrue(definitions.definition_target_valid(target, organism_id),
                    msg=f"Preparing the reserved-office {action} witness should produce a well-formed target; its shape is invalid.")
                now = datetime.now(timezone.utc)
                request = replace(c3._post("RECORD_ROLE_DEFINITION", target),
                    living_organism_id=organism_id, evaluated_at=now, effective_at=now)
                with patch("src.intevia.services.organism_application_authority._effective_version",
                           return_value=SimpleNamespace(cq_active=False, inherit_portal_cq_settings=False)), \
                     patch.object(provider, "_current_member", return_value=True):
                    try:
                        result = provider._qualify_role_definition(request,
                            SimpleNamespace(organism_id=organism_id), None, None)
                    except Exception as exc:
                        self.fail(f"The reserved-office {action} request should be refused before any definition lookup; it raised {type(exc).__name__} instead.")
                self.assertIsInstance(result, S015PostFoundingAuthorityRefusal,
                    msg=f"The provider should refuse the reserved-office {action} request; it returned a non-refusal.")
                self.assertEqual(result.refusal_code.value, "C_RESERVED_ROOT_OFFICE",
                    msg=f"The reserved-office {action} request should name the platform-office restriction; another refusal was returned.")

    def test_06_consent_receipts_are_strict_and_faults_are_not_request_mismatches(self):
        """O-29: valid stored consent results round-trip; malformed results retain a chained record-fault cause."""
        service = consent.OfficeAppointmentConsentService(authority=S015Authority(provider=ProductionApplicationAuthorityProvider()), clock=lambda: datetime.now(timezone.utc))
        for action in ("CONSENT", "DECLINE", "WITHDRAW"):
            original = consent.OfficeConsentCommandResult(uuid4(), uuid4(), uuid4(), action, 1,
                datetime.now(timezone.utc), "test-only-lineage", "test-only-authority", "s015d1:" + "0" * 64)
            payload = original.receipt_payload()
            try:
                replayed = service._replay_result(payload)
            except Exception as exc:
                self.fail(f"Reading a valid {action} receipt should reproduce its result; it raised {type(exc).__name__} instead.")
            self.assertEqual(replayed, original,
                msg=f"Replaying the stored {action} result should preserve all fields; the reconstructed result differs.")
        # One contract claim with a table of independently malformed fields.
        bad = [("not an object", []), ("missing field", {k:v for k,v in payload.items() if k != "consent_uuid"}),
               ("extra field", {**payload, "unexpected": "value"})]
        for key in payload:
            bad.append((key + " has the wrong type", {**payload, key: None}))
        for value in (True, 0, -1, "1"):
            bad.append(("invalid ordinal", {**payload, "ordinal": value}))
        bad += [("unknown action", {**payload, "action": "ASSIGN"}),
                ("noncanonical UUID", {**payload, "consent_uuid": payload["consent_uuid"].replace("-", "")}),
                ("naive time", {**payload, "recorded_at": original.recorded_at.replace(tzinfo=None).isoformat()}),
                ("noncanonical time", {**payload, "recorded_at": original.recorded_at.isoformat()})]
        for label, damaged in bad:
            with self.subTest(damage=label):
                try:
                    service._replay_result(damaged)
                except S015CommandReceiptMalformed as exc:
                    self.assertIsInstance(exc.__cause__, (TypeError, ValueError, KeyError),
                        msg=f"Reading a consent receipt with {label} should retain the reason for its record fault; no recognised cause was chained.")
                except Exception as exc:
                    self.fail(f"Reading a consent receipt with {label} should report a damaged record, not a different request; it raised {type(exc).__name__}.")
                else:
                    self.fail(f"Reading a consent receipt with {label} should refuse the damaged record; it was accepted.")

    def test_07_admission_experiments_require_explicit_selection(self):
        """Test-route budget: new admission experiments are selectable but absent from ordinary CI."""
        from verification.admission_mutations import ADMISSION_MUTATIONS
        from verification.mutations import MUTATIONS, selected_mutation
        self.assertFalse(set(MUTATIONS) & set(ADMISSION_MUTATIONS),
            msg="SR-H admission experiments should not run on the ordinary route; their names appear in both registries.")
        for name, expected in ADMISSION_MUTATIONS.items():
            with self.subTest(mutation=name):
                self.assertEqual(expected["class"], "ADMISSION",
                    msg="Each new SR-H experiment should run only at admission; an entry has another class.")
                self.assertEqual(selected_mutation(name), expected,
                    msg="Selecting a named admission experiment should return its intended SQL and target; the selected entry differs.")
        with self.assertRaises(RuntimeError, msg="An unknown experiment must be refused before SQL runs; the selector accepted an unknown name."):
            selected_mutation("sr-h-unknown-test-only")
