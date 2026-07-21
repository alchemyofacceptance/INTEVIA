import time
from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.models import (
    Event,
    EventRegistration,
    EventRegistrationEvidenceReference,
    EventRegistrationTransition,
    Identity,
)
from core.session import (
    SESSION_ACCESS_EPOCH_KEY,
    SESSION_IDENTITY_KEY,
    SESSION_STARTED_AT_KEY,
)


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class AuthenticationShellTests(TestCase):
    def identity(self, username="carmien", state=Identity.AccessState.ACTIVE):
        credential = User.objects.create_user(
            username=username,
            password="governed-test-password",
            is_active=state != Identity.AccessState.DEACTIVATED,
        )
        identity = Identity.objects.create(
            credential=credential,
            display_name=username.title(),
            access_state=state,
        )
        return credential, identity

    def login(self, username="carmien"):
        return self.client.post(
            reverse("login"),
            {"username": username, "password": "governed-test-password"},
        )

    def test_login_rotates_and_binds_browser_session(self):
        _, identity = self.identity()
        session = self.client.session
        session["pre_login"] = True
        session.save()
        old_key = session.session_key

        response = self.login()

        self.assertRedirects(response, reverse("shell"))
        session = self.client.session
        self.assertNotEqual(session.session_key, old_key)
        self.assertEqual(session[SESSION_IDENTITY_KEY], str(identity.identity_id))
        self.assertEqual(session[SESSION_ACCESS_EPOCH_KEY], 0)
        self.assertTrue(session.get_expire_at_browser_close())

    def test_failed_login_is_generic(self):
        self.identity()
        response = self.client.post(
            reverse("login"),
            {"username": "carmien", "password": "wrong"},
        )
        self.assertContains(response, "Unable to sign in with those credentials.")
        self.assertNotContains(response, "inactive")

    def test_restricted_identity_reaches_only_restricted_shell(self):
        self.identity(state=Identity.AccessState.RESTRICTED)
        response = self.login()
        self.assertRedirects(response, reverse("restricted"))
        self.assertEqual(self.client.get(reverse("restricted")).status_code, 200)
        self.assertRedirects(
            self.client.get(reverse("event-list")),
            reverse("restricted"),
        )
        self.assertRedirects(
            self.client.get(reverse("shell")),
            reverse("restricted"),
        )

    def test_deactivated_identity_cannot_login(self):
        self.identity(state=Identity.AccessState.DEACTIVATED)
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SESSION_IDENTITY_KEY, self.client.session)

    def test_stale_epoch_and_absolute_age_flush_sessions(self):
        _, identity = self.identity()
        self.login()
        Identity.objects.filter(pk=identity.pk).update(access_epoch=1)
        response = self.client.get(reverse("shell"))
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn(SESSION_IDENTITY_KEY, self.client.session)

        self.login()
        session = self.client.session
        session[SESSION_STARTED_AT_KEY] = int(time.time()) - (8 * 60 * 60)
        session.save()
        response = self.client.get(reverse("shell"))
        self.assertRedirects(response, reverse("login"))

    def test_logout_is_post_only_and_flushes_session(self):
        self.identity()
        self.login()
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn(SESSION_IDENTITY_KEY, self.client.session)

    def test_logout_requires_csrf(self):
        self.identity()
        client = Client(enforce_csrf_checks=True)
        login_page = client.get(reverse("login"))
        csrf_token = login_page.cookies["csrftoken"].value
        client.post(
            reverse("login"),
            {
                "username": "carmien",
                "password": "governed-test-password",
                "csrfmiddlewaretoken": csrf_token,
            },
        )
        self.assertEqual(client.post(reverse("logout")).status_code, 403)


