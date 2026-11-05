"""LAW-1/F storage witnesses L-01 to L-03. Authority-atomic acts arrive in P2."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event
from uuid import uuid4

from django.db import IntegrityError, connection, connections, transaction
from django.test import TransactionTestCase
from psycopg import sql

from core import models
from src.intevia.services import law_records, root_genesis
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.library_service import LibraryService
from tests import root_genesis_support, s015_0021_support as support


class LibraryFixtureCapability:
    def authorise(self, *, identity, action, target, timestamp):
        return "fixture:library-publication"


@contextmanager
def staged_facts():
    """Exercise projections before source qualification. Never commit staged facts."""
    with transaction.atomic():
        try:
            yield
        finally:
            transaction.set_rollback(True)


def found():
    if not models.PlatformTermsVersion.objects.exists():
        support.register_test_learner(username="l-terms-" + uuid4().hex)
    at = support.database_now()
    with transaction.atomic(), connection.cursor() as cursor:
        return support.found_nonroot(cursor, tag="l-" + uuid4().hex, fixture_at=at)


def query_one(statement, params=()):
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchone()[0]


class Document:
    def __init__(self, identity):
        role, _ = models.Role.objects.get_or_create(name="LAW library fixture role")
        models.ProfileRole.objects.get_or_create(identity=identity, role=role)
        self.author = identity.credential
        self.service = LibraryService(
            authority=ContributionAuthority(LibraryFixtureCapability())
        )
        self.name = "law-" + uuid4().hex
        self.resource = self.service.create_resource(
            identity=self.author,
            resource_id=self.name,
            content="[#1] First version.",
            evidence_reference="fixture:law:creation",
        )
        self.first = self.resource.current_version
        self.transition("publish_library_resource")

    def transition(self, command):
        return self.service.transition_resource(
            identity=self.author,
            resource_id=self.name,
            command=command,
            evidence_reference="fixture:law:" + uuid4().hex,
        )

    def next_version(self):
        return self.service.create_successor_version(
            identity=self.author,
            resource_id=self.name,
            content="[#1] Second version.",
            evidence_reference="fixture:law:successor",
        )


def stage_binding(
    doc,
    lo,
    event,
    at,
    *,
    version=None,
    scope="LIVING_ORGANISM",
    law_class="POLICY",
    route="COORDINATOR_ROUTE",
    owner="LO_COORDINATOR",
    reserved=False,
    predecessor=None,
    binding_uuid=None,
    resolution_effect=None,
    resolution_reference=None,
):
    """Direct projection input. A founding event proves no LAW authority."""
    key = binding_uuid or uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO public.core_organismlawbinding
               (binding_uuid,resource_id,version_id,organism_id,scope_kind,law_class,
                consequence_class,decision_route,owner_kind,adoption_event_id,in_force_from,
                reserves_bylaws,predecessor_id,resolution_effect,resolution_reference)
               VALUES (%s,%s,%s,%s,%s,%s,'BINDING',%s,%s,%s,%s,%s,%s,%s,%s)""",
            [
                key,
                doc.resource.pk,
                (version or doc.first).pk,
                lo,
                scope,
                law_class,
                route,
                owner,
                event,
                at,
                reserved,
                predecessor,
                resolution_effect,
                resolution_reference,
            ],
        )
    return key


def check_now(name):
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SET CONSTRAINTS {} IMMEDIATE").format(sql.Identifier(name))
        )


