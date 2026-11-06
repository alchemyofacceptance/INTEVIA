"""GI-01..05: governed records/objectives; no manufactured GOVERNANCE or patched imports."""
from dataclasses import replace
from datetime import datetime
from importlib import import_module
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import charter_entry, circle_commands, root_genesis
from src.intevia.services import guiding_intentions as gi
from src.intevia.services import law_acts as law
from src.intevia.services import library_policy as policy
from src.intevia.services import organism_membership_service as memberships
from src.intevia.services import organism_office_consent_service as consent
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.library_laws import LawReadUnavailable, our_laws
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import S015Authority
from src.intevia.services.organism_role_service import OrganismRoleService
from tests import root_genesis_support
from tests import s015_0021_support as support
from tests import test_law_acts as fixtures
from tests.test_law_records import LibraryFixtureCapability


def local_law(lo, actor, *, circle=None):
    """Real Coordinator provenance, publication and adoption, not a synthetic capability."""
    version = policy.draft_policy(credential=actor.credential, organism_uuid=lo.organism_id,
        resource_reference="gi-law-" + uuid4().hex, content="[#1] Serve the recorded purpose.",
        authors=[actor.identity_id], request_uuid=uuid4(), declare_own_authorship=True)
    policy.publish_policy(credential=actor.credential, organism_uuid=lo.organism_id,
                          version_id=version.pk, request_uuid=uuid4())
    proposal = law.LawProposal(organism=lo.organism_id, resource=version.resource.resource_id,
        proposal=uuid4(), operation="ADOPT", intent="ADOPT", version=version.version_number,
        law_class="TERMS_OF_REFERENCE" if circle else "POLICY",
        scope="CIRCLE" if circle else "LIVING_ORGANISM", circle=circle.circle_uuid if circle else None)
    result = law.OrganismLawService().adopt_law(fixtures.command(actor, proposal))
    if result.status != "ADOPT":
        raise AssertionError("GI prerequisite adoption failed: " + result.message)
    return {"binding": proposal.proposal, "version": version.pk}, proposal


def arguments(lo, actor, *, citations=(), circle=None):
    return {"credential": actor.credential, "organism_uuid": lo.organism_id, "gi_uuid": uuid4(),
            "request_uuid": uuid4(), "title": "Members can find their rules", "content": "Keep exact governing versions available.",
            "citations": list(citations), "circle_uuid": circle.circle_uuid if circle else None}


