"""Explicit alpha entry for the two ratified GRP-13 instruments."""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5

from django.db import connection, transaction

from core import models
from src.intevia.services.law_acts import AdoptLawCommand, LawProposal, OrganismLawService
from src.intevia.services.library_service import LibraryService


RATIFIED_ON = date(2026, 6, 27)


class CharterEntryRefused(RuntimeError):
    """An initial charter entry could not be completed."""


@dataclass(frozen=True)
class Charter:
    resource: str
    filename: str
    digest: str

    @property
    def ratification(self):
        return f"GRP-13:ratified:{RATIFIED_ON.isoformat()}:{self.filename}"

    def check(self, data):
        if sha256(data).hexdigest() != self.digest:
            raise CharterEntryRefused(f"The bytes of {self.filename} differ from the ratified text.")


CHARTERS = (
    Charter("intevia:charter:free-will", "FREE_WILL_CHARTER.md", "ef09fca88d32a4fe4dbb7e76f3517fc5db0621ad60bbf0d813730d820eb26fc5"),
    Charter("intevia:charter:care-constitutional", "CARE_CONSTITUTIONAL_CHARTER.md", "01c70ecc68f0dd5d0ec737d3877bd5dfa2d556c8136387e020dc8da3b8ee111b"),
)
_DOCUMENTS = Path(__file__).resolve().parents[3] / "docs" / "constitution"


def _published_version(library, charter, text, actor):
    resource = models.LibraryResource.objects.select_for_update().filter(resource_id=charter.resource).first()
    if resource is None:
        resource = library.create_resource(
            identity=actor.credential, resource_id=charter.resource, content=text, exact_content=True,
            evidence_reference=charter.ratification,
        )
        library.transition_resource(
            identity=actor.credential, resource_id=charter.resource, command="publish_library_resource",
            evidence_reference=f"GRP-13:initial-publication:{charter.filename}",
        )
    # Also used after an interruption between publication and admission. An existing
    # name is insufficient: require the original version, ratification and publication.
    version = models.LibraryResourceVersion.objects.filter(resource=resource, version_number=1).first()
    if (version is None or version.created_by_id != actor.pk
            or not version.evidence_references.filter(reference=charter.ratification, reference_type="creation",
                                                       supplied_by=actor).exists()
            or not resource.transitions.filter(version=version, command="publish_library_resource",
                                               actor=actor, to_state="published").exists()):
        raise CharterEntryRefused(f"Cannot establish the initial publication of {charter.filename}.")
    charter.check(version.content.encode("utf-8"))
    return version.version_number


def _prepare_publications(credential, library):
    texts = {}
    for charter in CHARTERS:
        data = (_DOCUMENTS / charter.filename).read_bytes()
        charter.check(data)
        texts[charter] = data.decode("utf-8")
    with transaction.atomic():
        actor = models.Identity.objects.select_for_update().get(credential=credential)
        root = models.LivingOrganism.objects.select_for_update().get(platform_root=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.r_founder_office(%s,%s,statement_timestamp(),statement_timestamp())",
                           [root.pk, actor.pk])
            office = cursor.fetchone()[0]
        if actor.pk != root.founder_identity_id or office is None:
            raise CharterEntryRefused("The effective Founder & Steward must present the charter entry.")
        versions = {c: _published_version(library, c, texts[c], actor) for c in CHARTERS}
    return root.organism_id, versions


def enter_constitutional_charters(*, credential, library_authority, request_id: UUID):
    """Publish and adopt on explicit invocation. Reuse request_id to recover the same receipts.

    Supply the presenter's credential and the configured LIBRARY ContributionAuthority.
    Both publications precede intake because LAW rechecks publication at receipt time.
    All four LAW effects commit together. Each has its own intake, decision, event and receipt.
    """
    if not isinstance(request_id, UUID):
        raise ValueError("Charter entry needs a request UUID.")
    if connection.in_atomic_block or not connection.get_autocommit():
        raise CharterEntryRefused("Start charter entry outside a transaction so LAW intake can commit.")
    root, versions = _prepare_publications(credential, LibraryService(authority=library_authority))
    service = OrganismLawService()
    admitted = []
    for charter, version in versions.items():
        for scope in ("PLATFORM", "LIVING_ORGANISM"):
            coordinate = f"{charter.resource}:{scope}"
            key = "charter-entry:" + str(uuid5(request_id, f"{root}:{coordinate}"))
            proposal = LawProposal(organism=root, resource=charter.resource, proposal=uuid5(root, coordinate),
                                   scope=scope, operation="ADOPT", intent="ADOPT", law_class="ARTICLE",
                                   version=version, requested_route="FOUNDER_STEWARD")
            command = AdoptLawCommand(credential=credential, proposal=proposal, request_reference=key,
                                      idempotency_key=key)
            admitted.append((command, service._admit(command)))
    results = []
    with transaction.atomic():
        for command, bound in admitted:
            result = service._execute_admitted(command, bound)
            if result.status != "ADOPT" or result.route != "FOUNDER_STEWARD":
                raise CharterEntryRefused(result.message or "The Founder & Steward's charter adoption was refused.")
            results.append(result)
    return tuple(results)
