from dataclasses import fields
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlparse

from django.contrib import admin
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from core.models import (
    Event,
    EventAttendance,
    EventAttendanceTransition,
    EventRegistration,
    EventRegistrationTransition,
    Identity,
)
from src.intevia.services.event_read_service import (
    ATTENDANCE_NONE,
    ATTENDANCE_PRESENT,
    ATTENDANCE_WITHDRAWN,
    REGISTRATION_CANCELLED,
    REGISTRATION_CURRENT,
    REGISTRATION_NONE,
    EventDetailPresentation,
    EventHomeSummary,
    EventNotVisible,
    EventReadService,
    visible_event_queryset,
)


NOW = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)


class S009Fixtures:
    @staticmethod
    def identity(username, *, state=Identity.AccessState.ACTIVE):
        user = User.objects.create_user(
            username=username,
            password="governed-test-password",
            is_active=state != Identity.AccessState.DEACTIVATED,
        )
        identity = Identity.objects.create(
            credential=user,
            display_name=username.title(),
            access_state=state,
        )
        return user, identity

    @staticmethod
    def event(owner, suffix, *, created_at=NOW):
        return Event.objects.create(
            event_id=f"event:s009:{suffix}",
            title=f"Event {suffix}",
            description=f"A neutral summary for {suffix}.",
            owner=owner,
            state=Event.State.PUBLISHED,
            created_at=created_at,
        )

    @staticmethod
    def registration(
        event,
        participant,
        suffix,
        *,
        state=EventRegistration.State.REGISTERED,
        origin=EventRegistration.Origin.SELF,
        predecessor=None,
        at=NOW,
    ):
        registration = EventRegistration.objects.create(
            registration_id=f"registration:s009:{suffix}",
            event=event,
            participant=participant,
            state=state,
            origin=origin,
            predecessor=predecessor,
            event_state_at_registration=event.state,
            eligibility_basis_type=EventRegistration.EligibilityBasisType.OTHER,
            eligibility_basis_reference=f"internal:basis:{suffix}",
            eligibility_evaluated_at=at,
            registered_at=at,
        )
        action = (
            EventRegistrationTransition.ActionType.RE_REGISTER
            if predecessor is not None
            else (
                EventRegistrationTransition.ActionType.REGISTER_THIRD_PARTY
                if origin == EventRegistration.Origin.THIRD_PARTY
                else EventRegistrationTransition.ActionType.REGISTER_SELF
            )
        )
        EventRegistrationTransition.objects.create(
            registration=registration,
            from_state="new",
            to_state=state,
            action_type=action,
            actor=participant,
            authority_reference=f"internal:authority:{suffix}",
            authority_event=event,
            authority_participant=participant,
            authority_predecessor=predecessor,
            authority_evaluated_at=at,
            occurred_at=at,
            lineage_reference=f"internal:lineage:{suffix}",
        )
        return registration

    @staticmethod
    def cancel(registration, *, at=NOW):
        previous = registration.transitions.order_by("occurred_at", "pk").last()
        EventRegistration.objects.filter(pk=registration.pk).update(
            state=EventRegistration.State.CANCELLED
        )
        registration.refresh_from_db()
        EventRegistrationTransition.objects.create(
            registration=registration,
            from_state=EventRegistration.State.REGISTERED,
            to_state=EventRegistration.State.CANCELLED,
            action_type=EventRegistrationTransition.ActionType.CANCEL,
            actor=registration.participant,
            authority_reference="internal:authority:cancel",
            authority_event=registration.event,
            authority_participant=registration.participant,
            authority_predecessor=registration.predecessor,
            authority_evaluated_at=at,
            cancellation_basis=EventRegistrationTransition.CancellationBasis.OTHER,
            basis_source=EventRegistrationTransition.BasisSource.ACTOR_RECORDED,
            occurred_at=at,
            lineage_reference=f"internal:lineage:cancel:{registration.pk}",
            previous_transition=previous,
        )
        return registration

    @staticmethod
    def attendance(event, subject, suffix, *, status=EventAttendance.Status.PRESENT):
        attendance = EventAttendance.objects.create(
            attendance_id=f"attendance:s009:{suffix}",
            event=event,
            subject=subject,
            status=EventAttendance.Status.PRESENT,
            observed_at=NOW,
            origin=EventAttendance.Origin.GOVERNED_WALK_IN,
        )
        initial = EventAttendanceTransition.objects.create(
            attendance=attendance,
            sequence=1,
            action=EventAttendanceTransition.Action.RECORD,
            from_status="unrecorded",
            to_status=EventAttendance.Status.PRESENT,
            actor=subject,
            authority_reference=f"internal:authority:{suffix}",
            authority_evaluated_at=NOW,
            origin=attendance.origin,
            resulting_observed_at=NOW,
            idempotency_key=f"key:s009:{suffix}",
            payload_fingerprint="a" * 64,
            recorded_at=NOW,
        )
        if status == EventAttendance.Status.VOIDED:
            EventAttendance.objects.filter(pk=attendance.pk).update(status=status)
            attendance.refresh_from_db()
            EventAttendanceTransition.objects.create(
                attendance=attendance,
                previous_transition=initial,
                sequence=2,
                action=EventAttendanceTransition.Action.VOID,
                from_status=EventAttendance.Status.PRESENT,
                to_status=EventAttendance.Status.VOIDED,
                actor=subject,
                authority_reference=f"internal:authority:{suffix}:void",
                authority_evaluated_at=NOW + timedelta(minutes=1),
                origin=attendance.origin,
                previous_observed_at=NOW,
                resulting_observed_at=NOW,
                idempotency_key=f"key:s009:{suffix}:void",
                payload_fingerprint="b" * 64,
                recorded_at=NOW + timedelta(minutes=1),
            )
        return attendance


