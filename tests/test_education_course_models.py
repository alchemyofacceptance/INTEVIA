from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.test import TestCase

from core.models import Course, CourseVersion, Identity


class EducationCourseModelTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="course-model")
        self.identity = Identity.objects.create(
            credential=user, access_state=Identity.AccessState.ACTIVE
        )
        self.now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)

    def version(self, course, **values):
        defaults = dict(
            course=course, version_number=1, predecessor=None, action="CREATE",
            course_name="Course", course_description="Description",
            course_learning_objectives="Objectives", definition_basis_reference="basis",
            actor=self.identity, actor_access_epoch=0, authority_reference="authority",
            authority_decision_reference="s014d1:" + "a" * 64,
            authority_evaluated_at=self.now, request_reference="request",
            idempotency_key="idempotency", payload_fingerprint="b" * 64,
            occurred_at=self.now, lineage_reference="s014l1:" + "c" * 64,
        )
        defaults.update(values)
        return CourseVersion.objects.create(**defaults)

    def test_exact_constraint_and_index_catalogue(self):
        self.assertEqual(
            [field.name for field in Course._meta.fields],
            ["id", "course_id", "created_by", "created_at", "current_version"],
        )
        self.assertEqual(
            [field.name for field in CourseVersion._meta.fields],
            [
                "id", "course", "version_number", "predecessor", "action",
                "course_name", "course_description", "course_learning_objectives",
                "definition_basis_reference", "actor", "actor_access_epoch",
                "authority_reference", "authority_decision_reference",
                "authority_evaluated_at", "request_reference", "idempotency_key",
                "payload_fingerprint", "occurred_at", "lineage_reference",
            ],
        )
        self.assertEqual({c.name for c in Course._meta.constraints}, {
            "s014_course_id_uniq", "s014_course_current_version_uniq"
        })
        self.assertEqual({c.name for c in CourseVersion._meta.constraints}, {
            "s014_course_version_number_uniq",
            "s014_course_actor_action_idem_uniq",
            "s014_course_initial_version_uniq",
            "s014_course_predecessor_uniq",
            "s014_course_version_lineage_uniq",
            "s014_course_version_positive",
            "s014_course_action_valid",
            "s014_course_create_shape",
            "s014_course_append_shape",
            "s014_course_actor_epoch_nonnegative",
        })
        self.assertEqual(CourseVersion._meta.indexes, [])
        for model, name in ((Course, "created_by"), (Course, "current_version"),
                            (CourseVersion, "course"), (CourseVersion, "predecessor"),
                            (CourseVersion, "actor")):
            self.assertFalse(model._meta.get_field(name).db_index)

    def test_choice_order_and_command_values(self):
        self.assertEqual(list(CourseVersion.CourseVersionAction.choices), [
            ("CREATE", "Create"), ("APPEND_VERSION", "Append version")
        ])
        self.assertEqual(
            list(CourseVersion._meta.get_field("action").choices),
            [("CREATE", "Create"), ("APPEND_VERSION", "Append version")],
        )

    def test_ordinary_instances_are_immutable_and_pointer_is_private(self):
        course = Course.objects.create(course_id=uuid4(), created_by=self.identity, created_at=self.now)
        version = self.version(course)
        course.current_version = version
        with self.assertRaises(ValidationError):
            course.save()
        course.current_version = None
        course._advance_current_version(version)
        version.course_name = "Changed"
        with self.assertRaises(ValidationError):
            version.save()
        with self.assertRaises(ValidationError):
            course.delete()
        with self.assertRaises(ValidationError):
            version.delete()

    def test_course_reloads_on_the_durable_instance_alias(self):
        course = Course(
            pk=41,
            course_id=uuid4(),
            created_by=self.identity,
            created_at=self.now,
        )
        course._state.adding = False
        course._state.db = "default"
        original = SimpleNamespace(
            course_id=uuid4(),
            created_by_id=course.created_by_id,
            created_at=course.created_at,
            current_version_id=None,
        )
        manager = Mock()
        manager.using.return_value.get.return_value = original
        with patch.object(Course, "objects", manager), self.assertRaises(ValidationError):
            course.save()
        manager.using.assert_called_once_with("default")
        manager.using.return_value.get.assert_called_once_with(pk=course.pk)

    def test_course_rejects_missing_alias_and_cross_alias_pointer_advance(self):
        course = Course.objects.create(
            course_id=uuid4(), created_by=self.identity, created_at=self.now
        )
        version = self.version(course)
        course._state.db = None
        with self.assertRaisesRegex(ValidationError, "database alias"):
            course.save()
        course._state.db = "default"
        version._state.db = None
        with self.assertRaisesRegex(ValidationError, "share a database"):
            course._advance_current_version(version)

    def test_model_enforces_multiline_bounds_and_canonical_text(self):
        course = Course.objects.create(course_id=uuid4(), created_by=self.identity, created_at=self.now)
        with self.assertRaises(ValidationError):
            self.version(course, course_description="x" * 4097)
        with self.assertRaises(ValidationError):
            self.version(course, course_name=" Course ")