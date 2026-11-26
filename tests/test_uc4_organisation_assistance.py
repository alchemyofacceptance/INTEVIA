"""Organisation-scoped CARE reads, including the interim probation boundary."""
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TransactionTestCase

from core.models import OrganismMembership
from src.intevia.services import care_assistance as care
from src.intevia.services import member_navigation, organism_membership_service, timed_events_handoff
from tests import test_c1b_departure
from tests.cq_authority_support import authority
from tests.office_record_support import approved_office
from tests.s015_0021_support import database_now
from tests.test_law_acts import add_member, ordinary_birth
from tests.test_office_record_services import fixture_membership_state


def read_setting(owner, reader):
    return care.read_organisation_assistance(credential=reader.credential, organism_uuid=owner.organism_id)


def choose(owner, founder, enabled, predecessor=None):
    return care.set_assistance(credential=founder.credential, organism_uuid=owner.organism_id,
                               enabled=enabled, predecessor=predecessor, request_uuid=uuid4())


def record_counts(owner):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT (SELECT count(*) FROM public.core_careassistancesettingact WHERE organism_id=%s),
                   (SELECT count(*) FROM public.core_careofficeobservation WHERE organism_id=%s),
                   (SELECT count(*) FROM public.core_livingorganismevent WHERE living_organism_id=%s)
        """, [owner.pk] * 3)
        return cursor.fetchone()


class OrganisationAssistanceTests(TransactionTestCase):
    def assert_unavailable(self, credential, target):
        with self.assertRaises(care.SettingRefused, msg="An unseen setting must refuse the read.") as refusal:
            care.read_organisation_assistance(credential=credential, organism_uuid=target)
        self.assertEqual(refusal.exception.code, "CARE_SETTING_UNAVAILABLE",
                         "Every unseen setting must use the same unavailable code.")
        self.assertEqual(refusal.exception.args, care.SettingRefused("CARE_SETTING_UNAVAILABLE").args,
                         "The reason for withholding the setting must not change the public message.")

    def test_member_without_office_reads_default_then_chosen_off(self):
        owner, founder = ordinary_birth()
        reader = add_member(owner)
        self.assertEqual(read_setting(owner, reader),
                         {"enabled": True, "choice_source": "default", "can_change": False},
                         "A member without an office must see default On and no change authority.")
        off = choose(owner, founder, False)
        self.assertEqual(read_setting(owner, reader), {
            "enabled": False, "choice_source": "chosen", "can_change": False,
            "switch_receipt": off["act_uuid"], "switched_by": founder.identity_id,
            "switched_by_name": founder.display_name,
        }, "The member must see the local Off choice with its receipt and released chooser name.")

    def test_departed_foreign_unknown_and_unbound_get_the_same_refusal(self):
        owner, founder = ordinary_birth()
        foreign, _ = ordinary_birth()
        departing = add_member(owner)
        membership = OrganismMembership.objects.get(living_organism=owner, identity=departing)
        service = organism_membership_service.OrganismMembershipService(authority=authority(), clock=database_now)
        service.leave_organism(test_c1b_departure.DepartureTests().command(membership))
        cases = (
            ("departed", departing.credential, owner.organism_id),
            ("foreign", founder.credential, foreign.organism_id),
            ("unknown", founder.credential, uuid4()),
            ("unbound", get_user_model().objects.create_user(username="uc4a-" + uuid4().hex), owner.organism_id),
        )
        for reason, credential, target in cases:
            with self.subTest(reason=reason):
                self.assert_unavailable(credential, target)

    def test_failed_setting_query_propagates_its_database_error(self):
        owner, founder = ordinary_birth()
        self.assertEqual(read_setting(owner, founder)["choice_source"], "default",
                         "First establish that the admitted founder can read the organisation's default.")
        failure = DatabaseError("Setting query interrupted")

        def interrupt_query(execute, sql, params, many, context):
            if "core_careassistancesettingact" in sql:
                raise failure
            return execute(sql, params, many, context)

        with connection.execute_wrapper(interrupt_query), self.assertRaises(DatabaseError) as caught:
            read_setting(owner, founder)
        self.assertIs(caught.exception, failure, "A failed query must propagate its own error to the caller.")

    def test_member_on_probation_gets_the_same_refusal(self):
        owner, founder = ordinary_birth()
        reader = add_member(owner)
        self.assertTrue(read_setting(owner, reader)["enabled"], "The active member must first see default On.")
        fixture_membership_state(owner, founder, reader, OrganismMembership.State.PROBATIONARY)
        self.assert_unavailable(reader.credential, owner.organism_id)

    def test_choice_is_local_and_read_at_the_admission_cut(self):
        owner, founder = ordinary_birth()
        other, other_founder = ordinary_birth()
        choose(other, other_founder, False)
        self.assertEqual(read_setting(owner, founder)["choice_source"], "default",
                         "A choice in another organisation must leave this organisation on its default.")
        off = choose(owner, founder, False)
        cut = database_now()
        on = choose(owner, founder, True, predecessor=off["act_uuid"])
        self.assertLess(cut, on["recorded_at"], "The second choice must fall after the controlled cut.")
        with patch.object(member_navigation, "database_now", return_value=cut):
            self.assertEqual(read_setting(owner, founder)["switch_receipt"], off["act_uuid"],
                             "The read must select the choice visible at the admission cut.")
        self.assertEqual(read_setting(owner, founder)["switch_receipt"], on["act_uuid"],
                         "A fresh read must select the newest visible version.")

    def test_read_preserves_setting_observation_events_and_wake(self):
        owner, founder, *_ = approved_office()
        for state in ("default", "chosen"):
            # Materialise a choice only between the two reads.
            if state == "chosen":
                choose(owner, founder, False)
            before = record_counts(owner)
            slip = timed_events_handoff.open_slip()
            try:
                setting = read_setting(owner, founder)
                self.assertEqual(record_counts(owner), before,
                                 "Reading must leave setting acts, observations and all organism events intact.")
                self.assertIsNone(slip.organism_uuid, "Reading this setting must leave the wake slip empty.")
                self.assertTrue(setting["can_change"], "The current Organisation Coordinator must have change authority.")
                self.assertEqual(setting["choice_source"], state, "The read must describe the requested source.")
            finally:
                timed_events_handoff.discard(slip)


class OrganisationChooserProjectionTests(SimpleTestCase):
    def test_withheld_chooser_never_starts_a_name_lookup(self):
        owner, chooser, key = uuid4(), uuid4(), uuid4()
        # Native switches use a Coordinator. Supply a totals-only source to the real disclosure rule.
        row = {"act_uuid": key, "enabled": False, "can_change": False, "enterer_identity_uuid": chooser,
               "authority_source": {"kind": "CQ_RESULT", "counting_rule": {"reporting": "TOTALS_ONLY"}}}
        with patch.object(care.disc, "_rows", return_value=[]) as lookup:
            result = care._setting_with_name(row, SimpleNamespace(pk=1), owner)
        self.assertEqual(lookup.call_count, 0, "Withholding must happen before any chooser-name lookup.")
        self.assertEqual(result, {"enabled": False, "choice_source": "chosen", "can_change": False,
                                  "switch_receipt": key},
                         "The setting and receipt remain visible while both chooser fields are withheld.")


class LimitedMembershipReadTests(TransactionTestCase):
    def test_suspended_and_restricted_members_can_read(self):
        owner, founder = ordinary_birth()
        for state in (OrganismMembership.State.SUSPENDED, OrganismMembership.State.RESTRICTED):
            with self.subTest(state=state):
                reader = add_member(owner)
                fixture_membership_state(owner, founder, reader, state)
                try:
                    setting = read_setting(owner, reader)
                except care.SettingRefused:
                    setting = None
                self.assertEqual(setting,
                                 {"enabled": True, "choice_source": "default", "can_change": False},
                                 "A suspended or restricted member must still be able to read the setting.")
