"""DISC2 commands and audience-filtered, read-only phase and settings projections.

A caller managing its own outer transaction calls translate_refusal at its COMMIT.
All effective times are sampled by PostgreSQL. No client can supply a phase clock.
"""

import json
import re
from datetime import datetime
from uuid import UUID, uuid4, uuid5

from django.db import IntegrityError, connections, transaction

from src.intevia.services import cq_reads
from src.intevia.services import discussion as disc
from src.intevia.services import timed_events_handoff as handoff

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
    'CMT_PARENT_DORMANT': ("The sub-committee chair could not be filled. "
                           "This place is reserved while the committee that delegated the work is dormant. "
                           "The chair is still empty. "
                           "Ask the Board to check the forming law and arrange for the dormant committee to be reseated first. "
                           "After reseating, the Board or the body named in the sub-committee's forming law handles the appointment."),
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
    "CQ_PREPARE_SUGGESTION": "Only the author can mark this Suggestion as prepared. In the Board, the Board Chair can also do this.",
    "CQ_PREPARE_OFFICE": "Only the officer or administrator authorised for this vote can mark it as prepared.",
    "CQ_CLARITY_OFFICE": "Only the officer authorised for this board can extend or close Clarity.",
    "CQ_FINISH_OFFICE": "Only the officer authorised for this board can open the vote from Clarity.",
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
    "CQ_AUTHOR_CLEAR": "You are already clear as the author. You can edit the wording or reply to the discussion.",
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
    stamp = disc._rows("SELECT public.intevia_clock_statement() AS stamp")[0]["stamp"]
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


def _encoded(args, operation):
    """The request arguments as the JSON text storage receives, or the landed input refusal."""
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
    return encoded


def _execute(actor, owner, board, thread, operation, key, args, received):
    encoded = _encoded(args, operation)
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


# The acts a rule makes on a thread when a deadline has passed, and nothing else.
RULE_OPERATIONS = ("PREPARATION_DUE", "RESPONSE_DUE", "SETTLE")


def record_rule_act(*, alias, actor, organism_uuid, board_uuid, thread_uuid, operation, key, args, received):
    """Make one act a rule owes on a thread, on the named database connection.

    The public entry for a caller that acts for a rule, not for a member's own command:
    the TIMED EVENTS engine. It admits only the three operations the catch-up makes. The
    caller has already found the act due and holds the locks its own route takes. The
    act is written exactly as the catch-up writes it: the same storage function, the
    same arguments, the acting member as its recorded actor, and the rule as its
    authority, which storage decides from the operation.

    Returns the act's id, which is the request key. A request whose key has already made
    this same act returns that key again, as the landed ``cq_execute`` does; the same key
    with any other content is refused (CQ_REQUEST). A storage refusal is raised unchanged
    (IntegrityError) for the caller to handle.
    """
    if operation not in RULE_OPERATIONS:
        raise ValueError(f"not an act a rule makes: {operation!r}")
    encoded = _encoded(args, operation)
    with connections[alias].cursor() as cursor:
        cursor.execute(
            "SELECT public.cq_execute(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
            [organism_uuid, board_uuid, thread_uuid, operation, key, encoded, actor.pk, actor.access_epoch, received],
        )
        return cursor.fetchone()[0]


