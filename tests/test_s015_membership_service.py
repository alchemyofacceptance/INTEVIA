from uuid import UUID

from django.test import SimpleTestCase

from core.organism_contract import OrganismAction
from src.intevia.services.organism_membership_service import command_mutex_key


class S015MembershipSubstrateTests(SimpleTestCase):
    def test_mutex_key_is_deterministic_and_domain_separated(self):
        common = {
            "actor_identity_id": UUID(int=1),
            "idempotency_key": "fixture",
        }
        found = command_mutex_key(
            action=OrganismAction.FOUND_LIVING_ORGANISM,
            **common,
        )
        replay = command_mutex_key(
            action=OrganismAction.TRANSITION_MEMBERSHIP,
            **common,
        )
        self.assertEqual(
            found,
            command_mutex_key(
                action=OrganismAction.FOUND_LIVING_ORGANISM,
                **common,
            ),
        )
        self.assertNotEqual(found, replay)
        self.assertGreaterEqual(found, -(2**63))
        self.assertLess(found, 2**63)
