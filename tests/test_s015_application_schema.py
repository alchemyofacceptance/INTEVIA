"""S3 round-1 PostgreSQL schema and historical canonical-byte contracts."""

import datetime as dt
import json
import uuid
from unittest import skipUnless

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from tests.s015_0021_support import T0, add_remaining_aggregates, append_event, append_second_event, empty_fp, found_nonroot, fresh, refresh, seed_identity, u
from tests.test_s015_0024_commitment_preimages import canonical_value, digest, l1_preimage, parts_preimage


POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "S3 guardians require PostgreSQL")
EVENT_TABLE = "core_organismmembershiptransition"
ANCHOR = "core_organismmembership"


def snapshot(cursor, event_id, membership_id):
    cursor.execute(
        "SELECT public.s015_canonical_event_record('public.core_organismmembershiptransition'::regclass, to_jsonb(t)), "
        "t.l1_commitment, t.parts_commitment, t.actor_id, t.actor_access_epoch, t.actor_state "
        "FROM public.core_organismmembershiptransition t WHERE t.id = %s", (event_id,)
    )
    record = cursor.fetchone()
    cursor.execute(
        "SELECT public.s015_event_set_preimage(%s, %s, %s, clock_timestamp()), "
        "public.s015_fingerprint_event_set(%s, %s, %s, clock_timestamp())",
        (ANCHOR, membership_id, T0 + dt.timedelta(days=1), ANCHOR, membership_id, T0 + dt.timedelta(days=1)),
    )
    return record, cursor.fetchone()


@POSTGRESQL_ONLY
class HistoricalActorBytes(TransactionTestCase):
    def setUp(self):
        MigrationExecutor(connection).migrate([("core", "0024_s015_compact_commitment_preimages")])
        self.at_0024 = True

    def tearDown(self):
        # Restore the current head AND verify it, so later tests in this process run on it (0026 since S3 round 2b).
        MigrationExecutor(connection).migrate([("core", "0026_s015_application_admission_time")])
        self.assertIn(("core", "0026_s015_application_admission_time"),
                      MigrationRecorder(connection).applied_migrations())
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef('public.s3_terminal_lock()'::regprocedure)")
            self.assertIn("NEW.recorded_at := clock_timestamp();", cursor.fetchone()[0])

    def compare_before_after(self, actorless):
        with transaction.atomic(), connection.cursor() as cursor:
            ids = found_nonroot(cursor, tag=f"s3-compat-{fresh()}")
            event_id = ids["events"][EVENT_TABLE]
            if actorless:
                event_id, _ = append_event(
                    cursor, EVENT_TABLE, ids["membership"], 2, "SUSPEND", "ACTIVE", "SUSPENDED",
                    None, ids["basis"], fresh(), pred_id=event_id,
                    eff=T0 + dt.timedelta(hours=1), occ=T0 + dt.timedelta(hours=1),
                    rec=T0 + dt.timedelta(hours=1), extra={"reason_class": "TEST"},
                    overrides={"actor_access_epoch": None, "actor_state": "NOT_ESTABLISHED", "actor_capacity": "NA_NO_PARTY"},
                )
            before = snapshot(cursor, event_id, ids["membership"])
        MigrationExecutor(connection).migrate([("core", "0025_s015_application_routes")])
        self.at_0024 = False
        with connection.cursor() as cursor:
            after = snapshot(cursor, event_id, ids["membership"])
        self.assertEqual(before, after)
        self.assertEqual(before[0][-1], "NOT_ESTABLISHED" if actorless else "PARTY")
        if actorless:
            self.assertIsNone(before[0][3])
            self.assertIsNone(before[0][4])
        else:
            self.assertEqual(before[0][3], ids["identity"])
            self.assertEqual(before[0][4], 0)

    def test_c1_historical_party_record_set_commitments_and_actor(self):
        self.compare_before_after(actorless=False)

    def test_c2_historical_actorless_record_set_commitments_and_actor(self):
        self.compare_before_after(actorless=True)


