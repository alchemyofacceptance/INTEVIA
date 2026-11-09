"""Voting authority at an effective instant and an independent knowledge cut."""

from django.db import connections


def voting_enabled(alias, organism_id, effective_at, known_at):
    with connections[alias].cursor() as cursor:
        cursor.execute(
            "SELECT (public.cq_settings_values_at(%s,%s,%s)->>'enabled')::boolean",
            [organism_id, effective_at, known_at],
        )
        enabled, = cursor.fetchone()
    if enabled is None:
        raise RuntimeError("The effective CQ settings do not establish whether voting is enabled.")
    return enabled
