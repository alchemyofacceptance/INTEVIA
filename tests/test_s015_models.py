from django.db import models
from django.test import SimpleTestCase

from core.models import (
    AuthorityBasis,
    AuthorityPrincipal,
    Circle,
    ContextualRoleAssignment,
    EmergencyAuthorityEnvelope,
    GovernedVisibilityGrant,
    LivingOrganism,
    OrganismMembership,
)


class S015ModelContractTests(SimpleTestCase):
    def test_guardian_23_matrix_is_exactly_eight_models_and_51_columns(self):
        expected = {
            AuthorityPrincipal: 3,
            AuthorityBasis: 13,
            EmergencyAuthorityEnvelope: 9,
            GovernedVisibilityGrant: 11,
            LivingOrganism: 6,
            Circle: 2,
            OrganismMembership: 3,
            ContextualRoleAssignment: 4,
        }
        self.assertEqual(len(expected), 8)
        self.assertEqual(sum(expected.values()), 51)
        for model, count in expected.items():
            self.assertEqual(len(model.S015_IMMUTABLE_FIELDS), count)

    def test_guardian_foreign_keys_are_protect(self):
        fields = (
            AuthorityBasis._meta.get_field("authority_principal"),
            AuthorityBasis._meta.get_field("derivation_root_principal"),
            LivingOrganism._meta.get_field("founding_authority_basis"),
            LivingOrganism._meta.get_field("founder_identity"),
            OrganismMembership._meta.get_field("identity"),
            OrganismMembership._meta.get_field("living_organism"),
            ContextualRoleAssignment._meta.get_field("membership"),
            ContextualRoleAssignment._meta.get_field("role_definition"),
            ContextualRoleAssignment._meta.get_field("circle"),
        )
        self.assertTrue(all(field.remote_field.on_delete is models.PROTECT for field in fields))
