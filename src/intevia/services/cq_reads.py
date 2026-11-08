"""Public Decision projections. Resolve the viewer and thread before a ballot or reporting link."""

from django.db import connections, transaction

from src.intevia.services import discussion as disc
from src.intevia.services.law_records import _records

RESULT_LABELS = {
    "QUALIFIED": "Qualified.",
    "NOT_QUALIFIED_VOTE": "Not Qualified.",
    "NO_DECISION_SLATE_CHANGED": "A candidate withdrew. A fresh election is required.",
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


def result_scope_query(actor, organism_uuid, circle_uuid, thread_uuid):
    """The existing thread and GOVERNANCE-result audience at the current cut."""
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
    return query, args


def _scope(actor, organism_uuid, circle_uuid, thread_uuid):
    disc._read_owner(organism_uuid, circle_uuid)
    disc._read_uuid(thread_uuid)
    query, args = result_scope_query(actor, organism_uuid, circle_uuid, thread_uuid)
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
 a.facts->>'event_kind' AS event_kind, a.facts->'invalid_provisions' AS invalid_provisions, a.facts->'ballot' AS ballot,a.facts->'attempt' AS attempt,a.facts->'count' AS count,
 a.facts->'reporting_rule' AS reporting_rule,a.facts->'group_gaze' AS participants,
 a.facts->'opened_at' AS opened_at,a.facts->'deadline' AS deadline,a.facts->'closed_at' AS closed_at,
 a.facts->'settled_at' AS settled_at,a.facts->'restart' AS restarted,
 a.facts->'referral'->>'destination' AS referral_destination,
 CASE WHEN a.facts ? 'referral' THEN jsonb_build_object('source_result',a.act_uuid,
        'wording_version',a.facts->'referral'->'wording_version','wording_fingerprint',a.facts->'referral'->'wording_fingerprint',
        'attachment_fingerprint',a.facts->'referral'->'attachment_fingerprint') END AS referral,
 a.facts->>'disposition' AS disposition, a.facts->>'reason' AS effect_reason,
 a.facts->'retry_requested' AS retry_requested,a.facts->'version' AS wording_version
 FROM public.core_cqact a WHERE a.operation IN ('SETTLE','CLOSE','NO_BALLOT') AND a.board_uuid=%s AND a.thread_uuid=%s"""


def _present(row):
    if row.get("referral_destination") is None:
        row.pop("referral_destination", None)
        row.pop("referral", None)
    if row.get("disposition") is None:
        row.pop("disposition", None)
    if row.get("effect_reason") is None:
        row.pop("effect_reason", None)
    count = row["count"]
    if count["kind"] != "NO_DECISION_PROVISION_ENDED":
        row.pop("invalid_provisions")
    row["post_type"] = "GROUP_GAZE"
    row["event_kind"] = row.get("event_kind") or "DECISION"
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
        row["reporting_link_label"] = "Election ballot privacy" if row["event_kind"] == "ELECTION" else "View reporting decision"
        return row
    row["markdown"] = (
        f"{RESULT_LABELS[count['kind']]} {count['participated']} of "
        f"{count['eligible']} eligible took part. {count['required']} required."
    )
    if row["event_kind"] == "ELECTION":
        row.pop("participants", None)
        row["reporting_link_label"] = "Election ballot privacy"
        row["post_type"] = "GROUP_GAZE"
        if count.get("winner"):
            row["markdown"] += f" Elected member: {count['winner']}."
        return row
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


def _permitted_versions(rows, actor, organism_uuid):
    _project_authorities(rows, actor, organism_uuid)
    for row in rows:
        source = row["authority_source"]
        if not _settings_enterer_visible(source):
            row.pop("actor_identity_uuid", None)
            row.pop("enterer_identity_uuid", None)
        if source["kind"] == "CQ_RESULT" and "result_post" not in source:
            row.pop("decision", None)
            row.pop("evidence_reference", None)
    return rows


_VERSION_EVIDENCE = """a.authority_source,a.fingerprint,a.received_at,e.occurred_at,
    a.effective_at AS act_effective_at,a.effective_at AS effective_from,a.recorded_at,
    actor.identity_id AS actor_identity_uuid,enterer.identity_id AS enterer_identity_uuid,
    e.authority_decision_reference,e.evidence_reference"""

_VERSION_JOINS = """FROM public.core_cqact a JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
    JOIN public.core_identity actor ON actor.id=a.actor_id
    JOIN public.core_identity enterer ON enterer.id=e.enterer_identity_id"""


def _circle_versions(owner, circle, actor, organism_uuid, *, version=None):
    """Call only after the Circle register or a particular result admitted this viewer."""
    query = """SELECT 'CIRCLE' AS origin,a.act_uuid,a.act_uuid AS version,c.circle_uuid,
        plan->>'predecessor' AS predecessor,plan->>'previous_choice' AS previous_choice,
        plan->'previous_rule' AS previous_rule,plan->'previous_rule'->>'value' AS previous_value,
        plan->>'choice' AS choice,plan->>'value' AS value,(a.request->>'result')::uuid AS decision,"""
    query += _VERSION_EVIDENCE + " " + _VERSION_JOINS + """
        JOIN public.core_discboard b ON b.board_uuid=a.board_uuid
        JOIN public.core_circle c ON c.id=b.circle_id AND c.parent_organism_id=a.organism_id
        CROSS JOIN LATERAL (SELECT a.facts->'domain_plan' AS plan) domain
        WHERE a.organism_id=%s AND c.id=%s AND a.operation='IMPLEMENT' AND plan->>'domain'='CIRCLE_REPORTING'"""
    args = [owner, circle]
    if version is not None:
        query += " AND a.act_uuid=%s"
        args.append(version)
    return _permitted_versions(disc._rows(query + " ORDER BY a.ordinal", args), actor, organism_uuid)


def _provision_decision(owner, rule, actor, organism_uuid):
    rows = disc._rows(
        """SELECT a.act_uuid AS version,public.cq4_decision(a.act_uuid,a.authority_source) AS decision,"""
        + _VERSION_EVIDENCE + " " + _VERSION_JOINS + """
        WHERE a.organism_id=%s AND a.operation='SETTINGS' AND a.act_uuid=%s""",
        [owner, rule["version"]],
    )
    if not rows:
        raise disc.DiscussionUnavailable()
    return rule | _permitted_versions(rows, actor, organism_uuid)[0]


@transaction.atomic
def reporting_decision(
    *, credential, organism_uuid, thread_uuid, result_uuid, circle_uuid=None
):
    actor = disc._actor(credential)
    board = _scope(actor, organism_uuid, circle_uuid, thread_uuid)
    disc._read_uuid(result_uuid)
    # Start from the result in the authorised scope. A caller cannot substitute an LO settings ID.
    rows = disc._rows(
        """SELECT a.facts->'reporting_rule' AS rule,b.circle_id FROM public.core_cqact a
        JOIN public.core_discboard b USING(board_uuid)
        WHERE operation IN ('SETTLE','CLOSE','NO_BALLOT') AND board_uuid=%s AND thread_uuid=%s AND act_uuid=%s""",
        [board["board_uuid"], thread_uuid, result_uuid],
    )
    if not rows:
        raise disc.DiscussionUnavailable()
    rule, circle = rows[0]["rule"], rows[0]["circle_id"]
    if rule.get("origin") == "ELECTION":
        return rule
    version = rule["version"]
    owner = board["organism_id"]
    if rule.get("origin") == "CONSTITUTION":
        return _provision_decision(owner, rule, actor, organism_uuid)
    if rule.get("origin") == "CIRCLE":
        versions = _circle_versions(owner, circle, actor, organism_uuid, version=version)
        if not versions:
            raise disc.DiscussionUnavailable()
        return versions[0]
    inheritance = {}
    if "inheritance_version" in rule:
        versions = _circle_versions(owner, circle, actor, organism_uuid, version=rule["inheritance_version"])
        if not versions:
            raise disc.DiscussionUnavailable()
        inheritance = {"inheritance_decision": versions[0]}
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
        return _permitted_versions(rows, actor, organism_uuid)[0] | inheritance
    rows = disc._rows(
        """SELECT 'PLATFORM' AS origin,reporting_version AS version,reporting AS value,ruling_basis
                         FROM public.core_cqreportingdefault WHERE reporting_version=%s""",
        [version],
    )
    if not rows:
        raise disc.DiscussionUnavailable()
    return rows[0] | inheritance


@transaction.atomic
def vote_status(
    *, credential, organism_uuid, thread_uuid, ballot_uuid=None, circle_uuid=None
):
    """The latest or exact ballot. Before settlement the only live figures concern participation."""
    actor = disc._actor(credential)
    board = _scope(actor, organism_uuid, circle_uuid, thread_uuid)
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
    if status["event_kind"] == "ELECTION":
        receipts = disc._rows(
            """SELECT act_uuid AS receipt FROM public.core_cqact
               WHERE operation='ANSWER' AND request->>'ballot'=%s AND actor_id=%s""",
            [str(status["ballot"]), actor.pk],
        )
        status["own_receipt"] = receipts[0]["receipt"] if receipts else None
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


def _project_authorities(records, actor, organism_uuid, database_alias="default"):
    """Project only the exact, currently visible result links, in one read for a register."""
    linked = []
    for row in records:
        source = row.get("authority_source") or {}
        collective = source.get("kind") == "CQ_RESULT"
        reference = (
            source.get("reference") if collective else source.get("referral_result")
        )
        if reference:
            linked.append((row, source, collective, reference))
    if not linked:
        return
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT r.act_uuid::text AS reference,jsonb_build_object('board',r.board_uuid,'circle',c.circle_uuid,
                'thread',r.thread_uuid,'result_post',r.act_uuid,'ballot',o.act_uuid,
                'wording_version',r.facts->'version','attachment_fingerprint',r.facts->'attachment_fingerprint',
                'counting_rule',(o.facts->'rules')-'provisions'-'applied_provisions') AS trace
            FROM public.core_cqact r JOIN public.core_livingorganism lo ON lo.id=r.organism_id
            JOIN public.core_discboard b ON b.board_uuid=r.board_uuid LEFT JOIN public.core_circle c ON c.id=b.circle_id
            LEFT JOIN public.core_cqact o ON o.act_uuid=(r.facts->>'ballot')::uuid
            WHERE r.act_uuid=ANY(%s::uuid[]) AND lo.organism_id=%s
              AND public.cq3_result_visible(r.act_uuid,lo.id,%s,statement_timestamp())
            """,
            [sorted({item[3] for item in linked}), organism_uuid, actor.pk],
        )
        visible = {row["reference"]: row["trace"] for row in _records(cursor)}
    for row, source, collective, reference in linked:
        if collective:
            projected = {"kind": source["kind"], "route": source["route"]}
            if reference in visible:
                projected.update(reference=reference, **visible[reference])
        else:
            projected = dict(source)
            if reference in visible:
                projected["referral"] = visible[reference]
            else:
                projected.pop("referral_result")
        row["authority_source"] = projected


