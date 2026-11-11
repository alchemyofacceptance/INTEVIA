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

ELECTION_METHOD_MESSAGES = {
    "ELECT_METHOD_ROUTES": "The office definition and the cited provision give different nomination routes. Reconcile them before calling the election.",
    "ELECT_METHOD_PLACE": "The cited provision must identify a supported electoral place, either a seat or an office.",
    "ELECT_METHOD_FIELDS": "The cited electoral provision contains an unsupported field. Review the provision before calling the election.",
    "ELECT_METHOD_COUNT_FIELDS": "Supply the counting instructions as an object containing only supported counting fields.",
    "ELECT_METHOD_COUNT": "This counting method is unsupported. Hold the election before opening.",
    "ELECT_METHOD_NOMINATION": "The cited nomination route is unsupported. Review who may nominate for this place.",
    "ELECT_METHOD_TERM": "Review the term. Supply an end and any start with a time zone, with the end after the start or current time.",
    "ELECT_METHOD_VALUE": "A value in the cited provision cannot be read. Check the representation of the provision and its dates.",
    "ELECT_METHOD_WAIVER": "The electoral minimum-participation waiver must be true or false.",
    "ELECT_METHOD_ABSTENTION": "The electoral abstention rule must be true or false.",
    "ELECT_METHOD_BASE": "Specify a supported basis for counting votes under the cited provision.",
    "ELECT_METHOD_TIE": "Specify the supported rule for a tied election under the cited provision.",
    "ELECT_METHOD_QUORUM": "Review the electoral participation rule, its value and any minimum waiver in the cited provision.",
    "ELECT_METHOD_THRESHOLD": "Review the qualification threshold and any fraction in the cited electoral provision.",
}