def compete_on_installed_window(test, at):
    """Replay the *installed* invariant at real commits on two independent connections.

    Only the columns used by the installed exclusion are needed. The dedicated disposable schema
    makes this PG storage witness independent of the P2 act/receipt gate.
    """
    support._require_disposable_fixture_database()
    namespace = "law_window_" + uuid4().hex
    quoted_schema = sql.Identifier(namespace)
    table = sql.Identifier(namespace, "windows")
    constraint = sql.Identifier(namespace, "law_window")
    definition = query_one(
        """SELECT pg_get_constraintdef(oid) FROM pg_constraint
           WHERE conrelid='public.core_organismlawbinding'::regclass AND conname='l_one_version_per_scope'"""
    )
    insert = sql.SQL("INSERT INTO {} VALUES (1,'LIVING_ORGANISM',1,NULL,%s,%s)").format(table)
    observed_empty = Event()

    def other_connection():
        db = connections["default"]
        try:
            with transaction.atomic(), db.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '15s'")
                cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(table))
                visible = cursor.fetchone()[0]
                observed_empty.set()
                cursor.execute(insert, [at, None])
            return visible, None
        except IntegrityError as error:
            return visible, (
                error.__cause__.sqlstate,
                error.__cause__.diag.constraint_name,
            )
        finally:
            db.close()

    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(quoted_schema))
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("""CREATE TABLE {} (resource_id bigint,scope_kind text,organism_id bigint,scope_circle_id bigint,
                in_force_from timestamptz,in_force_until timestamptz,CONSTRAINT law_window {})""").format(
                    table, sql.SQL(definition)
                )
            )
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SET CONSTRAINTS {} IMMEDIATE").format(constraint)
                )
                cursor.execute(insert, [at, None])
                contender = pool.submit(other_connection)
                test.assertTrue(
                    observed_empty.wait(10),
                    "The independent contender must see the empty scope.",
                )
            visible, refused = contender.result(timeout=20)
        test.assertEqual(
            (visible, refused),
            (0, ("23P01", "law_window")),
            "Exactly one first window must commit; the competing commit must fail on overlap.",
        )
        boundary = at + timedelta(seconds=1)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql.SQL("SET CONSTRAINTS {} IMMEDIATE").format(constraint))
            cursor.execute(
                sql.SQL("UPDATE {} SET in_force_until=%s").format(table), [boundary]
            )
            cursor.execute(insert, [boundary, None])
        test.assertEqual(
            query_one(sql.SQL("SELECT count(*) FROM {}").format(table)),
            2,
            "Adjacent half-open windows must commit with immediate exclusion checking.",
        )
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql.SQL("INSERT INTO {} VALUES "
                "(2,'LIVING_ORGANISM',1,NULL,%s,NULL),(1,'LIVING_ORGANISM',2,NULL,%s,NULL),"
                "(1,'CIRCLE',1,1,%s,NULL)").format(table), [at, at, at])
        test.assertEqual(query_one(sql.SQL("SELECT count(*) FROM {}").format(table)), 5)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(table))
            cursor.execute(sql.SQL("DROP SCHEMA {}").format(quoted_schema))


