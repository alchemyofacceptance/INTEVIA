"""Guardians for S012 six-model schema, choices, constraints, immutability, and queryset bypass."""

import re
from datetime import datetime, timezone
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from core.models import (
    Identity,
    Service,
    ServiceActivity,
    ServiceActivityAssignment,
    ServiceActivityEvidenceReference,
    ServiceActivityReview,
    ServiceActivityTransition,
    ServiceVersion,
    ServiceWorkSubmission,
)


NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
_VALID_FP = "a" * 64
_VALID_DECISION = "s012d1:" + "a" * 64
_VALID_LINEAGE = "s012l1:" + "b" * 64


def _make_identity(username, *, active=True):
    user = User.objects.create_user(username=username)
    if active:
        user.is_active = True
        user.save()
    identity = Identity.objects.create(
        credential=user,
        access_state=Identity.AccessState.ACTIVE if active else Identity.AccessState.PENDING,
    )
    return identity


def _make_service(creator):
    service = Service(
        service_id=f"svc-{uuid4().hex[:8]}",
        state=Service.State.PUBLISHED,
        created_by=creator,
        created_at=NOW,
    )
    service.save()
    sv = ServiceVersion(
        service=service,
        version_number=1,
        capability_purpose="Test capability",
        domain_intent="Test domain intent",
        created_by=creator,
        created_at=NOW,
    )
    sv.save()
    service.current_version = sv
    service.save()
    return service, sv


def _make_activity(creator, sv, *, activity_id=None, state="unassigned"):
    activity_id = activity_id or uuid4()
    a = ServiceActivity(
        activity_id=activity_id,
        service_version=sv,
        initiating_domain="service",
        initiating_domain_reference="REF-001",
        state=state,
        created_by=creator,
        created_at=NOW,
    )
    a.save()
    return a


def _make_transition(activity, actor, *, sequence=1, action="CREATE",
                     from_state=None, to_state="unassigned", prev=None,
                     lineage_ref=None):
    lineage_ref = lineage_ref or ("s012l1:" + uuid4().hex + "a" * 32)[:71]
    t = ServiceActivityTransition(
        activity=activity,
        sequence=sequence,
        previous_transition=prev,
        action=action,
        from_state=from_state,
        to_state=to_state,
        actor=actor,
        actor_access_epoch=actor.access_epoch,
        authority_reference="AUTH-001",
        authority_decision_reference=_VALID_DECISION,
        authority_evaluated_at=NOW,
        request_reference="REQ-001",
        idempotency_key=f"IDEM-{uuid4().hex[:8]}",
        payload_fingerprint=_VALID_FP,
        occurred_at=NOW,
        lineage_reference=lineage_ref,
    )
    t.save()
    return t


class SixDistinctModelsTests(TestCase):
    def test_six_s012_models_are_distinct_classes(self):
        self.assertEqual(
            len({
                ServiceActivity,
                ServiceActivityTransition,
                ServiceActivityAssignment,
                ServiceWorkSubmission,
                ServiceActivityReview,
                ServiceActivityEvidenceReference,
            }),
            6,
        )


class ServiceActivitySchemaTests(TestCase):
    def setUp(self):
        self.identity = _make_identity("schema-owner")
        self.service, self.sv = _make_service(self.identity)

    def test_nine_initiating_domains(self):
        expected = {
            "core", "organism", "library", "education", "event",
            "service", "discussion", "engagement", "exchange",
        }
        actual = {v for v, _ in ServiceActivity.InitiatingDomain.choices}
        self.assertEqual(actual, expected)

    def test_eight_states(self):
        expected = {
            "unassigned", "assigned", "in_progress", "submitted",
            "reviewed", "completed", "declined", "cancelled",
        }
        actual = {v for v, _ in ServiceActivity.State.choices}
        self.assertEqual(actual, expected)

    def test_invalid_domain_rejected_by_check(self):
        a = _make_activity(self.identity, self.sv)
        a.initiating_domain = "invalid"
        with self.assertRaises((ValidationError, Exception)):
            a.save()

    def test_invalid_state_rejected_by_check(self):
        a = _make_activity(self.identity, self.sv)
        t = _make_transition(a, self.identity)
        a.head_transition = t
        a.state = "invalid"
        with self.assertRaises((ValidationError, Exception)):
            a.save()


