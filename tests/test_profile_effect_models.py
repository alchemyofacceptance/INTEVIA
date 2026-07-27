"""Guardians for the S013 profile effect models and schema metadata."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import (
    Identity,
    ProfileEffectProjectionDisposition,
    ProfileEffectProposalLineage,
    ProfileEffectProposalTransition,
)


NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)


def _make_identity(username: str) -> Identity:
    user = User.objects.create_user(username=username)
    user.is_active = True
    user.save()
    return Identity.objects.create(
        credential=user,
        access_state=Identity.AccessState.ACTIVE,
    )


def _make_lineage(subject: Identity):
    lineage = ProfileEffectProposalLineage(
        subject=subject,
        proposer=subject,
        source_database_alias="default",
        source_activity_id=uuid4(),
        source_transition_pk=11,
        source_transition_sequence=4,
        source_transition_lineage_reference="s012l1:" + "a" * 64,
        source_occurred_at=NOW,
        source_actor_access_epoch=subject.access_epoch,
        source_authority_reference="AUTH-S012-001",
        source_qualification_reference="s012sq1:" + "b" * 64,
        subject_relation=ProfileEffectProposalLineage.SubjectRelation.IMMUTABLE_ACTIVITY_ASSIGNEE,
        effect_type=ProfileEffectProposalLineage.EffectType.SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED,
        contract_version=1,
        has_current_survivor=True,
        created_at=NOW,
        updated_at=NOW,
    )
    lineage.save()
    transition = ProfileEffectProposalTransition(
        lineage=lineage,
        sequence=1,
        previous_transition=None,
        action=ProfileEffectProposalTransition.Action.CREATE_PROPOSAL,
        from_state=None,
        to_state=ProfileEffectProposalTransition.State.ACTIVE,
        actor=subject,
        actor_access_epoch=subject.access_epoch,
        authority_reference="AUTH-PROPOSAL-001",
        authority_decision_reference="s013pa1:" + "c" * 64,
        authority_evaluated_at=NOW,
        request_reference="REQ-001",
        idempotency_key="IDEM-001",
        payload_fingerprint="d" * 64,
        occurred_at=NOW,
        lineage_reference="s013pl1:" + "e" * 64,
    )
    transition.save()
    lineage.head_proposal_transition = transition
    lineage.save()
    return lineage, transition


class ProfileEffectModelTests(TestCase):
    def setUp(self):
        self.subject = _make_identity("profile-effect-subject")
        self.lineage, self.transition = _make_lineage(self.subject)

    def test_three_models_are_distinct(self):
        self.assertEqual(
            len({
                ProfileEffectProposalLineage,
                ProfileEffectProposalTransition,
                ProfileEffectProjectionDisposition,
            }),
            3,
        )

    def test_root_immutable_and_undeletable(self):
        self.lineage.source_database_alias = "other"
        with self.assertRaises(ValidationError):
            self.lineage.save()
        with self.assertRaises(ValidationError):
            self.lineage.delete()

    def test_transition_append_only_and_undeletable(self):
        self.transition.request_reference = "CHANGED"
        with self.assertRaises(ValidationError):
            self.transition.save()
        with self.assertRaises(ValidationError):
            self.transition.delete()

    def test_projection_append_only_and_undeletable(self):
        disposition = ProfileEffectProjectionDisposition.objects.create(
            proposal_transition=self.transition,
            sequence=1,
            previous_disposition=None,
            action=ProfileEffectProjectionDisposition.Action.AUTHORISE_PROJECTION,
            from_state=ProfileEffectProjectionDisposition.State.UNAUTHORISED,
            to_state=ProfileEffectProjectionDisposition.State.AUTHORISED,
            actor=self.subject,
            actor_access_epoch=self.subject.access_epoch,
            authority_reference="AUTH-PROJECTION-001",
            authority_decision_reference="s013px1:" + "f" * 64,
            authority_evaluated_at=NOW + timedelta(minutes=1),
            request_reference="REQ-002",
            idempotency_key="IDEM-002",
            payload_fingerprint="1" * 64,
            occurred_at=NOW + timedelta(minutes=1),
            lineage_reference="s013xl1:" + "2" * 64,
        )
        disposition.request_reference = "CHANGED"
        with self.assertRaises(ValidationError):
            disposition.save()
        with self.assertRaises(ValidationError):
            disposition.delete()

    def test_exact_constraint_catalogues(self):
        root_constraints = {item.name for item in ProfileEffectProposalLineage._meta.constraints if item.name.startswith("s013_pe_")}
        proposal_constraints = {item.name for item in ProfileEffectProposalTransition._meta.constraints if item.name.startswith("s013_pe_")}
        projection_constraints = {item.name for item in ProfileEffectProjectionDisposition._meta.constraints if item.name.startswith("s013_pe_")}
        self.assertEqual(
            root_constraints,
            {
                "s013_pe_lineage_id_uniq",
                "s013_pe_lineage_semantic_uniq",
                "s013_pe_current_survivor_uniq",
                "s013_pe_head_proposal_uniq",
                "s013_pe_subject_proposer_ck",
                "s013_pe_subject_relation_ck",
                "s013_pe_effect_type_ck",
                "s013_pe_contract_version_ck",
                "s013_pe_source_sequence_positive_ck",
                "s013_pe_source_lineage_ref_ck",
                "s013_pe_source_qualification_ref_ck",
                "s013_pe_source_refs_nonempty_ck",
            },
        )
        self.assertEqual(
            proposal_constraints,
            {
                "s013_pe_prop_sequence_uniq",
                "s013_pe_prop_actor_action_idem_uniq",
                "s013_pe_prop_initial_uniq",
                "s013_pe_prop_successor_uniq",
                "s013_pe_prop_lineage_ref_uniq",
                "s013_pe_prop_sequence_positive_ck",
                "s013_pe_prop_action_valid_ck",
                "s013_pe_prop_from_state_valid_ck",
                "s013_pe_prop_to_state_valid_ck",
                "s013_pe_prop_edge_valid_ck",
                "s013_pe_prop_payload_hex_ck",
                "s013_pe_prop_decision_ref_ck",
                "s013_pe_prop_lineage_ref_ck",
                "s013_pe_prop_refs_nonempty_ck",
            },
        )
        self.assertEqual(
            projection_constraints,
            {
                "s013_pe_proj_sequence_uniq",
                "s013_pe_proj_actor_action_idem_uniq",
                "s013_pe_proj_initial_uniq",
                "s013_pe_proj_successor_uniq",
                "s013_pe_proj_lineage_ref_uniq",
                "s013_pe_proj_sequence_positive_ck",
                "s013_pe_proj_action_valid_ck",
                "s013_pe_proj_from_state_valid_ck",
                "s013_pe_proj_to_state_valid_ck",
                "s013_pe_proj_edge_valid_ck",
                "s013_pe_proj_payload_hex_ck",
                "s013_pe_proj_decision_ref_ck",
                "s013_pe_proj_lineage_ref_ck",
                "s013_pe_proj_refs_nonempty_ck",
            },
        )