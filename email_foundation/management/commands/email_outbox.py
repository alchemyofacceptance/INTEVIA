"""Local worker, stuck summary and housekeeping; never prints message payloads."""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from email_foundation.models import Outbox
from email_foundation.service import database_now
from email_foundation.worker import housekeeping, recover, run_worker


class Command(BaseCommand):
    help = "Process captured email, report stuck work, reconcile acceptance or erase old operational payloads."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("work", "check", "recover", "housekeep"))
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 1000:
            raise CommandError(
                "No email work started: --limit must be between 1 and 1000."
            )
        action = options["action"]
        if action == "check":
            now = database_now()
            stuck = Outbox.objects.filter(
                Q(state=Outbox.State.ACCEPTANCE_UNCERTAIN)
                | Q(state=Outbox.State.QUEUED, lease_until__lte=now)
                | Q(
                    state__in=(Outbox.State.REQUESTED, Outbox.State.QUEUED),
                    ready_at__lt=now - timedelta(hours=1),
                    lease_id__isnull=True,
                )
            ).count()
            self.stdout.write(
                f"Email intents requiring operator attention: {stuck}. No message content printed."
            )
            if stuck:
                raise CommandError(
                    "Email work needs attention. Inspect the local outbox and run recover; uncertain acceptance is not permission to resend."
                )
            return
        count = {"work": run_worker, "recover": recover, "housekeep": housekeeping}[
            action
        ](limit)
        self.stdout.write(
            f"Email {action}: {count} records processed. This command does not certify delivery or reading."
        )
