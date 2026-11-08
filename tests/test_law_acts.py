"""L-04–L-08. Governed LAW acts and the reading of an office citation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Barrier
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import Max, Model
from django.test import TransactionTestCase

from core import models
from core.organism_contract import OrganismAction
from src.intevia.services import charter_entry
from src.intevia.services import law_acts as law
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.law_records import laws_in_force, resolve_citation
from src.intevia.services.library_service import LibraryService
from src.intevia.services import organism_application_service as gateway
from src.intevia.services import organism_formation_service as formation
from src.intevia.services import root_genesis
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import S015Authority
from tests import root_genesis_support, s015_0021_support as support
from tests import test_sr_definitions as definitions
from tests import test_sr_office_guards as offices
from tests.test_law_records import Document, LibraryFixtureCapability, found, query_one


def command(actor, proposal):
    key = uuid4().hex
    return law.AdoptLawCommand(credential=actor.credential, proposal=proposal,
                               request_reference="law:" + key, idempotency_key=key)


def adoption(lo, doc, law_class="POLICY"):
    return law.LawProposal(organism=lo.organism_id, resource=doc.name, proposal=uuid4(),
                           operation="ADOPT", intent="ADOPT", law_class=law_class, version=1)


def ordinary_birth(*, offering_choice="COMPLIMENTARY_BETA", board_seats=0, elected=()):
    """Use the existing registration, founding, gateway, phases and Birthing commands."""
    tag = uuid4().hex
    founder = support.register_test_learner(username="law-founder-" + tag)
    with transaction.atomic(), connection.cursor() as cur:
        support.seed_fixture_substitute_authority(cur, "law-principal-" + tag)
    at = support.database_now()
    terms = support.registration_terms_version_in_force(effective_at=at, received_at=at)
    result = formation.OrganismFormationService(clock=support.database_now).found_living_organism(
        formation.FoundLivingOrganismCommand(credential=founder.credential, name="LAW " + tag,
            purpose="LAW routes rehearsal", request_reference="found:" + tag, idempotency_key=tag,
            accepted_terms_version_uuid=terms.terms_version_uuid))
    lo = models.LivingOrganism.objects.get(organism_id=result.living_organism_uuid)
    phases = formation.OrganismPhaseAdvanceService(clock=support.database_now)
    def phase(source, target):
        key = uuid4().hex
        phases.advance_phase(formation.PhaseAdvanceCommand(credential=founder.credential,
            living_organism_uuid=lo.organism_id, from_phase=source, to_phase=target,
            request_reference="phase:" + key, idempotency_key=key))
    phase("CONCEIVING", "GERMINATING")
    service = gateway.OrganismApplicationService(
        authority=S015Authority(provider=ProductionApplicationAuthorityProvider()), clock=support.database_now)
    settings = (
        (OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, dict(cq_active=False, governance_enabled=bool(board_seats),
            inherit_portal_cq_settings=False, require_cq_to_change_lo_settings=False,
            require_cq_to_change_application_question=False)),
        (OrganismAction.EFFECT_APPLICATION_SETTINGS, dict(applications_open=True, automatic_acceptance=False,
            processing_responsibility="LO")),
        (OrganismAction.EFFECT_APPLICATION_QUESTION, dict(question_text="Why join?", answer_format="LONG_ANSWER")),
    )
    for action, fields in settings:
        key = uuid4().hex
        at = support.database_now()
        draft = service.save_gateway_draft(gateway.GatewayDraftCommand(credential=founder.credential,
            living_organism_id=lo.organism_id, action=action, fields=fields, occurred_at=at, effective_at=at,
            request_reference="draft:" + key, idempotency_key=key))
        key = uuid4().hex
        at = support.database_now()
        service.effect_gateway_version(gateway.EffectGatewayVersionCommand(credential=founder.credential,
            living_organism_id=lo.organism_id, action=action, version_uuid=draft.version_uuid,
            occurred_at=at, effective_at=at, request_reference="effect:" + key, idempotency_key=key))
    if board_seats:
        from tests.cq_authority_support import form_board
        form_board(lo, founder, board_seats, elected)
    phase("GERMINATING", "BIRTHING")
    text = "Disposable L rehearsal offering. The founder elects the complimentary beta package."
    digest = sha256(text.encode()).hexdigest()
    version = (models.OrganismEntitlementPolicyText.objects.aggregate(n=Max("policy_version"))["n"] or 0) + 1
    models.OrganismEntitlementPolicyText.objects.create(policy_version=version, accepted_text=text, text_sha256=digest)
    key = uuid4().hex
    formation.OrganismBirthingService(clock=support.database_now).birth_living_organism(formation.BirthingCommand(
        credential=founder.credential, living_organism_uuid=lo.organism_id, request_reference="birth:" + key,
        idempotency_key=key, offering_choice=offering_choice, policy_version=version, policy_text_sha256=digest))
    return lo, founder


def add_member(lo):
    with transaction.atomic(), connection.cursor() as cur:
        _, identity, _ = offices._add_member(cur, {"lo": lo.pk, "basis": lo.founding_authority_basis_id},
                                             uuid4().hex, support.database_now())
    return models.Identity.objects.get(pk=identity)


def nominate_for_office(fx, row):
    """Create one genuinely pending nomination under this definition."""
    key = uuid4().hex
    with transaction.atomic(), connection.cursor() as cursor:
        person, _, membership = offices._add_member(cursor, fx.ids, key, support.database_now())
    with transaction.atomic(), connection.cursor() as cursor:
        _, nomination, _ = offices._propose(cursor, fx.ids, proposer_id=fx.actor.pk,
            proposer_membership_id=fx.ids["membership"], nominee_membership_id=person,
            nominee_membership_uuid=membership, definition_id=row.pk, definition_uuid=row.role_uuid,
            at=support.database_now(), tag=key)
    return nomination


class LawActTests(TransactionTestCase):
    def test_competing_adoptions_replay_supersession_and_retirement(self):
        """L-04. Two genuine first commands arbitrate one window. Replay and history keep its exact cause."""
        ids = found()
        lo = models.LivingOrganism.objects.get(pk=ids["lo"])
        actor = models.Identity.objects.get(pk=ids["identity"])
        doc = Document(actor)
        attempts = [command(actor, adoption(lo, doc)) for _ in range(2)]
        start = Barrier(2)
        def compete(cmd):
            try:
                start.wait(timeout=15)
                return law.OrganismLawService().adopt_law(cmd)
            finally:
                connections["default"].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, attempts))
        self.assertCountEqual([r.status for r in results], ["ADOPT", "REFUSED"])
        i = next(i for i, r in enumerate(results) if r.status == "ADOPT")
        service = law.OrganismLawService()
        for cmd, result in zip(attempts, results, strict=True):
            self.assertEqual(service.adopt_law(cmd), result)
        winner = attempts[i]
        event = models.LivingOrganismEvent.objects.get(event_uuid=results[i].event)
        actor_state = query_one("SELECT actor_state FROM public.core_livingorganismevent WHERE id = %s", [event.pk])
        enterer = query_one("SELECT enterer_identity_id FROM public.core_livingorganismevent WHERE id = %s", [event.pk])
        self.assertEqual((event.effective_at, actor_state, enterer),
                         (event.recorded_at, "LIVING_ORGANISM", actor.pk))
        self.assertEqual(results[i].route, "FOUNDER_CONSTITUTIVE")
        self.assertEqual(models.CommandIntake.objects.filter(action="ADOPT_LAW").count(), 2)
        self.assertEqual(models.OrganismCommandReceipt.objects.filter(action="ADOPT_LAW").count(), 2)
        self.assertLess(event.received_at, event.effective_at)
        successor = doc.next_version()
        doc.transition("publish_library_resource")
        second = replace(winner.proposal, proposal=uuid4(), predecessor=winner.proposal.proposal, version=successor.version_number)
        downgrade = replace(second, proposal=uuid4(), law_class="PROCEDURE")
        self.assertIn("lower class", service.adopt_law(command(actor, downgrade)).message)
        second_act = service.adopt_law(command(actor, second))
        self.assertEqual(second_act.status, "ADOPT")
        self.assertEqual(query_one("""SELECT b.in_force_until=n.in_force_from FROM public.core_organismlawbinding b
            JOIN public.core_organismlawbinding n ON n.predecessor_id=b.binding_uuid WHERE n.binding_uuid=%s""",
            [second.proposal]), True)
        final = replace(second, proposal=uuid4(), predecessor=second.proposal, operation="RETIRE", intent="RETIRE")
        retired = service.adopt_law(command(actor, final))
        self.assertEqual(retired.status, "RETIRE")
        self.assertEqual(query_one("SELECT count(*) FROM public.core_organismlawbinding WHERE in_force_until IS NULL"), 0)
        self.assertEqual(query_one("SELECT count(*) FROM public.core_organismlawending"), 2)
        self.assertEqual(service.adopt_law(winner), results[i])

    def test_protected_authority_members_and_root(self):
        """L-05. Count all members. An independent agreement closes a pair; neither recusal nor an ordinary office opens root law."""
        service = law.OrganismLawService()
        lo, founder = ordinary_birth()
        doc = Document(founder)
        spec = replace(adoption(lo, doc, "ARTICLE"), reserves_bylaws=True)
        self.assertEqual(service.adopt_law(command(founder, spec)).route, "SOLE_MEMBER_COORDINATOR")
        bylaw_doc = Document(founder)
        bylaw = adoption(lo, bylaw_doc, "BYLAW")
        self.assertEqual(service.adopt_law(command(founder, bylaw)).status, "ADOPT")
        self.assertEqual(query_one("SELECT owner_kind FROM public.core_organismlawbinding WHERE binding_uuid=%s",
                                   [bylaw.proposal]), "MEMBERS")
        member = add_member(lo)
        pair = replace(spec, proposal=uuid4(), predecessor=spec.proposal, operation="RETIRE", intent="RETIRE")
        first = command(founder, pair)
        self.assertEqual(service.adopt_law(first).status, "AGREE")
        self.assertEqual(query_one("SELECT count(*) FROM public.core_organismlawending"), 0)
        duplicate = service.adopt_law(command(founder, pair))
        self.assertEqual(duplicate.status, "REFUSED")
        self.assertIn("start a fresh proposal", duplicate.message)
        self.assertEqual(service.adopt_law(command(member, replace(pair, requested_route="SOLE_MEMBER_COORDINATOR"))).status, "REFUSED")
        completion = service.adopt_law(command(member, pair))
        self.assertEqual((completion.status, completion.route), ("RETIRE", "UNANIMOUS_PAIR"))
        self.assertEqual(service.adopt_law(first).status, "AGREE")
        add_member(lo)
        frozen = replace(spec, proposal=uuid4(), predecessor=spec.proposal)
        refusal = service.adopt_law(command(founder, frozen))
        self.assertEqual(refusal.status, "REFUSED")
        self.assertIn("collective", refusal.message)
        # R provides the actual root office. A nominee and an ordinary founder hold no root mandate.
        root = root_genesis.execute(root_genesis_support.command())
        genesis = models.RootGenesis.objects.get(organism_uuid=root.organism_id)
        steward = root.founder_identity
        root_doc = Document(steward)
        platform = replace(adoption(root, root_doc, "ARTICLE"), scope="PLATFORM")
        for person in (founder, genesis.nominee):
            self.assertEqual(service.adopt_law(command(person, platform)).status, "REFUSED")
        accepted = service.adopt_law(command(steward, platform))
        self.assertEqual((accepted.status, accepted.route), ("ADOPT", "FOUNDER_STEWARD"))
        self.assertEqual(query_one("SELECT plan->>'root_office' IS NOT NULL FROM public.core_lawact WHERE proposal=%s",
                                  [platform.proposal]), True)
        root_copy = replace(platform, scope="LIVING_ORGANISM", proposal=uuid4())
        self.assertEqual(service.adopt_law(command(steward, root_copy)).status, "ADOPT")
        root_doc.next_version()
        root_doc.transition("publish_library_resource")
        disagreement = replace(root_copy, version=2, predecessor=root_copy.proposal, proposal=uuid4())
        self.assertIn("disagree", service.adopt_law(command(steward, disagreement)).message)
        outsider = replace(platform, organism=lo.organism_id, proposal=uuid4())
        self.assertIn("Only INTEVIA", service.adopt_law(command(founder, outsider)).message)


    def test_nominations_deferral_and_closed_routes(self):
        """L-06. Preview and defer a real pending nomination. The receipt cannot be forged or replayed as an adoption."""
        fx = definitions.SRDefinitionCommandTests()
        fx.setUp()
        service = law.OrganismLawService()
        doc = Document(fx.actor)
        initial = adoption(fx.organism, doc)
        self.assertEqual(service.adopt_law(command(fx.actor, initial)).status, "ADOPT")
        # The definition command records the version/locator under its ordinary authority.
        definition = fx._command()
        citation = replace(definition.content.provisions[0], provision_reference="law-ref:" + doc.name + "#1",
                           provision_version="1")
        office = fx._success(replace(definition, content=replace(definition.content, provisions=(citation,))))
        row = models.OrganismRoleDefinition.objects.get(role_uuid=office.role_definition_uuid)
        nomination = nominate_for_office(fx, row)
        retire = replace(initial, predecessor=initial.proposal, proposal=uuid4(), operation="RETIRE", intent="RETIRE")
        preview = service.preview_adoption(credential=fx.actor.credential, proposal=retire)
        self.assertEqual(preview["affected_count"], 1)
        self.assertEqual(preview["affected"]["nominations"], [str(nomination)])
        self.assertEqual(preview["affected"]["citations"][0]["locator"], "1")
        version = doc.service.create_successor_version(identity=doc.author, resource_id=doc.name,
            content="\ufeff[#1] First version.\n[#2] A separate clause.", evidence_reference="law:witness:unchanged-clause")
        doc.transition("publish_library_resource")
        unchanged_clause = replace(initial, proposal=uuid4(), predecessor=initial.proposal, version=version.version_number)
        candidate = service.preview_adoption(credential=fx.actor.credential, proposal=unchanged_clause)
        self.assertEqual(candidate["affected_count"], 0)
        # A BOM on a later line belongs to the clause text, even before a marker.
        self.assertEqual(query_one("SELECT content FROM public.l_clauses(%s) WHERE locator='1'",
                                   ["\ufeff[#1] Text\n\ufeff[#2] More"]), " Text\n\ufeff[#2] More")
        reclassified = replace(unchanged_clause, consequence_class="DECLARATORY", proposal=uuid4())
        consequence = service.preview_adoption(credential=fx.actor.credential, proposal=reclassified)
        self.assertEqual(consequence["affected"]["nominations"], [str(nomination)])
        self.assertEqual([c["effect"] for c in consequence["affected"]["citations"]], ["RECLASSIFIED"])
        self.assertNotEqual(consequence["affected_reference"], candidate["affected_reference"])
        self.assertEqual(service.adopt_law(command(fx.actor, reclassified)).status, "REFUSED")
        hold = replace(reclassified, operation="DEFER", affected_reference=consequence["affected_reference"])
        self.assertEqual(service.adopt_law(command(fx.actor, hold)).status, "DEFER")
        for body, effect in (("[#1] Changed words.", "CHANGED"), ("[#2] Another clause.", "GONE")):
            with self.subTest(effect=effect):
                successor = doc.service.create_successor_version(identity=doc.author, resource_id=doc.name,
                    content=body, evidence_reference="law:witness:" + effect)
                doc.transition("publish_library_resource")
                proposal = replace(unchanged_clause, proposal=uuid4(), version=successor.version_number,
                                   law_class="ARTICLE")
                result = service.preview_adoption(credential=fx.actor.credential, proposal=proposal)
                self.assertEqual(result["affected"]["nominations"], [str(nomination)])
                self.assertEqual([c["effect"] for c in result["affected"]["citations"]], [effect])
        outdated = replace(retire, operation="DEFER", affected_reference=candidate["affected_reference"])
        self.assertEqual(service.adopt_law(command(fx.actor, outdated)).status, "REFUSED")
        blocked = service.adopt_law(command(fx.actor, retire))
        self.assertIn("Pending nominations", blocked.message)
        postpone = replace(retire, operation="DEFER", affected_reference=preview["affected_reference"])
        deferred = command(fx.actor, postpone)
        act = service.adopt_law(deferred)
        self.assertEqual(act.status, "DEFER")
        self.assertEqual(service.adopt_law(deferred), act)
        self.assertEqual(query_one("SELECT count(*) FROM public.core_organismlawending"), 0)
        self.assertEqual(models.ContextualRoleAssignment.objects.get(assignment_uuid=nomination).state, "PROPOSED")
        for option in ("RESOLVE", "PROCEED_AND_CLOSE"):
            denied = service.adopt_law(command(fx.actor, replace(retire, operation=option)))
            self.assertEqual(denied.message, "Closing or resolving nominations is not yet available. Defer the change instead.")
        for feature in ("CIRCLE_DELEGATION", "ISSUE_CIRCLE_TERMS", "RESOLUTION_LABEL"):
            denied = service.adopt_law(command(fx.actor, replace(retire, feature=feature)))
            self.assertIn("not yet available", denied.message)
        receipt = models.OrganismCommandReceipt.objects.get(action="ADOPT_LAW", idempotency_key=deferred.idempotency_key)
        with self.assertRaises(IntegrityError) as forged, transaction.atomic():
            forged_receipt = models.OrganismCommandReceipt(actor=fx.actor, action="ADOPT_LAW", idempotency_key=uuid4().hex,
                request_reference=deferred.request_reference, payload_fingerprint=receipt.payload_fingerprint,
                authority_decision_reference=receipt.authority_decision_reference, lineage_reference=receipt.lineage_reference,
                result_payload={"status":"ADOPT"})
            Model.save(forged_receipt, force_insert=True)
        self.assertEqual(forged.exception.__cause__.sqlstate, "23514")
        self.assertEqual(UUID(act.event), models.LivingOrganismEvent.objects.get(event_uuid=act.event).event_uuid)

    def test_inherited_citation_keeps_exact_version_across_successors_and_gap(self):
        """L-07. Compare each successor to the original clause on the root's platform chain."""
        office_fixture = definitions.SRDefinitionCommandTests()
        office_fixture.setUp()
        subject = office_fixture.organism.organism_id
        root = root_genesis.execute(root_genesis_support.command())
        steward = root.founder_identity
        doc = Document(steward)
        service = law.OrganismLawService()

        def enact(spec):
            outcome = service.adopt_law(command(steward, spec))
            self.assertEqual(outcome.status, spec.operation, outcome.message)
            return spec

        def successor(previous, body, law_class):
            version = doc.service.create_successor_version(
                identity=doc.author, resource_id=doc.name, content=body,
                evidence_reference="law:resolver:" + uuid4().hex,
            )
            doc.transition("publish_library_resource")
            return enact(replace(previous, version=version.version_number, predecessor=previous.proposal,
                                 proposal=uuid4(), law_class=law_class))

        def cite(spec=None, locator=None):
            request = office_fixture._command()
            if spec is not None:
                provision = replace(request.content.provisions[0], provision_reference=f"law-ref:{doc.name}#{locator}",
                                    provision_version=str(spec.version))
                request = replace(request, content=replace(request.content, provisions=(provision,)))
            result = office_fixture._success(request)
            return models.OrganismRoleDefinitionProvision.objects.get(
                definition__role_uuid=result.role_definition_uuid
            ).provision_uuid

        first = enact(replace(adoption(root, doc), scope="PLATFORM"))
        original = cite(first, "1")
        legacy = cite()
        self.assertEqual(resolve_citation(subject, legacy)["now"]["reason"], "NO_VERSION_LINK")
        self.assertIsNone(resolve_citation(root.organism_id, original))
        first_read = resolve_citation(subject, original)
        self.assertEqual(first_read["at_act"]["status"], "IN_FORCE")
        self.assertEqual((first_read["cited"]["resource_reference"], first_read["cited"]["cited_version"],
                          first_read["cited"]["cited_number"], first_read["cited"]["locator"]),
                         (doc.name, doc.first.pk, 1, "1"))
        self.assertEqual(first_read["cited"]["binding"]["binding_uuid"], first.proposal)
        self.assertEqual(first_read["cited"]["binding"]["issuer"], root.organism_id)
        self.assertEqual(first_read["cited"]["binding"]["scope_key"], "PLATFORM")

        same = successor(first, "[#two] Another clause.\n[#three] Kept.\n[#1] First version.", "POLICY")
        second_read = resolve_citation(subject, original)
        self.assertEqual(second_read["now"]["status"], "EQUIVALENT")
        self.assertEqual(second_read["now"]["governing"]["version_number"], 2)
        self.assertEqual(second_read["now"]["governing"]["binding_uuid"], same.proposal)
        second_cut = support.database_now()
        removed = cite(same, "two")
        reclassified = cite(same, "three")
        changed = successor(same, "[#1] Changed.\n[#three] Kept.", "ARTICLE")
        for citation, status in ((original, "CHANGED"), (removed, "GONE"), (reclassified, "RECLASSIFIED")):
            with self.subTest(status=status):
                reading = resolve_citation(subject, citation)
                self.assertEqual(reading["now"]["status"], status)
                self.assertEqual(reading["now"]["first_divergence"]["binding"]["binding_uuid"], changed.proposal)
                self.assertEqual(reading["at_act"]["status"], "IN_FORCE")

        restored = successor(changed, "[#1] First version.", "ARTICLE")
        # Hold effective time current and rewind only knowledge, then the reverse.
        later = support.database_now()
        for at, known in ((later, second_cut), (second_cut, later)):
            reading = resolve_citation(subject, original, at=at, known_at=known)
            self.assertEqual(reading["at_act"]["governing"]["binding_uuid"], same.proposal)
            self.assertEqual(reading["at_act"]["status"], "EQUIVALENT")
            self.assertEqual(reading["now"]["governing"]["binding_uuid"], restored.proposal)
            self.assertEqual(reading["now"]["first_divergence"]["binding"]["binding_uuid"], changed.proposal)
            self.assertEqual(reading["cited"], first_read["cited"])

        # The same unresolved nomination holds a change to its LO's own law
        # but allows the root to retire platform law through the governed route.
        def nominate(provision):
            row = models.OrganismRoleDefinitionProvision.objects.select_related("definition").get(provision_uuid=provision)
            return nominate_for_office(office_fixture, row.definition)

        legacy_nomination = nominate(legacy)
        local_doc = Document(office_fixture.actor)
        local = adoption(office_fixture.organism, local_doc)
        self.assertEqual(service.adopt_law(command(office_fixture.actor, local)).status, "ADOPT")
        local_retire = replace(local, proposal=uuid4(), predecessor=local.proposal, operation="RETIRE", intent="RETIRE")
        local_read = service.preview_adoption(credential=office_fixture.actor.credential, proposal=local_retire)
        self.assertEqual(local_read["affected"]["nominations"], [str(legacy_nomination)])
        self.assertEqual([c["effect"] for c in local_read["affected"]["citations"]], ["UNRESOLVED"])

        enact(replace(restored, proposal=uuid4(), predecessor=restored.proposal, operation="RETIRE", intent="RETIRE"))
        in_gap = support.database_now()
        retirement = resolve_citation(subject, original)["now"]
        self.assertEqual(retirement["status"], "RETIRED")
        self.assertIsNone(retirement["governing"])
        resumed = enact(replace(restored, proposal=uuid4(), predecessor=restored.proposal))
        after = support.database_now()
        self.assertEqual(resolve_citation(subject, original, at=in_gap, known_at=in_gap)["at_act"]["status"], "RETIRED")
        replay = resolve_citation(subject, original, at=in_gap, known_at=after)
        self.assertEqual(replay["at_act"]["status"], "GAP")
        self.assertIsNone(replay["at_act"]["governing"])
        self.assertTrue(replay["now"]["crossed_gap"])
        self.assertEqual(replay["now"]["governing"]["binding_uuid"], resumed.proposal)
        self.assertEqual(replay["cited"], first_read["cited"])
        linked_nomination = nominate(original)
        retirement = replace(resumed, proposal=uuid4(), predecessor=resumed.proposal, operation="RETIRE", intent="RETIRE")
        platform_read = service.preview_adoption(credential=steward.credential, proposal=retirement)
        self.assertEqual(platform_read["affected"]["nominations"], [str(linked_nomination)])
        self.assertEqual([c["effect"] for c in platform_read["affected"]["citations"]], ["RETIRED"])
        self.assertEqual(service.adopt_law(command(steward, retirement)).status, "REFUSED")


    def test_charters_enter_whole_and_inherited_protection_refuses_retirement(self):
        """L-08. Ratified bytes, paired adoption, whole citation, binding inheritance and durable refusal."""
        office = definitions.SRDefinitionCommandTests()
        office.setUp()
        root = root_genesis.execute(root_genesis_support.command())
        founder = root.founder_identity
        role, _ = models.Role.objects.get_or_create(name="LAW library fixture role")
        models.ProfileRole.objects.get_or_create(identity=founder, role=role)
        request = dict(credential=founder.credential, library_authority=ContributionAuthority(LibraryFixtureCapability()),
                       request_id=uuid4())
        library = LibraryService(authority=request["library_authority"])
        with self.assertRaises(ValidationError):
            library.create_resource(identity=founder.credential, resource_id="law-empty-" + uuid4().hex,
                                    content="\ufeff \n", exact_content=True, evidence_reference="fixture:blank")
        execute = law.OrganismLawService._execute_admitted

        def interrupt_second(service, cmd, bound):
            if cmd.proposal.scope == "LIVING_ORGANISM":
                self.assertEqual(query_one("SELECT count(*) FROM public.core_organismlawbinding"), 1)
                raise RuntimeError("L-08 interruption at the second binding")
            return execute(service, cmd, bound)

        with patch.object(law.OrganismLawService, "_execute_admitted", interrupt_second):
            with self.assertRaisesRegex(RuntimeError, "L-08 interruption"):
                charter_entry.enter_constitutional_charters(**request)
        self.assertEqual(query_one("SELECT count(*) FROM public.core_organismlawbinding"), 0)
        self.assertFalse(models.OrganismCommandReceipt.objects.filter(action="ADOPT_LAW").exists())

        acts = charter_entry.enter_constitutional_charters(**request)
        self.assertEqual(len(acts), 4)
        self.assertEqual({a.route for a in acts}, {"FOUNDER_STEWARD"})
        self.assertEqual(charter_entry.enter_constitutional_charters(**request), acts)
        self.assertEqual(models.OrganismCommandReceipt.objects.filter(action="ADOPT_LAW").count(), 4)
        local = laws_in_force(root.organism_id)
        inherited = laws_in_force(office.organism.organism_id)
        self.assertEqual((len(local), len(inherited)), (4, 2))
        for charter in charter_entry.CHARTERS:
            with self.subTest(instrument=charter.filename):
                version = models.LibraryResourceVersion.objects.get(resource__resource_id=charter.resource)
                source = (Path(__file__).resolve().parents[1] / "docs/constitution" / charter.filename).read_bytes()
                self.assertEqual(version.content.encode("utf-8"), source)
                self.assertEqual(sha256(source).hexdigest(), charter.digest)
                self.assertTrue(source.startswith(b"\xef\xbb\xbf") and source.endswith(b"\n"))
                self.assertTrue(version.evidence_references.filter(reference=charter.ratification,
                                    transition__command="create_library_resource", supplied_by=founder).exists())
                copies = [row for row in local if row["library_reference"] == charter.resource]
                self.assertEqual({row["scope"] for row in copies}, {"PLATFORM", f"LO:{root.organism_id}"})
                self.assertEqual({(row["version"], row["class_name"]) for row in copies}, {(version.pk, "ARTICLE")})
                events = models.LivingOrganismEvent.objects.filter(event_uuid__in=[row["act"] for row in copies])
                self.assertEqual(len(events), 2)
                for event in events:
                    self.assertEqual(event.effective_at, event.recorded_at)
                    self.assertGreater(event.effective_at.date(), charter_entry.RATIFIED_ON)
                platform, = [row for row in inherited if row["library_reference"] == charter.resource]
                self.assertTrue(platform["inherited"])
                self.assertEqual(platform["binding"], next(row["binding"] for row in copies if row["scope"] == "PLATFORM"))
                definition = office._command()
                link = replace(definition.content.provisions[0], provision_reference="law-ref:" + charter.resource,
                               provision_version=str(version.version_number))
                created = office._success(replace(definition, content=replace(definition.content, provisions=(link,))))
                provision = models.OrganismRoleDefinitionProvision.objects.get(definition__role_uuid=created.role_definition_uuid)
                reading = resolve_citation(office.organism.organism_id, provision.provision_uuid)
                self.assertEqual((reading["cited"]["cited_version"], reading["cited"]["locator"],
                                  reading["now"]["status"]), (version.pk, None, "IN_FORCE"))
                for actor, subject in ((office.actor, office.organism), (founder, root)):
                    retirement = law.LawProposal(organism=subject.organism_id, resource=charter.resource, proposal=uuid4(),
                                                 operation="RETIRE", intent="RETIRE", law_class="ARTICLE", version=1,
                                                 scope="PLATFORM", predecessor=platform["binding"])
                    cmd = command(actor, retirement)
                    refused = law.OrganismLawService().adopt_law(cmd)
                    self.assertEqual(refused.status, "REFUSED")
                    self.assertIn("explicit ruling made outside this route, under GRP-13", refused.message)
                    receipt = models.OrganismCommandReceipt.objects.get(actor=actor, action="ADOPT_LAW",
                                                                         idempotency_key=cmd.idempotency_key)
                    decision = models.AuthorityDecision.objects.get(decision_reference=receipt.authority_decision_reference)
                    self.assertEqual((decision.outcome, receipt.result_payload["status"]), ("REFUSED", "REFUSED"))
        self.assertEqual(laws_in_force(office.organism.organism_id), inherited)
        self.assertEqual(query_one("SELECT count(*) FROM public.core_organismlawending"), 0)
