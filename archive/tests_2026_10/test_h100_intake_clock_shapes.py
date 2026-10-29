"""PKT-CLK-3 CLK3-1 pure intake-shape witnesses."""

from dataclasses import fields, replace
from datetime import datetime, timezone
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import SimpleTestCase

from core.models import IntakeEntry
from src.intevia.services.command_intake import (
    ENTRY_ACTIONS,
    ActorRef,
    intake_fingerprint,
    intake_shape,
)
from src.intevia.services.organism_entitlement_service import EntitlementElectionCommand
from src.intevia.services.organism_formation_service import (
    BirthingCommand,
    FoundLivingOrganismCommand,
    PhaseAdvanceCommand,
)
from src.intevia.services.organism_governance_service import (
    AdoptGoverningInstrumentCommand,
    CreateGovernanceCircleCommand,
)


UTC = timezone.utc
T0 = datetime(2026, 10, 23, 12, tzinfo=UTC)
T1 = datetime(2026, 10, 23, 12, 1, tzinfo=UTC)


def _commands(credential):
    organism_uuid = uuid4()
    return (
        (
            IntakeEntry.FOUND_LIVING_ORGANISM,
            FoundLivingOrganismCommand(
                credential=credential,
                name="A Clocked Organism",
                purpose="A purpose for the clocked organism.",
                vision="A vision.",
                mission="A mission.",
                values="Values.",
                accepted_terms_version_uuid=uuid4(),
                request_reference="clk3:found:reference",
                idempotency_key="clk3:found:key",
            ),
        ),
        (
            IntakeEntry.ADVANCE_PHASE,
            PhaseAdvanceCommand(
                credential=credential,
                living_organism_uuid=organism_uuid,
                from_phase="CONCEIVING",
                to_phase="GERMINATING",
                request_reference="clk3:phase:reference",
                idempotency_key="clk3:phase:key",
            ),
        ),
        (
            IntakeEntry.BIRTHING,
            BirthingCommand(
                credential=credential,
                living_organism_uuid=organism_uuid,
                request_reference="clk3:birthing:reference",
                idempotency_key="clk3:birthing:key",
                offering_choice="FREE",
                policy_version=1,
                policy_text_sha256="a" * 64,
            ),
        ),
        (
            IntakeEntry.ADOPT_GOVERNING_INSTRUMENT,
            AdoptGoverningInstrumentCommand(
                credential=credential,
                living_organism_uuid=organism_uuid,
                rule_uuid=uuid4(),
                version=1,
                body={"seats": []},
                effective_from=T0,
                request_reference="clk3:adopt:reference",
                idempotency_key="clk3:adopt:key",
            ),
        ),
        (
            IntakeEntry.CREATE_GOVERNANCE_CIRCLE,
            CreateGovernanceCircleCommand(
                credential=credential,
                living_organism_uuid=organism_uuid,
                request_reference="clk3:circle:reference",
                idempotency_key="clk3:circle:key",
            ),
        ),
        (
            IntakeEntry.ACTIVATE_BETA,
            EntitlementElectionCommand(
                credential=credential,
                living_organism_uuid=organism_uuid,
                request_reference="clk3:beta:reference",
                idempotency_key="clk3:beta:key",
                occurred_at=T0,
                policy_version=1,
                policy_text_sha256="b" * 64,
            ),
        ),
    )


def _fingerprint(entry, command, actor):
    shape = intake_shape(entry, command)
    return intake_fingerprint(
        entry=entry,
        action=ENTRY_ACTIONS[entry],
        actor_identity_id=actor.identity_id,
        actor_access_epoch=actor.access_epoch,
        request_reference=shape.request_reference,
        idempotency_key=shape.idempotency_key,
        target=shape.target,
        command=shape.command,
        omitted=shape.omitted,
    )


