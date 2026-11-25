"""Presentation data only: no authority, membership or voting decisions."""
import math
from django.conf import settings

DOMAINS = {"library":"Library", "education":"Education", "events":"Events", "service":"Service", "discussion":"Discussion"}

ANSWERS = {
    "consented": ("Consented", "✓"),
    "declined": ("Declined", "✕"),
    "abstained": ("Abstained", "—"),
    "pending": ("Not yet", "…"),
}

def initials(name):
    """First and last words; collisions intentionally remain visible in the list."""
    words = str(name or "").split()
    return (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper() if words else "?"

def vote_labels():
    """One extension point for a later, governed organisation label."""
    return {"action": "Vote", "process": "Voting"}

def ballot_choice_labels():
    """The default words for a ballot's answers, beside the same extension point.

    When organisations can set their own labels (owed rows C1-153 and C1-162),
    this is the one place the ballot's words are taken from.
    """
    return {"QUALIFIED": "For", "NOT_QUALIFIED": "Against", "ABSTAIN": "Abstain"}


# The member's own answer card (HG captures AH Q18 and AP2 of 24 Nov 2026): INTEVIA's shared notice
# in the kind that matches the answer: green for For, red for Against, amber for Abstain.
ANSWER_NOTICE_KINDS = {"QUALIFIED": "success", "NOT_QUALIFIED": "refusal", "ABSTAIN": "warning"}


# Insufficient participation is a danger signal (HG captures AH Q19 and AQ4 of 24 Nov 2026): INTEVIA's red
# card, for this outcome only. Every other result with no decision keeps its plain look.
INSUFFICIENT_PARTICIPATION = "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION"


def danger_card(count=None, label="Board members"):
    """The red card's words. The second sentence is given only from a result's own counts.

    The stored result keeps its own sentence; these fuller words are the card's alone (capture AQ4).
    """
    card = {"lead": "Insufficient participation.", "rest": "No decision was reached.", "counts": None}
    if count and all(key in count for key in ("participated", "eligible", "required")):
        # The same words as the result page's participation row, abstentions included (capture AX2).
        from core.vote_tests import participation_sentence
        card["counts"] = participation_sentence(count, label)
    return card


def own_answer_card(choice, *, voting_open):
    """The words and colour of the member's own answer card, or None for a choice the ballot does not offer.

    While voting is open the card also carries INTEVIA's confirmation, so the member reads one card
    (capture AQ2): "Your answer is recorded. You answered For." After the vote closes it reads
    "You answered For." alone.
    """
    label = ballot_choice_labels().get(choice)
    if label is None:
        return None
    return {"label": label, "kind": ANSWER_NOTICE_KINDS[choice],
            "text": ("Your answer is recorded. " if voting_open else "") + f"You answered {label}."}

def vote_status_line(answered, eligible):
    """The count-only status of an open vote, for the line under a Topic's title and the Board's list."""
    return f"Voting open · {answered} of {eligible} answered"

def participation(members=(), *, counts=None, anonymous=False):
    """Build display values; anonymous output has no member objects or names.

    Callers must supply only already-authorised information. This function does
    not decide anonymity or visibility. Anonymous callers should pass counts
    only, never load names merely to hide them here.
    """
    if anonymous:
        if counts is None:
            raise ValueError("Anonymous participation needs explicit counts.")
        safe_counts = {key: int(counts.get(key, 0)) for key in ANSWERS}
        if any(value < 0 for value in safe_counts.values()):
            raise ValueError("Participation counts cannot be negative.")
        people = []
    else:
        people = []
        safe_counts = {key: 0 for key in ANSWERS}
        for member in members:
            state = member.get("answer", "pending")
            if state not in ANSWERS:
                raise ValueError("Unknown participation answer.")
            name = str(member["name"])
            people.append({"name": name, "initials": initials(name), "role": member.get("role", ""),
                           "answer": state, "label": ANSWERS[state][0], "symbol": ANSWERS[state][1]})
            safe_counts[state] += 1
    total = sum(safe_counts.values())
    threshold = settings.INTEVIA_ORBIT_MAX_MEMBERS
    mode = "ring" if anonymous or total > threshold else "orbit"
    for index, member in enumerate(people):
        angle = 2 * math.pi * index / max(total, 1) - math.pi / 2
        member["x"] = f"{math.cos(angle):.5f}"
        member["y"] = f"{math.sin(angle):.5f}"
    summary = ", ".join(f"{safe_counts[key]} {label}" for key, (label, _) in ANSWERS.items())
    return {"anonymous": anonymous, "mode": mode, "people": people, "total": total,
            "counts": [{"key": key, "label": label, "symbol": symbol, "count": safe_counts[key]}
                       for key, (label, symbol) in ANSWERS.items()], "labels": vote_labels(),
            "ring": {"total": total, "label": "people", "text": f"{total} people: {summary}",
                     "segments": ring_segments([(key, safe_counts[key]) for key in ANSWERS], total)}}


def ring_segments(parts, total):
    """Turn counts into the ring's arcs: each a share of 100, starting where the last one ended.

    A share still to come ("pending") is drawn flat; every recorded share takes the shared glass.
    """
    segments, start = [], 0.0
    for key, count in parts:
        length = 100 * count / total if total > 0 else 0.0
        segments.append({"key": key, "glass": key != "pending", "length": f"{length:.3f}",
                         "rest": f"{100 - length:.3f}", "offset": f"{0.0 - start:.3f}", "shown": count > 0})
        start += length
    return segments


def count_ring(answered, eligible, label):
    """The count-only ring of an open vote: how many have answered, and no one's name or answer.

    Interim by the Human Governor's rulings of 24 Nov 2026 (captures E and G; owed row
    C1-401): names join only where a subscribing organisation turns the default off.
    """
    waiting = max(eligible - answered, 0)
    return {"total": eligible, "label": label, "text": f"{answered} of {eligible} {label} have answered",
            "segments": ring_segments([("answered", answered), ("pending", waiting)], eligible),
            "counts": [{"key": "answered", "symbol": "✓", "label": "Answered", "count": answered},
                       {"key": "pending", "symbol": "…", "label": "Not yet", "count": waiting}]}


# The closed vote's ring (HG captures AF, AK4 and AO1 of 24 Nov 2026). The keys are the shared answer
# classes, as the shared participation component already draws them: "consented" is the For green,
# "declined" the Against red and "abstained" the Abstain amber. "pending" is the share that did not
# answer, drawn flat.
NO_ANSWER_LABEL = "Did not answer"


def result_ring(tally, label):
    """The ring, the counts and, where the record names them, who answered what on a closed vote.

    ``tally`` is ``cq_reads``'s own count of the recorded result. Abstain has a row only where the
    vote allowed it. ``names`` is present only under NAMED reporting; a row with no one is left out.
    """
    words = ballot_choice_labels()
    rows = [("consented", "✓", words["QUALIFIED"], tally["for"], "QUALIFIED"),
            ("declined", "✕", words["NOT_QUALIFIED"], tally["against"], "NOT_QUALIFIED")]
    if tally["abstain_allowed"] or tally["abstain"]:
        rows.append(("abstained", "—", words["ABSTAIN"], tally["abstain"], "ABSTAIN"))
    rows.append(("pending", "…", NO_ANSWER_LABEL, tally["no_answer"], "NO_ANSWER"))
    summary = ", ".join(f"{count} {word}" for _, _, word, count, _ in rows)
    ring = {"total": tally["eligible"], "label": label, "text": f"{tally['eligible']} {label}: {summary}",
            "segments": ring_segments([(key, count) for key, _, _, count, _ in rows], tally["eligible"]),
            "counts": [{"key": key, "symbol": symbol, "label": word, "count": count} for key, symbol, word, count, _ in rows]}
    if tally.get("names") is not None:
        ring["names"] = [{"label": word, "names": tally["names"][answer]}
                         for _, _, word, _, answer in rows if tally["names"].get(answer)]
    return ring


TOPIC_TYPES = {"TOPIC": "Topic", "QUESTION": "Question", "SUGGESTION": "Suggestion"}


def topic_type_label(value):
    """Public label for a stored ordinary Topic type."""
    return TOPIC_TYPES.get(value, "")
