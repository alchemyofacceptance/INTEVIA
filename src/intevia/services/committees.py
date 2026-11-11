"""Committee instruments, authenticated mandate reads and seat-linked work."""

import json

from django.db import connections, transaction


def attachment(*, organism_uuid, act, mandate=None, **fields):
    spec = dict(fields, organism=str(organism_uuid), act=act)
    if mandate is not None:
        spec["mandate"] = str(mandate)
    return {
        "kind": "COMMITTEE",
        "spec": spec,
        "text": json.dumps(spec, sort_keys=True, ensure_ascii=False),
    }


@transaction.atomic
def history(*, credential, organism_uuid, circle_uuid):
    from src.intevia.services import cq, discussion as disc

    actor = disc._actor(credential)
    lo = cq._read_owner(actor, organism_uuid)
    rows = disc._rows(
        """
        SELECT c.id FROM public.core_circle c JOIN public.core_livingorganism o ON o.id=c.parent_organism_id
        WHERE c.circle_uuid=%s AND o.id=%s""",
        [circle_uuid, lo],
    )
    if not rows:
        raise disc.DiscussionUnavailable()
    return disc._rows(
        """
        SELECT m.binding,m.act_uuid AS implementation,a.request->>'result' AS decision,m.effective_at,m.recorded_at,
          m.mandate->>'kind' AS kind,m.mandate->'terms_effect'->'binding_uuid' AS terms_binding,
          m.mandate->'chair_basis' AS composition_basis,m.mandate->'officer_finding' AS officer_finding,
          public.cmt_live(m.binding,statement_timestamp(),statement_timestamp()) AS in_force,
          coalesce((SELECT jsonb_agg(to_jsonb(g) ORDER BY g.effective_at,g.recorded_at,g.requirement)
            FROM public.core_cmtcomposition g WHERE g.mandate=m.binding),'[]') AS composition
        FROM public.cmt_mandates m JOIN public.core_cqact a USING(act_uuid)
        WHERE m.organism_id=%s AND m.circle_id=%s ORDER BY m.ordinal""",
        [lo, rows[0]["id"]],
    )


def work_refusal(activity, assignment, alias, person):
    """Give a former worker the reason recorded on the current custody receipt."""
    from src.intevia.services.cq import MESSAGES

    with connections[alias].cursor() as cursor:
        cursor.execute(
            """SELECT EXISTS(
                SELECT FROM public.cmt_worker(%s,statement_timestamp(),statement_timestamp()) worker
                JOIN public.core_cmtcustody held ON held.receipt=worker.receipt
                WHERE held.cause ? 'stand_down' AND held.cause->>'custodian'='LO_COORDINATORS'
                AND (%s OR EXISTS(SELECT FROM public.core_cmtcustody prior
                    WHERE prior.activity=held.activity AND prior.holder=%s)))""",
            [activity.pk, assignment.assignee_id == person, person],
        )
        after_stand_down, = cursor.fetchone()
    return MESSAGES["CMT_WORK_STAND_DOWN" if after_stand_down else "CMT_WORK"]


def work_holder(activity, assignment, alias, at=None, *, person=None):
    if (
        activity.initiating_domain != "service"
        or not activity.initiating_domain_reference.startswith(("committee:", "board:"))
    ):
        return assignment.assignee_id
    with connections[alias].cursor() as cursor:
        cursor.execute(
            """SELECT holder FROM public.cmt_worker(%s,coalesce(%s,statement_timestamp()),statement_timestamp())""",
            [activity.pk, at],
        )
        rows = cursor.fetchall()
    if rows:
        holders = {row[0] for row in rows}
        if person is not None:
            return person if person in holders else None
        return holders.pop() if len(holders) == 1 else None
    # Until the Board binds the work there is no authority to act for its seat.
    return None if activity.initiating_domain_reference.startswith("board:") else assignment.assignee_id

@transaction.atomic
def board_work(*, credential, organism_uuid, target):
    """List activities attached to one stable Board seat or office, including held work."""
    from src.intevia.services import cq, discussion as disc

    actor = disc._actor(credential)
    owner = cq._read_owner(actor, organism_uuid)
    boards = disc._rows(
        """SELECT c.id FROM public.core_circle c WHERE c.parent_organism_id=%s AND c.kind='GOVERNANCE'
            AND public.disc_audience(c.parent_organism_id,c.id,%s,'MEMBERS',statement_timestamp())""",
        [owner, actor.pk],
    )
    if not boards:
        raise disc.DiscussionUnavailable()
    return disc._rows(
        """SELECT a.activity_id,w.receipt,i.identity_id AS holder
           FROM public.core_cmtcustody anchor JOIN public.core_serviceactivity a ON a.id=anchor.activity
           CROSS JOIN LATERAL public.cmt_worker(a.id,statement_timestamp(),statement_timestamp()) w
           LEFT JOIN public.core_identity i ON i.id=w.holder
           WHERE anchor.board_circle=%s AND anchor.board_target=%s::jsonb AND anchor.predecessor IS NULL
           ORDER BY a.activity_id""",
        [boards[0]["id"], json.dumps(target)],
    )
