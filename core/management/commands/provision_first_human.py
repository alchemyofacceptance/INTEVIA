from django.core.management.base import BaseCommand, CommandError


SETUP_COMMAND_RETIRED_MESSAGE = (
    "Nothing was created. This setup command has been retired: it recorded a "
    "root-LO membership request that INTEVIA no longer uses. Register the "
    "person with `provision_learner` instead."
)


class Command(BaseCommand):
    help = "Retired; use provision_learner."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--display-name")
        parser.add_argument("--human-classification")
        parser.add_argument("--authority-reference")
        parser.add_argument("--evidence-reference")
        parser.add_argument("--correlation-id")
        parser.add_argument("--membership-fulfilment-evidence")
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Retained only so the retired command can explain its refusal.",
        )

    def handle(self, *args, **options):
        raise CommandError(SETUP_COMMAND_RETIRED_MESSAGE)