class TransitionSchemaTests(TestCase):
    def test_eight_actions(self):
        expected = {
            "CREATE", "ASSIGN", "ACCEPT_ASSIGNMENT", "DECLINE_ASSIGNMENT",
            "SUBMIT_WORK", "REVIEW_WORK", "COMPLETE_ACTIVITY", "CANCEL_ACTIVITY",
        }
        actual = {v for v, _ in ServiceActivityTransition.Action.choices}
        self.assertEqual(actual, expected)


class EvidenceKindTests(TestCase):
    def test_seven_evidence_kinds(self):
        expected = {
            "activity_basis", "assignment_basis", "submission_support",
            "review_record", "completion_record", "cancellation_basis",
            "decline_basis",
        }
        actual = {v for v, _ in ServiceActivityEvidenceReference.Kind.choices}
        self.assertEqual(actual, expected)


class ImmutabilityTests(TestCase):
    def setUp(self):
        self.identity = _make_identity("immutable-test")
        self.service, self.sv = _make_service(self.identity)

    def test_activity_parentage_immutable(self):
        a = _make_activity(self.identity, self.sv)
        a.initiating_domain_reference = "CHANGED"
        with self.assertRaises(ValidationError):
            a.save()

    def test_activity_delete_refused(self):
        a = _make_activity(self.identity, self.sv)
        with self.assertRaises(ValidationError):
            a.delete()

    def test_transition_append_only(self):
        a = _make_activity(self.identity, self.sv)
        t = _make_transition(a, self.identity)
        t.request_reference = "CHANGED"
        with self.assertRaises(ValidationError):
            t.save()

    def test_transition_delete_refused(self):
        a = _make_activity(self.identity, self.sv)
        t = _make_transition(a, self.identity)
        with self.assertRaises(ValidationError):
            t.delete()

    def test_assignment_immutable(self):
        a = _make_activity(self.identity, self.sv)
        t = _make_transition(a, self.identity)
        assign = ServiceActivityAssignment(
            activity=a, assignee=self.identity, assigned_by=self.identity,
            assignment_reference="REF", assigned_at=NOW, transition=t,
        )
        assign.save()
        assign.assignment_reference = "CHANGED"
        with self.assertRaises(ValidationError):
            assign.save()

    def test_assignment_delete_refused(self):
        a = _make_activity(self.identity, self.sv)
        t = _make_transition(a, self.identity)
        assign = ServiceActivityAssignment(
            activity=a, assignee=self.identity, assigned_by=self.identity,
            assignment_reference="REF", assigned_at=NOW, transition=t,
        )
        assign.save()
        with self.assertRaises(ValidationError):
            assign.delete()

    def test_submission_immutable_and_no_delete(self):
        a = _make_activity(self.identity, self.sv)
        t1 = _make_transition(a, self.identity)
        # Need ASSIGN then ACCEPT then SUBMIT for proper FK
        t2 = _make_transition(a, self.identity, sequence=2, action="ASSIGN",
                              from_state="unassigned", to_state="assigned", prev=t1)
        assign = ServiceActivityAssignment(
            activity=a, assignee=self.identity, assigned_by=self.identity,
            assignment_reference="REF", assigned_at=NOW, transition=t2,
        )
        assign.save()
        t3 = _make_transition(a, self.identity, sequence=3, action="SUBMIT_WORK",
                              from_state="in_progress", to_state="submitted", prev=t2)
        sub = ServiceWorkSubmission(
            activity=a, submitted_by=self.identity,
            submission_reference="REF-SUB", submitted_at=NOW, transition=t3,
        )
        sub.save()
        sub.submission_reference = "CHANGED"
        with self.assertRaises(ValidationError):
            sub.save()
        with self.assertRaises(ValidationError):
            sub.delete()

    def test_review_immutable_and_no_delete(self):
        a = _make_activity(self.identity, self.sv)
        t1 = _make_transition(a, self.identity)
        t2 = _make_transition(a, self.identity, sequence=2, action="ASSIGN",
                              from_state="unassigned", to_state="assigned", prev=t1)
        assign = ServiceActivityAssignment(
            activity=a, assignee=self.identity, assigned_by=self.identity,
            assignment_reference="REF", assigned_at=NOW, transition=t2,
        )
        assign.save()
        t3 = _make_transition(a, self.identity, sequence=3, action="SUBMIT_WORK",
                              from_state="in_progress", to_state="submitted", prev=t2)
        sub = ServiceWorkSubmission(
            activity=a, submitted_by=self.identity,
            submission_reference="REF-SUB", submitted_at=NOW, transition=t3,
        )
        sub.save()
        t4 = _make_transition(a, self.identity, sequence=4, action="REVIEW_WORK",
                              from_state="submitted", to_state="reviewed", prev=t3)
        rev = ServiceActivityReview(
            submission=sub, reviewed_by=self.identity,
            review_reference="REF-REV", reviewed_at=NOW, transition=t4,
        )
        rev.save()
        rev.review_reference = "CHANGED"
        with self.assertRaises(ValidationError):
            rev.save()
        with self.assertRaises(ValidationError):
            rev.delete()

    def test_evidence_immutable_and_no_delete(self):
        a = _make_activity(self.identity, self.sv)
        t = _make_transition(a, self.identity)
        ev = ServiceActivityEvidenceReference(
            transition=t, evidence_kind="activity_basis",
            reference="REF-EV", supplied_by=self.identity,
            authority_reference="AUTH-001", occurred_at=NOW,
        )
        ev.save()
        ev.reference = "CHANGED"
        with self.assertRaises(ValidationError):
            ev.save()
        with self.assertRaises(ValidationError):
            ev.delete()


