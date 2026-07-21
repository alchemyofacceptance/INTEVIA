from datetime import datetime, timezone
import inspect

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import CareResponse, Profile, ProfileRole, Role
from src.intevia.services.care_service import CareService
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.library_service import LibraryService


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


class CareFoundationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="care-participant")
        self.profile = Profile.objects.create(user=self.user)
        role = Role.objects.create(name="CARE participant")
        ProfileRole.objects.create(profile=self.profile, role=role)
        self.authority = ContributionAuthority(Capability())
        self.care = CareService(authority=self.authority)

    def response_arguments(self, **overrides):
        arguments = {
            "identity": self.user,
            "response_id": "care:response:1",
            "trigger_type": CareResponse.TriggerType.HUMAN_REQUEST,
            "trigger_reference": "request:1",
            "context_source": CareResponse.ContextSource.HUMAN_DECLARED,
            "context_reference": "context:declared:1",
            "output_type": CareResponse.OutputType.BOUNDARY_SIGNAL,
            "content": "This touches a governed boundary.",
            "evidence_reference": "evidence:care:1",
            "lineage_reference": "care-lineage:1",
            "occurred_at": NOW,
        }
        arguments.update(overrides)
        return arguments

    def test_authority_denial_persists_nothing(self):
        denied = CareService(
            authority=ContributionAuthority(Capability({"record_care_response"}))
        )
        with self.assertRaises(NotAuthorised):
            denied.record_consequential_response(**self.response_arguments())
        self.assertEqual(CareResponse.objects.count(), 0)

    def test_only_declared_context_sources_are_accepted(self):
        for context_source in CareResponse.ContextSource.values:
            response = self.care.record_consequential_response(
                **self.response_arguments(
                    response_id=f"care:{context_source}",
                    context_source=context_source,
                    lineage_reference=f"lineage:{context_source}",
                )
            )
            self.assertEqual(response.context_source, context_source)

        with self.assertRaises(ValidationError):
            self.care.record_consequential_response(
                **self.response_arguments(
                    response_id="care:inferred",
                    context_source="inferred_emotional_state",
                )
            )

    def test_transient_orientation_is_deterministic_and_not_persisted(self):
        arguments = {
            "identity": self.user,
            "trigger_type": CareResponse.TriggerType.HUMAN_REQUEST,
            "trigger_reference": "request:transient",
            "context_source": CareResponse.ContextSource.EXPLICIT_PREFERENCE,
            "context_reference": "preference:minimal",
            "output_type": CareResponse.OutputType.ORIENTATION_GUIDANCE,
            "content": "Here is the requested orientation.",
            "occurred_at": NOW,
        }
        first = self.care.render_transient_response(**arguments)
        second = self.care.render_transient_response(**arguments)
        self.assertEqual(first, second)
        self.assertEqual(CareResponse.objects.count(), 0)

    def test_consequential_output_cannot_use_transient_path(self):
        with self.assertRaises(ValidationError):
            self.care.render_transient_response(
                identity=self.user,
                trigger_type=CareResponse.TriggerType.HUMAN_REQUEST,
                trigger_reference="request:boundary",
                context_source=CareResponse.ContextSource.HUMAN_DECLARED,
                context_reference="context:boundary",
                output_type=CareResponse.OutputType.BOUNDARY_SIGNAL,
                content="Boundary signal",
                occurred_at=NOW,
            )
        self.assertEqual(CareResponse.objects.count(), 0)

    def test_persisted_response_is_immutable_and_non_authoritative(self):
        response = self.care.record_consequential_response(
            **self.response_arguments(human_response_reference="human:disagreed")
        )
        self.assertEqual(response.human_response_reference, "human:disagreed")
        self.assertEqual(
            response.authority_reference,
            f"authority:{self.profile.pk}:record_care_response",
        )
        response.content = "changed"
        with self.assertRaises(ValidationError):
            response.save()
        with self.assertRaises(ValidationError):
            response.delete()

    def test_successor_preserves_one_lineage_without_editing_predecessor(self):
        first = self.care.record_consequential_response(**self.response_arguments())
        second = self.care.record_consequential_response(
            **self.response_arguments(
                response_id="care:response:2",
                evidence_reference="evidence:care:2",
                predecessor=first,
            )
        )
        first.refresh_from_db()
        self.assertEqual(second.predecessor, first)
        self.assertEqual(second.lineage_reference, first.lineage_reference)
        self.assertEqual(first.content, "This touches a governed boundary.")

    def test_optimisation_and_assessment_inputs_do_not_exist(self):
        prohibited = {
            "autonomy_score",
            "competence_score",
            "compliance",
            "dependency",
            "engagement",
            "inferred_confidence",
            "inferred_emotion",
            "retention",
            "readiness",
            "response_delay",
            "typing_speed",
            "writing_style",
        }
        model_fields = {field.name for field in CareResponse._meta.get_fields()}
        service_parameters = set(
            inspect.signature(
                self.care.record_consequential_response
            ).parameters
        )
        self.assertTrue(prohibited.isdisjoint(model_fields | service_parameters))

    def test_selection_pins_published_version_and_preserves_library_state(self):
        library = LibraryService(authority=self.authority)
        resource = library.create_resource(
            identity=self.user,
            resource_id="library:care",
            content="Governed orientation",
            evidence_reference="evidence:library:care",
            occurred_at=NOW,
        )
        library.transition_resource(
            identity=self.user,
            resource_id=resource.resource_id,
            command="publish_library_resource",
            evidence_reference="evidence:library:care:publish",
            occurred_at=NOW,
        )
        resource.refresh_from_db()
        transition_count = resource.transitions.count()
        response = self.care.record_consequential_response(
            **self.response_arguments(
                selected_library_version=resource.current_version,
                selection_basis="declared-topic:governance",
                governing_rule_reference="crcl-rule:orientation:v1",
            )
        )
        resource.refresh_from_db()
        self.assertEqual(response.selected_library_version, resource.current_version)
        self.assertEqual(response.selection_basis, "declared-topic:governance")
        self.assertEqual(
            response.governing_rule_reference,
            "crcl-rule:orientation:v1",
        )
        self.assertEqual(resource.state, resource.State.PUBLISHED)
        self.assertEqual(resource.transitions.count(), transition_count)

    def test_governed_action_selection_does_not_require_library_content(self):
        response = self.care.record_consequential_response(
            **self.response_arguments(
                selection_basis="declared-boundary:governance",
                governing_rule_reference="organism-rule:human-review:v1",
            )
        )
        self.assertIsNone(response.selected_library_version)
        self.assertEqual(
            response.governing_rule_reference,
            "organism-rule:human-review:v1",
        )

    def test_unpublished_or_incomplete_selection_lineage_is_rejected(self):
        library = LibraryService(authority=self.authority)
        resource = library.create_resource(
            identity=self.user,
            resource_id="library:care:draft",
            content="Draft orientation",
            evidence_reference="evidence:library:care:draft",
            occurred_at=NOW,
        )
        with self.assertRaises(ValidationError):
            self.care.record_consequential_response(
                **self.response_arguments(
                    selected_library_version=resource.current_version,
                    selection_basis="declared-topic:governance",
                    governing_rule_reference="crcl-rule:orientation:v1",
                )
            )
        with self.assertRaises(ValidationError):
            self.care.record_consequential_response(
                **self.response_arguments(
                    response_id="care:missing-basis",
                    governing_rule_reference="crcl-rule:orientation:v1",
                )
            )
        self.assertEqual(CareResponse.objects.count(), 0)
