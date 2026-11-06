"""LIB's bounded Policy journey. Authorship, release authority and adoption are distinct."""
from contextlib import contextmanager
from hashlib import sha256
import json
from uuid import UUID, uuid4, uuid5

from django.db import connections, transaction

from core import models
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.library_service import LibraryService
from src.intevia.services.law_records import _records
from src.intevia.services.organism_command_support import _lock_actor, _lock_registered_organism

AUTHOR_DECLARATION = "I declare my authorship of this exact version and confirm its recorded author list is complete."
CARRIED_ATTRIBUTION = "Attribution carried from the cited predecessor declaration; no new declaration by that author."


class PolicyRefused(ValueError):
    """A Policy act lacks exact evidence or its present authority."""

    def __init__(self, message, *, code=None):
        self.code = code
        super().__init__(message)


def _refuse_authority(*, root=False):
    message = "This act requires the selected organisation's effective authority at receipt and execution. CQ is not available yet."
    if root:
        from src.intevia.services.root_publication import PublicationRefused
        raise PublicationRefused('R_EFFECTIVE_AUTHORITY_REQUIRED', message)
    raise PolicyRefused(message, code='LIB_AUTHORITY_SOURCE_REQUIRED')


def _uuid(value):
    if type(value) is not UUID:
        raise PolicyRefused("Choose an exact UUID for this act.")
    return value


def _rows(sql, params=()):
    with connections['default'].cursor() as c:
        c.execute(sql, params)
        return _records(c) if c.description else []


def _decoded_authority_source(value):
    """Raw PostgreSQL cursors may return jsonb as JSON text."""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise PolicyRefused("The recorded authority source is unavailable.")
    return value


def _now():
    return _rows('SELECT statement_timestamp() AS stamp')[0]['stamp']


def _meta(version_id):
    rows = _rows('SELECT m.*,lo.organism_id AS organism_uuid FROM public.core_libpolicyversion m '
                 'JOIN public.core_livingorganism lo ON lo.id=m.organism_id WHERE m.version_id=%s', [version_id])
    if not rows:
        raise PolicyRefused("This exact version has no recorded Policy provenance and complete author-list source.")
    return rows[0]


@contextmanager
def _context(credential, organism_uuid, version_id=None, *, root=False):
    _uuid(organism_uuid)
    # Only immutable provenance is read before locks, to choose the full lock set.
    owners = _rows('SELECT lo.organism_id FROM public.core_libpolicyversion p JOIN public.core_livingorganism lo '
                   'ON lo.id=p.organism_id WHERE p.version_id=%s', [version_id]) if version_id is not None else []
    with transaction.atomic():
        received = _now()
        actor = _lock_actor(database_alias='default', credential=credential, unavailable=PolicyRefused)
        # Resolve the immutable anchor before the registered-organism lock can
        # expose an existence error. Unknown and unauthorised use one refusal.
        lo = models.LivingOrganism.objects.filter(organism_id=organism_uuid).first()
        if lo is None:
            _refuse_authority(root=root)
        ids = {organism_uuid, *(r['organism_id'] for r in owners)}
        for key in sorted(ids, key=str):
            _lock_registered_organism('default', key)
        yield lo, actor, received


def _refuse_version():
    raise PolicyRefused("This Policy version is unavailable for this act.", code='LIB_VERSION_UNAVAILABLE')


@contextmanager
def _version_context(credential, version_id, *, release=False, route=None):
    """Authenticate before provenance; unavailable and inaccessible versions agree.

    Listed authors may attest their own contribution without holding an office or
    membership in the owning LO. Release still requires that LO's effective office.
    """
    with transaction.atomic():
        received = _now()
        actor = _lock_actor(database_alias='default', credential=credential, unavailable=PolicyRefused)
        try:
            meta = _meta(version_id)
        except PolicyRefused:
            _refuse_version()
        _lock_registered_organism('default', meta['organism_uuid'])
        lo = models.LivingOrganism.objects.get(pk=meta['organism_id'])
        if release:
            route = route or ('ROOT_EXCEPTION' if lo.platform_root else 'COORDINATOR_ROUTE')
            try:
                _source(lo, actor, route, received)
            except PolicyRefused:
                _refuse_version()
        elif actor.pk not in meta['author_ids']:
            _refuse_version()
        yield lo, actor, received, meta, route


