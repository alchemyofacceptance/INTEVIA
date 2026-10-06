from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import unicodedata
from typing import Callable
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, connections, transaction
from django.utils.text import slugify

from core.models import (
    AuthorityBasis,
    AuthorityPrincipal,
    ContextualRoleAssignment,
    EssentialCoverageCase,
    EssentialCoverageRequirement,
    Identity,
    LivingOrganism,
    LOFoundingContentVersion,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismRoleDefinition,
    PlatformTermsAcceptance,
    PlatformTermsVersion,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    S015PreFoundingAuthorityRequest,
    QualifiedS015AuthorityDecision,
    canonical_json_bytes,
    canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    ReplayResultType,
    ReplayViewer,
    S015CommandReplayMismatch,
    _canonical_key,
    _empty_anchor_fingerprint,
    _lock_actor,
    _resulting_temporal_basis,
    acquire_organism_command_mutex,
    authority_atomic,
    serve_replay,
)
from src.intevia.services.organism_formation_authority import (
    founding_acceptance_uuid,
    founding_authority_request,
)
from src.intevia.services.organism_membership_service import _insert_membership_transition_row


_COMMAND_DOMAIN = b"INTEVIA:S015:FOUND_LIVING_ORGANISM:COMMAND:v1\x00"
_LINEAGE_DOMAIN = b"INTEVIA:S015:FOUND_LIVING_ORGANISM:LINEAGE:v1\x00"
_ACTION = OrganismAction.FOUND_LIVING_ORGANISM
_NAME_CONSTRAINT = "core_livingorganism_slug_key"


@dataclass(frozen=True, slots=True)
class FoundLivingOrganismCommand:
    credential: User
    name: str
    purpose: str
    request_reference: str
    idempotency_key: str
    accepted_terms_version_uuid: UUID | None = None
    vision: str | None = None
    mission: str | None = None
    values: str | None = None
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FoundLivingOrganismResult:
    living_organism_uuid: UUID
    event_uuid: UUID
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str]:
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "event_uuid": str(self.event_uuid),
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: FoundLivingOrganismResult) -> dict[str, str]:
        if type(result) is not cls:
            raise TypeError("founding replay result has the wrong type")
        return {}


