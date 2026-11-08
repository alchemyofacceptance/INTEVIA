"""DISC2 commands and audience-filtered, read-only phase and settings projections.

A caller managing its own outer transaction calls translate_refusal at its COMMIT.
All effective times are sampled by PostgreSQL. No client can supply a phase clock.
"""

import json
import re
from datetime import datetime
from uuid import UUID, uuid5

from django.db import IntegrityError, transaction

from src.intevia.services import cq_reads
from src.intevia.services import discussion as disc

CLARITY_STAGE = {
    "label": "Clarity stage before the vote",
    "help": "On: members can confirm the final wording before voting. Ordinarily only those who confirm may vote. "
    "A declared constitutional right to vote keeps this stage open to everyone eligible and removes the confirmation requirement to vote. "
    "Off: the vote opens by rule after preparation, and every eligible member may vote.",
}

MESSAGES = {
    "CQ_RESPONSE_DUE": "The Coordinator response period has ended. Run CQ catch-up to record the rule outcome.",
    "CQ_NOT_READY": "There is no separate vote-opening step. Proceed from Clarity, or check the current CQ phase.",
    "CQ_NOT_OPEN": "This vote has not opened.",
    "CQ_VOTE_CLOSED": "This vote is closed.",
    "CQ_ELECTORATE": "You are not in this vote's eligible electorate.",
    "CQ_SAT_OUT": "You did not confirm clarity on this wording, so you sit out this vote.",
    "CQ_CONFLICTED": "You declared a conflict for this proposal and cannot vote on it.",
    "CQ_PROBATION_VOTE": "Probationary members may discuss but cannot vote.",
    "CQ_ABSTAIN_OFF": "Abstention is not available for this vote.",
    "CQ_DUPLICATE": "You have already answered this vote.",
    "CQ_CHOICE": "Choose Qualified, Not Qualified or Abstain.",
    "CQ_NO_DUE": "No CQ phase transition is due.",
    "CQ_SETTINGS_OFFICE": "Only this Living Organism's effective Coordinator may change its CQ settings.",
    "CQ_REPORT_DATE": "Choose a reporting effective time at or after this decision and its preceding reporting rule.",
    "CQ_SETTINGS_STALE": "Choose the current CQ settings version before changing it.",
    "CQ_SETTINGS_INPUT": "Supply positive whole-day durations, supported quorum and voting settings, and valid exact fractions.",
    "CQ_SETTINGS_UNCHANGED": "These CQ settings are already in force.",
    "CQ_SUBSCRIBER": "Subscriber access is required to select this CQ option. An option already chosen stays in force.",
    "CQ_LAW": "Identify the Article or Bylaw in force, its exact version, provision, body, decision, stage and denominator.",
    "CQ_RESTRICTED": "Suspended and restricted members may read permitted CQ records but cannot change them.",
    "CQ_PROBATION": "Probationary members may discuss but cannot perform CQ governance acts.",
    "CQ_INITIATOR": "Only this Suggestion's author, or a Coordinator where this Living Organism allows it, may put it forward for CQ.",
    "CQ_DISABLED": "CQ is switched off for this Living Organism.",
    "CQ_GI_VERSION": "The GI predecessor is no longer current or the GI has retired.",
    "CQ_GI_LAW": "A GI needs its exact applicable local law in force. Review its citations.",
    "CQ_REFERRAL_BODY": "This referral cannot go to this body. Use the recorded destination.",
    "CQ_REFERRAL_MATTER": "Carry the exact referred motion and attachment into the new Suggestion.",
    "CQ_REFERRAL_USED": "This referral already has a receiving CQ journey.",
    "CQ_BODY": "This board cannot decide this act. Put it forward in the body that owns it.",
    "CQ_ATTACHMENT": "This board cannot carry out this exact proposal. Check the body, version and current law.",
    "CQ_SUGGESTION": "Only a Suggestion can be put forward for CQ.",
    "CQ_EXISTS": "This Suggestion already has a CQ journey.",
    "CQ_FULL_AUDIENCE": "This category does not give the full board electorate access to the Suggestion.",
    "CQ_INIT_VERSION": "Choose the current opening-post version before putting this Suggestion forward.",
    "CQ_VERSION": "Choose the current wording before recording your Clarity response.",
    "CQ_PREPARE_OFFICE": "Only this board's effective Coordinator may prepare this Suggestion.",
    "CQ_CLARITY_OFFICE": "Only this board's effective Coordinator may extend or close clarity.",
    "CQ_FINISH_OFFICE": "Only this board's effective Coordinator may finish clarity.",
    "CQ_OPEN_OFFICE": "Only this board's effective Coordinator may open this vote.",
    "CQ_PREPARATION": "This CQ is no longer in preparation.",
    "CQ_CONFIRM_PERIOD": "This CQ is not accepting clarity confirmations.",
    "CQ_CONFIRM_ELIGIBLE": "Only an eligible member of this board may confirm clarity for this CQ.",
    "CQ_RESPONSE_REPEAT": "You have already confirmed clarity for this CQ.",
    "CQ_QUESTION_REPEAT": "You have already recorded a question about this wording.",
    "CQ_CLARITY_PERIOD": "This CQ is no longer in clarity.",
    "CQ_THREE": "Fewer than three eligible members remain. A CQ vote cannot open.",
    "CQ_WINDOW": "The clarity period has not ended and not everyone is clear.",
    "CQ_CDPT": "The required clarity confirmations have not been reached.",
    "CQ_WITHDRAW_AUTHOR": "Only this Suggestion's eligible author may withdraw it.",
    "CQ_WITHDRAW_PHASE": "A Suggestion can be withdrawn only during preparation or clarity.",
    "CQ_CONFLICT_PERIOD": "The conflict-declaration period has ended; this CQ cannot accept another declaration.",
    "CQ_CONFLICT_REPEAT": "You have already declared a conflict for this proposal.",
    "CQ_CONFLICT_BASIS": "Supply the basis of your declared conflict.",
    "CQ_REQUEST": "This request identity already records a different CQ act.",
    "CQ_INPUT": "Supply exact CQ references and the fields required for this act.",
    "CQ_COMPLETE": "The CQ act could not complete with its authority and complete lineage. No changes were committed.",
}


