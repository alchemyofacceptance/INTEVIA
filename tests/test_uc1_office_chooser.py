"""A current, permitted chooser name belongs to the organisation’s setting block."""
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.urls import reverse
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, care_state_text, office_path, signed_in


def chosen_fields(page):
    cards = page.select("section", **{"aria-labelledby": "settings-assistance"})
    return [(term["text"], value["text"]) for card in cards for group in page.select("dl", within=card)
            for term, value in zip(page.select("dt", within=group), page.select("dd", within=group), strict=True)]


@override_settings(ALLOWED_HOSTS=["testserver"])
class OfficeChooserTests(TransactionTestCase):
    def setUp(self):
        from tests.office_record_support import approved_office
        from tests.test_law_acts import add_member

        self.owner, self.chooser, self.office, _, _, _ = approved_office()
        self.url = reverse("organisation_settings", args=(self.owner.organism_id,))
        self.member = signed_in(add_member(self.owner))

    def read(self, client):
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200, "The admitted member must receive the settings page.")
        assert_private(self, response)
        return response, Markup(response.content.decode())

    def choose(self, enabled=False, predecessor=None):
        from src.intevia.services.care_assistance import set_assistance

        return set_assistance(credential=self.chooser.credential, organism_uuid=self.owner.organism_id,
                              enabled=enabled, predecessor=predecessor, request_uuid=uuid4())

    def set_name(self, name):
        # The current resolution is independent of the original choice and of the account's shell label.
        with connection.cursor() as cursor:
            cursor.execute("UPDATE public.core_identityresolution SET display_name=%s WHERE identity_id=%s",
                           [name, self.chooser.pk])
            self.assertEqual(cursor.rowcount, 1, "The fixture must change exactly the chooser's current resolution.")

    def test_the_setting_shows_the_current_name_only_where_released(self):
        off = self.choose()
        name = "Rowan <Trustee> & Fell"
        self.set_name(name)
        for client, has_form in ((self.member, False), (signed_in(self.chooser), True)):
            with self.subTest(switch_form=has_form):
                response, page = self.read(client)
                self.assertEqual(chosen_fields(page), [("Chosen by", name)],
                                 "The setting must show the current resolved chooser name after the act.")
                office_response = client.get(office_path(self.owner, self.office), headers={"Purpose": "prefetch"})
                assert_private(self, office_response)
                office_page = Markup(office_response.content.decode())
                record = office_response.context["record"]
                self.assertEqual(record["care"].get("switched_by_name"), name,
                                 "The office read must supply the current name in the setting projection.")
                self.assertEqual(record["care"].get("switched_by"), self.chooser.identity_id,
                                 "The permitted service identity must be the person who made this choice.")
                self.assertTrue(any(row["kind"] == "setting_change" for row in record["history"]),
                                "The privacy witness must include the human choice in the office history.")
                rows = office_page.select("ol", classes=("office-timeline",))
                self.assertTrue(len(rows) == 1 and office_page.select("li", within=rows[0]),
                                "The rendered office must contain populated history rows.")
                self.assertNotIn(name, rows[0]["text"], "No timeline row may attribute the choice to its Enterer.")
                self.assertFalse(any(name in value for node in office_page.nodes if any(parent is rows[0] for parent in node["parents"])
                                     for value in node["attrs"].values() if value),
                                 "A history attribute must not carry the chooser name.")
                self.assertFalse(any("switched_by_name" in row for row in record["history"]),
                                 "No history row may receive the chooser-name field.")
                card = page.select("section", **{"aria-labelledby": "settings-assistance"})[0]
                self.assertEqual(bool(page.select("form", within=card)), has_form,
                                 "The response-channel check must cover both the command form and the text-only setting.")
                ref = str(self.chooser.identity_id)
                self.assertNotIn(ref.encode(), office_response.content, "The office HTML must also omit the chooser UUID.")
                self.assertNotIn(ref.encode(), response.content, "The chooser's UUID must be absent from all HTML, including hidden fields.")
                self.assertFalse(any(ref in value for value in response.headers.values()),
                                 "Response headers must also omit the chooser's UUID.")
                self.assertNotIn(ref, response.cookies.output(), "Response cookies must also omit the chooser’s UUID.")
                self.assertFalse(page.select("trustee"), "Punctuation in the current name must remain escaped text.")

        self.choose(True, off["act_uuid"])
        on, page = self.read(self.member)
        self.assertIn("On · Organisation choice", care_state_text(on.content.decode()), "The On control must be chosen explicitly.")
        self.assertEqual(chosen_fields(page), [("Chosen by", name)], "A chosen On setting must also name its chooser.")

    def test_default_blank_and_severed_choices_have_no_name_line(self):
        default, page = self.read(self.member)
        self.assertEqual(chosen_fields(page), [], "A default setting must have no chooser label or placeholder.")
        self.assertNotIn("switched_by_name", default.context["setting"], "The default must omit the chooser-name field.")
        self.choose()
        self.set_name(" \t ")
        blank, page = self.read(self.member)
        self.assertEqual(chosen_fields(page), [], "A whitespace-only resolution must omit the whole chooser line.")
        self.assertNotIn("switched_by_name", blank.context["setting"], "Blank names must be omitted at the read boundary.")
        old_name = "Chooser before severance"
        self.set_name(old_name)
        self.assertEqual(chosen_fields(self.read(self.member)[1]), [("Chosen by", old_name)],
                         "The positive control must resolve the chooser before severance.")
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM public.core_identityresolution WHERE identity_id=%s", [self.chooser.pk])
            cursor.execute("SELECT EXISTS (SELECT FROM public.core_identityresolutionsevered WHERE identity_id=%s)",
                           [self.chooser.pk])
            self.assertTrue(cursor.fetchone()[0], "The database must witness the actual severance before the final read.")
        severed, page = self.read(self.member)
        self.assertEqual(chosen_fields(page), [], "A severed chooser must leave neither a name nor a placeholder.")
        self.assertNotIn("switched_by_name", severed.context["setting"], "Severance must remove the name from the settings read.")
        self.assertNotContains(severed, old_name, msg_prefix="The previous chooser name must not survive elsewhere on the page.")
        self.assertNotContains(severed, str(self.chooser.identity_id), msg_prefix="A severed chooser must have no identifier fallback.")
        self.assertIn("Off · Organisation choice", care_state_text(severed.content.decode()), "Severance must preserve the recorded setting.")


