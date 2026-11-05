"""Founder declaration and fail-closed boundary for the still-absent content-evidence interface.

This module does not substitute upload/ownership/latest-editor fields for authorship.
No publish is reported until exact-release authorship and independent LO permission
interfaces can be implemented against authoritative domain records.
"""
from hashlib import sha256
from uuid import UUID
from django.db import connections
from core import models
from core.root_publication_contract import DECLARATION_TEXT,SOURCE_HASHES,VERIFICATION_REFERENCE
from src.intevia.services import circle_commands as circles

class PublicationRefused(ValueError):
    def __init__(self,code,message):
        self.code=code
        super().__init__(message)


def _office(alias,lo,actor,at):
    with connections[alias].cursor() as cursor:
        cursor.execute('SELECT public.r_founder_office(%s,%s,%s,%s)',[lo.pk,actor.pk,at,at])
        office=cursor.fetchone()[0]
    if office is None:
        raise PublicationRefused('R_EFFECTIVE_AUTHORITY_REQUIRED','This act requires effective Founder Steward authority. A nomination grants none; the declaration is reserved to its declarer.')
    return office


def declare(*,credential,organism_uuid,request_uuid:UUID,accept_declaration:bool,access_posture,alias='default'):
    """The explicit declaration act only. GENESIS and source-file possession do not invoke it."""
    if access_posture != 'PRIVATE_DECLARING_ACTOR':
        raise PublicationRefused('R_ACCESS_POSTURE_UNAVAILABLE','This service currently records the declaration privately for its declarer. Other disclosure policies need their governed access interface.')
    if type(request_uuid) is not UUID or accept_declaration is not True:
        raise PublicationRefused('R_DECLARATION_EXPLICIT','Read and explicitly declare the offered standing exception.')
    with circles.command_context(alias,credential,organism_uuid,require_member=True) as (lo,actor,at):
        office=_office(alias,lo,actor,at)
        old=models.RootPublicationDeclaration.objects.using(alias).filter(living_organism=lo).first()
        if old:
            if old.request_uuid==request_uuid and old.actor_id==actor.pk:return old
            raise PublicationRefused('R_DECLARATION_ALREADY_RECORDED','The standing declaration is already recorded. Changes require their own governed act.')
        return models.RootPublicationDeclaration.objects.using(alias).create(living_organism=lo,actor=actor,
            actor_access_epoch=actor.access_epoch,founder_office_id=office,authority_basis=lo.founding_authority_basis,
            prior_root_event=lo.head_event,verification_reference=VERIFICATION_REFERENCE,access_posture=access_posture,
            text=DECLARATION_TEXT,text_sha256=sha256(DECLARATION_TEXT.encode()).hexdigest(),source_hashes=SOURCE_HASHES,
            request_uuid=request_uuid,effective_at=at,received_at=at)


def publish_own(*,credential,organism_uuid,domain,version_id,alias='default'):
    """Refuse until the designed exact-version content-evidence adapter exists; no publish side effect.

    This is not a successful publication implementation. It deliberately exposes the
    absent interface instead of accepting caller-asserted authors or permissions.
    Ordinary content publication services remain on their existing authority routes.
    """
    if domain not in ('EDUCATION','LIBRARY'):
        raise PublicationRefused('R_PUBLICATION_SCOPE','The exception covers EDUCATION and LIBRARY only.')
    from src.intevia.services.root_continuity import publication_capacity, ContinuityRefused
    with circles.command_context(alias,credential,organism_uuid) as (lo,actor,at):
        try:
            publication_capacity(alias, lo, actor, domain, at)
        except ContinuityRefused as refusal:
            raise PublicationRefused('R_EFFECTIVE_AUTHORITY_REQUIRED', str(refusal)) from refusal
        declaration=models.RootPublicationDeclaration.objects.using(alias).filter(living_organism=lo,effective_at__lte=at,recorded_at__lte=at).first()
        if declaration is None:
            raise PublicationRefused('R_DECLARATION_REQUIRED','Record your standing declaration before using this publication exception.')
        if domain=='EDUCATION':
            raise PublicationRefused('R_COURSE_RELEASE_INTERFACE_MISSING','The EDUCATION domain does not yet expose the exact Course Release and its complete Session Version set. A CourseVersion row is not a substitute.')
        if not models.LibraryResourceVersion.objects.using(alias).filter(pk=version_id).exists():
            raise PublicationRefused('R_EXACT_VERSION_REQUIRED','The exact content version is not recorded.')
        raise PublicationRefused('R_AUTHORSHIP_INTERFACE_MISSING','Publication is unavailable: the domain does not yet expose verified authorship for the exact resource version or every Session Version in the exact Course Release, together with required independent LO permission.')
