import getpass
from uuid import UUID, uuid5

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import ProvisioningReconciliationAttempt
from src.intevia.services.identity_service import (
    CredentialService,
    IdentityLifecycleService,
    OriginatingMembershipProvisioningService,
)


HUMAN_CLASSIFICATION = "HUMAN DEVELOPMENT IDENTITY"


class Command(BaseCommand):
    help = "Provision and explicitly activate the first governed Human Identity."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--display-name", required=True)
        parser.add_argument("--human-classification", required=True)
        parser.add_argument("--authority-reference", required=True)
        parser.add_argument("--evidence-reference", required=True)
        parser.add_argument("--correlation-id", required=True)
        parser.add_argument("--membership-fulfilment-evidence", required=True)
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Explicitly authorise activation after membership fulfilment.",
        )

    def handle(self, *args, **options):
        if options["human_classification"] != HUMAN_CLASSIFICATION:
            raise CommandError(
                f"--human-classification must be exactly {HUMAN_CLASSIFICATION!r}"
            )
        if not options["activate"]:
            raise CommandError("explicit --activate intent is required")
        try:
            correlation_id = UUID(options["correlation_id"])
        except (TypeError, ValueError) as exc:
            raise CommandError("--correlation-id must be a valid UUID") from exc

        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Password (again): ")
        if not password or password != confirmation:
            raise CommandError("password confirmation did not match")

        technical_executor = "management:provision_first_human"
        try:
            with transaction.atomic():
                identity, request = CredentialService.provision(
                    username=options["username"],
                    password=password,
                    display_name=options["display_name"],
                    human_classification=options["human_classification"],
                    authority_reference=options["authority_reference"],
                    evidence_reference=options["evidence_reference"],
                    correlation_id=correlation_id,
                    technical_executor=technical_executor,
                )
                fulfilment_correlation_id = uuid5(
                    correlation_id,
                    "originating-membership-fulfilled",
                )
                activation_correlation_id = uuid5(
                    correlation_id,
                    "identity-activated",
                )
                OriginatingMembershipProvisioningService.reconcile(
                    request_id=request.pk,
                    state=ProvisioningReconciliationAttempt.State.FULFILLED,
                    authority_reference=options["authority_reference"],
                    evidence_reference=options[
                        "membership_fulfilment_evidence"
                    ],
                    correlation_id=fulfilment_correlation_id,
                )
                IdentityLifecycleService.activate(
                    identity_id=identity.identity_id,
                    fulfilment_evidence_reference=options[
                        "membership_fulfilment_evidence"
                    ],
                    requesting_actor=None,
                    authority_reference=options["authority_reference"],
                    technical_executor=technical_executor,
                    correlation_id=activation_correlation_id,
                )
                identity.refresh_from_db(fields=("access_state",))
        except ValidationError as exc:
            raise CommandError("governed Identity provisioning failed") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Identity {identity.identity_id} is {identity.access_state}."
            )
        )