class LawRecordTests(TransactionTestCase):
    def test_overlapping_versions_and_competing_first_windows(self):
        """L-01. The installed database exclusion discriminates G5 by assertion."""
        first, other = found(), found()
        doc = Document(models.Identity.objects.get(pk=first["identity"]))
        second = doc.next_version()
        doc.transition("publish_library_resource")
        at = support.database_now()
        with staged_facts():
            stage_binding(doc, first["lo"], first["lo_event"], at)
            stage_binding(doc, first["lo"], other["lo_event"], at, version=second)
            with self.assertRaises(
                IntegrityError, msg="Two overlapping versions were admitted."
            ) as refused:
                with transaction.atomic():
                    check_now("l_one_version_per_scope")
            self.assertEqual(
                (
                    refused.exception.__cause__.sqlstate,
                    refused.exception.__cause__.diag.constraint_name,
                ),
                ("23P01", "l_one_version_per_scope"),
                "The database must identify this exact window exclusion.",
            )
        compete_on_installed_window(self, at)

    def test_publication_archive_and_source_of_law(self):
        """L-02. Publication and ordinary LO events confer no LAW standing."""
        ids = found()
        doc = Document(models.Identity.objects.get(pk=ids["identity"]))
        lo = models.LivingOrganism.objects.get(pk=ids["lo"])
        self.assertEqual(
            law_records.laws_in_force(lo.organism_id),
            [],
            "Publishing a document cannot adopt it.",
        )
        unpublished = doc.next_version()
        at = support.database_now()
        with (
            self.assertRaisesMessage(
                IntegrityError, "publication of this exact version"
            ),
            transaction.atomic(),
        ):
            stage_binding(doc, lo.pk, ids["lo_event"], at, version=unpublished)
        with staged_facts():
            binding = stage_binding(doc, lo.pk, ids["lo_event"], at)
            history = law_records.law_history(lo.organism_id, doc.name)
            self.assertEqual(
                [
                    (r["binding_uuid"], r["version_id"], r["ending_act"])
                    for r in history
                ],
                [(binding, doc.first.pk, None)],
                "A successor draft cannot replace an adopted version.",
            )
            doc.transition("publish_library_resource")
            doc.transition("deprecate_library_resource")
            rows = law_records.laws_in_force(lo.organism_id)
            self.assertEqual(
                [(r["binding"], r["version_number"]) for r in rows],
                [(binding, 1)],
                "Deprecation must preserve the original binding.",
            )
            with self.assertRaises(IntegrityError) as refused, transaction.atomic():
                doc.transition("archive_library_resource")
            self.assertEqual(
                refused.exception.__cause__.diag.constraint_name,
                "l_archive_in_force",
                "The active law, rather than an unrelated refusal, must protect the resource.",
            )
            with (
                self.assertRaisesMessage(IntegrityError, "recorded LAW ending"),
                transaction.atomic(),
            ):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE public.core_organismlawbinding SET in_force_until=statement_timestamp() "
                        "WHERE binding_uuid=%s",
                        [binding],
                    )
            with (
                self.assertRaisesMessage(
                    IntegrityError, "S015 immutable or append-only row mutation refused"
                ),
                transaction.atomic(),
            ):
                models.LibraryResourceVersion.objects.filter(pk=doc.first.pk).update(
                    content="changed"
                )
            with self.assertRaises(IntegrityError) as unqualified, transaction.atomic():
                check_now("l_check_adoption")
            self.assertEqual(
                unqualified.exception.__cause__.diag.constraint_name,
                "l_qualified_effect",
                "A valid FOUND event cannot serve as a LAW act.",
            )
        self.assertEqual(
            law_records.law_history(lo.organism_id, doc.name),
            [],
            "No binding from the unqualified act may persist.",
        )

        # The existing gateway helper supplies a second real LO event at a later
        # cut. It carries no LAW qualification. Roll the entire projection back.
        doc.transition("publish_library_resource")
        before = support.database_now()
        boundary = support.database_now()
        with transaction.atomic(), connection.cursor() as cursor:
            gateway = support.seed_test_gateway(
                cursor, ids, at=boundary, include_question=False
            )
        with staged_facts():
            first = stage_binding(doc, lo.pk, ids["lo_event"], before)
            second = uuid4()
            check_now("l_one_version_per_scope")
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO public.core_organismlawending
                       (binding_id,end_event_id,decision_route,end_kind,successor_id)
                       VALUES (%s,%s,'COORDINATOR_ROUTE','SUPERSEDED',%s)""",
                    [first, gateway["head_event"], second],
                )
            stage_binding(
                doc,
                lo.pk,
                gateway["head_event"],
                boundary,
                version=unpublished,
                predecessor=first,
                binding_uuid=second,
            )
            check_now("l_check_ending")
            for cut, known, expected in (
                (before, None, first),
                (boundary, before, first),
                (boundary, None, second),
            ):
                rows = law_records.laws_in_force(lo.organism_id, at=cut, known_at=known)
                self.assertEqual(
                    [r["binding"] for r in rows],
                    [expected],
                    "The half-open boundary and the independent knowledge cut must both hold.",
                )
            history = law_records.law_history(lo.organism_id, doc.name)
            self.assertEqual(history[0]["successor_id"], second)
            self.assertEqual(history[0]["ending_act"], history[1]["adoption_act"])
            self.assertEqual(history[0]["effective_until"], boundary)
            self.assertEqual(history[1]["predecessor_id"], first)
        self.assertEqual(law_records.law_history(lo.organism_id, doc.name), [])

    def test_platform_scope_and_ownership(self):
        """L-03. Platform visibility, rooted scope, class ordering, markers and ToR areas."""
        root = root_genesis.execute(root_genesis_support.command())
        ids = found()
        local = models.LivingOrganism.objects.get(pk=ids["lo"])
        doc = Document(models.Identity.objects.get(pk=ids["identity"]))
        root_event = models.LivingOrganismEvent.objects.get(
            living_organism=root, action="FOUND"
        )
        at = support.database_now()
        with staged_facts():
            stage_binding(
                doc,
                local.pk,
                ids["lo_event"],
                at,
                law_class="MEMBERS_RESOLUTION",
                owner="MEMBERS",
                resolution_effect="STANDING",
                resolution_reference="fixture:standing-decision",
            )
            self.assertEqual(
                law_records.laws_in_force(local.organism_id)[0]["display_class"],
                "Members' Resolution",
            )
        with self.assertRaises(IntegrityError) as one_off, transaction.atomic():
            stage_binding(
                doc,
                local.pk,
                ids["lo_event"],
                at,
                law_class="MEMBERS_RESOLUTION",
                owner="MEMBERS",
                resolution_effect="ONE_OFF",
                resolution_reference="fixture:one-off-decision",
            )
        self.assertEqual(
            one_off.exception.__cause__.diag.constraint_name, "l_resolution_effect_kind"
        )
        with staged_facts():
            stage_binding(
                doc,
                local.pk,
                ids["lo_event"],
                at,
                law_class="ARTICLE",
                owner="MEMBERS",
                resolution_effect="AMENDS_ARTICLES",
                resolution_reference="fixture:amending-resolution",
            )
            self.assertEqual(
                [r["class_name"] for r in law_records.laws_in_force(local.organism_id)],
                ["ARTICLE"],
            )
        article = dict(
            scope="PLATFORM",
            law_class="ARTICLE",
            owner="MEMBERS",
            route="FOUNDER_STEWARD",
        )
        with (
            self.assertRaisesMessage(IntegrityError, "Only the root LAW route"),
            transaction.atomic(),
        ):
            stage_binding(doc, local.pk, ids["lo_event"], at, **article)
        with staged_facts():
            key = stage_binding(doc, root.pk, root_event.pk, at, **article)
            inherited = law_records.laws_in_force(local.organism_id, at=at)
            self.assertEqual(
                [(r["binding"], r["inherited"], r["owner"]) for r in inherited],
                [(key, True, "MEMBERS")],
                "An ordinary LO must see the exact platform binding.",
            )
            stage_binding(doc, local.pk, ids["lo_event"], at)
            with self.assertRaises(IntegrityError) as shadow, transaction.atomic():
                check_now("l_no_platform_shadow")
            self.assertEqual(
                shadow.exception.__cause__.diag.constraint_name,
                "l_no_platform_shadow",
                "A local version must never shadow inherited law.",
            )
        with staged_facts():
            stage_binding(doc, local.pk, ids["lo_event"], at)
            stage_binding(doc, root.pk, root_event.pk, at, **article)
            with self.assertRaises(IntegrityError) as refused, transaction.atomic():
                check_now("l_no_platform_shadow")
            self.assertEqual(refused.exception.__cause__.diag.constraint_name, "l_no_platform_shadow")
        with staged_facts():
            marker = stage_binding(
                doc,
                local.pk,
                ids["lo_event"],
                at,
                law_class="ARTICLE",
                owner="MEMBERS",
                reserved=True,
            )
            with (
                self.assertRaisesMessage(IntegrityError, "lower class"),
                transaction.atomic(),
            ):
                stage_binding(
                    doc,
                    local.pk,
                    root_event.pk,
                    support.database_now(),
                    predecessor=marker,
                )
            reservation = query_one(
                "SELECT public.l_reservation(%s,%s,%s)", [local.pk, at, at]
            )
            self.assertEqual(
                reservation,
                marker,
                "The reservation must cite the exact Article adoption.",
            )
            self.assertEqual(
                query_one(
                    "SELECT public.l_class_owner('BYLAW',%s,NULL)",
                    [reservation is not None],
                ),
                "MEMBERS",
                "A declared Bylaw reservation applies on the free tier.",
            )
        self.assertEqual(
            query_one("SELECT public.l_class_owner('BYLAW',false,NULL)"), "GOVERNANCE"
        )
        self.assertEqual(
            query_one("SELECT public.l_class_owner('PROCEDURE',false,NULL)"),
            "LO_COORDINATOR",
        )
        for name, rank in (
            ("ARTICLE", 1),
            ("MEMBERS_RESOLUTION", 2),
            ("BYLAW", 3),
            ("POLICY", 4),
            ("PROCEDURE", 5),
            ("TERMS_OF_REFERENCE", None),
        ):
            self.assertEqual(query_one("SELECT public.l_law_rank(%s)", [name]), rank)
        areas = '{"claims":{},"expenses":{"POLICY":"ADOPT","PROCEDURE":"PROPOSE"}}'
        for area, name, right, owner in (
            ("claims", "POLICY", "PROPOSE", "GOVERNANCE"),
            ("claims", "PROCEDURE", "ADOPT", "CIRCLE"),
            ("expenses", "POLICY", "ADOPT", "CIRCLE"),
            ("expenses", "PROCEDURE", "PROPOSE", "GOVERNANCE"),
        ):
            observed = query_one(
                "SELECT public.l_tor_right(%s::jsonb,%s,%s)", [areas, area, name]
            )
            self.assertEqual(observed, right)
            self.assertEqual(
                query_one("SELECT public.l_class_owner(%s,false,%s)", [name, observed]),
                owner,
            )
        self.assertIsNone(
            query_one(
                "SELECT public.l_tor_right(%s::jsonb,'unknown','POLICY')", [areas]
            )
        )
        self.assertFalse(
            query_one(
                "SELECT public.l_subscriber_capability('CIRCLE_DELEGATION',%s,%s)",
                [local.organism_id, at],
            ),
            "An unregistered SE capability may not grant delegation.",
        )