class NegativeQuerysetBypassTests(TestCase):
    """Demonstrate that ORM guards do not prevent QuerySet.update() or bulk_create."""

    def setUp(self):
        self.identity = _make_identity("bypass-test")
        self.service, self.sv = _make_service(self.identity)

    def test_queryset_update_bypasses_save_immutability(self):
        """Negative guardian: QuerySet.update() bypasses ServiceActivity.save() immutability."""
        a = _make_activity(self.identity, self.sv)
        original_ref = a.initiating_domain_reference
        ServiceActivity.objects.filter(pk=a.pk).update(
            initiating_domain_reference="BYPASSED"
        )
        a.refresh_from_db()
        self.assertEqual(a.initiating_domain_reference, "BYPASSED")
        # restore
        ServiceActivity.objects.filter(pk=a.pk).update(
            initiating_domain_reference=original_ref
        )

    def test_queryset_delete_bypasses_delete_guard(self):
        """Negative guardian: QuerySet.delete() bypasses model delete() guard."""
        a = _make_activity(self.identity, self.sv)
        pk = a.pk
        ServiceActivity.objects.filter(pk=pk).delete()
        self.assertFalse(ServiceActivity.objects.filter(pk=pk).exists())

    def test_transition_bulk_create_bypasses_save_guard(self):
        """Negative guardian: bulk_create bypasses append-only save guard."""
        a = _make_activity(self.identity, self.sv)
        lr1 = "s012l1:" + uuid4().hex + "a" * 32
        lr2 = "s012l1:" + uuid4().hex + "b" * 32
        transitions = [
            ServiceActivityTransition(
                activity=a, sequence=1, action="CREATE", from_state=None,
                to_state="unassigned", actor=self.identity,
                actor_access_epoch=0, authority_reference="AUTH",
                authority_decision_reference=_VALID_DECISION,
                authority_evaluated_at=NOW, request_reference="REQ",
                idempotency_key=f"BULK-{i}", payload_fingerprint=_VALID_FP,
                occurred_at=NOW, lineage_reference=lr[:71],
            )
            for i, lr in enumerate([lr1, lr2])
        ]
        # The first will succeed; the second might conflict on (activity, sequence)
        # but this demonstrates bulk_create bypasses save() checks
        created = ServiceActivityTransition.objects.bulk_create([transitions[0]])
        self.assertEqual(len(created), 1)
        # Now attempt to save with pk set — ORM guard stops it
        created[0].request_reference = "CHANGED"
        with self.assertRaises(ValidationError):
            created[0].save()