MESSAGES = {
    'CMT_FORM': 'This Circle needs an adopted forming Policy or Bylaw and its exact terms of reference. Put the complete mandate to the competent body.',
    'CMT_AUTH': 'Put this act to the body named in the forming law.',
    'CMT_STALE': 'The committee’s mandate has changed. Review the current version before putting this change forward.',
    'CMT_PARENT': 'The Board can form this sub-committee by Policy. Formation by its parent needs subscriber entitlement and authority in the parent’s terms.',
    'CMT_AREA': 'These terms do not authorise this act in this area. Refer it to the Board.',
    'CMT_LEVEL_2': 'This committee can recommend; the Board decides.',
    'CMT_LEVEL_3': 'This committee can report findings. Send them to the deciding body.',
    'CMT_LEVEL_4': 'This committee can investigate. Give the information to the deciding body.',
    'CMT_FLOOR': 'A current Board member must hold this committee place. Identify an eligible trustee before formation.',
    'CMT_CHAIR': 'These terms require a Board member as chair. Choose a current trustee.',
    'CMT_TIER': 'This organisation uses one chair rule for all committees. Use the Board’s recorded rule.',
    'CMT_CHOICE': 'The Board must record its chair rule before forming this committee.',
    'CMT_CHAIR_GAP': "The committee chair's place is empty. Ask the Board to fill it under the forming law.",
    'CMT_ENDED': 'This committee’s mandate has ended. Refer new work to the Board.',
    'CMT_END': 'Identify the dated end or the exact task and its completion authority in the terms.',
    'CMT_TASK': 'Only the authority named in the terms can record completion of the stated task. Refer it there.',
    'CMT_POWER': 'The grant does not cover this electoral act or place. Refer it to the recorded holder.',
    'CMT_RECUSED': 'You are a candidate in this election. Ask another authorised administrator to act.',
    'CMT_ADMIN': 'The Board may name an eligible member for this contest after the administrator stands aside.',
    "CMT_WORK_STAND_DOWN": (
        "This work passed to the LO Coordinator(s) when the Board stood down. "
        "This request cannot change it. Its custody record is unchanged. "
        "Ask an LO Coordinator to pick it up or arrange reassignment."
    ),
    'CMT_WORK': 'This work has returned to the chair. Ask the chair to reassign it.',
    'CMT_NO_CHAIR': 'There is no effective chair to receive this work. Refer it to the Board.',
    'CMT_WORK_LAW': 'No governing work-transfer clause is in force. Ask the Board to identify or adopt one.',
    "CQ_BOARD_ACTIVE": (
        "Switching voting off was refused. Voting stays on while the Board is active. "
        "Nothing changed. Put Board stand-down to the members or the body named in the "
        "constitution. Your LO Coordinator(s) can help."
    ),
    "CQ_SETTINGS_TIME": (
        "Voting authority could not be checked because the effective or knowledge time was missing. "
        "Nothing changed. Retry with both times. If the service supplied them, ask the service "
        "administrator to check the request."
    ),
    "CQ_SETTINGS_WORDING": "The attached wording differs from the proposed settings. Attach the exact change again.",
    "CQ_SETTINGS_REFERRAL": "Use the exact settings motion that closed below three eligible members. A Coordinator who declared a conflict cannot decide it.",
    "CQ_SETTINGS_VOTE": "The members decide these settings by vote. Put the change forward as a Suggestion.",
    "CQ_SETTINGS_RESTART": "Re-enable CQ with the recorded settings, then put the change to the members.",
    "CQ_REPORT_SCOPE": "Each subscribing Circle decides its own reporting. Put a local change to that Circle.",
    "CQ_REPORTING_LAW": "The cited constitutional provision fixes reporting for this Circle. Follow the provision or change the law first.",
    "CQ_SETTINGS_PROVISION": "A cited provision is still in force. Keep it in these settings or change the law first.",
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
    "CQ_CHOICE": "Choose For, Against or Abstain.",
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
    "CQ_RESTORE_OFF": "The Board cannot be restored while voting is off. Its state is unchanged. Ask an effective LO Coordinator to re-enable the recorded voting settings, then put restoration to the deciding body.",
    "CQ_RESTORE_TENURE": "This restoration does not cover the current stood-down Board. Its state is unchanged. Check the latest Board decision. Your LO Coordinator(s) can help trace it.",
    "CQ_RESTORE_PENDING": "Restoration is already authorised for this Board. It remains inactive until its trustees are seated. Follow the existing decision. Your LO Coordinator(s) can present it for activation.",
    "CQ_RESTORE_SEATING": "This seat needs a current restoration decision and voting on. No trustee has been seated. Follow the members’ decision and the constitutional election or recognition route. Ask the LO Coordinator(s) for the record.",
    "CQ_RESTORE_ROSTER": "An existing Board place has no award under this restoration decision. The Board remains inactive. Review the place and its original term. Your LO Coordinator(s) can help reconcile its governing record.",
    "CQ_RESTORE_TERM": "This restoration election needs a term stated in the cited provision. The seat has not been filled. Check the term in the constitution. The LO Coordinator(s) can help locate the governing version.",
    "CQ_STAND_DOWN_DATE": "Choose stand-down at the result. A separately dated stand-down is not yet available.",
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
    "ELECT_CITATION": "Cite the constitutional provision and version for this place, or enter the constitution in LIBRARY.",
    **ELECTION_METHOD_MESSAGES,
    "ELECT_GOVERNANCE": "Check the recorded Birthing choice and effective GOVERNANCE configuration before calling this election.",
    "ELECT_GRANT_BODY": "Only the competent Board can adopt this transfer Policy.",
    "ELECT_GRANT_SCOPE": "Identify one exact power, its holder, recipient and constitutional provision.",
    "ELECT_GRANT_LAW": "Cite a current provision authorising this transfer, including a membership decision for a reserved power.",
    "ELECT_GRANT_STALE": "The power has a different holder or predecessor. Review its latest transfer.",
    "ELECT_GRANT_SAME": "This body already holds the named power.",
    "ELECT_GRANT_RECIPIENT": "Choose an existing eligible Circle of this organisation or return the power to its constitutional holder.",
    "ELECT_VACANCY_SOURCE": "Choose a recorded, effective vacancy for an operative election.",
    "ELECT_VACANCY_HOLD": "The vacancy is recorded. Obtain direction on the constitutional route before filling it.",
    "ELECT_VACANCY_ROUTE": "Use the filling route and body named for this vacancy.",
    "ELECT_VACANCY_BOARD": "The constitution requires an active GOVERNANCE Board for this vacancy.",
    "ELECT_VACANCY_USED": "This vacancy already has a call. Follow its recorded journey.",
    "ELECT_VACANCY_APPOINTER": "Only the constitutionally named appointing authority may fill this vacancy.",
    "ELECT_VACANCY_NOMINEE": "A Board appointment decides one consenting nominee. Resolve the slate before opening.",
    "ELECT_VACANCY_LINK": "This place has a recorded vacancy. Start from its filling instruction.",
    "ELECT_CALL": "This body cannot call this election. Use the authority named for this seat.",
    "ELECT_NOMINATION": "This seat uses a different nomination route. Follow the cited provision.",
    "ELECT_CANDIDATE": "Choose an eligible member for this place and cite their office proposal where required.",
    "ELECT_SLATE_LOCKED": "The nomination window for this act has closed. Check the current election phase.",
    "ELECT_OWN": "Only the nominee can consent to or withdraw this nomination.",
    "ELECT_DUPLICATE": "This nomination or response is already recorded.",
    "ELECT_EMPTY": "No eligible candidate has consented. Return to nominations.",
    "ELECT_CHANGED": "The list changed. Review the slate and call a fresh election.",
    "ELECT_CHOICE": "Choose a candidate on this ballot, or use a permitted confirmation or abstention.",
    "ELECT_OCCUPIED": "This place is occupied. Review its recorded term before calling another election.",
    "ELECT_RESULT": "This result does not authorise this person for this place. Use the applicable result.",
    "ELECT_HANDOVER_UNKNOWN": "The cited provision gives no confirmed handover date. Supply the provision and evidence of its date.",
    "ELECT_HOLDER_EXPIRED": "Your term has ended. Check the vacancy or succession record for this place.",
    "ELECT_RECOGNISER": "This recognition requires the authority named in the constitution. Refer it to that body.",
    "ELECT_ORIGINAL": "The original appointment is unverified. Cite its decision and the evidence required by the constitution.",
    "ELECT_RESIGN_DATE": "This resignation date cannot be used. It must fall within your current term and not before your notice. Check the date the constitution gives.",
    "ELECT_REMAINDER": "The remaining term cannot be established. Cite the term provision, its version and the appointment dates.",
    "ELECT_RECOGNITION_CONSENT": "The holder has not confirmed this place. Ask them to record their consent.",
    "ELECT_RECOGNITION_EXPIRED": "This appointment has ended. Check the vacancy route.",
    "ELECT_RECOGNITION_OCCUPIED": "This place is already occupied for that period. Check the incumbency record.",
    "ELECT_RESIGN_HOLDER": "Only the holder can resign this Board place. The holder must give their own notice.",
    "ELECT_NOTICE_DATE": "Your notice is recorded. The effective date needs a constitutional basis. Refer it to the competent body.",
    "ELECT_CONT_REQUEST": "This request already records a different act. Check the receipt or use a new request.",
    "ELECT_SUCCESSOR_LOST": "The successor is no longer eligible. Check the constitutional filling route.",
    "ELECT_VACANCY_VOTING": "The vacancy is recorded. Its filling route requires voting to be on before an election can open.",
    "ELECT_HOLDER_EARLY": "Your term has not started. Check the start recorded for this place.",
    "ELECT_HANDOVER": "These terms do not meet at the recorded handover. Check the place and both terms before calling the election.",
    "ELECT_TERM": "This prospective succession needs the term-continuity route. Hold it for that route.",
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


def _admit_settings(key, actor, fields, owner):
    # Except for a well-formed Off attempt, admission keeps its usual
    # precedence. That attempt must reach storage to leave its refusal.
    def admit():
        disc._rows(
            """SELECT public.cq4_direct_admit(lo.id,%s,%s,%s,%s,%s,statement_timestamp())
               FROM public.core_livingorganism lo WHERE lo.organism_id=%s""",
            [key, actor.pk, actor.access_epoch, fields.get("changes") == {"enabled": True},
             _id(fields.get("below_three_result"), nullable=True), owner],
        )

    off_attempt = (
        isinstance(fields.get("changes"), dict)
        and fields["changes"].get("enabled") is False
        and (fields.get("predecessor") is None or isinstance(fields["predecessor"], UUID))
        and "below_three_result" not in fields
    )
    if off_attempt:
        try:
            with transaction.atomic():
                admit()
        except IntegrityError as denied:
            code = getattr(getattr(denied.__cause__, "diag", None), "message_primary", None)
            if code != "CQ_SETTINGS_VOTE":
                raise
            # The input trigger repeats admission and returns this same
            # public reason. It can commit a receipt if the Board rule applies.
    else:
        admit()


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
            if operation == "SETTINGS":
                _admit_settings(key, actor, fields, owner)
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
            act = _execute(actor, owner, board, thread, operation, key, fields, received)
            if act is not None:
                return act
            code = disc._rows(
                """SELECT facts->>'code' AS code FROM public.core_cqact
                   WHERE act_uuid=%s AND organism_id=(SELECT id FROM public.core_livingorganism
                   WHERE organism_id=%s) AND actor_id=%s AND operation='REFUSE_VOTING_OFF'""",
                [key, owner, actor.pk],
            )[0]["code"]
        # A normal caller gets its refusal after the receipt and ILC event commit.
        # A caller that owns an outer transaction owns the final commit as usual.
        raise CQRefused(code)
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
    below_three_result=None,
):
    """A patch over the predecessor version. None identifies platform defaults."""
    return _command(
        credential,
        organism_uuid,
        request_uuid,
        "SETTINGS",
        predecessor=predecessor,
        changes=changes,
        **({"below_three_result": below_three_result} if below_three_result is not None else {}),
        **(
            {"reporting_effective_from": reporting_effective_from}
            if reporting_effective_from is not None
            else {}
        ),
    )