class CQRefused(disc.DiscussionRefused):
    def __init__(self, code):
        super().__init__(MESSAGES[code], code=code)


def translate_refusal(error):
    code = getattr(getattr(error.__cause__, "diag", None), "message_primary", None)
    if code == "DISC_UNAVAILABLE":
        raise disc.DiscussionUnavailable() from error
    if code in MESSAGES:
        raise CQRefused(code) from error
    disc.translate_refusal(error)


def _id(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, UUID):
        raise CQRefused("CQ_INPUT")
    return str(value)


def _lock(request_uuid, owner):
    # Same request-lock namespace/order as DISC1. Execution is a separate
    # statement after both locks, so waiting cannot backdate a governed act.
    stamp = disc._rows("SELECT statement_timestamp() AS stamp")[0]["stamp"]
    disc._rows(
        "SELECT pg_advisory_xact_lock(hashtextextended('DISC1:request:'||%s::text,0))",
        [request_uuid],
    )
    if not disc._rows(
        """SELECT public.s015_lock_organisms(ARRAY[lo.organism_id])
        FROM public.core_livingorganism lo WHERE lo.organism_id=%s""",
        [owner],
    ):
        raise disc.DiscussionUnavailable()
    return stamp


def _json_reference(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime) and value.utcoffset() is not None:
        return value.isoformat()
    raise TypeError("A CQ setting must be representable in JSON.")


def _execute(actor, owner, board, thread, operation, key, args, received):
    try:
        encoded = json.dumps(
            args, ensure_ascii=False, allow_nan=False, default=_json_reference
        )
        encoded.encode("utf-8")
        if re.search(r"(?<!\\)(?:\\\\)*\\u0000", encoded):
            raise ValueError("PostgreSQL text cannot contain NUL")
    except (TypeError, ValueError, RecursionError) as exc:
        raise CQRefused(
            "CQ_SETTINGS_INPUT" if operation == "SETTINGS" else "CQ_INPUT"
        ) from exc
    return disc._rows(
        """SELECT public.cq_execute(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) AS act""",
        [
            owner,
            board,
            thread,
            operation,
            key,
            encoded,
            actor.pk,
            actor.access_epoch,
            received,
        ],
    )[0]["act"]


