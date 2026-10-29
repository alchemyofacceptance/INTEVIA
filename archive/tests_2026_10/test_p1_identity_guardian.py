import datetime as dt
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core.models import (
    Identity,
    LearnerRegistration,
    PlatformTermsAcceptance,
    PlatformTermsVersion,
)
from src.intevia.services.identity_service import LearnerRegistrationService
from tests.s015_0021_support import (
    create_test_identity,
    register_test_learner,
    seed_identity,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "P1 identity guardian checks are PostgreSQL-only",
)


@POSTGRESQL_ONLY
class P1IdentityGuardianTests(TransactionTestCase):
    reset_sequences = True

    def test_identity_requires_a_resolution_row_at_commit(self):
        username = f"p1-missing-{uuid4().hex}"
        credential = User.objects.create_user(username=username, password=None)

        with self.assertRaises(IntegrityError) as refused:
            with transaction.atomic():
                Identity.objects.create(
                    credential=credential,
                    display_name="P1 missing resolution",
                    access_state=Identity.AccessState.ACTIVE,
                )

    def test_test_identity_helper_writes_the_resolution_row(self):
        identity = create_test_identity(
            username=f"p1-helper-{uuid4().hex}",
            display_name="P1 helper",
            access_state=Identity.AccessState.ACTIVE,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT display_name, canonical_username, credential_link "
                "FROM public.core_identityresolution WHERE identity_id = %s",
                [identity.pk],
            )
            row = cursor.fetchone()

        self.assertEqual(
            row,
            (
                identity.display_name,
                identity.canonical_username,
                identity.credential_id,
            ),
        )

    def test_seed_identity_writes_the_resolution_in_its_transaction(self):
        with connection.cursor() as cursor:
            identity_id = seed_identity(cursor, f"guardian-{uuid4().hex}")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.display_name, r.canonical_username, r.credential_link
                  FROM public.core_identityresolution r
                  JOIN public.core_identity i ON i.id = r.identity_id
                 WHERE r.identity_id = %s
                   AND r.canonical_username = i.canonical_username
                """,
                [identity_id],
            )
            self.assertIsNotNone(cursor.fetchone())

    def test_identity_username_must_match_its_resolution(self):
        identity = create_test_identity(
            username=f"p1-equality-{uuid4().hex}",
            access_state=Identity.AccessState.ACTIVE,
        )

        with self.assertRaises(IntegrityError) as refused:
            with transaction.atomic():
                Identity.objects.filter(pk=identity.pk).update(
                    canonical_username=f"changed-{uuid4().hex}"
                )

        identity.refresh_from_db()
        self.assertEqual(
            identity.canonical_username,
            identity.credential.username,
        )

    def test_resolution_username_and_credential_link_are_immutable(self):
        identity = create_test_identity(
            username=f"p1-immutable-{uuid4().hex}",
            access_state=Identity.AccessState.ACTIVE,
        )
        replacement = User.objects.create_user(
            username=f"p1-replacement-{uuid4().hex}",
            password=None,
        )

        for column, value in (
            ("canonical_username", "changed"),
            ("credential_link", replacement.pk),
        ):
            with self.subTest(column=column), self.assertRaises(DatabaseError):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(
                        f"UPDATE public.core_identityresolution SET {column} = %s "
                        "WHERE identity_id = %s",
                        [value, identity.pk],
                    )

    def test_identity_guard_triggers_are_deferred_and_registered(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tgname, tgdeferrable, tginitdeferred
                  FROM pg_trigger
                 WHERE tgrelid = 'public.core_identity'::regclass
                   AND tgname IN (
                       's015_0022_guard_identity_resolution',
                       'p1_identity_username_equal'
                   )
                 ORDER BY tgname
                """
            )
            triggers = cursor.fetchall()

        self.assertEqual(
            triggers,
            [
                ("p1_identity_username_equal", True, True),
                ("s015_0022_guard_identity_resolution", True, True),
            ],
        )

    def test_one_registration_per_identity_is_a_database_constraint(self):
        """W-A1.8: the database permits only one registration per identity."""
        identity = register_test_learner(
            username=f"p1-single-registration-{uuid4().hex}",
            password="governed-test-password",
            display_name="Single Registration",
        )
        registration = LearnerRegistration.objects.get(identity=identity)
        duplicate_acceptance_uuid = uuid4()
        duplicate_registration_uuid = uuid4()
        acceptance_count = PlatformTermsAcceptance.objects.filter(
            accepted_by_identity=identity
        ).count()
        acceptance = PlatformTermsAcceptance.objects.get(
            acceptance_uuid=registration.evidence_reference.removeprefix(
                "platform-terms-acceptance:"
            )
        )

        with self.assertRaises(IntegrityError) as refused:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO public.core_registrationreceipt "
                        "DEFAULT VALUES RETURNING id, received_at"
                    )
                    receipt_id, receipt_at = cursor.fetchone()
                subject_reference = (
                    f"learner-registration:{duplicate_registration_uuid}"
                )
                PlatformTermsAcceptance.objects.create(
                    acceptance_uuid=duplicate_acceptance_uuid,
                    terms_version=acceptance.terms_version,
                    acceptance_kind=PlatformTermsAcceptance.Kind.REGISTRATION,
                    subject_reference=subject_reference,
                    accepted_by_identity=identity,
                    accepted_by_access_epoch=identity.access_epoch,
                    accepted_at=receipt_at,
                    received_at=receipt_at,
                    acceptance_commitment=(
                        LearnerRegistrationService._acceptance_commitment(
                            terms_version=acceptance.terms_version,
                            subject_reference=subject_reference,
                            identity=identity,
                            accepted_at=receipt_at,
                        )
                    ),
                )
                duplicate_registration = LearnerRegistration(
                    registration_uuid=duplicate_registration_uuid,
                    identity=identity,
                    actor_access_epoch=registration.actor_access_epoch,
                    entry_mode=registration.entry_mode,
                    enterer_executor=registration.enterer_executor,
                    authority_reference=registration.authority_reference,
                    evidence_reference=(
                        f"platform-terms-acceptance:{duplicate_acceptance_uuid}"
                    ),
                    registration_receipt_id=receipt_id,
                    effective_at=receipt_at,
                    received_at=receipt_at,
                )
                LearnerRegistration.objects.bulk_create(
                    [duplicate_registration]
                )

        self.assertIn(
            "core_learnerregistration_identity_id_key",
            str(refused.exception),
        )
        self.assertEqual(
            LearnerRegistration.objects.filter(identity=identity).count(),
            1,
        )
        self.assertEqual(
            PlatformTermsAcceptance.objects.filter(
                accepted_by_identity=identity
            ).count(),
            acceptance_count,
        )

    def test_registration_guard_checks_receipt_transaction_times_and_latest_terms(self):
        """W-RT7: the SQL guard alone rejects invalid receipt and non-latest terms."""
        seeded_identity = register_test_learner(
            username=f"p1-guard-terms-{uuid4().hex}",
            password="Registration-Governed-Password-1",
            display_name="P1 guard terms seed",
        )
        seeded_registration = LearnerRegistration.objects.get(
            identity=seeded_identity
        )
        seed_acceptance = PlatformTermsAcceptance.objects.get(
            acceptance_uuid=seeded_registration.evidence_reference.removeprefix(
                "platform-terms-acceptance:"
            )
        )
        first_version = seed_acceptance.terms_version

        def create_receipt():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO public.core_registrationreceipt DEFAULT VALUES "
                    "RETURNING id, received_at, transaction_id"
                )
                return cursor.fetchone()

        def direct_registration(
            label,
            terms_version,
            *,
            omit_receipt=False,
            foreign_receipt=False,
            time_mismatch=False,
        ):
            foreign = create_receipt() if foreign_receipt else None
            with transaction.atomic():
                identity = create_test_identity(
                    username=f"p1-guard-{label}-{uuid4().hex}",
                    display_name=f"P1 guard {label}",
                    access_state=Identity.AccessState.ACTIVE,
                )
                receipt_id, receipt_at, _transaction_id = (
                    foreign if foreign is not None else create_receipt()
                )
                registration_uuid = uuid4()
                acceptance_uuid = uuid4()
                subject_reference = f"learner-registration:{registration_uuid}"
                PlatformTermsAcceptance.objects.create(
                    acceptance_uuid=acceptance_uuid,
                    terms_version=terms_version,
                    acceptance_kind=PlatformTermsAcceptance.Kind.REGISTRATION,
                    subject_reference=subject_reference,
                    accepted_by_identity=identity,
                    accepted_by_access_epoch=identity.access_epoch,
                    accepted_at=receipt_at,
                    received_at=receipt_at,
                    acceptance_commitment=(
                        LearnerRegistrationService._acceptance_commitment(
                            terms_version=terms_version,
                            subject_reference=subject_reference,
                            identity=identity,
                            accepted_at=receipt_at,
                        )
                    ),
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO public.core_learnerregistration (
                            registration_uuid,
                            identity_id,
                            actor_access_epoch,
                            entry_mode,
                            enterer_executor,
                            authority_reference,
                            evidence_reference,
                            registration_receipt_id,
                            effective_at,
                            received_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        [
                            registration_uuid,
                            identity.pk,
                            identity.access_epoch,
                            LearnerRegistration.EntryMode.SUPPORTED,
                            "test:registration_fixture",
                            (
                                f"platform-terms-version:"
                                f"{terms_version.terms_version_uuid}#registration"
                            ),
                            f"platform-terms-acceptance:{acceptance_uuid}",
                            None if omit_receipt else receipt_id,
                            (
                                receipt_at + dt.timedelta(microseconds=1)
                                if time_mismatch
                                else receipt_at
                            ),
                            receipt_at,
                        ],
                    )
                    return cursor.fetchone()[0]

        self.assertIsNotNone(direct_registration("valid-base", first_version))
        for label, options in (
            ("missing-receipt", {"omit_receipt": True}),
            ("other-transaction", {"foreign_receipt": True}),
            ("mismatched-coordinate", {"time_mismatch": True}),
        ):
            with self.subTest(reason=label):
                with self.assertRaises(DatabaseError) as refused:
                    direct_registration(label, first_version, **options)
                self.assertIn(
                    "P1 registration requires acceptance",
                    str(refused.exception),
                )
                self.assertIsNotNone(direct_registration(f"{label}-control", first_version))

        second_version = PlatformTermsVersion.objects.create(
            terms_version_uuid=uuid4(),
            version_label="P1-GUARD-2",
            text_commitment="s015r1:" + "0" * 64,
            text_body="P1 guard successor terms two",
            cited_rulings=[],
            published_by_authority_reference="fixture:p1-guard",
            is_fixture=True,
            effective_from=first_version.effective_from,
            supersedes=first_version,
        )
        third_version = PlatformTermsVersion.objects.create(
            terms_version_uuid=uuid4(),
            version_label="P1-GUARD-3",
            text_commitment="s015r1:" + "1" * 64,
            text_body="P1 guard successor terms three",
            cited_rulings=[],
            published_by_authority_reference="fixture:p1-guard",
            is_fixture=True,
            effective_from=first_version.effective_from,
            supersedes=second_version,
        )
        with self.assertRaises(DatabaseError) as outdated:
            direct_registration("not-latest", first_version)
        self.assertIn(
            "P1 registration requires acceptance",
            str(outdated.exception),
        )
        self.assertIsNotNone(direct_registration("latest-control", third_version))
