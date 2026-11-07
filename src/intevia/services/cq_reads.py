"""Public Decision projections. Resolve the viewer and thread before a ballot or reporting link."""

from django.db import transaction

from src.intevia.services import discussion as disc

RESULT_LABELS = {
    "QUALIFIED": "Qualified.",
    "NOT_QUALIFIED_VOTE": "Not Qualified.",
    "NOT_QUALIFIED_TIE": "Not Qualified. The vote was tied.",
    "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION": "Insufficient participation. No decision was reached.",
    "NO_DECISION_FEWER_THAN_THREE": "Fewer than three eligible members remained. No decision was reached.",
}


NO_BALLOT_LABELS = {
    "NO_DECISION_COORDINATOR_CLOSED": "The Coordinator closed the Suggestion during Clarity.",
    "NO_DECISION_FEWER_THAN_THREE": "Fewer than three eligible voters remained.",
    "NO_DECISION_PROVISION_ENDED": "A cited constitutional provision was no longer in force.",
    "NO_DECISION_CLARITY_THRESHOLD": "Too few eligible members confirmed the final wording.",
    "NO_DECISION_MOTION_UNAVAILABLE": "The motion was unavailable to its full electorate.",
}


def _scope(actor, organism_uuid, circle_uuid, thread_uuid):
    disc._read_owner(organism_uuid, circle_uuid)
    disc._read_uuid(thread_uuid)
    query = """SELECT b.board_uuid,b.organism_id,
        public.disc_audience(b.organism_id,b.circle_id,%s,cat.access_level,statement_timestamp()) AS thread_audience
        FROM public.core_discboard b
        JOIN public.core_livingorganism lo ON lo.id=b.organism_id
        LEFT JOIN public.core_circle circle ON circle.id=b.circle_id
        JOIN public.disc_categories cat ON cat.board_uuid=b.board_uuid
        JOIN public.core_discthread t USING(category_uuid)
        JOIN public.discp_posts first ON first.thread_uuid=t.thread_uuid AND first.post_number=1
        WHERE lo.organism_id=%s AND circle.circle_uuid IS NOT DISTINCT FROM %s::uuid AND t.thread_uuid=%s
        AND (public.disc_audience(b.organism_id,b.circle_id,%s,cat.access_level,statement_timestamp())
          OR (circle.kind='GOVERNANCE' AND cat.access_level<>'COORDINATORS'
            AND EXISTS(SELECT FROM public.core_cqact o WHERE o.thread_uuid=t.thread_uuid AND o.operation IN ('PROCEED','OPEN_VOTE','CLOSE','NO_BALLOT'))
            AND public.disc_audience(b.organism_id,NULL,%s,'MEMBERS',statement_timestamp())))
        AND ((NOT first.coordinator_hidden AND (NOT first.author_hidden OR EXISTS(
             SELECT FROM public.core_cqact o WHERE o.thread_uuid=t.thread_uuid AND o.operation IN ('PROCEED','OPEN_VOTE','CLOSE','NO_BALLOT'))))
             OR public.disc_office(b.organism_id,b.circle_id,%s,statement_timestamp()) IS NOT NULL)"""
    args = [
        actor.pk,
        organism_uuid,
        circle_uuid,
        thread_uuid,
        actor.pk,
        actor.pk,
        actor.pk,
    ]
    if not disc._rows(query, args):
        raise disc.DiscussionUnavailable()
    disc._lock_registered_organism("default", organism_uuid)
    rows = disc._rows(query, args)
    if not rows:
        raise disc.DiscussionUnavailable()
    return rows[0]


