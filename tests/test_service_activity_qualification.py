"""Guardians for the S012 submission qualification seam used by S013."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import transaction
from django.test import TestCase, TransactionTestCase

from core.models import (
    Identity,
    Service,
    ServiceActivity,
    ServiceActivityAssignment,
    ServiceActivityEvidenceReference,
    ServiceActivityTransition,
    ServiceVersion,
    ServiceWorkSubmission,
)
from src.intevia.services.service_activity_read_service import (
    ServiceCommandAction,
    _qualification_canonical_bytes,
    _recompute_decision_reference,
    _recompute_lineage_reference,
    _recompute_payload_fingerprint,
    _recompute_target_fingerprint,
    ServiceActivityReadError,
    ServiceActivityReadLineageError,
    ServiceActivityReadService,
)


NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
class _DummyVisibilityProvider:
    def check_visibility(self, *, request):
        return None


def _make_identity(username: str) -> Identity:
    user = User.objects.create_user(username=username)
    user.is_active = True
    user.save()
    return Identity.objects.create(
        credential=user,
        access_state=Identity.AccessState.ACTIVE,
    )


def _make_service(owner: Identity):
    service = Service(
        service_id=f"svc-{uuid4().hex[:8]}",
        state=Service.State.PUBLISHED,
        created_by=owner,
        created_at=NOW,
    )
    service.save()
    version = ServiceVersion(
        service=service,
        version_number=1,
        capability_purpose="Qualification test",
        domain_intent="Qualification test",
        created_by=owner,
        created_at=NOW,
    )
    version.save()
    service.current_version = version
    service.save()
    return service, version


def _evidence_dicts(evidence_tuples, actor_identity_id):
    actor_text = str(actor_identity_id)
    values = [
        {
            "evidence_kind": kind,
            "reference": reference,
            "supplied_by_identity_id": actor_text,
        }
        for kind, reference in evidence_tuples
    ]
    values.sort(key=lambda item: (item["evidence_kind"].encode("utf-8"), item["reference"].encode("utf-8")))
    return values


def _make_transition(
    activity,
    actor,
    *,
    sequence,
    action,
    from_state,
    to_state,
    previous,
    occurred_at,
    target_dict,
    command_dict,
    evidence_tuples,
):
    action_enum = ServiceCommandAction(action)
    evidence_dicts = _evidence_dicts(evidence_tuples, actor.identity_id)
    if action == ServiceActivityTransition.Action.CREATE:
        target_fingerprint = _recompute_target_fingerprint(
            action_enum,
            activity.activity_id,
            service_version_pk=activity.service_version_id,
        )
    elif action == ServiceActivityTransition.Action.ASSIGN:
        target_fingerprint = _recompute_target_fingerprint(
            action_enum,
            activity.activity_id,
            assignee_identity_id=target_dict["assignee_identity_id"],
        )
    else:
        target_fingerprint = _recompute_target_fingerprint(
            action_enum,
            activity.activity_id,
        )
    payload_fingerprint = _recompute_payload_fingerprint(
        action_enum,
        actor.identity_id,
        actor.access_epoch,
        f"REQ-{sequence}",
        f"IDEM-{sequence}",
        occurred_at,
        target_dict,
        command_dict,
        evidence_dicts,
    )
    lineage_reference = _recompute_lineage_reference(
        activity.activity_id,
        sequence,
        action_enum,
        actor.identity_id,
        actor.access_epoch,
        payload_fingerprint,
        occurred_at,
    )
    decision_reference = _recompute_decision_reference(
        "default",
        actor.pk,
        actor.identity_id,
        actor.access_epoch,
        action_enum,
        target_fingerprint,
        f"REQ-{sequence}",
        f"IDEM-{sequence}",
        occurred_at,
        f"AUTH-{sequence}",
    )
    row = ServiceActivityTransition(
        activity=activity,
        sequence=sequence,
        previous_transition=previous,
        action=action,
        from_state=from_state,
        to_state=to_state,
        actor=actor,
        actor_access_epoch=actor.access_epoch,
        authority_reference=f"AUTH-{sequence}",
        authority_decision_reference=decision_reference,
        authority_evaluated_at=occurred_at,
        request_reference=f"REQ-{sequence}",
        idempotency_key=f"IDEM-{sequence}",
        payload_fingerprint=payload_fingerprint,
        occurred_at=occurred_at,
        lineage_reference=lineage_reference,
    )
    row.save()
    return row


def _make_submitted_activity(actor: Identity):
    _, version = _make_service(actor)
    activity = ServiceActivity(
        activity_id=uuid4(),
        service_version=version,
        initiating_domain=ServiceActivity.InitiatingDomain.SERVICE,
        initiating_domain_reference="QUAL-REF-001",
        state=ServiceActivity.State.UNASSIGNED,
        created_by=actor,
        created_at=NOW,
    )
    activity.save()

    t1 = _make_transition(
        activity,
        actor,
        sequence=1,
        action=ServiceActivityTransition.Action.CREATE,
        from_state=None,
        to_state=ServiceActivity.State.UNASSIGNED,
        previous=None,
        occurred_at=NOW,
        target_dict={"activity_id": str(activity.activity_id), "service_version_pk": activity.service_version_id},
        command_dict={
            "activity_basis_reference": "EVIDENCE-CREATE-001",
            "initiating_domain": activity.initiating_domain,
            "initiating_domain_reference": activity.initiating_domain_reference,
        },
        evidence_tuples=[("activity_basis", "EVIDENCE-CREATE-001")],
    )
    ServiceActivityEvidenceReference.objects.create(
        transition=t1,
        evidence_kind=ServiceActivityEvidenceReference.Kind.ACTIVITY_BASIS,
        reference="EVIDENCE-CREATE-001",
        supplied_by=actor,
        authority_reference="AUTH-EVIDENCE-1",
        occurred_at=NOW,
    )
    t2 = _make_transition(
        activity,
        actor,
        sequence=2,
        action=ServiceActivityTransition.Action.ASSIGN,
        from_state=ServiceActivity.State.UNASSIGNED,
        to_state=ServiceActivity.State.ASSIGNED,
        previous=t1,
        occurred_at=NOW + timedelta(minutes=1),
        target_dict={"activity_id": str(activity.activity_id), "assignee_identity_id": str(actor.identity_id)},
        command_dict={
            "assignee_identity_id": str(actor.identity_id),
            "assignment_basis_reference": "EVIDENCE-ASSIGN-001",
            "assignment_reference": "ASSIGN-001",
        },
        evidence_tuples=[("assignment_basis", "EVIDENCE-ASSIGN-001")],
    )
    ServiceActivityAssignment.objects.create(
        activity=activity,
        assignee=actor,
        assigned_by=actor,
        assignment_reference="ASSIGN-001",
        assigned_at=t2.occurred_at,
        transition=t2,
    )
    ServiceActivityEvidenceReference.objects.create(
        transition=t2,
        evidence_kind=ServiceActivityEvidenceReference.Kind.ASSIGNMENT_BASIS,
        reference="EVIDENCE-ASSIGN-001",
        supplied_by=actor,
        authority_reference="AUTH-EVIDENCE-2",
        occurred_at=t2.occurred_at,
    )
    t3 = _make_transition(
        activity,
        actor,
        sequence=3,
        action=ServiceActivityTransition.Action.ACCEPT_ASSIGNMENT,
        from_state=ServiceActivity.State.ASSIGNED,
        to_state=ServiceActivity.State.IN_PROGRESS,
        previous=t2,
        occurred_at=NOW + timedelta(minutes=2),
        target_dict={"activity_id": str(activity.activity_id)},
        command_dict={},
        evidence_tuples=[],
    )
    t4 = _make_transition(
        activity,
        actor,
        sequence=4,
        action=ServiceActivityTransition.Action.SUBMIT_WORK,
        from_state=ServiceActivity.State.IN_PROGRESS,
        to_state=ServiceActivity.State.SUBMITTED,
        previous=t3,
        occurred_at=NOW + timedelta(minutes=3),
        target_dict={"activity_id": str(activity.activity_id)},
        command_dict={
            "submission_reference": "SUBMISSION-001",
            "submission_support_references": ["EVIDENCE-SUBMIT-001"],
        },
        evidence_tuples=[("submission_support", "EVIDENCE-SUBMIT-001")],
    )
    submission = ServiceWorkSubmission.objects.create(
        activity=activity,
        submitted_by=actor,
        submission_reference="SUBMISSION-001",
        submitted_at=t4.occurred_at,
        transition=t4,
    )
    ServiceActivityEvidenceReference.objects.create(
        transition=t4,
        evidence_kind=ServiceActivityEvidenceReference.Kind.SUBMISSION_SUPPORT,
        reference="EVIDENCE-SUBMIT-001",
        supplied_by=actor,
        authority_reference="AUTH-EVIDENCE-4",
        occurred_at=t4.occurred_at,
    )
    activity.state = ServiceActivity.State.SUBMITTED
    activity.head_transition = t4
    activity.save()
    return activity, submission


class ServiceSubmissionQualificationTests(TransactionTestCase):
    def setUp(self):
        self.identity = _make_identity("qualification-subject")
        self.activity, self.submission = _make_submitted_activity(self.identity)
        self.service = ServiceActivityReadService(
            visibility_provider=_DummyVisibilityProvider(),
            clock=lambda: NOW,
        )

    def test_requires_outer_atomic(self):
        with self.assertRaises(ServiceActivityReadError):
            self.service.qualify_submission_occurrence(activity_id=self.activity.activity_id)

    def test_returns_deterministic_dto(self):
        with transaction.atomic():
            result = self.service.qualify_submission_occurrence(
                activity_id=self.activity.activity_id
            )
        self.assertEqual(result.database_alias, "default")
        self.assertEqual(result.activity_id, self.activity.activity_id)
        self.assertEqual(result.subject_identity_id, self.identity.identity_id)
        self.assertEqual(result.actor_identity_id, self.identity.identity_id)
        self.assertTrue(result.actor_equals_assignee)
        self.assertEqual(result.qualification_schema, "intevia.s012.service-submission-qualification.v1")
        self.assertEqual(result.qualification_contract_version, 1)
        self.assertTrue(result.qualification_reference.startswith("s012sq1:"))
        self.assertEqual(len(result.qualification_reference), 72)

    def test_fixed_vector_uses_exact_canonical_profile_and_reference(self):
        payload = {
            "actor_access_epoch": 7,
            "actor_equals_assignee": True,
            "actor_identity_id": UUID("11111111-1111-4111-8111-111111111111"),
            "activity_id": UUID("22222222-2222-4222-8222-222222222222"),
            "contract_version": 1,
            "database_alias": "default",
            "occurred_at": datetime(2026, 7, 27, 12, 3, tzinfo=timezone.utc),
            "schema": "intevia.s012.service-submission-qualification.v1",
            "source_authority_reference": "AUTH-4",
            "subject_identity_id": UUID("11111111-1111-4111-8111-111111111111"),
            "submit_transition_lineage_reference": "s012l1:" + "a" * 64,
            "submit_transition_pk": 4,
            "submit_transition_sequence": 4,
        }
        import hashlib

        digest = hashlib.sha256(
            b"INTEVIA:S012:SERVICE_SUBMISSION_QUALIFICATION:v1\x00"
            + _qualification_canonical_bytes(payload)
        ).hexdigest()
        self.assertEqual(
            f"s012sq1:{digest}",
            "s012sq1:6cb6ee24f9bcda96bef588bbb9d81b26bd30c323c9ffa47b22d3c34a039db6ec",
        )

    def test_qualification_reference_binds_exact_database_alias(self):
        with transaction.atomic():
            result = self.service.qualify_submission_occurrence(
                activity_id=self.activity.activity_id
            )
        payload = {
            "actor_access_epoch": result.actor_access_epoch,
            "actor_equals_assignee": result.actor_equals_assignee,
            "actor_identity_id": result.actor_identity_id,
            "activity_id": result.activity_id,
            "contract_version": result.qualification_contract_version,
            "database_alias": "other",
            "occurred_at": result.occurred_at,
            "schema": result.qualification_schema,
            "source_authority_reference": result.source_authority_reference,
            "subject_identity_id": result.subject_identity_id,
            "submit_transition_lineage_reference": result.submit_transition_lineage_reference,
            "submit_transition_pk": result.submit_transition_pk,
            "submit_transition_sequence": result.submit_transition_sequence,
        }
        import hashlib

        other = "s012sq1:" + hashlib.sha256(
            b"INTEVIA:S012:SERVICE_SUBMISSION_QUALIFICATION:v1\x00"
            + _qualification_canonical_bytes(payload)
        ).hexdigest()
        self.assertNotEqual(result.qualification_reference, other)

    def test_canonical_profile_rejects_nfd_and_unsupported_types(self):
        base = {
            "actor_access_epoch": 0,
            "actor_equals_assignee": True,
            "actor_identity_id": self.identity.identity_id,
            "activity_id": self.activity.activity_id,
            "contract_version": 1,
            "database_alias": "default",
            "occurred_at": NOW,
            "schema": "intevia.s012.service-submission-qualification.v1",
            "source_authority_reference": "AUTH-4",
            "subject_identity_id": self.identity.identity_id,
            "submit_transition_lineage_reference": "s012l1:" + "a" * 64,
            "submit_transition_pk": 4,
            "submit_transition_sequence": 4,
        }
        invalid_values = [
            ("database_alias", "de\u0301fault"),
            ("occurred_at", datetime(2026, 7, 27, 12, 3)),
            ("submit_transition_pk", True),
            ("actor_equals_assignee", 1),
            ("source_authority_reference", 1.0),
            ("source_authority_reference", object()),
        ]
        for field, value in invalid_values:
            with self.subTest(field=field, value_type=type(value).__name__):
                payload = dict(base)
                payload[field] = value
                with self.assertRaises(ServiceActivityReadError):
                    _qualification_canonical_bytes(payload)

    def test_qualification_returns_no_submission_or_evidence_content_and_mutates_nothing(self):
        model_counts = {
            model: model.objects.count()
            for model in (
                ServiceActivity,
                ServiceActivityTransition,
                ServiceActivityAssignment,
                ServiceWorkSubmission,
                ServiceActivityEvidenceReference,
            )
        }
        with transaction.atomic():
            result = self.service.qualify_submission_occurrence(
                activity_id=self.activity.activity_id
            )
        self.assertFalse(hasattr(result, "submission_reference"))
        self.assertFalse(hasattr(result, "evidence"))
        self.assertEqual(
            model_counts,
            {model: model.objects.count() for model in model_counts},
        )

    def test_rejects_stale_head_and_corrupt_fingerprints(self):
        submit = self.submission.transition
        for field, value in (
            ("payload_fingerprint", "0" * 64),
            ("lineage_reference", "s012l1:" + "0" * 64),
            ("authority_decision_reference", "s012d1:" + "0" * 64),
        ):
            with self.subTest(field=field):
                original = getattr(submit, field)
                ServiceActivityTransition.objects.filter(pk=submit.pk).update(
                    **{field: value}
                )
                with transaction.atomic():
                    with self.assertRaises(ServiceActivityReadLineageError):
                        self.service.qualify_submission_occurrence(
                            activity_id=self.activity.activity_id
                        )
                ServiceActivityTransition.objects.filter(pk=submit.pk).update(
                    **{field: original}
                )

        ServiceActivity.objects.filter(pk=self.activity.pk).update(
            head_transition_id=submit.previous_transition_id
        )
        with transaction.atomic():
            with self.assertRaises(ServiceActivityReadLineageError):
                self.service.qualify_submission_occurrence(
                    activity_id=self.activity.activity_id
                )

    def test_rejects_submitter_assignee_mismatch(self):
        other = _make_identity("qualification-other")
        ServiceWorkSubmission.objects.filter(pk=self.submission.pk).update(
            submitted_by=other
        )
        with transaction.atomic():
            with self.assertRaises(ServiceActivityReadLineageError):
                self.service.qualify_submission_occurrence(
                    activity_id=self.activity.activity_id
                )