def _command(
    credential,
    organism_uuid,
    request_uuid,
    operation,
    *,
    thread_uuid=None,
    circle_uuid=None,
    **fields,
):
    try:
        with transaction.atomic():
            actor = disc._actor(credential)
            owner = _id(organism_uuid)
            key = _id(request_uuid)
            _id(circle_uuid, nullable=True)
            board = (
                None
                if operation == "SETTINGS"
                else disc._board_id(organism_uuid, circle_uuid)
            )
            thread = _id(thread_uuid, nullable=operation == "SETTINGS")
            received = _lock(key, owner)
            if "referral_result" in fields:
                visibility, args = cq_reads.result_scope_query(
                    actor, organism_uuid, circle_uuid, thread_uuid
                )
                if not any(
                    row["thread_audience"] for row in disc._rows(visibility, args)
                ):
                    raise disc.DiscussionUnavailable()
                source_key = _id(fields["referral_result"])
                visible = disc._rows(
                    "SELECT public.cq3_result_visible(%s,id,%s,statement_timestamp()) AS visible "
                    "FROM public.core_livingorganism WHERE organism_id=%s",
                    [source_key, actor.pk, owner],
                )
                if not visible or not visible[0]["visible"]:
                    raise disc.DiscussionUnavailable()
            # Resolve visibility and role even if the caller supplied malformed
            # operation details. The writer rechecks the source at execution.
            disc._rows(
                """SELECT public.cq_source(lo.id,%s,%s,%s,%s,statement_timestamp())
                FROM public.core_livingorganism lo WHERE lo.organism_id=%s""",
                [board, thread, operation, actor.pk, owner],
            )
            if operation == "ANSWER":
                disc._rows(
                    "SELECT public.cq_answer_eligible(%s,%s,%s,%s,statement_timestamp())",
                    [board, thread, _id(fields["ballot"]), actor.pk],
                )
            for name in (
                "version",
                "predecessor",
                "previous_thread",
                "ballot",
                "referral_result",
            ):
                if name in fields:
                    fields[name] = _id(
                        fields[name], nullable=name not in ("version", "ballot")
                    )
            # Input type validation before json.dumps avoids accidental coercion.
            if operation == "SETTINGS" and not isinstance(fields.get("changes"), dict):
                raise CQRefused("CQ_SETTINGS_INPUT")
            return _execute(
                actor, owner, board, thread, operation, key, fields, received
            )
    except IntegrityError as exc:
        translate_refusal(exc)


def change_settings(
    *,
    credential,
    organism_uuid,
    request_uuid,
    predecessor,
    changes,
    reporting_effective_from=None,
):
    """A patch over the predecessor version. None identifies platform defaults."""
    return _command(
        credential,
        organism_uuid,
        request_uuid,
        "SETTINGS",
        predecessor=predecessor,
        changes=changes,
        **(
            {"reporting_effective_from": reporting_effective_from}
            if reporting_effective_from is not None
            else {}
        ),
    )


def put_forward(
    *,
    credential,
    organism_uuid,
    thread_uuid,
    version_uuid,
    request_uuid,
    circle_uuid=None,
    previous_thread=None,
    attachment=None,
    referral_result=None,
):
    return _command(
        credential,
        organism_uuid,
        request_uuid,
        "INITIATE",
        thread_uuid=thread_uuid,
        circle_uuid=circle_uuid,
        version=version_uuid,
        previous_thread=previous_thread,
        **({"attachment": attachment} if attachment is not None else {}),
        **({"referral_result": referral_result} if referral_result is not None else {}),
    )


def mark_prepared(**scope):
    return _command(operation="PREPARED", **scope)


def confirm_clarity(*, version_uuid, **scope):
    return _command(operation="CONFIRM", version=version_uuid, **scope)


def raise_clarity_question(*, version_uuid, **scope):
    """Mark an unresolved question on this wording. Discussion text stays in the forum."""
    return _command(operation="QUESTION", version=version_uuid, **scope)


def declare_conflict(*, basis, **scope):
    return _command(operation="CONFLICT", basis=basis, **scope)


def extend_clarity(**scope):
    return _command(operation="EXTEND", **scope)