def _source(lo, actor, route, received):
    rows = _rows('SELECT public.lib_policy_source(%s,%s,%s,%s,%s)::text AS original, '
                 'public.lib_policy_source(%s,%s,%s,statement_timestamp(),statement_timestamp())::text AS current',
                 [lo.pk, actor.pk, route, received, received, lo.pk, actor.pk, route])
    row = rows[0]
    if row['original'] is None or row['original'] != row['current']:
        _refuse_authority()
    return row['current']


class _PolicyAuthority(ContributionAuthority):
    """D3's scoped replacement: real office facts, without a synthetic ProfileRole."""
    def __init__(self, lo, actor, route, received, reference):
        self.lo, self.actor, self.route, self.received, self.reference = lo, actor, route, received, reference

    def evaluate(self, *, identity, action, target, timestamp):
        reference = target if isinstance(target, str) else target.resource_id
        if identity.pk != self.actor.credential_id or reference != self.reference or action not in (
            'create_library_resource', 'create_library_resource_version', 'publish_library_resource'
        ):
            raise PolicyRefused("This Policy capability does not authorise that actor, action or resource.")
        # Actor and credential are already locked before organisation/resource locks.
        return self.actor, 'lib-policy:' + _source(self.lo, self.actor, self.route, self.received)


def _authors(identities):
    if not isinstance(identities, (tuple, list)) or not identities:
        raise PolicyRefused("Record the complete author list for this exact version.")
    for value in identities:
        _uuid(value)
    rows = list(models.Identity.objects.filter(identity_id__in=identities).values_list('pk', flat=True))
    if len(rows) != len(identities) or len(set(identities)) != len(identities):
        raise PolicyRefused("Every author must name one distinct recorded identity.")
    return sorted(rows)


def _claim(version_id, author_id, actor, received, inherited_from=None):
    key = uuid4()
    _rows('INSERT INTO public.core_libauthorclaim(claim_uuid,version_id,author_id,actor_id,actor_epoch,inherited_from,declaration,received_at) '
          'VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
          [key, version_id, author_id, actor.pk, actor.access_epoch, inherited_from,
           AUTHOR_DECLARATION if inherited_from is None else CARRIED_ATTRIBUTION, received])
    return key


def draft_policy(*, credential, organism_uuid, resource_reference, content, authors,
                 request_uuid, declare_own_authorship=False, predecessor_version_id=None, route=None):
    """Create/revise under office authority; explicit authorship supplies no capability.

    ``authors`` is the complete author list being recorded, not a permission list.
    Carried authors retain their predecessor declarations without a new veto over revisions.
    New contributors must declare against this exact version before publication.
    """
    _uuid(request_uuid)
    if type(declare_own_authorship) is not bool:
        raise PolicyRefused("Choose whether you explicitly declare your own authorship.")
    if not isinstance(content, str) or not content.strip():
        raise PolicyRefused("Write the Policy text before saving it.")
    content = content.strip()
    digest = sha256(content.encode()).hexdigest()
    with _context(credential, organism_uuid, predecessor_version_id) as (lo, actor, received):
        route = route or ('ROOT_EXCEPTION' if lo.platform_root else 'COORDINATOR_ROUTE')
        source = _source(lo, actor, route, received)
        author_ids = _authors(authors)
        if declare_own_authorship and actor.pk not in author_ids:
            raise PolicyRefused("Your own declaration must appear in the recorded author list.")
        prior = _rows('SELECT p.*,v.predecessor_id,r.resource_id FROM public.core_libpolicyversion p '
                      'JOIN public.core_libraryresourceversion v ON v.id=p.version_id '
                      'JOIN public.core_libraryresource r ON r.id=v.resource_id WHERE p.request_uuid=%s', [request_uuid])
        if prior:
            old = prior[0]
            if (old['actor_id'], old['organism_id'], old['resource_id'], old['content_sha256'], old['author_ids'],
                old['predecessor_id'], old['declared_self'], _decoded_authority_source(old['authority_source'])['route']) != (
                actor.pk, lo.pk, resource_reference, digest, author_ids, predecessor_version_id, declare_own_authorship, route):
                raise PolicyRefused("This request already records different Policy content or authorship.")
            return models.LibraryResourceVersion.objects.get(pk=old['version_id'])
        library = LibraryService(authority=_PolicyAuthority(lo, actor, route, received, resource_reference))
        inherited = []
        if predecessor_version_id is None:
            resource = library.create_resource(identity=credential, resource_id=resource_reference, content=content,
                evidence_reference='lib-policy-draft:' + str(request_uuid), occurred_at=received)
            version = resource.current_version
        else:
            meta = _meta(predecessor_version_id)
            predecessor = models.LibraryResourceVersion.objects.select_related('resource').get(pk=predecessor_version_id)
            models.LibraryResource.objects.select_for_update().get(pk=predecessor.resource_id)
            if meta['organism_id'] != lo.pk or predecessor.resource.resource_id != resource_reference or predecessor.resource.current_version_id != predecessor.pk:
                raise PolicyRefused("Revise the owning organisation's current exact Policy version.")
            # Carry one attribution per author. Prefer their explicit declaration
            # when the predecessor also records an earlier carried attribution.
            inherited = _rows('SELECT DISTINCT ON (author_id) * FROM public.core_libauthorclaim '
                              'WHERE version_id=%s ORDER BY author_id,(inherited_from IS NULL) DESC', [predecessor.pk])
            if set(meta['author_ids']) - set(author_ids) or {c['author_id'] for c in inherited} != set(meta['author_ids']):
                raise PolicyRefused("Retain every prior contributor and complete the predecessor's authorship evidence first.")
            version = library.create_successor_version(identity=credential, resource_id=resource_reference, content=content,
                evidence_reference='lib-policy-draft:' + str(request_uuid), occurred_at=received)
        _rows('INSERT INTO public.core_libpolicyversion(version_id,organism_id,request_uuid,actor_id,actor_epoch,authority_source,author_ids,content_sha256,received_at,declared_self) '
              'VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)',
              [version.pk, lo.pk, request_uuid, actor.pk, actor.access_epoch, source, author_ids, digest, received, declare_own_authorship])
        for claim in inherited:
            if not (claim['author_id'] == actor.pk and declare_own_authorship):
                _claim(version.pk, claim['author_id'], actor, received, claim['claim_uuid'])
        if declare_own_authorship:
            _claim(version.pk, actor.pk, actor, received)
        return version


