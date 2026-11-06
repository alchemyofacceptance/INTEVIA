"""LIB-01 to LIB-03. Exact law text, member audience and GRP-13 authority."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from uuid import uuid4
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction

from django.test import SimpleTestCase, TransactionTestCase

from core import models
from src.intevia.services import charter_entry, circle_commands, root_genesis, root_publication
# Bind continuity to the real command context before LIB-10 patches its source.
from src.intevia.services import root_continuity
from src.intevia.services import library_policy as policy
from src.intevia.services.library_service import LibraryService
from src.intevia.services import law_acts as law
from src.intevia.services import organism_membership_service as memberships
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.library_exact_version_contract import DisclosureResult
from src.intevia.services.library_exact_version_policy import (
    ImmutableLibraryBindingProvider, LibraryExactVersionPolicyV1,
)
from src.intevia.services.library_laws import LawReadUnavailable, our_laws
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import S015Authority
from tests import root_genesis_support, s015_0021_support as support
from tests import test_law_acts as law_fixtures
from tests.test_law_records import Document, LibraryFixtureCapability


class LibraryGovernanceTextTests(SimpleTestCase):
    def test_grp13_authorised_text_and_supersession(self):
        """LIB-03. The exact authorised section is in the cycle, with its source draft retired."""
        root = Path(__file__).resolve().parents[1]
        path = root / "docs/constitution/GRP_13_GOVERNED_AMENDMENT_CYCLE.md"
        data = path.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(sha256(data).hexdigest(), "4b7d1eb53f59adef214468e0d64144cc4d4b89093a6c224e4008c1cb18e9e2b4")
        text = data.decode("utf-8-sig")
        heading = "## Charter amendment authority\n"
        self.assertEqual(text.count(heading), 1)
        section = heading + text.split(heading, 1)[1].split("## Human Governance Rule", 1)[0].rstrip() + "\n"
        self.assertEqual(sha256(section.encode("utf-8")).hexdigest(), "b68e7c580c7a51d14383c3d623aaff0171b15c93fb35e88ecf9ecf9485ecca50")
        self.assertFalse((root / "docs/constitution/GRP_13_CHARTER_AMENDMENT_AUTHORITY_DRAFT.md").exists())
        historical = root / "docs/superseded/GRP_13_CHARTER_AMENDMENT_AUTHORITY_DRAFT.md"
        self.assertTrue(historical.read_text(encoding="utf-8").startswith("Status: Historical."))
        for filename, pin in (
            ("FREE_WILL_CHARTER.md", "ef09fca88d32a4fe4dbb7e76f3517fc5db0621ad60bbf0d813730d820eb26fc5"),
            ("CARE_CONSTITUTIONAL_CHARTER.md", "01c70ecc68f0dd5d0ec737d3877bd5dfa2d556c8136387e020dc8da3b8ee111b"),
        ):
            with self.subTest(charter=filename):
                self.assertEqual(sha256((root / "docs/constitution" / filename).read_bytes()).hexdigest(), pin)


    def test_education_alias_reaches_its_original_context(self):
        """LIB-10 / P2g-4. A LIBRARY-only alias refusal cannot intercept EDUCATION."""
        credential = object()
        organism = uuid4()
        class ContextReached(Exception):
            pass
        for alias in ('default', 'education_other'):
            with self.subTest(alias=alias), patch.object(
                root_publication.circles, 'command_context', side_effect=ContextReached
            ) as context:
                with self.assertRaises(ContextReached):
                    root_publication.publish_own(credential=credential, organism_uuid=organism,
                        domain='EDUCATION', version_id=1, alias=alias)
                context.assert_called_once_with(alias, credential, organism)
        with patch.object(root_publication.circles, 'command_context') as context:
            with self.assertRaises(root_publication.PublicationRefused) as refused:
                root_publication.publish_own(credential=credential, organism_uuid=organism,
                    domain='LIBRARY', version_id=1, alias='education_other')
            self.assertEqual(refused.exception.code, 'R_DATABASE_ROUTE_UNAVAILABLE')
            context.assert_not_called()
        self.assertIs(root_continuity.command_context, circle_commands.command_context,
            'LIB-10 must leave continuity bound to the real command context.')


class LibraryLawsTests(TransactionTestCase):
    def test_our_laws_exact_versions_at_two_cuts(self):
        """LIB-01. Bound text at both cuts; own, Circle and inherited provenance."""
        lo, coordinator = law_fixtures.ordinary_birth()
        reader = law_fixtures.add_member(lo)
        circle = circle_commands.form(
            credential=coordinator.credential, organism_uuid=lo.organism_id,
            circle_uuid=uuid4(), kind="GENERIC", purpose="LIB read witness",
            request_uuid=uuid4(),
        )
        service = law.OrganismLawService()
        doc = Document(coordinator)
        first_spec = law_fixtures.adoption(lo, doc)
        first = service.adopt_law(law_fixtures.command(coordinator, first_spec))
        self.assertEqual(first.status, "ADOPT")
        first_event = models.LivingOrganismEvent.objects.get(event_uuid=first.event)
        terms = Document(coordinator)
        terms_spec = replace(law_fixtures.adoption(lo, terms, "TERMS_OF_REFERENCE"),
                             scope="CIRCLE", circle=circle.circle_uuid)
        terms_act = service.adopt_law(law_fixtures.command(coordinator, terms_spec))
        self.assertEqual(terms_act.status, "ADOPT")
        root = root_genesis.execute(root_genesis_support.command())
        founder = root.founder_identity
        role, _ = models.Role.objects.get_or_create(name="LAW library fixture role")
        models.ProfileRole.objects.get_or_create(identity=founder, role=role)
        charter_entry.enter_constitutional_charters(
            credential=founder.credential,
            library_authority=ContributionAuthority(LibraryFixtureCapability()),
            request_id=uuid4(),
        )
        before = support.database_now()
        def read(**cuts):
            return our_laws(credential=reader.credential, organism_uuid=lo.organism_id, **cuts)["laws"]
        def policy(rows):
            return next(row for row in rows if row["library_reference"] == doc.name)
        initial = read(at=before, known_at=before)
        self.assertEqual(len(initial), 4)
        self.assertEqual(policy(initial)["content"], "[#1] First version.")
        self.assertEqual(policy(initial)["version_number"], 1)
        tor, = [r for r in initial if r["library_reference"] == terms.name]
        self.assertEqual((tor["scope_kind"], tor["scope_circle"], tor["class_name"]),
                         ("CIRCLE", circle.circle_uuid, "TERMS_OF_REFERENCE"))
        self.assertFalse(tor["inherited"])
        inherited = [r for r in initial if r["inherited"]]
        self.assertEqual(len(inherited), 2)
        self.assertEqual({r["library_reference"] for r in inherited},
                         {c.resource for c in charter_entry.CHARTERS})
        for row in initial:
            with self.subTest(reference=row["library_reference"]):
                event = models.LivingOrganismEvent.objects.get(event_uuid=row["act"])
                self.assertEqual(row["adoption_received_at"], event.received_at)
                self.assertEqual(row["adoption_recorded_at"], event.recorded_at)
                self.assertEqual(row["in_force_from"], event.effective_at)
                self.assertEqual(row["authority_decision_reference"], event.authority_decision_reference)
                self.assertEqual(row["consequence_class"], "BINDING")
                self.assertNotIn("organisation_received_at", row)
                self.assertEqual(row["content"], models.LibraryResourceVersion.objects.get(pk=row["version"]).content)
                if row["inherited"]:
                    self.assertEqual((row["scope"], row["issuer"], row["decision_route"]),
                                     ("PLATFORM", root.organism_id, "FOUNDER_STEWARD"))
        self.assertEqual(policy(initial)["decision_route"], "COORDINATOR_ROUTE")
        self.assertEqual(policy(initial)["owner_organism_uuid"], lo.organism_id)
        successor = doc.next_version()
        # This exact assertion is the recurring fault's target: current draft v2
        # is different text, while v1 remains the organisation's governing law.
        self.assertEqual(policy(read())["content"], "[#1] First version.")
        doc.transition("publish_library_resource")
        second_spec = replace(first_spec, proposal=uuid4(), predecessor=first_spec.proposal,
                              version=successor.version_number)
        second = service.adopt_law(law_fixtures.command(coordinator, second_spec))
        self.assertEqual(second.status, "ADOPT")
        second_event = models.LivingOrganismEvent.objects.get(event_uuid=second.event)
        after = support.database_now()
        current = policy(read(at=after, known_at=after))
        historical = policy(read(at=before, known_at=after))
        formerly_known = policy(read(at=after, known_at=before))
        self.assertEqual((current["binding"], current["version_number"], current["content"]),
                         (second_spec.proposal, 2, "[#1] Second version."))
        self.assertEqual(current["predecessor"], first_spec.proposal)
        for past in (historical, formerly_known):
            self.assertEqual((past["binding"], past["version_number"], past["content"]),
                             (first_spec.proposal, 1, "[#1] First version."))
            self.assertEqual(past["in_force_from"], first_event.effective_at)
        self.assertEqual(historical["effective_until"], second_event.effective_at)
        self.assertIsNone(formerly_known["effective_until"])
        self.assertEqual({r["binding"] for r in read() if r["inherited"]},
                         {r["binding"] for r in inherited})

    def test_our_laws_disclosure(self):
        """LIB-02. Current member access is law-only, and ends on departure."""
        lo, coordinator = law_fixtures.ordinary_birth()
        member = law_fixtures.add_member(lo)
        outsider = support.register_test_learner(username="lib-outsider-" + uuid4().hex)
        self.assertEqual(our_laws(credential=member.credential, organism_uuid=lo.organism_id)["laws"], [])
        doc = Document(coordinator)
        nonlaw = Document(coordinator)
        spec = law_fixtures.adoption(lo, doc)
        outcome = law.OrganismLawService().adopt_law(law_fixtures.command(coordinator, spec))
        self.assertEqual(outcome.status, "ADOPT")
        past = support.database_now()
        doc.next_version()
        for person in (member, coordinator):
            rows = our_laws(credential=person.credential, organism_uuid=lo.organism_id)["laws"]
            self.assertEqual([(r["library_reference"], r["content"]) for r in rows],
                             [(doc.name, "[#1] First version.")])
        # The general LIBRARY policy still needs its own viewer authority.
        policy = LibraryExactVersionPolicyV1(provider=ImmutableLibraryBindingProvider(
            enabled=True, complete_for_policy=True))
        for item in (doc, nonlaw):
            item.resource.refresh_from_db()
            result, _, _, _ = policy.determine_disclosure(
                identity=member, resource=item.resource, version=item.first,
                evaluated_at=support.database_now(),
            )
            self.assertIs(result, DisclosureResult.HIDDEN)
        read_refusals = []
        for credential, subject in ((outsider.credential, lo.organism_id),
                                    (coordinator.credential, uuid4())):
            with self.assertRaises(LawReadUnavailable) as refused:
                our_laws(credential=credential, organism_uuid=subject, at=past, known_at=past)
            read_refusals.append(str(refused.exception))
        self.assertEqual(read_refusals[0], read_refusals[1])
        membership = models.OrganismMembership.objects.get(identity=member, living_organism=lo)
        route = memberships.OrganismMembershipService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=support.database_now,
        )
        at = support.database_now()
        route.leave_organism(memberships.LeaveOrganismCommand(
            credential=member.credential, membership_uuid=membership.membership_uuid,
            confirmed=True, reason_class="VOLUNTARY", occurred_at=at, effective_at=at,
            idempotency_key=uuid4().hex, request_reference="lib-leave:" + uuid4().hex,
        ))
        membership.refresh_from_db()
        self.assertEqual(membership.state, "ENDED")
        with self.assertRaises(LawReadUnavailable):
            our_laws(credential=member.credential, organism_uuid=lo.organism_id, at=past, known_at=past)
        self.assertEqual(our_laws(credential=coordinator.credential, organism_uuid=lo.organism_id)["laws"][0]["content"],
                         "[#1] First version.")

    def test_coordinator_publishes_and_adopts_policy(self):
        """LIB-04. Real office capability; writer/author/releaser/adopter stay distinct."""
        lo, coordinator = law_fixtures.ordinary_birth()
        author = law_fixtures.add_member(lo)
        before_roles = models.ProfileRole.objects.count()
        reference = 'policy-' + uuid4().hex
        draft_args = dict(credential=coordinator.credential, organism_uuid=lo.organism_id,
            resource_reference=reference, content='[#1] Payments need a recorded approval.',
            authors=[author.identity_id], request_uuid=uuid4())
        first = policy.draft_policy(**draft_args)
        self.assertEqual(policy.draft_policy(**draft_args).pk, first.pk)
        self.assertEqual(models.ProfileRole.objects.count(), before_roles, 'The product path must not fabricate generic role grants.')
        authority_message = "This act requires the selected organisation's effective authority at receipt and execution. CQ is not available yet."
        # The same signed-in non-governor receives identical refusals for an
        # existing LO and an unregistered UUID, for each organisation writer.
        for writer in ('draft', 'publish', 'root_publish'):
            refusals = []
            expected_type = root_publication.PublicationRefused if writer == 'root_publish' else policy.PolicyRefused
            expected_code = 'R_EFFECTIVE_AUTHORITY_REQUIRED' if writer == 'root_publish' else 'LIB_AUTHORITY_SOURCE_REQUIRED'
            for subject in (lo.organism_id, uuid4()):
                with self.subTest(writer=writer, subject=subject), self.assertRaises(expected_type) as refused:
                    if writer == 'draft':
                        policy.draft_policy(**(draft_args | {'credential': author.credential,
                            'organism_uuid': subject, 'request_uuid': uuid4()}))
                    else:
                        publish = policy.publish_root if writer == 'root_publish' else policy.publish_policy
                        publish(credential=author.credential, organism_uuid=subject,
                            version_id=first.pk, request_uuid=uuid4())
                self.assertIs(type(refused.exception), expected_type)
                self.assertEqual(refused.exception.code, expected_code)
                self.assertEqual(str(refused.exception), authority_message)
                refusals.append(str(refused.exception))
            self.assertEqual(refusals[0], refusals[1])
        with self.assertRaises(policy.PolicyRefused):
            policy.draft_policy(**(draft_args | {'content': 'Changed text.'}))
        with self.assertRaises(policy.PolicyRefused):
            policy.publish_policy(credential=author.credential, organism_uuid=lo.organism_id,
                version_id=first.pk, request_uuid=uuid4())
        spec = law.LawProposal(organism=lo.organism_id, resource=reference, proposal=uuid4(),
            operation='ADOPT', intent='ADOPT', law_class='POLICY', version=1)
        service = law.OrganismLawService()
        self.assertEqual(service.adopt_law(law_fixtures.command(coordinator, spec)).status, 'REFUSED')
        with self.assertRaises(policy.PolicyRefused):
            policy.publish_policy(credential=coordinator.credential, organism_uuid=lo.organism_id,
                version_id=first.pk, request_uuid=uuid4())
        policy.declare_authorship(credential=author.credential, version_id=first.pk,
            content_sha256=sha256(first.content.encode()).hexdigest(), authors=[author.identity_id], affirm=True)
        publish_args = dict(credential=coordinator.credential, organism_uuid=lo.organism_id,
            version_id=first.pk, request_uuid=uuid4())
        published = policy.publish_policy(**publish_args)
        self.assertEqual(policy.publish_policy(**publish_args), published)
        self.assertEqual(set(published), {'publication'}, 'Ordinary publishing must not inherit root self-acceptance.')
        self.assertEqual(our_laws(credential=coordinator.credential, organism_uuid=lo.organism_id)['laws'], [])
        adopted = service.adopt_law(law_fixtures.command(coordinator, spec))
        self.assertEqual((adopted.status, adopted.route), ('ADOPT', 'COORDINATOR_ROUTE'))
        earlier = support.database_now()
        membership = models.OrganismMembership.objects.get(identity=author, living_organism=lo)
        departing_at = support.database_now()
        memberships.OrganismMembershipService(authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=support.database_now).leave_organism(memberships.LeaveOrganismCommand(
                credential=author.credential, membership_uuid=membership.membership_uuid, confirmed=True,
                reason_class='VOLUNTARY', occurred_at=departing_at, effective_at=departing_at,
                idempotency_key=uuid4().hex, request_reference='lib-author-leaves:'+uuid4().hex))
        # Attribution remains history; the departed author has no veto over the
        # current Coordinator's next version and supplies no release authority.
        second = policy.draft_policy(credential=coordinator.credential, organism_uuid=lo.organism_id,
            resource_reference=reference, content='[#1] Payments need two recorded approvals.',
            authors=[author.identity_id, coordinator.identity_id], request_uuid=uuid4(),
            predecessor_version_id=first.pk, declare_own_authorship=True)
        carried = policy._rows('SELECT author_id,inherited_from,declaration FROM public.core_libauthorclaim WHERE version_id=%s', [second.pk])
        self.assertEqual({r['author_id'] for r in carried}, {author.pk, coordinator.pk})
        self.assertIsNotNone(next(r['inherited_from'] for r in carried if r['author_id']==author.pk))
        self.assertEqual(next(r['declaration'] for r in carried if r['author_id']==author.pk), policy.CARRIED_ATTRIBUTION)
        policy.publish_policy(credential=coordinator.credential, organism_uuid=lo.organism_id,
            version_id=second.pk, request_uuid=uuid4())
        next_spec = replace(spec, proposal=uuid4(), predecessor=spec.proposal, version=2)
        self.assertEqual(service.adopt_law(law_fixtures.command(coordinator, next_spec)).status, 'ADOPT')
        past, = our_laws(credential=coordinator.credential, organism_uuid=lo.organism_id, at=earlier)['laws']
        present, = our_laws(credential=coordinator.credential, organism_uuid=lo.organism_id)['laws']
        self.assertEqual((past['version'], past['content'], present['version'], present['content']),
                         (first.pk, first.content, second.pk, second.content))
        for row in (past, present):
            self.assertEqual(row['authority_source']['kind'], 'LAW_AUTHORITY_DECISION')
            self.assertEqual(row['authority_source']['route'], 'COORDINATOR_ROUTE')
            self.assertIsNotNone(row['authority_source']['office'])
        release = policy._rows('SELECT actor_id,authority_source FROM public.core_libpublicationfact WHERE fact_uuid=%s', [published['publication']])[0]
        self.assertEqual(release['actor_id'], coordinator.pk)
        self.assertEqual(release['authority_source']['kind'], 'OFFICE')
        self.assertEqual(release['authority_source']['route'], 'COORDINATOR_ROUTE')
        self.assertNotEqual(release['actor_id'], author.pk)
        # Existing G4 refuses live GOVERNANCE pending DISCUSSION. No manufactured
        # ACTIVE row or disabled safeguard is used to invent the interim positive.
        for route in ('GOVERNANCE_INTERIM', 'CQ_GOVERNANCE_CIRCLE'):
            with self.subTest(route=route), self.assertRaises(policy.PolicyRefused) as refused:
                policy.draft_policy(credential=coordinator.credential, organism_uuid=lo.organism_id,
                    resource_reference='held-'+uuid4().hex, content='Held route.', authors=[coordinator.identity_id],
                    request_uuid=uuid4(), route=route)
            self.assertIs(type(refused.exception), policy.PolicyRefused)
            self.assertEqual(refused.exception.code, 'LIB_AUTHORITY_SOURCE_REQUIRED')
            self.assertEqual(str(refused.exception), authority_message)
        self.assertEqual(models.ProfileRole.objects.count(), before_roles)

    def test_root_publishes_own_exact_version(self):
        """LIB-05. Exact self-acceptance/publication plus independently scoped LO permission."""
        lo, coordinator = law_fixtures.ordinary_birth()
        root = root_genesis.execute(root_genesis_support.command())
        founder = root.founder_identity
        args = dict(credential=founder.credential, organism_uuid=root.organism_id)
        version = policy.draft_policy(**args, resource_reference='root-policy-'+uuid4().hex,
            content='[#1] The root records its operating Policy.', authors=[founder.identity_id],
            request_uuid=uuid4(), declare_own_authorship=True)
        with self.assertRaises(root_publication.PublicationRefused) as absent:
            root_publication.publish_own(**args, domain='LIBRARY', version_id=version.pk)
        self.assertEqual(absent.exception.code, 'R_DECLARATION_REQUIRED')
        declaration = root_publication.declare(**args, request_uuid=uuid4(), accept_declaration=True,
            access_posture='PRIVATE_DECLARING_ACTOR')
        published = root_publication.publish_own(**args, domain='LIBRARY', version_id=version.pk)
        self.assertEqual(root_publication.publish_own(**args, domain='LIBRARY', version_id=version.pk), published)
        self.assertEqual(set(published), {'acceptance','publication'})
        self.assertNotEqual(published['acceptance'], published['publication'])
        facts = policy._rows('SELECT * FROM public.core_libpublicationfact WHERE version_id=%s ORDER BY kind', [version.pk])
        self.assertEqual(len(facts), 2)
        for fact in facts:
            self.assertEqual((fact['actor_id'],fact['version_id'],fact['declaration_id']), (founder.pk,version.pk,declaration.pk))
            self.assertTrue(fact['self_accepted'])
            self.assertFalse(fact['independent_review'])
            self.assertEqual(fact['authority_source']['kind'], 'ROOT_PUBLICATION_CAPACITY')
        self.assertIsNone(facts[0]['transition_id'])
        transition = models.LibraryResourceTransition.objects.get(pk=facts[1]['transition_id'])
        self.assertEqual((transition.version_id,transition.actor_id,transition.command),
                         (version.pk, founder.pk, 'publish_library_resource'))
        # Another organisation's Coordinator transcribes the founder's Policy.
        # Its author's identity does not supply that organisation's release power.
        foreign = policy.draft_policy(credential=coordinator.credential, organism_uuid=lo.organism_id,
            resource_reference='foreign-policy-'+uuid4().hex, content='[#1] An independently released Policy.',
            authors=[founder.identity_id], request_uuid=uuid4())
        policy.declare_authorship(credential=founder.credential, version_id=foreign.pk,
            content_sha256=sha256(foreign.content.encode()).hexdigest(), authors=[founder.identity_id], affirm=True)
        with self.assertRaises(root_publication.PublicationRefused) as missing:
            root_publication.publish_own(**args, domain='LIBRARY', version_id=foreign.pk)
        self.assertEqual(missing.exception.code, 'R_LO_PERMISSION_REQUIRED')
        permission = policy.permit_release(credential=coordinator.credential, version_id=foreign.pk)
        result = root_publication.publish_own(**args, domain='LIBRARY', version_id=foreign.pk)
        row = policy._rows('SELECT p.actor_id,p.authority_source,f.authority_source AS publisher_source '
            'FROM public.core_libreleasepermission p JOIN public.core_libpublicationfact f ON f.permission_uuid=p.permission_uuid '
            'WHERE f.fact_uuid=%s', [result['publication']])[0]
        self.assertEqual(row['actor_id'], coordinator.pk)
        self.assertEqual(row['authority_source']['organism'], str(lo.organism_id))
        self.assertEqual(row['publisher_source']['organism'], str(root.organism_id))
        self.assertEqual(policy._rows('SELECT permission_uuid FROM public.core_libreleasepermission WHERE version_id=%s', [foreign.pk])[0]['permission_uuid'], permission)
        self.assertFalse(models.OrganismCommandReceipt.objects.filter(action='ADOPT_LAW').exists(), 'Publication makes no law.')

    def test_publication_evidence_refusal_is_atomic(self):
        """LIB-06. No missing/coauthored/forged evidence or half-publication succeeds."""
        root = root_genesis.execute(root_genesis_support.command())
        founder = root.founder_identity
        other = support.register_test_learner(username='lib-coauthor-'+uuid4().hex)
        args = dict(credential=founder.credential, organism_uuid=root.organism_id)
        root_publication.declare(**args, request_uuid=uuid4(), accept_declaration=True, access_posture='PRIVATE_DECLARING_ACTOR')
        coauthored = policy.draft_policy(**args, resource_reference='coauthored-'+uuid4().hex, content='[#1] Joint work.',
            authors=[founder.identity_id,other.identity_id], request_uuid=uuid4(), declare_own_authorship=True)
        with self.assertRaises(policy.PolicyRefused):
            policy.declare_authorship(credential=other.credential, version_id=coauthored.pk,
                content_sha256='0'*64, authors=[founder.identity_id,other.identity_id], affirm=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            policy._claim(coauthored.pk, other.pk, founder, support.database_now())
        policy.declare_authorship(credential=other.credential, version_id=coauthored.pk,
            content_sha256=sha256(coauthored.content.encode()).hexdigest(), authors=[founder.identity_id,other.identity_id], affirm=True)
        with self.assertRaises(root_publication.PublicationRefused) as joint:
            root_publication.publish_own(**args, domain='LIBRARY', version_id=coauthored.pk)
        self.assertEqual(joint.exception.code, 'R_SOLE_AUTHOR_REQUIRED')
        with self.assertRaises(policy.PolicyRefused):
            policy.draft_policy(**args, resource_reference=coauthored.resource.resource_id, content='[#1] Cannot erase a coauthor.',
                authors=[founder.identity_id], request_uuid=uuid4(), predecessor_version_id=coauthored.pk, declare_own_authorship=True)
        version = policy.draft_policy(**args, resource_reference='atomic-'+uuid4().hex, content='[#1] Sole work.',
            authors=[founder.identity_id], request_uuid=uuid4(), declare_own_authorship=True)
        for source, actor in (
            (policy._source(root, founder, 'ROOT_EXCEPTION', support.database_now()), other),
            ('{"kind":"CQ_DECISION","reference":"not-issued","route":"CQ_GOVERNANCE_CIRCLE"}', founder),
        ):
            with self.assertRaisesRegex(IntegrityError, 'release permission requires'), transaction.atomic():
                policy._rows('INSERT INTO public.core_libreleasepermission(permission_uuid,version_id,actor_id,actor_epoch,authority_source,received_at) '
                    'VALUES(%s,%s,%s,%s,%s::jsonb,statement_timestamp())',
                    [uuid4(), version.pk, actor.pk, actor.access_epoch, source])
        original = policy._insert_fact
        def fail_second(**kwargs):
            if kwargs['kind']=='PUBLICATION':
                raise RuntimeError('LIB-06 interrupted publication')
            return original(**kwargs)
        with patch.object(policy, '_insert_fact', fail_second), self.assertRaisesRegex(RuntimeError,'LIB-06 interrupted'):
            root_publication.publish_own(**args, domain='LIBRARY', version_id=version.pk)
        self.assertEqual(policy._rows('SELECT count(*) AS n FROM public.core_libpublicationfact')[0]['n'], 0)
        self.assertEqual(policy._rows('SELECT count(*) AS n FROM public.core_libreleasepermission')[0]['n'], 0)
        version.resource.refresh_from_db()
        self.assertEqual(version.resource.state, 'draft')
        self.assertFalse(models.LibraryResourceTransition.objects.filter(version=version,command='publish_library_resource').exists())
        # A raw transition through the old generic API cannot bypass a tracked
        # Policy's deferred release guard, even with the old fixture capability.
        library = LibraryService(authority=ContributionAuthority(LibraryFixtureCapability()))
        role, _ = models.Role.objects.get_or_create(name='LIB negative-only capability fixture')
        models.ProfileRole.objects.get_or_create(identity=founder, role=role)
        with self.assertRaisesRegex(IntegrityError,'governed publication fact'), transaction.atomic():
            library.transition_resource(identity=founder.credential,resource_id=version.resource.resource_id,
                command='publish_library_resource',evidence_reference='fixture:lib-forged-publication')
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        result = root_publication.publish_own(**args, domain='LIBRARY', version_id=version.pk)
        self.assertEqual(set(result), {'acceptance','publication'})
        with self.assertRaises(IntegrityError), transaction.atomic():
            policy._rows('UPDATE public.core_libpublicationfact SET authority_source=%s::jsonb WHERE fact_uuid=%s',
                         ['{}', result['publication']])
        with self.assertRaises(policy.PolicyRefused):
            policy.publish_policy(credential=other.credential,organism_uuid=root.organism_id,
                version_id=coauthored.pk,request_uuid=uuid4())


    def test_returning_author_records_own_declaration_beside_carried_attribution(self):
        """LIB-07 / P2g-1. Two distinct facts, one of each kind; later carry is unambiguous."""
        lo, coordinator = law_fixtures.ordinary_birth()
        author = law_fixtures.add_member(lo)
        reference = 'lib-returning-' + uuid4().hex
        authors = [author.identity_id]
        args = dict(credential=coordinator.credential, organism_uuid=lo.organism_id,
            resource_reference=reference, authors=authors)
        first = policy.draft_policy(**args, content='[#1] Original contribution.', request_uuid=uuid4())
        first_claim = policy.declare_authorship(credential=author.credential, version_id=first.pk,
            content_sha256=sha256(first.content.encode()).hexdigest(), authors=authors, affirm=True)
        second = policy.draft_policy(**args, content='[#1] Returning contribution.', request_uuid=uuid4(),
            predecessor_version_id=first.pk)
        carried, = policy._rows('SELECT * FROM public.core_libauthorclaim WHERE version_id=%s', [second.pk])
        self.assertEqual(carried['inherited_from'], first_claim)
        self.assertEqual(carried['actor_id'], coordinator.pk)
        declaration = dict(credential=author.credential, version_id=second.pk,
            content_sha256=sha256(second.content.encode()).hexdigest(), authors=authors, affirm=True)
        own = policy.declare_authorship(**declaration)
        self.assertNotEqual(own, carried['claim_uuid'])
        self.assertEqual(policy.declare_authorship(**declaration), own)
        claims = policy._rows('SELECT * FROM public.core_libauthorclaim WHERE version_id=%s', [second.pk])
        self.assertEqual(len(claims), 2)
        self.assertEqual(next(r for r in claims if r['claim_uuid']==carried['claim_uuid']), carried)
        explicit = next(r for r in claims if r['claim_uuid']==own)
        self.assertEqual((explicit['actor_id'], explicit['author_id'], explicit['inherited_from'], explicit['declaration']),
            (author.pk, author.pk, None, policy.AUTHOR_DECLARATION))
        # Database uniqueness still independently refuses a second fact of either kind.
        for actor, inherited, index in ((author, None, 'lib_author_own_once'),
                                       (coordinator, first_claim, 'lib_author_carried_once')):
            with self.subTest(index=index), self.assertRaisesRegex(IntegrityError, index), transaction.atomic():
                policy._claim(second.pk, author.pk, actor, support.database_now(), inherited)
        policy.publish_policy(credential=coordinator.credential, organism_uuid=lo.organism_id,
            version_id=second.pk, request_uuid=uuid4())
        third = policy.draft_policy(**args, content='[#1] Next revision.', request_uuid=uuid4(),
            predecessor_version_id=second.pk)
        next_carried, = policy._rows('SELECT * FROM public.core_libauthorclaim WHERE version_id=%s', [third.pk])
        self.assertEqual(next_carried['inherited_from'], own)
        self.assertEqual(next_carried['declaration'], policy.CARRIED_ATTRIBUTION)

    def test_version_writers_authenticate_before_provenance_and_hide_unavailable_versions(self):
        """LIB-08 / P2g-2. No provenance before sign-in; equal unavailable-version refusals."""
        lo, coordinator = law_fixtures.ordinary_birth()
        outsider = support.register_test_learner(username='lib-version-outsider-'+uuid4().hex)
        tracked = policy.draft_policy(credential=coordinator.credential, organism_uuid=lo.organism_id,
            resource_reference='lib-private-'+uuid4().hex, content='[#1] Internal Policy.',
            authors=[coordinator.identity_id], request_uuid=uuid4(), declare_own_authorship=True)
        untracked = Document(coordinator).first
        missing = max(tracked.pk, untracked.pk) + 10000
        for writer in ('declare', 'release'):
            for credential in (None, outsider.credential):
                refusals = []
                for version in (tracked.pk, untracked.pk, missing):
                    with self.subTest(writer=writer, signed_in=credential is not None, version=version):
                        # A no-credential attempt must not call the provenance reader at all.
                        with patch.object(policy, '_meta', wraps=policy._meta) as metadata:
                            with self.assertRaises(policy.PolicyRefused) as refused:
                                if writer == 'declare':
                                    policy.declare_authorship(credential=credential, version_id=version,
                                        content_sha256='0'*64, authors=[outsider.identity_id], affirm=True)
                                else:
                                    policy.permit_release(credential=credential, version_id=version)
                            if credential is None:
                                metadata.assert_not_called()
                        refusals.append((type(refused.exception), refused.exception.code, str(refused.exception)))
                self.assertEqual(refusals, [refusals[0]] * 3)
                if credential is not None:
                    self.assertEqual(refusals[0], (policy.PolicyRefused, 'LIB_VERSION_UNAVAILABLE',
                        'This Policy version is unavailable for this act.'))
        self.assertEqual(policy._rows('SELECT count(*) AS n FROM public.core_libreleasepermission')[0]['n'], 0)
        claims = policy._rows('SELECT actor_id FROM public.core_libauthorclaim WHERE version_id=%s', [tracked.pk])
        self.assertEqual(claims, [{'actor_id': coordinator.pk}])

    def test_root_default_request_validates_credential_and_organisation_first(self):
        """LIB-09 / P2g-3. Invalid inputs refuse before deterministic request derivation."""
        lo, coordinator = law_fixtures.ordinary_birth()
        for credential, organism, message in (
            (None, lo.organism_id, 'actor Identity not found'),
            (coordinator.credential, 'not-a-uuid', 'Choose an exact UUID for this act.'),
            (coordinator.credential, None, 'Choose an exact UUID for this act.'),
        ):
            with self.subTest(organism=organism, signed_in=credential is not None):
                with patch.object(policy, 'uuid5', side_effect=AssertionError('Request derived before validation')) as derive:
                    with self.assertRaises(root_publication.PublicationRefused) as refused:
                        root_publication.publish_own(credential=credential, organism_uuid=organism,
                            domain='LIBRARY', version_id=1)
                    derive.assert_not_called()
                self.assertIs(type(refused.exception), root_publication.PublicationRefused)
                self.assertEqual(refused.exception.code, 'R_PUBLICATION_EVIDENCE_REQUIRED')
                self.assertEqual(str(refused.exception), message)
        self.assertEqual(policy._rows('SELECT count(*) AS n FROM public.core_libpublicationfact')[0]['n'], 0)