def _settings_enterer_visible(source):
    """A settings trace must not identify the last voter in a totals-only ballot."""
    if source["kind"] != "CQ_RESULT":
        return True
    return source.get("counting_rule", {}).get("reporting") == "NAMED"


def _authority_trace(source, actor, organism_uuid):
    row = {"authority_source": source}
    _project_authorities([row], actor, organism_uuid)
    return row["authority_source"]


@transaction.atomic
def implementation_record(
    *, credential, organism_uuid, thread_uuid, result_uuid, circle_uuid=None
):
    """Follow a result to the single act it implemented. A result can have no effect."""
    actor = disc._actor(credential)
    board = _scope(actor, organism_uuid, circle_uuid, thread_uuid)
    result = _result(board["board_uuid"], thread_uuid, result_uuid)
    rows = disc._rows(
        """
        SELECT i.act_uuid AS implementation,i.authority_source,
               i.facts->'domain_plan'->>'domain' AS domain,
               g.act_uuid AS gi_act,g.version_uuid AS gi_version,
               CASE WHEN i.facts->'domain_plan'->>'domain'='ELECTION' THEN jsonb_build_object(
                   'result',i.request->'result','ballot',i.facts->'domain_plan'->'ballot',
                   'winner',i.facts->'domain_plan'->'winner'->'person','nomination',i.facts->'domain_plan'->'winner'->'nomination',
                   'consent',i.facts->'domain_plan'->'winner'->'consent','place',i.facts->'domain_plan'->'place',
                   'body',i.facts->'domain_plan'->'body','binding',i.facts->'domain_plan'->'binding',
                   'version',i.facts->'domain_plan'->'law_version','provision',i.facts->'domain_plan'->'provision',
                   'start',i.facts->'domain_plan'->'start','end',i.facts->'domain_plan'->'end') END AS election,
               l.binding_uuid AS adopted_binding,ending.binding_id AS ended_binding,
               p.permission_uuid AS release_permission,p.authority_source AS permission_source,
               f.fact_uuid AS publication,s.act_uuid AS settings_version,
               CASE WHEN i.facts->'domain_plan'->>'domain'='CIRCLE_REPORTING' THEN i.act_uuid END AS circle_reporting_version
          FROM public.core_cqact i JOIN public.core_livingorganismevent e ON e.event_uuid=i.act_uuid
          LEFT JOIN public.core_cqact s ON s.organism_id=i.organism_id AND s.operation='SETTINGS'
             AND s.request->>'result'=i.request->>'result'
          LEFT JOIN public.core_giact g ON g.act_uuid=(i.facts->'domain_plan'->'payload'->>'request')::uuid
          LEFT JOIN public.core_organismlawbinding l ON l.adoption_event_id=e.id
          LEFT JOIN public.core_organismlawending ending ON ending.end_event_id=e.id
          LEFT JOIN public.core_libreleasepermission p ON p.version_id=(i.facts->'domain_plan'->>'version')::bigint
             AND i.facts->'domain_plan'->>'domain' IN ('PUBLISH_POLICY','RELEASE_POLICY')
          LEFT JOIN public.core_libpublicationfact f ON f.operation_uuid=%s AND f.kind='PUBLICATION'
         WHERE i.operation='IMPLEMENT' AND i.board_uuid=%s AND i.thread_uuid=%s AND i.request->>'result'=%s
         """,
        [result_uuid, board["board_uuid"], thread_uuid, str(result_uuid)],
    )
    if not rows:
        return {"result": result["post_uuid"], "effect": None}
    effect = {key: value for key, value in rows[0].items() if value is not None}
    effect["authority_source"] = _authority_trace(
        effect["authority_source"], actor, organism_uuid
    )
    if "permission_source" in effect:
        effect["permission_source"] = _authority_trace(
            effect["permission_source"], actor, organism_uuid
        )
        permission = effect["permission_source"]
        if permission["kind"] == "CQ_RESULT" and "result_post" not in permission:
            effect.pop("release_permission")
    return {"result": result["post_uuid"], "effect": effect}
