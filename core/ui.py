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
    return {"anonymous": anonymous, "mode": mode, "people": people, "total": total,
            "counts": [{"key": key, "label": label, "symbol": symbol, "count": safe_counts[key]}
                       for key, (label, symbol) in ANSWERS.items()], "labels": vote_labels()}