class EventPresentationContractTests(S009Fixtures, TestCase):
    def setUp(self):
        _, self.identity_record = self.identity("s009-reader")

    def test_registration_selection_covers_none_current_cancelled_and_successor(self):
        no_record = self.event(self.identity_record, "none")
        self.assertEqual(
            EventReadService.present_event(
                self.identity_record, no_record.event_id
            ).registration.message,
            REGISTRATION_NONE,
        )

        current_event = self.event(self.identity_record, "current")
        self.registration(current_event, self.identity_record, "current")
        self.assertEqual(
            EventReadService.present_event(
                self.identity_record, current_event.event_id
            ).registration.message,
            REGISTRATION_CURRENT,
        )

        cancelled_event = self.event(self.identity_record, "cancelled")
        cancelled = self.registration(
            cancelled_event, self.identity_record, "cancelled"
        )
        self.cancel(cancelled, at=NOW + timedelta(minutes=1))
        self.assertEqual(
            EventReadService.present_event(
                self.identity_record, cancelled_event.event_id
            ).registration.message,
            REGISTRATION_CANCELLED,
        )

        successor = self.registration(
            cancelled_event,
            self.identity_record,
            "successor",
            predecessor=cancelled,
            at=NOW + timedelta(minutes=2),
        )
        self.assertEqual(successor.origin, EventRegistration.Origin.SELF)
        self.assertEqual(
            EventReadService.present_event(
                self.identity_record, cancelled_event.event_id
            ).registration.message,
            REGISTRATION_CURRENT,
        )

    def test_multiple_history_and_third_party_origin_remain_neutral(self):
        event = self.event(self.identity_record, "history")
        first = self.registration(
            event,
            self.identity_record,
            "history-one",
            origin=EventRegistration.Origin.THIRD_PARTY,
        )
        self.cancel(first, at=NOW + timedelta(minutes=1))
        second = self.registration(
            event,
            self.identity_record,
            "history-two",
            predecessor=first,
            at=NOW + timedelta(minutes=2),
        )
        self.cancel(second, at=NOW + timedelta(minutes=3))
        self.registration(
            event,
            self.identity_record,
            "history-three",
            predecessor=second,
            at=NOW + timedelta(minutes=4),
        )

        detail = EventReadService.present_event(self.identity_record, event.event_id)

        self.assertEqual(detail.registration.message, REGISTRATION_CURRENT)
        self.assertNotIn("you registered", detail.registration.message.lower())
        self.assertNotIn("you booked", detail.registration.message.lower())

    def test_ambiguous_registration_lineage_fails_closed(self):
        event = self.event(self.identity_record, "ambiguous")
        first = self.registration(event, self.identity_record, "ambiguous-one")
        self.cancel(first)
        second = self.registration(
            event,
            self.identity_record,
            "ambiguous-two",
            at=NOW + timedelta(minutes=1),
        )
        self.cancel(second, at=NOW + timedelta(minutes=2))

        with self.assertRaises(EventNotVisible):
            EventReadService.present_event(self.identity_record, event.event_id)

    def test_attendance_meanings_are_independent_and_human_facing(self):
        none_event = self.event(self.identity_record, "attendance-none")
        self.assertEqual(
            EventReadService.present_event(
                self.identity_record, none_event.event_id
            ).attendance.message,
            ATTENDANCE_NONE,
        )
        present_event = self.event(self.identity_record, "attendance-present")
        self.attendance(present_event, self.identity_record, "present")
        self.assertEqual(
            EventReadService.present_event(
                self.identity_record, present_event.event_id
            ).attendance.message,
            ATTENDANCE_PRESENT,
        )
        withdrawn_event = self.event(self.identity_record, "attendance-withdrawn")
        self.attendance(
            withdrawn_event,
            self.identity_record,
            "withdrawn",
            status=EventAttendance.Status.VOIDED,
        )
        self.assertEqual(
            EventReadService.present_event(
                self.identity_record, withdrawn_event.event_id
            ).attendance.message,
            ATTENDANCE_WITHDRAWN,
        )

    def test_attendance_history_maps_correction_void_and_reinstatement(self):
        event = self.event(self.identity_record, "attendance-history")
        attendance = self.attendance(event, self.identity_record, "history")
        previous = attendance.transitions.get(sequence=1)
        actions = (
            (EventAttendanceTransition.Action.CORRECT, "present", "present"),
            (EventAttendanceTransition.Action.VOID, "present", "voided"),
            (EventAttendanceTransition.Action.REINSTATE, "voided", "present"),
        )
        for sequence, (action, from_status, to_status) in enumerate(actions, 2):
            previous = EventAttendanceTransition.objects.create(
                attendance=attendance,
                previous_transition=previous,
                sequence=sequence,
                action=action,
                from_status=from_status,
                to_status=to_status,
                actor=self.identity_record,
                authority_reference=f"internal:authority:history:{sequence}",
                authority_evaluated_at=NOW + timedelta(minutes=sequence),
                origin=attendance.origin,
                previous_observed_at=NOW,
                resulting_observed_at=NOW,
                idempotency_key=f"key:s009:history:{sequence}",
                payload_fingerprint=str(sequence) * 64,
                recorded_at=NOW + timedelta(minutes=sequence),
            )

        history = EventReadService.attendance_history(
            self.identity_record, event.event_id
        )
        rendered = " ".join(entry.message for entry in history.entries)
        self.assertEqual(len(history.entries), 4)
        self.assertIn("corrected", rendered)
        self.assertIn("withdrawn", rendered)
        self.assertIn("restored", rendered)
        for forbidden in (
            "voided",
            "unrecorded",
            "competence",
            "Recognition",
            "completion",
            "participation",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_home_has_no_combined_or_action_fields(self):
        forbidden = {
            "journey_status",
            "participation_status",
            "event_status_for_human",
            "completion_status",
            "next_action",
            "eligibility",
        }
        self.assertTrue(forbidden.isdisjoint({field.name for field in fields(EventHomeSummary)}))
        self.assertTrue(
            forbidden.isdisjoint(
                {field.name for field in fields(EventDetailPresentation)}
            )
        )

    def test_all_projection_consumers_call_shared_visibility_boundary(self):
        event = self.event(self.identity_record, "shared-visibility")
        with patch(
            "src.intevia.services.event_read_service.visible_event_queryset",
            wraps=visible_event_queryset,
        ) as shared:
            EventReadService.list_visible(self.identity_record)
            EventReadService.inspect_event(self.identity_record, event.event_id)
            EventReadService.inspect_attendance(self.identity_record, event.event_id)
            EventReadService.home(self.identity_record)
            EventReadService.present_event(self.identity_record, event.event_id)
            EventReadService.registration_history(
                self.identity_record, event.event_id
            )
            EventReadService.attendance_history(self.identity_record, event.event_id)
        self.assertEqual(shared.call_count, 7)


class EventProjectionQueryTests(S009Fixtures, TestCase):
    def setUp(self):
        _, self.identity_record = self.identity("s009-query-reader")

    def capture_home(self):
        with CaptureQueriesContext(connection) as captured:
            EventReadService.home(self.identity_record)
        return captured

    def test_home_query_count_is_constant_bounded_and_order_is_intrinsic(self):
        empty = self.capture_home()
        self.assertLessEqual(len(empty), 4)
        first = self.event(self.identity_record, "query-one", created_at=NOW)
        one = self.capture_home()
        for index in range(2, 7):
            self.event(
                self.identity_record,
                f"query-{index}",
                created_at=NOW + timedelta(minutes=index),
            )
        many = self.capture_home()
        self.assertEqual(len(one), len(many))
        self.assertLessEqual(len(many), 4)
        self.assertEqual(
            EventReadService.home(self.identity_record).events[0].event_id,
            first.event_id,
        )

    def test_overlapping_visibility_deduplicates_without_more_queries(self):
        event = self.event(self.identity_record, "overlap")
        self.registration(event, self.identity_record, "overlap")
        self.attendance(event, self.identity_record, "overlap")
        captured = self.capture_home()
        home = EventReadService.home(self.identity_record)
        self.assertEqual(len(home.events), 1)
        self.assertLessEqual(len(captured), 4)

    def test_reads_issue_no_for_update_and_histories_are_bounded(self):
        event = self.event(self.identity_record, "query-history")
        registration = self.registration(
            event, self.identity_record, "query-history"
        )
        self.attendance(event, self.identity_record, "query-history")
        with CaptureQueriesContext(connection) as captured:
            EventReadService.home(self.identity_record)
            EventReadService.registration_history(
                self.identity_record, event.event_id
            )
            EventReadService.attendance_history(self.identity_record, event.event_id)
        sql = "\n".join(query["sql"].upper() for query in captured)
        self.assertNotIn("FOR UPDATE", sql)
        with CaptureQueriesContext(connection) as registration_queries:
            EventReadService.registration_history(
                self.identity_record, event.event_id
            )
        with CaptureQueriesContext(connection) as attendance_queries:
            EventReadService.attendance_history(self.identity_record, event.event_id)
        self.assertLessEqual(len(registration_queries), 4)
        self.assertLessEqual(len(attendance_queries), 4)
        self.assertIsNotNone(registration.pk)

    def test_absent_histories_remain_bounded(self):
        event = self.event(self.identity_record, "query-empty-history")
        with CaptureQueriesContext(connection) as registration_queries:
            EventReadService.registration_history(
                self.identity_record, event.event_id
            )
        with CaptureQueriesContext(connection) as attendance_queries:
            EventReadService.attendance_history(self.identity_record, event.event_id)
        self.assertLessEqual(len(registration_queries), 4)
        self.assertLessEqual(len(attendance_queries), 4)


class S009HttpGuardianTests(S009Fixtures, TestCase):
    def setUp(self):
        self.user, self.identity_record = self.identity("s009-http-reader")
        self.other_user, self.other = self.identity("s009-http-other")
        self.event_record = self.event(self.identity_record, "http-owned")
        self.registration_record = self.registration(
            self.event_record, self.identity_record, "http-owned"
        )
        self.attendance_record = self.attendance(
            self.event_record, self.identity_record, "http-owned"
        )
        self.client.post(
            reverse("login"),
            {"username": self.user.username, "password": "governed-test-password"},
        )

    def personal_routes(self):
        return (
            reverse("shell"),
            reverse("event-list"),
            reverse("event-detail", args=[self.event_record.event_id]),
            reverse(
                "registration-detail",
                args=[self.event_record.event_id, self.registration_record.registration_id],
            ),
            reverse("registration-history", args=[self.event_record.event_id]),
            reverse("attendance-detail", args=[self.event_record.event_id]),
            reverse("attendance-history", args=[self.event_record.event_id]),
        )

    def test_all_personal_routes_are_get_only_and_cache_protected(self):
        for path in self.personal_routes():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("private", response.headers["Cache-Control"])
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.assertIn("Cookie", response.headers["Vary"])
                self.assertEqual(self.client.post(path).status_code, 405)

    def test_unrelated_and_wrong_parent_routes_are_uniform_404(self):
        hidden = self.event(self.other, "http-hidden")
        hidden_registration = self.registration(
            hidden, self.other, "http-hidden"
        )
        hidden_paths = (
            reverse("event-detail", args=[hidden.event_id]),
            reverse(
                "registration-detail",
                args=[hidden.event_id, hidden_registration.registration_id],
            ),
            reverse("registration-history", args=[hidden.event_id]),
            reverse("attendance-detail", args=[hidden.event_id]),
            reverse("attendance-history", args=[hidden.event_id]),
            reverse(
                "registration-detail",
                args=[hidden.event_id, self.registration_record.registration_id],
            ),
        )
        for path in hidden_paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

        visible_wrong_parent = self.event(self.identity_record, "wrong-parent")
        self.assertEqual(
            self.client.get(
                reverse(
                    "registration-detail",
                    args=[
                        visible_wrong_parent.event_id,
                        self.registration_record.registration_id,
                    ],
                )
            ).status_code,
            404,
        )

    def test_other_humans_records_never_leak_from_owned_event(self):
        other_registration = self.registration(
            self.event_record, self.other, "http-other"
        )
        other_attendance = self.attendance(
            self.event_record, self.other, "http-other"
        )
        detail = self.client.get(
            reverse("event-detail", args=[self.event_record.event_id])
        )
        self.assertNotContains(detail, other_registration.registration_id)
        self.assertNotContains(detail, other_attendance.attendance_id)
        self.assertEqual(
            self.client.get(
                reverse(
                    "registration-detail",
                    args=[self.event_record.event_id, other_registration.registration_id],
                )
            ).status_code,
            404,
        )

    def test_staff_and_superuser_do_not_bypass(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=("is_staff", "is_superuser"))
        hidden = self.event(self.other, "http-staff-hidden")
        for name in (
            "event-detail",
            "registration-history",
            "attendance-detail",
            "attendance-history",
        ):
            self.assertEqual(
                self.client.get(reverse(name, args=[hidden.event_id])).status_code,
                404,
            )

    def test_logout_restriction_and_epoch_invalidation_reveal_no_cached_page(self):
        page = reverse("event-detail", args=[self.event_record.event_id])
        self.assertContains(self.client.get(page), self.event_record.title)
        self.client.post(reverse("logout"))
        response = self.client.get(page)
        self.assertRedirects(response, f"{reverse('login')}?next={page}")
        self.assertNotContains(response, self.event_record.title, status_code=302)

        self.client.post(
            reverse("login"),
            {"username": self.user.username, "password": "governed-test-password"},
        )
        Identity.objects.filter(pk=self.identity_record.pk).update(access_epoch=1)
        for path in self.personal_routes():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    resolve(urlparse(response.url).path).url_name,
                    "login",
                )
                self.assertNotContains(
                    response, self.event_record.title, status_code=302
                )

    def test_restricted_page_is_cache_protected_and_domain_routes_redirect(self):
        restricted_user, _ = self.identity(
            "s009-restricted", state=Identity.AccessState.RESTRICTED
        )
        self.client.post(reverse("logout"))
        self.client.post(
            reverse("login"),
            {
                "username": restricted_user.username,
                "password": "governed-test-password",
            },
        )
        response = self.client.get(reverse("restricted"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.headers["Vary"])
        self.assertRedirects(
            self.client.get(reverse("event-list")), reverse("restricted")
        )
        for path in self.personal_routes():
            if path == reverse("shell"):
                continue
            with self.subTest(path=path):
                self.assertRedirects(self.client.get(path), reverse("restricted"))

    def test_rendered_vocabulary_excludes_raw_and_combined_meaning(self):
        responses = [self.client.get(path) for path in self.personal_routes()]
        body = "\n".join(response.content.decode() for response in responses)
        forbidden = (
            "voided",
            "unrecorded",
            "You booked",
            "You registered",
            "Booking confirmed",
            "journey status",
            "complete/incomplete",
            "progress bar",
            "internal:authority",
            "internal:lineage",
            "internal:basis",
        )
        for value in forbidden:
            self.assertNotIn(value, body)

    def test_accessibility_structure_is_present_on_every_personal_page(self):
        for path in self.personal_routes():
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                self.assertEqual(body.count("<h1"), 1)
                self.assertIn('<a class="skip-link" href="#main-content">', body)
                self.assertIn('<nav aria-label="Primary navigation">', body)
                self.assertIn('<main id="main-content"', body)
                self.assertIn("aria-current=\"page\"", body)
                self.assertIn("<title>", body)

    def test_relevant_core_models_remain_absent_from_admin(self):
        relevant = {
            Event,
            EventRegistration,
            EventRegistrationTransition,
            EventAttendance,
            EventAttendanceTransition,
            Identity,
        }
        self.assertTrue(relevant.isdisjoint(admin.site._registry))

    def test_views_connect_no_domain_command_service(self):
        import inspect

        from core import views

        source = inspect.getsource(views)
        self.assertNotIn("EventRegistrationService", source)
        self.assertNotIn("EventAttendanceService", source)
        self.assertNotIn("EventService(", source)