# Explicit projection: the private opening roster, individual input acts and
# commitment salts are never returned. Totals-only result facts contain no names.
_RESULT = """SELECT a.act_uuid AS post_uuid,a.thread_uuid,(a.facts->>'post_number')::integer AS post_number,
 a.facts->'invalid_provisions' AS invalid_provisions, a.facts->'ballot' AS ballot,a.facts->'attempt' AS attempt,a.facts->'count' AS count,
 a.facts->'reporting_rule' AS reporting_rule,a.facts->'group_gaze' AS participants,
 a.facts->'opened_at' AS opened_at,a.facts->'deadline' AS deadline,a.facts->'closed_at' AS closed_at,
 a.facts->'settled_at' AS settled_at,a.facts->'restart' AS restarted,
 a.facts->'retry_requested' AS retry_requested,a.facts->'version' AS wording_version
 FROM public.core_cqact a WHERE a.operation IN ('SETTLE','CLOSE','NO_BALLOT') AND a.board_uuid=%s AND a.thread_uuid=%s"""


def _present(row):
    count = row["count"]
    if count["kind"] != "NO_DECISION_PROVISION_ENDED":
        row.pop("invalid_provisions")
    row["post_type"] = "GROUP_GAZE"
    row["event_kind"] = "DECISION"
    row["hidden"] = False
    if row["ballot"] is None:
        row.pop("participants")
        row["markdown"] = (
            f"No vote opened. {NO_BALLOT_LABELS[count['kind']]} No decision was reached."
        )
        if count["kind"] == "NO_DECISION_CLARITY_THRESHOLD":
            row["markdown"] += (
                f" {count['clarity_confirmed']} of {count['clarity_eligible']} confirmed clarity."
                f" {count['clarity_required']} confirmations were required."
            )
        row["reporting_link_label"] = "View reporting decision"
        return row
    row["markdown"] = (
        f"{RESULT_LABELS[count['kind']]} {count['participated']} of "
        f"{count['eligible']} eligible took part. {count['required']} required."
    )
    if row["reporting_rule"]["value"] == "TOTALS_ONLY":
        row.pop("participants")
        row["reporting_link_label"] = "Names not shown · View reporting decision"
    else:
        row["reporting_link_label"] = "View reporting decision"
    return row


def _thread_results(board, thread, visible):
    if visible:
        return [
            _present(row)
            for row in disc._rows(_RESULT + " ORDER BY a.ordinal", [board, thread])
        ]
    return disc._rows(
        """SELECT act_uuid AS post_uuid,thread_uuid,(facts->>'post_number')::int AS post_number,
             true AS hidden,'GROUP_GAZE' AS post_type,NULL::text AS markdown FROM public.core_cqact
             WHERE board_uuid=%s AND thread_uuid=%s AND operation IN ('SETTLE','CLOSE','NO_BALLOT') ORDER BY ordinal""",
        [board, thread],
    )


def _result(board, thread, post):
    disc._read_uuid(post)
    rows = disc._rows(_RESULT + " AND a.act_uuid=%s", [board, thread, post])
    if not rows:
        raise disc.DiscussionUnavailable()
    return _present(rows[0])


@transaction.atomic
def result_post(
    *, credential, organism_uuid, thread_uuid, result_uuid, circle_uuid=None
):
    board = _scope(disc._actor(credential), organism_uuid, circle_uuid, thread_uuid)
    return _result(board["board_uuid"], thread_uuid, result_uuid)


@transaction.atomic
def reporting_decision(
    *, credential, organism_uuid, thread_uuid, result_uuid, circle_uuid=None
):
    board = _scope(disc._actor(credential), organism_uuid, circle_uuid, thread_uuid)
    disc._read_uuid(result_uuid)
    # Start from the result in the authorised scope. A caller cannot substitute an LO settings ID.
    rows = disc._rows(
        """SELECT facts->'reporting_rule'->>'version' AS version FROM public.core_cqact
        WHERE operation IN ('SETTLE','CLOSE','NO_BALLOT') AND board_uuid=%s AND thread_uuid=%s AND act_uuid=%s""",
        [board["board_uuid"], thread_uuid, result_uuid],
    )
    if not rows:
        raise disc.DiscussionUnavailable()
    version = rows[0]["version"]
    rows = disc._rows(
        """SELECT 'LO' AS origin,lo.organism_id AS organism_uuid,r.version,r.predecessor,
        r.previous_value,r.value,r.previous_scope,r.scope,r.previous_fingerprint,r.value_fingerprint,
        actor.identity_id AS actor_identity_uuid,enterer.identity_id AS enterer_identity_uuid,r.authority_source,r.received_at,r.occurred_at,r.act_effective_at,
        r.effective_from,r.recorded_at,r.decision,r.authority_decision_reference,r.evidence_reference
        FROM public.cq_reporting_rules r JOIN public.core_livingorganism lo ON lo.id=r.organism_id
        JOIN public.core_identity actor ON actor.id=r.actor_id
        JOIN public.core_identity enterer ON enterer.id=r.enterer_identity_id
        WHERE r.organism_id=%s AND r.version=%s""",
        [board["organism_id"], version],
    )
    if rows:
        return rows[0]
    rows = disc._rows(
        """SELECT 'PLATFORM' AS origin,reporting_version AS version,reporting AS value,ruling_basis
                         FROM public.core_cqreportingdefault WHERE reporting_version=%s""",
        [version],
    )
    if not rows:
        raise disc.DiscussionUnavailable()
    return rows[0]


