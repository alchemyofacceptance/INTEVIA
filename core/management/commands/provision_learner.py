import getpass
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.models import LearnerRegistration, PlatformTermsVersion
from src.intevia.services.identity_service import (
    LearnerRegistrationService,
    RegistrationServiceRefused,
)


class Command(BaseCommand):
    help = "Register a Learner with support, with the person as the actor."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--display-name", required=True)
        parser.add_argument(
            "--terms-version",
            required=True,
            help="The platform-terms version label to show and accept.",
        )

    def handle(self, *args, **options):
        terms_version = PlatformTermsVersion.objects.filter(
            version_label=options["terms_version"]
        ).order_by("-effective_from", "-recorded_at", "-pk").first()
        if terms_version is not None:
            self.stdout.write(terms_version.text_body)
        confirmation = input(
            "The person must type ACCEPT to confirm that they have read and "
            "accepted these platform terms: "
        )
        accepted = confirmation == "ACCEPT"
        password = getpass.getpass("Password: ")
        password_confirmation = getpass.getpass("Password (again): ")
        if not password or password != password_confirmation:
            raise CommandError("password confirmation did not match")

        try:
            registration = LearnerRegistrationService.register(
                username=options["username"],
                password=password,
                display_name=options["display_name"],
                terms_version_uuid=(
                    None
                    if terms_version is None
                    else terms_version.terms_version_uuid
                ),
                terms_accepted=accepted,
                correlation_id=uuid4(),
                entry_mode=LearnerRegistration.EntryMode.SUPPORTED,
                enterer_executor="management:provision_learner",
            )
        except RegistrationServiceRefused as exc:
            raise CommandError(exc.message) from exc
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Learner registration {registration.registration_uuid} completed."
            )
        )
