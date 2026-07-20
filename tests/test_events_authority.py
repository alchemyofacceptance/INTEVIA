from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Event, Profile, ProfileRole, Role
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.event_service import EventService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


class EventAuthorityTests(TestCase):
    def identity(self, name, *, with_role=True):
        user = User.objects.create_user(username=name)
        profile = Profile.objects.create(user=user)
        if with_role:
            role, _ = Role.objects.get_or_create(name="Event participant")
            ProfileRole.objects.create(profile=profile, role=role)
        return user, profile

    def test_existing_capability_contract_authorises_event_creation(self):
        user, profile = self.identity("event-authorised")
        service = EventService(authority=ContributionAuthority(Capability()))
        event = service.create_event(
            identity=user,
            event_id="event:authorised",
            title="Authorised event",
            evidence_reference="evidence:authorised",
            occurred_at=NOW,
        )
        self.assertEqual(event.owner, profile)
        self.assertEqual(
            event.transitions.get().authority_reference,
            f"authority:{profile.pk}:create_event",
        )

    def test_denial_leaves_no_partial_event_records(self):
        user, _ = self.identity("event-denied")
        service = EventService(
            authority=ContributionAuthority(Capability({"create_event"}))
        )
        with self.assertRaises(NotAuthorised):
            service.create_event(
                identity=user,
                event_id="event:denied",
                title="Denied event",
                evidence_reference="evidence:denied",
                occurred_at=NOW,
            )
        self.assertFalse(Event.objects.filter(event_id="event:denied").exists())

    def test_role_is_required(self):
        user, _ = self.identity("event-no-role", with_role=False)
        service = EventService(authority=ContributionAuthority(Capability()))
        with self.assertRaises(NotAuthorised):
            service.create_event(
                identity=user,
                event_id="event:no-role",
                title="No role",
                evidence_reference="evidence:no-role",
                occurred_at=NOW,
            )
