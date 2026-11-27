"""Exact local Policy revision and authorship reads for the member pages."""

from uuid import UUID

from django.db import transaction

from src.intevia.services import cq_reads, discussion, library_policy
from src.intevia.services.library_laws import LawReadUnavailable, our_law_version


def revision_access(credential, org, law, status):
    """Read the writer's effective route and head only for an already released local Policy."""
    if (law.get("scope_kind") != "LIVING_ORGANISM" or law["class_name"] != "POLICY"
            or law["issuer"] != org or law["inherited"]):
        return None
    rows = library_policy._rows("""
        SELECT m.author_ids, i.id AS reader, r.current_version_id AS head, r.state,
               public.lib_policy_source(lo.id,i.id,CASE WHEN lo.platform_root THEN 'ROOT_EXCEPTION'
                   ELSE 'COORDINATOR_ROUTE' END,public.intevia_clock_statement(),
                   public.intevia_clock_statement()) IS NOT NULL AS authorised
          FROM public.core_libpolicyversion m
          JOIN public.core_livingorganism lo ON lo.id=m.organism_id
          JOIN public.core_libraryresourceversion v ON v.id=m.version_id
          JOIN public.core_libraryresource r ON r.id=v.resource_id
          JOIN public.core_identity i ON i.credential_id=%s
         WHERE lo.organism_id=%s AND m.version_id=%s AND r.resource_id=%s
        """, [credential.pk, org, law["version"], law["library_reference"]])
    if not rows:
        return None
    row = rows[0]
    row["current"] = (status == "In force now" and row["head"] == law["version"]
                      and row["state"] == "published")
    return row


def revision_was_received(request_uuid, predecessor):
    """Allow the exact reviewed request to reach the writer's full replay comparison."""
    return bool(library_policy._rows("""
        SELECT 1 FROM public.core_libpolicyversion m
          JOIN public.core_libraryresourceversion v ON v.id=m.version_id
         WHERE m.request_uuid=%s AND v.predecessor_id=%s
        """, [request_uuid, predecessor]))


def contributor_rows(identities, carried, drafter):
    """Name an admitted roster using the current, unsevered identity resolution."""
    rows = library_policy._rows("""
        SELECT i.id, i.identity_id, coalesce(nullif(btrim(r.display_name),''),'Member') AS name
          FROM public.core_identity i
          LEFT JOIN public.core_identityresolution r ON r.identity_id=i.id AND NOT EXISTS
             (SELECT FROM public.core_identityresolutionsevered s WHERE s.identity_id=i.id)
         WHERE i.id=ANY(%s) ORDER BY i.id
        """, [sorted(identities)])
    for row in rows:
        row["drafter"] = row["id"] == drafter
        row["carried"] = row["id"] in carried
    return rows


def payment_citation(credential, org, circle, thread, result):
    """Resolve one exact reader-visible, plain, qualified Board decision."""
    decision = cq_reads.result_post(credential=credential, organism_uuid=org, circle_uuid=circle,
                                    thread_uuid=thread, result_uuid=result, governance_only=True, detail=True)
    if not decision["carried_no_attached_act"]:
        raise discussion.DiscussionUnavailable()
    return decision


def cited_payment(credential, org, stored):
    """Release display facts only after the reader can open this exact plain Board result."""
    text = stored.strip()
    try:
        result = UUID(text)
    except ValueError:
        return None
    if str(result) != text.lower():
        return None
    # The stored clause lacks the address. Resolve coordinates within its organisation;
    # resolving coordinates alone never grants a result, its facts or its link.
    rows = library_policy._rows("""
        SELECT c.circle_uuid, act.thread_uuid
          FROM public.core_cqact act
          JOIN public.core_livingorganism lo ON lo.id=act.organism_id
          JOIN public.core_discboard b ON b.board_uuid=act.board_uuid AND b.organism_id=lo.id
          JOIN public.core_circle c ON c.id=b.circle_id AND c.parent_organism_id=lo.id
         WHERE lo.organism_id=%s AND act.act_uuid=%s AND act.operation='SETTLE' AND c.kind='GOVERNANCE'
        """, [org, result])
    if len(rows) != 1:
        return None
    circle, thread = rows[0]["circle_uuid"], rows[0]["thread_uuid"]
    try:
        payment = payment_citation(credential, org, circle, thread, result)
    except discussion.DiscussionUnavailable:
        return None
    return dict(wording=payment["wording"], body=payment["deciding_body"]["full_name"],
                recorded_at=payment["recorded_at"], address=(org, circle, thread, result))


@transaction.atomic
def draft_receipt(credential, org, reference, number):
    """Release a current successor draft to its effective reviser or its named drafter.

    Carried credit alone grants no new declaration page. Resolve only draft coordinates
    before the reader check, and fetch its unpublished wording only after that check.
    """
    with library_policy._context(credential, org) as (owner, actor, received):
        rows = library_policy._rows("""
            SELECT v.id, v.predecessor_id, m.author_ids, m.actor_id, m.content_sha256,
                   p.author_ids AS carried
              FROM public.core_libraryresource r
              JOIN public.core_libraryresourceversion v ON v.id=r.current_version_id
              JOIN public.core_libpolicyversion m ON m.version_id=v.id
              JOIN public.core_libpolicyversion p ON p.version_id=v.predecessor_id
                 AND p.organism_id=m.organism_id
             WHERE r.resource_id=%s AND v.version_number=%s AND m.organism_id=%s AND r.state='draft'
            """, [reference, number, owner.pk])
        if not rows:
            raise LawReadUnavailable()
        work = rows[0]
        own = work["actor_id"] == actor.pk and actor.pk in work["author_ids"]
        if not own:
            try:
                route = "ROOT_EXCEPTION" if owner.platform_root else "COORDINATOR_ROUTE"
                library_policy._source(owner, actor, route, received)
            except library_policy.PolicyRefused:
                raise LawReadUnavailable() from None
        bindings = library_policy._rows("""
            SELECT binding, version_number FROM public.lib_laws_at(%s,%s,%s)
             WHERE scope_kind='LIVING_ORGANISM' AND class_name='POLICY' AND issuer=%s
               AND version=%s AND library_reference=%s
            """, [org, received, received, org, work["predecessor_id"], reference])
        if len(bindings) != 1:
            raise LawReadUnavailable()
        back = bindings[0]
        try:
            our_law_version(credential=credential, organism_uuid=org, **back)
        except LawReadUnavailable:
            # A drafter may retain their own receipt after leaving the organisation.
            # Authorship does not release an organisation law address.
            back["binding"] = None
        work.update(back, own=own, number=number, reference=reference)
        work["authors"] = contributor_rows(work["author_ids"], work["carried"], work["actor_id"])
        work["content"] = library_policy._rows(
            "SELECT content FROM public.core_libraryresourceversion WHERE id=%s", [work["id"]])[0]["content"]
        claims = library_policy._rows("""
            SELECT recorded_at, declaration FROM public.core_libauthorclaim
             WHERE version_id=%s AND author_id=%s AND inherited_from IS NULL
            """, [work["id"], work["actor_id"]])
        work["claim"] = claims[0] if claims else None
        return work
