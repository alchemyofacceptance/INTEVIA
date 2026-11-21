"""Board-office family and effective tenure for an already admitted reader."""

from src.intevia.services import discussion as disc


def _tenure(family_ids, owner_id, at):
    # A rejoin retains the membership anchor. Bind its departure to this
    # appointment's period, using the original proposal and the actual award.
    rows = disc._rows("""
        WITH appointments AS (
            SELECT a.assignment_uuid AS occupancy,a.id AS assignment_id,m.id AS member_id,m.identity_id,
                   award.effective_at AS starts,
                   least(public.elect_office_deadline(award.id,%(at)s),
                         (SELECT min(x.effective_at) FROM public.core_contextualroleassignmentstateevent x
                           WHERE x.assignment_id=a.id AND x.resulting_state='ENDED'
                             AND greatest(x.received_at,x.recorded_at)<=%(at)s),
                         (SELECT min(x.effective_at) FROM public.core_organismmembershiptransition x
                           WHERE x.membership_id=m.id AND x.resulting_state='ENDED'
                             AND (x.recorded_at>origin.recorded_at OR x.effective_at>=award.effective_at)
                             AND greatest(x.received_at,x.recorded_at)<=%(at)s)) AS ends
              FROM public.core_contextualroleassignment a
              JOIN public.core_organismmembership m ON m.id=a.membership_id AND m.living_organism_id=%(owner_id)s
              JOIN public.core_contextualroleassignmentstateevent origin ON origin.assignment_id=a.id AND origin.sequence=1
              JOIN public.core_contextualroleassignmentstateevent award ON award.assignment_id=a.id AND award.action='ASSIGN'
             WHERE a.role_definition_id=ANY(%(versions)s::bigint[]) AND a.circle_id IS NULL
               AND award.effective_at<=%(at)s AND greatest(award.received_at,award.recorded_at)<=%(at)s
        ), terms AS (
            SELECT p.*,public.elect_term_includes(%(at)s,p.starts,p.ends)
                       AND public.elect_office_effective(p.assignment_id,%(at)s,%(at)s) AS ongoing
              FROM appointments p WHERE p.ends IS NULL OR p.starts<p.ends
        ), held AS (
            SELECT t.* FROM terms t WHERE t.ongoing OR (
                t.ends<=%(at)s AND EXISTS (
                    SELECT FROM (
                        SELECT t.starts AS moment
                        UNION ALL
                        SELECT e.effective_at FROM public.core_contextualroleassignmentstateevent e
                         WHERE e.assignment_id=t.assignment_id AND e.resulting_state='ACTIVE'
                           AND greatest(e.received_at,e.recorded_at)<=%(at)s
                        UNION ALL
                        SELECT m.effective_at FROM public.core_organismmembershiptransition m
                         WHERE m.membership_id=t.member_id AND m.resulting_state='ACTIVE'
                           AND greatest(m.received_at,m.recorded_at)<=%(at)s
                    ) probe
                    WHERE probe.moment>=t.starts AND probe.moment<t.ends
                      AND public.elect_office_effective(t.assignment_id,probe.moment,%(at)s)
                )
            )
        )
        SELECT visible_vacancy,ongoing,starts,ends,holder_name
          FROM (
            SELECT false AS visible_vacancy,h.ongoing,h.starts,h.ends,r.display_name AS holder_name,h.occupancy
              FROM held h
              LEFT JOIN public.core_identityresolution r ON r.identity_id=h.identity_id
               AND NOT EXISTS (SELECT FROM public.core_identityresolutionsevered z WHERE z.identity_id=h.identity_id)
            UNION ALL
            SELECT true,false,NULL,NULL,NULL,NULL
             WHERE NOT EXISTS (SELECT FROM terms WHERE ongoing)
          ) visible
         ORDER BY visible_vacancy DESC,ongoing DESC,starts DESC,occupancy
    """, {"versions": family_ids, "owner_id": owner_id, "at": at})
    tenure = []
    for row in rows:
        if row["visible_vacancy"]:
            tenure.append({"state": "vacant"})
            continue
        term = {"state": "ongoing" if row["ongoing"] else "former",
                "starts_at": row["starts"], "ends_at": row["ends"]}
        if row["holder_name"]:
            term["holder_name"] = row["holder_name"]
        tenure.append(term)
    return tenure


def office_family_head(*, owner_id, code, requested, at):
    """Find the one current definition whose predecessor chain contains the requested pin."""
    current = disc._rows("""
        WITH RECURSIVE family AS (
            SELECT id,role_uuid,definition_version,supersedes_id,active FROM public.core_organismroledefinition
             WHERE living_organism_id=%(owner)s AND scope='LIVING_ORGANISM' AND kind='BOARD_OFFICE'
               AND code=%(code)s AND effective_at<=%(at)s AND received_at<=%(at)s AND recorded_at<=%(at)s
        ), lineage AS (
            SELECT id AS current_id,id,role_uuid,supersedes_id FROM family WHERE active
            UNION
            SELECT l.current_id,p.id,p.role_uuid,p.supersedes_id
              FROM lineage l JOIN family p ON p.id=l.supersedes_id
        )
        SELECT c.id,c.role_uuid,c.definition_version,array_agg(l.id) AS family_ids
          FROM lineage l JOIN family c ON c.id=l.current_id
         GROUP BY c.id,c.role_uuid,c.definition_version HAVING bool_or(l.role_uuid=%(requested)s)
    """, {"owner": owner_id, "code": code, "at": at, "requested": requested})
    return current[0] if len(current) == 1 else None


def office_family(*, owner_id, code, requested, at):
    """Return the current definition, version and public tenure at the caller's cut."""
    role = office_family_head(owner_id=owner_id, code=code, requested=requested, at=at)
    if role is None:
        return None
    return role["role_uuid"], role["definition_version"], _tenure(role["family_ids"], owner_id, at)