class ConstraintCatalogueTests(TestCase):
    """Verify exact 14 unique / 17 check / 10 index names exist in the schema."""

    EXPECTED_UNIQUE = {
        "s012_activity_id_uniq",
        "s012_activity_head_uniq",
        "s012_assignment_activity_uniq",
        "s012_assignment_transition_uniq",
        "s012_submission_activity_uniq",
        "s012_submission_transition_uniq",
        "s012_review_submission_uniq",
        "s012_review_transition_uniq",
        "s012_transition_activity_sequence_uniq",
        "s012_activity_actor_action_idem_uniq",
        "s012_transition_initial_uniq",
        "s012_transition_successor_uniq",
        "s012_transition_lineage_ref_uniq",
        "s012_evidence_tuple_uniq",
    }

    EXPECTED_CHECK = {
        "s012_activity_domain_valid_ck",
        "s012_activity_state_valid_ck",
        "s012_activity_refs_nonempty_ck",
        "s012_assignment_refs_nonempty_ck",
        "s012_submission_refs_nonempty_ck",
        "s012_review_refs_nonempty_ck",
        "s012_transition_sequence_positive_ck",
        "s012_transition_action_valid_ck",
        "s012_transition_from_state_valid_ck",
        "s012_transition_to_state_valid_ck",
        "s012_transition_edge_valid_ck",
        "s012_transition_payload_hex_ck",
        "s012_transition_decision_ref_ck",
        "s012_transition_lineage_ref_ck",
        "s012_transition_refs_nonempty_ck",
        "s012_evidence_kind_valid_ck",
        "s012_evidence_refs_nonempty_ck",
    }

    EXPECTED_INDEXES = {
        "s012_activity_service_version_idx",
        "s012_activity_state_idx",
        "s012_activity_created_by_idx",
        "s012_assignment_assignee_idx",
        "s012_assignment_assigned_by_idx",
        "s012_submission_submitted_by_idx",
        "s012_review_reviewed_by_idx",
        "s012_transition_actor_idx",
        "s012_transition_activity_action_idx",
        "s012_evidence_supplied_by_idx",
    }

    def _collect_s012_names(self):
        found_unique = set()
        found_check = set()
        found_index = set()
        s012_models = [
            ServiceActivity, ServiceActivityTransition,
            ServiceActivityAssignment, ServiceWorkSubmission,
            ServiceActivityReview, ServiceActivityEvidenceReference,
        ]
        for model_cls in s012_models:
            for c in model_cls._meta.constraints:
                if c.name.startswith("s012_"):
                    if isinstance(c, models.UniqueConstraint):
                        found_unique.add(c.name)
                    elif isinstance(c, models.CheckConstraint):
                        found_check.add(c.name)
            for idx in model_cls._meta.indexes:
                if idx.name.startswith("s012_"):
                    found_index.add(idx.name)
        return found_unique, found_check, found_index

    def test_exact_14_unique_constraints(self):
        found, _, _ = self._collect_s012_names()
        self.assertEqual(found, self.EXPECTED_UNIQUE)
        self.assertEqual(len(found), 14)

    def test_exact_17_check_constraints(self):
        _, found, _ = self._collect_s012_names()
        self.assertEqual(found, self.EXPECTED_CHECK)
        self.assertEqual(len(found), 17)

    def test_exact_10_indexes(self):
        _, _, found = self._collect_s012_names()
        self.assertEqual(found, self.EXPECTED_INDEXES)
        self.assertEqual(len(found), 10)