@POSTGRESQL_ONLY
class ApplicationSchemaGuards(TransactionTestCase):
    def setUp(self):
        self.tag = f"s3-{fresh()}"
        with transaction.atomic(), connection.cursor() as cursor:
            self.ids = found_nonroot(cursor, tag=self.tag)
            self.applicant_id = seed_identity(cursor, f"app-{self.tag}")
            membership_uuid = u(f"membership:app:{self.tag}")
            cursor.execute(
                "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, "
                "state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, 'PROPOSED', now(), %s, %s, %s, %s, %s) RETURNING id",
                (membership_uuid, T0, T0, empty_fp(cursor, ANCHOR, membership_uuid), self.applicant_id, self.ids["lo"]),
            )
            self.member_id = cursor.fetchone()[0]
            self.proposal_id = append_event(
                cursor, EVENT_TABLE, self.member_id, 1, "PROPOSE", None, "PROPOSED", self.applicant_id,
                self.ids["basis"], f"propose-{self.tag}", extra={"reason_class": "APPLICATION"},
            )[0]
            refresh(cursor, ANCHOR, self.member_id)
            cursor.execute(
                "INSERT INTO core_application (application_uuid, applicant_id, living_organism_id, actor_id, enterer_id, "
                "occurred_at, effective_at, received_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (u(f"application:{self.tag}"), self.applicant_id, self.ids["lo"], self.applicant_id,
                 self.applicant_id, T0, T0, T0),
            )
            self.application_id = cursor.fetchone()[0]

    def assert_refused(self, write):
        with self.assertRaises((DatabaseError, IntegrityError)):
            with transaction.atomic(), connection.cursor() as cursor:
                write(cursor)

    def claim(self, cursor, *, replaces=None, actor=None, office=None):
        assignment_uuid = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO core_csaassignment (assignment_uuid, application_id, replaces_id, assignee_membership_id, "
            "assignee_office_id, actor_id, enterer_id, completion_due_at, occurred_at, effective_at, received_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (assignment_uuid, self.application_id, replaces, self.ids["membership"],
             office or self.ids["assignments"]["COORDINATOR"], actor or self.ids["identity"], self.ids["identity"],
             T0 + dt.timedelta(days=27), T0, T0, T0),
        )
        return cursor.fetchone()[0], assignment_uuid

    def engage(self, cursor, claim_uuid, *, ordinal=1, basis=None, route="LO_CSA", settings_basis=None):
        cursor.execute(
            "INSERT INTO core_applicationengagement (application_id, engagement_ordinal, route, deciding_party_id, "
            "engagement_reference, settings_basis, basis_reference, actor_id, enterer_id, occurred_at, effective_at, received_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.application_id, ordinal, route, self.ids["lo"], claim_uuid,
             settings_basis or "DEFAULT (no settings store): cq_active=No, application_sole_participant_delegation=No", basis,
             self.ids["identity"], self.ids["identity"], T0, T0, T0),
        )

    def decide(self, cursor, claim_id, *, outcome="DECLINE", route="LO_CSA", enterer=None):
        decision_uuid = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO core_applicationdecision (decision_uuid, application_id, route, deciding_party_id, outcome, "
            "assignment_reference_id, responsible_coordinator_id, responsible_capacity, qualifying_office_id, "
            "authority_decision_reference, executor_id, enterer_id, occurred_at, effective_at, received_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'COORDINATOR', %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (decision_uuid, self.application_id, route, self.ids["lo"], outcome, claim_id,
             enterer or self.ids["identity"], self.ids["assignments"]["COORDINATOR"],
             "s015d1:" + "a" * 64, self.ids["identity"], enterer or self.ids["identity"],
             T0 + dt.timedelta(hours=1), T0 + dt.timedelta(hours=1), T0 + dt.timedelta(hours=1)),
        )
        return cursor.fetchone()[0], decision_uuid

    def admit(self, cursor, decision_id, decision_uuid, *, overrides=None, member_id=None, proposal_id=None):
        changes = {
            "actor_access_epoch": None, "actor_state": "LIVING_ORGANISM", "actor_capacity": "NA_NO_PARTY",
            "enterer_identity_id": self.ids["identity"], "evidence_reference": f"application-decision:{decision_uuid}",
        }
        changes.update(overrides or {})
        event_id, _ = append_event(
            cursor, EVENT_TABLE, member_id or self.member_id, 2, "ACTIVATE", "PROPOSED", "ACTIVE", None,
            self.ids["basis"], f"admit-{fresh()}", pred_id=proposal_id or self.proposal_id,
            eff=T0 + dt.timedelta(hours=1), occ=T0 + dt.timedelta(hours=1),
            rec=T0 + dt.timedelta(hours=1), extra={"reason_class": "ADMISSION"}, overrides=changes,
        )
        cursor.execute(
            "INSERT INTO core_applicationdecisioneffect (decision_id, transition_id, occurred_at, effective_at, received_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (decision_id, event_id, T0 + dt.timedelta(hours=1), T0 + dt.timedelta(hours=1), T0 + dt.timedelta(hours=1)),
        )
        return event_id

    def test_c3_fresh_party_transition_preserves_original_grammar(self):
        with connection.cursor() as cursor:
            event_id, _ = append_event(
                cursor, EVENT_TABLE, self.member_id, 2, "ACTIVATE", "PROPOSED", "ACTIVE",
                self.applicant_id, self.ids["basis"], f"party-{self.tag}", pred_id=self.proposal_id,
                eff=T0 + dt.timedelta(hours=1), occ=T0 + dt.timedelta(hours=1),
                rec=T0 + dt.timedelta(hours=1), extra={"reason_class": "TEST"},
            )
            self.assertEqual(snapshot(cursor, event_id, self.member_id)[0][-1], "PARTY")

    def test_c4_other_eleven_chains_reject_lo_token(self):
        with transaction.atomic(), connection.cursor() as cursor:
            add_remaining_aggregates(cursor, self.ids, self.tag)
            cursor.execute("SELECT event_table FROM public.s015_anchor_map() WHERE event_table <> %s", (EVENT_TABLE,))
            tables = [row[0] for row in cursor.fetchall()]
        self.assertEqual(len(tables), 11)
        for table in tables:
            with self.subTest(table=table):
                with self.assertRaises((DatabaseError, IntegrityError)) as error:
                    with transaction.atomic(), connection.cursor() as cursor:
                        append_second_event(cursor, self.ids, table, f"other-{fresh()}", overrides={
                            "actor_id": None, "actor_access_epoch": None,
                            "actor_state": "LIVING_ORGANISM", "actor_capacity": "NA_NO_PARTY",
                        })
                self.assertIn("actor_state_vocab", str(error.exception))

    def test_c5_malformed_actor_and_missing_or_mismatched_decision_refused(self):
        with transaction.atomic(), connection.cursor() as cursor:
            claim_id, claim_uuid = self.claim(cursor)
            self.engage(cursor, claim_uuid)
            decision_id, decision_uuid = self.decide(cursor, claim_id, outcome="ACCEPT")
            for malformed in (
                {"actor_id": self.ids["identity"]}, {"actor_access_epoch": 0},
                {"actor_capacity": "STANDING"}, {"entry_mode": "COMPOSED"},
                {"prior_state": None}, {"resulting_state": "PROPOSED"},
                {"evidence_reference": "application-decision:" + str(uuid.uuid4())},
                {"evidence_reference": "missing"},
            ):
                with self.subTest(malformed=malformed):
                    self.assert_refused(lambda attempt: self.admit(attempt, decision_id, decision_uuid, overrides=malformed))
            other_identity = seed_identity(cursor, f"other-{self.tag}")
            other_uuid = u(f"membership:other:{self.tag}")
            cursor.execute(
                "INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, "
                "state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, 'PROPOSED', now(), %s, %s, %s, %s, %s) RETURNING id",
                (other_uuid, T0, T0, empty_fp(cursor, ANCHOR, other_uuid), other_identity, self.ids["lo"]),
            )
            other_member = cursor.fetchone()[0]
            other_proposal = append_event(
                cursor, EVENT_TABLE, other_member, 1, "PROPOSE", None, "PROPOSED", other_identity,
                self.ids["basis"], f"other-{self.tag}", extra={"reason_class": "APPLICATION"},
            )[0]
            refresh(cursor, ANCHOR, other_member)
            self.assert_refused(lambda attempt: self.admit(
                attempt, decision_id, decision_uuid, member_id=other_member, proposal_id=other_proposal,
            ))
            self.admit(cursor, decision_id, decision_uuid)

    def test_c5_decline_cannot_attribute_an_admission_to_the_lo(self):
        with connection.cursor() as cursor:
            claim_id, claim_uuid = self.claim(cursor)
            self.engage(cursor, claim_uuid)
            decision_id, decision_uuid = self.decide(cursor, claim_id)
        self.assert_refused(lambda cursor: self.admit(cursor, decision_id, decision_uuid))

    def test_c6_lo_admission_has_independent_whole_column_bytes(self):
        with transaction.atomic(), connection.cursor() as cursor:
            claim_id, claim_uuid = self.claim(cursor)
            self.engage(cursor, claim_uuid)
            decision_id, decision_uuid = self.decide(cursor, claim_id, outcome="ACCEPT")
            event_id = self.admit(cursor, decision_id, decision_uuid)
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_jsonb(t), t.l1_commitment, t.parts_commitment FROM public.core_organismmembershiptransition t WHERE id = %s", (event_id,))
            row, l1, parts = cursor.fetchone()
            row = json.loads(row) if isinstance(row, str) else row
            cursor.execute(
                "SELECT a.attname, a.atttypid::regtype::text FROM pg_catalog.pg_attribute a "
                "WHERE a.attrelid = 'public.core_organismmembershiptransition'::regclass AND a.attnum > 0 "
                "AND NOT a.attisdropped ORDER BY a.attname COLLATE \"C\""
            )
            inventory = [{"column": name, "pg_type": {"character varying": "varchar", "timestamp with time zone": "timestamptz"}.get(kind, kind)}
                         for name, kind in cursor.fetchall()]
            expected_record = "{" + ",".join(json.dumps(col["column"]) + ":" + canonical_value(col["pg_type"], row[col["column"]])
                                              for col in inventory) + "}"
            cursor.execute("SELECT public.s015_canonical_event_record('public.core_organismmembershiptransition'::regclass, to_jsonb(t)) FROM public.core_organismmembershiptransition t WHERE id = %s", (event_id,))
            self.assertEqual(cursor.fetchone()[0], expected_record)
            self.assertEqual(l1, digest("s015r1", l1_preimage(EVENT_TABLE, inventory, row)))
            self.assertEqual(parts, digest("s015p1", parts_preimage(EVENT_TABLE, row["event_uuid"], [])))

    def test_s1_b_g9_requires_frozen_current_route_and_effective_assignee(self):
        with connection.cursor() as cursor:
            claim_id, claim_uuid = self.claim(cursor)
        self.assert_refused(lambda cursor: self.decide(cursor, claim_id))
        with connection.cursor() as cursor:
            self.engage(cursor, claim_uuid)
        for variant in ({"route": "CIRCLE_CSA"}, {"enterer": self.applicant_id}):
            with self.subTest(variant=variant):
                self.assert_refused(lambda cursor: self.decide(cursor, claim_id, **variant))
        with connection.cursor() as cursor:
            decision_id, _ = self.decide(cursor, claim_id)
        self.assertIsInstance(decision_id, int)

    def test_s1_b_g9_rejects_an_incompatible_frozen_basis(self):
        with connection.cursor() as cursor:
            claim_id, claim_uuid = self.claim(cursor)
            self.engage(cursor, claim_uuid, settings_basis="cq_active=Yes")
        self.assert_refused(lambda cursor: self.decide(cursor, claim_id))

    def test_s2_b_g9_prime_requires_prior_unable_for_reengagement(self):
        with connection.cursor() as cursor:
            claim_id, claim_uuid = self.claim(cursor)
            self.engage(cursor, claim_uuid)
        self.assert_refused(lambda cursor: self.engage(cursor, claim_uuid, ordinal=2, basis="invented"))
        ending_uuid = str(uuid.uuid4())
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_csaassignmentend (ending_uuid, assignment_id, actor_id, enterer_id, occurred_at, effective_at, received_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (ending_uuid, claim_id, self.ids["identity"], self.ids["identity"], T0, T0, T0),
            )
            replacement_id, replacement_uuid = self.claim(cursor, replaces=claim_id)
            self.engage(cursor, replacement_uuid, ordinal=2, basis=ending_uuid)
        self.assertIsInstance(replacement_id, int)

    def test_s3_b_g10_applicant_only_and_no_decision(self):
        def withdraw(cursor, actor):
            cursor.execute(
                "INSERT INTO core_applicationwithdrawal (application_id, actor_id, enterer_id, occurred_at, effective_at, received_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (self.application_id, actor, actor, T0, T0, T0),
            )

        self.assert_refused(lambda cursor: withdraw(cursor, self.ids["identity"]))
        with connection.cursor() as cursor:
            claim_id, claim_uuid = self.claim(cursor)
            self.engage(cursor, claim_uuid)
            self.decide(cursor, claim_id)
        self.assert_refused(lambda cursor: withdraw(cursor, self.applicant_id))

    def test_s3_applicant_withdrawal_closes_the_application(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_applicationwithdrawal (application_id, actor_id, enterer_id, occurred_at, effective_at, received_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING recorded_at",
                (self.application_id, self.applicant_id, self.applicant_id, T0, T0, T0),
            )
            self.assertIsNotNone(cursor.fetchone()[0])
        self.assert_refused(lambda cursor: self.claim(cursor))

    def test_s4_pf01_single_claim_then_explicit_end_and_replacement(self):
        with connection.cursor() as cursor:
            claim_id, _ = self.claim(cursor)
        self.assert_refused(lambda cursor: self.claim(cursor))
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_csaassignmentend (ending_uuid, assignment_id, actor_id, enterer_id, occurred_at, effective_at, received_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), claim_id, self.ids["identity"], self.ids["identity"], T0, T0, T0),
            )
            new_id, _ = self.claim(cursor, replaces=claim_id)
        self.assertNotEqual(claim_id, new_id)

    def test_s5_accept_has_exactly_one_matching_effect(self):
        with connection.cursor() as cursor:
            claim_id, claim_uuid = self.claim(cursor)
            self.engage(cursor, claim_uuid)
        self.assert_refused(lambda cursor: self.decide(cursor, claim_id, outcome="ACCEPT"))

        def mismatched_link(cursor, decision_id):
            cursor.execute(
                "INSERT INTO core_applicationdecisioneffect (decision_id, transition_id, occurred_at, effective_at, received_at) "
                "VALUES (%s, %s, %s, %s, %s)", (decision_id, self.proposal_id, T0, T0, T0),
            )
            cursor.execute("SET CONSTRAINTS s3_effect_check IMMEDIATE")

        with transaction.atomic(), connection.cursor() as cursor:
            decision_id, decision_uuid = self.decide(cursor, claim_id, outcome="ACCEPT")
            self.assert_refused(lambda attempt: mismatched_link(attempt, decision_id))
            event_id = self.admit(cursor, decision_id, decision_uuid)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM core_applicationdecisioneffect WHERE decision_id = %s AND transition_id = %s", (decision_id, event_id))
            self.assertEqual(cursor.fetchone()[0], 1)
        self.assert_refused(lambda cursor: cursor.execute(
            "INSERT INTO core_applicationdecisioneffect (decision_id, transition_id, occurred_at, effective_at, received_at) "
            "VALUES (%s, %s, %s, %s, %s)", (decision_id, event_id, T0, T0, T0),
        ))

    def test_s6_application_references_are_database_foreign_keys(self):
        self.assert_refused(lambda cursor: cursor.execute(
            "INSERT INTO core_application (application_uuid, applicant_id, living_organism_id, actor_id, enterer_id, "
            "occurred_at, effective_at, received_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), -1, self.ids["lo"], self.applicant_id, self.applicant_id, T0, T0, T0),
        ))
        self.assert_refused(lambda cursor: cursor.execute(
            "INSERT INTO core_applicationengagement (application_id, engagement_ordinal, route, deciding_party_id, "
            "engagement_reference, settings_basis, actor_id, enterer_id, occurred_at, effective_at, received_at) "
            "VALUES (%s, 1, 'LO_CSA', %s, 'missing', 'DEFAULT', %s, %s, %s, %s, %s)",
            (-1, self.ids["lo"], self.ids["identity"], self.ids["identity"], T0, T0, T0),
        ))
