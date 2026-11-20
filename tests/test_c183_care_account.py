"""CARE's installed identity, its migration edge and the public account routes."""

from io import StringIO
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core.models import Identity, LearnerRegistration, PlatformTermsVersion
from core.session import (
    SESSION_ACCESS_EPOCH_KEY,
    SESSION_IDENTITY_KEY,
    SESSION_STARTED_AT_KEY,
)
from src.intevia.services.clock import database_now
from src.intevia.services.identity_service import (
    CredentialService,
    CredentialServiceRefused,
    LearnerRegistrationService,
    RegistrationServiceRefused,
)
from tests.s015_0021_support import register_test_learner

PUBLIC_ID = UUID("b79c7d99-67b3-4dd0-8ab7-ee1328d1e183")
RESERVATION = (
    "This name belongs to CARE and is reserved by INTEVIA. "
    "Nothing was registered or changed. Choose another name and try again. "
    "If you think this is wrong, contact INTEVIA support."
)
COLLISION = (
    "CARE's account could not be installed. An existing account already holds the "
    "reserved {kind} in {table}. No account was created, renamed or adopted. Ask the database "
    "operator to resolve the name by hand and then run the migration again."
)
# Disposable input shared with the existing governed registration fixture.
FORM_INPUT = "governed-test-password"


def care():
    return Identity.objects.select_related("credential").get(identity_id=PUBLIC_ID)


