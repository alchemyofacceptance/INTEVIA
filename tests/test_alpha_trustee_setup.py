"""Trustee setup entry-point witnesses. Test admission and break cases accompany the carrier."""
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from core import models
from core.management.commands import setup_alpha_trustee as setup


class AlphaDatabaseBoundaryTests(SimpleTestCase):
    def test_nonqualifying_database_stops_before_provisioning(self):
        """A1-DB: refusal before provisioning. CLI boundary; no SQL permitted."""
        # A protected name, outside both disposable families. The owned-database argument
        # equals the configured name, so only the family check can be what refuses.
        name = "intevia_production"
        with patch.dict(connection.settings_dict, NAME=name):
            with self.assertRaisesMessage(CommandError, "named disposable database does not qualify"):
                call_command("setup_alpha_trustee", "texts", owned_database=name, stdout=StringIO())

    def test_owned_name_must_equal_the_configured_name(self):
        """A1-DB2: two qualifying names that differ still refuse before provisioning. No SQL permitted."""
        configured, owned = "intevia_alpha_throwaway_film1", "intevia_alpha_throwaway_film2"
        with patch.dict(connection.settings_dict, NAME=configured):
            self.assertTrue(
                setup.fixture_terms_permitted("default"),
                "Control: the configured name qualifies, so the refusal below is the owned-name comparison's.",
            )
            with self.assertRaisesMessage(CommandError, "named disposable database does not qualify"):
                call_command("setup_alpha_trustee", "texts", owned_database=owned, stdout=StringIO())


