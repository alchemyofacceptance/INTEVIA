"""Exact Policy publication choices for an authorised author on the governing Board."""

import json

from src.intevia.services import discussion, library_laws


# Match 0076's author test, including a lawful declaration carried from the predecessor.
_DECLARED = """NOT EXISTS (
    SELECT FROM unnest(m.author_ids) author WHERE NOT EXISTS (
        SELECT FROM public.core_libauthorclaim claim
         WHERE claim.version_id=v.id AND claim.author_id=author))"""


def choices(org, board, actor, at):
    """Return ready successors of governing local Policies, after the caller's INITIATE check.

    None means this reader or Board cannot propose publication. An empty list means
    they can, but no current draft qualifies. The projection releases titles and public coordinates only.
    """
    if not discussion._rows("""
            SELECT 1 FROM public.core_livingorganism lo
            JOIN public.core_discboard b ON b.organism_id=lo.id
            JOIN public.core_circle c ON c.id=b.circle_id AND c.kind='GOVERNANCE'
            WHERE lo.organism_id=%s AND b.board_uuid=%s
              AND public.disc_board_place_ok(lo.id,c.id,%s,%s)
              AND public.cq3_body_route(lo,b.board_uuid,'LO_COORDINATOR','POLICY',
                      'LIVING_ORGANISM',false,%s,%s)='CQ_GOVERNANCE_CIRCLE'
            """, [org, board["board_uuid"], actor.pk, at, at, at]):
        return None
    rows = discussion._rows(f"""
        SELECT law.binding, law.library_reference, split_part(v.content,chr(10),1) AS content,
               law.class_name, law.display_class,
               v.version_number
          FROM public.lib_laws_at(%s,%s,%s) law
          JOIN public.core_libraryresource r ON r.resource_id=law.library_reference AND r.state='draft'
          JOIN public.core_libraryresourceversion v ON v.id=r.current_version_id
               AND v.resource_id=r.id AND v.version_number>law.version_number
          JOIN public.core_libpolicyversion m ON m.version_id=v.id AND m.organism_id=%s
         WHERE law.issuer=%s AND law.scope_kind='LIVING_ORGANISM' AND law.class_name='POLICY'
               AND {_DECLARED}
         ORDER BY law.library_reference, v.version_number
        """, [org, at, at, board["organism_id"], org])
    return library_laws.law_display_rows(org, rows)


def exact(org, reference, number, *, offered):
    """Check the signed local coordinates. Only a ready head releases its stored wording."""
    rows = discussion._rows(f"""
        WITH target AS (
            SELECT v.content, r.current_version_id=v.id AND r.state='draft' AS current,
                   {_DECLARED} AS declared
              FROM public.core_libraryresource r
              JOIN public.core_libraryresourceversion v ON v.resource_id=r.id
              JOIN public.core_libpolicyversion m ON m.version_id=v.id
              JOIN public.core_livingorganism lo ON lo.id=m.organism_id
             WHERE lo.organism_id=%s AND r.resource_id=%s AND v.version_number=%s
               AND EXISTS (SELECT FROM public.lib_laws_at(lo.organism_id,public.intevia_clock_statement(),
                                                  public.intevia_clock_statement()) law
                            WHERE law.resource=r.id AND law.issuer=lo.organism_id
                              AND law.scope_kind='LIVING_ORGANISM' AND law.class_name='POLICY')
        )
        SELECT current, declared, CASE WHEN %s AND current AND declared THEN content END AS wording FROM target
        """, [org, reference, number, offered])
    if not rows:
        raise discussion.DiscussionUnavailable()
    return rows[0]


def attachment(org, reference, number, wording):
    """Build the exact 0076 instrument from server-read coordinates and immutable wording."""
    return {"kind": "PUBLISH_POLICY", "spec": {"organism": str(org), "resource": reference, "version": number},
            "text": wording}


def fingerprint(instrument):
    """Use the same canonical fingerprint that the database records on INITIATE."""
    row, = discussion._rows("SELECT public.disc_fingerprint(%s::jsonb) AS fingerprint",
                            [json.dumps(instrument, ensure_ascii=False)])
    return row["fingerprint"]


def recorded_attachment(scope, actor, opening, review):
    """Recover this exact receipt only for the writer's own governed replay check."""
    rows = discussion._rows("""
        SELECT a.request->'attachment' AS instrument FROM public.core_cqact a
        JOIN public.core_discboard b ON b.board_uuid=a.board_uuid AND b.organism_id=a.organism_id
        JOIN public.core_circle c ON c.id=b.circle_id
        JOIN public.core_livingorganism lo ON lo.id=a.organism_id
        WHERE a.act_uuid=%s AND a.operation='INITIATE' AND a.thread_uuid=%s
          AND a.actor_id=%s AND a.actor_epoch=%s AND c.circle_uuid=%s AND lo.organism_id=%s
          AND public.disc_board_place_ok(lo.id,b.circle_id,a.actor_id,public.intevia_clock_statement())
          AND a.request->>'version'=%s AND a.facts->>'attachment_fingerprint'=%s
          AND a.request#>>'{attachment,kind}'=%s
          AND a.request#>>'{attachment,spec,resource}'=%s AND a.request#>>'{attachment,spec,version}'=%s
        """, [scope["request_uuid"], scope["thread_uuid"], actor.pk, actor.access_epoch,
              scope["circle_uuid"], scope["organism_uuid"], opening, review["fingerprint"],
              "LAW" if review["intent"] == "adopt" else "PUBLISH_POLICY",
              review["resource"], str(review["number"])])
    return rows[0]["instrument"] if rows else None