def _admit_settings(key, actor, fields, owner):
    # Except for a well-formed Off attempt, admission keeps its usual
    # precedence. That attempt must reach storage to leave its refusal.
    def admit():
        disc._rows(
            """SELECT public.cq4_direct_admit(lo.id,%s,%s,%s,%s,%s,public.intevia_clock_statement())
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
                    "SELECT public.cq3_result_visible(%s,id,%s,public.intevia_clock_statement()) AS visible "
                    "FROM public.core_livingorganism WHERE organism_id=%s",
                    [source_key, actor.pk, owner],
                )
                if not visible or not visible[0]["visible"]:
                    raise disc.DiscussionUnavailable()
            # Resolve visibility and role even if the caller supplied malformed
            # operation details. The writer rechecks the source at execution.
            disc._rows(
                """SELECT public.cq_source(lo.id,%s,%s,%s,%s,public.intevia_clock_statement())
                FROM public.core_livingorganism lo WHERE lo.organism_id=%s""",
                [board, thread, operation, actor.pk, owner],
            )
            if operation == "SETTINGS":
                _admit_settings(key, actor, fields, owner)
            if operation == "ANSWER":
                disc._rows(
                    "SELECT public.cq_answer_eligible(%s,%s,%s,%s,public.intevia_clock_statement())",
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
              WHERE ((h.phase='PREPARATION' AND h.deadline<=public.intevia_clock_statement())
                 OR (h.phase='CLARITY' AND h.response_deadline<=public.intevia_clock_statement())
                 OR (h.phase='VOTING' AND public.cq_can_settle(h.act_uuid,public.intevia_clock_statement())))
              AND public.disc_member_state(lo.id,m.identity_id,public.intevia_clock_statement())='ACTIVE'
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
                    AND h.act_uuid=%s AND ((h.phase='PREPARATION' AND h.deadline<=public.intevia_clock_statement())
                      OR (h.phase='CLARITY' AND h.response_deadline<=public.intevia_clock_statement())
                 OR (h.phase='VOTING' AND public.cq_can_settle(h.act_uuid,public.intevia_clock_statement())))
                    AND public.disc_member_state(h.organism_id,%s,public.intevia_clock_statement())='ACTIVE'""",
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
      public.disc_member_state(lo.id,%s,public.intevia_clock_statement()) IN ('ACTIVE','SUSPENDED','RESTRICTED')"""
    params = [organism_uuid, actor.pk]
    if not disc._rows(query, params):
        raise disc.DiscussionUnavailable()
    disc._lock_registered_organism("default", organism_uuid)
    rows = disc._rows(query, params)
    if not rows:
        raise disc.DiscussionUnavailable()
    # TIMED EVENTS: this person's standing in this LO is established here. A hint only.
    handoff.mark(organism_uuid, actor)
    return rows[0]["id"]


def _settings_circle(owner, actor, circle_uuid):
    if circle_uuid is None:
        return None
    disc._read_uuid(circle_uuid)
    rows = disc._rows(
        """SELECT c.id FROM public.core_circle c WHERE c.parent_organism_id=%s AND c.circle_uuid=%s
        AND public.disc_audience(c.parent_organism_id,c.id,%s,'MEMBERS',public.intevia_clock_statement())""",
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
            public.cq4_reporting(%s,%s,NULL,public.intevia_clock_statement()) AS reporting_rule,
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
                "SELECT (public.cq4_local_version(%s,%s,public.intevia_clock_statement())).act_uuid AS version",
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
            (facts->>'below_three_result')::uuid,organism_id,%s,public.intevia_clock_statement())
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
                CASE WHEN public.cq3_result_visible(src.act_uuid,i.organism_id,%s,public.intevia_clock_statement())
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


def _preparation_preview(board, thread, actor, owner):
    """Read the PREPARED facts and the ordinary RULE_OPEN conditions at one cut.

    The parent projection admits only Board Suggestions without an attachment.
    Keep roster identities, the applied provisions and the authority source in SQL.
    """
    return disc._rows(
        """WITH prep AS MATERIALIZED (
               SELECT public.cq_phase_facts(b.organism_id,b.board_uuid,t.thread_uuid,'PREPARED',
                          '{}'::jsonb,%s,public.intevia_clock_statement()) AS facts,
                      (i.facts#>>'{settings,clarity_days}')::integer AS days,
                      b.organism_id,b.board_uuid,t.thread_uuid,cat.access_level
               FROM public.core_discboard b JOIN public.discp_threads t USING (board_uuid)
               JOIN public.disc_categories cat ON cat.category_uuid=t.category_uuid
               JOIN public.core_cqact i ON i.board_uuid=b.board_uuid AND i.thread_uuid=t.thread_uuid
                    AND i.operation='INITIATE'
               WHERE b.board_uuid=%s AND t.thread_uuid=%s
           ), destination AS (
               SELECT facts->>'version' AS version,days,
                 CASE WHEN facts->>'phase'='CLARITY' THEN 'CLARITY'
                      WHEN facts->>'rule_open' IS DISTINCT FROM 'true' THEN NULL
                      WHEN EXISTS (
                         SELECT FROM jsonb_array_elements((facts->'applied_provisions') ||
                            public.cq_applicable(public.cq_settings_at(organism_id,public.intevia_clock_statement()),
                              board_uuid,thread_uuid,'VOTE')) AS law
                         WHERE NOT public.cq_law_current(%s,law,public.intevia_clock_statement())
                      ) THEN 'NO_DECISION_PROVISION_ENDED'
                      WHEN access_level='COORDINATORS' THEN 'NO_DECISION_MOTION_UNAVAILABLE'
                      WHEN jsonb_array_length(public.cq_open_roster(facts,board_uuid,thread_uuid,
                               public.intevia_clock_statement()))<3 THEN 'NO_DECISION_FEWER_THAN_THREE'
                      ELSE 'VOTING' END AS destination
               FROM prep
           ) SELECT version,destination,CASE WHEN destination='CLARITY' THEN days END AS days FROM destination""",
        [actor.pk, board["board_uuid"], thread, owner],
    )[0]


@transaction.atomic
def confirm_prepared(*, confirmation, **scope):
    """Record the existing command only if its durable effect matches the prompt.

    A changed wording or outcome rolls back this request. A replay compares
    the original receipt and its immediate consequence, even after later acts.
    """
    receipt = mark_prepared(**scope)
    rows = disc._rows(
        """SELECT p.facts->>'version' AS version,
                  CASE WHEN p.facts->>'phase'='CLARITY' THEN 'CLARITY'
                       WHEN child.operation='OPEN_VOTE' THEN 'VOTING'
                       WHEN child.operation='NO_BALLOT' THEN child.facts#>>'{count,kind}' END AS destination,
                  CASE WHEN p.facts->>'phase'='CLARITY'
                       THEN (i.facts#>>'{settings,clarity_days}')::integer END AS days
           FROM public.core_cqact p
           JOIN public.core_cqact i ON i.thread_uuid=p.thread_uuid AND i.board_uuid=p.board_uuid AND i.operation='INITIATE'
           LEFT JOIN public.core_cqact child ON child.thread_uuid=p.thread_uuid AND child.board_uuid=p.board_uuid
                AND child.operation IN ('OPEN_VOTE','NO_BALLOT') AND child.request->>'trigger_act'=p.act_uuid::text
           WHERE p.act_uuid=%s AND p.thread_uuid=%s AND p.operation='PREPARED'""",
        [receipt, scope["thread_uuid"]],
    )
    if rows != [confirmation]:
        raise disc.DiscussionRefused(code="preparation_changed")
    return receipt


def _vote_command_allowed(board, thread, actor, operation, args, at):
    """Test the same two stable predicates the writer checks at submission."""
    scope = [board["organism_id"], board["board_uuid"], thread, operation]
    try:
        with transaction.atomic():
            if operation == "PROCEED":
                # Opening a ballot checks more than the ordinary Clarity phase predicate.
                facts = "public.cq_ballot_facts(%s,%s,%s,%s,%s,%s::jsonb,%s,%s)"
                params = scope + [uuid4(), json.dumps(args), actor.pk, at]
            else:
                facts = "public.cq_phase_facts(%s,%s,%s,%s,%s::jsonb,%s,%s)"
                params = scope + [json.dumps(args), actor.pk, at]
            rows = disc._rows(
                "SELECT public.cq_source(%s,%s,%s,%s,%s,%s) IS NOT NULL AND "
                + facts + " IS NOT NULL AS allowed",
                scope + [actor.pk, at] + params,
            )
    except IntegrityError as error:
        code = getattr(getattr(error.__cause__, "diag", None), "message_primary", None)
        if code not in MESSAGES and code not in disc.MESSAGES:
            raise
        return False
    return bool(rows and rows[0]["allowed"])


def check_clarity_action(*, credential, organism_uuid, circle_uuid, thread_uuid, request_uuid,
                         operation, window, version):
    """Recheck this Board form under the submission lock before using the ordinary writer.

    A receipt for the same command may go to the writer's normal replay check.
    A fresh Clarity conflict must never become a ballot conflict. The writer
    checks cq_source under this lock before either replaying a receipt or writing.
    """
    actor = disc._actor(credential)
    board = cq_reads._scope(actor, organism_uuid, circle_uuid, thread_uuid)
    if not board["thread_audience"] or board["circle_kind"] != "GOVERNANCE":
        raise disc.DiscussionUnavailable()
    rows = disc._rows(
        """SELECT EXISTS (SELECT FROM public.core_cqact a WHERE a.act_uuid=%s AND a.operation=%s
                          AND a.actor_id=%s AND a.actor_epoch=%s AND a.board_uuid=h.board_uuid
                          AND a.thread_uuid=h.thread_uuid AND a.organism_id=h.organism_id) AS replay,
                  h.phase='CLARITY' AND h.act_uuid=%s AND p.version_uuid=%s AS current_form,
                  p.author_id=%s AS is_author,
                  EXISTS (SELECT FROM jsonb_array_elements(public.cq_roster(h.board_uuid,h.thread_uuid,
                             public.intevia_clock_statement())) m WHERE (m->>'person')::bigint=%s) AS eligible
           FROM public.cq_phases h
           JOIN public.discp_posts p ON p.thread_uuid=h.thread_uuid AND p.post_number=1
           WHERE h.organism_id=%s AND h.board_uuid=%s AND h.thread_uuid=%s""",
        [request_uuid, operation, actor.pk, actor.access_epoch, window, version, actor.pk, actor.pk,
         board["organism_id"], board["board_uuid"], thread_uuid],
    )
    if not rows or (not rows[0]["replay"] and not rows[0]["current_form"]):
        raise disc.DiscussionRefused(code="clarity_changed")
    if operation == "WITHDRAW" and not rows[0]["is_author"]:
        raise CQRefused("CQ_WITHDRAW_AUTHOR")
    if operation == "CONFLICT" and not rows[0]["replay"] and not rows[0]["eligible"]:
        raise CQRefused("CQ_ELECTORATE")


def _clarity_responses(board, thread, actor, row):
    """Project totals for the live wording and reader-specific response permissions."""
    at = row["read_at"]
    counts = disc._rows(
        """SELECT first.version_uuid, count(m.entry)::int AS eligible,
                  count(m.entry) FILTER (
                      WHERE (m.entry->>'person')::bigint=first.author_id OR last.is_clear
                  )::int AS confirmed,
                  coalesce(bool_or(last.is_clear) FILTER (WHERE (m.entry->>'person')::bigint=%s),
                           false) AS own_clear,
                  count(m.entry) FILTER (WHERE (m.entry->>'person')::bigint=%s)>0 AS in_roster
           FROM public.cq_phases h
           JOIN public.discp_posts first ON first.thread_uuid=h.thread_uuid AND first.post_number=1
           LEFT JOIN LATERAL jsonb_array_elements(public.cq_roster(h.board_uuid,h.thread_uuid,%s)) m(entry) ON true
           LEFT JOIN LATERAL (
               SELECT a.operation='CONFIRM' AND a.facts->>'version'=first.version_uuid::text AS is_clear
               FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid
                 AND a.facts->'member'=m.entry AND a.operation IN ('CONFIRM','QUESTION')
                 AND a.effective_at<=least(h.deadline,%s)
               ORDER BY a.ordinal DESC LIMIT 1
           ) last ON true
           WHERE h.organism_id=%s AND h.board_uuid=%s AND h.thread_uuid=%s AND h.phase='CLARITY'
             AND NOT first.author_hidden AND NOT first.coordinator_hidden
           GROUP BY first.version_uuid""",
        [actor.pk, actor.pk, at, at,
         board["organism_id"], board["board_uuid"], thread],
    )
    if not counts:
        return {}
    count = counts[0]
    version = count.pop("version_uuid")
    own_clear = count.pop("own_clear") and not row["is_author"]
    in_roster = count.pop("in_roster")
    # Use the real writer predicates. The basis here is only for the read probe.
    actions = []
    if in_roster and _vote_command_allowed(board, thread, actor, "CONFLICT", {"basis": "Declared by member"}, at):
        actions.append("CONFLICT")
    for operation in ("PROCEED", "EXTEND", "CLOSE"):
        if _vote_command_allowed(board, thread, actor, operation, {}, at):
            actions.append(operation)
    if row["is_author"] and _vote_command_allowed(board, thread, actor, "WITHDRAW", {}, at):
        actions.append("WITHDRAW")
    result = {"count": count, "own_clear": own_clear,
              "clarity_actions": actions, "clarity_window": row["clarity_window"], "clarity_version": version}
    if count["confirmed"] == count["eligible"] and count["eligible"] >= 3 and at < row["response_deadline"]:
        result["stepped_out"] = True
        result["can_proceed"] = "PROCEED" in actions
    if not row["is_author"] and at < row["deadline"]:
        for operation in ("CONFIRM", "QUESTION"):
            if _vote_command_allowed(board, thread, actor, operation, {"version": str(version)}, at):
                result[operation.lower() + "_version"] = version
    return result


# The answers a ballot can offer, in the order shown. A ballot offers ABSTAIN
# only when the rules it captured at opening allow abstention.
BALLOT_OPTIONS = ("QUALIFIED", "NOT_QUALIFIED", "ABSTAIN")


def _answer_refusal(board, thread, actor, ballot, at):
    """Ask the writer's own STABLE checks whether this reader may answer this ballot now.

    Return None when an answer would be admitted, otherwise the governed refusal
    code. The probe names the one choice every ballot offers and writes nothing.
    """
    scope = [board["organism_id"], board["board_uuid"], thread, "ANSWER"]
    args = json.dumps({"ballot": str(ballot), "choice": "QUALIFIED"})
    try:
        with transaction.atomic():
            disc._rows(
                "SELECT public.cq_source(%s,%s,%s,%s,%s,%s) IS NOT NULL AND "
                "public.cq_ballot_facts(%s,%s,%s,%s,%s,%s::jsonb,%s,%s) IS NOT NULL AS allowed",
                scope + [actor.pk, at] + scope + [uuid4(), args, actor.pk, at],
            )
    except IntegrityError as error:
        code = getattr(getattr(error.__cause__, "diag", None), "message_primary", None)
        if code not in MESSAGES and code not in disc.MESSAGES:
            raise
        return code
    return None


def _open_ballot(board, thread, actor, row):
    """Project the open ordinary ballot for this one reader: the count, and only their own answer.

    Interim, by the Human Governor's ruling of 24 Nov 2026 (capture E; owed row
    C1-401): while a vote is open no participant's name or identity is selected
    here, whatever reporting rule the ballot captured. Results follow that rule.
    An election's ballot is never projected here.
    """
    at = row["read_at"]
    rows = disc._rows(
        """SELECT (o.facts->>'attempt')::int AS attempt,
                  coalesce((o.facts->'rules'->>'abstention')::boolean,false) AS abstention,
                  c.eligible, c.answered,
                  CASE WHEN %s THEN (SELECT a.request->>'choice' FROM public.core_cqact a
                       WHERE a.operation='ANSWER' AND a.request->>'ballot'=o.act_uuid::text
                         AND a.actor_id=%s AND a.recorded_at<=%s
                       ORDER BY a.ordinal LIMIT 1) END AS own_answer
           FROM public.core_cqact o
           CROSS JOIN LATERAL (SELECT count(*)::int AS eligible, count(e.choice)::int AS answered
                FROM public.cq_ballot_entries(o.act_uuid,
                       least(%s,(o.facts->>'deadline')::timestamptz)) e) c
           WHERE o.act_uuid=%s AND o.board_uuid=%s AND o.thread_uuid=%s
             AND o.operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE')
             AND o.facts->>'event_kind'='DECISION'""",
        [row["clarity_reader"], actor.pk, at, at, row["ballot_uuid"], board["board_uuid"], thread],
    )
    if not rows:
        return None
    facts = rows[0]
    ballot = {"ballot_uuid": row["ballot_uuid"], "attempt": facts["attempt"],
              "options": [option for option in BALLOT_OPTIONS if option != "ABSTAIN" or facts["abstention"]],
              "eligible": facts["eligible"], "answered": facts["answered"]}
    if row["clarity_reader"]:
        # Only an active or probationary member receives their own state, as with their own conflict.
        refusal = _answer_refusal(board, thread, actor, row["ballot_uuid"], at)
        ballot.update(own_answer=facts["own_answer"], may_answer=refusal is None, answer_refusal=refusal)
    return ballot


@transaction.atomic
def board_open_vote_counts(*, credential, organism_uuid, circle_uuid):
    """Count the answers on every open ordinary ballot of this Board that the reader may see.

    One statement for the whole Board: thread → eligible, answered. As in
    ``_open_ballot`` (the Human Governor's ruling of 24 Nov 2026, capture E; owed
    row C1-401), no participant's name, identity or choice is selected. A ballot
    past its deadline, an election ballot and a Topic the reader may not open are
    left out. A Board-confirmation election (a vacancy filled by Board vote) opens a
    DECISION ballot, so it is counted here though the Topic does not project it:
    owed in full to row C1-289 (capture Q of 24 Nov 2026, D3).
    """
    actor = disc._actor(credential)
    disc._read_owner(organism_uuid, circle_uuid)
    rows = disc._rows(
        """SELECT h.thread_uuid, c.eligible, c.answered
           FROM public.cq_phases h
           JOIN public.core_cqact o ON o.act_uuid=h.act_uuid
           JOIN public.core_discboard b ON b.board_uuid=h.board_uuid
           JOIN public.core_livingorganism lo ON lo.id=b.organism_id
           JOIN public.core_circle circle ON circle.id=b.circle_id
           JOIN public.discp_threads t ON t.thread_uuid=h.thread_uuid AND t.board_uuid=b.board_uuid
           JOIN public.disc_categories cat ON cat.category_uuid=t.category_uuid
           JOIN public.discp_posts opening ON opening.thread_uuid=t.thread_uuid AND opening.post_number=1
           CROSS JOIN LATERAL (SELECT count(*)::int AS eligible, count(e.choice)::int AS answered
                FROM public.cq_ballot_entries(o.act_uuid, public.intevia_clock_statement()) e) c
           WHERE lo.organism_id=%s AND circle.circle_uuid=%s AND circle.kind='GOVERNANCE'
             AND h.phase='VOTING' AND o.operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE')
             AND o.facts->>'event_kind'='DECISION'
             AND public.intevia_clock_statement()<(o.facts->>'deadline')::timestamptz
             AND t.topic_type='SUGGESTION' AND NOT (opening.author_hidden OR opening.coordinator_hidden)
             AND public.disc_audience(b.organism_id,b.circle_id,%s,cat.access_level,
                                      public.intevia_clock_statement())""",
        [organism_uuid, circle_uuid, actor.pk],
    )
    return {row["thread_uuid"]: {"eligible": row["eligible"], "answered": row["answered"]} for row in rows}


@transaction.atomic
def board_topic_vote(*, credential, organism_uuid, circle_uuid, thread_uuid, preview_prepared=False):
    """Return the admitted Board reader's vote line, wording and available command.

    A live draft follows the opening post. An opened ballot pins its own version;
    a terminal act without a ballot supplies the wording at closure. While an
    ordinary ballot is the Topic's head, ``ballot`` carries the count and, for an
    active or probationary member, their own answer and whether they may answer.
    ``result_due`` tells an active member that the deadline has passed with no result.
    Once a result is recorded, ``decided_own_answer`` gives an active or probationary
    member their own answer on the decided ballot, and nobody else's (capture N).
    """
    actor = disc._actor(credential)
    board = cq_reads._scope(actor, organism_uuid, circle_uuid, thread_uuid)
    if not board["thread_audience"]:
        return None
    rows = disc._rows(
        """SELECT opening.version_uuid, opening.author_id=%s AS is_author, initiated.act_uuid, v.version_number, body.markdown,
                  initiated.request ? 'attachment' AS attached,
                  initiated.request->'attachment'->>'kind' AS attachment_kind,
                  state.act_uuid AS clarity_window, state.phase, state.deadline, state.response_deadline,
                  public.intevia_clock_statement() AS read_at, head.effective_at,
                  head.operation AS head_operation, head.facts#>>'{count,kind}' AS outcome,
                  ballot.act_uuid AS ballot_uuid,
                  CASE WHEN c.kind='GOVERNANCE' THEN 'BOARD'
                       WHEN public.cmt_live((public.cmt_current(c.id,public.intevia_clock_statement(),
                              public.intevia_clock_statement())).binding,public.intevia_clock_statement(),
                              public.intevia_clock_statement()) THEN 'COMMITTEE'
                       ELSE 'CIRCLE' END AS body_kind,
                  public.disc_member_state(b.organism_id,%s,public.intevia_clock_statement())
                    IN ('ACTIVE','PROBATIONARY') AS clarity_reader,
                  CASE WHEN ballot.authority_source @>
                      '{"kind":"RULE","reference":"HG:BR2","route":"CLARITY_FULL_PARTICIPATION"}'::jsonb
                    THEN jsonb_array_length(ballot.facts->'roster') END AS opening_count
           FROM public.core_discboard b JOIN public.core_circle c ON c.id=b.circle_id
           JOIN public.discp_threads t ON t.board_uuid=b.board_uuid
           JOIN public.discp_posts opening ON opening.thread_uuid=t.thread_uuid AND opening.post_number=1
           LEFT JOIN public.core_cqact initiated ON initiated.thread_uuid=t.thread_uuid
                AND initiated.board_uuid=b.board_uuid AND initiated.operation='INITIATE'
           LEFT JOIN public.cq_phases state ON state.board_uuid=b.board_uuid AND state.thread_uuid=t.thread_uuid
           LEFT JOIN public.core_cqact head ON head.act_uuid=state.act_uuid
           LEFT JOIN public.core_cqact ballot ON ballot.act_uuid=(head.facts->>'ballot')::uuid
                AND ballot.board_uuid=b.board_uuid AND ballot.thread_uuid=t.thread_uuid
                AND ballot.operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE')
           LEFT JOIN public.core_discpostversion v ON v.post_uuid=opening.post_uuid
                AND v.version_uuid=CASE
                    WHEN ballot.act_uuid IS NOT NULL THEN (ballot.facts->>'version')::uuid
                    WHEN state.phase IN ('RESULT','CLOSED','WITHDRAWN') THEN (head.facts->>'version')::uuid
                    ELSE opening.version_uuid END
           LEFT JOIN public.core_discbody body ON body.version_uuid=v.version_uuid
           WHERE b.board_uuid=%s AND t.thread_uuid=%s AND c.kind='GOVERNANCE'
                AND t.topic_type='SUGGESTION' AND NOT (opening.author_hidden OR opening.coordinator_hidden)""",
        [actor.pk, actor.pk, board["board_uuid"], thread_uuid],
    )
    if not rows:
        return None
    row = rows[0]
    panel = {}
    if row["act_uuid"]:
        panel.update({name: row[name] for name in (
            "phase", "deadline", "response_deadline", "read_at", "effective_at",
            "head_operation", "outcome", "ballot_uuid", "version_number", "markdown",
        )})
        panel["specialist_preparation"] = row["attachment_kind"] in ("ELECTION", "COMMITTEE")
        if row["clarity_reader"]:
            if row["phase"] == "CLARITY":
                panel.update(_clarity_responses(board, thread_uuid, actor, row))
            live_ballot = (row["phase"] == "VOTING" and row["deadline"] is not None
                           and row["read_at"] < row["deadline"])
            if live_ballot and row["opening_count"] is not None:
                panel["opening_count"] = row["opening_count"]
            if panel.get("count") or live_ballot:
                # Only the admitted reader's declaration, at the same cut as their status.
                own = disc._rows(
                    """SELECT a.facts->>'basis' AS basis FROM public.core_cqact a
                       WHERE a.board_uuid=%s AND a.thread_uuid=%s AND a.actor_id=%s
                         AND a.operation='CONFLICT' AND a.recorded_at<=%s""",
                    [board["board_uuid"], thread_uuid, actor.pk, row["read_at"]],
                )
                panel["own_conflict"] = bool(own)
                if own:
                    panel["own_conflict_reason"] = own[0]["basis"]
        if (row["phase"] == "VOTING" and row["ballot_uuid"] is not None
                and row["attachment_kind"] != "ELECTION"):
            ballot = _open_ballot(board, thread_uuid, actor, row)
            if ballot is not None:
                panel["ballot"] = ballot
                if row["read_at"] >= row["deadline"]:
                    # Past its deadline with no result. Only an active member's catch-up would record it.
                    active = disc._rows(
                        "SELECT public.disc_member_state(%s,%s,%s)='ACTIVE' AS active",
                        [board["organism_id"], actor.pk, row["read_at"]],
                    )[0]["active"]
                    if active:
                        panel["result_due"] = True
        elif (row["phase"] == "RESULT" and row["ballot_uuid"] is not None
              and row["attachment_kind"] != "ELECTION" and row["clarity_reader"]):
            # Capture N: only the reader's own answer on the ballot this result decided.
            own = disc._rows(
                """SELECT a.request->>'choice' AS choice FROM public.core_cqact a
                   WHERE a.operation='ANSWER' AND a.request->>'ballot'=%s
                     AND a.actor_id=%s AND a.recorded_at<=%s ORDER BY a.ordinal LIMIT 1""",
                [str(row["ballot_uuid"]), actor.pk, row["read_at"]],
            )
            if own:
                panel["decided_own_answer"] = own[0]["choice"]
        panel["body_kind"] = row["body_kind"]
        panel["election"] = row["attachment_kind"] == "ELECTION"
        if row["phase"] != "PREPARATION" or row["attached"]:
            return panel
        op, args = "PREPARED", {}
    else:
        op, args = "INITIATE", {"version": str(row["version_uuid"])}
    # These are the writer's STABLE checks. Discard all returned authority and
    # electorate data. A normal rule refusal means this reader has no command.
    if op == "PREPARED" and preview_prepared:
        try:
            with transaction.atomic():
                disc._rows("SELECT public.cq_source(%s,%s,%s,%s,%s,public.intevia_clock_statement()) IS NOT NULL",
                           [board["organism_id"], board["board_uuid"], thread_uuid, op, actor.pk])
                preview = _preparation_preview(board, thread_uuid, actor, organism_uuid)
        except IntegrityError as error:
            code = getattr(getattr(error.__cause__, "diag", None), "message_primary", None)
            if code not in MESSAGES and code not in disc.MESSAGES:
                raise
            return panel
        if preview["destination"] is None:
            return panel
        panel["confirmation"] = preview
        panel["is_author"] = row["is_author"]
    elif not _vote_command_allowed(board, thread_uuid, actor, op, args, row["read_at"]):
        return panel
    panel["operation"] = op
    if op == "INITIATE":
        panel["opening_version"] = row["version_uuid"]
    return panel
