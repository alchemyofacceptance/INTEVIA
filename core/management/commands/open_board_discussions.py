"""Open the discussion of every active Board that has none. One-off, safe to repeat.

Boards activated before activation opened their discussion (HG capture CX,
23 Nov 2026) have none. For each, this command asks the discussion service to
open it as the person who activated that Board, with the request key the
activation itself would have used. The service applies its usual checks to that
person. If the service refuses (for example the person no longer holds a current
seat on the Board, is no longer an active member, or their sign-in is not active),
nothing is written for that Board and it is reported as skipped, stating only what
the command knows: that the discussion could not be opened as that person.
The command uses no owner or superuser power and bypasses no check; it loads the
activating person's sign-in record only to name them to the service.
"""
from uuid import UUID, uuid5

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connections

from src.intevia.services import discussion

# Active Boards with no discussion, each with its latest activation and that
# activation's human enterer. The discussion service works on "default" only.
PENDING = """
    SELECT lo.organism_id, c.circle_uuid, a.event_uuid, i.credential_id
    FROM public.core_circle c
    JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
    JOIN LATERAL (
        SELECT e.event_uuid, e.enterer_identity_id FROM public.core_circlestateevent e
        WHERE e.circle_id=c.id AND e.action='ACTIVATE' AND e.resulting_state='ACTIVE'
        ORDER BY e.sequence DESC LIMIT 1) a ON true
    LEFT JOIN public.core_identity i ON i.id=a.enterer_identity_id
    WHERE c.kind='GOVERNANCE'
      AND public.s015_fold_effective_state('core_circle',c.id,
            public.intevia_clock_statement(),public.intevia_clock_statement())='ACTIVE'
      AND NOT EXISTS (SELECT FROM public.core_discboard b WHERE b.circle_id=c.id)
    ORDER BY lo.organism_id, c.circle_uuid"""

SKIP_REASON = ("the discussion could not be opened as the person who activated this Board. "
               "Nothing was written. Ask the Organisation Coordinator to check that person's "
               "sign-in and Board seat before running this again")


class Command(BaseCommand):
    help = "Open the discussion of every active Board that has none, as the person who activated it."

    def handle(self, *args, **options):
        with connections["default"].cursor() as cursor:
            cursor.execute(PENDING)
            pending = cursor.fetchall()
        users = get_user_model().objects
        opened = skipped = 0
        for organism, circle, activation, credential_id in pending:
            organism, circle, activation = (UUID(str(value)) for value in (organism, circle, activation))
            user = users.filter(pk=credential_id).first() if credential_id is not None else None
            try:
                if user is None:
                    raise discussion.DiscussionUnavailable()
                discussion.ensure_board(credential=user, organism_uuid=organism, circle_uuid=circle,
                                        request_uuid=uuid5(activation, "board"))
            except (discussion.DiscussionRefused, discussion.DiscussionUnavailable):
                skipped += 1
                self.stdout.write(f"SKIPPED organisation {organism} Board {circle}: {SKIP_REASON}.")
            else:
                opened += 1
                self.stdout.write(f"OPENED organisation {organism} Board {circle}.")
        self.stdout.write(f"Active Boards without a discussion: {len(pending)}. "
                          f"Opened: {opened}. Skipped: {skipped}.")
