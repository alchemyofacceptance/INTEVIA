"""The words for the tests a vote is decided by: presentation only, no voting decision.

One set of sentence builders (HG captures AT, AX2 and AY of 24 Nov 2026) serves the result page
("How this result was reached") and the Topic while voting is open ("How this vote will be
decided"), so the rule a member reads before the vote and the test they read after it are worded
alike.

Every sentence is built from what the vote recorded and never from today's settings: ``count`` is
the result's own stored count (``public.cq_count_decision``, migration 0075) and ``rule`` is the
rule the ballot fixed when voting opened (the ballot act's ``facts->'rules'``). Nothing is decided
here: the result's ``kind`` says which test failed, and these builders only word it.
"""

# The shared chips: INTEVIA's answer chips in the kinds the stylesheet already has.
MET = {"state": "met", "key": "consented", "symbol": "✓", "label": "Met"}
NOT_MET = {"state": "not_met", "key": "declined", "symbol": "✕", "label": "Not met"}
NOT_TESTED = {"state": "not_tested", "key": "pending", "symbol": "—", "label": "Not tested"}
NEEDED = {"state": "needed", "key": "pending", "symbol": "", "label": "Needed"}

FEWER_THAN_THREE = "NO_DECISION_FEWER_THAN_THREE"
INSUFFICIENT = "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION"
TIE = "NOT_QUALIFIED_TIE"
CARRIED = "QUALIFIED"
NOT_CARRIED = "NOT_QUALIFIED_VOTE"
# The results of an ordinary counted ballot. Any other kind (a result with no ballot, an election's
# changed slate) has no test summary and keeps its own outcome sentence.
COUNTED_KINDS = (CARRIED, NOT_CARRIED, TIE, INSUFFICIENT, FEWER_THAN_THREE)

_FRACTIONS = {(1, 2): "half", (1, 3): "one third", (2, 3): "two thirds", (1, 4): "one quarter",
              (3, 4): "three quarters", (2, 5): "two fifths", (3, 5): "three fifths", (4, 5): "four fifths"}


def outcome_word(kind):
    """The Outcome row's word for a counted ballot (capture AT2), or None for any other result."""
    if kind not in COUNTED_KINDS:
        return None
    return {CARRIED: "Carried", NOT_CARRIED: "Not carried", TIE: "Not carried"}.get(kind, "No decision")


def _row(chip, text):
    return {**chip, "text": text}


def _abstentions(count):
    """", including 1 abstention" after "took part", only where abstentions exist (capture AX2)."""
    number = count.get("abstained") or 0
    if not number:
        return ""
    return f", including {number} abstention{'' if number == 1 else 's'}"


def participation_sentence(count, label="Board members"):
    """How many took part and how many were required, from the result's own count."""
    required = count["required"]
    needed = ("No minimum was required." if required == 0
              else f"{required} {'was' if required == 1 else 'were'} required.")
    return f"{count['participated']} of {count['eligible']} eligible {label} took part{_abstentions(count)}. {needed}"


def _fraction(rule):
    pair = (rule.get("numerator"), rule.get("denominator"))
    return _FRACTIONS.get(pair, f"{pair[0]}/{pair[1]}")


def _base_words(rule):
    """What the threshold is measured against, as the ballot's rule fixed it."""
    return {"ELIGIBLE": "eligible members", "PARTICIPANTS": "the answers"}.get(
        rule.get("threshold_base"), "the For and Against answers")


def _abstention_note(count, rule):
    """Say that abstentions were left out of the majority's base, only where that is so."""
    number = count.get("abstained") or 0
    if not number or rule.get("threshold_base") in ("ELIGIBLE", "PARTICIPANTS"):
        return ""
    return (" The abstention counted towards participation only." if number == 1
            else f" The {number} abstentions counted towards participation only.")