class EventReadShellTests(TestCase):
    def setUp(self):
        self.user, self.identity = self.make_identity("reader")
        self.other_user, self.other = self.make_identity("other")
        self.client.post(
            reverse("login"),
            {"username": "reader", "password": "governed-test-password"},
        )

    @staticmethod
    def make_identity(username):
        user = User.objects.create_user(
            username=username,
            password="governed-test-password",
        )
        identity = Identity.objects.create(
            credential=user,
            display_name=username.title(),
            access_state=Identity.AccessState.ACTIVE,
        )
        return user, identity

    @staticmethod
    def event(owner, event_id):
        return Event.objects.create(
            event_id=event_id,
            title=event_id,
            description="Bounded Event description",
            owner=owner,
            state=Event.State.PUBLISHED,
            created_at=NOW,
        )

    @staticmethod
    def registration(event, participant, registration_id):
        registration = EventRegistration.objects.create(
            registration_id=registration_id,
            event=event,
            participant=participant,
            state=EventRegistration.State.REGISTERED,
            origin=EventRegistration.Origin.SELF,
            event_state_at_registration=event.state,
            eligibility_basis_type=EventRegistration.EligibilityBasisType.OTHER,
            eligibility_basis_reference="internal:basis",
            eligibility_evaluated_at=NOW,
            registered_at=NOW,
        )
        transition = EventRegistrationTransition.objects.create(
            registration=registration,
            from_state="new",
            to_state=registration.state,
            action_type=EventRegistrationTransition.ActionType.REGISTER_SELF,
            actor=participant,
            authority_reference="internal:authority:secret",
            authority_event=event,
            authority_participant=participant,
            authority_evaluated_at=NOW,
            cancellation_basis="",
            basis_source="",
            occurred_at=NOW,
            lineage_reference=f"lineage:{registration_id}",
        )
        EventRegistrationEvidenceReference.objects.create(
            transition=transition,
            reference="internal:evidence:secret",
            reference_type="receipt",
            supplied_by=participant,
            occurred_at=NOW,
        )
        return registration

    def test_default_deny_hides_unrelated_event_existence(self):
        hidden = self.event(self.other, "event:hidden")
        response = self.client.get(reverse("event-list"))
        self.assertNotContains(response, hidden.title)
        self.assertEqual(
            self.client.get(reverse("event-detail", args=[hidden.event_id])).status_code,
            404,
        )

    def test_staff_and_superuser_do_not_bypass_event_visibility(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=("is_staff", "is_superuser"))
        hidden = self.event(self.other, "event:staff-hidden")
        self.assertNotContains(self.client.get(reverse("event-list")), hidden.title)
        self.assertEqual(
            self.client.get(reverse("event-detail", args=[hidden.event_id])).status_code,
            404,
        )

    def test_owner_sees_event_but_not_another_identity_registration(self):
        event = self.event(self.identity, "event:owned")
        registration = self.registration(event, self.other, "registration:other")
        response = self.client.get(reverse("event-detail", args=[event.event_id]))
        self.assertContains(response, event.title)
        self.assertNotContains(response, registration.registration_id)
        guessed = self.client.get(
            reverse(
                "registration-detail",
                args=[event.event_id, registration.registration_id],
            )
        )
        self.assertEqual(guessed.status_code, 404)

    def test_own_registration_projects_allowlisted_lineage_only(self):
        event = self.event(self.other, "event:registered")
        registration = self.registration(
            event,
            self.identity,
            "registration:reader",
        )
        response = self.client.get(
            reverse(
                "registration-detail",
                args=[event.event_id, registration.registration_id],
            )
        )
        self.assertContains(response, "Registration lineage")
        self.assertContains(response, "Evidence type: receipt")
        self.assertNotContains(response, "internal:authority:secret")
        self.assertNotContains(response, "internal:evidence:secret")
        self.assertNotContains(response, "internal:basis")

    def test_event_routes_are_read_only(self):
        event = self.event(self.identity, "event:read-only")
        self.assertEqual(self.client.post(reverse("event-list")).status_code, 405)
        self.assertEqual(
            self.client.post(reverse("event-detail", args=[event.event_id])).status_code,
            405,
        )