"""Read published successors of local Policies and construct the reviewed LAW instrument."""

from uuid import UUID

from src.intevia.services import discussion, library_laws
from src.intevia.services.law_acts import LawProposal


def choices(org, organism_id, at):
    """List recorded publications above each governing local Policy after Board admission."""
    rows = discussion._rows("""
        SELECT law.binding, law.library_reference, split_part(law.content,chr(10),1) AS content, law.class_name, law.display_class,
               law.version_number AS previous_number, v.version_number
          FROM public.lib_laws_at(%s,%s,%s) law
          JOIN public.core_libraryresource r ON r.id=law.resource AND r.state<>'archived'
          JOIN public.core_libraryresourceversion v ON v.resource_id=law.resource
               AND v.version_number>law.version_number
         WHERE law.issuer=%s AND law.class_name='POLICY' AND law.scope_kind='LIVING_ORGANISM'
           AND EXISTS (
               SELECT FROM public.core_libpublicationfact f
               JOIN public.core_libraryresourcetransition tr ON tr.id=f.transition_id
                    AND tr.resource_id=v.resource_id AND tr.version_id=v.id AND tr.to_state='published'
                WHERE f.version_id=v.id AND f.organism_id=%s AND f.kind='PUBLICATION'
                  AND tr.occurred_at<=%s AND f.recorded_at<=%s)
         ORDER BY law.library_reference,v.version_number DESC
        """, [org, at, at, org, organism_id, at, at])
    display = library_laws.law_display_rows(org, rows)
    for item, row in zip(display, rows, strict=True):
        item["previous_number"] = row["previous_number"]
    return display


def wording(org, reference, number):
    """Read the exact published local text after the signed target passes the chooser check."""
    rows = discussion._rows("""
        SELECT v.content FROM public.core_libraryresourceversion v
        JOIN public.core_libraryresource r ON r.id=v.resource_id
        JOIN public.core_libpublicationfact f ON f.version_id=v.id AND f.kind='PUBLICATION'
        JOIN public.core_livingorganism lo ON lo.id=f.organism_id
        JOIN public.core_libraryresourcetransition tr ON tr.id=f.transition_id
             AND tr.version_id=v.id AND tr.resource_id=r.id AND tr.to_state='published'
        WHERE r.resource_id=%s AND v.version_number=%s AND lo.organism_id=%s
        """, [reference, number, org])
    if not rows:
        raise discussion.DiscussionRefused(code="adoption_changed")
    return rows[0]["content"]


def current(org, reference):
    """Find the admitted organisation's current local binding for a fresh review after refusal."""
    rows = discussion._rows("""
        SELECT binding,version_number FROM public.lib_laws_at(%s,public.intevia_clock_statement(),
                   public.intevia_clock_statement()) WHERE issuer=%s AND library_reference=%s
                   AND class_name='POLICY' AND scope_kind='LIVING_ORGANISM'
        """, [org, org, reference])
    return rows[0] if rows else None


def attachment(org, review, text):
    """Fix the operation and scope on the server and retain the signed predecessor and proposal."""
    proposal = LawProposal(organism=org, resource=review["resource"], version=review["number"],
                           proposal=UUID(review["proposal"]), predecessor=UUID(review["predecessor"]),
                           law_class="POLICY", operation="ADOPT", intent="ADOPT", scope="LIVING_ORGANISM")
    return {"kind": "LAW", "spec": proposal.content(), "text": text}