def _result_from_payload(payload: object) -> FoundLivingOrganismResult:
    if type(payload) is not dict:
        raise S015CommandReplayMismatch("completed receipt does not match request")
    try:
        return FoundLivingOrganismResult(
            living_organism_uuid=UUID(payload["living_organism_uuid"]),
            event_uuid=UUID(payload["event_uuid"]),
            lineage_reference=payload["lineage_reference"],
            authority_reference=payload["authority_reference"],
            authority_decision_reference=payload["authority_decision_reference"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise S015CommandReplayMismatch("completed receipt does not match request") from exc


class OrganismFormationService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        authority: S015Authority | None = None,
        database_alias: str = "default",
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        if authority is None:
            authority = S015Authority(
                provider=ProductionApplicationAuthorityProvider(database_alias=database_alias),
                database_alias=database_alias,
            )
        if not isinstance(authority, S015Authority) or authority.database_alias != database_alias:
            raise TypeError("matching S015 authority is required")
        self._clock = clock
        self._authority = authority
        self._alias = database_alias

    def found_living_organism(
        self,
        command: FoundLivingOrganismCommand,
    ) -> FoundLivingOrganismResult:
        if connections[self._alias].in_atomic_block:
            raise S015FormationTechnicalFailure(
                "FOUND_LIVING_ORGANISM requires a top-level transaction; an enclosing atomic block "
                "would put founding rows in a savepoint and invalidate founding xmin provenance."
            )
        with authority_atomic(self._alias):
            try:
                actor = _lock_actor(
                    database_alias=self._alias,
                    credential=command.credential,
                    unavailable=S015FormationActorUnavailable,
                )
            except S015FormationActorUnavailable as exc:
                inactive_identity = (
                    Identity.objects.using(self._alias)
                    .select_related("credential")
                    .filter(credential=command.credential)
                    .first()
                )
                if inactive_identity is not None and (
                    inactive_identity.access_state != Identity.AccessState.ACTIVE
                    or not inactive_identity.credential.is_active
                ):
                    raise S015AuthorityDenied(
                        "The LO was not founded and nothing was saved: your account is not active "
                        "at the time of this act.",
                        refusal_code=S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                        disclosed_refusal_code=S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                    ) from exc
                raise
            request_reference = _canonical_key(command.request_reference, "request_reference", 128)
            idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
            received_at = self._timestamp(self._clock(), "received_at")
            effective_at = (
                received_at
                if command.effective_at is None
                else self._timestamp(command.effective_at, "effective_at")
            )
            if effective_at > received_at:
                raise ValueError("effective_at must not be later than received_at")
            name = self._required_text(command.name, "name")
            purpose = self._required_text(command.purpose, "purpose")
            vision = self._optional_text(command.vision, "vision")
            mission = self._optional_text(command.mission, "mission")
            values = self._optional_text(command.values, "values")
            terms_version_uuid = command.accepted_terms_version_uuid
            if terms_version_uuid is not None and type(terms_version_uuid) is not UUID:
                raise TypeError("accepted_terms_version_uuid must be a UUID or None")

            slug = slugify(name)[:90]
            if not slug:
                raise S015AuthorityDenied(
                    "The LO was not founded and nothing was saved: a name (and a purpose) is required.",
                    refusal_code=S015AuthorityRefusalCode.LO_NAME_REQUIRED,
                    disclosed_refusal_code=S015AuthorityRefusalCode.LO_NAME_REQUIRED,
                )
            acquire_organism_command_mutex(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                action=_ACTION,
                idempotency_key=idempotency_key,
            )
            fingerprint = self._command_fingerprint(
                actor=actor,
                name=name,
                purpose=purpose,
                vision=vision,
                mission=mission,
                values=values,
                accepted_terms_version_uuid=terms_version_uuid,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                effective_at=command.effective_at,
            )
            replay = self._attempt_replay(
                actor=actor,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if LivingOrganism.objects.using(self._alias).filter(slug=slug).exists():
                raise S015AuthorityDenied(
                    "The LO was not founded and nothing was saved: that name is already taken by another LO. Choose a different name.",
                    refusal_code=S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
                    disclosed_refusal_code=S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
                )

            proposed_organism_uuid = uuid4()
            request = founding_authority_request(
                database_alias=self._alias,
                actor_pk=actor.pk,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                proposed_organism_uuid=proposed_organism_uuid,
                accepted_terms_version_uuid=terms_version_uuid,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                effective_at=effective_at,
                received_at=received_at,
            )
            try:
                decision = self._authority.qualify(request)
            except S015AuthorityDenied as exc:
                message = self._authored_founding_refusal_message(exc.refusal_code)
                if message is None:
                    raise
                raise S015AuthorityDenied(
                    message,
                    refusal_code=exc.refusal_code,
                    disclosed_refusal_code=exc.disclosed_refusal_code,
                    authority_decision_reference=exc.authority_decision_reference,
                ) from exc
            try:
                return self._write_founding_batch(
                    actor=actor,
                    request=request,
                    decision=decision,
                    name=name,
                    purpose=purpose,
                    vision=vision,
                    mission=mission,
                    values=values,
                    slug=slug,
                    fingerprint=fingerprint,
                    effective_at=effective_at,
                    received_at=received_at,
                )
            except IntegrityError as exc:
                if self._constraint_name(exc) == _NAME_CONSTRAINT:
                    raise S015AuthorityDenied(
                        "The LO was not founded and nothing was saved: that name is already taken by another LO. Choose a different name.",
                        refusal_code=S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
                        disclosed_refusal_code=S015AuthorityRefusalCode.LO_NAME_UNAVAILABLE,
                    ) from exc
                raise

    def _write_founding_batch(
        self,
        *,
        actor: Identity,
        request: S015PreFoundingAuthorityRequest,
        decision: QualifiedS015AuthorityDecision,
        name: str,
        purpose: str,
        vision: str | None,
        mission: str | None,
        values: str | None,
        slug: str,
        fingerprint: str,
        effective_at: datetime | None,
        received_at: datetime,
    ) -> FoundLivingOrganismResult:
        authority_facts = json.loads(decision.authority_reference[len("s015a1:"):])
        rule_reference = authority_facts["rule"]
        registration_reference = authority_facts["registration"]
        registration_uuid = UUID(registration_reference.removeprefix("learner-registration:"))
        terms_version_uuid = UUID(rule_reference.removeprefix("platform-terms-version:").removesuffix("#founding"))
        organism_uuid = request.living_organism_id
        acceptance_uuid = founding_acceptance_uuid(organism_uuid)
        terms_version = PlatformTermsVersion.objects.using(self._alias).get(
            terms_version_uuid=terms_version_uuid,
        )
        principal = self._founding_principal()
        if principal is None:
            raise S015AuthorityDenied(
                self._founding_principal_absent_message(),
                refusal_code=S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT,
                disclosed_refusal_code=S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT,
                authority_decision_reference=decision.authority_decision_reference,
            )

        acceptance_reference = f"platform-terms-acceptance:{acceptance_uuid}"
        acceptance_commitment = "s015r1:" + hashlib.sha256(canonical_json_bytes({
            "terms_version_uuid": terms_version_uuid,
            "acceptance_kind": "FOUNDING",
            "subject_reference": f"living-organism:{organism_uuid}",
            "accepted_by_identity_id": actor.identity_id,
            "accepted_by_access_epoch": actor.access_epoch,
            "accepted_at": canonical_timestamp(effective_at),
        })).hexdigest()
        PlatformTermsAcceptance.objects.using(self._alias).create(
            acceptance_uuid=acceptance_uuid,
            terms_version=terms_version,
            acceptance_kind=PlatformTermsAcceptance.Kind.FOUNDING,
            subject_reference=f"living-organism:{organism_uuid}",
            accepted_by_identity=actor,
            accepted_by_access_epoch=actor.access_epoch,
            accepted_at=effective_at,
            received_at=received_at,
            recorded_at=None,
            acceptance_commitment=acceptance_commitment,
        )

        scope_fingerprint = hashlib.sha256(canonical_json_bytes({
            "organism_id": organism_uuid,
            "founder_identity_id": actor.identity_id,
            "founder_access_epoch": actor.access_epoch,
            "registration_uuid": registration_uuid,
        })).hexdigest()
        basis = AuthorityBasis.objects.using(self._alias).create(
            authority_principal=principal,
            derivation_root_principal=principal,
            issuer=actor,
            authority_class="FOUNDING",
            scope_fingerprint=scope_fingerprint,
            instrument_reference=rule_reference,
            evidence_reference=acceptance_reference,
            issued_at=effective_at,
            effective_at=effective_at,
            received_at=received_at,
        )

        content_uuid = uuid4()
        organism = LivingOrganism.objects.using(self._alias).create(
            organism_id=organism_uuid,
            slug=slug,
            state=LivingOrganism.State.FOUNDING_PENDING,
            current_state_effective_at=effective_at,
            state_known_at=received_at,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=self._alias,
                anchor_token="core_livingorganism",
                anchor_uuid=organism_uuid,
            ),
            constitutional_spine_reference=f"founding-content:{content_uuid}",
            founding_authority_basis=None,
            founder_identity=None,
            platform_root=False,
        )
        LOFoundingContentVersion.objects.using(self._alias).create(
            content_uuid=content_uuid,
            living_organism=organism,
            version=1,
            supersedes=None,
            name=name,
            purpose=purpose,
            vision=vision,
            mission=mission,
            values=values,
            actor_identity=actor,
            actor_access_epoch=actor.access_epoch,
            authority_decision_reference=decision.authority_decision_reference,
            effective_at=effective_at,
            received_at=received_at,
            recorded_at=None,
            content_commitment=None,
        )

        found_event_uuid = uuid4()
        self._insert_chain_event(
            table="core_livingorganismevent",
            anchor_column="living_organism_id",
            anchor_id=organism.pk,
            event_uuid=found_event_uuid,
            action="FOUND",
            prior_state=None,
            resulting_state="ACTIVE",
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            authority_basis_id=basis.pk,
            authority_decision_reference=decision.authority_decision_reference,
            evidence_reference=f"founding-content:{content_uuid}",
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )

        membership_uuid = uuid4()
        membership = OrganismMembership.objects.using(self._alias).create(
            membership_uuid=membership_uuid,
            identity=actor,
            living_organism=organism,
            state=OrganismMembership.State.ACTIVE,
            current_state_effective_at=effective_at,
            state_known_at=received_at,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=self._alias,
                anchor_token="core_organismmembership",
                anchor_uuid=membership_uuid,
            ),
        )
        LivingOrganism.objects.using(self._alias).filter(pk=organism.pk).update(
            founding_authority_basis=basis,
            founder_identity=actor,
        )

        batch_reference = f"founding-batch:{found_event_uuid}"
        temporal_kind, temporal_reference = _resulting_temporal_basis(
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        transition_common = {
            "database_alias": self._alias,
            "actor_id": actor.pk,
            "actor_state": "PARTY",
            "actor_capacity": "STANDING",
            "enterer_identity_id": actor.pk,
            "entry_mode": "TRANSPORT",
            "composing_rule_state": "NA_TRANSPORT",
            "composing_rule_reference": None,
            "composer_state": "NA_TRANSPORT",
            "composer_identity_id": None,
            "effective_until": None,
            "effective_until_state": "NONE",
            "l1_commitment": None,
            "l2_commitment": None,
            "actor_access_epoch": actor.access_epoch,
            "authority_basis_id": basis.pk,
            "authority_decision_reference": decision.authority_decision_reference,
            "evidence_reference": batch_reference,
            "request_reference": request.request_reference,
            "idempotency_key": request.idempotency_key,
            "payload_fingerprint": fingerprint,
            "temporal_basis_kind": temporal_kind,
            "temporal_basis_reference": temporal_reference,
            "occurred_at": effective_at,
            "effective_at": effective_at,
            "received_at": received_at,
            "reason_class": "FOUNDING",
        }
        open_event_uuid = uuid4()
        _insert_membership_transition_row(
            event_uuid=open_event_uuid,
            membership_id=membership.pk,
            sequence=1,
            predecessor_id=None,
            predecessor_sequence=None,
            action="OPEN",
            prior_state=None,
            resulting_state="PROPOSED",
            lineage_reference=self._lineage_reference("membership-open", open_event_uuid, fingerprint),
            **transition_common,
        )
        activate_event_uuid = uuid4()
        _insert_membership_transition_row(
            event_uuid=activate_event_uuid,
            membership_id=membership.pk,
            sequence=2,
            predecessor_id=self._membership_event_id(open_event_uuid),
            predecessor_sequence=1,
            action="ACTIVATE",
            prior_state="PROPOSED",
            resulting_state="ACTIVE",
            lineage_reference=self._lineage_reference("membership-activate", activate_event_uuid, fingerprint),
            **transition_common,
        )

        role = OrganismRoleDefinition.objects.using(self._alias).create(
            role_uuid=uuid4(),
            living_organism=organism,
            code="COORDINATOR",
            scope=OrganismRoleDefinition.Scope.LIVING_ORGANISM,
            definition_version=1,
            active=True,
        )
        assignment_uuid = uuid4()
        assignment = ContextualRoleAssignment.objects.using(self._alias).create(
            assignment_uuid=assignment_uuid,
            membership=membership,
            role_definition=role,
            circle=None,
            state=ContextualRoleAssignment.State.ACTIVE,
            current_state_effective_at=effective_at,
            state_known_at=received_at,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=self._alias,
                anchor_token="core_contextualroleassignment",
                anchor_uuid=assignment_uuid,
            ),
        )
        assignment_event_uuid = uuid4()
        self._insert_chain_event(
            table="core_contextualroleassignmentstateevent",
            anchor_column="assignment_id",
            anchor_id=assignment.pk,
            event_uuid=assignment_event_uuid,
            action="ASSIGN",
            prior_state=None,
            resulting_state="ACTIVE",
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            authority_basis_id=basis.pk,
            authority_decision_reference=decision.authority_decision_reference,
            evidence_reference=batch_reference,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )

        requirement = EssentialCoverageRequirement.objects.using(self._alias).create(
            requirement_uuid=uuid4(),
            roster_version=1,
            responsibility_domain_code="GOVERNANCE",
            qualifying_role_code="COORDINATOR",
            minimum_active_occupants=1,
            scope="LIVING_ORGANISM",
            effective_from=effective_at,
            effective_until=None,
            living_organism=organism,
        )
        coverage_case = EssentialCoverageCase.objects.using(self._alias).create(
            case_uuid=uuid4(),
            living_organism=organism,
        )
        self._insert_chain_event(
            table="core_coverageassessment",
            anchor_column="case_id",
            anchor_id=coverage_case.pk,
            event_uuid=uuid4(),
            action="ASSESS",
            prior_state=None,
            resulting_state="SATISFIED",
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            authority_basis_id=basis.pk,
            authority_decision_reference=decision.authority_decision_reference,
            evidence_reference=batch_reference,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            payload_fingerprint=fingerprint,
            effective_at=effective_at,
            received_at=received_at,
            extra={
                "roster_version": requirement.roster_version,
                "evaluated_state_at": effective_at,
                "evaluated_known_at": received_at,
                "determiner_capacity_reference": batch_reference,
            },
        )

        result_lineage = self._lineage_reference("receipt", found_event_uuid, fingerprint)
        result = FoundLivingOrganismResult(
            living_organism_uuid=organism_uuid,
            event_uuid=found_event_uuid,
            lineage_reference=result_lineage,
            authority_reference=decision.authority_reference,
            authority_decision_reference=decision.authority_decision_reference,
        )
        OrganismCommandReceipt.objects.using(self._alias).create(
            actor=actor,
            action=_ACTION.value,
            idempotency_key=request.idempotency_key,
            request_reference=request.request_reference,
            payload_fingerprint=fingerprint,
            authority_decision_reference=decision.authority_decision_reference,
            lineage_reference=result_lineage,
            result_payload=result.receipt_payload(),
        )
        self._refresh("core_livingorganism", organism.pk, effective_at)
        self._refresh("core_organismmembership", membership.pk, effective_at)
        self._refresh("core_contextualroleassignment", assignment.pk, effective_at)
        return serve_replay(
            result,
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            result_type=ReplayResultType(FoundLivingOrganismResult, self._alias),
        )

    def _attempt_replay(
        self,
        *,
        actor: Identity,
        request_reference: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> FoundLivingOrganismResult | None:
        receipt = (
            OrganismCommandReceipt.objects.using(self._alias)
            .select_for_update()
            .filter(actor=actor, action=_ACTION.value, idempotency_key=idempotency_key)
            .first()
        )
        if receipt is None:
            return None
        if receipt.request_reference != request_reference or receipt.payload_fingerprint != fingerprint:
            raise S015CommandReplayMismatch("completed receipt does not match request")
        return serve_replay(
            _result_from_payload(receipt.result_payload),
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            result_type=ReplayResultType(FoundLivingOrganismResult, self._alias),
        )

    def _command_fingerprint(
        self,
        *,
        actor: Identity,
        name: str,
        purpose: str,
        vision: str | None,
        mission: str | None,
        values: str | None,
        accepted_terms_version_uuid: UUID | None,
        request_reference: str,
        idempotency_key: str,
        effective_at: datetime,
    ) -> str:
        payload = {
            "action": _ACTION.value,
            "actor_access_epoch": actor.access_epoch,
            "actor_identity_id": actor.identity_id,
            "effective_at": (
                None if effective_at is None else canonical_timestamp(effective_at)
            ),
            "idempotency_key": idempotency_key,
            "name": name,
            "purpose": purpose,
            "request_reference": request_reference,
            "terms_version_uuid": accepted_terms_version_uuid,
            "values": values,
            "vision": vision,
            "mission": mission,
        }
        return hashlib.sha256(_COMMAND_DOMAIN + canonical_json_bytes(payload)).hexdigest()

    def _insert_chain_event(
        self,
        *,
        table: str,
        anchor_column: str,
        anchor_id: int,
        event_uuid: UUID,
        action: str,
        prior_state: str | None,
        resulting_state: str,
        actor: Identity,
        actor_access_epoch: int,
        authority_basis_id: int,
        authority_decision_reference: str,
        evidence_reference: str,
        request_reference: str,
        idempotency_key: str,
        payload_fingerprint: str,
        effective_at: datetime,
        received_at: datetime,
        extra: dict[str, object] | None = None,
    ) -> int:
        allowed = {
            "core_livingorganismevent": ("living_organism_id", "resulting_state", ()),
            "core_contextualroleassignmentstateevent": ("assignment_id", "resulting_state", ()),
            "core_coverageassessment": (
                "case_id",
                "result",
                ("roster_version", "evaluated_state_at", "evaluated_known_at", "determiner_capacity_reference"),
            ),
        }
        if table not in allowed or allowed[table][0] != anchor_column:
            raise ValueError("unsupported founding event table")
        state_column = allowed[table][1]
        extra_columns = allowed[table][2]
        extra = extra or {}
        if set(extra) != set(extra_columns):
            if extra_columns or extra:
                raise ValueError("founding event fields do not match table")
        temporal_kind, temporal_reference = _resulting_temporal_basis(
            payload_fingerprint=payload_fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        columns: dict[str, object] = {
            "event_uuid": event_uuid,
            anchor_column: anchor_id,
            "sequence": 1,
            "predecessor_id": None,
            "predecessor_sequence": None,
            "action": action,
            "prior_state": prior_state,
            state_column: resulting_state,
            "actor_id": actor.pk,
            "actor_state": "PARTY",
            "actor_capacity": "STANDING",
            "enterer_identity_id": actor.pk,
            "entry_mode": "TRANSPORT",
            "composing_rule_state": "NA_TRANSPORT",
            "composing_rule_reference": None,
            "composer_state": "NA_TRANSPORT",
            "composer_identity_id": None,
            "effective_until": None,
            "effective_until_state": "NONE",
            "l2_commitment": None,
            "actor_access_epoch": actor_access_epoch,
            "authority_basis_id": authority_basis_id,
            "authority_decision_reference": authority_decision_reference,
            "evidence_reference": evidence_reference,
            "request_reference": request_reference,
            "idempotency_key": idempotency_key,
            "payload_fingerprint": payload_fingerprint,
            "lineage_reference": self._lineage_reference(action, event_uuid, payload_fingerprint),
            "temporal_basis_kind": temporal_kind,
            "temporal_basis_reference": temporal_reference,
            "occurred_at": effective_at,
            "effective_at": effective_at,
            "received_at": received_at,
            "recorded_at": None,
        }
        columns.update(extra)
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_0022_digest('s015p1', "
                "public.s015_0022_parts_preimage(%s, %s::uuid))",
                [table, event_uuid],
            )
            columns["parts_commitment"] = cursor.fetchone()[0]
            columns["predecessor_l1_commitment"] = None
            names = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))
            cursor.execute(
                f"INSERT INTO public.{table} ({names}) VALUES ({placeholders}) RETURNING id",
                list(columns.values()),
            )
            return cursor.fetchone()[0]

    def _membership_event_id(self, event_uuid: UUID) -> int:
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT id FROM public.core_organismmembershiptransition WHERE event_uuid = %s",
                [event_uuid],
            )
            row = cursor.fetchone()
        if row is None:
            raise S015FormationTechnicalFailure("membership opening event was not persisted")
        return row[0]

    def _founding_principal(self) -> AuthorityPrincipal | None:
        from src.intevia.services.organism_authority import _founding_authority_principal

        return _founding_authority_principal(self._alias)

    def _founding_principal_absent_message(self) -> str:
        fixture_substitute_exists = AuthorityPrincipal.objects.using(self._alias).filter(
            governed_source_namespace="INTEVIA_FIXTURE_AUTHORITY_V1",
        ).exists()
        if fixture_substitute_exists and not settings.INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED:
            return "the labelled development substitute is not permitted in this environment"
        return (
            "The LO was not founded: the platform's founding authority principal is not registered "
            "on this database (the GENESIS principal, act 1 of the bootstrap). Register it (or, in "
            "development, seed it labelled), then found."
        )

    def _authored_founding_refusal_message(
        self,
        code: S015AuthorityRefusalCode | None,
    ) -> str | None:
        if code is S015AuthorityRefusalCode.FOUNDING_PRINCIPAL_ABSENT:
            return self._founding_principal_absent_message()
        messages = {
            S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED: (
                "The LO was not founded and nothing was saved. INTEVIA records which platform-terms "
                "version every founder accepts, and no acceptance of the version in force was "
                "received. Read the terms shown and confirm them, then found again."
            ),
            S015AuthorityRefusalCode.FOUNDING_TERMS_VERSION_SUPERSEDED: (
                "The LO was not founded and nothing was saved. The terms version you accepted is no "
                "longer the version in force. Reload to see the current terms and confirm them."
            ),
            S015AuthorityRefusalCode.E_NOT_REGISTERED: (
                "The LO was not founded and nothing was saved. Founding is a registered Learner's "
                "act, and no governed registration is effective for your account at this time. "
                "(Development: the setup command's account has no registration; provision through "
                "the supported path.)"
            ),
            S015AuthorityRefusalCode.E_IDENTITY_INACTIVE: (
                "The LO was not founded and nothing was saved: your account is not active at the "
                "time of this act."
            ),
        }
        return messages.get(code)

    def _refresh(self, anchor_token: str, anchor_id: int, effective_at: datetime) -> None:
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                [anchor_token, anchor_id, effective_at],
            )
            cursor.fetchone()

    @staticmethod
    def _lineage_reference(kind: str, event_uuid: UUID, fingerprint: str) -> str:
        payload = canonical_json_bytes({
            "event_uuid": event_uuid,
            "fingerprint": fingerprint,
            "kind": kind,
        })
        return "s015l1:" + hashlib.sha256(_LINEAGE_DOMAIN + payload).hexdigest()

    @staticmethod
    def _constraint_name(exc: IntegrityError) -> str | None:
        cause = exc.__cause__
        diagnostics = getattr(cause, "diag", None)
        return getattr(diagnostics, "constraint_name", None)

    @staticmethod
    def _timestamp(value: object, name: str) -> datetime:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise TypeError(f"{name} must be timezone-aware")
        return value

    @staticmethod
    def _required_text(value: object, name: str) -> str:
        if type(value) is not str:
            raise TypeError(f"{name} must be a string")
        normalized = unicodedata.normalize("NFC", value)
        if not normalized.strip():
            raise S015AuthorityDenied(
                "The LO was not founded and nothing was saved: a name (and a purpose) is required.",
                refusal_code=S015AuthorityRefusalCode.LO_NAME_REQUIRED,
                disclosed_refusal_code=S015AuthorityRefusalCode.LO_NAME_REQUIRED,
            )
        if normalized != value:
            raise ValueError(f"{name} must use canonical Unicode")
        if name == "name" and len(value) > 90:
            raise ValueError("name exceeds the supported length")
        return value

    @staticmethod
    def _optional_text(value: object, name: str) -> str | None:
        if value is None:
            return None
        if type(value) is not str:
            raise TypeError(f"{name} must be a string or None")
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError(f"{name} must use canonical Unicode")
        return value


