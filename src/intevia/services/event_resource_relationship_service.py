"""Transactional commands for governed Event resource relationships."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
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
    CANONICALIZATION,
    DISCLOSURE_POLICY_REFERENCE,
    SCHEMA_ID,
    SCHEMA_VERSION,
    EventAuthorityResult,
    EventRelationshipAction,
    EventRelationshipAuthority,
    EventRelationshipAuthorityEnvelope,
    EventRelationshipAuthorityTarget,
    EventRelationshipDisclosure,
    ExistenceDisclosureResult,
    RelationshipDisclosureEnvelope,
    RelationshipDisclosureResult,
    RelationshipPurpose,
    RelationshipState,
    VoidReason,
    canonical_event_payload_bytes,
    determination_reference_for,
)
from src.intevia.services.library_exact_version_contract import (
    AuthorityResult,
    ConsequentialLibraryEvidence,
    DeterminationEnvelope,
    DisclosureResult,
    LibraryAction,
    LibraryExactVersionContractService,
    LibraryRequestContext,
    LinkabilityResult,
)


_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]{0,119}\Z")
_CONSUMER_REFERENCE = "consumer.s011b"
_ELIGIBLE_EVENT_STATES = frozenset(
    (Event.State.DRAFT, Event.State.PUBLISHED, Event.State.ACTIVE)
)
_TERMINAL_EVENT_STATES = frozenset((*_ELIGIBLE_EVENT_STATES, Event.State.COMPLETED))


class EventResourceRelationshipCommandError(Exception):
    """Base class for non-revealing command failure."""


class EventResourceRelationshipRefused(EventResourceRelationshipCommandError):
    """Signal that the command's checks decided against the change.

    Authority, linkability and disclosure checks can refuse. So can the
    Event or relationship state, an existing link, an unchanged version
    or purpose, a survivor outside the correction boundary, or a scope
    that does not permit the correction.
    """
    pass


class EventResourceRelationshipHold(EventResourceRelationshipCommandError):
    """Signal that the service could not proceed on the available facts.

    Examples include a HOLD result, absent or unexpected evidence, an
    unresolved Event or an inconsistent acting Identity. The exception
    gives no instruction to retry.
    """
    pass


class EventResourceRelationshipIdempotencyConflict(
    EventResourceRelationshipCommandError
):
    """Signal a different fingerprint for an actor, action and key.

    When a command reaches its replay check, a matching fingerprint
    returns the stored transition and a different one raises this
    conflict. CREATE reaches the check only when the Event already has
    a link to the resource. Otherwise a reused CREATE key fails the
    database's uniqueness with IntegrityError, not this conflict.
    """
    pass


class EventResourceRelationshipService:
    """Manage a governed link between an Event and a Library version.

    Create, supersede the version, amend the purpose, retire or void the
    link. A new transition records its assertion and the evidence
    required for the action, and the new assertion becomes the head.
    A recognised replay returns the stored transition.

    Commands enter an atomic block on the service alias. Some writes
    rely on database routing. A command can refuse, hold or report an
    idempotency conflict. Input, contract, model and database errors can
    also escape, including after database work begins.
    """
    def __init__(
        self,
        *,
        library_contract: LibraryExactVersionContractService,
        event_authority: EventRelationshipAuthority,
        relationship_disclosure: EventRelationshipDisclosure,
        database_alias: str = "default",
    ) -> None:
        """Store the Library contract, both EVENT evaluators and the
        alias.

        A missing dependency (None) raises TypeError. A different
        Library database_alias raises ValueError. Reading an absent
        alias raises AttributeError. The evaluators' methods are
        unchecked.
        """
        if library_contract is None or event_authority is None or relationship_disclosure is None:
            raise TypeError("Library contract and both EVENT evaluators are required")
        if library_contract.database_alias != database_alias:
            raise ValueError("Library contract must use the command database alias")
        self.library_contract = library_contract
        self.event_authority = event_authority
        self.relationship_disclosure = relationship_disclosure
        self.database_alias = database_alias

    @staticmethod
    def _reference(value: str, name: str) -> str:
        """Return the reference unchanged, or raise ValidationError.

        Require a plain str of 1 to 120 ASCII characters. Start with a
        letter or digit, then allow letters, digits, '.', '_', '~', ':'
        or '-'. The error names the field.
        """
        if type(value) is not str or _REFERENCE.fullmatch(value) is None:
            raise ValidationError(f"{name} is invalid")
        return value

    @staticmethod
    def _occurred_at(value: datetime) -> datetime:
        """Return the supplied timezone-aware datetime unchanged.

        Raise ValidationError for a non-datetime, absent tzinfo or a UTC
        offset of None. Errors from obtaining the offset propagate.
        """
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("occurred_at must be timezone-aware")
        return value

    @staticmethod
    def _actor_id(value: UUID | str) -> str:
        """Return the actor's UUID as lowercase, hyphenated text.

        Keep a UUID or parse the input with UUID(). Parser TypeError,
        ValueError and AttributeError become ValidationError.
        Noncanonical string input also raises ValidationError. This
        helper does no Identity lookup.
        """
        try:
            parsed = value if isinstance(value, UUID) else UUID(value)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("actor_identity_id is invalid") from exc
        canonical = str(parsed)
        if isinstance(value, str) and value != canonical:
            raise ValidationError("actor_identity_id is invalid")
        return canonical

    @staticmethod
    def _fingerprint(payload: dict[str, object]) -> str:
        """Hash sorted, compact JSON as UTF-8 and return SHA-256 hex.

        Non-ASCII characters are retained. Non-finite floats or cycles
        raise ValueError. Unsupported values or incomparable keys can
        raise TypeError. Encoding errors, including UnicodeEncodeError,
        and recursion errors propagate.
        """
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _locked_actor(self, evidence: ConsequentialLibraryEvidence) -> Identity:
        """Lock and return the actor in the Library authority payload.

        Read the authority payload. A None actor ID or epoch raises
        EventResourceRelationshipHold. Fetch the Identity by ID on the
        service alias with FOR UPDATE, joining its credential. In
        PostgreSQL the caller's transaction locks both rows. No match or
        multiple matches raise the same Hold.

        Compare the stored ID and epoch as strings to the payload. A
        mismatch, an Identity outside ACTIVE or an inactive credential
        raises Hold. This read writes nothing and does not decide
        Library authority. Missing attributes, UUID field
        ValidationError and database or transaction errors propagate.
        """
        payload = evidence.authority_envelope.payload
        if payload.actor_identity_id is None or payload.actor_access_epoch is None:
            raise EventResourceRelationshipHold("applicable Identity is unresolved")
        try:
            actor = (
                Identity.objects.using(self.database_alias)
                .select_for_update()
                .select_related("credential")
                .get(identity_id=payload.actor_identity_id)
            )
        except (Identity.DoesNotExist, Identity.MultipleObjectsReturned) as exc:
            raise EventResourceRelationshipHold("applicable Identity is unresolved") from exc
        if (
            str(actor.identity_id) != payload.actor_identity_id
            or str(actor.access_epoch) != payload.actor_access_epoch
            or actor.access_state != Identity.AccessState.ACTIVE
            or not actor.credential.is_active
        ):
            raise EventResourceRelationshipHold("applicable Identity is inconsistent")
        return actor

    def _locked_actor_id(self, actor_identity_id: str) -> Identity:
        """Lock and return the current active Identity by ID.

        Fetch by ID on the service alias with FOR UPDATE, joining the
        credential. In PostgreSQL the caller's transaction locks both
        rows. No match or multiple matches raise
        EventResourceRelationshipHold. An Identity outside ACTIVE or an
        inactive credential then raises the same Hold.

        No epoch or authority evidence is checked. This read writes
        nothing. UUID field ValidationError and database or transaction
        errors propagate.
        """
        try:
            actor = (
                Identity.objects.using(self.database_alias)
                .select_for_update()
                .select_related("credential")
                .get(identity_id=actor_identity_id)
            )
        except (Identity.DoesNotExist, Identity.MultipleObjectsReturned) as exc:
            raise EventResourceRelationshipHold("applicable Identity is unresolved") from exc
        if actor.access_state != Identity.AccessState.ACTIVE or not actor.credential.is_active:
            raise EventResourceRelationshipHold("applicable Identity is inconsistent")
        return actor

    @staticmethod
    def _require_library_create(evidence: ConsequentialLibraryEvidence) -> None:
        """Check Library authority first, then linkability, by value.

        Authority HOLD raises EventResourceRelationshipHold. Any
        remaining value other than QUALIFIED raises
        EventResourceRelationshipRefused. Missing linkability or its
        HOLD result raises Hold. Any remaining result other than
        LINKABLE raises Refused.

        Return None on success. Disclosure and target identity are
        unchecked. No queries, writes or locks. Malformed required
        envelope or payload attributes can raise AttributeError.
        """
        authority = evidence.authority_envelope.payload.result
        if authority == AuthorityResult.HOLD:
            raise EventResourceRelationshipHold("Library authority is unresolved")
        if authority != AuthorityResult.QUALIFIED:
            raise EventResourceRelationshipRefused("Library authority refused")
        if evidence.linkability_envelope is None:
            raise EventResourceRelationshipHold("Library linkability is unresolved")
        linkability = evidence.linkability_envelope.payload.result
        if linkability == LinkabilityResult.HOLD:
            raise EventResourceRelationshipHold("Library linkability is unresolved")
        if linkability != LinkabilityResult.LINKABLE:
            raise EventResourceRelationshipRefused("Library target is not linkable")

    @staticmethod
    def _require_library_amend(evidence: ConsequentialLibraryEvidence) -> None:
        """Check the Library evidence for a purpose amendment.

        Authority HOLD raises EventResourceRelationshipHold. Any
        remaining value other than QUALIFIED raises
        EventResourceRelationshipRefused. Any linkability envelope then
        raises Hold. Missing disclosure or disclosure HOLD raises Hold.
        Any remaining disclosure other than CONTENT_VISIBLE raises
        Refused. Results are compared by value.

        Return None on success. No target check, queries, writes or
        locks. Malformed required envelope or payload attributes can
        raise AttributeError.
        """
        authority = evidence.authority_envelope.payload.result
        if authority == AuthorityResult.HOLD:
            raise EventResourceRelationshipHold("Library authority is unresolved")
        if authority != AuthorityResult.QUALIFIED:
            raise EventResourceRelationshipRefused("Library authority refused")
        if evidence.linkability_envelope is not None:
            raise EventResourceRelationshipHold("AMEND_PURPOSE produced linkability")
        if evidence.disclosure_envelope is None:
            raise EventResourceRelationshipHold("Library disclosure is unresolved")
        disclosure = evidence.disclosure_envelope.payload.result
        if disclosure == DisclosureResult.HOLD:
            raise EventResourceRelationshipHold("Library disclosure is unresolved")
        if disclosure != DisclosureResult.CONTENT_VISIBLE:
            raise EventResourceRelationshipRefused("Library content is hidden")

    @staticmethod
    def _require_event_authority(envelope: EventRelationshipAuthorityEnvelope) -> None:
        """Require the EVENT authority evaluator's QUALIFIED member.

        The HOLD member raises EventResourceRelationshipHold. QUALIFIED
        returns None. Anything else, including either spelling as plain
        text, raises EventResourceRelationshipRefused. The comparison
        uses enum identity.

        Only the result is inspected. No target check, queries, writes
        or locks. An absent result attribute raises AttributeError.
        """
        if envelope.result is EventAuthorityResult.HOLD:
            raise EventResourceRelationshipHold("EVENT authority is unresolved")
        if envelope.result is not EventAuthorityResult.QUALIFIED:
            raise EventResourceRelationshipRefused("EVENT authority refused")

    @staticmethod
    def _require_relationship_disclosure(envelope: RelationshipDisclosureEnvelope) -> None:
        """Require visible relationship and existence results.

        Either enum's HOLD member raises EventResourceRelationshipHold
        first. Otherwise return None only for the VISIBLE and
        EXISTENCE_VISIBLE members together. Every remaining combination
        raises EventResourceRelationshipRefused. Equal strings do not
        satisfy these identity checks.

        Only the two results are used. No viewer or target check,
        queries, writes or locks. A missing attribute raises
        AttributeError if the check reaches it.
        """
        if (
            envelope.result is RelationshipDisclosureResult.HOLD
            or envelope.existence_result is ExistenceDisclosureResult.HOLD
        ):
            raise EventResourceRelationshipHold("relationship disclosure is unresolved")
        if (
            envelope.result is not RelationshipDisclosureResult.VISIBLE
            or envelope.existence_result is not ExistenceDisclosureResult.EXISTENCE_VISIBLE
        ):
            raise EventResourceRelationshipRefused("relationship is hidden")

    @staticmethod
    def _event_evidence(
        transition: EventResourceRelationshipTransition,
        envelope: EventRelationshipAuthorityEnvelope,
    ) -> None:
        """Store the transition's EVENT_AUTHORITY evidence.

        Use the EVENT schema, the envelope's result, references, time
        and bytes, the SHA-256 of those bytes, and the target's actor ID
        and epoch. The manager chooses the write database. The helper
        assumes a usable envelope and returns None. Malformed fields can
        raise AttributeError or TypeError. Model and database errors
        propagate.
        """
        payload = envelope.canonical_payload
        EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY,
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            canonicalization=CANONICALIZATION,
            result=envelope.result.value,
            determination_reference=envelope.determination_reference,
            policy_reference=envelope.policy_reference,
            authority_binding_reference=envelope.binding_reference,
            provider_snapshot_reference=envelope.provider_snapshot_reference,
            canonical_payload=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            actor_identity_id=envelope.target.actor_identity_id,
            actor_access_epoch=envelope.target.actor_access_epoch,
            evaluated_at=envelope.evaluated_at,
        )

    @staticmethod
    def _library_evidence(
        transition: EventResourceRelationshipTransition,
        kind: str,
        envelope: DeterminationEnvelope,
    ) -> None:
        """Store one Library evidence row under the supplied kind.

        Copy schema, result, references, time, actor and viewer from the
        determination, with its bytes and their SHA-256. Keep absent
        epochs as None and convert present epochs with int(). The helper
        does not match kind to the envelope. The database allows one
        row per transition and kind.

        The model's save checks the kind against its choices and the
        digest against the bytes. The manager chooses the write
        database. Return None. Malformed fields, integer conversion,
        validation and insert errors propagate, including TypeError and
        ValueError.
        """
        payload = envelope.payload
        canonical = envelope.canonical_payload
        EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=kind,
            schema_id=payload.schema_id,
            schema_version=payload.schema_version,
            canonicalization=payload.canonicalization,
            result=payload.result,
            determination_reference=envelope.determination_reference,
            policy_reference=payload.policy_reference,
            authority_binding_reference=payload.authority_binding_reference,
            provider_snapshot_reference=payload.provider_snapshot_reference,
            canonical_payload=canonical,
            payload_sha256=hashlib.sha256(canonical).hexdigest(),
            actor_identity_id=payload.actor_identity_id,
            actor_access_epoch=(
                int(payload.actor_access_epoch)
                if payload.actor_access_epoch is not None
                else None
            ),
            viewer_identity_id=payload.viewer_identity_id,
            viewer_access_epoch=(
                int(payload.viewer_access_epoch)
                if payload.viewer_access_epoch is not None
                else None
            ),
            request_reference=payload.request_reference,
            consumer_reference=payload.consumer_reference,
            evaluated_at=envelope.payload.evaluated_at,
        )

    @staticmethod
    def _relationship_disclosure_evidence(
        transition: EventResourceRelationshipTransition,
        envelope: RelationshipDisclosureEnvelope,
    ) -> None:
        """Record the relationship disclosure evidence for a transition.

        Store the relationship result, EVENT schema, fixed disclosure
        policy reference, envelope references, viewer, epoch, time and
        canonical bytes with their SHA-256. The existence result has no
        separate column. The manager chooses the write database. Return
        None. Malformed fields can raise AttributeError or TypeError.
        Model and database errors propagate.
        """
        payload = envelope.canonical_payload
        EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=EventResourceRelationshipEvidence.Kind.RELATIONSHIP_DISCLOSURE_ELIGIBILITY,
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            canonicalization=CANONICALIZATION,
            result=envelope.result.value,
            determination_reference=envelope.determination_reference,
            policy_reference=DISCLOSURE_POLICY_REFERENCE,
            authority_binding_reference=envelope.binding_reference,
            provider_snapshot_reference=envelope.provider_snapshot_reference,
            canonical_payload=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            viewer_identity_id=envelope.viewer_identity_id,
            viewer_access_epoch=envelope.viewer_access_epoch,
            evaluated_at=envelope.evaluated_at,
        )

    @staticmethod
    def _correction_evidence(
        transition: EventResourceRelationshipTransition,
        *,
        reason: VoidReason,
        survivor_relationship_id: str | None,
        survivor_assertion_id: int | None,
        rationale_reference: str | None,
        correction_evidence_reference: str | None,
        occurred_at: datetime,
    ) -> None:
        """Record the reason and evidence for a void correction.

        Encode the reason, survivor IDs, rationale and evidence
        references, ISO occurrence time and transition ID as a canonical
        payload. Then get this transition's EVENT_AUTHORITY row.
        DoesNotExist or MultipleObjectsReturned propagates.

        Insert CORRECTION with the reason as result, the derived
        determination reference and SHA-256, that EVENT row's policy,
        the transition's actor and recorded epoch, and the occurrence
        time. Access to an uncached actor can read its row. The related
        lookup and the manager insert use Django routing. No alias,
        explicit row lock or transaction is selected here.

        Return None. The caller checks the action and survivor.
        Malformed inputs can raise AttributeError or TypeError. Time,
        canonicalisation, model and database errors propagate, including
        IntegrityError on a database constraint.
        """
        payload = canonical_event_payload_bytes(
            {
                "correction_evidence_reference": correction_evidence_reference,
                "occurred_at": occurred_at.isoformat(),
                "rationale_reference": rationale_reference,
                "reason": reason.value,
                "survivor_assertion_id": (
                    str(survivor_assertion_id)
                    if survivor_assertion_id is not None
                    else None
                ),
                "survivor_relationship_id": survivor_relationship_id,
                "transition_id": str(transition.pk),
            }
        )
        event_evidence = transition.evidence.get(
            kind=EventResourceRelationshipEvidence.Kind.EVENT_AUTHORITY
        )
        EventResourceRelationshipEvidence.objects.create(
            transition=transition,
            kind=EventResourceRelationshipEvidence.Kind.CORRECTION,
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            canonicalization=CANONICALIZATION,
            result=reason.value,
            determination_reference=determination_reference_for(payload),
            policy_reference=event_evidence.policy_reference,
            canonical_payload=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            actor_identity_id=transition.actor.identity_id,
            actor_access_epoch=transition.actor_access_epoch,
            evaluated_at=occurred_at,
        )

    def _event_target(
        self,
        *,
        actor: Identity,
        action: EventRelationshipAction,
        authority_scope: str,
        event: Event,
        relationship: EventResourceRelationship,
        assertion: EventResourceAssertion,
        version_number: int,
        proposed_purpose: RelationshipPurpose | None,
        occurred_at: datetime,
    ) -> EventRelationshipAuthorityTarget:
        """Build the EVENT authority target for an existing link.

        Include actor ID and epoch, action, scope, Event ID and state,
        link and assertion IDs, resource ID, supplied version, current
        and proposed purposes, and occurrence time. An uncached resource
        can require a read. An unknown stored purpose or a failed target
        field check raises ValueError. Lookup, parser and timezone
        errors can escape. Construction does not determine authority.
        """
        return EventRelationshipAuthorityTarget(
            actor_identity_id=str(actor.identity_id),
            actor_access_epoch=actor.access_epoch,
            action=action,
            authority_scope=authority_scope,
            event_id=event.event_id,
            event_state=event.state,
            relationship_id=relationship.relationship_id,
            current_assertion_id=assertion.pk,
            resource_id=relationship.library_resource.resource_id,
            version_number=version_number,
            current_purpose=RelationshipPurpose(assertion.purpose),
            proposed_purpose=proposed_purpose,
            occurred_at=occurred_at,
        )

    def _replay(
        self,
        *,
        actor: Identity,
        action: EventRelationshipAction,
        idempotency_key: str,
        fingerprint: str,
    ) -> EventResourceRelationshipTransition | None:
        """Read the first transition for the actor, action and key.

        Use the service alias. Return None for no match. A different
        stored fingerprint raises
        EventResourceRelationshipIdempotencyConflict. An equal
        fingerprint returns the stored transition. The database key is
        unique within actor and action.

        This lookup writes nothing and requests no row lock. It trusts
        the supplied fingerprint and checks neither current authority
        nor the head. Invalid filter values can raise ValueError.
        Database errors propagate. An action without a value attribute
        raises AttributeError.
        """
        replay = (
            EventResourceRelationshipTransition.objects.using(self.database_alias)
            .filter(actor=actor, action=action.value, idempotency_key=idempotency_key)
            .first()
        )
        if replay is None:
            return None
        if replay.payload_fingerprint != fingerprint:
            raise EventResourceRelationshipIdempotencyConflict(
                "idempotency key was used with a different payload"
            )
        return replay

    def _append(
        self,
        *,
        relationship: EventResourceRelationship,
        current: EventResourceAssertion,
        previous_transition: EventResourceRelationshipTransition,
        action: EventRelationshipAction,
        version,
        purpose: RelationshipPurpose,
        state: str,
        prior_disposition: str,
        actor: Identity,
        authority_scope: str,
        event_evidence: EventRelationshipAuthorityEnvelope,
        request_reference: str,
        idempotency_key: str,
        fingerprint: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        """Append a successor and transition, then advance the head.

        Create an assertion at current revision plus one, with current
        as predecessor, the supplied version, purpose, state, actor,
        epoch and time. Create its transition at the previous sequence
        plus one, linking both assertions and the previous transition.
        Record the action, disposition, actor and epoch, authority
        scope, EVENT reference and evaluation time, request and consumer
        references, key, fingerprint, a new transaction UUID and
        occurrence time.

        Write EVENT authority evidence, set and save the head, and
        return the transition. The creates and head save use Django
        routing. The assertion save checks predecessor, revision and
        resource version. The transition save checks predecessor,
        sequence, assertions and fingerprint shape. Both run full_clean
        with constraint validation disabled. The head save reads the
        original relationship and validates its immutable fields and
        head. These checks can read related rows.

        The caller supplies the transaction and locks. This helper does
        not verify the current head, the prior transition's result or
        the authority verdict. It does not catch ValidationError, lookup
        errors such as DoesNotExist, IntegrityError or other database
        errors. Malformed inputs or evidence can raise AttributeError or
        TypeError.
        """
        assertion = EventResourceAssertion.objects.create(
            relationship=relationship,
            revision=current.revision + 1,
            predecessor=current,
            library_resource_version=version,
            purpose=purpose.value,
            state=state,
            created_by=actor,
            actor_access_epoch=actor.access_epoch,
            created_at=occurred_at,
        )
        transition = EventResourceRelationshipTransition.objects.create(
            relationship=relationship,
            previous_transition=previous_transition,
            sequence=previous_transition.sequence + 1,
            action=action.value,
            from_assertion=current,
            resulting_assertion=assertion,
            prior_disposition=prior_disposition,
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            authority_scope=authority_scope,
            event_authority_reference=event_evidence.determination_reference,
            event_authority_evaluated_at=event_evidence.evaluated_at,
            request_reference=request_reference,
            consumer_reference=_CONSUMER_REFERENCE,
            idempotency_key=idempotency_key,
            payload_fingerprint=fingerprint,
            transaction_reference=uuid4(),
            occurred_at=occurred_at,
        )
        self._event_evidence(transition, event_evidence)
        relationship.head_assertion = assertion
        relationship.save(update_fields=("head_assertion",))
        return transition

    def create(
        self,
        *,
        relationship_id: str,
        event_id: str,
        resource_id: str,
        version_number: int,
        purpose: RelationshipPurpose,
        actor_identity_id: UUID | str,
        authority_scope: str,
        library_context: LibraryRequestContext,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        """Create an Event's first link to a version of a resource.

        Hold and Refused below mean EventResourceRelationshipHold and
        EventResourceRelationshipRefused.

        Validate the references, actor UUID, aware time, positive int
        version, purpose enum and Library context, in that order.
        Invalid inputs raise ValidationError. Fingerprint the command
        using only request_reference from the Library context.

        In an atomic block on the service alias, lock the resource,
        read its version without locking it, then lock the Event.
        A missing Event raises Hold. Lock existing links for the pair
        in PK order. Evaluate Library CREATE with the actor lock, then
        lock the reported actor and credential. Missing identity or
        epoch, an epoch mismatch or inactive access raises Hold.

        Require QUALIFIED Library authority and LINKABLE evidence.
        Unresolved results or absent linkability raise Hold. Denials
        raise Refused. Refuse Events outside DRAFT, PUBLISHED or ACTIVE.
        Only for an existing pair, look up actor, CREATE and key.
        Return the stored transition on an equal fingerprint. A mismatch
        raises EventResourceRelationshipIdempotencyConflict. Without
        a matching key, refuse the existing pair.

        For a new pair, ask EVENT authority about the proposed purpose
        and exact version, with no link or assertion ID on its target.
        HOLD raises Hold, any other non-QUALIFIED result raises Refused.
        Require a resolved resource and version. Create the link, its
        CURRENT revision 1 and sequence 1 CREATE. Write EVENT authority,
        Library authority and linkability evidence, save the head and
        return the transition. Writes follow Django routing.

        Time or fingerprint conversion errors propagate. Library scope
        errors (RuntimeError), target ValueError, evaluator and evidence
        errors propagate. Model saves can raise ValidationError for a
        taken relationship ID or a scope over 64 characters. On a fresh
        pair a reused actor/CREATE/key can reach the transition insert
        and raise IntegrityError. Other database errors also propagate.
        """
        relationship_id = self._reference(relationship_id, "relationship_id")
        event_id = self._reference(event_id, "event_id")
        resource_id = self._reference(resource_id, "resource_id")
        authority_scope = self._reference(authority_scope, "authority_scope")
        idempotency_key = self._reference(idempotency_key, "idempotency_key")
        actor_identity_id = self._actor_id(actor_identity_id)
        occurred_at = self._occurred_at(occurred_at)
        if type(version_number) is not int or version_number < 1:
            raise ValidationError("version_number is invalid")
        if not isinstance(purpose, RelationshipPurpose):
            raise ValidationError("purpose is invalid")
        if not isinstance(library_context, LibraryRequestContext):
            raise ValidationError("library_context is invalid")
        fingerprint = self._fingerprint(
            {
                "action": EventRelationshipAction.CREATE.value,
                "actor_identity_id": actor_identity_id,
                "authority_scope": authority_scope,
                "event_id": event_id,
                "idempotency_key": idempotency_key,
                "occurred_at": occurred_at.isoformat(),
                "purpose": purpose.value,
                "relationship_id": relationship_id,
                "request_reference": library_context.request_reference,
                "resource_id": resource_id,
                "version_number": str(version_number),
            }
        )

        with transaction.atomic(using=self.database_alias):
            scope = self.library_contract.acquire_consequential_library_scope(
                resource_id=resource_id,
                version_number=version_number,
            )
            resource = scope._resource
            version = scope._version
            try:
                event = (
                    Event.objects.using(self.database_alias)
                    .select_for_update()
                    .get(event_id=event_id)
                )
            except Event.DoesNotExist as exc:
                raise EventResourceRelationshipHold("Event is unresolved") from exc
            existing = list(
                EventResourceRelationship.objects.using(self.database_alias)
                .select_for_update()
                .filter(event=event, library_resource=resource)
                .order_by("pk")
            )
            library_evidence = self.library_contract.evaluate_consequential_library_truth(
                scope=scope,
                actor_identity_id=actor_identity_id,
                action=LibraryAction.CREATE,
                context=library_context,
                evaluated_at=occurred_at,
            )
            actor = self._locked_actor(library_evidence)
            self._require_library_create(library_evidence)
            if event.state not in _ELIGIBLE_EVENT_STATES:
                raise EventResourceRelationshipRefused("Event state refuses CREATE")
            if existing:
                replay = (
                    EventResourceRelationshipTransition.objects.using(self.database_alias)
                    .filter(
                        actor=actor,
                        action=EventResourceRelationshipTransition.Action.CREATE,
                        idempotency_key=idempotency_key,
                    )
                    .first()
                )
                if replay is not None and replay.payload_fingerprint == fingerprint:
                    return replay
                if replay is not None:
                    raise EventResourceRelationshipIdempotencyConflict(
                        "idempotency key was used with a different payload"
                    )
                raise EventResourceRelationshipRefused("relationship already exists")
            target = EventRelationshipAuthorityTarget(
                actor_identity_id=str(actor.identity_id),
                actor_access_epoch=actor.access_epoch,
                action=EventRelationshipAction.CREATE,
                authority_scope=authority_scope,
                event_id=event.event_id,
                event_state=event.state,
                relationship_id=None,
                current_assertion_id=None,
                resource_id=resource_id,
                version_number=version_number,
                current_purpose=None,
                proposed_purpose=purpose,
                occurred_at=occurred_at,
            )
            event_evidence = self.event_authority.determine_authority(
                identity=actor,
                target=target,
                evaluated_at=occurred_at,
            )
            self._require_event_authority(event_evidence)
            if resource is None or version is None:
                raise EventResourceRelationshipHold("exact Library target is unresolved")

            relationship = EventResourceRelationship.objects.create(
                relationship_id=relationship_id,
                event=event,
                library_resource=resource,
                created_at=occurred_at,
            )
            assertion = EventResourceAssertion.objects.create(
                relationship=relationship,
                revision=1,
                library_resource_version=version,
                purpose=purpose.value,
                state=EventResourceAssertion.State.CURRENT,
                created_by=actor,
                actor_access_epoch=actor.access_epoch,
                created_at=occurred_at,
            )
            transition = EventResourceRelationshipTransition.objects.create(
                relationship=relationship,
                sequence=1,
                action=EventResourceRelationshipTransition.Action.CREATE,
                resulting_assertion=assertion,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_scope=authority_scope,
                event_authority_reference=event_evidence.determination_reference,
                event_authority_evaluated_at=event_evidence.evaluated_at,
                request_reference=library_context.request_reference,
                consumer_reference=_CONSUMER_REFERENCE,
                idempotency_key=idempotency_key,
                payload_fingerprint=fingerprint,
                transaction_reference=uuid4(),
                occurred_at=occurred_at,
            )
            self._event_evidence(transition, event_evidence)
            self._library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                library_evidence.authority_envelope,
            )
            self._library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
                library_evidence.linkability_envelope,
            )
            relationship.head_assertion = assertion
            relationship.save(update_fields=("head_assertion",))
            return transition

    def supersede_version(
        self,
        *,
        relationship_id: str,
        event_id: str,
        resource_id: str,
        version_number: int,
        actor_identity_id: UUID | str,
        authority_scope: str,
        library_context: LibraryRequestContext,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        """Point the link at a different exact version of its resource.

        Delegate to _library_successor with SUPERSEDE_VERSION and no
        purpose, so the current purpose is kept. The same checks,
        refusals, locks and evidence apply; Library linkability is
        required. The new version must differ from the head's but need
        not be newer.
        """
        return self._library_successor(
            relationship_id=relationship_id,
            event_id=event_id,
            resource_id=resource_id,
            version_number=version_number,
            purpose=None,
            actor_identity_id=actor_identity_id,
            action=EventRelationshipAction.SUPERSEDE_VERSION,
            authority_scope=authority_scope,
            library_context=library_context,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )

    def amend_purpose(
        self,
        *,
        relationship_id: str,
        event_id: str,
        resource_id: str,
        version_number: int,
        purpose: RelationshipPurpose,
        actor_identity_id: UUID | str,
        authority_scope: str,
        library_context: LibraryRequestContext,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        """Amend the purpose of a link at its current exact version.

        Reject a non-RelationshipPurpose with ValidationError before
        database work. Return _library_successor's AMEND_PURPOSE
        transition. A head-version mismatch raises
        EventResourceRelationshipHold. Library authority and content
        visibility precede replay. A fresh amendment also requires a
        changed purpose, EVENT authority and disclosure of both the
        current assertion and its existence to the actor.
        """
        if not isinstance(purpose, RelationshipPurpose):
            raise ValidationError("purpose is invalid")
        return self._library_successor(
            relationship_id=relationship_id,
            event_id=event_id,
            resource_id=resource_id,
            version_number=version_number,
            purpose=purpose,
            actor_identity_id=actor_identity_id,
            action=EventRelationshipAction.AMEND_PURPOSE,
            authority_scope=authority_scope,
            library_context=library_context,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )

    def _library_successor(
        self,
        *,
        relationship_id: str,
        event_id: str,
        resource_id: str,
        version_number: int,
        purpose: RelationshipPurpose | None,
        actor_identity_id: UUID | str,
        action: EventRelationshipAction,
        authority_scope: str,
        library_context: LibraryRequestContext,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        """Append a new version or purpose to a current link.

        Hold and Refused below mean EventResourceRelationshipHold and
        EventResourceRelationshipRefused.

        Check references, actor UUID, aware time, positive int version
        and Library context; invalid values raise ValidationError.
        The public wrapper supplies the action and validates any new
        purpose. Fingerprint the command before acquiring locks.

        In an atomic block on the service alias, lock the resource,
        read its exact version, then lock the Event and matching link
        (ID, Event and resource). Lock its assertions by revision and
        PK, then its transitions by sequence and PK. The joined version
        rows are only read. Hold for a missing Event, link, head among
        the assertions, or transition. Use the last transition without
        checking its result against the head. For AMEND_PURPOSE, raise
        Hold if the supplied version differs from the head's exact
        version.

        Evaluate Library truth with the actor lock. Lock the reported
        actor and credential; missing or inconsistent access raises
        Hold. Require QUALIFIED authority. Supersession needs LINKABLE.
        Amendment needs no linkability envelope and CONTENT_VISIBLE.
        Unresolved or missing required evidence, or linkability on an
        amendment, raises Hold. Negative results raise Refused.

        Look up the actor, action and key. An equal fingerprint returns
        the stored transition. A different fingerprint raises
        EventResourceRelationshipIdempotencyConflict. For a fresh act,
        refuse an Event outside DRAFT, PUBLISHED or ACTIVE or a head
        outside CURRENT. Hold for an unresolved exact target. Refuse
        an unchanged version on supersession or purpose on amendment.
        Require QUALIFIED EVENT authority; amendment also needs the
        current assertion and its existence visible. HOLD raises Hold;
        other unsuccessful results raise Refused.

        Append the next CURRENT assertion and transition, write EVENT
        authority evidence and save the head. Record prior disposition
        as SUPERSEDED for a version change or CURRENT for an amendment.
        Add Library authority evidence, then linkability or, for an
        amendment, Library and relationship disclosure evidence.
        Return the transition. Earlier assertions keep their state.
        Writes follow Django routing.

        Time and fingerprint errors, Library scope RuntimeError, enum
        or target ValueError, evaluator and evidence errors propagate.
        Model ValidationError (including unique-field checks), lookup,
        IntegrityError and other database errors also propagate.
        """
        relationship_id = self._reference(relationship_id, "relationship_id")
        event_id = self._reference(event_id, "event_id")
        resource_id = self._reference(resource_id, "resource_id")
        authority_scope = self._reference(authority_scope, "authority_scope")
        idempotency_key = self._reference(idempotency_key, "idempotency_key")
        actor_identity_id = self._actor_id(actor_identity_id)
        occurred_at = self._occurred_at(occurred_at)
        if type(version_number) is not int or version_number < 1:
            raise ValidationError("version_number is invalid")
        if not isinstance(library_context, LibraryRequestContext):
            raise ValidationError("library_context is invalid")
        fingerprint = self._fingerprint(
            {
                "action": action.value,
                "actor_identity_id": actor_identity_id,
                "authority_scope": authority_scope,
                "event_id": event_id,
                "idempotency_key": idempotency_key,
                "occurred_at": occurred_at.isoformat(),
                "purpose": purpose.value if purpose else None,
                "relationship_id": relationship_id,
                "request_reference": library_context.request_reference,
                "resource_id": resource_id,
                "version_number": str(version_number),
            }
        )
        with transaction.atomic(using=self.database_alias):
            scope = self.library_contract.acquire_consequential_library_scope(
                resource_id=resource_id,
                version_number=version_number,
            )
            resource, version = scope._resource, scope._version
            try:
                event = Event.objects.using(self.database_alias).select_for_update().get(
                    event_id=event_id
                )
                relationship = (
                    EventResourceRelationship.objects.using(self.database_alias)
                    .select_for_update(of=("self",))
                    .select_related("library_resource")
                    .get(
                        relationship_id=relationship_id,
                        event=event,
                        library_resource=resource,
                    )
                )
                assertions = list(
                    EventResourceAssertion.objects.using(self.database_alias)
                    .select_for_update(of=("self",))
                    .select_related("library_resource_version")
                    .filter(relationship=relationship)
                    .order_by("revision", "pk")
                )
                transitions = list(
                    EventResourceRelationshipTransition.objects.using(self.database_alias)
                    .select_for_update()
                    .filter(relationship=relationship)
                    .order_by("sequence", "pk")
                )
            except (Event.DoesNotExist, EventResourceRelationship.DoesNotExist) as exc:
                raise EventResourceRelationshipHold("relationship header is unresolved") from exc
            current = next(
                (item for item in assertions if item.pk == relationship.head_assertion_id),
                None,
            )
            if current is None or not transitions:
                raise EventResourceRelationshipHold("relationship header is unresolved")
            previous = transitions[-1]
            if action is EventRelationshipAction.AMEND_PURPOSE and (
                current.library_resource_version_id != getattr(version, "pk", None)
            ):
                raise EventResourceRelationshipHold("exact current assertion is unresolved")
            library_evidence = self.library_contract.evaluate_consequential_library_truth(
                scope=scope,
                actor_identity_id=actor_identity_id,
                action=LibraryAction(action.value),
                context=library_context,
                evaluated_at=occurred_at,
            )
            actor = self._locked_actor(library_evidence)
            if action is EventRelationshipAction.AMEND_PURPOSE:
                self._require_library_amend(library_evidence)
            else:
                self._require_library_create(library_evidence)
            replay = self._replay(
                actor=actor,
                action=action,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if (
                event.state not in _ELIGIBLE_EVENT_STATES
                or current.state != EventResourceAssertion.State.CURRENT
            ):
                raise EventResourceRelationshipRefused(f"relationship refuses {action.value}")
            if resource is None or version is None:
                raise EventResourceRelationshipHold("exact Library target is unresolved")
            proposed_purpose = purpose or RelationshipPurpose(current.purpose)
            if (
                action is EventRelationshipAction.SUPERSEDE_VERSION
                and current.library_resource_version_id == version.pk
            ):
                raise EventResourceRelationshipRefused(
                    "SUPERSEDE_VERSION requires a different exact version"
                )
            if (
                action is EventRelationshipAction.AMEND_PURPOSE
                and proposed_purpose is RelationshipPurpose(current.purpose)
            ):
                raise EventResourceRelationshipRefused(
                    "AMEND_PURPOSE requires a changed purpose"
                )
            event_evidence = self.event_authority.determine_authority(
                identity=actor,
                target=self._event_target(
                    actor=actor,
                    action=action,
                    authority_scope=authority_scope,
                    event=event,
                    relationship=relationship,
                    assertion=current,
                    version_number=version_number,
                    proposed_purpose=proposed_purpose,
                    occurred_at=occurred_at,
                ),
                evaluated_at=occurred_at,
            )
            self._require_event_authority(event_evidence)
            relationship_evidence = None
            if action is EventRelationshipAction.AMEND_PURPOSE:
                relationship_evidence = self.relationship_disclosure.determine_disclosure(
                    identity=actor,
                    event_id=event.event_id,
                    relationship_id=relationship.relationship_id,
                    assertion_id=current.pk,
                    state=RelationshipState(current.state),
                    purpose=RelationshipPurpose(current.purpose),
                    evaluated_at=occurred_at,
                )
                self._require_relationship_disclosure(relationship_evidence)
            transition = self._append(
                relationship=relationship,
                current=current,
                previous_transition=previous,
                action=action,
                version=version,
                purpose=proposed_purpose,
                state=EventResourceAssertion.State.CURRENT,
                prior_disposition=(
                    EventResourceAssertion.State.CURRENT
                    if action is EventRelationshipAction.AMEND_PURPOSE
                    else EventResourceAssertion.State.SUPERSEDED
                ),
                actor=actor,
                authority_scope=authority_scope,
                event_evidence=event_evidence,
                request_reference=library_context.request_reference,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                occurred_at=occurred_at,
            )
            self._library_evidence(
                transition,
                EventResourceRelationshipEvidence.Kind.LIBRARY_AUTHORITY,
                library_evidence.authority_envelope,
            )
            if action is EventRelationshipAction.AMEND_PURPOSE:
                self._library_evidence(
                    transition,
                    EventResourceRelationshipEvidence.Kind.LIBRARY_DISCLOSURE_ELIGIBILITY,
                    library_evidence.disclosure_envelope,
                )
                self._relationship_disclosure_evidence(transition, relationship_evidence)
            else:
                self._library_evidence(
                    transition,
                    EventResourceRelationshipEvidence.Kind.LIBRARY_LINKABILITY,
                    library_evidence.linkability_envelope,
                )
            return transition

    def retire(
        self,
        *,
        relationship_id: str,
        event_id: str,
        actor_identity_id: UUID | str,
        authority_scope: str,
        request_reference: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> EventResourceRelationshipTransition:
        """Retire a current link and return its terminal transition.

        Delegate RETIRE and the caller's request reference to _terminal.
        A fresh retirement admits DRAFT, PUBLISHED, ACTIVE or COMPLETED
        and refuses ARCHIVED. A matching replay can return before this
        state check. Retirement makes no Library evaluation.
        """
        return self._terminal(
            relationship_id=relationship_id,
            event_id=event_id,
            actor_identity_id=actor_identity_id,
            action=EventRelationshipAction.RETIRE,
            authority_scope=authority_scope,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )

    def void(
        self,
        *,
        relationship_id: str,
        event_id: str,
        actor_identity_id: UUID | str,
        authority_scope: str,
        reason: VoidReason,
        request_reference: str,
        idempotency_key: str,
        occurred_at: datetime,
        survivor_relationship_id: str | None = None,
        survivor_assertion_id: int | None = None,
        rationale_reference: str | None = None,
        correction_evidence_reference: str | None = None,
    ) -> EventResourceRelationshipTransition:
        """Void a current link and return the correction transition.

        First require a VoidReason. Check any survivor relationship,
        rationale and correction evidence references, then require any
        survivor assertion ID to be an exact positive int. For
        DUPLICATE_ASSERTION, supply a named link and assertion, or
        "NO_SURVIVOR" with a rationale and no assertion ID. Violations
        raise ValidationError before database work. With these checks
        passed, OTHER_GOVERNED_CORRECTION raises
        EventResourceRelationshipHold before delegation.

        Return _terminal's VOID result. It owns the alias transaction,
        locks, actor check, replay and propagated errors. A matching
        replay returns before survivor, state, scope and EVENT authority
        checks. A fresh duplicate with a named survivor must resolve it.
        A fresh void of an ARCHIVED Event, or for WRONG_PURPOSE, needs
        the scope "historical_correction" (else
        EventResourceRelationshipRefused).
        Other admitted reasons record optional survivor fields without
        resolving them. Rationale and evidence references are recorded
        as supplied. A fresh void writes the terminal assertion and
        transition, EVENT authority evidence and CORRECTION evidence.
        """
        if not isinstance(reason, VoidReason):
            raise ValidationError("reason is invalid")
        if survivor_relationship_id is not None:
            survivor_relationship_id = self._reference(
                survivor_relationship_id, "survivor_relationship_id"
            )
        if rationale_reference is not None:
            rationale_reference = self._reference(rationale_reference, "rationale_reference")
        if correction_evidence_reference is not None:
            correction_evidence_reference = self._reference(
                correction_evidence_reference, "correction_evidence_reference"
            )
        if survivor_assertion_id is not None and (
            type(survivor_assertion_id) is not int or survivor_assertion_id < 1
        ):
            raise ValidationError("survivor_assertion_id is invalid")
        if (
            reason is VoidReason.DUPLICATE_ASSERTION
            and (
                survivor_relationship_id is None
                or (
                    survivor_relationship_id == "NO_SURVIVOR"
                    and (
                        rationale_reference is None
                        or survivor_assertion_id is not None
                    )
                )
                or (
                    survivor_relationship_id != "NO_SURVIVOR"
                    and survivor_assertion_id is None
                )
            )
        ):
            raise ValidationError(
                "duplicate correction requires an exact survivor or structured NO_SURVIVOR"
            )
        if reason is VoidReason.OTHER_GOVERNED_CORRECTION:
            raise EventResourceRelationshipHold(
                "OTHER correction classification is unresolved"
            )
        return self._terminal(
            relationship_id=relationship_id,
            event_id=event_id,
            actor_identity_id=actor_identity_id,
            action=EventRelationshipAction.VOID,
            authority_scope=authority_scope,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            reason=reason,
            survivor_relationship_id=survivor_relationship_id,
            survivor_assertion_id=survivor_assertion_id,
            rationale_reference=rationale_reference,
            correction_evidence_reference=correction_evidence_reference,
        )

    def _terminal(
        self,
        *,
        relationship_id: str,
        event_id: str,
        actor_identity_id: UUID | str,
        action: EventRelationshipAction,
        authority_scope: str,
        request_reference: str,
        idempotency_key: str,
        occurred_at: datetime,
        reason: VoidReason | None = None,
        survivor_relationship_id: str | None = None,
        survivor_assertion_id: int | None = None,
        rationale_reference: str | None = None,
        correction_evidence_reference: str | None = None,
    ) -> EventResourceRelationshipTransition:
        """Retire or void the head and return its terminal transition.

        Hold and Refused below mean EventResourceRelationshipHold and
        EventResourceRelationshipRefused.

        Check link and Event references, actor UUID, scope, request
        reference, key and aware time. Invalid values raise
        ValidationError. Fingerprint the inputs, including correction
        fields. The public wrappers supply the action and reason.

        On the service alias, enter an atomic block. Lock the Event,
        then its named link, assertions by revision and PK, and
        transitions by sequence and PK. Read the linked resource and
        versions without locking them. A missing Event, link, head
        among its assertions, or transition raises Hold. Take the last
        transition without checking that its result is the head.
        Lock the actor and joined credential. An unresolved actor,
        non-ACTIVE access or inactive credential raises Hold. No prior
        epoch is compared and no Library evaluation is made.

        Look up the actor, action and key. Return an equal-fingerprint
        replay. A mismatch raises
        EventResourceRelationshipIdempotencyConflict.
        For a fresh duplicate void naming a survivor, lock the survivor
        link in this Event, then its named assertion. Missing rows raise
        Hold. Refuse unless the link is this one, the assertion is
        neither the head nor the last sorted assertion, and its version
        and purpose match the head. The survivor is recorded only.

        Refuse a non-CURRENT head. RETIRE admits DRAFT, PUBLISHED,
        ACTIVE and COMPLETED. VOID admits all declared Event states.
        For VOID, ARCHIVED or WRONG_PURPOSE requires the exact scope
        "historical_correction". Bad scope or state raises Refused.
        Evaluate EVENT authority on the head version and purpose.
        HOLD raises Hold; any other non-QUALIFIED result raises Refused.

        Append the next RETIRED or VOIDED assertion and transition with
        the head version and purpose and prior disposition CURRENT.
        Write EVENT authority evidence and save the head. For VOID,
        read that evidence row and add CORRECTION evidence. Earlier
        assertions retain their state. Writes follow Django routing.

        Time, fingerprint, enum and target conversion errors propagate,
        including ValueError. Evaluator and malformed-evidence errors,
        model ValidationError (including unique-field checks), lookup
        errors, IntegrityError and other database errors propagate.
        """
        relationship_id = self._reference(relationship_id, "relationship_id")
        event_id = self._reference(event_id, "event_id")
        actor_identity_id = self._actor_id(actor_identity_id)
        authority_scope = self._reference(authority_scope, "authority_scope")
        request_reference = self._reference(request_reference, "request_reference")
        idempotency_key = self._reference(idempotency_key, "idempotency_key")
        occurred_at = self._occurred_at(occurred_at)
        fingerprint = self._fingerprint(
            {
                "action": action.value,
                "actor_identity_id": actor_identity_id,
                "authority_scope": authority_scope,
                "correction_evidence_reference": correction_evidence_reference,
                "event_id": event_id,
                "idempotency_key": idempotency_key,
                "occurred_at": occurred_at.isoformat(),
                "rationale_reference": rationale_reference,
                "reason": reason.value if reason else None,
                "relationship_id": relationship_id,
                "request_reference": request_reference,
                "survivor_assertion_id": survivor_assertion_id,
                "survivor_relationship_id": survivor_relationship_id,
            }
        )
        with transaction.atomic(using=self.database_alias):
            try:
                event = Event.objects.using(self.database_alias).select_for_update().get(
                    event_id=event_id
                )
                relationship = (
                    EventResourceRelationship.objects.using(self.database_alias)
                    .select_for_update(of=("self",))
                    .select_related("library_resource")
                    .get(relationship_id=relationship_id, event=event)
                )
                assertions = list(
                    EventResourceAssertion.objects.using(self.database_alias)
                    .select_for_update(of=("self",))
                    .select_related("library_resource_version")
                    .filter(relationship=relationship)
                    .order_by("revision", "pk")
                )
                transitions = list(
                    EventResourceRelationshipTransition.objects.using(self.database_alias)
                    .select_for_update()
                    .filter(relationship=relationship)
                    .order_by("sequence", "pk")
                )
            except (Event.DoesNotExist, EventResourceRelationship.DoesNotExist) as exc:
                raise EventResourceRelationshipHold("relationship header is unresolved") from exc
            current = next(
                (item for item in assertions if item.pk == relationship.head_assertion_id),
                None,
            )
            if current is None or not transitions:
                raise EventResourceRelationshipHold("relationship header is unresolved")
            previous = transitions[-1]
            actor = self._locked_actor_id(actor_identity_id)
            replay = self._replay(
                actor=actor,
                action=action,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if (
                action is EventRelationshipAction.VOID
                and reason is VoidReason.DUPLICATE_ASSERTION
                and survivor_relationship_id != "NO_SURVIVOR"
            ):
                try:
                    survivor_relationship = (
                        EventResourceRelationship.objects.using(self.database_alias)
                        .select_for_update(of=("self",))
                        .get(
                            relationship_id=survivor_relationship_id,
                            event=event,
                        )
                    )
                    survivor = EventResourceAssertion.objects.using(
                        self.database_alias
                    ).select_for_update(of=("self",)).get(
                        pk=survivor_assertion_id,
                        relationship=survivor_relationship,
                    )
                except (
                    EventResourceRelationship.DoesNotExist,
                    EventResourceAssertion.DoesNotExist,
                ) as exc:
                    raise EventResourceRelationshipHold(
                        "duplicate survivor is unresolved"
                    ) from exc
                if (
                    survivor_relationship.pk != relationship.pk
                    or survivor.pk == current.pk
                    or survivor.pk not in {item.pk for item in assertions[:-1]}
                    or survivor.library_resource_version_id
                    != current.library_resource_version_id
                    or survivor.purpose != current.purpose
                ):
                    raise EventResourceRelationshipRefused(
                        "duplicate survivor is outside the correction boundary"
                    )
            if current.state != EventResourceAssertion.State.CURRENT:
                raise EventResourceRelationshipRefused("relationship is terminal")
            if action is EventRelationshipAction.RETIRE and event.state not in _TERMINAL_EVENT_STATES:
                raise EventResourceRelationshipRefused("Event state refuses RETIRE")
            if (
                action is EventRelationshipAction.VOID
                and event.state not in _TERMINAL_EVENT_STATES
                and event.state != Event.State.ARCHIVED
            ):
                raise EventResourceRelationshipRefused("Event state refuses VOID")
            if (
                action is EventRelationshipAction.VOID
                and event.state == Event.State.ARCHIVED
                and authority_scope != "historical_correction"
            ):
                raise EventResourceRelationshipRefused(
                    "archived Event requires historical correction"
                )
            if (
                action is EventRelationshipAction.VOID
                and reason is VoidReason.WRONG_PURPOSE
                and authority_scope != "historical_correction"
            ):
                raise EventResourceRelationshipRefused(
                    "ordinary wrong purpose requires AMEND_PURPOSE"
                )
            event_evidence = self.event_authority.determine_authority(
                identity=actor,
                target=self._event_target(
                    actor=actor,
                    action=action,
                    authority_scope=authority_scope,
                    event=event,
                    relationship=relationship,
                    assertion=current,
                    version_number=current.library_resource_version.version_number,
                    proposed_purpose=RelationshipPurpose(current.purpose),
                    occurred_at=occurred_at,
                ),
                evaluated_at=occurred_at,
            )
            self._require_event_authority(event_evidence)
            state = (
                EventResourceAssertion.State.RETIRED
                if action is EventRelationshipAction.RETIRE
                else EventResourceAssertion.State.VOIDED
            )
            transition = self._append(
                relationship=relationship,
                current=current,
                previous_transition=previous,
                action=action,
                version=current.library_resource_version,
                purpose=RelationshipPurpose(current.purpose),
                state=state,
                prior_disposition=EventResourceAssertion.State.CURRENT,
                actor=actor,
                authority_scope=authority_scope,
                event_evidence=event_evidence,
                request_reference=request_reference,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                occurred_at=occurred_at,
            )
            if action is EventRelationshipAction.VOID:
                self._correction_evidence(
                    transition,
                    reason=reason,
                    survivor_relationship_id=survivor_relationship_id,
                    survivor_assertion_id=survivor_assertion_id,
                    rationale_reference=rationale_reference,
                    correction_evidence_reference=correction_evidence_reference,
                    occurred_at=occurred_at,
                )
            return transition


__all__ = [
    "EventResourceRelationshipCommandError",
    "EventResourceRelationshipHold",
    "EventResourceRelationshipIdempotencyConflict",
    "EventResourceRelationshipRefused",
    "EventResourceRelationshipService",
]