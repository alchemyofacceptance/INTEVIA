"""C-N.1 display names, read-cut boundaries and name-query inventory."""

from datetime import timedelta
from uuid import uuid4

from django.db import connection, transaction
from django.db.transaction import TransactionManagementError
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core import models
from src.intevia.services import circle_commands
from src.intevia.services._record_names import full_name_and_short_label, office_label
from tests.s015_0021_support import database_now, found_nonroot, register_test_learner


def name_sources(queries):
    tables = (models.LOFoundingContentVersion._meta.db_table, models.CircleBirthDescription._meta.db_table)
    return [table for query in queries for table in tables if table in query["sql"].lower()]


def form_generic(owner, founder, name=None):
    return circle_commands.form(
        credential=founder.credential, organism_uuid=owner.organism_id, circle_uuid=uuid4(),
        kind="GENERIC", purpose="Care for the local river", name=name, request_uuid=uuid4(),
    )


class OfficeLabelTests(SimpleTestCase):
    def test_office_codes_are_words(self):
        self.assertEqual(office_label("TREASURER"), "Treasurer", "The Treasurer heading must use title case.")
        self.assertEqual(office_label("DEPUTY_TREASURER"), "Deputy Treasurer",
                         "Each underscore in an office code must display as a space.")

    def test_office_label_preserves_intevia(self):
        self.assertEqual(office_label("INTEVIA_FOUNDER_STEWARD"), "INTEVIA Founder Steward",
                         "An office label must keep INTEVIA in capitals.")
        self.assertEqual(office_label("TREASURER"), "Treasurer",
                         "The Treasurer label must retain its ordinary title case.")


