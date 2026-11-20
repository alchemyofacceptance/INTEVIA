"""C1-183. Exercise the reserved principal at the writer, gateway and membership boundaries."""

from uuid import uuid4

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core.models import OrganismMembership
from src.intevia.services import organism_membership_service as memberships
from src.intevia.services.organism_authority import S015AuthorityError
from tests import s015_0021_support as support
from tests import cq_authority_support as boards
from tests import test_law_acts as laws
from tests.office_record_support import (
    approved_office, care_account, footprint, propose_observation, read_source_candidate, submit_pair,
)


class CareAuthorityTests(TransactionTestCase):
    def test_only_the_observation_accepts_care_as_writer_principal(self):
        owner, founder = laws.ordinary_birth()
        system = care_account()
        key = uuid4()
        with transaction.atomic():
            submit_pair(owner, founder, key, "matched")
        before = footprint(owner)
        self.assertEqual(before[1:], ([key], [key]),
                         "The human Coordinator must be able to record the control setting and its event.")
        with self.assertRaises(IntegrityError, msg="The same writer must refuse CARE on a setting command.") as caught:
            with transaction.atomic():
                submit_pair(owner, founder, uuid4(), "matched", prior=key, enabled=True, writer=system)
        self.assertEqual(caught.exception.__cause__.diag.message_primary, "CARE_WRITER_PRINCIPAL",
                         "The writer must refuse the principal before the setting matcher or gateway runs.")
        self.assertEqual(footprint(owner), before, "The refused command must roll back its detail and preserve the head.")

    def test_writer_checks_the_command_and_anchor_before_dispatch(self):
        owner, human = laws.ordinary_birth()
        system = care_account()
        # Same arguments as far as dispatch is concerned. Leave the predecessor
        # empty so that the lawful principal reaches the pre-existing envelope
        # guard. None of these calls can create an event.
        statement = """SELECT public.s015_append_event(%s,%s,'RECORD_CARE_OFFICE_OBSERVATION','ACTIVE',
            public.intevia_clock_statement(),public.intevia_clock_statement(),public.intevia_clock_statement(),
            %s,%s,NULL,NULL,NULL,NULL,'{}'::jsonb,NULL,%s,NULL,NULL,NULL,'CARE:ALPHA:TREASURER_VACANCY:1',NULL)"""
        for label, token, principal, event, expected in (
            ("lawful dispatch", "core_livingorganism", system.pk, uuid4(), "CARE_OBSERVATION_ENVELOPE"),
            ("human principal", "core_livingorganism", human.pk, uuid4(), "CARE_WRITER_PRINCIPAL"),
            ("missing principal", "core_livingorganism", None, uuid4(), "CARE_WRITER_PRINCIPAL"),
            ("other anchor", "core_circle", system.pk, uuid4(), "CARE_WRITER_PRINCIPAL"),
            ("non-command", "core_livingorganism", system.pk, None, "CARE_WRITER_PRINCIPAL"),
        ):
            with self.subTest(case=label):
                with self.assertRaises(DatabaseError, msg="Each probe must stop at its designated boundary.") as caught:
                    with transaction.atomic(), connection.cursor() as cur:
                        cur.execute(statement, [token, owner.pk, principal, owner.founding_authority_basis_id, event])
                self.assertEqual(caught.exception.__cause__.diag.message_primary, expected,
                                 "Only the exact command with CARE may reach the observation envelope check.")

    def test_gateway_checks_every_role_before_birthing_returns(self):
        owner, founder = laws.ordinary_birth()
        system = care_account()
        with transaction.atomic(), connection.cursor() as cur:
            # An unconstrained, transaction-local copy isolates the installed gateway
            # from the earlier chain triggers. BIRTHING itself has no detail dependency.
            cur.execute("""SELECT count(*) FROM pg_catalog.pg_trigger
                WHERE tgrelid='public.core_livingorganismevent'::regclass AND tgenabled='O'
                  AND tgfoid='public.s3fu_guard_gateway_effect()'::regprocedure AND tgtype=7""")
            self.assertEqual(cur.fetchone(), (1,), "The tested function must guard real organisation event inserts.")
            cur.execute("""CREATE TEMP TABLE care_gateway_probe ON COMMIT DROP AS
                           SELECT * FROM public.core_livingorganismevent WITH NO DATA""")
            cur.execute("""CREATE TRIGGER probe_before BEFORE INSERT ON care_gateway_probe
                         FOR EACH ROW EXECUTE FUNCTION public.s3fu_guard_gateway_effect()""")
            cur.execute("""INSERT INTO pg_temp.care_gateway_probe
                        SELECT * FROM public.core_livingorganismevent WHERE living_organism_id=%s AND action='BIRTHING'""",
                        [owner.pk])
            self.assertEqual(cur.rowcount, 1, "The genuine human birthing event must reach the gateway's early return.")
            for field, refusal in (
                ("actor_id", "CARE_GATEWAY_ACTOR"),
                ("composer_identity_id", "CARE_GATEWAY_ROLE"),
                ("enterer_identity_id", "CARE_GATEWAY_ROLE"),
            ):
                with self.subTest(field=field):
                    with self.assertRaises(IntegrityError, msg="The gateway must refuse CARE in this role on birthing.") as caught:
                        with transaction.atomic():
                            cur.execute("""INSERT INTO pg_temp.care_gateway_probe
                                SELECT (jsonb_populate_record(e,jsonb_build_object(%s::text,%s::bigint))).*
                                FROM public.core_livingorganismevent e WHERE living_organism_id=%s AND action='BIRTHING'""",
                                        [field, system.pk, owner.pk])
                    self.assertEqual(caught.exception.__cause__.diag.message_primary, refusal,
                                     "The reserved-role guard must give this refusal before action dispatch.")

            for composer, enterer, expected in (
                (system.pk, system.pk, "CARE_GATEWAY_TABLE"),
                (None, system.pk, "CARE_GATEWAY_PAIR"),
                (system.pk, founder.pk, "CARE_GATEWAY_PAIR"),
            ):
                with self.subTest(action="observation", composer=composer, enterer=enterer):
                    with self.assertRaises(DatabaseError, msg="Each probe must stop at its own gateway check.") as caught:
                        with transaction.atomic():
                            cur.execute("""INSERT INTO pg_temp.care_gateway_probe
                                (action, actor_id, composer_identity_id, enterer_identity_id)
                                VALUES ('RECORD_CARE_OFFICE_OBSERVATION',NULL,%s,%s)""", [composer, enterer])
                    self.assertEqual(caught.exception.__cause__.diag.message_primary, expected,
                                     "Both CARE pointers and the organisation event table are independently required.")

    def test_pair_matcher_resolves_both_care_references(self):
        owner, founder, office, *_ = approved_office()
        system = care_account()
        source = read_source_candidate(owner, founder, office)
        key = uuid4()
        with transaction.atomic(), connection.cursor() as cur:
            propose_observation(owner, founder, source, key)
            # The matcher checks the inserting transaction. Change only a typed
            # event value presented to it; the committed history is never edited.
            check = """SELECT public.care_observation_matches(jsonb_populate_record(e,
                jsonb_build_object('composer_identity_id',%s::bigint,'enterer_identity_id',%s::bigint)))
                FROM public.core_livingorganismevent e WHERE event_uuid=%s"""
            cur.execute(check, [system.pk, system.pk, key])
            self.assertEqual(cur.fetchone(), (True,), "The matcher must accept the genuine same-transaction pair.")
            for composer, enterer in ((founder.pk, system.pk), (system.pk, founder.pk),
                                      (founder.pk, founder.pk), (None, system.pk), (system.pk, None)):
                with self.subTest(composer=composer, enterer=enterer):
                    cur.execute(check, [composer, enterer, key])
                    self.assertEqual(cur.fetchone(), (False,), "Both recorded roles must independently resolve to CARE.")

    def test_the_membership_anchor_refuses_the_system_subject(self):
        owner, _ = laws.ordinary_birth()
        human = laws.add_member(owner)
        self.assertTrue(OrganismMembership.objects.filter(identity=human, living_organism=owner, state="ACTIVE").exists(),
                        "A person must be able to hold a committed active membership in this organisation.")
        system = care_account()
        key = uuid4()
        with transaction.atomic(), connection.cursor() as cur:
            stamp = support.database_now()
            fp = support.empty_fp(cur, "core_organismmembership", str(key))
            with self.assertRaises(IntegrityError, msg="Membership creation must refuse CARE as its subject.") as caught:
                with transaction.atomic():
                    cur.execute("""INSERT INTO public.core_organismmembership
                        (membership_uuid,state,created_at,current_state_effective_at,state_known_at,
                         state_source_event_set_fingerprint,identity_id,living_organism_id)
                        VALUES (%s,'ACTIVE',%s,%s,%s,%s,%s,%s)""", [key, stamp, stamp, stamp, fp, system.pk, owner.pk])
            self.assertEqual(caught.exception.__cause__.diag.message_primary, "CARE_MEMBERSHIP_SUBJECT",
                             "The membership identity check must explain the refusal at insertion.")
        self.assertFalse(OrganismMembership.objects.filter(identity=system).exists(),
                         "The system account must have no membership after refusal.")

    def test_membership_open_explains_the_system_account_refusal(self):
        owner, founder = laws.ordinary_birth()
        system = care_account()
        stamp = support.database_now()
        service = memberships.OrganismMembershipService(authority=boards.authority(), clock=support.database_now)
        key = uuid4().hex
        command = memberships.OpenMembershipCommand(
            credential=founder.credential, request_reference="membership:" + key, idempotency_key=key,
            occurred_at=stamp, effective_at=stamp, living_organism_id=owner.organism_id,
            member_identity_id=system.identity_id, reason_class="APPLICATION")
        try:
            service.open_membership(command)
        except memberships.S015MembershipRefused as exc:
            refusal = (exc.refusal_code.value, str(exc))
        except S015AuthorityError:
            refusal = ("general authority refusal", "")
        else:
            refusal = ("membership opened", "")
        self.assertEqual(refusal, ("DENIED",
                         "Membership could not be opened. CARE is INTEVIA's system account. "
                         "No membership was created. Choose the person who is joining. "
                         "If you need help, contact the Organisation Coordinator."),
                         "The member must see the failed action, reason, outcome, next step and help.")