class AlphaTrusteeSetupTests(TransactionTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.records = self.directory / "records"
        self.records.mkdir()
        authority = Path(__file__).resolve().parents[1] / "docs/governance/root_genesis"
        for filename, _ in setup.ROOT_FILES.values():
            (self.records / filename).write_bytes((authority / filename).read_bytes())
        self.inputs = self.directory / "inputs.json"
        self.accounts = {
            key: {"password": token_urlsafe(32), "accept_terms": True}
            for key in (*setup.ROOT_USERS, *setup.PEOPLE)
        }

    def supply(self, labels, *, departure=False):
        self.inputs.write_text(json.dumps({
            "accept_genesis_terms": True, "accept_founding_terms": True,
            "confirm_treasurer_departure": departure,
            "accounts": {label: self.accounts[label] for label in labels},
        }), encoding="utf-8")

    def stage(self, name):
        out = StringIO()
        call_command("setup_alpha_trustee", name, owned_database=connection.settings_dict["NAME"],
                     inputs=self.inputs, records=self.records, stdout=out)
        return out.getvalue()

    def test_root_resumes_from_issued_bytes_and_refuses_changed_source(self):
        """A1-ROOT: same issued root receipt on retry. Command/GENESIS integration; isolated PG database."""
        self.supply(setup.ROOT_USERS)
        issued = self.stage("texts")
        first = self.stage("root")
        self.inputs.unlink()
        self.assertEqual(self.stage("texts"), issued)
        self.assertEqual(self.stage("root"), first)
        record = models.RootGenesis.objects.get()
        self.assertIn(str(record.designation_uuid), first)
        self.assertIn(str(record.organism_uuid), first)
        self.assertEqual(record.canonical_reference, "intevia-root-lo")

        path = self.records / "PRINCIPAL_INVOCATION_RECEIVED_v1.txt"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesMessage(CommandError, "differs from its issued bytes"):
            self.stage("root")
        self.assertEqual(models.RootGenesis.objects.count(), 1)
        self.assertEqual(models.LearnerRegistration.objects.count(), 2)
        self.assertEqual(models.PlatformTermsVersion.objects.filter(version_label=setup.TERMS_LABEL).count(), 1)
        offering = models.OrganismEntitlementPolicyText.objects.get(accepted_text=setup.OFFERING)
        self.assertEqual(offering.text_sha256, sha256(setup.OFFERING.encode()).hexdigest())

    def test_interrupted_people_stage_resumes_and_founding_replays(self):
        """A1-FOUND: resume registrations and return the same LO/phase. Command integration; isolated PG database."""
        self.supply([*setup.ROOT_USERS, "founder"])
        self.stage("texts")
        self.stage("root")
        with self.assertRaisesMessage(CommandError, "input for trustee-two is missing"):
            self.stage("people")
        registered = models.LearnerRegistration.objects.get(identity__canonical_username=setup.PEOPLE["founder"][0])
        self.assertEqual(models.LearnerRegistration.objects.count(), 3, "The first demonstration registration must commit.")
        self.supply(self.accounts)
        people = self.stage("people")
        self.assertIn(str(registered.registration_uuid), people)
        first = self.stage("found")
        self.assertEqual(self.stage("people"), people)
        self.assertEqual(self.stage("found"), first, "The same two committed receipts must be served.")
        self.assertEqual(models.LearnerRegistration.objects.count(), 7)
        demo = models.LivingOrganism.objects.get(platform_root=False)
        self.assertEqual(demo.founder_identity_id, registered.identity_id)
        events = models.LivingOrganismEvent.objects.filter(living_organism=demo)
        self.assertIn(str(events.get(action="FOUND").event_uuid), first)
        self.assertIn(str(events.get(action="PHASE_ADVANCE").event_uuid), first)

    def through(self, last):
        self.supply(self.accounts)
        stages = ("texts", "root", "people", "found", "formation", "birth",
                  "policies", "treasurer", "trustees", "activate")
        for stage in stages[:stages.index(last) + 1]:
            self.stage(stage)
        return models.LivingOrganism.objects.get(platform_root=False)

    @override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_film_law_titles_follow_real_adoption_and_successor(self):
        from uuid import uuid4

        from django.urls import reverse
        from src.intevia.services import law_acts, library_laws, library_policy
        from tests.test_law_acts import command
        from tests.test_uc1_office_page import signed_in

        lo = self.through("policies")
        scope = dict(organism_uuid=lo.organism_id, credential=lo.founder_identity.credential)
        client = signed_in(lo.founder_identity)
        listing = reverse("organisation_laws", args=[lo.organism_id])
        first_page = client.get(listing)
        for title in ("Board membership", "Treasurer office"):
            self.assertContains(first_page, f"<strong>{title}</strong>", html=True)
        self.assertContains(first_page, "Policy · Governance")
        first = next(row for row in library_laws.our_laws(**scope)["laws"]
                     if row["library_reference"] == setup.DUTIES_REF)
        version = library_policy.draft_policy(**scope, resource_reference=setup.DUTIES_REF,
            content=setup.DUTIES_TEXT + " The Board reviews payments.", predecessor_version_id=first["version"],
            authors=[lo.founder_identity.identity_id], declare_own_authorship=True, request_uuid=uuid4())
        library_policy.publish_policy(**scope, version_id=version.pk, request_uuid=uuid4())
        proposal = law_acts.LawProposal(organism=lo.organism_id, resource=setup.DUTIES_REF,
            proposal=uuid4(), predecessor=first["binding"], operation="ADOPT", intent="ADOPT",
            law_class="POLICY", version=version.version_number)
        receipt = law_acts.OrganismLawService().adopt_law(command(lo.founder_identity, proposal))
        self.assertEqual(receipt.status, "ADOPT", "The real film policy must have a recorded successor.")
        page = client.get(listing)
        self.assertContains(page, "<strong>Treasurer office</strong>", html=True, msg_prefix="Version 2 must inherit the film title.")
        self.assertContains(page, "alpha-treasurer-duties · Version 2 · In force now")
        detail = client.get(reverse("organisation_law_version", args=[lo.organism_id, proposal.proposal, 2]))
        self.assertContains(detail, '<h1 class="record-heading page-title">Policy: Treasurer office</h1>', html=True)
        self.assertContains(detail, '<p class="meta">alpha-treasurer-duties · Governance · Version 2 · In force now</p>', html=True)
        old_url = reverse("organisation_law_version", args=[lo.organism_id, first["binding"], 1])
        self.assertContains(detail, f'href="{old_url}"')

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_a0_finishes_on_original_appointments_after_interrupted_seating(self):
        """A1c-A0. An interrupted Board setup reaches the authorised A0 exactly once."""
        from src.intevia.services import cq, law_records, library_laws

        lo = self.through("birth")
        circle = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        places = models.CircleParticipation.objects.filter(circle=circle)
        founder_place = places.get()
        self.assertEqual((founder_place.seat_id, founder_place.membership.identity_id),
                         ("trustee-0", lo.founder_identity_id))
        scope = {"credential": lo.founder_identity.credential, "organism_uuid": lo.organism_id}
        self.assertFalse(cq.settings(**scope)["settings"]["enabled"])
        with self.assertRaisesMessage(CommandError, "seat-trustee-three receipt is missing"):
            self.stage("activate")
        self.stage("policies")
        in_force = {law["library_reference"]: law for law in library_laws.our_laws(**scope)["laws"]}
        self.assertEqual(in_force[setup.DUTIES_REF]["content"],
                         "[#approval] The organisation has a Treasurer. The Board of Trustees appoints "
                         "the holder with the nominee's consent. The Treasurer approves ordinary payments.",
                         "The adopted film law must use the Board wording and retain the parser's source space.")
        for ref, content in [(setup.DUTIES_REF, setup.DUTIES_TEXT), (setup.MINIMUM_REF, setup.MINIMUM_TEXT)]:
            self.assertEqual((in_force[ref]["content"], in_force[ref]["decision_route"],
                              in_force[ref]["class_name"], in_force[ref]["inherited"]),
                             (content, "COORDINATOR_ROUTE", "POLICY", False))
        self.stage("treasurer")
        role = models.OrganismRoleDefinition.objects.get(living_organism=lo, code=setup.TREASURER_CODE)
        appointment = models.ContextualRoleAssignment.objects.get(role_definition=role)
        approval = role.provisions.get(relationship="OFFICE_BASIS")
        citation = law_records.resolve_citation(lo.organism_id, approval.provision_uuid)
        self.assertEqual((citation["at_act"]["status"], citation["cited"]["locator"],
                          citation["cited"]["binding"]["binding_uuid"]),
                         ("IN_FORCE", setup.APPROVAL, in_force[setup.DUTIES_REF]["binding"]))
        self.assertEqual(appointment.membership.identity.canonical_username, setup.PEOPLE["treasurer"][0])
        assent = models.OfficeAppointmentConsent.objects.get(assignment=appointment, action="CONSENT")
        self.assertEqual((assent.nominee_membership_id, assent.proposal_event.assignment_id),
                         (appointment.membership_id, appointment.pk))
        assigned = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=appointment, action="ASSIGN")
        self.assertEqual(assigned.resulting_state, "ACTIVE")
        self.assertEqual(places.count(), 1, "The Treasurer must be in office before the other two seats.")
        self.supply(set(self.accounts) - {"trustee-three"})
        with self.assertRaisesMessage(CommandError, "input for trustee-three is missing"):
            self.stage("trustees")
        second = places.get(seat_id="trustee-1")
        self.assertFalse(cq.settings(**scope)["settings"]["enabled"])
        self.assertFalse(models.CircleStateEvent.objects.filter(circle=circle, action="ACTIVATE").exists())
        self.supply(self.accounts)
        with self.assertRaisesMessage(CommandError, "seat-trustee-three receipt is missing"):
            self.stage("activate")
        self.stage("trustees")
        self.assertEqual(list(places.order_by("seat_id").values_list(
            "membership__identity__canonical_username", flat=True)),
            [setup.PEOPLE[name][0] for name in ("founder", "trustee-two", "trustee-three")])
        self.assertEqual(places.get(seat_id="trustee-1").participation_uuid, second.participation_uuid)
        self.assertEqual([place.decision.constitutional_route for place in places.exclude(seat_id="trustee-0")],
                         ["APPOINTMENT", "APPOINTMENT"])
        output = self.stage("activate")
        activation = models.CircleStateEvent.objects.get(circle=circle, action="ACTIVATE", resulting_state="ACTIVE")
        self.assertIn(str(activation.event_uuid), output)
        voting = cq.settings(**scope)
        self.assertEqual((voting["version"], voting["settings"]["enabled"], voting["settings"]["clarity_stage"]),
                         (setup.scenario_id("voting-on"), True, True))
        counts = (models.OrganismCommandReceipt.objects.count(), models.CircleStateEvent.objects.count(),
                  models.OrganismRoleDefinition.objects.count(), models.OfficeAppointmentConsent.objects.count())
        for stage in ("formation", "birth", "policies", "treasurer", "trustees", "activate"):
            self.stage(stage)
        self.assertEqual((models.OrganismCommandReceipt.objects.count(), models.CircleStateEvent.objects.count(),
                          models.OrganismRoleDefinition.objects.count(), models.OfficeAppointmentConsent.objects.count()),
                         counts, "Replaying completed stages must retain the committed records.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.core_cqact WHERE organism_id=%s AND operation='SETTINGS'",
                           [lo.pk])
            self.assertEqual(cursor.fetchone()[0], 1)

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_a1_departure_ends_only_the_treasurers_membership_and_appointment(self):
        """A1c-A1. Voluntary departure opens the Treasurer vacancy; original seats continue."""
        from src.intevia.services import organism_role_definition_service as definitions

        lo = self.through("activate")
        circle = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        places = models.CircleParticipation.objects.filter(circle=circle).order_by("seat_id")
        original = list(places.values_list("participation_uuid", flat=True))
        self.assertEqual(len(original), 3)
        role = models.OrganismRoleDefinition.objects.get(living_organism=lo, code=setup.TREASURER_CODE)
        office = models.ContextualRoleAssignment.objects.get(role_definition=role)
        with self.assertRaisesMessage(CommandError, "has not confirmed departure"):
            self.stage("depart")
        self.assertFalse(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=office, action="END").exists())
        self.supply(self.accounts, departure=True)
        out = self.stage("depart")
        ended = models.OrganismMembershipTransition.objects.get(
            membership=office.membership, reason_class="VOLUNTARY", resulting_state="ENDED")
        end_office = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=office, action="END")
        self.assertEqual((end_office.effective_at, end_office.received_at, end_office.idempotency_key),
                         (ended.effective_at, ended.received_at, ended.idempotency_key))
        self.assertIn(str(ended.event_uuid), out)
        self.assertIn(str(end_office.event_uuid), out)
        self.assertFalse(next(v for v in definitions.board_office_vacancies(lo.organism_id)
                              if v.role_identity == role.role_uuid).filled)
        self.assertEqual(list(places.values_list("participation_uuid", flat=True)), original)
        self.assertFalse(models.CircleParticipationEnd.objects.filter(participation__in=places).exists())
        self.assertEqual(models.OrganismMembershipTransition.objects.filter(
            membership__living_organism=lo, resulting_state="ENDED").count(), 1,
            "Only the Treasurer's membership may end when she leaves; another member's membership ended too.")
        self.assertEqual(models.ContextualRoleAssignmentStateEvent.objects.filter(
            assignment__membership__living_organism=lo, action="END").count(), 1,
            "Only the Treasurer's appointment may end when she leaves; another appointment ended too.")
        self.assertEqual(self.stage("depart"), out)
        self.assertIn("A1 already complete", self.stage("activate"))
        lo.refresh_from_db()
        circle.refresh_from_db()
        self.assertEqual((lo.state, circle.state), ("ACTIVE", "ACTIVE"))
