"""Reader-specific destinations for an adopted Policy's recorded Board publication."""

from src.intevia.services import cq_reads, discussion, library_policy


def result_address(scope):
    """Return the four named route coordinates of an independently released result."""
    return (scope["organism_uuid"], scope["circle_uuid"], scope["thread_uuid"], scope["result_uuid"])


def board_publication(credential, org, law):
    """Read this version's Board publication at adoption, then admit its result separately today."""
    if (law.get("scope_kind") != "LIVING_ORGANISM" or law["class_name"] != "POLICY"
            or law["issuer"] != org or law["inherited"]):
        return None
    rows = library_policy._rows("""
        SELECT f.fact_uuid, r.act_uuid AS result, c.circle_uuid, r.thread_uuid
          FROM public.core_libpublicationfact f
          JOIN public.core_libraryresourceversion v ON v.id=f.version_id
          JOIN public.core_libraryresourcetransition tr ON tr.id=f.transition_id
               AND tr.version_id=v.id AND tr.resource_id=v.resource_id AND tr.to_state='published'
          JOIN public.core_livingorganism lo ON lo.id=f.organism_id
          JOIN public.core_cqact r ON r.act_uuid=f.operation_uuid AND r.organism_id=lo.id
               AND r.operation='SETTLE' AND r.facts->>'disposition'='APPLIED'
          JOIN public.core_cqact i ON i.request->>'result'=r.act_uuid::text AND i.organism_id=r.organism_id
               AND i.board_uuid=r.board_uuid AND i.thread_uuid=r.thread_uuid AND i.operation='IMPLEMENT'
               AND i.facts->>'disposition'='APPLIED' AND i.facts->'result_authority'=f.authority_source
               AND i.facts#>>'{domain_plan,domain}'='PUBLISH_POLICY'
               AND i.facts#>>'{domain_plan,version}'=v.id::text
               AND i.facts#>>'{domain_plan,resource}'=v.resource_id::text
          JOIN public.core_discboard b ON b.board_uuid=r.board_uuid AND b.organism_id=lo.id
          JOIN public.core_circle c ON c.id=b.circle_id AND c.parent_organism_id=lo.id AND c.kind='GOVERNANCE'
         WHERE lo.organism_id=%s AND f.kind='PUBLICATION' AND f.version_id=%s AND v.resource_id=%s
    """, [org, law["version"], law["resource"]])
    if len(rows) != 1:
        return None
    target = rows[0]
    try:
        fact = library_policy.policy_act_record(credential=credential, organism_uuid=org,
            act_uuid=target["fact_uuid"], at=law["in_force_from"], known_at=law["adoption_recorded_at"])
    except discussion.DiscussionUnavailable:
        return None
    if (fact["operation"] != "PUBLICATION" or fact["act"] != target["fact_uuid"]
            or fact["version_id"] != law["version"] or fact["authority_source"]["kind"] != "CQ_RESULT"):
        return None
    publication = dict(version=law["version_number"], at=fact["effective_at"])
    scope = dict(credential=credential, organism_uuid=org, circle_uuid=target["circle_uuid"],
                 thread_uuid=target["thread_uuid"], result_uuid=target["result"])
    try:
        result = cq_reads.result_post(**scope, governance_only=True)
    except discussion.DiscussionUnavailable:
        return publication
    if result["post_uuid"] == target["result"]:
        publication["result_address"] = result_address(scope)
    return publication