def _majority_sentence(count, rule, carried):
    """The threshold test's sentence for a ballot whose threshold was applied.

    ``carried`` is the stored outcome. A fraction that was not reached is never said to have been
    reached (HG capture BK of 25 Nov 2026, BK2; GPT Astra's posts-box part 2 review, P2-1): the failed
    sentence gives the count first, then what was required.
    """
    yes, base = count["qualified"], count.get("qualification_denominator")
    if base is None:
        base = yes + count["not_qualified"]
    note = _abstention_note(count, rule)
    of_what = {"ELIGIBLE": "eligible members", "PARTICIPANTS": "answers"}.get(
        rule.get("threshold_base"), "For and Against answers")
    if rule.get("threshold") == "FRACTION":
        if not carried:
            return f"{yes} of {base} {of_what} were For. At least {_fraction(rule)} were required.{note}"
        return f"At least {_fraction(rule)} of {_base_words(rule)} were For: {yes} of {base}.{note}"
    return f"Majority: {yes} of the {base} {of_what} were For. More than half was required.{note}"


def _untested(rule, because):
    name = f"At least {_fraction(rule)} of {_base_words(rule)}" if rule.get("threshold") == "FRACTION" else "Majority"
    return _row(NOT_TESTED, f"{name}: not tested, because {because}.")


def result_tests(count, rule, label="Board members"):
    """The rows of "How this result was reached", in the engine's order, or None where there is no summary.

    The engine tests that at least three members are eligible, then participation, then the
    threshold; once a test fails, a later one is "Not tested". There is no summary for a result
    that is not a counted ordinary ballot, or whose ballot's rule is not at hand.
    """
    kind = (count or {}).get("kind")
    if kind not in COUNTED_KINDS or not rule or "participated" not in count:
        return None
    if kind == FEWER_THAN_THREE:
        left = count["eligible"]
        because = "fewer than three eligible members remained"
        return [_row(NOT_MET, f"At least three eligible members: {left} remained."),
                _row(NOT_TESTED, f"Participation: not tested, because {because}."),
                _untested(rule, because)]
    participation = f"Participation: {participation_sentence(count, label)}"
    if kind == INSUFFICIENT:
        return [_row(NOT_MET, participation), _untested(rule, "participation was not met")]
    if kind == TIE:
        tied = f"The vote was tied, {count['qualified']} to {count['not_qualified']}. A tie is not carried."
        return [_row(MET, participation), _row(NOT_MET, tied + _abstention_note(count, rule))]
    carried = kind == CARRIED
    return [_row(MET, participation), _row(MET if carried else NOT_MET, _majority_sentence(count, rule, carried))]


def _tie_could_meet(rule):
    """Whether an even split would meet the stated share: a fraction of one half or lower."""
    numerator, denominator = rule.get("numerator"), rule.get("denominator")
    return bool(numerator and denominator) and 2 * numerator <= denominator


def vote_rule_rows(eligible, required, rule, label="Board members"):
    """The rows of "How this vote will be decided" while voting is open (capture AY3), or None.

    ``rule`` is the rule the open ballot fixed and ``required`` the engine's own count of how many
    must take part. With fewer than three eligible the engine reaches no decision whatever is
    answered, so no rule is promised.
    """
    if not rule or required is None or eligible is None or eligible < 3:
        return None
    if required == 0:
        participation = "Participation: no minimum is required."
    else:
        participation = (f"Participation: at least {required} of the {eligible} eligible {label} "
                         f"{'takes' if required == 1 else 'take'} part.")
    if rule.get("abstention"):
        participation += " Abstentions count towards taking part."
    if rule.get("threshold") == "FRACTION":
        threshold = f"At least {_fraction(rule)} of {_base_words(rule)} are For."
        if _tie_could_meet(rule):
            # The engine applies its tie rule before the fraction (capture BK2; Astra's P2-4).
            threshold += " A tie is not carried."
    else:
        threshold = f"Majority: more than half of {_base_words(rule)} are For."
    return [_row(NEEDED, participation), _row(NEEDED, threshold)]


def progress_line(answered, required):
    """How many have answered and how many more must, for the vote to count (capture AY3). Answers are only counted."""
    if answered == 0:
        so_far = "So far nobody has answered."
    else:
        so_far = f"So far {answered} {'has' if answered == 1 else 'have'} answered."
    more = required - answered
    if more <= 0:
        # HG capture BW of 25 Nov 2026, BW4: with no minimum and nobody answered, the line says only that.
        return so_far if answered == 0 else f"{so_far} Enough have taken part for the vote to count."
    if answered == 0:
        return f"{so_far} {more} {'is' if more == 1 else 'are'} needed for the vote to count."
    return f"{so_far} {more} more {'is' if more == 1 else 'are'} needed for the vote to count."
