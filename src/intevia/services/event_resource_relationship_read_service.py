"""Current-only, default-deny Event resource relationship projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from django.db import transaction

from core.models import (
    Event,
    EventResourceAssertion,
    EventResourceRelationship,
    EventResourceRelationshipEvidence,
    EventResourceRelationshipTransition,
    Identity,
)
from src.intevia.services.event_resource_relationship_contract import (
    AUTHORITY_POLICY_REFERENCE,
    CANONICALIZATION as EVENT_CANONICALIZATION,
    DISCLOSURE_POLICY_REFERENCE,
    POLICY_ENVIRONMENT as EVENT_POLICY_ENVIRONMENT,
    SCHEMA_ID as EVENT_SCHEMA_ID,
    SCHEMA_VERSION as EVENT_SCHEMA_VERSION,
    EventRelationshipDisclosure,
    ExistenceDisclosureResult,
    RelationshipDisclosureResult,
    RelationshipPurpose,
    RelationshipState,
    VoidReason,
    canonical_event_payload_bytes,
    determination_reference_for,
)
from src.intevia.services.library_exact_version_contract import (
    AuthorityResult,
    DeterminationKind,
    DeterminationPayload,
    DisclosureResult,
    LibraryExactVersionContractService,
    LinkabilityResult,
    canonical_payload_bytes,
    envelope_for,
)


@dataclass(frozen=True, slots=True)
class EventResourcePresentation:
    content: str
    purpose: str


class EventResourceRelationshipReadService:
    def __init__(
        self,
        *,
        library_contract: LibraryExactVersionContractService,
        relationship_disclosure: EventRelationshipDisclosure,
        database_alias: str = "default",
    ) -> None:
        if library_contract is None or relationship_disclosure is None:
            raise TypeError("Library and EVENT disclosure evaluators are required")
        if library_contract.database_alias != database_alias:
            raise ValueError("Library contract must use the read database alias")
        self.library_contract = library_contract
        self.relationship_disclosure = relationship_disclosure
        self.database_alias = database_alias

    @staticmethod
    def _canonical_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _canonical_object(payload: bytes) -> dict[str, object] | None:
        def object_pairs(pairs):
            result = {}
            for name, value in pairs:
                if name in result:
                    raise ValueError("duplicate evidence field")
                result[name] = value
            return result

        try:
            parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=object_pairs)
            if type(parsed) is not dict:
                return None
            return parsed
        except (UnicodeDecodeError, ValueError, TypeError):
            return None

    @classmethod
    def _valid_event_authority(
        cls,
        evidence: EventResourceRelationshipEvidence,
        relationship: EventResourceRelationship,
        assertion: EventResourceAssertion,
        transition: EventResourceRelationshipTransition,
    ) -> bool:
        canonical = bytes(evidence.canonical_payload)
        payload = cls._canonical_object(canonical)
        predecessor = transition.from_assertion
        expected_fields = {
            "action", "actor_access_epoch", "actor_identity_id", "authority_scope",
            "binding_reference", "canonicalization", "current_assertion_id",
            "current_purpose", "environment", "evaluated_at", "event_id",
            "event_state", "occurred_at", "policy_reference", "proposed_purpose",
            "provider_snapshot_reference", "relationship_id", "resource_id",
            "result", "schema_id", "schema_version", "version_number",
        }
        if payload is None or set(payload) != expected_fields:
            return False
        try:
            if canonical_event_payload_bytes(payload) != canonical:
                return False
        except (TypeError, ValueError):
            return False
        create = transition.action == EventResourceRelationshipTransition.Action.CREATE
        return (
            evidence.schema_id == EVENT_SCHEMA_ID
            and evidence.schema_version == EVENT_SCHEMA_VERSION
            and evidence.canonicalization == EVENT_CANONICALIZATION
            and evidence.result == AuthorityResult.QUALIFIED
            and evidence.policy_reference == AUTHORITY_POLICY_REFERENCE
            and evidence.determination_reference == determination_reference_for(canonical)
            and evidence.determination_reference == transition.event_authority_reference
            and evidence.payload_sha256 == hashlib.sha256(canonical).hexdigest()
            and str(evidence.actor_identity_id) == str(transition.actor.identity_id)
            and evidence.actor_access_epoch == transition.actor_access_epoch
            and evidence.viewer_identity_id is None
            and evidence.viewer_access_epoch is None
            and evidence.request_reference is None
            and evidence.consumer_reference is None
            and evidence.authority_binding_reference == payload["binding_reference"]
            and evidence.provider_snapshot_reference == payload["provider_snapshot_reference"]
            and cls._canonical_time(evidence.evaluated_at) == payload["evaluated_at"]
            and payload["schema_id"] == EVENT_SCHEMA_ID
            and payload["schema_version"] == EVENT_SCHEMA_VERSION
            and payload["canonicalization"] == EVENT_CANONICALIZATION
            and payload["environment"] == EVENT_POLICY_ENVIRONMENT
            and payload["policy_reference"] == AUTHORITY_POLICY_REFERENCE
            and payload["result"] == AuthorityResult.QUALIFIED
            and payload["action"] == transition.action
            and payload["actor_identity_id"] == str(transition.actor.identity_id)
            and payload["actor_access_epoch"] == str(transition.actor_access_epoch)
            and payload["authority_scope"] == transition.authority_scope
            and payload["event_id"] == relationship.event.event_id
            and payload["relationship_id"] == (None if create else relationship.relationship_id)
            and payload["current_assertion_id"] == (
                None if create else str(getattr(predecessor, "pk", None))
            )
            and payload["resource_id"] == relationship.library_resource.resource_id
            and payload["version_number"]
            == str(assertion.library_resource_version.version_number)
            and payload["current_purpose"] == (
                None if create else getattr(predecessor, "purpose", None)
            )
            and payload["proposed_purpose"] == assertion.purpose
            and payload["occurred_at"] == cls._canonical_time(transition.occurred_at)
            and payload["evaluated_at"]
            == cls._canonical_time(transition.event_authority_evaluated_at)
        )

    @classmethod
    def _valid_library_evidence(
        cls,
        evidence: EventResourceRelationshipEvidence,
        relationship: EventResourceRelationship,
        assertion: EventResourceAssertion,
        transition: EventResourceRelationshipTransition,
    ) -> bool:
        canonical = bytes(evidence.canonical_payload)
        values = cls._canonical_object(canonical)
        if values is None:
            return False
        try:
            payload = DeterminationPayload(**values)
            if canonical_payload_bytes(payload) != canonical:
                return False
            envelope = envelope_for(payload)
        except (TypeError, ValueError):
            return False
        expected = {
            EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY: (
                DeterminationKind.AUTHORITY.value,
                AuthorityResult.QUALIFIED.value,
            ),
            EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY: (
                DeterminationKind.LINKABILITY.value,
                LinkabilityResult.LINKABLE.value,
            ),
            EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY: (
                DeterminationKind.DISCLOSURE.value,
                DisclosureResult.CONTENT_VISIBLE.value,
            ),
        }.get(evidence.kind)
        if expected is None:
            return False
        actor_axis = evidence.kind == EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY
        viewer_axis = (
            evidence.kind
            == EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY
        )
        return (
            (payload.determination_kind, payload.result) == expected
            and evidence.schema_id == payload.schema_id
            and evidence.schema_version == payload.schema_version
            and evidence.canonicalization == payload.canonicalization
            and evidence.result == payload.result
            and evidence.policy_reference == payload.policy_reference
            and evidence.determination_reference == envelope.determination_reference
            and evidence.payload_sha256 == hashlib.sha256(canonical).hexdigest()
            and evidence.authority_binding_reference
            == payload.authority_binding_reference
            and evidence.provider_snapshot_reference
            == payload.provider_snapshot_reference
            and (str(evidence.actor_identity_id) if evidence.actor_identity_id else None)
            == payload.actor_identity_id
            and (
                str(evidence.actor_access_epoch)
                if evidence.actor_access_epoch is not None
                else None
            ) == payload.actor_access_epoch
            and (str(evidence.viewer_identity_id) if evidence.viewer_identity_id else None)
            == payload.viewer_identity_id
            and (
                str(evidence.viewer_access_epoch)
                if evidence.viewer_access_epoch is not None
                else None
            ) == payload.viewer_access_epoch
            and evidence.request_reference == payload.request_reference
            and evidence.consumer_reference == payload.consumer_reference
            and cls._canonical_time(evidence.evaluated_at) == payload.evaluated_at
            and payload.resource_id == relationship.library_resource.resource_id
            and payload.resource_version_pk == str(assertion.library_resource_version_id)
            and payload.version_number
            == str(assertion.library_resource_version.version_number)
            and payload.action == (transition.action if actor_axis else None)
            and payload.actor_identity_id
            == (str(transition.actor.identity_id) if actor_axis else None)
            and payload.actor_access_epoch
            == (str(transition.actor_access_epoch) if actor_axis else None)
            and payload.viewer_identity_id
            == (str(transition.actor.identity_id) if viewer_axis else None)
            and payload.viewer_access_epoch
            == (str(transition.actor_access_epoch) if viewer_axis else None)
            and payload.request_reference
            == (transition.request_reference if actor_axis else None)
            and payload.consumer_reference
            == (transition.consumer_reference if actor_axis else None)
        )

    @classmethod
    def _valid_relationship_disclosure(
        cls,
        evidence: EventResourceRelationshipEvidence,
        relationship: EventResourceRelationship,
        transition: EventResourceRelationshipTransition,
    ) -> bool:
        canonical = bytes(evidence.canonical_payload)
        payload = cls._canonical_object(canonical)
        predecessor = transition.from_assertion
        expected_fields = {
            "assertion_id", "binding_reference", "evaluated_at", "event_id",
            "existence_result", "policy_reference", "provider_snapshot_reference",
            "purpose", "relationship_id", "result", "state",
            "viewer_access_epoch", "viewer_identity_id",
        }
        if payload is None or set(payload) != expected_fields or predecessor is None:
            return False
        try:
            if canonical_event_payload_bytes(payload) != canonical:
                return False
        except (TypeError, ValueError):
            return False
        return (
            evidence.schema_id == EVENT_SCHEMA_ID
            and evidence.schema_version == EVENT_SCHEMA_VERSION
            and evidence.canonicalization == EVENT_CANONICALIZATION
            and evidence.result == RelationshipDisclosureResult.VISIBLE
            and evidence.policy_reference == DISCLOSURE_POLICY_REFERENCE
            and evidence.determination_reference == determination_reference_for(canonical)
            and evidence.payload_sha256 == hashlib.sha256(canonical).hexdigest()
            and evidence.actor_identity_id is None
            and evidence.actor_access_epoch is None
            and str(evidence.viewer_identity_id) == str(transition.actor.identity_id)
            and evidence.viewer_access_epoch == transition.actor_access_epoch
            and evidence.authority_binding_reference == payload["binding_reference"]
            and evidence.provider_snapshot_reference == payload["provider_snapshot_reference"]
            and payload["event_id"] == relationship.event.event_id
            and payload["relationship_id"] == relationship.relationship_id
            and payload["assertion_id"] == str(predecessor.pk)
            and payload["state"] == predecessor.state
            and payload["purpose"] == predecessor.purpose
            and payload["result"] == RelationshipDisclosureResult.VISIBLE
            and payload["existence_result"]
            == ExistenceDisclosureResult.EXISTENCE_VISIBLE
            and payload["viewer_identity_id"] == str(transition.actor.identity_id)
            and payload["viewer_access_epoch"] == str(transition.actor_access_epoch)
            and payload["evaluated_at"] == evidence.evaluated_at.isoformat()
        )

    @classmethod
    def _valid_correction(
        cls,
        evidence: EventResourceRelationshipEvidence,
        relationship: EventResourceRelationship,
        assertions: list[EventResourceAssertion],
        transition: EventResourceRelationshipTransition,
    ) -> bool:
        canonical = bytes(evidence.canonical_payload)
        payload = cls._canonical_object(canonical)
        expected_fields = {
            "correction_evidence_reference", "occurred_at", "rationale_reference",
            "reason", "survivor_assertion_id", "survivor_relationship_id",
            "transition_id",
        }
        if payload is None or set(payload) != expected_fields:
            return False
        try:
            if canonical_event_payload_bytes(payload) != canonical:
                return False
            reason = VoidReason(payload["reason"])
        except (TypeError, ValueError):
            return False
        event_evidence = transition.evidence.filter(
            kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY
        ).first()
        if reason is VoidReason.OTHER_GOVERNED_CORRECTION:
            return False
        if reason is VoidReason.DUPLICATE_ASSERTION:
            if payload["survivor_relationship_id"] == "NO_SURVIVOR":
                survivor_valid = (
                    payload["survivor_assertion_id"] is None
                    and type(payload["rationale_reference"]) is str
                )
            else:
                try:
                    survivor_id = int(payload["survivor_assertion_id"])
                except (TypeError, ValueError):
                    return False
                predecessor = transition.from_assertion
                survivor = next((item for item in assertions if item.pk == survivor_id), None)
                survivor_valid = (
                    payload["survivor_relationship_id"] == relationship.relationship_id
                    and survivor is not None
                    and predecessor is not None
                    and survivor.pk != predecessor.pk
                    and survivor.revision < predecessor.revision
                    and survivor.library_resource_version_id
                    == predecessor.library_resource_version_id
                    and survivor.purpose == predecessor.purpose
                )
        else:
            survivor_valid = (
                payload["survivor_relationship_id"] is None
                and payload["survivor_assertion_id"] is None
            )
        return (
            survivor_valid
            and event_evidence is not None
            and evidence.schema_id == EVENT_SCHEMA_ID
            and evidence.schema_version == EVENT_SCHEMA_VERSION
            and evidence.canonicalization == EVENT_CANONICALIZATION
            and evidence.result == reason.value
            and evidence.policy_reference == event_evidence.policy_reference
            and evidence.determination_reference == determination_reference_for(canonical)
            and evidence.payload_sha256 == hashlib.sha256(canonical).hexdigest()
            and str(evidence.actor_identity_id) == str(transition.actor.identity_id)
            and evidence.actor_access_epoch == transition.actor_access_epoch
            and evidence.viewer_identity_id is None
            and evidence.viewer_access_epoch is None
            and payload["transition_id"] == str(transition.pk)
            and payload["occurred_at"] == transition.occurred_at.isoformat()
            and cls._canonical_time(evidence.evaluated_at)
            == cls._canonical_time(transition.occurred_at)
        )

    @classmethod
    def _valid_evidence_lineage(
        cls,
        relationship: EventResourceRelationship,
        assertions: list[EventResourceAssertion],
        transitions: list[EventResourceRelationshipTransition],
    ) -> bool:
        required = {
            EventResourceRelationshipTransition.Action.CREATE: {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
            },
            EventResourceRelationshipTransition.Action.SUPERSEDE_VERSION: {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
            },
            EventResourceRelationshipTransition.Action.AMEND_PURPOSE: {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY,
                EventResourceRelationshipEvidence.Kind.RELATIONSHIP_DISCLOSURE_ELIGIBILITY,
            },
            EventResourceRelationshipTransition.Action.RETIRE: {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
            },
            EventResourceRelationshipTransition.Action.VOID: {
                EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
                EventResourceRelationshipEvidence.Kind.CORRECTION,
            },
        }
        for assertion, transition in zip(assertions, transitions, strict=True):
            evidence = list(transition.evidence.all())
            kinds = [item.kind for item in evidence]
            if len(kinds) != len(set(kinds)) or set(kinds) != required.get(
                transition.action
            ):
                return False
            for item in evidence:
                if item.kind == EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY:
                    valid = cls._valid_event_authority(
                        item, relationship, assertion, transition
                    )
                elif item.kind in {
                    EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                    EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
                    EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY,
                }:
                    valid = cls._valid_library_evidence(
                        item, relationship, assertion, transition
                    )
                elif item.kind == EventResourceRelationshipEvidence.Kind.RELATIONSHIP_DISCLOSURE_ELIGIBILITY:
                    valid = cls._valid_relationship_disclosure(
                        item, relationship, transition
                    )
                else:
                    valid = cls._valid_correction(
                        item, relationship, assertions, transition
                    )
                if not valid:
                    return False
        return True

    @staticmethod
    def _reconstruct(
        relationship: EventResourceRelationship,
    ) -> EventResourceAssertion | None:
        assertions = list(relationship.assertions.order_by("revision", "pk"))
        transitions = list(relationship.transitions.order_by("sequence", "pk"))
        if not assertions or len(assertions) != len(transitions):
            return None
        if relationship.head_assertion_id != assertions[-1].pk:
            return None
        for index, (assertion, transition) in enumerate(
            zip(assertions, transitions, strict=True),
            start=1,
        ):
            predecessor = assertions[index - 2] if index > 1 else None
            previous_transition = transitions[index - 2] if index > 1 else None
            if (
                assertion.relationship_id != relationship.pk
                or assertion.revision != index
                or assertion.predecessor_id != getattr(predecessor, "pk", None)
                or transition.relationship_id != relationship.pk
                or transition.sequence != index
                or transition.previous_transition_id
                != getattr(previous_transition, "pk", None)
                or transition.from_assertion_id != getattr(predecessor, "pk", None)
                or transition.resulting_assertion_id != assertion.pk
            ):
                return None
            if index == 1:
                if (
                    transition.action
                    != EventResourceRelationshipTransition.Action.CREATE
                    or assertion.state != EventResourceAssertion.State.CURRENT
                ):
                    return None
                continue
            if transition.action == EventResourceRelationshipTransition.Action.SUPERSEDE_VERSION:
                valid = (
                    assertion.library_resource_version_id
                    != predecessor.library_resource_version_id
                    and assertion.purpose == predecessor.purpose
                    and assertion.state == EventResourceAssertion.State.CURRENT
                    and transition.prior_disposition
                    == EventResourceAssertion.State.SUPERSEDED
                )
            elif transition.action == EventResourceRelationshipTransition.Action.AMEND_PURPOSE:
                valid = (
                    assertion.library_resource_version_id
                    == predecessor.library_resource_version_id
                    and assertion.purpose != predecessor.purpose
                    and assertion.state == EventResourceAssertion.State.CURRENT
                    and transition.prior_disposition
                    == EventResourceAssertion.State.CURRENT
                )
            elif transition.action == EventResourceRelationshipTransition.Action.RETIRE:
                valid = (
                    assertion.library_resource_version_id
                    == predecessor.library_resource_version_id
                    and assertion.purpose == predecessor.purpose
                    and assertion.state == EventResourceAssertion.State.RETIRED
                )
            elif transition.action == EventResourceRelationshipTransition.Action.VOID:
                valid = (
                    assertion.library_resource_version_id
                    == predecessor.library_resource_version_id
                    and assertion.purpose == predecessor.purpose
                    and assertion.state == EventResourceAssertion.State.VOIDED
                )
            else:
                valid = False
            if not valid:
                return None
        if not EventResourceRelationshipReadService._valid_evidence_lineage(
            relationship, assertions, transitions
        ):
            return None
        return assertions[-1]

    def _same_viewer(self, viewer: Identity) -> bool:
        try:
            current = Identity.objects.using(self.database_alias).select_related(
                "credential"
            ).get(pk=viewer.pk)
        except Identity.DoesNotExist:
            return False
        return (
            current.identity_id == viewer.identity_id
            and current.credential_id == viewer.credential_id
            and current.access_epoch == viewer.access_epoch
            and current.access_state == Identity.AccessState.ACTIVE
            and current.credential.is_active
        )

    def present(
        self,
        *,
        viewer: Identity,
        event: Event,
        evaluated_at: datetime,
    ) -> tuple[EventResourcePresentation, ...]:
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            return ()
        with transaction.atomic(using=self.database_alias):
            try:
                current_viewer = (
                    Identity.objects.using(self.database_alias)
                    .select_for_update(of=("self",))
                    .select_related("credential")
                    .get(
                        pk=viewer.pk,
                        identity_id=viewer.identity_id,
                        credential_id=viewer.credential_id,
                    )
                )
            except Identity.DoesNotExist:
                return ()
            if (
                current_viewer.access_epoch != viewer.access_epoch
                or current_viewer.access_state != Identity.AccessState.ACTIVE
                or not current_viewer.credential.is_active
            ):
                return ()
            candidates = EventResourceRelationship.objects.using(
                self.database_alias
            ).filter(event=event)
            visible = []
            for relationship in candidates:
                try:
                    head = self._reconstruct(relationship)
                    if head is None or head.state != EventResourceAssertion.State.CURRENT:
                        continue
                    version = head.library_resource_version
                    library = self.library_contract.determine_disclosure(
                        viewer_identity_id=current_viewer.identity_id,
                        resource_id=relationship.library_resource.resource_id,
                        version_number=version.version_number,
                        evaluated_at=evaluated_at,
                    )
                    if (
                        library.payload.result != DisclosureResult.CONTENT_VISIBLE
                        or library.payload.viewer_identity_id
                        != str(current_viewer.identity_id)
                        or library.payload.viewer_access_epoch
                        != str(current_viewer.access_epoch)
                        or not self._same_viewer(current_viewer)
                    ):
                        continue
                    event_disclosure = self.relationship_disclosure.determine_disclosure(
                        identity=current_viewer,
                        event_id=event.event_id,
                        relationship_id=relationship.relationship_id,
                        assertion_id=head.pk,
                        state=RelationshipState(head.state),
                        purpose=RelationshipPurpose(head.purpose),
                        evaluated_at=evaluated_at,
                    )
                    if (
                        event_disclosure.result is not RelationshipDisclosureResult.VISIBLE
                        or event_disclosure.existence_result
                        is not ExistenceDisclosureResult.EXISTENCE_VISIBLE
                        or event_disclosure.viewer_identity_id
                        != str(current_viewer.identity_id)
                        or event_disclosure.viewer_access_epoch
                        != current_viewer.access_epoch
                        or not self._same_viewer(current_viewer)
                    ):
                        continue
                    visible.append(
                        EventResourcePresentation(
                            content=version.content,
                            purpose=RelationshipPurpose(head.purpose).display,
                        )
                    )
                except (AttributeError, TypeError, ValueError):
                    continue
        visible.sort(key=lambda item: (item.purpose, item.content))
        return tuple(visible)


__all__ = [
    "EventResourcePresentation",
    "EventResourceRelationshipReadService",
]