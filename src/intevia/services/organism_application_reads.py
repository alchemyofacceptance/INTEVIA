"""Read an LO application and its history for its permitted audience.

OrganismApplicationReader returns an ApplicationView and lineage
HistoryRows only to a reader whose Identity is active and who is
the applicant or a current Coordinator of the application's LO.
Each read uses the database's present time for the audience check.
_present_application_audiences decides that audience; the command
support module's replay viewer also uses it. Nothing here writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connections

from core.models import (
    Application,
    ContextualRoleAssignment,
    Identity,
    IdentityTransition,
)
from src.intevia.services.organism_application_service import (
    S015ApplicationNotFound,
    S015ApplicationRefused,
)
from src.intevia.services.organism_role_service import OrganismRoleService


@dataclass(frozen=True)
class ActorRef:
    """A person named in a read, as the reader is shown them.

    identity_id is the Identity's primary key, or None where the record
    names no person. resolution is RESOLVED (display_name is the
    person's present resolved name), SEVERED (the resolution was removed
    and the identity recorded as severed; no name), NO_RESOLUTION (no
    resolution row; no name) or NOT_A_PERSON (no identity; no name).
    """
    identity_id: int | None
    resolution: str
    display_name: str | None


@dataclass(frozen=True)
class ApplicationView:
    """The application as submitted.

    Carries the application's UUID, its LO's primary key, the applicant
    (ActorRef), the predecessor's UUID if it names one, the settings and
    question versions it was answered against, the question's text and
    answer format, the answer, and its effective and recorded times.
    The application's own values do not change once recorded
    (Application is append-only); the applicant's name is the present
    one (_actor_ref). It carries no status; read_application_history
    returns the lineage events.
    """
    application_uuid: UUID
    living_organism_id: int
    applicant: ActorRef
    predecessor_application_uuid: UUID | None
    settings_version_uuid: UUID
    question_version_uuid: UUID
    question_text: str
    answer_format: str
    answer: str
    effective_at: datetime
    recorded_at: datetime


@dataclass(frozen=True)
class HistoryRow:
    """One event of the application's lineage, as recorded.

    sequence, action, prior_state, resulting_state, actor_state,
    actor_capacity, entry_mode, authority_decision_reference,
    evidence_reference, effective_at and recorded_at are stored values.
    actor, enterer and composer are ActorRefs resolved from the event's
    identity columns; a null column gives NOT_A_PERSON. actor_id names
    a person only for PARTY; a LIVING_ORGANISM actor has no person id.
    References are returned as text and are not followed.
    """
    sequence: int
    action: str
    prior_state: str | None
    resulting_state: str
    actor_state: str
    actor: ActorRef
    actor_capacity: str
    entry_mode: str
    enterer: ActorRef
    composer: ActorRef
    authority_decision_reference: str | None
    evidence_reference: str | None
    effective_at: datetime
    recorded_at: datetime


def _present_application_audiences(
    *, application: Application | None, reader: Identity, organism_pk: int,
    at: datetime, database_alias: str,
) -> tuple[bool, bool, bool]:
    """Return (identity_active, applicant, processing) for reader at at.

    Expects reader to be the caller's established Identity. Activity
    uses reader.pk; audience matches use reader.identity_id. This
    helper does not authenticate the caller or revalidate that pair.

    identity_active: the latest IdentityTransition for reader.pk with
    occurred_at no later than at, ordered by occurred_at then primary
    key, has resulting_state Identity.AccessState.ACTIVE. If absent
    or not ACTIVE, return (False, False, False) without further reads.
    There is no recorded-time cut.

    applicant: application is given and its applicant Identity's UUID
    equals reader.identity_id. Return (True, True, False) immediately:
    an applicant is never also processing, even when a Coordinator.

    processing: an ACTIVE membership in the LO named by organism_pk
    has an ACTIVE LO-scope COORDINATOR assignment with a qualifying
    term. Membership and assignment states are read as stored, not
    historically folded. _head_assign_term reads the ASSIGN event
    with the highest sequence, without a time cut or consultation of
    later END or REASSIGN events. An absent term, non-FIXED term or
    null end qualifies; a FIXED term with an end requires at < end.

    With application None, applicant is False. Reads only; database
    and term-evaluation errors propagate. Used by _authorise_read and
    the command support module's _viewer_has_audience.
    """
    latest_state = (
        IdentityTransition.objects.using(database_alias)
        .filter(identity_id=reader.pk, occurred_at__lte=at)
        .order_by("-occurred_at", "-pk")
        .values_list("resulting_state", flat=True)
        .first()
    )
    if latest_state != Identity.AccessState.ACTIVE:
        return False, False, False
    if application is not None:
        applicant_identity_id = Identity.objects.using(database_alias).filter(
            pk=application.applicant_id,
        ).values_list("identity_id", flat=True).first()
        if applicant_identity_id == reader.identity_id:
            return True, True, False

    offices = ContextualRoleAssignment.objects.using(database_alias).filter(
        membership__identity__identity_id=reader.identity_id,
        membership__living_organism_id=organism_pk,
        membership__state="ACTIVE",
        role_definition__code="COORDINATOR",
        role_definition__scope="LIVING_ORGANISM",
        state="ACTIVE",
    )
    from src.intevia.services.organism_role_service import _head_assign_term

    for office in offices:
        term = _head_assign_term(database_alias=database_alias, assignment_id=office.pk)
        if term is None or term[0] != "FIXED" or term[1] is None or at < term[1]:
            return True, False, True
    return True, False, False


class OrganismApplicationReader:
    """Serve an application and its history to a reader in its
    audience.

    Each read finds the application by UUID, takes the database's
    present time (clock_timestamp()) and authorises the reader at that
    time (_authorise_read) before reading anything further. Reads use
    the database alias given at construction and write nothing.
    """
    def __init__(self, *, authority, clock, database_alias: str = "default"):
        """Keep the authority, the clock and the database alias.

        Neither the authority nor the clock is used by the reads:
        _present_application_audiences decides the audience, and the time
        is the database's.
        """
        self._authority = authority
        self._clock = clock
        self._alias = database_alias

    def read_application(self, application_uuid: UUID, reader: Identity) -> ApplicationView:
        """Return the ApplicationView of the application for reader.

        Finds the application (_application; S015ApplicationNotFound
        "application not found" for an unknown UUID, before any audience
        check), takes the database time and authorises the reader
        (_authorise_read; S015ApplicationRefused). Then reads the
        predecessor's UUID, if the application names one, and the settings
        and question versions, and resolves the applicant (_actor_ref).

        The predecessor is given by UUID only; neither its content nor its
        own audience is read. Nothing is written.
        """
        application = self._application(application_uuid)
        at = self._database_time()
        self._authorise_read(application, reader, at)

        predecessor_uuid = None
        if application.predecessor_reference_id is not None:
            predecessor_uuid = (
                Application.objects.using(self._alias)
                .filter(pk=application.predecessor_reference_id)
                .values_list("application_uuid", flat=True)
                .first()
            )
        question = application.question_version
        settings = application.settings_version
        return ApplicationView(
            application_uuid=application.application_uuid,
            living_organism_id=application.living_organism_id,
            applicant=self._actor_ref(application.applicant_id),
            predecessor_application_uuid=predecessor_uuid,
            settings_version_uuid=settings.version_uuid,
            question_version_uuid=question.version_uuid,
            question_text=question.question_text,
            answer_format=question.answer_format,
            answer=application.answer,
            effective_at=application.effective_at,
            recorded_at=application.recorded_at,
        )

    def read_application_history(
        self, application_uuid: UUID, reader: Identity,
    ) -> tuple[HistoryRow, ...]:
        """Return the application's lineage events for reader, in sequence
        order.

        Finds the application and authorises the reader as read_application
        does (the unknown-UUID refusal comes first). Then reads every event
        of the application's lineage by raw SQL, ordered by sequence, with
        no time or state filter, and returns one HistoryRow per event; the
        actor, enterer and composer are resolved by _actor_ref. Nothing is
        written.
        """
        application = self._application(application_uuid)
        at = self._database_time()
        self._authorise_read(application, reader, at)
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT e.sequence, e.action, e.prior_state, e.resulting_state, e.actor_id, e.actor_state, e.actor_capacity, "
                "e.entry_mode, e.enterer_identity_id, e.composer_identity_id, e.authority_decision_reference, "
                "e.evidence_reference, e.effective_at, e.recorded_at "
                "FROM public.core_applicationlineageevent e "
                "JOIN public.core_applicationlineage l ON l.id = e.application_lineage_id "
                "WHERE l.application_id = %s "
                "ORDER BY e.sequence",
                [application.pk],
            )
            events = cursor.fetchall()
        return tuple(
            HistoryRow(
                sequence=row[0],
                action=row[1],
                prior_state=row[2],
                resulting_state=row[3],
                actor_state=row[5],
                actor=self._actor_ref(row[4]),
                actor_capacity=row[6],
                entry_mode=row[7],
                enterer=self._actor_ref(row[8]),
                composer=self._actor_ref(row[9]),
                authority_decision_reference=row[10],
                evidence_reference=row[11],
                effective_at=row[12],
                recorded_at=row[13],
            )
            for row in events
        )

    def _authorise_read(self, application: Application, reader: Identity, at: datetime) -> None:
        """Return if reader may read application at at; otherwise raise
        S015ApplicationRefused.

        Uses _present_application_audiences for the application's LO.
        Refuses with "S3FU read refused: reader not active" when the
        reader's identity is not ACTIVE at at. An active reader who is
        neither the applicant nor a current Coordinator of that LO is
        refused with "S3FU read refused: not in the application's
        audience".
        """
        identity_active, applicant, processing = _present_application_audiences(
            application=application,
            reader=reader,
            organism_pk=application.living_organism_id,
            at=at,
            database_alias=self._alias,
        )
        if not identity_active:
            raise S015ApplicationRefused("S3FU read refused: reader not active")
        if applicant or processing:
            return
        raise S015ApplicationRefused("S3FU read refused: not in the application's audience")

    def _application(self, application_uuid: UUID) -> Application:
        """Return the Application with application_uuid, or raise
        S015ApplicationNotFound "application not found".
        """
        application = (
            Application.objects.using(self._alias)
            .filter(application_uuid=application_uuid)
            .first()
        )
        if application is None:
            raise S015ApplicationNotFound("application not found")
        return application

    def _database_time(self) -> datetime:
        """Read and return clock_timestamp() from this reader's database alias.

        Executes a new clock query on each call. Does not use the injected
        clock or enforce increasing values between calls. Writes nothing.
        """
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            return cursor.fetchone()[0]

    def _actor_ref(self, identity_id: int | None) -> ActorRef:
        """Resolve an Identity primary key to an ActorRef.

        None gives NOT_A_PERSON with no name, without a query. Otherwise
        check core_identityresolutionsevered first: a row gives SEVERED
        with no name. If not severed, a core_identityresolution row gives
        RESOLVED with its display_name; no row gives NO_RESOLUTION.
        All non-null cases retain the supplied Identity primary key.

        The name is the current resolution, not the name at the event's
        time. This helper performs no audience check. It reads on this
        reader's database alias, writes nothing and lets read errors
        propagate.
        """
        if identity_id is None:
            return ActorRef(None, "NOT_A_PERSON", None)
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM public.core_identityresolutionsevered WHERE identity_id = %s",
                [identity_id],
            )
            if cursor.fetchone() is not None:
                return ActorRef(identity_id, "SEVERED", None)
            cursor.execute(
                "SELECT display_name FROM public.core_identityresolution WHERE identity_id = %s",
                [identity_id],
            )
            resolution = cursor.fetchone()
        if resolution is None:
            return ActorRef(identity_id, "NO_RESOLUTION", None)
        return ActorRef(identity_id, "RESOLVED", resolution[0])