class S015FormationActorUnavailable(Exception):
    pass


class S015FormationTechnicalFailure(Exception):
    pass


__all__ = [
    "FoundLivingOrganismCommand",
    "FoundLivingOrganismResult",
    "OrganismFormationService",
    "OrganismPhaseAdvanceService",
    "PhaseAdvanceCommand",
    "PhaseAdvanceResult",
    "PhaseReadiness",
    "PhaseReadinessQuery",
    "S015PhaseAdvanceNotFound",
    "S015PhaseAdvanceTechnicalFailure",
    "S015PhaseAdvanceUnavailable",
    "S015FormationActorUnavailable",
    "S015FormationTechnicalFailure",
]

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Callable
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import connections

from core.models import (
    LivingOrganism,
    LivingOrganismEvent,
    LOFoundingContentVersion,
    LOGovernanceConfigurationVersion,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
    S015PostFoundingAuthorityRequest,
    authority_target_fingerprint,
    canonical_json_bytes,
)
from src.intevia.services.organism_command_support import (
    _OrganismCommandService,
    _canonical_key,
    _canonical_uuid,
    _command_payload,
    _lock_actor,
    _lock_actor_membership_context,
    _lock_registered_organism,
    _resulting_temporal_basis,
    _validate_timestamp,
    ReplayViewer,
    S015CommandReplayMismatch,
    acquire_organism_command_mutex,
    authority_atomic,
)