class GuidingIntentionTests(TransactionTestCase):
    def refused(self, code, call):
        with self.assertRaises(gi.GIRefused) as caught:
            call()
        self.assertIs(type(caught.exception), gi.GIRefused)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), gi.MESSAGES[code])

    def test_exact_local_law_required_for_lo_and_circle(self):
        """GI-01. No-law discrimination plus LO/Circle ownership and exact applicable citations."""
        lo, actor = fixtures.ordinary_birth()
        args = arguments(lo, actor)
        before = models.LivingOrganismEvent.objects.count()
        # Recurring-control target: bypassing gi_laws_ok permits this act and
        # must fail assertRaises, not fail during a different positive setup.
        self.refused("GI_LAW_REQUIRED", lambda: gi.create_intention(**args))
        self.assertEqual(gi._rows("SELECT count(*) AS n FROM public.core_guidingintention")[0]["n"], 0)
        self.assertEqual(models.LivingOrganismEvent.objects.count(), before)
        citation, _ = local_law(lo, actor)
        args["citations"] = [citation]
        created = gi.create_intention(**args)
        self.assertEqual(gi.create_intention(**args), created)
        event = models.LivingOrganismEvent.objects.get(event_uuid=created["act"])
        # These attribution columns are SQL-owned, not Django model fields.
        # Keep the institutional-actor/person-enterer assertion via the shared reader.
        attribution, = gi._rows("""SELECT actor_state, enterer_identity_id
            FROM public.core_livingorganismevent WHERE event_uuid=%s""", [created["act"]])
        self.assertEqual((event.action, attribution["actor_state"], attribution["enterer_identity_id"]),
                         ("RECORD_GUIDING_INTENTION", "LIVING_ORGANISM", actor.pk))
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.effective_at, event.recorded_at)
        self.assertLessEqual(event.received_at, event.effective_at)
        self.assertEqual(event.prior_state, event.resulting_state)
        history = gi.intention_history(credential=actor.credential, organism_uuid=lo.organism_id, gi_uuid=args["gi_uuid"])
        self.assertEqual(history["current"]["version_uuid"], created["version"])
        self.assertEqual(history["current"]["authority_source"]["route"], "COORDINATOR_ROUTE")
        circle = circle_commands.form(credential=actor.credential, organism_uuid=lo.organism_id,
            circle_uuid=uuid4(), kind="GENERIC", purpose="GI witness", request_uuid=uuid4())
        terms, _ = local_law(lo, actor, circle=circle)
        owned = arguments(lo, actor, citations=[terms], circle=circle)
        circle_act = gi.create_intention(**owned)
        circle_history = gi.intention_history(credential=actor.credential, organism_uuid=lo.organism_id, gi_uuid=owned["gi_uuid"])
        self.assertEqual(circle_history["current"]["circle"], circle.circle_uuid)
        self.assertEqual(circle_history["current"]["authority_source"]["route"], "CIRCLE_COORDINATOR")
        office = models.ContextualRoleAssignment.objects.get(circle=circle,
            membership__identity=actor, role_definition__code="COORDINATOR", state="ACTIVE")
        self.assertEqual(circle_history["current"]["authority_source"]["reference"], str(office.assignment_uuid))
        self.assertNotEqual(circle_act["version"], created["version"])
        # Circle terms cannot be borrowed as an LO-wide legal basis.
        self.refused("GI_LAW_REQUIRED", lambda: gi.create_intention(**arguments(lo, actor, citations=[terms])))
        # The Circle may instead serve its LO's applicable law.
        gi.create_intention(**arguments(lo, actor, citations=[citation], circle=circle))
        for invalid in ([citation, citation], [{"binding": uuid4(), "version": citation["version"]}],
                        [{"binding": citation["binding"], "version": terms["version"]}]):
            self.refused("GI_LAW_REQUIRED", lambda invalid=invalid: gi.create_intention(**arguments(lo, actor, citations=invalid)))
        foreign, other_actor = fixtures.ordinary_birth()
        foreign_law, _ = local_law(foreign, other_actor)
        self.refused("GI_LAW_REQUIRED", lambda: gi.create_intention(**arguments(lo, actor, citations=[foreign_law])))
        root = root_genesis.execute(root_genesis_support.command())
        founder = root.founder_identity
        role, _ = models.Role.objects.get_or_create(name="GI charter prerequisite fixture")
        models.ProfileRole.objects.get_or_create(identity=founder, role=role)
        charter_entry.enter_constitutional_charters(credential=founder.credential,
            library_authority=ContributionAuthority(LibraryFixtureCapability()), request_id=uuid4())
        inherited = next(row for row in our_laws(credential=actor.credential, organism_uuid=lo.organism_id)["laws"] if row["inherited"])
        inherited_citation = {"binding": inherited["binding"], "version": inherited["version"]}
        self.refused("GI_LAW_REQUIRED", lambda: gi.create_intention(**arguments(lo, actor, citations=[inherited_citation])))
        mixed = gi.create_intention(**arguments(lo, actor, citations=[citation, inherited_citation]))
        mixed_read = next(row for row in gi.our_objectives(credential=actor.credential,
            organism_uuid=lo.organism_id)["objectives"] if row["gi"] == mixed["gi"])
        platform = next(item["law"] for item in mixed_read["current"]["citations"] if item["law"]["inherited"])
        self.assertEqual(platform["issuer"], str(root.organism_id))
        self.assertEqual(datetime.fromisoformat(platform["adoption_received_at"]), inherited["adoption_received_at"])
        self.assertNotIn("organisation_received_at", platform)

    def test_revision_retirement_immutable_history_and_atomic_failure(self):
        """GI-02. Current-predecessor discipline, preserved bytes, stale-law retirement, rollback and direct-write guards."""
        lo, actor = fixtures.ordinary_birth()
        citation, proposal = local_law(lo, actor)
        args = arguments(lo, actor, citations=[citation])
        args["content"] = "  Preserve the exact entered text.\n"
        created = gi.create_intention(**args)
        before_revision = support.database_now()
        revised_args = args | {"request_uuid": uuid4(), "predecessor": created["version"], "content": "A second intention version."}
        revised = gi.revise_intention(**revised_args)
        self.assertEqual(gi.revise_intention(**revised_args), revised)
        history = gi.intention_history(credential=actor.credential, organism_uuid=lo.organism_id, gi_uuid=args["gi_uuid"])
        self.assertEqual([row["version_number"] for row in history["history"]], [1, 2])
        self.assertEqual(history["history"][0]["content"], args["content"])
        self.assertEqual(history["current"]["predecessor"], created["version"])
        past = gi.intention_history(credential=actor.credential, organism_uuid=lo.organism_id, gi_uuid=args["gi_uuid"],
                                    at=before_revision, known_at=before_revision)
        self.assertEqual(past["current"]["version_uuid"], created["version"])
        self.refused("GI_PREDECESSOR", lambda: gi.revise_intention(**(revised_args | {"request_uuid": uuid4()})))
        self.refused("GI_REQUEST_CONFLICT", lambda: gi.revise_intention(**(revised_args | {"content": "Different request content"})))
        for table, column, value in (("core_giversion", "title", "changed"), ("core_giact", "fingerprint", "0" * 64)):
            with self.assertRaises(IntegrityError) as caught, transaction.atomic(), connection.cursor() as cur:
                cur.execute(f"UPDATE public.{table} SET {column}=%s", [value])
            self.assertEqual(caught.exception.__cause__.diag.message_primary, "S015 immutable or append-only row mutation refused")
        # Genuine failure after anchor and act inserts; the trigger is scoped to
        # this transaction and is rolled back with the failed service call.
        count = gi._rows("SELECT count(*) AS n FROM public.core_giact")[0]["n"]
        events = models.LivingOrganismEvent.objects.count()
        with self.assertRaises(IntegrityError) as interrupted:  # noqa: SIM117 -- rollback must precede inspection
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute("""CREATE FUNCTION public.gi_test_interrupt() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN RAISE EXCEPTION 'GI_TEST_INTERRUPT' USING ERRCODE='23514'; END $$;
                    CREATE TRIGGER gi_test_interrupt BEFORE INSERT ON public.core_giversion
                    FOR EACH ROW EXECUTE FUNCTION public.gi_test_interrupt();""")
                gi.create_intention(**arguments(lo, actor, citations=[citation]))
        self.assertEqual(interrupted.exception.__cause__.diag.message_primary, "GI_TEST_INTERRUPT")
        self.assertEqual(gi._rows("SELECT count(*) AS n FROM public.core_giact")[0]["n"], count)
        self.assertEqual(models.LivingOrganismEvent.objects.count(), events)
        # Exercise translation at a service-owned COMMIT, without replacing a
        # product guard. The deliberate deferred failure is removed in finally.
        for code in ("GI_COMPLETE", "GI_CITATIONS_COMPLETE"):
            with connection.cursor() as cur:
                cur.execute(f"""CREATE FUNCTION public.gi_test_deferred_refusal() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN RAISE EXCEPTION '{code}' USING ERRCODE='23514'; END $$;
                    CREATE CONSTRAINT TRIGGER gi_test_deferred_refusal AFTER INSERT ON public.core_giversion
                    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.gi_test_deferred_refusal();""")
            try:
                self.refused(code, lambda: gi.create_intention(**arguments(lo, actor, citations=[citation])))
                self.assertEqual(gi._rows("SELECT count(*) AS n FROM public.core_giact")[0]["n"], count)
                self.assertEqual(models.LivingOrganismEvent.objects.count(), events)
                # If the caller owns COMMIT, the service has already returned.
                # Document and witness that one untranslatable boundary case.
                returned = False
                with self.assertRaises(IntegrityError) as outer_refusal, transaction.atomic():
                    gi.create_intention(**arguments(lo, actor, citations=[citation]))
                    returned = True
                self.assertTrue(returned)
                self.assertEqual(outer_refusal.exception.__cause__.diag.message_primary, code)
                self.assertEqual(gi._rows("SELECT count(*) AS n FROM public.core_giact")[0]["n"], count)
                self.assertEqual(models.LivingOrganismEvent.objects.count(), events)
            finally:
                with connection.cursor() as cur:
                    cur.execute("""DROP TRIGGER gi_test_deferred_refusal ON public.core_giversion;
                        DROP FUNCTION public.gi_test_deferred_refusal();""")
        retired_law = replace(proposal, proposal=uuid4(), predecessor=proposal.proposal, operation="RETIRE", intent="RETIRE")
        result = law.OrganismLawService().adopt_law(fixtures.command(actor, retired_law))
        self.assertEqual(result.status, "RETIRE")
        self.refused("GI_LAW_REQUIRED", lambda: gi.revise_intention(**(revised_args | {
            "request_uuid": uuid4(), "predecessor": revised["version"]})))
        retire_args = {key: args[key] for key in ("credential", "organism_uuid", "gi_uuid", "circle_uuid")}
        retire_args.update(request_uuid=uuid4(), predecessor=revised["version"])
        retired = gi.retire_intention(**retire_args)
        self.assertEqual(gi.retire_intention(**retire_args), retired)
        final = gi.intention_history(credential=actor.credential, organism_uuid=lo.organism_id, gi_uuid=args["gi_uuid"])
        self.assertIsNone(final["current"])
        self.assertEqual([row["operation"] for row in final["history"]], ["CREATE", "REVISE", "RETIRE"])
        self.assertEqual(final["history"][0], history["history"][0])
        self.refused("GI_RETIRED", lambda: gi.retire_intention(**(retire_args | {"request_uuid": uuid4()})))
        migration = import_module("core.migrations.0071_guiding_intentions")
        with connection.schema_editor(atomic=True) as editor:  # noqa: SIM117 -- inspect refusal within the editor transaction
            with self.assertRaises(RuntimeError) as reversal:
                migration.reverse(None, editor)
        self.assertEqual(str(reversal.exception), "GI reversal refused while intention history exists.")
        self.assertEqual(gi.intention_history(credential=actor.credential, organism_uuid=lo.organism_id,
                                            gi_uuid=args["gi_uuid"])["history"], final["history"])

    test_revision_retirement_immutable_history_and_atomic_failure.ISOLATION_FULL_CAPTURE = True

    def test_office_scope_exact_refusals_and_forged_sources(self):
        """GI-03. Real office authority; no author privilege, absent-target oracle, CQ or dormant GOVERNANCE bypass."""
        lo, actor = fixtures.ordinary_birth()
        member = fixtures.add_member(lo)
        citation, _ = local_law(lo, actor)
        args = arguments(lo, actor, citations=[citation])
        result = gi.create_intention(**args)
        for owner in (lo.organism_id, uuid4()):
            for operation in ("CREATE", "REVISE", "RETIRE"):
                call = args | {"credential": member.credential, "organism_uuid": owner, "request_uuid": uuid4()}
                if operation == "CREATE":
                    call["gi_uuid"] = uuid4()
                    fn = gi.create_intention
                elif operation == "REVISE":
                    call["predecessor"] = result["version"]
                    fn = gi.revise_intention
                else:
                    call = {key: call[key] for key in ("credential", "organism_uuid", "gi_uuid", "request_uuid", "circle_uuid")}
                    call["predecessor"] = result["version"]
                    fn = gi.retire_intention
                self.refused("GI_AUTHORITY", lambda call=call, fn=fn: fn(**call))
        for route in ("GOVERNANCE_INTERIM", "CQ_GOVERNANCE_CIRCLE", "CQ_CIRCLE", "ROOT_EXCEPTION"):
            self.refused("GI_AUTHORITY", lambda route=route: gi.create_intention(**(args | {
                "route": route, "gi_uuid": uuid4(), "request_uuid": uuid4()})))
        # A real LO Coordinator has no implicit authority over an absent Circle.
        self.refused("GI_AUTHORITY", lambda: gi.create_intention(**(args | {
            "circle_uuid": uuid4(), "gi_uuid": uuid4(), "request_uuid": uuid4()})))
        # Database source checks do not accept a caller-provided CQ/office label.
        with self.assertRaises(IntegrityError) as forged, transaction.atomic(), connection.cursor() as cur:
            key = uuid4()
            cur.execute("""INSERT INTO public.core_giact(act_uuid,gi_uuid,operation,version_uuid,actor_id,actor_epoch,
                authority_source,payload,fingerprint,received_at)
                SELECT %s,gi_uuid,'REVISE',%s,actor_id,actor_epoch,'{"kind":"CQ","route":"CQ_CIRCLE"}'::jsonb,
                p,public.gi_payload_fingerprint(p),statement_timestamp()
                FROM public.core_giact a CROSS JOIN LATERAL (SELECT a.payload||jsonb_build_object(
                  'request',%s::text,'operation','REVISE','predecessor',a.version_uuid,'route','CQ_CIRCLE') AS p) q
                WHERE a.act_uuid=%s""", [key, uuid4(), str(key), result["act"]])
        self.assertEqual(forged.exception.__cause__.diag.message_primary, "GI_AUTHORITY")
        # The same-state ILC branch is not an unqualified escape hatch: even a
        # structurally valid shared-writer call needs its exact GI act evidence.
        with self.assertRaises(IntegrityError) as bare_event, transaction.atomic(), connection.cursor() as cur:
            event_key = uuid4()
            cur.execute("""SELECT public.s015_append_event('core_livingorganism',l.id,'RECORD_GUIDING_INTENTION',
                public.s015_fold_effective_state('core_livingorganism',l.id,statement_timestamp(),statement_timestamp()),
                statement_timestamp(),statement_timestamp(),statement_timestamp(),%s,l.founding_authority_basis_id,
                %s,%s,%s,%s,'{}'::jsonb,NULL,%s,h.id,h.sequence::integer,NULL::bigint,NULL::text,%s)
                FROM public.core_livingorganism l JOIN public.core_livingorganismevent h ON h.id=l.head_event_id
                WHERE l.id=%s""", [actor.pk, "s015d1:" + "0" * 64, "gi-act:" + str(event_key),
                    "gi:" + str(event_key), "0" * 64, event_key, "gi:" + str(event_key), lo.pk])
        self.assertEqual(bare_event.exception.__cause__.diag.message_primary, "GI_EVENT")
        # A member can inspect Circle/LO history; that disclosure grants no writer authority.
        seen = gi.intention_history(credential=member.credential, organism_uuid=lo.organism_id, gi_uuid=args["gi_uuid"])
        self.assertEqual(seen["current"]["version_uuid"], result["version"])
        # Separate the two offices: appoint a member to this Circle, then the
        # founder leaves only the Circle while keeping the LO Coordinator office.
        circle = circle_commands.form(credential=actor.credential, organism_uuid=lo.organism_id,
            circle_uuid=uuid4(), kind="GENERIC", purpose="Independent Circle office", request_uuid=uuid4())
        member_row = models.OrganismMembership.objects.get(identity=member, living_organism=lo)
        request = circle_commands.request_place(credential=member.credential, organism_uuid=lo.organism_id,
            circle_uuid=circle.circle_uuid, membership_uuid=member_row.membership_uuid,
            request_uuid=uuid4(), consent="GI Circle office witness")
        place = circle_commands.admit(credential=actor.credential, organism_uuid=lo.organism_id,
            circle_uuid=circle.circle_uuid, request_uuid=request.request_uuid)
        authority = S015Authority(provider=ProductionApplicationAuthorityProvider())
        roles = OrganismRoleService(authority=authority, clock=support.database_now)
        office_uuid, nomination_uuid = uuid4(), uuid4()
        office_args = {"credential": actor.credential, "organism_uuid": lo.organism_id, "circle_uuid": circle.circle_uuid}
        roles.nominate_circle_coordinator(**office_args, participation_uuid=place.participation_uuid,
            assignment_uuid=office_uuid, request_uuid=nomination_uuid)
        at = support.database_now()
        consent.OfficeAppointmentConsentService(authority=authority, clock=support.database_now).record_office_consent(
            consent.RecordOfficeConsentCommand(credential=member.credential, request_reference="gi-office-consent:"+uuid4().hex,
                idempotency_key=uuid4().hex, occurred_at=at, effective_at=at, assignment_uuid=office_uuid,
                proposal_event_uuid=nomination_uuid, action="CONSENT"))
        roles.appoint_circle_coordinator(**office_args, assignment_uuid=office_uuid, request_uuid=uuid4())
        founder_place = models.CircleParticipation.objects.get(circle=circle, membership__identity=actor)
        circle_commands.leave(**office_args, participation_uuid=founder_place.participation_uuid,
                              request_uuid=uuid4(), confirmed=True)
        cut = support.database_now()
        holders = gi._rows("SELECT person FROM public.circle_holders(%s,%s,%s)", [circle.pk, cut, cut])
        self.assertEqual(holders, [{"person": member.pk}])
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(
            membership__identity=member, membership__living_organism=lo, role_definition__scope="LIVING_ORGANISM").exists())
        lo_holders = gi._rows("""SELECT m.identity_id FROM public.c2_seat_facts(%s,%s,%s) f
            JOIN public.core_organismmembership m ON m.id=f.membership_id
            WHERE f.role_scope='LIVING_ORGANISM' AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'""", [lo.pk, cut, cut])
        self.assertIn({"identity_id": actor.pk}, lo_holders)
        independent_args = arguments(lo, member, citations=[citation], circle=circle)
        independent = gi.create_intention(**independent_args)
        self.refused("GI_AUTHORITY", lambda: gi.create_intention(**(independent_args | {
            "credential": actor.credential, "request_uuid": uuid4(), "gi_uuid": uuid4()})))
        independent_revision = independent_args | {"request_uuid": uuid4(), "predecessor": independent["version"],
                                                   "content": "The Circle's own revised intention."}
        self.refused("GI_AUTHORITY", lambda: gi.revise_intention(**(independent_revision | {"credential": actor.credential})))
        revised = gi.revise_intention(**independent_revision)
        independent_retirement = {key: independent_args[key] for key in ("credential", "organism_uuid", "gi_uuid", "circle_uuid")}
        independent_retirement.update(request_uuid=uuid4(), predecessor=revised["version"])
        self.refused("GI_AUTHORITY", lambda: gi.retire_intention(**(independent_retirement | {"credential": actor.credential})))
        gi.retire_intention(**independent_retirement)
        independent_history = gi.intention_history(credential=member.credential, organism_uuid=lo.organism_id,
                                                  gi_uuid=independent_args["gi_uuid"])
        self.assertEqual([row["operation"] for row in independent_history["history"]], ["CREATE", "REVISE", "RETIRE"])
        self.assertEqual({row["authority_source"]["reference"] for row in independent_history["history"]}, {str(office_uuid)})
        self.assertIsNone(independent_history["current"])

        # A request identity belongs to its LO/Circle owner, not globally to a caller.
        # Both directions within one LO and a foreign LO must hide its existence.
        before_conflicts = gi._rows("SELECT count(*) AS n FROM public.core_giact")[0]["n"]
        self.refused("GI_AUTHORITY", lambda: gi.create_intention(**(independent_args | {
            "gi_uuid": uuid4(), "request_uuid": args["request_uuid"]})))
        self.refused("GI_AUTHORITY", lambda: gi.create_intention(**(args | {
            "gi_uuid": uuid4(), "request_uuid": independent_args["request_uuid"]})))
        self.refused("GI_REQUEST_CONFLICT", lambda: gi.create_intention(**(args | {"gi_uuid": uuid4()})))
        self.refused("GI_REQUEST_CONFLICT", lambda: gi.create_intention(**(independent_args | {"gi_uuid": uuid4()})))
        self.assertEqual(gi._rows("SELECT count(*) AS n FROM public.core_giact")[0]["n"], before_conflicts)
        foreign, foreign_actor = fixtures.ordinary_birth()
        foreign_law, _ = local_law(foreign, foreign_actor)
        self.refused("GI_AUTHORITY", lambda: gi.create_intention(**(arguments(foreign, foreign_actor, citations=[foreign_law]) | {
            "request_uuid": args["request_uuid"]})))
        outsider = support.register_test_learner(username="gi-outsider-" + uuid4().hex)
        for owner in (lo.organism_id, uuid4()):
            with self.assertRaises(LawReadUnavailable) as denied:
                gi.intention_history(credential=outsider.credential, organism_uuid=owner, gi_uuid=args["gi_uuid"])
            self.assertEqual(str(denied.exception), "Reading these laws requires current membership in this organisation.")

    def test_objectives_two_coordinates_exact_laws_and_review_flags(self):
        """GI-04. Exact versions survive law changes; effect/knowledge cuts govern GI and law history separately."""
        lo, actor = fixtures.ordinary_birth()
        citation, proposal = local_law(lo, actor)
        other_citation, _ = local_law(lo, actor)
        circle = circle_commands.form(credential=actor.credential, organism_uuid=lo.organism_id,
            circle_uuid=uuid4(), kind="GENERIC", purpose="Objectives witness", request_uuid=uuid4())
        terms, terms_proposal = local_law(lo, actor, circle=circle)
        before_create = support.database_now()
        args = arguments(lo, actor, citations=[citation, other_citation])
        created = gi.create_intention(**args)
        circle_args = arguments(lo, actor, citations=[terms], circle=circle)
        circle_created = gi.create_intention(**circle_args)
        before_law_change = support.database_now()

        def read(**cuts):
            result = gi.our_objectives(credential=actor.credential, organism_uuid=lo.organism_id, **cuts)
            return {row["gi"]: row for row in result["objectives"]}

        def cited(row, binding):
            return next(item for item in row["citations"] if item["binding"] == str(binding))

        self.assertEqual(read(at=before_create, known_at=before_create), {})
        initial = read()
        self.assertEqual(set(initial), {args["gi_uuid"], circle_args["gi_uuid"]})
        self.assertEqual(initial[args["gi_uuid"]]["owner"],
                         {"kind": "LIVING_ORGANISM", "organism": lo.organism_id, "circle": None})
        self.assertEqual(initial[circle_args["gi_uuid"]]["owner"],
                         {"kind": "CIRCLE", "organism": lo.organism_id, "circle": circle.circle_uuid})
        laws = {str(row["binding"]): row for row in our_laws(
            credential=actor.credential, organism_uuid=lo.organism_id)["laws"]}
        for objective in initial.values():
            current = objective["current"]
            event = models.LivingOrganismEvent.objects.get(event_uuid=current["act_uuid"])
            self.assertFalse(objective["needs_review"])
            self.assertNotIn("history", objective)
            self.assertEqual((current["enterer"], current["lineage_reference"], current["authority_decision_reference"]),
                             (actor.pk, event.lineage_reference, event.authority_decision_reference))
            self.assertEqual((current["received_at"], current["effective_at"], current["recorded_at"]),
                             (event.received_at, event.effective_at, event.recorded_at))
            for item in current["citations"]:
                expected = laws[item["binding"]]
                self.assertTrue(item["in_force"])
                self.assertIsNone(item["ending"])
                for key in ("version", "version_number", "class_name", "content", "library_reference",
                            "consequence_class", "inherited", "scope_kind", "authority_source"):
                    self.assertEqual(item["law"][key], expected[key])
                for key in ("in_force_from", "adoption_received_at", "adoption_recorded_at"):
                    self.assertEqual(datetime.fromisoformat(item["law"][key]), expected[key])
        self.assertEqual(initial[circle_args["gi_uuid"]]["current"]["authority_source"]["route"], "CIRCLE_COORDINATOR")
        self.assertEqual(cited(initial[circle_args["gi_uuid"]]["current"], terms["binding"])["law"]["class_name"],
                         "TERMS_OF_REFERENCE")

        successor = policy.draft_policy(credential=actor.credential, organism_uuid=lo.organism_id,
            resource_reference=proposal.resource, content="[#1] A deliberately changed legal purpose.",
            authors=[actor.identity_id], request_uuid=uuid4(), predecessor_version_id=citation["version"],
            declare_own_authorship=True)
        # A successor draft cannot hide or replace the exact cited law text.
        self.assertEqual(read()[args["gi_uuid"]]["current"]["citations"], initial[args["gi_uuid"]]["current"]["citations"])
        policy.publish_policy(credential=actor.credential, organism_uuid=lo.organism_id,
                              version_id=successor.pk, request_uuid=uuid4())
        replacement = replace(proposal, proposal=uuid4(), predecessor=proposal.proposal, version=successor.version_number)
        outcome = law.OrganismLawService().adopt_law(fixtures.command(actor, replacement))
        self.assertEqual(outcome.status, "ADOPT")
        ending_event = models.LivingOrganismEvent.objects.get(event_uuid=outcome.event)
        after_law_change = support.database_now()
        now = read(at=after_law_change, known_at=after_law_change)[args["gi_uuid"]]
        self.assertTrue(now["needs_review"])
        self.assertEqual(now["current"]["version_uuid"], created["version"])
        stale = cited(now["current"], citation["binding"])
        self.assertFalse(stale["in_force"])
        self.assertTrue(cited(now["current"], other_citation["binding"])["in_force"])
        self.assertEqual((stale["version"], stale["law"]["content"]),
                         (citation["version"], laws[str(citation["binding"])]["content"]))
        self.assertEqual(stale["ending"]["successor"], str(replacement.proposal))
        self.assertEqual(stale["ending"]["ending_act"], str(outcome.event))
        self.assertEqual(datetime.fromisoformat(stale["law"]["effective_until"]), ending_event.effective_at)
        for cuts in ({"at": before_law_change, "known_at": after_law_change},
                     {"at": after_law_change, "known_at": before_law_change}):
            historical = read(**cuts)[args["gi_uuid"]]
            self.assertFalse(historical["needs_review"])
            self.assertTrue(cited(historical["current"], citation["binding"])["in_force"])
        unknown_ending = cited(read(at=after_law_change, known_at=before_law_change)[args["gi_uuid"]]["current"], citation["binding"])
        self.assertIsNone(unknown_ending["ending"])
        self.assertIsNone(unknown_ending["law"]["effective_until"])
        revised = gi.revise_intention(**(args | {"request_uuid": uuid4(), "predecessor": created["version"],
            "content": "Explicitly serve the revised law.", "citations": [{"binding": replacement.proposal, "version": successor.pk}]}))
        history = read(include_history=True)[args["gi_uuid"]]
        self.assertFalse(history["needs_review"])
        self.assertEqual(history["current"]["version_uuid"], revised["version"])
        self.assertEqual([row["needs_review"] for row in history["history"]], [True, False])
        self.assertEqual(history["history"][0]["authority_source"], initial[args["gi_uuid"]]["current"]["authority_source"])
        # A later-known GI revision must not escape either temporal cut.
        for cuts in ({"at": after_law_change}, {"known_at": after_law_change}):
            earlier = read(include_history=True, **cuts)[args["gi_uuid"]]
            self.assertEqual([row["version_uuid"] for row in earlier["history"]], [created["version"]])

        retired_terms = replace(terms_proposal, proposal=uuid4(), predecessor=terms_proposal.proposal,
                                operation="RETIRE", intent="RETIRE")
        self.assertEqual(law.OrganismLawService().adopt_law(fixtures.command(actor, retired_terms)).status, "RETIRE")
        circle_now = read()[circle_args["gi_uuid"]]
        self.assertTrue(circle_now["needs_review"])
        self.assertIsNone(circle_now["current"]["citations"][0]["ending"]["successor"])
        before_retirement = support.database_now()
        gi.retire_intention(credential=actor.credential, organism_uuid=lo.organism_id,
            gi_uuid=circle_args["gi_uuid"], circle_uuid=circle.circle_uuid, predecessor=circle_created["version"], request_uuid=uuid4())
        self.assertNotIn(circle_args["gi_uuid"], read())
        retired = read(include_history=True)[circle_args["gi_uuid"]]
        self.assertTrue(retired["retired"])
        self.assertIsNone(retired["current"])
        self.assertEqual([row["operation"] for row in retired["history"]], ["CREATE", "RETIRE"])
        for cuts in ({"at": before_retirement}, {"known_at": before_retirement}):
            self.assertFalse(read(**cuts)[circle_args["gi_uuid"]]["retired"])

    def test_objectives_current_members_all_circles_and_exact_refusals(self):
        """GI-05. LO membership sees Circle GIs; outsider/unknown/departed refusals match Our laws even at past cuts."""
        lo, actor = fixtures.ordinary_birth()
        member = fixtures.add_member(lo)
        read_args = {"credential": member.credential, "organism_uuid": lo.organism_id}
        self.assertEqual(gi.our_objectives(**read_args)["objectives"], [])
        circle = circle_commands.form(credential=actor.credential, organism_uuid=lo.organism_id,
            circle_uuid=uuid4(), kind="GENERIC", purpose="Whole LO disclosure", request_uuid=uuid4())
        terms, _ = local_law(lo, actor, circle=circle)
        args = arguments(lo, actor, citations=[terms], circle=circle)
        created = gi.create_intention(**args)
        # This member has no Circle participation, office or authorship.
        membership = models.OrganismMembership.objects.get(identity=member, living_organism=lo)
        self.assertFalse(models.CircleParticipation.objects.filter(membership=membership, circle=circle).exists())
        visible, = gi.our_objectives(**read_args)["objectives"]
        self.assertEqual(visible["current"]["version_uuid"], created["version"])
        self.assertEqual(visible["owner"]["circle"], circle.circle_uuid)
        for malformed in ("not-a-uuid", str(args["gi_uuid"]), None):
            with self.assertRaises(ValueError) as bad_history:
                gi.intention_history(**read_args, gi_uuid=malformed)
            self.assertIs(type(bad_history.exception), ValueError)
            self.assertEqual(str(bad_history.exception), "Choose an exact UUID for this GI history.")
        foreign, foreign_actor = fixtures.ordinary_birth()
        foreign_citation, _ = local_law(foreign, foreign_actor)
        gi.create_intention(**arguments(foreign, foreign_actor, citations=[foreign_citation]))
        self.assertEqual([row["gi"] for row in gi.our_objectives(**read_args)["objectives"]], [args["gi_uuid"]])
        past = support.database_now()
        outsider = support.register_test_learner(username="gi-read-outsider-" + uuid4().hex)

        def denied(credential, owner):
            cuts = {"credential": credential, "organism_uuid": owner, "at": past, "known_at": past}
            with self.assertRaises(LawReadUnavailable) as laws_denied:
                our_laws(**cuts)
            self.assertEqual(str(laws_denied.exception), "Reading these laws requires current membership in this organisation.")
            for include_history in (False, True):
                with self.assertRaises(LawReadUnavailable) as objectives_denied:
                    gi.our_objectives(**cuts, include_history=include_history)
                self.assertIs(type(objectives_denied.exception), type(laws_denied.exception))
                self.assertEqual(str(objectives_denied.exception), str(laws_denied.exception))
            with self.assertRaises(LawReadUnavailable) as history_denied:
                gi.intention_history(**cuts, gi_uuid="not-a-uuid")
            self.assertIs(type(history_denied.exception), type(laws_denied.exception))
            self.assertEqual(str(history_denied.exception), str(laws_denied.exception))

        denied(outsider.credential, lo.organism_id)
        denied(member.credential, uuid4())
        at = support.database_now()
        memberships.OrganismMembershipService(authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=support.database_now).leave_organism(memberships.LeaveOrganismCommand(
                credential=member.credential, membership_uuid=membership.membership_uuid, confirmed=True,
                reason_class="VOLUNTARY", occurred_at=at, effective_at=at,
                idempotency_key=uuid4().hex, request_reference="gi-reader-leaves:" + uuid4().hex))
        denied(member.credential, lo.organism_id)