def query(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def resolution(identity):
    return query(
        """
        SELECT canonical_username, display_name, credential_link
        FROM public.core_identityresolution WHERE identity_id = %s
    """,
        [identity.pk],
    )


def account_counts():
    return query("""
        SELECT (SELECT count(*) FROM public.auth_user), (SELECT count(*) FROM public.core_identity),
               (SELECT count(*) FROM public.core_identityresolution), (SELECT count(*) FROM core_learnerregistration)
    """)[0]


class CareMigrationTests(TestCase):
    ISOLATION_FULL_CAPTURE = True

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.edge = self.executor.loader.get_migration("core", "0113_care_system_account")
        self.parent = self.executor.loader.project_state(self.edge.dependencies)

    def test_account_has_only_its_reserved_identity_and_unusable_credential(self):
        ident = care()
        user = ident.credential
        self.assertEqual(
            (ident.display_name, ident.canonical_username, ident.access_state, ident.access_epoch),
            ("CARE", "care", "deactivated", 0),
            "The fixed public identity must have CARE's reserved names, deactivated access and initial epoch.",
        )
        self.assertEqual(
            (
                user.username,
                user.is_active,
                user.is_staff,
                user.is_superuser,
                user.email,
                user.first_name,
                user.last_name,
            ),
            ("care", True, False, False, "", "", ""),
            "The sign-in record must carry only the reserved username and ordinary enabled flag.",
        )
        self.assertEqual(
            (ident.created_at, ident.updated_at, ident.deactivated_at),
            (user.date_joined,) * 3,
            "The account and its access state must share one installation time.",
        )
        self.assertEqual(
            (ident.activated_at, ident.restricted_at, user.last_login, ident.colour_mode),
            (None, None, None, "device"),
            "The new account must have no activation, restriction or sign-in and must follow the device.",
        )
        self.assertFalse(user.has_usable_password(), "CARE must have an unusable password.")
        self.assertFalse(
            user.groups.exists() or user.user_permissions.exists(),
            "No group or permission may be granted to CARE.",
        )
        self.assertEqual(
            resolution(ident),
            [("care", "CARE", user.pk)],
            "The name record must resolve to CARE and its own credential.",
        )
        self.assertFalse(
            LearnerRegistration.objects.filter(identity=ident).exists(),
            "A system account must have no learner registration.",
        )

    def test_occupied_username_refuses_before_any_write(self):
        self.check_name_collisions(
            (
                ("person", "username", "auth_user"),
                ("credential", "username", "auth_user"),
                ("identity", "username", "core_identity"),
                ("resolution", "username", "core_identityresolution"),
            )
        )

    def test_occupied_display_name_refuses_before_any_write(self):
        self.check_name_collisions(
            (
                ("identity display", "display name", "core_identity"),
                ("resolution display", "display name", "core_identityresolution"),
            )
        )

    def check_name_collisions(self, cases):
        self.executor.unapply_migration(self.parent, self.edge)
        for namespace, kind, table in cases:
            with self.subTest(namespace=namespace), transaction.atomic():
                # Isolate each location before deferred checks. Roll back each fixture.
                user = User.objects.create_user("CaRe" if namespace in {"person", "credential"} else "human")
                human = uuid4()
                canonical = {"person": "care", "identity": "CaRe"}.get(namespace, "human")
                name = {"person": "care", "resolution": "cArE"}.get(namespace, "human")
                ident_pk = query(
                    """
                    INSERT INTO public.core_identity
                    (identity_id,credential_id,display_name,canonical_username,access_state,access_epoch,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,'active',0,public.intevia_clock_statement(),
                            public.intevia_clock_statement()) RETURNING id
                """,
                    [
                        human,
                        user.pk,
                        " ＣＡＲＥ " if namespace == "identity display" else "Fixture person",
                        canonical,
                    ],
                )[0][0]
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO public.core_identityresolution
                        (identity_id, credential_link, display_name, canonical_username)
                        VALUES (%s,%s,%s,%s)
                    """,
                        [
                            ident_pk,
                            user.pk,
                            " ＣａＲｅ " if namespace == "resolution display" else "Fixture person",
                            name,
                        ],
                    )
                before = account_counts()
                writes = []

                def observe(execute, sql, params, many, context, writes=writes):
                    if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "ALTER")):
                        writes.append(sql.split()[0])
                    return execute(sql, params, many, context)

                with (
                    connection.execute_wrapper(observe),
                    self.assertRaises(
                        RuntimeError, msg="An occupied name must stop the upgrade before any write."
                    ) as refusal,
                ):
                    self.executor.apply_migration(self.parent, self.edge)
                self.assertEqual(
                    str(refusal.exception),
                    COLLISION.format(kind=kind, table=table),
                    "The refusal must tell the operator to resolve the occupied name by hand.",
                )
                self.assertEqual(
                    (writes, account_counts()),
                    ([], before),
                    "A collision must leave every record untouched and issue no write.",
                )
                self.assertFalse(
                    Identity.objects.filter(identity_id=PUBLIC_ID).exists(),
                    "An occupied name must never be adopted as the CARE identity.",
                )
                transaction.set_rollback(True)

    def test_empty_reverse_removes_only_the_triplet_and_reinstalls(self):
        old = care()
        self.executor.unapply_migration(self.parent, self.edge)
        self.assertFalse(
            Identity.objects.filter(identity_id=PUBLIC_ID).exists(),
            "Reversing the empty migration must remove CARE's identity.",
        )
        self.assertFalse(
            User.objects.filter(pk=old.credential_id).exists(),
            "Reversing the empty migration must remove CARE's linked credential.",
        )
        self.assertEqual(resolution(old), [], "Reversing must remove CARE's name record.")
        self.assertEqual(
            query(
                "SELECT identity_id FROM public.core_identityresolutionsevered WHERE identity_id=%s", [old.pk]
            ),
            [],
            "Removing CARE must leave no severance record.",
        )
        self.assertEqual(
            query("""SELECT tgenabled FROM pg_catalog.pg_trigger
                                  WHERE tgrelid='public.core_identityresolution'::regclass
                                  AND tgname='s015_0022_resolution_severance'"""),
            [("O",)],
            "The severance recorder must be enabled again at the previous head.",
        )
        self.executor.apply_migration(self.parent, self.edge)
        restored = care()
        self.assertEqual(
            resolution(restored),
            [("care", "CARE", restored.credential_id)],
            "Reinstalling must restore the name and credential under the same public identity.",
        )

    def test_reverse_refuses_a_reference_before_pausing_the_recorder(self):
        ident = care()
        group = Group.objects.create(name="migration reference witness")
        ident.credential.groups.add(group)
        ddl = []

        def observe(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith(("ALTER", "DELETE")):
                ddl.append(sql.split()[0])
            return execute(sql, params, many, context)

        with (
            connection.execute_wrapper(observe),
            self.assertRaises(
                RuntimeError, msg="A dependent record must stop the reverse in its preflight."
            ) as refusal,
        ):
            self.executor.unapply_migration(self.parent, self.edge)
        self.assertIn(
            "Another record refers to it.",
            str(refusal.exception),
            "The refusal must identify the outstanding reference.",
        )
        self.assertEqual(ddl, [], "The preflight must refuse before any trigger change or delete.")
        self.assertTrue(
            care().credential.groups.filter(pk=group.pk).exists(),
            "A refused reverse must keep the dependent record intact.",
        )


class CareReseedTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def seed_signature(self):
        return query(
            """
            SELECT i.identity_id, u.username, i.canonical_username, i.display_name,
                   r.canonical_username, r.display_name, i.access_state, i.access_epoch,
                   u.password = '!', u.is_active, u.is_staff, u.is_superuser,
                   i.colour_mode, i.activated_at IS NULL AND i.restricted_at IS NULL,
                   u.last_login IS NULL AND u.first_name = '' AND u.last_name = '' AND u.email = '',
                   u.date_joined = i.created_at AND i.created_at = i.updated_at
                     AND i.updated_at = i.deactivated_at
            FROM public.core_identity i
            JOIN public.auth_user u ON u.id = i.credential_id
            JOIN public.core_identityresolution r ON r.identity_id = i.id AND r.credential_link = u.id
            WHERE i.identity_id = %s
            """,
            [PUBLIC_ID],
        )

    def test_flush_restores_one_matching_care_triplet(self):
        # A disposable row distinguishes a real reset from a surviving initial seed.
        before = self.seed_signature()
        User.objects.create_user("reset-witness")
        call_command("flush", interactive=False, verbosity=0, reset_sequences=False)
        rows = self.seed_signature()
        self.assertEqual(
            rows,
            [
                (
                    PUBLIC_ID,
                    "care",
                    "care",
                    "CARE",
                    "care",
                    "CARE",
                    "deactivated",
                    0,
                    True,
                    True,
                    False,
                    False,
                    "device",
                    True,
                    True,
                    True,
                )
            ],
            "A flush must restore one linked CARE account with its reserved names, fixed unusable marker and original access state.",
        )
        self.assertEqual(rows, before, "The restored account must match the installed account.")
        self.assertEqual(
            account_counts(),
            (1, 1, 1, 0),
            "Only CARE's three account records may remain after the fixture has been flushed.",
        )

        # A second reset starts with both kinds of CARE act committed to the real ILC.
        from tests.test_office_record_guards import committed_observation
        from tests.office_record_support import submit_pair

        owner, founder, _ = committed_observation()
        switch = uuid4()
        with transaction.atomic():
            submit_pair(owner, founder, switch, "matched")

        sample_counts = """
            SELECT (SELECT count(*) FROM public.core_careassistancesettingact),
                   (SELECT count(*) FROM public.core_careofficeobservation),
                   (SELECT count(*) FROM public.core_livingorganismevent
                     WHERE action IN ('SET_CARE_ASSISTANCE', 'RECORD_CARE_OFFICE_OBSERVATION'))
        """
        self.assertEqual(query(sample_counts), [(1, 1, 2)],
                         "The second reset must start with both committed acts and their two ILC entries.")
        call_command("flush", interactive=False, verbosity=0, reset_sequences=False)
        self.assertEqual(query(sample_counts), [(0, 0, 0)],
                         "Reset must leave neither sample CARE act nor an ILC entry for either act.")
        self.assertEqual((self.seed_signature(), account_counts()), (rows, (1, 1, 1, 0)),
                         "Resetting a populated record must restore only the same healthy CARE triplet.")


@override_settings(
    ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class CareRouteTests(TestCase):
    def setUp(self):
        super().setUp()
        self.person = register_test_learner(
            username="careful-witness", display_name="Care Team", password=FORM_INPUT
        )
        self.terms = PlatformTermsVersion.objects.get(version_label="FIXTURE-DEV-1")

    def login(self, client, user, url):
        return client.post(url, {"username": user.username, "password": FORM_INPUT})

    def test_product_form_refuses_the_system_identity(self):
        url = reverse("login")
        positive = self.login(self.client, self.person.credential, url)
        self.assertEqual(
            (positive.status_code, positive.get("Location")),
            (302, reverse("shell")),
            "A registered human must be able to sign in through this form.",
        )
        user = care().credential
        # Isolate the state gate from the unusable-password protection.
        user.set_password(FORM_INPUT)
        user.save(update_fields=["password"])
        stranger = Client()
        denied = self.login(stranger, user, url)
        self.assertEqual(
            denied.status_code, 200, "The product form must refuse CARE even with a matching test password."
        )
        self.assertEqual(
            list(denied.context["form"].non_field_errors()),
            ["Unable to sign in with those credentials."],
            "The refused form must give the ordinary sign-in refusal.",
        )
        self.assertNotIn(
            SESSION_KEY, stranger.session, "The refused sign-in must leave no authenticated session."
        )

    def test_bound_session_is_dismissed_for_care(self):
        url = reverse("f0-you")
        for ident, admitted in ((self.person, True), (care(), False)):
            client = Client()
            client.force_login(ident.credential)
            session = client.session
            session[SESSION_IDENTITY_KEY] = str(ident.identity_id)
            session[SESSION_ACCESS_EPOCH_KEY] = ident.access_epoch
            session[SESSION_STARTED_AT_KEY] = int(database_now().timestamp())
            session.save()
            reply = client.get(url)
            if admitted:
                self.assertEqual(
                    reply.status_code, 200, "A correctly bound active human session must reach You."
                )
            else:
                self.assertEqual(
                    (reply.status_code, reply.get("Location")),
                    (302, reverse("login")),
                    "A correctly bound CARE session must return to sign-in.",
                )
                self.assertNotIn(
                    SESSION_KEY, client.session, "The middleware must clear CARE's authenticated session."
                )

    def test_admin_signin_refuses_care_by_its_public_identity(self):
        url = reverse("admin:login")
        user = self.person.credential
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.assertEqual(
            self.login(self.client, user, url).status_code,
            302,
            "A human staff credential must pass the administrator sign-in form.",
        )
        user = care().credential
        # Both fixture-only changes roll back. The separate seed test checks the real flags.
        user.set_password(FORM_INPUT)
        user.is_staff = True
        user.save(update_fields=["password", "is_staff"])
        client = Client()
        denied = self.login(client, user, url)
        self.assertEqual(
            denied.status_code, 200, "The administrator form must still refuse the CARE identity."
        )
        self.assertNotIn(
            SESSION_KEY, client.session, "Administrator sign-in must leave CARE without a session."
        )

    def test_registration_reserves_username_and_display_name(self):
        for username, display in (
            (" ＣaRe ", "Human"),
            ("future-trustee", "  caRE  "),
            ("future-trustee", "ＣＡＲＥ"),
        ):
            with self.subTest(username=username, display=display):
                before = account_counts()
                with self.assertRaises(
                    RegistrationServiceRefused, msg="Registration must refuse CARE's name."
                ) as refusal:
                    LearnerRegistrationService.register(
                        username=username,
                        display_name=display,
                        password=FORM_INPUT,
                        terms_accepted=True,
                        terms_version_uuid=self.terms.terms_version_uuid,
                        correlation_id=uuid4(),
                    )
                self.assertEqual(
                    refusal.exception.message,
                    RESERVATION,
                    "Self-registration must explain that this name belongs to CARE.",
                )
                self.assertEqual(
                    account_counts(), before, "A reserved name must leave registration untouched."
                )

    def test_supported_registration_shows_the_same_refusal(self):
        with (
            patch("builtins.input", return_value="ACCEPT"),
            patch("getpass.getpass", return_value=FORM_INPUT),
            self.assertRaises(
                CommandError, msg="Supported registration must refuse the reserved display name."
            ) as refusal,
        ):
            call_command(
                "provision_learner",
                username="supported-person",
                display_name="  cAre ",
                terms_version=self.terms.version_label,
                stdout=StringIO(),
            )
        self.assertEqual(
            str(refusal.exception),
            RESERVATION,
            "The operator must see the same reserved-name refusal as a person registering.",
        )
        self.assertFalse(
            User.objects.filter(username="supported-person").exists(),
            "The refused supported registration must leave no credential.",
        )

    def test_development_provisioning_reserves_both_names(self):
        for username, display in ((" CaRe ", "Human"), ("dev-person", "  cAre ")):
            before = account_counts()
            with self.assertRaises(
                CredentialServiceRefused, msg="Development provisioning must refuse the reserved name."
            ) as refusal:
                CredentialService.provision(
                    username=username,
                    display_name=display,
                    password=FORM_INPUT,
                    human_classification="HUMAN DEVELOPMENT IDENTITY",
                    authority_reference="fixture:authority",
                    evidence_reference="fixture:evidence",
                    correlation_id=uuid4(),
                    technical_executor="fixture:operator",
                )
            self.assertEqual(
                refusal.exception.message,
                RESERVATION,
                "Development provisioning must explain why the name is reserved.",
            )
            self.assertEqual(
                account_counts(), before, "No development credential or identity may be left behind."
            )

    def test_fixed_username_route_explains_the_reservation(self):
        for name, code in ((" ＣARE ", "NAME_RESERVED"), ("another-human", "USERNAME_CHANGE_UNAVAILABLE")):
            with self.assertRaises(
                CredentialServiceRefused, msg="Changing an existing username must be refused."
            ) as refusal:
                CredentialService.update_username(
                    identity_id=self.person.identity_id,
                    username=name,
                    requesting_actor=self.person,
                    authority_reference="fixture:authority",
                    evidence_reference="fixture:evidence",
                    correlation_id=uuid4(),
                    technical_executor="fixture:holder",
                )
            self.assertEqual(
                refusal.exception.code,
                code,
                "A reserved name must be distinguished from an ordinary fixed username.",
            )
            if code == "NAME_RESERVED":
                self.assertEqual(
                    refusal.exception.message, RESERVATION, "The rename route must explain the reservation."
                )
        self.assertEqual(
            User.objects.get(pk=self.person.credential_id).username,
            "careful-witness",
            "The existing username must survive both refused changes.",
        )

    def staff_session(self):
        user = self.person.credential
        user.is_staff = user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
        self.login(self.client, user, reverse("login"))

    def test_admin_add_reserves_the_username(self):
        self.staff_session()
        url = reverse("admin:auth_user_add")
        admitted = self.client.post(url, {"username": "care-team-editor", "usable_password": "false"})
        self.assertEqual(
            admitted.status_code, 302, "The same administrator must be able to add a distinct username."
        )
        response = self.client.post(url, {"username": "cArE", "usable_password": "false"})
        self.assertEqual(
            list(response.context["adminform"].form.errors.get("username", [])),
            [RESERVATION],
            "Adding a sign-in account in administration must explain the reserved name.",
        )
        self.assertFalse(
            User.objects.filter(username="cArE").exists(),
            "The refused addition must leave no sign-in record.",
        )

    def test_admin_edit_reserves_the_username(self):
        self.staff_session()
        user = self.person.credential
        response = self.client.post(reverse("admin:auth_user_change", args=[user.pk]), {"username": "cArE"})
        self.assertEqual(
            list(response.context["adminform"].form.errors.get("username", [])),
            [RESERVATION],
            "Editing a human sign-in account in administration must explain the reserved name.",
        )
        user.refresh_from_db()
        self.assertEqual(
            user.username, "careful-witness", "The administrator's refused edit must keep the original name."
        )
        form = (
            self.client.get(reverse("admin:auth_user_change", args=[care().credential_id]))
            .context["adminform"]
            .form
        )
        self.assertNotIn(
            "username", form.fields, "The CARE record must keep its fixed username in administration."
        )

    def test_operator_account_command_reserves_the_username(self):
        with self.assertRaises(
            CommandError, msg="Administrator setup must refuse the reserved name."
        ) as refusal:
            call_command("createsuperuser", interactive=False, username="CaRe", email="", verbosity=0)
        self.assertEqual(
            str(refusal.exception),
            RESERVATION,
            "The operator must see why the administrator name is reserved.",
        )
        self.assertFalse(
            User.objects.filter(username="CaRe").exists(),
            "Administrator setup must leave no mixed-case CARE account.",
        )