_PHASE_SUBTYPE = "TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE"
_PHASE_COMMAND_DOMAIN = b"INTEVIA:S015:TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE:COMMAND:v1\x00"
_PHASE_NAMES = {
    "CONCEIVING": "Conceiving",
    "GERMINATING": "Germinating",
    "BIRTHING": "Birthing",
    "BIRTHED": "Birthed",
}
_NEXT_PHASE = {
    "CONCEIVING": "GERMINATING",
    "GERMINATING": "BIRTHING",
    "BIRTHING": "BIRTHED",
}


@dataclass(frozen=True, slots=True)
class PhaseAdvanceCommand:
    credential: User
    living_organism_uuid: UUID
    from_phase: str
    to_phase: str
    request_reference: str
    idempotency_key: str
    effective_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PhaseReadinessQuery:
    living_organism_uuid: UUID
    from_phase: str
    to_phase: str
    effective_at: datetime
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class PhaseReadiness:
    ready: bool
    current_phase: str
    refusal_code: S015AuthorityRefusalCode | None
    missing_provision: str | None


@dataclass(frozen=True, slots=True)
class PhaseAdvanceResult:
    living_organism_uuid: UUID
    event_uuid: UUID
    event_sequence: int
    from_phase: str
    to_phase: str
    lineage_reference: str
    authority_reference: str
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, str | int]:
        return {
            "living_organism_uuid": str(self.living_organism_uuid),
            "event_uuid": str(self.event_uuid),
            "event_sequence": self.event_sequence,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "lineage_reference": self.lineage_reference,
            "authority_reference": self.authority_reference,
            "authority_decision_reference": self.authority_decision_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: PhaseAdvanceResult) -> dict[str, str]:
        if type(result) is not cls:
            raise TypeError("phase-advance replay result has the wrong type")
        return {}


