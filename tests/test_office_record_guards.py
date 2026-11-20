"""Database witnesses for the Alpha CARE setting and office-observation histories."""
from dataclasses import replace
from importlib import import_module
import os
import re
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from psycopg import sql

from core import models
from src.intevia.services import library_laws
from src.intevia.services import organism_role_definition_service as definitions
from intevia.test_postgresql_backend.operations import S015_TRUNCATE_GUARDIANS
from verification.isolation import state as isolation
from verification.route import Route
from tests import test_law_acts as laws
from tests.test_discussion_office_links import record_office
from tests.office_record_support import (
    approved_office, care_account, footprint, insert_observation_input, observation_footprint, propose_observation,
    read_source_candidate, submit_pair,
)


# SQL sent by the reverse operation can contain several commands and a DO body.
_SQL_PART = re.compile(
    r"""--[^\r\n]*|/\*|\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$|\b[Ee]'(?:\\[\s\S]|''|[^'\\])*'|"""
    r"""'(?:''|[^'])*'|"(?:""|[^"])*" """, re.VERBOSE)
_BLOCK_DELIMITER = re.compile(r"/\*|\*/")
_CHANGE_VERB = re.compile(r"\b(?:CREATE|ALTER|DROP|DELETE|INSERT|UPDATE|TRUNCATE)\b", re.IGNORECASE)


def change_verbs(statement):
    """Scan every command and dollar-quoted body, discarding line and nested block comments."""
    found = []
    start = 0
    match = _SQL_PART.search(statement, start)
    while match:
        found.extend(_CHANGE_VERB.findall(statement[start:match.start()]))
        token = match.group()
        start = match.end()
        if token == "/*":
            depth = 1
            for marker in _BLOCK_DELIMITER.finditer(statement, start):
                depth += 1 if marker.group() == "/*" else -1
                if not depth:
                    start = marker.end()
                    break
            else:
                start = len(statement)
        elif token.startswith("$"):
            end = statement.find(token, start)
            if end < 0:
                end = len(statement)
            found.extend(change_verbs(statement[start:end]))
            start = end + len(token)
        elif not token.startswith("--"):
            # Dynamic SQL may carry a change verb inside a quoted string.
            found.extend(_CHANGE_VERB.findall(token))
        match = _SQL_PART.search(statement, start)
    found.extend(_CHANGE_VERB.findall(statement[start:]))
    return [verb.upper() for verb in found]


def diagnostic(error):
    """Read PostgreSQL's diagnostic uniformly at statement and deferred-commit boundaries."""
    cause = error.__cause__
    detail = getattr(cause, "diag", None)
    return SimpleNamespace(sqlstate=getattr(cause, "sqlstate", None),
                           message_primary=getattr(detail, "message_primary", None),
                           constraint_name=getattr(detail, "constraint_name", None))


def head_receipt(owner, head_id):
    return models.LivingOrganismEvent.objects.filter(
        pk=head_id, living_organism=owner).values_list("event_uuid", "action").first()


def all_care_rows():
    """Capture complete detail and ILC rows, and every organisation head, in stable order."""
    with connection.cursor() as cur:
        rows = {}
        for table, key in (("core_careassistancesettingact", "act_uuid"),
                           ("core_careofficeobservation", "event_uuid")):
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", ["public." + table])
            if not cur.fetchone()[0]:
                rows[table] = None
                continue
            cur.execute(sql.SQL("SELECT {key},to_jsonb(d)::text FROM public.{table} d ORDER BY {key}").format(
                key=sql.Identifier(key), table=sql.Identifier(table)))
            rows[table] = cur.fetchall()
        cur.execute("""SELECT event_uuid,action,to_jsonb(e)::text
                         FROM public.core_livingorganismevent e
                        WHERE action IN ('SET_CARE_ASSISTANCE','RECORD_CARE_OFFICE_OBSERVATION')
                        ORDER BY event_uuid""")
        rows["events"] = cur.fetchall()
        cur.execute("SELECT organism_id,head_event_id FROM public.core_livingorganism ORDER BY organism_id")
        rows["heads"] = cur.fetchall()
        return rows