def settings_attachment(*, organism_uuid, predecessor, changes, reporting_effective_from=None):
    """Prepare the exact, inspectable settings patch to attach to a Suggestion."""
    spec = {"organism": organism_uuid, "predecessor": predecessor, "changes": changes}
    if reporting_effective_from is not None:
        spec["reporting_effective_from"] = reporting_effective_from
    return _settings_proposal("SETTINGS", spec)


def stand_down_attachment(*, organism_uuid, circle_uuid, lifecycle_uuid, board_uuid, citation=None):
    """Freeze the Board tenure and deciding body for a stand-down at the CQ result."""
    spec = {
        "organism": str(organism_uuid), "circle": str(circle_uuid),
        "act": "STAND_DOWN", "lifecycle": str(lifecycle_uuid),
        "body": str(board_uuid), "effective_at": "AT_RESULT", "citation": citation,
    }
    return {"kind": "BOARD_STAND_DOWN", "spec": spec,
            "text": json.dumps(spec, sort_keys=True, default=_json_reference)}


def restoration_attachment(*, organism_uuid, circle_uuid, stand_down_uuid, board_uuid, citation=None):
    """Put restoration of this precise Board tenure to its competent body."""
    spec = {
        "organism": str(organism_uuid), "circle": str(circle_uuid), "act": "RESTORE",
        "stand_down": str(stand_down_uuid), "body": str(board_uuid), "citation": citation,
    }
    return {"kind": "BOARD_RESTORE", "spec": spec,
            "text": json.dumps(spec, sort_keys=True, default=_json_reference)}


