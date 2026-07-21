from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase

from core.models import (
    Identity,
    IdentityTransition,
    ProfileRole,
    ProvisioningReconciliationAttempt,
)


class ProvisionFirstHumanCommandTests(TestCase):
    def options(self):
        return {
            "username": "first-human",
            "display_name": "First Human",
            "human_classification": "HUMAN DEVELOPMENT IDENTITY",
            "authority_reference": "authority:human-governor",
            "evidence_reference": "evidence:provisioning",
            "correlation_id": str(uuid4()),
            "membership_fulfilment_evidence": "evidence:membership-fulfilled",
            "activate": True,
        }

    @patch(
        "core.management.commands.provision_first_human.getpass.getpass",
        side_effect=["governed-test-password", "governed-test-password"],
    )
    def test_command_creates_governed_active_identity_without_role(
        self,
        _getpass,
    ):
        output = StringIO()
        options = self.options()

        call_command("provision_first_human", stdout=output, **options)

        identity = Identity.objects.select_related("credential").get()
        self.assertEqual(identity.access_state, Identity.AccessState.ACTIVE)
        self.assertTrue(identity.credential.is_active)
        self.assertTrue(identity.credential.check_password("governed-test-password"))
        self.assertEqual(ProfileRole.objects.count(), 0)
        self.assertEqual(
            list(identity.access_transitions.values_list("action", flat=True)),
            [IdentityTransition.Action.PROVISION, IdentityTransition.Action.ACTIVATE],
        )
        self.assertEqual(
            identity.originating_membership_requests.get()
            .reconciliation_attempts.filter(
                state=ProvisioningReconciliationAttempt.State.FULFILLED
            )
            .count(),
            1,
        )
        rendered = output.getvalue()
        self.assertIn(str(identity.identity_id), rendered)
        self.assertNotIn(options["username"], rendered)
        self.assertNotIn(options["authority_reference"], rendered)
        self.assertNotIn(options["membership_fulfilment_evidence"], rendered)
        self.assertNotIn("governed-test-password", rendered)

    @patch(
        "core.management.commands.provision_first_human.getpass.getpass"
    )
    def test_command_requires_explicit_activation_before_password_prompt(
        self,
        getpass,
    ):
        options = self.options()
        options["activate"] = False
        with self.assertRaisesMessage(CommandError, "explicit --activate"):
            call_command("provision_first_human", **options)
        getpass.assert_not_called()
        self.assertEqual(Identity.objects.count(), 0)

    @patch(
        "core.management.commands.provision_first_human.getpass.getpass",
        side_effect=["first-password", "different-password"],
    )
    def test_password_mismatch_has_no_partial_effect(self, _getpass):
        with self.assertRaisesMessage(CommandError, "did not match"):
            call_command("provision_first_human", **self.options())
        self.assertEqual(Identity.objects.count(), 0)

    @patch(
        "core.management.commands.provision_first_human.getpass.getpass",
        side_effect=[
            "governed-test-password",
            "governed-test-password",
            "governed-test-password",
            "governed-test-password",
        ],
    )
    def test_duplicate_command_rolls_back_without_partial_identity(self, _getpass):
        first_options = self.options()
        call_command("provision_first_human", **first_options)
        duplicate_options = self.options()
        duplicate_options["correlation_id"] = str(uuid4())

        with self.assertRaises(CommandError):
            call_command("provision_first_human", **duplicate_options)

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Identity.objects.count(), 1)
        self.assertEqual(ProfileRole.objects.count(), 0)
        self.assertEqual(IdentityTransition.objects.count(), 2)