def care_schema():
    """Observe the real schema even when a broken downgrade has removed either detail."""
    with connection.cursor() as cur:
        relations = ["core_careassistancesettingact", "core_careofficeobservation", "core_livingorganismevent"]
        cur.execute("""SELECT c.relname,a.attname,a.atttypid::regtype::text,a.attnotnull
                         FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                        WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(%s)
                          AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""", [relations])
        columns = cur.fetchall()
        cur.execute("""SELECT c.relname,k.conname,k.convalidated,pg_get_constraintdef(k.oid)
                         FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
                        WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(%s)
                        ORDER BY c.relname,k.conname""", [relations])
        constraints = cur.fetchall()
        cur.execute("""SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid)
                         FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                        WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(%s) AND NOT t.tgisinternal
                        ORDER BY c.relname,t.tgname""", [relations])
        triggers = cur.fetchall()
        cur.execute("""SELECT p.oid::bigint,p.proname,encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex')
                         FROM pg_proc p WHERE p.pronamespace='public'::regnamespace
                          AND (starts_with(p.proname::text,'care_') OR p.proname IN ('s015_append_event','s3fu_guard_gateway_effect'))
                        ORDER BY p.oid""")
        bodies = cur.fetchall()
        cur.execute("SELECT name FROM django_migrations WHERE app='core' ORDER BY name")
        return columns, constraints, triggers, bodies, cur.fetchall()


def requester_schema():
    with connection.cursor() as cur:
        cur.execute("""SELECT attname, attnotnull FROM pg_attribute
                       WHERE attrelid='public.core_careofficeobservation'::regclass
                         AND attname IN ('requester_id','requester_epoch') ORDER BY attname""")
        columns = cur.fetchall()
        cur.execute("""SELECT convalidated, pg_catalog.pg_get_expr(conbin, conrelid) FROM pg_constraint
                       WHERE conrelid='public.core_careofficeobservation'::regclass
                         AND conname='care_observation_requester_erased'""")
        return columns, cur.fetchone()


def committed_observation(*, legacy=False):
    owner, founder, office, _, _, _ = approved_office()
    source = read_source_candidate(owner, founder, office)
    key = uuid4()
    with transaction.atomic():
        propose_observation(owner, founder, source, key, enterer=founder if legacy else None)
    return owner, founder, key