def circle_reporting_attachment(*, organism_uuid, circle_uuid, predecessor, reporting):
    """Identify the Circle and the exact local version its vote would replace."""
    return _settings_proposal("CIRCLE_REPORTING", {
        "organism": organism_uuid, "circle": circle_uuid,
        "predecessor": predecessor, "reporting": reporting,
    })


def _settings_proposal(kind, spec):
    try:
        wording = json.dumps(spec, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, default=_json_reference)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CQRefused("CQ_SETTINGS_INPUT") from exc
    return {"kind": kind, "spec": json.loads(wording), "text": wording}


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
        from src.intevia.services.elections import catch_up_vacancies

        changed = catch_up_vacancies(credential=credential)
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
                    try:
                        with transaction.atomic():
                            act = _execute(
                                actor,
                                item["owner"],
                                item["board_uuid"],
                                item["thread_uuid"],
                                operation,
                                key,
                                args,
                                received,
                            )
                    except IntegrityError as error:
                        code = getattr(getattr(error.__cause__, "diag", None), "message_primary", None)
                        if code in ELECTION_METHOD_MESSAGES or code in {"ELECT_EMPTY", "ELECT_CHANGED", "ELECT_CITATION",
                                    "ELECT_OCCUPIED", "ELECT_TERM", "ELECT_HANDOVER", "ELECT_HANDOVER_UNKNOWN", "ELECT_GOVERNANCE", "ELECT_VACANCY_BOARD",
                                    "ELECT_VACANCY_LINK", "ELECT_VACANCY_NOMINEE", "ELECT_VACANCY_ROUTE", "ELECT_VACANCY_HOLD"}:
                            continue
                        raise
                    if act is not None:
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


