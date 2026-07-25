"""Current-only, default-deny Event resource relationship projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.models import (
    Event,
    EventResourceAssertion,
    EventResourceRelationship,
    EventResourceRelationshipTransition,
    Identity,
)
from src.intevia.services.event_resource_relationship_contract import (
    EventRelationshipDisclosure,
    ExistenceDisclosureResult,
    RelationshipDisclosureResult,
    RelationshipPurpose,
    RelationshipState,
)
from src.intevia.services.library_exact_version_contract import (
    DisclosureResult,
    LibraryExactVersionContractService,
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
        return assertions[-1]

    def present(
        self,
        *,
        viewer: Identity,
        event: Event,
        evaluated_at: datetime,
    ) -> tuple[EventResourcePresentation, ...]:
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            return ()
        candidates = EventResourceRelationship.objects.using(self.database_alias).filter(
            event=event
        )
        visible = []
        for relationship in candidates:
            try:
                head = self._reconstruct(relationship)
                if head is None or head.state != EventResourceAssertion.State.CURRENT:
                    continue
                version = head.library_resource_version
                library = self.library_contract.determine_disclosure(
                    viewer_identity_id=viewer.identity_id,
                    resource_id=relationship.library_resource.resource_id,
                    version_number=version.version_number,
                    evaluated_at=evaluated_at,
                )
                if library.payload.result != DisclosureResult.CONTENT_VISIBLE:
                    continue
                event_disclosure = self.relationship_disclosure.determine_disclosure(
                    identity=viewer,
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