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


TOPIC_TYPES = {"TOPIC": "Topic", "QUESTION": "Question", "SUGGESTION": "Suggestion"}


def topic_type_label(value):
    """Public label for a stored ordinary Topic type."""
    return TOPIC_TYPES.get(value, "")