def close_without_vote(**scope):
    return _command(operation="CLOSE", **scope)


def finish_clarity(**scope):
    """Proceed from Clarity and open the ballot in one office act."""
    return _command(operation="PROCEED", **scope)


def withdraw(**scope):
    return _command(operation="WITHDRAW", **scope)


def open_vote(**scope):
    """Legacy entry point. Authenticate and give the governed separate-opening refusal."""
    return _command(operation="OPEN_VOTE", **scope)


def answer(*, ballot_uuid, choice, **scope):
    """Bind the answer to the identified ballot, even after an automatic re-run."""
    return _command(operation="ANSWER", ballot=ballot_uuid, choice=choice, **scope)


def catch_up(*, credential):
    """RULE-sourced catch-up for this identity's LOs, with per-journey commits.

    The due list is only a hint. Re-read under the owner lock before deciding.
    Catch-up returns no thread IDs, content or attendance from private boards.
    """
    try:
        with transaction.atomic():
            actor = disc._actor(credential)
            due = disc._rows(
                """SELECT h.thread_uuid,h.board_uuid,h.act_uuid,h.phase,lo.organism_id AS owner
              FROM public.cq_phases h JOIN public.core_livingorganism lo ON lo.id=h.organism_id
              JOIN public.core_organismmembership m ON m.living_organism_id=lo.id AND m.identity_id=%s
              WHERE ((h.phase='PREPARATION' AND h.deadline<=statement_timestamp())
                 OR (h.phase='CLARITY' AND h.response_deadline<=statement_timestamp())
                 OR (h.phase='VOTING' AND public.cq_can_settle(h.act_uuid,statement_timestamp())))
              AND public.disc_member_state(lo.id,m.identity_id,statement_timestamp())='ACTIVE'
              ORDER BY lo.organism_id,h.thread_uuid""",
                [actor.pk],
            )
        changed = False
        for item in due:
            with transaction.atomic():
                actor = disc._actor(credential)
                head = item["act_uuid"]
                if item["phase"] == "VOTING":
                    operation, suffix, args = "SETTLE", "settle", {"ballot": str(head)}
                elif item["phase"] == "CLARITY":
                    operation, suffix, args = (
                        "RESPONSE_DUE",
                        "response-due",
                        {"window": str(head)},
                    )
                else:
                    operation, suffix, args = "PREPARATION_DUE", "preparation-due", {}
                key = uuid5(head, "DISC2:" + suffix)
                received = _lock(key, item["owner"])
                rows = disc._rows(
                    """SELECT 1 FROM public.cq_phases h WHERE h.thread_uuid=%s
                    AND h.act_uuid=%s AND ((h.phase='PREPARATION' AND h.deadline<=statement_timestamp())
                      OR (h.phase='CLARITY' AND h.response_deadline<=statement_timestamp())
                 OR (h.phase='VOTING' AND public.cq_can_settle(h.act_uuid,statement_timestamp())))
                    AND public.disc_member_state(h.organism_id,%s,statement_timestamp())='ACTIVE'""",
                    [item["thread_uuid"], item["act_uuid"], actor.pk],
                )
                if rows:
                    _execute(
                        actor,
                        item["owner"],
                        item["board_uuid"],
                        item["thread_uuid"],
                        operation,
                        key,
                        args,
                        received,
                    )
                    changed = True
        return "Due work recorded." if changed else "Nothing is due."
    except IntegrityError as exc:
        translate_refusal(exc)


def _read_owner(actor, organism_uuid):
    disc._read_uuid(organism_uuid)
    query = """SELECT lo.id FROM public.core_livingorganism lo WHERE lo.organism_id=%s AND
      public.disc_member_state(lo.id,%s,statement_timestamp()) IN ('ACTIVE','SUSPENDED','RESTRICTED')"""
    params = [organism_uuid, actor.pk]
    if not disc._rows(query, params):
        raise disc.DiscussionUnavailable()
    disc._lock_registered_organism("default", organism_uuid)
    rows = disc._rows(query, params)
    if not rows:
        raise disc.DiscussionUnavailable()
    return rows[0]["id"]


