"""Subject-private readback for S013 PROFILE_EFFECT lineages."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.contrib.auth.models import User
from django.db import transaction

from src.intevia.services.profile_effect_contract import (
    EffectType,
    NEUTRAL_MESSAGE,
    ProjectionAction,
    ProposalAction,
    SubjectProfileEffectDispositionHistoryEntryDTO,
    SubjectProfileEffectProposalHistoryEntryDTO,
    SubjectProfileEffectReadDTO,
    SubjectRelation,
)
from src.intevia.services.profile_effect_service import (
    _ProfileEffectServiceBase,
    ProfileEffectActorError,
    ProfileEffectCrossEpochConflict,
    ProfileEffectMalformedReplay,
    ProfileEffectNotFound,
)


class ProfileEffectReadService(_ProfileEffectServiceBase):
    def read_subject_profile_effect_lineage(
        self,
        *,
        credential: User,
        viewer_access_epoch: int,
        lineage_id: UUID,
    ) -> SubjectProfileEffectReadDTO:
        with transaction.atomic(using=self._alias):
            actor = self._lock_actor(credential)
            if actor.access_epoch != viewer_access_epoch:
                raise ProfileEffectCrossEpochConflict("viewer access epoch mismatch")
            lineage = self._lock_lineage(lineage_id)
            self._validate_root_alias(lineage)
            if actor.pk != lineage.subject_id:
                raise ProfileEffectActorError("viewer must equal subject")
            transitions = self._lock_proposal_lineage_rows(lineage)
            grouped = self._lock_dispositions_by_transition(transitions)
            current_survivor = transitions[-1] if lineage.has_current_survivor else None
            current_projection_state = None
            disposition_history = []
            if current_survivor is not None:
                current_state = self._current_disposition_state(current_survivor, grouped)
                current_projection_state = current_state.state
                for row in grouped.get(current_survivor.pk, []):
                    disposition_history.append(
                        SubjectProfileEffectDispositionHistoryEntryDTO(
                            action=ProjectionAction(row.action),
                            actor_identity_id=row.actor.identity_id,
                            occurred_at=row.occurred_at,
                            lineage_reference=row.lineage_reference,
                        )
                    )
            proposal_history = tuple(
                SubjectProfileEffectProposalHistoryEntryDTO(
                    action=ProposalAction(row.action),
                    actor_identity_id=row.actor.identity_id,
                    occurred_at=row.occurred_at,
                    lineage_reference=row.lineage_reference,
                )
                for row in transitions
            )
            return SubjectProfileEffectReadDTO(
                lineage_id=lineage.lineage_id,
                source_activity_id=lineage.source_activity_id,
                source_transition_action="SUBMIT_WORK",
                source_transition_sequence=lineage.source_transition_sequence,
                source_transition_lineage_reference=lineage.source_transition_lineage_reference,
                subject_identity_id=lineage.subject.identity_id,
                subject_relation=SubjectRelation(lineage.subject_relation),
                effect_type=EffectType(lineage.effect_type),
                neutral_message=NEUTRAL_MESSAGE,
                proposal_history=proposal_history,
                has_current_survivor=lineage.has_current_survivor,
                current_projection_state=current_projection_state,
                disposition_history=tuple(disposition_history),
            )


__all__ = ["ProfileEffectReadService"]