def declare_authorship(*, credential, version_id, content_sha256, authors, affirm):
    """A signed-in author explicitly confirms the exact bytes and complete author list."""
    with _version_context(credential, version_id) as (_, actor, received, meta, _):
        if affirm is not True:
            raise PolicyRefused("Explicitly confirm your authorship declaration.")
        if content_sha256 != meta['content_sha256'] or _authors(authors) != meta['author_ids']:
            raise PolicyRefused("The exact text, complete author list or declaring author differs.")
        old = _rows('SELECT claim_uuid FROM public.core_libauthorclaim '
                    'WHERE version_id=%s AND author_id=%s AND inherited_from IS NULL', [version_id, actor.pk])
        if old:
            return old[0]['claim_uuid']
        return _claim(version_id, actor.pk, actor, received)


def _permission(version_id, lo, actor, route, received):
    source = _source(lo, actor, route, received)
    old = _rows('SELECT * FROM public.core_libreleasepermission WHERE version_id=%s', [version_id])
    if old:
        return old[0]['permission_uuid']
    key = uuid4()
    _rows('INSERT INTO public.core_libreleasepermission(permission_uuid,version_id,actor_id,actor_epoch,authority_source,received_at) '
          'VALUES(%s,%s,%s,%s,%s::jsonb,%s)', [key, version_id, actor.pk, actor.access_epoch, source, received])
    return key


def permit_release(*, credential, version_id, route=None):
    """Owning organisation's exact-version permission; neither publication nor adoption."""
    with _version_context(credential, version_id, release=True, route=route) as (lo, actor, received, _, route):
        return _permission(version_id, lo, actor, route, received)


def _insert_fact(*, operation, kind, version, lo, actor, source, permission, declaration, transition, received):
    key = uuid4()
    _rows('INSERT INTO public.core_libpublicationfact(fact_uuid,operation_uuid,kind,version_id,organism_id,actor_id,actor_epoch,'
          'authority_source,permission_uuid,declaration_id,transition_id,self_accepted,independent_review,received_at) '
          'VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,false,%s)',
          [key, operation, kind, version.pk, lo.pk, actor.pk, actor.access_epoch, source, permission,
           declaration.pk if declaration else None, transition.pk if transition else None, declaration is not None, received])
    return key