def _phase_result_from_payload(payload: object) -> PhaseAdvanceResult:
    if type(payload) is not dict:
        raise S015CommandReplayMismatch("completed receipt does not match request")
    try:
        return PhaseAdvanceResult(
            living_organism_uuid=UUID(payload["living_organism_uuid"]),
            event_uuid=UUID(payload["event_uuid"]),
            event_sequence=payload["event_sequence"],
            from_phase=payload["from_phase"],
            to_phase=payload["to_phase"],
            lineage_reference=payload["lineage_reference"],
            authority_reference=payload["authority_reference"],
            authority_decision_reference=payload["authority_decision_reference"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise S015CommandReplayMismatch("completed receipt does not match request") from exc


class S015PhaseAdvanceNotFound(Exception):
    pass


class S015PhaseAdvanceUnavailable(Exception):
    pass


class S015PhaseAdvanceTechnicalFailure(Exception):
    pass


class OrganismPhaseAdvanceService(_OrganismCommandService):
    _action = OrganismAction.TRANSITION_LIVING_ORGANISM
    _not_found = S015PhaseAdvanceNotFound
    _authority_unavailable = S015PhaseAdvanceUnavailable

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        authority: S015Authority | None = None,
        database_alias: str = "default",
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        if authority is None:
            authority = S015Authority(
                provider=ProductionApplicationAuthorityProvider(database_alias=database_alias),
                database_alias=database_alias,
            )
        super().__init__(authority=authority, clock=clock, database_alias=database_alias)

    def _replay_result(self, payload: object) -> PhaseAdvanceResult:
        return _phase_result_from_payload(payload)

    def assess_readiness(self, query: PhaseReadinessQuery) -> PhaseReadiness:
        organism_uuid = _canonical_uuid(query.living_organism_uuid, "living_organism_uuid")
        from_phase = _canonical_key(query.from_phase, "from_phase", 24)
        to_phase = _canonical_key(query.to_phase, "to_phase", 24)
        effective_at = _validate_timestamp(query.effective_at, "effective_at")
        evaluated_at = _validate_timestamp(query.evaluated_at, "evaluated_at")
        if effective_at > evaluated_at:
            raise ValueError("effective_at must not be later than evaluated_at")
        try:
            organism = LivingOrganism.objects.using(self._alias).get(organism_id=organism_uuid)
        except LivingOrganism.DoesNotExist as exc:
            raise S015PhaseAdvanceNotFound("living organism not found") from exc

        current_phase = self._phase_at(organism, effective_at, evaluated_at)
        if (
            (from_phase, to_phase) not in {
                ("CONCEIVING", "GERMINATING"),
                ("GERMINATING", "BIRTHING"),
            }
            or from_phase != current_phase
            or self._birthing_recorded(organism, effective_at, evaluated_at)
        ):
            return PhaseReadiness(
                False,
                current_phase,
                S015AuthorityRefusalCode.PHASE_OUT_OF_ORDER,
                None,
            )

        if to_phase == "GERMINATING":
            if not self._founding_content_ready(organism, effective_at, evaluated_at):
                return PhaseReadiness(
                    False,
                    current_phase,
                    S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                    "founding content (name and purpose)",
                )
            return PhaseReadiness(True, current_phase, None, None)

        configuration = self._effective_governance_configuration(
            organism,
            effective_at,
            evaluated_at,
        )
        if configuration is None:
            return PhaseReadiness(
                False,
                current_phase,
                S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                "an effective governance configuration",
            )
        if configuration.governance_enabled is None:
            return PhaseReadiness(
                False,
                current_phase,
                S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                "GOVERNANCE choice",
            )
        if configuration.governance_enabled:
            return PhaseReadiness(
                False,
                current_phase,
                S015AuthorityRefusalCode.SB_IL_GOVCONST,
                "GOVERNANCE_CONSTITUTION",
            )
        return PhaseReadiness(True, current_phase, None, None)

    def advance_phase(self, command: PhaseAdvanceCommand) -> PhaseAdvanceResult:
        if connections[self._alias].in_atomic_block:
            raise S015PhaseAdvanceTechnicalFailure(
                "PHASE_ADVANCE could not be completed because of a technical problem; "
                "nothing was saved. Try again; if it persists, report it."
            )

        with authority_atomic(self._alias):
            actor = _lock_actor(
                database_alias=self._alias,
                credential=command.credential,
                unavailable=S015PhaseAdvanceUnavailable,
            )
            organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
            from_phase = _canonical_key(command.from_phase, "from_phase", 24)
            to_phase = _canonical_key(command.to_phase, "to_phase", 24)
            request_reference = _canonical_key(command.request_reference, "request_reference", 128)
            idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
            received_at = _validate_timestamp(self._clock(), "received_at")
            effective_at = (
                received_at
                if command.effective_at is None
                else _validate_timestamp(command.effective_at, "effective_at")
            )
            if effective_at > received_at:
                raise ValueError("effective_at must not be later than received_at")

            target = {
                "action": self._action.value,
                "organism_uuid": organism_uuid,
                "from_phase": from_phase,
                "to_phase": to_phase,
            }
            fingerprint = _command_payload(
                domain=_PHASE_COMMAND_DOMAIN,
                schema="s015-phase-advance-v1",
                action=self._action,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                occurred_at=effective_at,
                target=target,
                command={
                    "effective_at": effective_at,
                    "received_at": received_at,
                    "schema": "s015-phase-advance-v1",
                },
            )
            acquire_organism_command_mutex(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                action=self._action,
                idempotency_key=idempotency_key,
            )
            replay = self._attempt_replay(
                actor=actor,
                request_reference=request_reference,
                payload_fingerprint=fingerprint,
                idempotency_key=idempotency_key,
                viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
            )
            if replay is not None:
                return replay

            _lock_registered_organism(
                database_alias=self._alias,
                organism_id=organism_uuid,
            )
            try:
                organism = LivingOrganism.objects.using(self._alias).select_for_update().get(
                    organism_id=organism_uuid,
                )
            except LivingOrganism.DoesNotExist as exc:
                raise S015PhaseAdvanceNotFound("living organism not found") from exc
            actor_membership, role_refs = _lock_actor_membership_context(
                database_alias=self._alias,
                actor_identity_id=actor.identity_id,
                living_organism_id=organism_uuid,
                unavailable=S015PhaseAdvanceUnavailable,
            )
            request = S015PostFoundingAuthorityRequest(
                database_alias=self._alias,
                actor_pk=actor.pk,
                actor_identity_id=actor.identity_id,
                actor_access_epoch=actor.access_epoch,
                action=self._action,
                command_subtype=_PHASE_SUBTYPE,
                target_fingerprint=authority_target_fingerprint(target),
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                evaluated_at=received_at,
                active_membership_head_reference=actor_membership.head_transition.lineage_reference,
                contextual_role_head_references=role_refs,
                effective_at=effective_at,
                target=target,
                living_organism_id=organism_uuid,
            )
            try:
                decision = self._authority.qualify(request)
            except S015AuthorityDenied as exc:
                if exc.refusal_code in {
                    S015AuthorityRefusalCode.PHASE_OUT_OF_ORDER,
                    S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                    S015AuthorityRefusalCode.SB_IL_GOVCONST,
                    S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED,
                }:
                    raise S015AuthorityDenied(
                        self._authored_refusal_message(
                            exc.refusal_code,
                            organism,
                            to_phase,
                            effective_at,
                            received_at,
                        ),
                        refusal_code=exc.refusal_code,
                        disclosed_refusal_code=exc.disclosed_refusal_code,
                        authority_decision_reference=exc.authority_decision_reference,
                    ) from exc
                raise

            if organism.head_event_id is None or organism.founding_authority_basis_id is None:
                raise S015PhaseAdvanceTechnicalFailure(
                    "PHASE_ADVANCE could not be completed because of a technical problem; "
                    "nothing was saved. Try again; if it persists, report it."
                )
            predecessor = LivingOrganismEvent.objects.using(self._alias).select_for_update().get(
                pk=organism.head_event_id,
            )
            event_uuid = uuid4()
            temporal_kind, temporal_reference = _resulting_temporal_basis(
                payload_fingerprint=fingerprint,
                effective_at=effective_at,
                received_at=received_at,
            )
            lineage_reference = "s015l1:" + hashlib.sha256(
                _PHASE_COMMAND_DOMAIN + canonical_json_bytes({
                    "event": event_uuid,
                    "organism": organism_uuid,
                    "fingerprint": fingerprint,
                })
            ).hexdigest()
            with connections[self._alias].cursor() as cursor:
                cursor.execute(
                    "SELECT l1_commitment FROM public.core_livingorganismevent WHERE id = %s",
                    [predecessor.pk],
                )
                predecessor_commitment = cursor.fetchone()
                if predecessor_commitment is None or predecessor_commitment[0] is None:
                    raise S015PhaseAdvanceTechnicalFailure(
                        "PHASE_ADVANCE could not be completed because of a technical problem; "
                        "nothing was saved. Try again; if it persists, report it."
                    )
                cursor.execute(
                    "SELECT public.s015_0022_digest('s015p1', "
                    "public.s015_0022_parts_preimage(%s, %s::uuid))",
                    ["core_livingorganismevent", event_uuid],
                )
                parts_commitment = cursor.fetchone()[0]
                columns = {
                    "event_uuid": event_uuid,
                    "living_organism_id": organism.pk,
                    "sequence": predecessor.sequence + 1,
                    "predecessor_id": predecessor.pk,
                    "predecessor_sequence": predecessor.sequence,
                    "action": "PHASE_ADVANCE",
                    "prior_state": predecessor.resulting_state,
                    "resulting_state": predecessor.resulting_state,
                    "actor_id": None,
                    "actor_state": "LIVING_ORGANISM",
                    "actor_capacity": "STANDING",
                    "actor_access_epoch": None,
                    "enterer_identity_id": actor.pk,
                    "entry_mode": "TRANSPORT",
                    "composing_rule_state": "NA_TRANSPORT",
                    "composing_rule_reference": None,
                    "composer_state": "NA_TRANSPORT",
                    "composer_identity_id": None,
                    "effective_until": None,
                    "effective_until_state": "NONE",
                    "authority_basis_id": organism.founding_authority_basis_id,
                    "authority_decision_reference": decision.authority_decision_reference,
                    "evidence_reference": f"formation-phase:{from_phase}->{to_phase}",
                    "request_reference": request_reference,
                    "idempotency_key": idempotency_key,
                    "payload_fingerprint": fingerprint,
                    "lineage_reference": lineage_reference,
                    "temporal_basis_kind": temporal_kind,
                    "temporal_basis_reference": temporal_reference,
                    "occurred_at": effective_at,
                    "effective_at": effective_at,
                    "received_at": received_at,
                    "predecessor_l1_commitment": predecessor_commitment[0],
                    "parts_commitment": parts_commitment,
                }
                cursor.execute(
                    f"INSERT INTO public.core_livingorganismevent ({', '.join(columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING id",
                    list(columns.values()),
                )
                event_id = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                    ["core_livingorganism", organism.pk, effective_at],
                )
            event = LivingOrganismEvent.objects.using(self._alias).get(pk=event_id)
            result = PhaseAdvanceResult(
                living_organism_uuid=organism_uuid,
                event_uuid=event.event_uuid,
                event_sequence=event.sequence,
                from_phase=from_phase,
                to_phase=to_phase,
                lineage_reference=lineage_reference,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.authority_decision_reference,
            )
            return self._finalise(
                actor=actor,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
                result=result,
            )

    def _phase_at(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> str:
        evidence_reference = LivingOrganismEvent.objects.using(self._alias).filter(
            living_organism=organism,
            action="PHASE_ADVANCE",
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).order_by(
            "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
        ).values_list("evidence_reference", flat=True).first()
        return (
            "CONCEIVING"
            if evidence_reference is None
            else evidence_reference.rsplit(":", 1)[1].split("->", 1)[1]
        )

    def _birthing_recorded(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> bool:
        return LivingOrganismEvent.objects.using(self._alias).filter(
            living_organism=organism,
            action="BIRTHING",
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).exists()

    def _founding_content_ready(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> bool:
        content = LOFoundingContentVersion.objects.using(self._alias).filter(
            living_organism=organism,
            version=1,
            effective_at__lte=effective_at,
            recorded_at__lte=evaluated_at,
        ).first()
        return content is not None and bool(content.name.strip()) and bool(content.purpose.strip())

    def _effective_governance_configuration(
        self,
        organism: LivingOrganism,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> LOGovernanceConfigurationVersion | None:
        evidence_reference = LivingOrganismEvent.objects.using(self._alias).filter(
            living_organism=organism,
            action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION.value,
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).order_by(
            "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
        ).values_list("evidence_reference", flat=True).first()
        prefix = "governance-configuration-version:"
        if evidence_reference is None or not evidence_reference.startswith(prefix):
            return None
        try:
            version_uuid = UUID(evidence_reference[len(prefix):])
        except ValueError:
            return None
        return LOGovernanceConfigurationVersion.objects.using(self._alias).filter(
            version_uuid=version_uuid,
            living_organism=organism,
        ).first()

    def _authored_refusal_message(
        self,
        code: S015AuthorityRefusalCode,
        organism: LivingOrganism,
        to_phase: str,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> str:
        current_phase = self._phase_at(organism, effective_at, evaluated_at)
        if code is S015AuthorityRefusalCode.PHASE_OUT_OF_ORDER:
            next_phase = _NEXT_PHASE.get(current_phase, to_phase)
            return (
                f"The phase was not advanced and nothing changed: this LO is in "
                f"{_PHASE_NAMES.get(current_phase, current_phase)}, and phases advance one at a time "
                "(Conceiving → Germinating → Birthing → Birthed). "
                f"Advance to {_PHASE_NAMES.get(next_phase, next_phase)} first."
            )
        if code is S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED:
            return (
                "This LO's electorate has more than one member, so this act needs the electorate's "
                "qualified decision, and no CQ route is operative in this release (S3-IL-CQ). "
                "Nothing changed. This is a known limit, not a refusal of the act; it lifts when "
                "DISCUSSION's CQ is delivered."
            )
        if code is S015AuthorityRefusalCode.SB_IL_GOVCONST:
            return (
                "The phase was not advanced and nothing changed: this LO has GOVERNANCE enabled, so "
                "moving to Birthing needs its GOVERNANCE Circle and that Circle's constitutional "
                "admission route, with its deciding body, to be prescribed, adopted and recorded. "
                "Those records cannot be made in this release. This is a known limit of the current "
                "build, not a refusal of the LO's choice; it lifts when GOVERNANCE formation is "
                "delivered. The LO's other formation work can continue."
            )
        if to_phase == "GERMINATING":
            provision = "founding content (name and purpose)"
            return (
                f"The phase was not advanced and nothing changed: moving to "
                f"{_PHASE_NAMES.get(to_phase, to_phase)} needs {provision}, which is not yet "
                "established. Establish it, then advance."
            )
        configuration = self._effective_governance_configuration(
            organism,
            effective_at,
            evaluated_at,
        )
        if configuration is None:
            provision = "an effective governance configuration"
            return (
                f"The phase was not advanced and nothing changed: moving to "
                f"{_PHASE_NAMES.get(to_phase, to_phase)} needs {provision}, which is not yet "
                "established. Establish it, then advance."
            )
        if configuration.governance_enabled is None:
            return (
                "The phase was not advanced and nothing changed. Moving to Birthing needs this LO's "
                "GOVERNANCE choice to be confirmed in its governance configuration, and no choice is "
                "recorded yet: the offered default is not a confirmation. Confirm Yes or No by "
                "effecting the governance configuration, then advance."
            )
        raise S015PhaseAdvanceTechnicalFailure(
            "PHASE_ADVANCE could not be completed because of a technical problem; "
            "nothing was saved. Try again; if it persists, report it."
        )
