from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase

from core.models import (
    Identity,
    IdentityTransition,
    OriginatingMembershipProvisioningRequest,
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

    @patch("getpass.getpass")
    def test_command_creates_governed_active_identity_without_role(self, getpass):
        output = StringIO()
        options = self.options()
        with self.assertRaises(CommandError) as refused:
            call_command("provision_first_human", stdout=output, **options)

        self.assertEqual(
            str(refused.exception),
            "Nothing was created. This setup command has been retired: it "
            "recorded a root-LO membership request that INTEVIA no longer uses. "
            "Register the person with `provision_learner` instead.",
        )
        getpass.assert_not_called()
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Identity.objects.count(), 0)
        self.assertEqual(IdentityTransition.objects.count(), 0)
        self.assertEqual(OriginatingMembershipProvisioningRequest.objects.count(), 0)
        self.assertEqual(ProvisioningReconciliationAttempt.objects.count(), 0)
        self.assertEqual(ProfileRole.objects.count(), 0)
        self.assertEqual(output.getvalue(), "")

    @patch("getpass.getpass")
    def test_command_requires_explicit_activation_before_password_prompt(
        self,
        getpass,
    ):
        with self.assertRaisesMessage(CommandError, "setup command has been retired"):
            call_command("provision_first_human")
        getpass.assert_not_called()
        self.assertEqual(Identity.objects.count(), 0)

    @patch("getpass.getpass")
    def test_password_mismatch_has_no_partial_effect(self, getpass):
        with self.assertRaisesMessage(CommandError, "setup command has been retired"):
            call_command("provision_first_human", **self.options())
        getpass.assert_not_called()
        self.assertEqual(Identity.objects.count(), 0)

    @patch("getpass.getpass")
    def test_duplicate_command_rolls_back_without_partial_identity(self, getpass):
        with self.assertRaisesMessage(CommandError, "setup command has been retired"):
            call_command("provision_first_human", **self.options())

        getpass.assert_not_called()
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Identity.objects.count(), 0)
        self.assertEqual(IdentityTransition.objects.count(), 0)
        self.assertEqual(OriginatingMembershipProvisioningRequest.objects.count(), 0)
        self.assertEqual(ProfileRole.objects.count(), 0)
        self.assertEqual(ProvisioningReconciliationAttempt.objects.count(), 0)