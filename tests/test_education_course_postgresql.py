from copy import deepcopy
from datetime import datetime, timezone
import re
from threading import Barrier, Lock, Thread
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections
from django.test import TransactionTestCase

from core.models import Course, CourseVersion, Identity
from src.intevia.services.education_course_authority import EducationCourseAuthority
from src.intevia.services.education_course_contract import (
    AppendCourseVersionCommand,
    AppendCourseVersionReceipt,
    CreateCourseCommand,
    CreateCourseReceipt,
    EducationCourseAuthorityResponse,
)
from src.intevia.services.education_course_read_service import (
    CourseCurrentDefinitionDTO,
    EducationCourseReadService,
)
from src.intevia.services.education_course_service import (
    EducationCourseIdentityConflict,
    EducationCourseNotFound,
    EducationCourseService,
    EducationCourseStaleHeadConflict,
)


SECONDARY_ALIAS = "s014_secondary"
if SECONDARY_ALIAS not in connections.databases:
    secondary_database = deepcopy(connections.databases["default"])
    secondary_database["NAME"] = f"{secondary_database['NAME']}_s014_secondary"
    secondary_database["TEST"] = dict(secondary_database.get("TEST", {}))
    secondary_database["TEST"]["NAME"] = None
    connections.databases[SECONDARY_ALIAS] = secondary_database


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql", "PostgreSQL S014 qualification guardian"
)


NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)


class _Allow:
    def evaluate_course_definition(self, request):
        return EducationCourseAuthorityResponse(
            request.database_alias, request.actor_pk, request.actor_identity_id,
            request.actor_access_epoch, request.action, request.target_fingerprint,
            request.request_reference, request.idempotency_key, request.evaluated_at,
            "authority:s014:postgresql",
        )