def settings(*, credential, organism_uuid):
    with transaction.atomic():
        actor = disc._actor(credential)
        owner = _read_owner(actor, organism_uuid)
        row = disc._rows(
            """SELECT public.cq_settings(%s) AS settings, p.reporting_version AS platform_version,
          public.cq_reporting_at(%s,statement_timestamp())->>'version' AS reporting_version,
          (SELECT a.act_uuid FROM public.core_cqact a WHERE a.organism_id=%s AND a.operation='SETTINGS'
           ORDER BY ordinal DESC LIMIT 1) AS version FROM public.cq_platform_defaults p""",
            [owner, owner, owner],
        )[0]
        row["clarity_stage_control"] = CLARITY_STAGE.copy()
        return row


def settings_history(*, credential, organism_uuid):
    with transaction.atomic():
        owner = _read_owner(disc._actor(credential), organism_uuid)
        return disc._rows(
            """SELECT act_uuid,actor_id,authority_source,received_at,effective_at,recorded_at,facts,fingerprint
          FROM public.core_cqact WHERE organism_id=%s AND operation='SETTINGS' ORDER BY ordinal""",
            [owner],
        )


def phase(*, credential, organism_uuid, thread_uuid, circle_uuid=None):
    """Read no text and expose no roster or conflict declarations to a casual thread reader."""
    with transaction.atomic():
        actor = disc._actor(credential)
        board = cq_reads._scope(actor, organism_uuid, circle_uuid, thread_uuid)
        rows = disc._rows(
            """SELECT h.phase,h.deadline,h.response_deadline,
          CASE WHEN h.phase IN ('PREPARATION','CLARITY') THEN first.version_uuid ELSE (a.facts->>'version')::uuid END AS version,
          a.facts->'prepared' AS prepared, CASE WHEN %s THEN a.facts->'required' END AS required,
          CASE WHEN %s THEN jsonb_array_length(a.facts->'confirmed') END AS confirmations,
          a.effective_at, a.recorded_at FROM public.cq_phases h JOIN public.core_cqact a USING(act_uuid)
          JOIN public.discp_posts first ON first.thread_uuid=h.thread_uuid AND first.post_number=1
          WHERE h.board_uuid=%s AND h.thread_uuid=%s""",
            [
                board["thread_audience"],
                board["thread_audience"],
                board["board_uuid"],
                thread_uuid,
            ],
        )
        if not rows:
            raise disc.DiscussionUnavailable()
        state = rows[0]
        if board["thread_audience"]:
            links = disc._rows(
                """SELECT i.request->'attachment' AS attachment,
                i.facts->>'attachment_fingerprint' AS fingerprint,
                CASE WHEN public.cq3_result_visible(src.act_uuid,i.organism_id,%s,statement_timestamp())
                  THEN jsonb_build_object('result_uuid',src.act_uuid,'thread_uuid',src.thread_uuid,
                         'circle_uuid',c.circle_uuid,'wording_version',src.facts->'referral'->'wording_version') END AS referred_from
                FROM public.core_cqact i LEFT JOIN public.core_cqact src ON src.act_uuid=(i.request->>'referral_result')::uuid
                LEFT JOIN public.core_discboard b ON b.board_uuid=src.board_uuid LEFT JOIN public.core_circle c ON c.id=b.circle_id
                WHERE i.thread_uuid=%s AND i.board_uuid=%s AND i.operation='INITIATE'
                  AND (i.request ? 'attachment' OR i.request ? 'referral_result')""",
                [actor.pk, thread_uuid, board["board_uuid"]],
            )
            if links:
                state.update(
                    {
                        name: value
                        for name, value in links[0].items()
                        if value is not None
                    }
                )
        state["phase_dates"] = disc._rows(
            """SELECT operation,facts->>'phase' AS phase,effective_at AS entered_at,
                      (facts->>'deadline')::timestamptz AS deadline,(facts->>'response_deadline')::timestamptz AS response_deadline,(facts->>'closed_at')::timestamptz AS closed_at
               FROM public.core_cqact WHERE board_uuid=%s AND thread_uuid=%s AND facts ? 'phase'
                 AND (%s OR operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE','SETTLE','NO_BALLOT','CLOSE')) ORDER BY ordinal""",
            [board["board_uuid"], thread_uuid, board["thread_audience"]],
        )
        return state