class OfficeRecordGuardTests(TransactionTestCase):
    def test_setting_pair_is_required_on_both_sides(self):
        """B4. Detail alone, event alone and a mismatched event refuse; one matched pair commits.
        The trigger inventory (727 to 737 core triggers) witnesses the event-side
        deferred trigger's presence. B4 witnesses transaction outcomes at the runtime boundary.
        """
        owner, actor = laws.ordinary_birth()
        key = uuid4()
        try:
            with transaction.atomic():
                submit_pair(owner, actor, key, "matched")
        except IntegrityError as exc:
            if diagnostic(exc).message_primary != "CARE_SETTING_PAIR":
                raise
            self.fail("A valid CARE switch and its matching ILC event must commit together.")
        head, details, events = footprint(owner)
        self.assertEqual((details, events), ([key], [key]),
                         "The accepted pair must share the exact request UUID on both sides.")
        self.assertEqual(head_receipt(owner, head), (key, "SET_CARE_ASSISTANCE"),
                         "The organisation head must be the ILC entry for this exact CARE switch.")

        committed = footprint(owner)
        for mode in ("detail_only", "event_only", "wrong_fingerprint", "wrong_authority"):
            with self.subTest(mode=mode):
                with self.assertRaises(IntegrityError, msg="An unpaired or mismatched switch must refuse.") as caught:
                    with transaction.atomic():
                        submit_pair(owner, actor, uuid4(), mode, prior=key, enabled=True)
                self.assertEqual(diagnostic(caught.exception).message_primary, "CARE_SETTING_PAIR",
                                 "The setting-pair guard must refuse this case for the intended reason.")
                self.assertEqual(footprint(owner), committed,
                                 "A refused switch must leave both histories and the organisation head intact.")

    def test_observation_pair_matches_its_source(self):
        """A2. A coherent runtime pair requires the actual authorised source, correct owner and the governed ILC entry.

        Removing the alternative veto must allow the otherwise-valid forged pair to commit and fail the test.
        """
        owner, founder, office, definition, version, binding = approved_office()
        foreign, foreign_founder, foreign_office, _, _, foreign_binding = approved_office()
        foreign_source = read_source_candidate(foreign, foreign_founder, foreign_office)
        other = laws.add_member(owner)
        source = read_source_candidate(owner, founder, office)
        self.assertEqual((str(source["definition_uuid"]), str(source["binding_uuid"]), source["library_version_id"]),
                         (str(office), str(binding), version.pk),
                         "The control must start with the actual office and its local adopted version.")
        # The qualified local source and both LAW projections are checked below.
        scope = {"credential": founder.credential, "organism_uuid": owner.organism_id}
        visible_bindings = {v["binding"] for v in library_laws.our_laws(**scope)["laws"]}
        self.assertIn(binding, visible_bindings, "The local reader must be able to see the real local law.")
        self.assertNotIn(foreign_binding, visible_bindings,
                         "The other organisation's genuinely adopted duties must be unavailable in the local law view.")
        foreign_citation = source | {field:foreign_source[field] for field in (
            "binding_uuid", "library_version_id", "library_version_number", "adoption_uuid",
            "resource_reference", "locator", "clause_text")}
        initial = observation_footprint(owner)
        foreign_initial = observation_footprint(foreign)
        system = care_account()
        for label, claim, mode, event_owner, enterer, expected in (
            ("missing event", source, "detail_only", owner, system, "CARE_OBSERVATION_PAIR"),
            ("missing detail", source, "event_only", owner, system, "CARE_OBSERVATION_PAIR"),
            ("internally consistent invented clause", source | {"clause_text": " A different clause."},
             "matched", owner, system, "CARE_OBSERVATION_SOURCE"),
            ("local tenure with a foreign citation", foreign_citation, "matched", owner, system, "CARE_OBSERVATION_SOURCE"),
            ("wrong organisation's event", source, "matched", foreign, system, "CARE_OBSERVATION_PAIR"),
            ("substitute enterer", source, "matched", owner, other, "CARE_WRITER_PRINCIPAL"),
        ):
            with self.subTest(case=label):
                with self.assertRaises(IntegrityError, msg="The false or incomplete observation must refuse.") as caught:
                    with transaction.atomic():
                        propose_observation(owner, founder, claim, uuid4(), mode, event_owner, enterer)
                self.assertEqual(diagnostic(caught.exception).message_primary, expected,
                                 "The intended source or pair guard must explain this refusal.")
                self.assertEqual((observation_footprint(owner), observation_footprint(foreign)), (initial, foreign_initial),
                                 "Both organisation heads and both sets of observation records must survive refusal.")

        with self.assertRaises(IntegrityError, msg="A proposal with a mismatched requester epoch must refuse.") as wrong_epoch:
            with transaction.atomic():
                propose_observation(owner, founder, source, uuid4(), epoch=founder.access_epoch + 1)
        self.assertEqual(diagnostic(wrong_epoch.exception).message_primary, "CARE_OBSERVATION_SOURCE",
                         "The detail guard must reject a requester epoch that differs from current access.")
        self.assertEqual(observation_footprint(owner), initial,
                         "A refused requester epoch must leave the observation and owner head untouched.")

        key = uuid4()
        try:
            with transaction.atomic():
                propose_observation(owner, founder, source, key)
        except IntegrityError as exc:
            if diagnostic(exc).message_primary not in ("CARE_OBSERVATION_SOURCE", "CARE_OBSERVATION_PAIR", "CARE_GATEWAY_PAIR"):
                raise
            self.fail("The runtime role must be able to commit the genuine source and its matching observation.")
        committed = observation_footprint(owner)
        self.assertEqual(committed[:2], ([key], [key]), "The accepted observation must have one matching ILC UUID.")
        self.assertEqual(head_receipt(owner, committed[2]), (key, "RECORD_CARE_OFFICE_OBSERVATION"),
                         "The organisation head must be the ILC entry for this exact CARE observation.")
        with connection.cursor() as cur:
            cur.execute("""SELECT actor_state, actor_id, actor_access_epoch, actor_capacity,
                           entry_mode, composing_rule_state, composing_rule_reference,
                           composer_state, composer_identity_id, enterer_identity_id
                           FROM public.core_livingorganismevent WHERE event_uuid=%s""", [key])
            self.assertEqual(cur.fetchone(), ("NONE", None, None, "NA_NO_PARTY", "COMPOSED", "REFERENCE",
                             "CARE:ALPHA:TREASURER_VACANCY:1", "REFERENCE", system.pk, system.pk),
                             "The exact rule must compose this entry with CARE as Composer and Enterer and nobody as Actor.")
        # Keep all public evidence fixed and independently vary each private input on a typed copy.
        with connection.cursor() as cur:
            cur.execute('''SELECT d.requester_id, d.requester_epoch, d.payload_fingerprint, public.care_observation_payload(d),
                        public.care_observation_payload(jsonb_populate_record(
                            d,jsonb_build_object('requester_id',%s::bigint))),
                        public.care_observation_payload(jsonb_populate_record(
                            d,jsonb_build_object('requester_epoch',%s::bigint)))
                    FROM public.core_careofficeobservation d WHERE d.event_uuid=%s''', [other.pk, founder.access_epoch + 1, key])
            opener, epoch, stored, computed, different_reader, different_epoch = cur.fetchone()
        self.assertEqual((opener, epoch), (None, None),
                         "A committed observation must retain neither opener nor access epoch.")
        self.assertEqual((computed, different_reader, different_epoch), (stored, stored, stored),
                         "The public digest must stay identical when only the private requester or epoch changes.")
        with self.assertRaises(IntegrityError, msg="A second event UUID cannot claim the same source key.") as duplicate:
            with transaction.atomic():
                propose_observation(owner, founder, source, uuid4())
        self.assertEqual(diagnostic(duplicate.exception).constraint_name, "care_observation_source_key",
                         "Source uniqueness must reject the new event UUID for the already recorded evidence.")
        self.assertEqual(observation_footprint(owner), committed, "The original pair must survive the duplicate attempt.")

        alt = definitions.DefinitionProvision("ALTERNATIVE_ROUTE", "library:legacy#alternate-appointment", "v1")
        next_definition = replace(definition, alternative_route_reference=alt.provision_reference,
                                  provisions=definition.provisions + (alt,))
        next_office = record_office(owner, founder, next_definition, prior=office)
        basis = models.OrganismRoleDefinitionProvision.objects.get(
            definition__role_uuid=next_office, relationship="OFFICE_BASIS")
        proposed = source | {"definition_uuid": str(next_office), "provision_uuid": str(basis.provision_uuid)}
        before = observation_footprint(owner)
        # Build from genuine independent coordinates. Reading the faulty qualifier for
        # this source as a fixture precondition would mask the product break.
        try:
            with transaction.atomic():
                propose_observation(owner, founder, proposed, uuid4())
        except IntegrityError as exc:
            outcome = diagnostic(exc).message_primary
        else:
            outcome = "committed"
        self.assertEqual((outcome, observation_footprint(owner)), ("CARE_OBSERVATION_SOURCE", before),
                         "A real alternative must refuse the coherent pair and preserve the old details, events and head.")

    def test_opener_is_checked_at_insert_and_erased_before_storage(self):
        """AC5. Exercise storage independently of the later writer attribution change."""
        from tests.test_office_record_services import fixture_membership_state

        owner, founder, office, _, _, _ = approved_office()
        source = read_source_candidate(owner, founder, office)
        self.assertEqual(requester_schema(), ([("requester_epoch", False), ("requester_id", False)],
                                             (False, "((requester_id IS NULL) AND (requester_epoch IS NULL))")),
                         "Both requester columns must be nullable and the new-write check must remain unvalidated.")
        before = observation_footprint(owner)
        key = uuid4()
        with transaction.atomic():
            try:
                with transaction.atomic():
                    returned = insert_observation_input(source, key, founder.pk, founder.access_epoch)
            except IntegrityError as exc:
                self.fail("A qualified opener and epoch must produce a detail with both inputs erased. "
                          f"The database refused it at {diagnostic(exc).constraint_name}.")
            self.assertEqual(returned, (None, None), "RETURNING must expose two empty requester fields.")
            with connection.cursor() as cur:
                cur.execute("""
                    SELECT requester_id, requester_epoch, payload_fingerprint = public.care_observation_payload(d),
                           payload_fingerprint = public.care_observation_payload(jsonb_populate_record(
                               d, jsonb_build_object('requester_id', %s::bigint))),
                           payload_fingerprint = public.care_observation_payload(jsonb_populate_record(
                               d, jsonb_build_object('requester_epoch', %s::bigint)))
                    FROM public.core_careofficeobservation d WHERE event_uuid=%s
                """, [founder.pk, founder.access_epoch + 1, key])
                self.assertEqual(cur.fetchone(), (None, None, True, True, True),
                                 "The actual detail must erase both inputs and keep the public evidence digest.")
            # A detail alone is provisional. Roll it back before its pair falls due.
            transaction.set_rollback(True)

        self.assertEqual(observation_footprint(owner), before, "The provisional detail must leave no record or head change.")
        outsider = laws.ordinary_birth()[1]
        probationer = laws.add_member(owner)
        fixture_membership_state(owner, founder, probationer, "PROBATIONARY")
        # Member and office fixture changes legitimately advance the head.
        initial = observation_footprint(owner)
        for label, claim, opener, epoch in (
            ("missing opener", source, None, founder.access_epoch),
            ("missing epoch", source, founder.pk, None),
            ("missing both", source, None, None),
            ("stale epoch", source, founder.pk, founder.access_epoch + 1),
            ("another organisation", source, outsider.pk, outsider.access_epoch),
            ("probationary membership", source, probationer.pk, probationer.access_epoch),
            ("different clause", source | {"clause_text": "Different wording."}, founder.pk, founder.access_epoch),
        ):
            with self.subTest(case=label):
                with self.assertRaises(IntegrityError, msg="An unqualified input must be refused at insert.") as refused:
                    with transaction.atomic():
                        insert_observation_input(claim, uuid4(), opener, epoch)
                detail = diagnostic(refused.exception)
                self.assertEqual((detail.sqlstate, detail.message_primary), ("23514", "CARE_OBSERVATION_SOURCE"),
                                 "The source check must explain the refusal at the write cut.")
                self.assertEqual(observation_footprint(owner), initial,
                                 "Refusal must leave the observation history and current head intact.")
        self.assertEqual(initial[:2], before[:2], "The provisional read must leave no observation or event.")

    def test_0111_refuses_populated_reversal(self):
        """B5. The migration refuses either kind of committed history before changing rows, schema or the ledger.

        Exercise the registered 0111 reverse on default-On observation-only, both and switch-only histories.
        The qualified flush receives both kinds of committed history.
        """
        migration = import_module("core.migrations.0111_care_office_record")
        reverse = migration.Migration.operations[0].reverse_code
        editor = connection.schema_editor(atomic=False)
        probe = ("-- DROP\nLOCK TABLE witness; /* UPDATE /* DELETE */ TRUNCATE */ "
                 "DO $$ BEGIN /* ALTER */ CrEaTe TEMP TABLE witness_copy (n int); END $$;")
        self.assertEqual(change_verbs(probe), ["CREATE"],
                         "The observer must find a later change inside a batch and disregard commented verbs.")

        def seed_values():
            with connection.cursor() as cursor:
                cursor.execute("""SELECT to_jsonb(r) - 'id' - 'recorded_at'
                                    FROM public.core_governedrule r ORDER BY rule_uuid, version""")
                rules = cursor.fetchall()
                cursor.execute("""SELECT reporting_version, ruling_basis, reporting
                                    FROM public.core_cqreportingdefault ORDER BY reporting_version""")
                return rules, cursor.fetchall()

        original_seeds = seed_values()

        def verify_history(setting_ids, observation_ids):
            before = all_care_rows()
            self.assertEqual({key for key, _ in before["core_careassistancesettingact"]}, setting_ids,
                             "The setting detail history must contain exactly the committed fixture receipts.")
            self.assertEqual({key for key, _ in before["core_careofficeobservation"]}, observation_ids,
                             "The observation history must contain exactly the committed fixture receipts.")
            expected_events = {(key, "SET_CARE_ASSISTANCE") for key in setting_ids}
            expected_events.update((key, "RECORD_CARE_OFFICE_OBSERVATION") for key in observation_ids)
            self.assertEqual({(key, action) for key, action, _ in before["events"]}, expected_events,
                             "Each populated-history case needs exactly its own committed ILC pairs.")
            schema_before = care_schema()
            self.assertIn(("0111_care_office_record",), schema_before[-1],
                          "The populated refusal must exercise an applied 0111 migration.")
            attempted_changes = []

            def observe_change(execute, statement, params, many, context):
                # Observe DDL before an exception rolls it back and makes the final shape look intact.
                text = statement.as_string(connection.connection) if isinstance(statement, sql.Composable) else statement
                attempted_changes.extend(change_verbs(text))
                return execute(statement, params, many, context)

            with transaction.atomic():
                # A broken successful descent must be observed and then rolled back for the next case.
                try:
                    with transaction.atomic(), connection.execute_wrapper(observe_change):
                        reverse(None, editor)
                except DatabaseError as exc:
                    refusal = diagnostic(exc)
                    verdict = (refusal.sqlstate, refusal.message_primary)
                else:
                    verdict = ("reversed", "")
                after, schema_after = all_care_rows(), care_schema()
                transaction.set_rollback(True)
            self.assertEqual(verdict, ("23514", "CARE_OFFICE_RECORD_POPULATED"),
                             "A recorded CARE detail or event must stop the public downgrade with its own history refusal.")
            self.assertEqual(attempted_changes, [], "Recorded history must refuse before any function, schema or data change.")
            self.assertEqual((after, schema_after), (before, schema_before),
                             "Populated reversal must preserve full detail and event rows, heads, definitions, guards and ledger.")

        with self.subTest(history="observation only at default On"):
            owner, founder, observation = committed_observation()
            verify_history(set(), {observation})

        with self.subTest(history="both"):
            switch = uuid4()
            with transaction.atomic():
                submit_pair(owner, founder, switch, "matched")
            verify_history({switch}, {observation})

        call_command("flush", verbosity=0, interactive=False, database="default", reset_sequences=False,
                     allow_cascade=False, inhibit_post_migrate=False)
        reset = all_care_rows()
        self.assertEqual((reset["core_careassistancesettingact"], reset["core_careofficeobservation"], reset["events"]),
                         ([], [], []), "The qualified flush must empty both CARE detail tables and their ILC entries.")
        self.assertEqual(seed_values(), original_seeds,
                         "Reset must restore the same governed rule versions, contents and reporting default.")
        reset_state = isolation.capture()
        # Native targeted runs may omit the route environment. Use its own head producer in that case.
        expected_head = os.environ.get(isolation.EXPECTED_HEAD_ENV, Route.core_head())
        with patch.dict(os.environ, {isolation.EXPECTED_HEAD_ENV: expected_head}):
            self.assertEqual(isolation.check_requirements(reset_state, S015_TRUNCATE_GUARDIANS), [],
                             "A qualified reset must restore seeds, guards, clock and sequence discipline at the declared head.")
        with self.subTest(history="switch only"):
            owner, founder = laws.ordinary_birth()
            switch = uuid4()
            with transaction.atomic():
                submit_pair(owner, founder, switch, "matched")
            verify_history({switch}, set())

    test_0111_refuses_populated_reversal.ISOLATION_FULL_CAPTURE = True

    def test_detail_history_resists_mutation(self):
        """A7. Direct updates, deletes and truncation cannot alter either recorded pair or the organisation head."""
        owner, founder, observation = committed_observation()
        switch = uuid4()
        with transaction.atomic():
            submit_pair(owner, founder, switch, "matched")
        before = all_care_rows()
        self.assertEqual({(key, action) for key, action, _ in before["events"]},
                         {(switch, "SET_CARE_ASSISTANCE"), (observation, "RECORD_CARE_OFFICE_OBSERVATION")},
                         "The mutation witness requires real, committed setting and observation events.")
        self.assertEqual(([key for key, _ in before["core_careassistancesettingact"]],
                          [key for key, _ in before["core_careofficeobservation"]]), ([switch], [observation]),
                         "Both detail rows must exist before testing their protection.")
        with connection.cursor() as cur:
            cur.execute("""SELECT c.relname,t.tgname,t.tgenabled,t.tgtype
                             FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                            WHERE c.oid IN ('public.core_careassistancesettingact'::regclass,
                                            'public.core_careofficeobservation'::regclass)
                              AND t.tgfoid='public.s015_refuse_row_mutation()'::regprocedure
                              AND (t.tgtype & 32) <> 0 AND NOT t.tgisinternal""")
            self.assertEqual(set(cur.fetchall()), {
                ("core_careassistancesettingact", "s015_care_setting_no_truncate", "O", 34),
                ("core_careofficeobservation", "s015_care_observation_no_truncate", "O", 34),
            }, "Both tables must independently own an enabled, statement-level BEFORE TRUNCATE guardian.")
        # The observation FK references the setting table. Truncate its whole FK group explicitly
        # so a RESTRICT error cannot masquerade as the setting's BEFORE TRUNCATE guardian.
        for label, query, args in (
            ("observation UPDATE", "UPDATE public.core_careofficeobservation SET sentence=%s WHERE event_uuid=%s",
             ["A changed observation.", observation]),
            ("setting UPDATE", "UPDATE public.core_careassistancesettingact SET enabled=NOT enabled WHERE act_uuid=%s", [switch]),
            ("observation DELETE", "DELETE FROM public.core_careofficeobservation WHERE event_uuid=%s", [observation]),
            ("setting DELETE", "DELETE FROM public.core_careassistancesettingact WHERE act_uuid=%s", [switch]),
            ("observation TRUNCATE", "TRUNCATE public.core_careofficeobservation", []),
            ("setting TRUNCATE", "TRUNCATE public.core_careassistancesettingact, public.core_careofficeobservation", []),
        ):
            with self.subTest(operation=label):
                # Observe a successful mutation before restoring the fixture. An incidental SQL error cannot pass.
                with transaction.atomic():
                    try:
                        with transaction.atomic(), connection.cursor() as cur:
                            cur.execute(query, args)
                    except DatabaseError as exc:
                        refusal = diagnostic(exc)
                        verdict = (refusal.sqlstate, refusal.message_primary)
                    else:
                        verdict = ("mutation succeeded", "")
                    after = all_care_rows()
                    transaction.set_rollback(True)
                self.assertEqual((verdict, after), (("23000", "S015 immutable or append-only row mutation refused"), before),
                                 "The named direct mutation must be refused by the append-only guardian and preserve both pairs.")

    def test_0111_refuses_drift_in_each_shared_definition(self):
        """An empty install or reverse must refuse unrelated edits to either shared routine."""
        care = import_module("core.migrations.0111_care_office_record")
        operation = care.Migration.operations[0]
        with connection.schema_editor(atomic=True) as editor, connection.cursor() as cursor:
            initial = care_schema()
            # Rehearse 0111 against its own writer and gateway. The enclosing
            # rollback restores 0113, including its seed and its definitions.
            import_module("core.migrations.0113_care_system_account").uninstall(None, editor)
            for direction in ("reverse", "forward"):
                if direction == "forward":
                    operation.reverse_code(None, editor)
                apply = operation.code if direction == "forward" else operation.reverse_code
                for signature in (care.GATEWAY, care.WRITER):
                    with self.subTest(direction=direction, signature=signature), transaction.atomic():
                        clean = care.installed_definition(cursor, signature)
                        marker = "AS $function$\n"
                        self.assertEqual(clean.count(marker), 1,
                                         "The drift fixture must identify one PostgreSQL function body.")
                        changed = clean.replace(marker, marker + "-- V.1 drift witness\n", 1)
                        cursor.execute(changed)
                        self.assertEqual(care.installed_definition(cursor, signature), changed,
                                         "The catalogue must contain the altered definition before testing the guard.")
                        drifted = care_schema()
                        with self.assertRaises(RuntimeError, msg="0111 must refuse an altered shared definition.") as refusal:
                            with transaction.atomic():
                                apply(None, editor)
                        self.assertEqual(str(refusal.exception), "0111 found an unexpected function definition for " + signature,
                                         "The migration must identify the particular shared definition that drifted.")
                        self.assertEqual(care_schema(), drifted,
                                         "Atomic refusal must retain the received schema and both received function bodies.")
                        transaction.set_rollback(True)
            # The test transaction also encloses its real empty inverse.
            transaction.set_rollback(True)
        self.assertEqual(care_schema(), initial,
                         "After the rehearsal the received schema, routine definitions, OIDs and ledger must be restored.")

    test_0111_refuses_drift_in_each_shared_definition.ISOLATION_FULL_CAPTURE = True


class CareRequesterMigrationTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def step(self, forward):
        executor = MigrationExecutor(connection)
        edge = executor.loader.get_migration("core", "0113_care_system_account")
        previous = executor.loader.project_state(edge.dependencies)
        method = executor.apply_migration if forward else executor.unapply_migration
        method(previous, edge)

    def check_reverse_refusal(self, expected):
        def seed_and_recorder():
            with connection.cursor() as cur:
                cur.execute("""
                    SELECT i.id, u.id, r.identity_id, r.credential_link, t.tgenabled
                    FROM public.core_identity i
                    JOIN public.auth_user u ON u.id=i.credential_id
                    JOIN public.core_identityresolution r ON r.identity_id=i.id
                    JOIN pg_trigger t ON t.tgrelid='public.core_identityresolution'::regclass
                                     AND t.tgname='s015_0022_resolution_severance'
                    WHERE i.identity_id=%s
                """, ["b79c7d99-67b3-4dd0-8ab7-ee1328d1e183"])
                return cur.fetchall()

        old_rows, old_schema, old_seed = all_care_rows(), care_schema(), seed_and_recorder()
        changes = []

        def observe(execute, statement, params, many, context):
            text = statement.as_string(connection.connection) if isinstance(statement, sql.Composable) else statement
            # Locking reads include FOR UPDATE. It is not a data change.
            changes.extend(change_verbs(re.sub(r"\bFOR (?:NO KEY )?UPDATE\b", "", text, flags=re.I)))
            return execute(statement, params, many, context)

        with transaction.atomic():
            try:
                with transaction.atomic(), connection.execute_wrapper(observe):
                    self.step(False)
            except RuntimeError as exc:
                outcome = str(exc)
            except DatabaseError as exc:
                outcome = diagnostic(exc).message_primary
            else:
                outcome = "reversed"
            self.assertEqual(outcome, expected,
                             "The reverse must stop at the named history preflight before any change.")
            self.assertEqual(changes, [], "The reverse must refuse before its first data or schema change.")
            self.assertEqual((all_care_rows(), care_schema(), seed_and_recorder()), (old_rows, old_schema, old_seed),
                             "The refused reverse must preserve history, heads, schema, ledger, CARE and the recorder.")
            transaction.set_rollback(True)

    def test_legacy_observation_survives_upgrade_and_reverse(self):
        self.step(False)
        # Restore the received edge even if the legacy fixture or an assertion fails.
        self.addCleanup(self.restore_head)
        previous_schema = care_schema()
        owner, founder, key = committed_observation(legacy=True)
        old_rows = all_care_rows()
        with connection.cursor() as cur:
            cur.execute("SELECT requester_id, requester_epoch FROM public.core_careofficeobservation "
                        "WHERE event_uuid=%s", [key])
            self.assertEqual(cur.fetchone(), (founder.pk, founder.access_epoch),
                             "The previous edge must have committed a real observation with both original inputs.")
        self.step(True)
        self.assertEqual(requester_schema(), ([("requester_epoch", False), ("requester_id", False)],
                                             (False, "((requester_id IS NULL) AND (requester_epoch IS NULL))")),
                         "A populated upgrade must keep both columns and add the unvalidated two-NULL check.")
        self.assertEqual(all_care_rows(), old_rows, "The upgrade must preserve the entire legacy history and owner head.")
        # Exercise the installed event-Enterer FK probe now, using the real
        # legacy event as a positive control. Observe, without altering, the
        # actual downgrade query and the bound CARE key on the old-only path.
        care_pk = care_account().pk
        event_queries = []

        def see_enterer(execute, statement, params, many, context):
            if (isinstance(statement, str) and "core_livingorganismevent child" in statement
                    and "child.enterer_identity_id = parent.id" in statement):
                event_queries.append((statement, tuple(params)))
            return execute(statement, params, many, context)

        with connection.execute_wrapper(see_enterer):
            self.step(False)
        self.assertEqual([params for _, params in event_queries], [(care_pk,)],
                         "The reverse must consult the installed event Enterer reference with CARE's local key.")
        with connection.cursor() as cur:
            cur.execute(event_queries[0][0], [founder.pk])
            self.assertEqual(cur.fetchone(), (True,), "The same reference probe must find the real legacy Enterer.")
        self.assertEqual(all_care_rows(), old_rows,
                         "A legacy-only history must reverse with the same records, evidence, identities and head.")
        self.assertEqual(care_schema(), previous_schema, "Reversal must restore the previous catalogue and migration ledger.")
        self.assertEqual(head_receipt(owner, observation_footprint(owner)[2]), (key, "RECORD_CARE_OFFICE_OBSERVATION"),
                         "The old head must still name the original observation after reversal.")

    def restore_head(self):
        edge = ("core", "0113_care_system_account")
        if edge not in MigrationExecutor(connection).loader.applied_migrations:
            self.step(True)

    def test_new_format_detail_refuses_reverse_at_preflight(self):
        owner, founder, office, _, _, _ = approved_office()
        source = read_source_candidate(owner, founder, office)
        with transaction.atomic():
            insert_observation_input(source, uuid4(), founder.pk, founder.access_epoch)
            self.check_reverse_refusal(
                "CARE's migration could not be reversed. An observation uses the new private record format. "
                "Its history has been kept. Keep this migration in place and ask the database operator "
                "to review the observation history."
            )
            # The event is deliberately still pending, so no event FK can mask this refusal.
            transaction.set_rollback(True)

    def test_care_entered_observation_refuses_reverse_at_reference_preflight(self):
        owner, opener, office, _, _, _ = approved_office()
        source = read_source_candidate(owner, opener, office)
        care = care_account()
        key = uuid4()
        with transaction.atomic():
            propose_observation(owner, opener, source, key, enterer=care)
        with connection.cursor() as cur:
            cur.execute("SELECT enterer_identity_id FROM public.core_livingorganismevent WHERE event_uuid=%s", [key])
            self.assertEqual(cur.fetchone(), (care.pk,), "A committed ILC event must actually name CARE as its Enterer.")
        self.check_reverse_refusal(
            "CARE's account could not be removed. Another record refers to it. "
            "The account and its history have been kept. Keep this migration in place. "
            "Ask the database operator to review the references before any further action."
        )