class IdentityFKPredictionTests(TestCase):
    """Live catalogue remains exact: 34 base + 7 S012 + 4 S013 = 45."""

    EXPECTED_S012_FK_ADDITIONS = {
        ("serviceactivity", "created_by"),
        ("serviceactivitytransition", "actor"),
        ("serviceactivityassignment", "assignee"),
        ("serviceactivityassignment", "assigned_by"),
        ("serviceworksubmission", "submitted_by"),
        ("serviceactivityreview", "reviewed_by"),
        ("serviceactivityevidencereference", "supplied_by"),
    }

    EXPECTED_S013_FK_ADDITIONS = {
        ("profileeffectproposallineage", "subject"),
        ("profileeffectproposallineage", "proposer"),
        ("profileeffectproposaltransition", "actor"),
        ("profileeffectprojectiondisposition", "actor"),
    }

    EXCLUDED_INTERNAL_FKS = {
        ("identitytransition", "identity"),
        ("identitytransition", "requesting_actor"),
        ("originatingmembershipprovisioningrequest", "identity"),
    }

    def test_identity_fk_count_34_to_45(self):
        from django.apps import apps
        identity_model = apps.get_model("core", "Identity")
        fk_fields = []
        for model in apps.get_models():
            if model._meta.app_label != "core":
                continue
            for field in model._meta.get_fields():
                if (
                    hasattr(field, "related_model")
                    and field.related_model is identity_model
                    and hasattr(field, "column")
                ):
                    fk_fields.append((model._meta.model_name, field.name))

        scoped_fks = set(fk_fields) - self.EXCLUDED_INTERNAL_FKS
        self.assertEqual(len(scoped_fks), 45)

        s012_fks = {
            (name, fname) for name, fname in scoped_fks
            if (name, fname) in self.EXPECTED_S012_FK_ADDITIONS
        }
        self.assertEqual(s012_fks, self.EXPECTED_S012_FK_ADDITIONS)
        self.assertEqual(len(s012_fks), 7)

        s013_fks = {
            (name, fname) for name, fname in scoped_fks
            if (name, fname) in self.EXPECTED_S013_FK_ADDITIONS
        }
        self.assertEqual(s013_fks, self.EXPECTED_S013_FK_ADDITIONS)
        self.assertEqual(len(s013_fks), 4)

    def test_pre_s012_base_is_34(self):
        from django.apps import apps
        identity_model = apps.get_model("core", "Identity")
        all_fks = []
        for model in apps.get_models():
            if model._meta.app_label != "core":
                continue
            for field in model._meta.get_fields():
                if (
                    hasattr(field, "related_model")
                    and field.related_model is identity_model
                    and hasattr(field, "column")
                ):
                    all_fks.append((model._meta.model_name, field.name))
        scoped_fks = set(all_fks) - self.EXCLUDED_INTERNAL_FKS
        pre_s012 = (
            scoped_fks
            - self.EXPECTED_S012_FK_ADDITIONS
            - self.EXPECTED_S013_FK_ADDITIONS
        )
        self.assertEqual(len(pre_s012), 34)


class ProtectedForeignKeyTests(TestCase):
    """All S012 FK on_delete=PROTECT."""

    def setUp(self):
        self.identity = _make_identity("fk-protect-test")
        self.service, self.sv = _make_service(self.identity)

    def test_activity_created_by_protected(self):
        a = _make_activity(self.identity, self.sv)
        with self.assertRaises(ProtectedError):
            self.identity.delete()

    def test_transition_actor_protected(self):
        a = _make_activity(self.identity, self.sv)
        t = _make_transition(a, self.identity)
        with self.assertRaises(ProtectedError):
            self.identity.delete()