@POSTGRESQL_ONLY
class EducationCoursePostgreSQLCatalogueTests(TransactionTestCase):
    def assert_exact_catalogue(self, constraints, indexes):
        fixed_constraints = {
            "core_course_pkey", "core_courseversion_pkey",
            "s014_course_id_uniq", "s014_course_version_number_uniq",
            "s014_course_actor_action_idem_uniq",
            "s014_course_version_lineage_uniq", "s014_course_version_positive",
            "s014_course_action_valid", "s014_course_create_shape",
            "s014_course_append_shape", "s014_course_actor_epoch_nonnegative",
            "core_courseversion_actor_access_epoch_check",
            "core_courseversion_version_number_check",
        }
        foreign_key_patterns = (
            r"^core_course_created_by_id_[0-9a-f]+_fk_core_identity_id$",
            r"^core_course_current_version_id_[0-9a-f]+_fk_core_cour$",
            r"^core_courseversion_actor_id_[0-9a-f]+_fk_core_identity_id$",
            r"^core_courseversion_course_id_[0-9a-f]+_fk_core_course_id$",
            r"^core_courseversion_predecessor_id_[0-9a-f]+_fk_core_cour$",
        )
        generated = set()
        for pattern in foreign_key_patterns:
            matches = {name for name in constraints if re.fullmatch(pattern, name)}
            self.assertEqual(len(matches), 1, pattern)
            generated.update(matches)
        self.assertEqual(constraints, fixed_constraints | generated)
        self.assertEqual(indexes, {
            "core_course_pkey", "core_courseversion_pkey",
            "s014_course_id_uniq", "s014_course_current_version_uniq",
            "s014_course_version_number_uniq",
            "s014_course_actor_action_idem_uniq",
            "s014_course_initial_version_uniq", "s014_course_predecessor_uniq",
            "s014_course_version_lineage_uniq",
        })

    def test_exact_named_constraints_and_no_foreign_key_indexes(self):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT conname FROM pg_constraint
                WHERE conrelid IN ('core_course'::regclass, 'core_courseversion'::regclass)
            """)
            constraints = {row[0] for row in cursor.fetchall()}
            cursor.execute("""
                SELECT indexname FROM pg_indexes
                WHERE schemaname='public' AND tablename IN ('core_course','core_courseversion')
            """)
            indexes = {row[0] for row in cursor.fetchall()}
        self.assert_exact_catalogue(constraints, indexes)
        for prefix in (
            "core_course_created_by_id_", "core_course_current_version_id_",
            "core_courseversion_actor_id_", "core_courseversion_course_id_",
            "core_courseversion_predecessor_id_",
        ):
            self.assertFalse(any(name.startswith(prefix) for name in indexes))
        self.assertFalse(any(
            name.startswith("core_course_current_version_id_") for name in indexes
        ))
        with self.assertRaises(AssertionError):
            self.assert_exact_catalogue(
                constraints, indexes | {"s014_synthetic_undeclared_index"}
            )


@POSTGRESQL_ONLY
class EducationCoursePostgreSQLMultiDatabaseTests(TransactionTestCase):
    databases = {"default", SECONDARY_ALIAS}
    reset_sequences = True

    def setUp(self):
        self.default_user = User.objects.db_manager("default").create_user(
            username="course-default-collision"
        )
        self.default_identity = Identity.objects.using("default").create(
            credential=self.default_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        User.objects.db_manager(SECONDARY_ALIAS).create_user(
            username="course-secondary-spacer"
        )
        self.secondary_user = User.objects.db_manager(SECONDARY_ALIAS).create_user(
            username="course-secondary"
        )
        self.secondary_identity = Identity.objects.using(SECONDARY_ALIAS).create(
            credential=self.secondary_user,
            access_state=Identity.AccessState.ACTIVE,
        )

    def test_non_default_create_append_replay_immutable_and_same_pk_collision(self):
        default_course_id = uuid4()
        default_course = Course.objects.using("default").create(
            course_id=default_course_id,
            created_by=self.default_identity,
            created_at=NOW,
        )
        default_version = CourseVersion.objects.using("default").create(
            course=default_course, version_number=1, predecessor=None, action="CREATE",
            course_name="Default", course_description="Default description",
            course_learning_objectives="Default objectives",
            definition_basis_reference="default-basis", actor=self.default_identity,
            actor_access_epoch=0, authority_reference="default-authority",
            authority_decision_reference="s014d1:" + "a" * 64,
            authority_evaluated_at=NOW, request_reference="default-request",
            idempotency_key="default-idem", payload_fingerprint="b" * 64,
            occurred_at=NOW, lineage_reference="s014l1:" + "c" * 64,
        )
        default_course._advance_current_version(default_version)

        authority = EducationCourseAuthority(
            provider=_Allow(), database_alias=SECONDARY_ALIAS
        )
        service = EducationCourseService(
            authority=authority, clock=lambda: NOW, database_alias=SECONDARY_ALIAS
        )
        secondary_course_id = uuid4()
        create_command = CreateCourseCommand(
            self.secondary_user, secondary_course_id, "Secondary", "Description",
            "Objectives", "basis", "secondary-create", "secondary-create-idem", NOW,
        )
        created = service.create(create_command)
        replayed = service.create(create_command)
        appended = service.append_version(AppendCourseVersionCommand(
            self.secondary_user, secondary_course_id, created.course_version_pk,
            created.lineage_reference, "Secondary v2", "Description v2",
            "Objectives v2", "basis-v2", "secondary-append",
            "secondary-append-idem", NOW.replace(hour=13),
        ))
        self.assertTrue(replayed.replayed)
        self.assertEqual(appended.version_number, 2)
        secondary_course = Course.objects.using(SECONDARY_ALIAS).get(
            course_id=secondary_course_id
        )
        self.assertEqual(secondary_course.pk, default_course.pk)
        self.assertEqual(secondary_course.current_version_id, appended.course_version_pk)
        secondary_course.course_id = default_course_id
        with self.assertRaises(ValidationError):
            secondary_course.save()
        secondary_course.refresh_from_db(using=SECONDARY_ALIAS)
        self.assertEqual(secondary_course.course_id, secondary_course_id)
        default_course.refresh_from_db(using="default")
        self.assertEqual(default_course.current_version_id, default_version.pk)


@POSTGRESQL_ONLY
class EducationCoursePostgreSQLRaceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.users = [
            User.objects.create_user(username=f"course-race-{number}")
            for number in range(2)
        ]
        for user in self.users:
            Identity.objects.create(
                credential=user, access_state=Identity.AccessState.ACTIVE
            )

    def test_concurrent_same_course_identity_has_one_coherent_winner(self):
        course_id = uuid4()
        barrier = Barrier(2)
        result_lock = Lock()
        results = []

        def create(user_pk, suffix):
            close_old_connections()
            try:
                user = User.objects.get(pk=user_pk)
                service = EducationCourseService(
                    authority=EducationCourseAuthority(provider=_Allow()),
                    clock=lambda: NOW,
                )
                command = CreateCourseCommand(
                    credential=user,
                    course_id=course_id,
                    course_name=f"Course {suffix}",
                    course_description=f"Description {suffix}",
                    course_learning_objectives=f"Objectives {suffix}",
                    definition_basis_reference=f"basis-{suffix}",
                    request_reference=f"create-request-{suffix}",
                    idempotency_key=f"create-idem-{suffix}",
                    occurred_at=NOW,
                )
                barrier.wait(timeout=5)
                result = service.create(command)
            except Exception as error:
                result = error
            finally:
                close_old_connections()
            with result_lock:
                results.append(result)

        threads = [
            Thread(target=create, args=(user.pk, number))
            for number, user in enumerate(self.users)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(
            sum(isinstance(result, CreateCourseReceipt) for result in results), 1
        )
        self.assertEqual(
            sum(isinstance(result, EducationCourseIdentityConflict) for result in results),
            1,
            [type(result).__name__ for result in results],
        )
        self.assertEqual(Course.objects.filter(course_id=course_id).count(), 1)
        self.assertEqual(CourseVersion.objects.filter(course__course_id=course_id).count(), 1)

    def test_same_actor_create_race_resolves_to_exact_replay(self):
        user = self.users[0]
        command = CreateCourseCommand(
            user, uuid4(), "Course", "Description", "Objectives", "basis",
            "same-actor-request", "same-actor-idem", NOW,
        )
        barrier = Barrier(2)
        result_lock = Lock()
        results = []

        def create():
            close_old_connections()
            try:
                local_user = User.objects.get(pk=user.pk)
                service = EducationCourseService(
                    authority=EducationCourseAuthority(provider=_Allow()),
                    clock=lambda: NOW,
                )
                barrier.wait(timeout=5)
                result = service.create(
                    CreateCourseCommand(local_user, *command.__getstate__()[1:])
                )
            except Exception as error:
                result = error
            finally:
                close_old_connections()
            with result_lock:
                results.append(result)

        threads = [Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sum(isinstance(value, CreateCourseReceipt) for value in results), 2)
        self.assertEqual(sorted(value.replayed for value in results), [False, True])
        self.assertEqual(Course.objects.filter(course_id=command.course_id).count(), 1)
        self.assertEqual(CourseVersion.objects.filter(course__course_id=command.course_id).count(), 1)

    def test_competing_successors_and_read_append_are_coherent(self):
        creator = self.users[0]
        service = EducationCourseService(
            authority=EducationCourseAuthority(provider=_Allow()), clock=lambda: NOW
        )
        course_id = uuid4()
        first = service.create(CreateCourseCommand(
            creator, course_id, "Course", "Description", "Objectives", "basis",
            "create", "create-idem", NOW,
        ))
        barrier = Barrier(2)
        result_lock = Lock()
        results = []

        def append(user_pk, suffix):
            close_old_connections()
            try:
                local_user = User.objects.get(pk=user_pk)
                local_service = EducationCourseService(
                    authority=EducationCourseAuthority(provider=_Allow()),
                    clock=lambda: NOW,
                )
                command = AppendCourseVersionCommand(
                    local_user, course_id, first.course_version_pk,
                    first.lineage_reference, f"Course {suffix}",
                    f"Description {suffix}", f"Objectives {suffix}",
                    f"basis-{suffix}", f"append-{suffix}", f"append-idem-{suffix}",
                    NOW.replace(hour=13),
                )
                barrier.wait(timeout=5)
                result = local_service.append_version(command)
            except Exception as error:
                result = error
            finally:
                close_old_connections()
            with result_lock:
                results.append(result)

        threads = [
            Thread(target=append, args=(user.pk, number))
            for number, user in enumerate(self.users)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sum(isinstance(value, AppendCourseVersionReceipt) for value in results), 1)
        self.assertEqual(sum(isinstance(value, EducationCourseStaleHeadConflict) for value in results), 1)
        current = EducationCourseReadService(clock=lambda: NOW).get_current_course(
            credential=creator, course_id=course_id
        )
        self.assertIsInstance(current, CourseCurrentDefinitionDTO)
        self.assertEqual(current.version_number, 2)
        self.assertEqual(CourseVersion.objects.filter(course__course_id=course_id).count(), 2)

    def test_every_allowed_constraint_enters_only_its_recovery_route(self):
        actor = Identity.objects.get(credential=self.users[0])
        service = EducationCourseService(
            authority=EducationCourseAuthority(provider=_Allow()), clock=lambda: NOW
        )

        def error_for(name):
            error = IntegrityError("postgresql uniqueness loss")
            cause = Exception("driver error")
            cause.diag = SimpleNamespace(constraint_name=name)
            error.__cause__ = cause
            return error

        create_values = {"idempotency_key": "key", "course_id": uuid4()}
        for name in (
            "s014_course_actor_action_idem_uniq", "s014_course_id_uniq",
            "s014_course_version_number_uniq", "s014_course_initial_version_uniq",
        ):
            with self.subTest(command="CREATE", constraint=name), patch.object(
                service, "_discover_course_ids", return_value=[]
            ) as discover, patch.object(
                service, "_lock_roots_and_versions", return_value=({}, {})
            ), patch.object(service, "_current_winner", return_value=None), self.assertRaises(
                IntegrityError
            ):
                service._recover_create_race(
                    exc=error_for(name), actor=actor, values=create_values,
                    fingerprint="a" * 64,
                )
            discover.assert_called_once()
        append_values = {"idempotency_key": "key", "course_id": uuid4()}
        for name in (
            "s014_course_actor_action_idem_uniq",
            "s014_course_version_number_uniq", "s014_course_predecessor_uniq",
        ):
            with self.subTest(command="APPEND", constraint=name), patch.object(
                service, "_discover_course_ids", return_value=[]
            ) as discover, patch.object(
                service, "_lock_roots_and_versions", return_value=({}, {})
            ), patch.object(service, "_current_winner", return_value=None), self.assertRaises(
                EducationCourseNotFound
            ):
                service._recover_append_race(
                    exc=error_for(name), actor=actor, values=append_values,
                    fingerprint="a" * 64,
                )
            discover.assert_called_once()

    def test_disallowed_and_unknown_constraints_propagate_without_lookup(self):
        user = self.users[0]
        actor = Identity.objects.get(credential=user)
        service = EducationCourseService(
            authority=EducationCourseAuthority(provider=_Allow()),
            clock=lambda: NOW,
        )
        for constraint_name in (
            "s014_course_version_lineage_uniq",
            "s014_unknown_constraint",
            None,
        ):
            for recovery in (
                service._recover_create_race, service._recover_append_race
            ):
                with self.subTest(
                    constraint_name=constraint_name, recovery=recovery
                ):
                    error = IntegrityError("postgresql uniqueness loss")
                    cause = Exception("driver error")
                    cause.diag = SimpleNamespace(constraint_name=constraint_name)
                    error.__cause__ = cause
                    with patch.object(
                        service,
                        "_discover_course_ids",
                        side_effect=AssertionError("discovery must not run"),
                    ), self.assertRaises(IntegrityError) as raised:
                        recovery(
                            exc=error, actor=actor, values={}, fingerprint="a" * 64
                        )
                    self.assertIs(raised.exception, error)