class ChooserProjectionTests(SimpleTestCase):
    def test_withholding_precedes_name_resolution(self):
        from src.intevia.services import office_record as service
        from tests.test_uc4_settings_page import render_settings

        person, owner, key = uuid4(), uuid4(), uuid4()
        row = dict(act_uuid=key, enabled=False, can_change=False, enterer_identity_uuid=person,
                   authority_source={"kind": "OFFICE", "route": "COORDINATOR_ROUTE",
                                     "organism": str(owner), "reference": str(uuid4())})
        # This totals-only authority projection cannot be produced by a native CARE switch.
        # Exercise the real disclosure rule before the resolver is allowed to see a person.
        totals_only = {"kind": "CQ_RESULT", "counting_rule": {"reporting": "TOTALS_ONLY"}}

        def lookup(statement, params):
            self.assertEqual(params, [person], "The resolution lookup must use only the released identity reference.")
            return [{"display_name": "Released Name"}]

        actor = SimpleNamespace(pk=1)
        with patch.object(service.disc, "_rows", side_effect=lookup) as read:
            withheld = service._office_setting(row | {"authority_source": totals_only}, actor, owner)
            self.assertEqual(read.call_count, 0, "A withheld chooser must trigger no name lookup.")
            self.assertEqual(withheld, {"enabled": False, "choice_source": "chosen", "can_change": False, "switch_receipt": key},
                             "Withholding must preserve the choice while removing both chooser fields.")
            released = service._office_setting(row.copy(), actor, owner)
            self.assertEqual(released.get("switched_by_name"), "Released Name", "The release control must resolve this chooser.")
            read.side_effect = None
            read.return_value = []
            missing = service._office_setting(row.copy(), actor, owner)
            self.assertNotIn("switched_by_name", missing, "An admitted chooser with no matching resolution must omit the name.")
        for setting in (withheld, missing):
            raw = render_settings(setting).content.decode()
            self.assertEqual(chosen_fields(Markup(raw)), [], "A withheld or missing name must produce no chooser line.")
            self.assertIn("Off · Organisation choice", care_state_text(raw), "Withholding a name must preserve the chosen Off setting.")
            self.assertNotIn(str(person), raw, "Neither withheld nor missing resolution may expose an identity reference.")