def _publish(*, credential, organism_uuid, version_id, request_uuid, route, root=False):
    from src.intevia.services.root_publication import PublicationRefused
    if request_uuid is not None or not root:
        _uuid(request_uuid)
    with _context(credential, organism_uuid, version_id, root=root) as (lo, actor, received):
        # Both the UUID and the live credential/Identity have now been validated.
        if request_uuid is None:
            request_uuid = uuid5(organism_uuid, f'LIBRARY:{version_id}:{actor.credential_id}')
        try:
            source = _source(lo, actor, route, received)
        except PolicyRefused as exc:
            if root:
                raise PublicationRefused('R_EFFECTIVE_AUTHORITY_REQUIRED', str(exc)) from exc
            raise
        declaration = None
        if root:
            declaration = models.RootPublicationDeclaration.objects.filter(living_organism=lo,
                effective_at__lte=received, recorded_at__lte=received).first()
            if declaration is None:
                raise PublicationRefused('R_DECLARATION_REQUIRED', 'Record your standing declaration before using this publication exception.')
        version = models.LibraryResourceVersion.objects.select_related('resource').filter(pk=version_id).first()
        if version is None:
            if root:
                raise PublicationRefused('R_EXACT_VERSION_REQUIRED', 'The exact content version is not recorded.')
            raise PolicyRefused("The exact Policy version is unavailable.")
        models.LibraryResource.objects.select_for_update().get(pk=version.resource_id)
        try:
            meta = _meta(version_id)
        except PolicyRefused as exc:
            if root:
                raise PublicationRefused('R_AUTHORSHIP_INTERFACE_MISSING', str(exc)) from exc
            raise
        old = _rows('SELECT * FROM public.core_libpublicationfact WHERE operation_uuid=%s ORDER BY kind', [request_uuid])
        if old:
            if any((r['version_id'], r['actor_id'], r['organism_id'], _decoded_authority_source(r['authority_source'])['route']) !=
                   (version_id, actor.pk, lo.pk, route) for r in old):
                raise PolicyRefused("This publication request already names a different act.")
            return {r['kind'].lower(): r['fact_uuid'] for r in old}
        if version.resource.current_version_id != version.pk or version.resource.state != 'draft':
            raise PolicyRefused("Publish the current draft's exact version, or retry its original publication request.")
        claims = _rows('SELECT author_id FROM public.core_libauthorclaim WHERE version_id=%s', [version_id])
        if {c['author_id'] for c in claims} != set(meta['author_ids']):
            raise PolicyRefused("Every recorded contributor needs authorship evidence before publication.")
        if root and meta['author_ids'] != [actor.pk]:
            raise PublicationRefused('R_SOLE_AUTHOR_REQUIRED', 'Co-authored work follows its ordinary publication route; this exception requires sole recorded authorship.')
        if not root and lo.pk != meta['organism_id']:
            raise PolicyRefused("Only the owning organisation may use ordinary Policy publication.")
        if lo.pk == meta['organism_id']:
            permission = _permission(version_id, lo, actor, route, received)
        else:
            permissions = _rows('SELECT permission_uuid FROM public.core_libreleasepermission WHERE version_id=%s', [version_id])
            if not permissions:
                raise PublicationRefused('R_LO_PERMISSION_REQUIRED', "The owning organisation must first authorise this exact version's release.")
            permission = permissions[0]['permission_uuid']
        result = {}
        args = dict(operation=request_uuid, version=version, lo=lo, actor=actor, source=source,
                    permission=permission, declaration=declaration, received=received)
        if root:
            result['acceptance'] = _insert_fact(kind='ACCEPTANCE', transition=None, **args)
        library = LibraryService(authority=_PolicyAuthority(lo, actor, route, received, version.resource.resource_id))
        transition = library.transition_resource(identity=credential, resource_id=version.resource.resource_id,
            command='publish_library_resource', evidence_reference='lib-policy-publish:' + str(request_uuid), occurred_at=received)
        result['publication'] = _insert_fact(kind='PUBLICATION', transition=transition, **args)
        return result


def publish_policy(*, credential, organism_uuid, version_id, request_uuid, route='COORDINATOR_ROUTE'):
    """Release under the selected office. Authorship grants no publishing authority."""
    if route not in ('COORDINATOR_ROUTE', 'GOVERNANCE_INTERIM'):
        raise PolicyRefused("Use an available organisation publication route; CQ is not available yet.")
    return _publish(credential=credential, organism_uuid=organism_uuid, version_id=version_id,
                    request_uuid=request_uuid, route=route)


def publish_root(*, credential, organism_uuid, version_id, request_uuid):
    return _publish(credential=credential, organism_uuid=organism_uuid, version_id=version_id,
                    request_uuid=request_uuid, route='ROOT_EXCEPTION', root=True)