def _settings_circle(owner, actor, circle_uuid):
    if circle_uuid is None:
        return None
    disc._read_uuid(circle_uuid)
    rows = disc._rows(
        """SELECT c.id FROM public.core_circle c WHERE c.parent_organism_id=%s AND c.circle_uuid=%s
        AND public.disc_audience(c.parent_organism_id,c.id,%s,'MEMBERS',statement_timestamp())""",
        [owner, circle_uuid, actor.pk],
    )
    if not rows:
        raise disc.DiscussionUnavailable()
    return rows[0]["id"]


def settings(*, credential, organism_uuid, circle_uuid=None):
    """Current general settings. A motion-specific declaration is applied at its own opening."""
    with transaction.atomic():
        actor = disc._actor(credential)
        owner = _read_owner(actor, organism_uuid)
        circle = _settings_circle(owner, actor, circle_uuid)
        row = disc._rows(
            """SELECT public.cq_settings(%s) AS settings, p.reporting_version AS platform_version,
            public.cq4_reporting(%s,%s,NULL,statement_timestamp()) AS reporting_rule,
            (SELECT a.act_uuid FROM public.core_cqact a WHERE a.organism_id=%s AND a.operation='SETTINGS'
             ORDER BY a.ordinal DESC LIMIT 1) AS version FROM public.cq_platform_defaults p""",
            [owner, owner, circle, owner],
        )[0]
        rule = row["reporting_rule"]
        row["reporting_version"] = rule["version"]
        row["reporting_origin"] = rule.get("origin") or (
            "PLATFORM" if rule["version"] == row["platform_version"] else "LO"
        )
        row["settings"]["reporting"] = rule["value"]
        if circle is not None:
            # Legacy LO scope is historical metadata. A Circle's freedom to
            # choose reporting follows its own vote and the governing law.
            row["settings"].pop("reporting_scope")
            row["local_version"] = disc._rows(
                "SELECT (public.cq4_local_version(%s,%s,statement_timestamp())).act_uuid AS version",
                [owner, circle],
            )[0]["version"]
            row["inherits_reporting"] = row["reporting_origin"] in ("LO", "PLATFORM")
        row["clarity_stage_control"] = CLARITY_STAGE.copy()
        return row


def settings_history(*, credential, organism_uuid, circle_uuid=None):
    with transaction.atomic():
        actor = disc._actor(credential)
        owner = _read_owner(actor, organism_uuid)
        circle = _settings_circle(owner, actor, circle_uuid)
        if circle is not None:
            return cq_reads._circle_versions(owner, circle, actor, organism_uuid)
        rows = disc._rows(
            """SELECT act_uuid,actor_id,authority_source,received_at,effective_at,recorded_at,
          CASE WHEN facts ? 'below_three_result' AND NOT public.cq3_result_visible(
            (facts->>'below_three_result')::uuid,organism_id,%s,statement_timestamp())
            THEN facts-'below_three_result' ELSE facts END AS facts,fingerprint,
          public.cq4_decision(act_uuid,authority_source) AS decision
          FROM public.core_cqact WHERE organism_id=%s AND operation='SETTINGS' ORDER BY ordinal""",
            [actor.pk, owner],
        )
        cq_reads._project_authorities(rows, actor, organism_uuid)
        for row in rows:
            source = row["authority_source"]
            if not cq_reads._settings_enterer_visible(source):
                row.pop("actor_id")
            if source["kind"] == "CQ_RESULT" and "result_post" not in source:
                row.pop("decision")
                row["facts"].pop("decision", None)
        return rows


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
