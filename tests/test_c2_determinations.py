"""S findings on an owned database with separate, recorded instrument evidence."""
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from django.db import connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import scoped_determinations as service
from src.intevia.services.organism_coverage import office_scope
from tests import s015_0021_support as fixtures
from tests.test_c1_rejoin import _attempt


class ScopedFindingTests(TransactionTestCase):
    def setUp(self):
        with transaction.atomic(), connection.cursor() as cursor:
            ids = fixtures.found_nonroot(cursor, uuid4().hex)
            self.tested = ids["identity"]
            self.verifier = fixtures.seed_identity(cursor, uuid4().hex)
            self.issuer = fixtures.seed_identity(cursor, uuid4().hex)
        self.subject = models.AuthorityBasis.objects.get(pk=ids["basis"])
        self.scope = office_scope(models.LivingOrganism.objects.get(pk=ids["lo"]),
                                  models.ContextualRoleAssignment.objects.get(pk=ids["assignments"]["COORDINATOR"]), self.subject)
        self.basis = self.fresh_basis()
        now = fixtures.database_now()
        self.end = now + timedelta(days=2)
        self.grant = self.mandate(now)

    def fresh_basis(self):
        with transaction.atomic(), connection.cursor() as cursor:
            _, pk = fixtures.seed_authority(cursor, uuid4().hex, self.issuer, scope_fingerprint=self.scope.fingerprint())
        return models.AuthorityBasis.objects.get(pk=pk)

    def mandate(self, now):
        # Input from the instrument owner. S may consume but cannot issue this grant.
        return models.GovernedDetermination.objects.create(
            determination_type=service.Type.CONSTITUTIONAL_AUTHORITY_QUALIFICATION,
            determiner_id=self.issuer, tested_actor_id=self.verifier,
            relied_upon_authority_principal=self.basis.authority_principal,
            evidence_reference=self.basis.instrument_reference,
            method_reference="S:instrument:1|" + service.canonical_timestamp(self.end),
            scope_fingerprint=service.mandate_scope(self.scope, self.verifier, self.basis),
            independence_state="INDEPENDENT", result="QUALIFIED",
            occurred_at=now, effective_at=now, received_at=now,
        )

    def record(self, **overrides):
        now = fixtures.database_now()
        args = dict(alias="default", scope=self.scope, mandate_id=self.grant.determination_uuid,
                    credential=models.Identity.objects.get(pk=self.verifier).credential, verifier_basis=self.basis.basis_uuid,
                    result="QUALIFIED", evidence="fixture:office-reviewed", at=now,
                    received=now, until=self.end)
        return service.record(**(args | overrides))

    def test_target_and_hard_expiry(self):
        finding = self.record()
        known = fixtures.database_now()
        self.assertEqual(service.qualified("default", self.scope, known, known).pk, finding.pk,
                         "Coverage should receive the recorded finding for this exact office.")
        for scope, at in ((replace(self.scope, target=uuid4()), known), (self.scope, self.end)):
            with self.assertRaises(service.QualificationRefused,
                                   msg="Another office or the expiry instant must refuse the office finding."):
                service.qualified("default", scope, at, known)

    def test_derived_authority(self):
        models.AuthorityDerivationEdge.objects.create(
            principal=self.basis.authority_principal, root_principal=self.subject.authority_principal,
            authority_reference="fixture:source", evidence_reference="fixture:derivation")
        err, _ = _attempt(self.record)
        self.assertIs(type(err), service.QualificationRefused,
                      "The shared-root qualifier must refuse with the governed finding refusal.")
        self.assertIn("same source", str(err),
                      "The refusal must explain why a second instrument derived from the holder cannot be independent.")

    def test_beneficiary_cannot_determine(self):
        self.scope = replace(self.scope, beneficiary_id=self.verifier)
        self.basis = self.fresh_basis()
        self.grant = self.mandate(fixtures.database_now())
        err, _ = _attempt(self.record)
        self.assertIs(type(err), service.QualificationRefused,
                      "The verifier's own benefit must receive the governed finding refusal.")
        self.assertIn("benefits", str(err),
                      "The verifier must learn that this benefit prevents self-certification.")

    def test_contested_finding_preserves_history(self):
        finding = self.record()
        first = fixtures.database_now()
        service.invalidate(alias="default", scope=self.scope, finding_id=finding.determination_uuid,
                           mandate_id=self.grant.determination_uuid,
                           credential=models.Identity.objects.get(pk=self.verifier).credential,
                           verifier_basis=self.basis.basis_uuid, evidence="fixture:contested-office-evidence")
        now = fixtures.database_now()
        with self.assertRaisesRegex(service.QualificationRefused, "contest",
                                    msg="Coverage must hold once the finding is contested."):
            service.qualified("default", self.scope, now, now)
        self.assertEqual(service.qualified("default", self.scope, first, first).pk, finding.pk,
                         "The contest must preserve the earlier decision at its original knowledge cut.")

        old_basis = self.basis.basis_uuid
        with transaction.atomic(), connection.cursor() as cursor:
            self.verifier = fixtures.seed_identity(cursor, uuid4().hex)
        self.basis = self.fresh_basis()
        self.grant = self.mandate(fixtures.database_now())
        renewed = self.record()
        now = fixtures.database_now()
        self.assertEqual(service.qualified("default", self.scope, now, now).pk, renewed.pk,
                         "The replacement verifier must use the fresh finding on their own mandate.")
        finding.refresh_from_db()
        self.assertIn(str(old_basis), finding.method_reference,
                      "The original finding must retain its verifier's basis after the substitute acts.")