class ClockShapeTests(SimpleTestCase):
    """Exercise the six commissioned projections without database access."""

    def setUp(self):
        self.credential = User(username="clk3-shapes")
        self.actor = ActorRef(pk=101, identity_id=uuid4(), access_epoch=3)
        self.commands = _commands(self.credential)

    def test_six_clock_shapes_match_the_commissioned_projection(self):
        for entry, command in self.commands:
            shape = intake_shape(entry, command)
            with self.subTest(entry=entry):
                self.assertEqual(shape.request_reference, command.request_reference)
                self.assertEqual(shape.idempotency_key, command.idempotency_key)
                self.assertEqual(shape.omitted, ())
                self.assertIsNone(shape.target_organism_id)

        founding = intake_shape(*self.commands[0])
        self.assertEqual(founding.target, {})
        self.assertEqual(
            founding.command,
            {
                "name": "A Clocked Organism",
                "purpose": "A purpose for the clocked organism.",
                "vision": "A vision.",
                "mission": "A mission.",
                "values": "Values.",
                "accepted_terms_version_uuid": self.commands[0][1].accepted_terms_version_uuid,
                "effective_at": None,
            },
        )
        phase = intake_shape(*self.commands[1])
        self.assertEqual(
            phase.target,
            {
                "living_organism_uuid": self.commands[1][1].living_organism_uuid,
                "from_phase": "CONCEIVING",
                "to_phase": "GERMINATING",
            },
        )
        self.assertEqual(phase.command, {"effective_at": None})
        birthing = intake_shape(*self.commands[2])
        self.assertEqual(
            birthing.command,
            {
                "effective_at": None,
                "offering_choice": "FREE",
                "policy_text_sha256": "a" * 64,
                "policy_version": 1,
            },
        )
        adoption = intake_shape(*self.commands[3])
        self.assertEqual(
            adoption.target,
            {
                "living_organism_uuid": self.commands[3][1].living_organism_uuid,
                "rule_uuid": self.commands[3][1].rule_uuid,
                "version": 1,
                "body": {"seats": []},
                "effective_from": T0,
            },
        )
        self.assertEqual(adoption.command, {"effective_at": None})
        circle = intake_shape(*self.commands[4])
        self.assertEqual(
            circle.target,
            {"living_organism_uuid": self.commands[4][1].living_organism_uuid},
        )
        self.assertEqual(circle.command, {"effective_at": None})
        self.assertEqual(
            birthing.target,
            {"living_organism_uuid": self.commands[2][1].living_organism_uuid},
        )
        beta = intake_shape(*self.commands[5])
        self.assertEqual(
            beta.target,
            {"living_organism_uuid": self.commands[5][1].living_organism_uuid},
        )
        self.assertEqual(
            beta.command,
            {
                "occurred_at": T0,
                "policy_version": 1,
                "policy_text_sha256": "b" * 64,
            },
        )

    def test_each_material_field_changes_each_clock_shape_fingerprint(self):
        changes = (
            {
                "name": "Changed Organism",
                "purpose": "Changed purpose.",
                "vision": "Changed vision.",
                "mission": "Changed mission.",
                "values": "Changed values.",
                "accepted_terms_version_uuid": uuid4(),
                "effective_at": T0,
            },
            {
                "living_organism_uuid": uuid4(),
                "from_phase": "GERMINATING",
                "to_phase": "BIRTHING",
                "effective_at": T0,
            },
            {
                "living_organism_uuid": uuid4(),
                "effective_at": T0,
                "offering_choice": "COMPLIMENTARY_BETA",
                "policy_version": 2,
                "policy_text_sha256": "c" * 64,
            },
            {
                "living_organism_uuid": uuid4(),
                "rule_uuid": uuid4(),
                "version": 2,
                "body": {"seats": [{"seat_id": "other"}]},
                "effective_from": T1,
                "effective_at": T0,
            },
            {"living_organism_uuid": uuid4(), "effective_at": T0},
            {
                "living_organism_uuid": uuid4(),
                "occurred_at": T1,
                "policy_version": 2,
                "policy_text_sha256": "c" * 64,
            },
        )
        for (entry, command), material_changes in zip(self.commands, changes):
            original = _fingerprint(entry, command, self.actor)
            for field, value in material_changes.items():
                with self.subTest(entry=entry, field=field):
                    changed = replace(command, **{field: value})
                    self.assertNotEqual(
                        _fingerprint(entry, changed, self.actor),
                        original,
                    )

    def test_omitted_and_stated_effective_time_are_distinct_projections(self):
        for entry, command in self.commands[:5]:
            stated = replace(command, effective_at=T0)
            omitted_shape = intake_shape(entry, command)
            stated_shape = intake_shape(entry, stated)
            with self.subTest(entry=entry):
                self.assertIsNone(omitted_shape.command["effective_at"])
                self.assertEqual(stated_shape.command["effective_at"], T0)
                self.assertNotEqual(
                    _fingerprint(entry, command, self.actor),
                    _fingerprint(entry, stated, self.actor),
                )

        beta_entry, beta_command = self.commands[5]
        with self.assertRaises(TypeError):
            intake_shape(beta_entry, replace(beta_command, occurred_at=None))

    def test_exact_command_types_and_a_received_at_keyword_are_refused(self):
        for index, (entry, command) in enumerate(self.commands):
            wrong_command = self.commands[(index + 1) % len(self.commands)][1]
            with self.subTest(entry=entry, refusal="wrong command type"):
                with self.assertRaises(TypeError):
                    intake_shape(entry, wrong_command)
            kwargs = {field.name: getattr(command, field.name) for field in fields(command)}
            subclass = type(f"{type(command).__name__}Subclass", (type(command),), {})
            with self.subTest(entry=entry, refusal="subclass of the command type"):
                with self.assertRaises(TypeError):
                    intake_shape(entry, subclass(**kwargs))
            kwargs["received_at"] = T0
            with self.subTest(entry=entry, refusal="received_at keyword"):
                with self.assertRaises(TypeError):
                    type(command)(**kwargs)

    def test_beta_shape_keeps_the_pure_policy_format_checks_before_admission(self):
        entry, command = self.commands[5]
        with self.assertRaises(ValueError):
            intake_shape(entry, replace(command, policy_version=True))
        with self.assertRaises(ValueError):
            intake_shape(entry, replace(command, policy_text_sha256="A" * 64))
        with self.assertRaises(ValueError):
            intake_shape(entry, replace(command, policy_text_sha256="bad"))