class RecordNameTests(TransactionTestCase):
    def test_fixed_circle_names_and_labels(self):
        # These admitted-row projections exercise only the mapping from Circle kind.
        with transaction.atomic():
            cut = database_now()
            with CaptureQueriesContext(connection) as queries:
                for kind, full, short in (
                    ("GOVERNANCE", "Board of Trustees", "Board"),
                    ("APPLICATION", "Applications", "Applications"),
                    ("FINANCE", "Finance", "Finance"),
                    ("OPERATIONS", "Operations", "Operations"),
                ):
                    with self.subTest(kind=kind):
                        circle = models.Circle(parent_organism_id=7, kind=kind)
                        self.assertEqual(full_name_and_short_label(owner_id=7, circle=circle, cut=cut),
                                         (full, short), f"{kind} must have its approved full name and short label.")
            self.assertEqual(name_sources(queries), [], "Fixed Circle labels must need no stored name.")

    def test_founding_name_at_the_read_cut(self):
        from tests.test_law_acts import ordinary_birth

        owner, _ = ordinary_birth()
        row = models.LOFoundingContentVersion.objects.get(living_organism=owner, version=1)
        visible = max(row.received_at, row.effective_at, row.recorded_at)
        with transaction.atomic(), connection.cursor() as cursor:
            sparse_id = found_nonroot(cursor, fixture_at=database_now())["lo"]
            self.assertFalse(models.LOFoundingContentVersion.objects.filter(living_organism_id=sparse_id).exists(),
                             "The fallback fixture must be an organisation without founding content.")
            with CaptureQueriesContext(connection) as queries:
                names = full_name_and_short_label(owner_id=owner.pk, cut=visible)
            self.assertEqual(name_sources(queries), [models.LOFoundingContentVersion._meta.db_table],
                             "The observer must recognise the permitted founding-content query.")
            self.assertEqual(names,
                             (row.name, row.name), "Both organisation labels must equal the version-one name.")
            self.assertEqual(full_name_and_short_label(owner_id=owner.pk, cut=visible - timedelta(microseconds=1)),
                             ("This organisation", "This organisation"),
                             "A later founding name must be absent at the earlier cut.")
            self.assertEqual(full_name_and_short_label(owner_id=sparse_id, cut=database_now()),
                             ("This organisation", "This organisation"),
                             "An organisation without founding content must never borrow a name or show an identifier.")

    def test_generic_birth_name_and_fallback(self):
        from tests.test_law_acts import ordinary_birth

        owner, founder = ordinary_birth()
        named = form_generic(owner, founder, "River stewards")
        unnamed = form_generic(owner, founder)
        birth = models.CircleBirthDescription.objects.get(circle=named)
        visible = max(birth.recorded_at, birth.effective_at)
        with transaction.atomic():
            with CaptureQueriesContext(connection) as queries:
                names = full_name_and_short_label(owner_id=owner.pk, circle=named, cut=visible)
            self.assertEqual(name_sources(queries), [models.CircleBirthDescription._meta.db_table],
                             "The observer must recognise the permitted birth-description query.")
            self.assertEqual(names,
                             ("River stewards", "River stewards"), "Both Circle labels must equal the birth name.")
            self.assertEqual(full_name_and_short_label(owner_id=owner.pk, circle=named,
                                                      cut=visible - timedelta(microseconds=1)),
                             ("Circle", "Circle"), "A Circle name must wait until its birth description is visible.")
            self.assertEqual(full_name_and_short_label(owner_id=owner.pk, circle=unnamed, cut=database_now()),
                             ("Circle", "Circle"), "An unnamed Circle must display the word Circle in both places.")

    def test_refused_owner_never_reads_name_sources(self):
        from src.intevia.services import care_assistance as care
        from src.intevia.services.office_record import office_record
        from tests.test_discussion_office_links import office_basis, record_office
        from tests.test_law_acts import ordinary_birth

        owner, founder = ordinary_birth()
        office = record_office(owner, founder, office_basis(owner, founder))
        outsider = register_test_learner(username="labels-reader-" + uuid4().hex)
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(care.SettingRefused, msg="A non-member must be refused the office read.") as denied:
                office_record(credential=outsider.credential, organism_uuid=owner.organism_id,
                              office_definition_uuid=office)
        self.assertEqual(denied.exception.code, "CARE_SETTING_UNAVAILABLE",
                         "The public office read must return the unavailable refusal.")
        self.assertEqual(name_sources(queries), [], "A refused office read must query neither name source.")

    def test_foreign_circle_is_refused_before_name_lookup(self):
        from src.intevia.services import care_assistance as care
        from tests.test_discussion_office_links import office_basis, record_office
        from tests.test_law_acts import ordinary_birth

        owner, founder = ordinary_birth()
        other, other_founder = ordinary_birth()
        foreign = form_generic(other, other_founder, "Other river group")
        office = record_office(owner, founder, office_basis(owner, founder))
        with transaction.atomic():
            actor = care._actor(founder.credential)
            owner_id = care._lock_scope(owner.organism_id, actor)
            access = care._office_access_locked(founder.credential, owner.organism_id, office, actor, owner_id)
            cut = access["evaluated_at"]
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(ValueError, msg="A foreign Circle row must be refused.") as denied:
                    full_name_and_short_label(owner_id=owner_id, circle=foreign, cut=cut)
            self.assertEqual(str(denied.exception), "The Circle does not belong to the admitted organisation.",
                             "The refusal must identify the Circle ownership mismatch.")
            self.assertEqual(name_sources(queries), [], "A foreign Circle must be refused before either name is read.")

    def test_names_require_the_read_transaction(self):
        cut = database_now()
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(TransactionManagementError, msg="An unbound name read must refuse before lookup."):
                full_name_and_short_label(owner_id=1, cut=cut)
        self.assertEqual(name_sources(queries), [], "Calling outside the read transaction must query neither name source.")

    def test_founding_name_waits_for_receipt_alone(self):
        # The database assigns recorded_at. Supply the independent later receipt on
        # insert, so the comparison needs neither a clock change nor an amended row.
        with transaction.atomic(), connection.cursor() as cursor:
            effective = database_now()
            ids = found_nonroot(cursor, fixture_at=effective)
            actor = models.Identity.objects.get(pk=ids["identity"])
            content = models.LOFoundingContentVersion.objects.create(
                living_organism_id=ids["lo"], version=1, name="River reading room", purpose="Share local records",
                actor_identity=actor, actor_access_epoch=actor.access_epoch,
                authority_decision_reference=ids["founding_authority_decision_reference"], content_commitment=None,
                effective_at=effective, received_at=effective + timedelta(days=1),
            )
            content.refresh_from_db()
            cut = max(content.effective_at, content.recorded_at)
            self.assertLess(cut, content.received_at, "Only receipt may lie after the cut in this fixture.")
            self.assertEqual(full_name_and_short_label(owner_id=ids["lo"], cut=cut),
                             ("This organisation", "This organisation"),
                             "Until receipt, both labels must say This organisation even after recording and effect.")
            self.assertEqual(full_name_and_short_label(owner_id=ids["lo"], cut=content.received_at),
                             (content.name, content.name), "Both labels must become visible at the exact receipt time.")