@transaction.atomic
def vote_status(
    *, credential, organism_uuid, thread_uuid, ballot_uuid=None, circle_uuid=None
):
    """The latest or exact ballot. Before settlement the only live figures concern participation."""
    board = _scope(disc._actor(credential), organism_uuid, circle_uuid, thread_uuid)
    if ballot_uuid is not None:
        disc._read_uuid(ballot_uuid)
    rows = disc._rows(
        """SELECT statement_timestamp() AS read_at,o.act_uuid AS ballot,o.facts->>'event_kind' AS event_kind,
         o.effective_at AS opened_at,(o.facts->>'deadline')::timestamptz AS deadline,
         o.facts->'reporting_rule' AS reporting_rule,o.facts->'attempt' AS attempt,
         (o.facts->>'version')::uuid AS wording_version,r.act_uuid AS result_uuid
         FROM public.core_cqact o LEFT JOIN public.core_cqact r ON r.operation='SETTLE'
            AND r.request->>'ballot'=o.act_uuid::text
         WHERE o.board_uuid=%s AND o.thread_uuid=%s AND o.operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE')
            AND (%s::uuid IS NULL OR o.act_uuid=%s) ORDER BY o.ordinal DESC LIMIT 1""",
        [board["board_uuid"], thread_uuid, ballot_uuid, ballot_uuid],
    )
    if not rows and ballot_uuid is None:
        outcomes = disc._rows(
            _RESULT + " AND a.operation IN ('CLOSE','NO_BALLOT')",
            [board["board_uuid"], thread_uuid],
        )
        if outcomes:
            post = _present(outcomes[0])
            return {
                "phase": "CLOSED"
                if post["count"]["kind"] == "NO_DECISION_COORDINATOR_CLOSED"
                else "RESULT",
                "ballot": None,
                "result_uuid": post["post_uuid"],
                "result": post,
            }
    if not rows:
        raise disc.DiscussionUnavailable()
    status = rows[0]
    read_at = status.pop("read_at")
    if status["result_uuid"] is not None:
        status["phase"] = "RESULT"
        status["result"] = _result(
            board["board_uuid"], thread_uuid, status["result_uuid"]
        )
        return status
    status.update(
        disc._rows(
            """SELECT CASE WHEN %s::timestamptz>=%s THEN 'AWAITING_SETTLEMENT'
        ELSE 'VOTING' END AS phase,count(*) AS eligible,count(e.choice) AS participated
        FROM public.cq_ballot_entries(%s,%s) e""",
            [
                read_at,
                status["deadline"],
                status["ballot"],
                min(read_at, status["deadline"]),
            ],
        )[0]
    )
    if status["reporting_rule"]["value"] == "NAMED":
        status["participation"] = disc._rows(
            """SELECT identity_uuid AS identity,display_name AS name,
            choice IS NOT NULL AS answered FROM public.cq_ballot_entries(%s,%s)
            ORDER BY person""",
            [status["ballot"], min(read_at, status["deadline"])],
        )
    return status
