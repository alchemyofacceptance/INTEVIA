"""Condition witnesses use the production v1.0 authority reader and the real command routes."""

from dataclasses import fields, replace
from unittest.mock import patch
from uuid import UUID, uuid4

from django.test import TransactionTestCase

from core import models
from src.intevia.services import organism_conditions as conditions
from src.intevia.services.organism_condition_rules import (
    MENTORSHIP_RULE,
    PROBATION_RULE,
    decode_reference,
)
from src.intevia.services.organism_command_support import S015CommandReplayMismatch
from src.intevia.services.organism_authority import S015AuthorityDenied
from src.intevia.services.organism_membership_periods import bound_periods
from src.intevia.services.organism_membership_service import (
    LeaveOrganismCommand,
    OrganismMembershipService,
)
from tests import test_c1b_departure as departure
from tests import test_sr_office_actions as office
from tests.s015_0021_support import database_now
from tests.test_c1_rejoin import _attempt


class ConditionTests(TransactionTestCase):
    def setUp(self):
        self.fx = office.SROfficeActionTests()
        self.fx._start_organism()
        self.subject = self.fx._member()
        self.mentor = self.fx._member()
        self.second = self.fx._member()
        self.service = conditions.MembershipConditionService(
            authority=self.fx.authority, clock=database_now
        )

    def response(self, offer, mentor, *, decline=False):
        cls = (
            conditions.DeclineMentorOfferCommand
            if decline
            else conditions.AcceptMentorOfferCommand
        )
        at = database_now()
        return cls(
            credential=mentor.identity.credential,
            organism_uuid=self.fx.organism.organism_id,
            condition_uuid=UUID(offer.condition_uuid),
            mentor_start=mentor.head_transition.event_uuid,
            occurred_at=at,
            effective_at=at,
            idempotency_key=uuid4().hex,
            request_reference=uuid4().hex,
        )

    def leave(self, member):
        at = database_now()
        return OrganismMembershipService(
            authority=self.fx.authority, clock=database_now
        ).leave_organism(
            LeaveOrganismCommand(
                credential=member.identity.credential,
                membership_uuid=member.membership_uuid,
                confirmed=True,
                reason_class="VOLUNTARY",
                occurred_at=at,
                effective_at=at,
                request_reference=uuid4().hex,
                idempotency_key=uuid4().hex,
            )
        )

    def command(self, **changes):
        at = database_now()
        cmd = conditions.ConditionCommand(
            credential=self.fx.actor.credential,
            organism_uuid=self.fx.organism.organism_id,
            subject_uuid=self.subject.membership_uuid,
            subject_start=self.subject.head_transition.event_uuid,
            mentor_uuid=self.mentor.membership_uuid,
            mentor_start=self.mentor.head_transition.event_uuid,
            rule_uuid=PROBATION_RULE
            if changes.get("kind") == "PROBATION"
            else MENTORSHIP_RULE,
            rule_version=1,
            capacity="LO_COORDINATOR",
            kind="MENTORSHIP",
            evidence_reference="fixture:assignment",
            request_reference=uuid4().hex,
            idempotency_key=uuid4().hex,
            occurred_at=at,
            effective_at=at,
        )
        return replace(cmd, **changes)

    def test_replacement_preserves_periods_and_original_mentor(self):
        cmd = self.command()
        denied, _ = _attempt(
            lambda: self.service.record(
                replace(cmd, credential=self.second.identity.credential)
            )
        )
        self.assertIs(
            type(denied),
            S015AuthorityDenied,
            "Only an effective Coordinator may record a mentor offer.",
        )
        first = self.service.record(cmd)
        decision = models.AuthorityDecision.objects.get(
            decision_reference=first.authority_decision_reference
        )
        reference = next(
            fact["reference"]
            for fact in decision.facts_read
            if fact["relation"] == "condition-qualification"
        )
        decoded = decode_reference(reference)
        office = models.ContextualRoleAssignment.objects.get(
            membership=self.fx.founder_member,
            role_definition__code="COORDINATOR",
            state="ACTIVE",
        )
        self.assertEqual(
            (
                decoded["rule_uuid"],
                decoded["assignment_uuid"],
                decoded["head_event_uuid"],
                decoded["subject_start"],
                decoded["mentor_start"],
            ),
            (
                MENTORSHIP_RULE,
                office.assignment_uuid,
                office.head_state_event.event_uuid,
                cmd.subject_start,
                cmd.mentor_start,
            ),
            "The recording reference must identify the rule, recording office and bound periods.",
        )
        self.service.accept_offer(self.response(first, self.mentor))
        second = self.command(
            replaces=UUID(first.condition_uuid),
            mentor_uuid=self.second.membership_uuid,
            mentor_start=self.second.head_transition.event_uuid,
        )
        error, outcome = _attempt(lambda: self.service.record(second))
        self.assertIsNone(
            error,
            f"Replacing the named mentor under its exact rule must succeed. Got {type(error).__name__}.",
        )
        old = models.MembershipCondition.objects.get(
            condition_uuid=first.condition_uuid
        )
        self.assertEqual(
            old.head_state_event.resulting_state,
            "ACTIVE",
            "A replacement offer must leave the earlier mentoring condition effective.",
        )
        accepted, _ = _attempt(
            lambda: self.service.accept_offer(self.response(outcome, self.second))
        )
        self.assertIsNone(
            accepted, "The offered replacement mentor must be able to accept."
        )
        old = models.MembershipCondition.objects.get(
            condition_uuid=first.condition_uuid
        )
        new = models.MembershipCondition.objects.get(
            condition_uuid=outcome.condition_uuid
        )
        self.assertEqual(
            (
                old.mentor_membership_id,
                old.head_state_event.resulting_state,
                old.head_state_event.evidence_reference,
                new.mentor_membership_id,
            ),
            (
                self.mentor.pk,
                "SUPERSEDED",
                f"condition:{new.condition_uuid}",
                self.second.pk,
            ),
            "Replacement must link a fresh condition and preserve the original mentor.",
        )
        self.assertEqual(
            bound_periods(old, "default"),
            (cmd.subject_start, cmd.mentor_start),
            "The original commitment must still resolve to both original periods.",
        )
        self.assertEqual(
            self.service.record(second),
            outcome,
            "A replacement retry must return its existing receipt.",
        )
        error, _ = _attempt(
            lambda: self.service.record(self.command(subject_start=uuid4()))
        )
        self.assertIs(
            type(error),
            conditions.ConditionRefused,
            "A different subject period cannot inherit this condition.",
        )

    def test_departing_mentor_leaves_visible_unresolved_condition(self):
        result = self.service.record(self.command())
        self.service.accept_offer(self.response(result, self.mentor))
        error, _ = _attempt(lambda: self.leave(self.mentor))
        self.assertIsNone(
            error, f"The mentor should be able to depart. Got {type(error).__name__}."
        )
        self.assertEqual(
            self.service.own_status(
                credential=self.subject.identity.credential,
                condition_uuid=result.condition_uuid,
            ),
            "MENTOR_UNRESOLVED",
            "The subject must see that their mentor has left.",
        )
        self.mentor.refresh_from_db()
        old = models.MembershipCondition.objects.get(
            condition_uuid=result.condition_uuid
        )
        self.assertEqual(
            old.head_state_event.evidence_reference,
            f"membership-ending:{self.mentor.head_transition.event_uuid}",
            "The unresolved condition must identify the exact departure, preserving its original mentor.",
        )

    def test_probation_records_terms_but_refuses_coordinator_and_cq(self):
        departure.DepartureTests._state(
            self,
            self.subject,
            "core_organismmembership",
            "core_organismmembershiptransition",
            "PROBATIONARY",
        )
        with self.assertRaisesRegex(
            conditions.ConditionRefused, "cannot name a mentor"
        ):
            conditions.condition_shape(
                replace(self.command(), kind="PROBATION", rule_uuid=PROBATION_RULE)
            )
        denied, _ = _attempt(
            lambda: self.service.record(
                self.command(kind="PROBATION", mentor_uuid=None, mentor_start=None)
            )
        )
        self.assertIs(
            type(denied),
            S015AuthorityDenied,
            "Probation needs a qualifying entitlement.",
        )
        self.fx._custom_entitlement()
        result = self.service.record(
            self.command(kind="PROBATION", mentor_uuid=None, mentor_start=None)
        )
        decision = models.AuthorityDecision.objects.get(
            decision_reference=result.authority_decision_reference
        )
        self.assertEqual(
            (decision.target["rule_uuid"], decision.target["rule_version"]),
            (str(PROBATION_RULE), 1),
            "Probation must identify the exact ruled terms in its durable decision.",
        )
        replacement = self.service.record(
            self.command(
                kind="PROBATION",
                mentor_uuid=None,
                mentor_start=None,
                replaces=UUID(result.condition_uuid),
            )
        )
        old = models.MembershipCondition.objects.get(
            condition_uuid=result.condition_uuid
        )
        new = models.MembershipCondition.objects.get(
            condition_uuid=replacement.condition_uuid
        )
        self.assertEqual(
            (
                old.head_state_event.resulting_state,
                old.head_state_event.evidence_reference,
                new.head_state_event.resulting_state,
            ),
            ("SUPERSEDED", f"condition:{new.condition_uuid}", "ACTIVE"),
            "Replacement probation terms must take effect and supersede the terms they name.",
        )
        coordinator = models.OrganismRoleDefinition.objects.get(
            living_organism=self.fx.organism, code="COORDINATOR", active=True
        )
        error, _ = _attempt(
            lambda: self.fx.roles.propose_assignment(
                self.fx._proposal_command(coordinator, self.subject)
            )
        )
        self.assertIs(
            type(error),
            S015AuthorityDenied,
            "The production office route must refuse a probationer's Coordinator nomination.",
        )
        error, _ = _attempt(
            lambda: self.service.record(
                self.command(
                    kind="PROBATION",
                    mentor_uuid=None,
                    mentor_start=None,
                    cq_outcome_uuid=uuid4(),
                )
            )
        )
        self.assertIs(
            type(error),
            conditions.ConditionRefused,
            "A CQ reference cannot complete probation before basic CQ exists.",
        )
        self.assertIn(
            "basic CQ",
            str(error),
            "The refusal must identify the missing outcome service.",
        )
        self.subject.refresh_from_db()
        self.assertEqual(
            self.subject.state,
            "PROBATIONARY",
            "A refused probation outcome must preserve membership.",
        )

    def test_mail_refusal_is_saved_without_reversing_assignment(self):
        # Supply a temporary local NOTICE registration so EM-a reaches its lock
        # boundary with a real origin. No network or product registration.
        from email_foundation import service as mail
        from types import SimpleNamespace

        template = SimpleNamespace(
            category="NOTICE", path_prefix="/membership-conditions/"
        )
        with (
            patch.object(
                conditions,
                "templates",
                return_value={("c1.mentor_assignment", 1): template},
            ),
            patch.object(
                mail, "templates", return_value={("c1.mentor_assignment", 1): template}
            ),
        ):
            error, result = _attempt(lambda: self.service.record(self.command()))
        self.assertIsNone(
            error,
            f"A recipient's mail refusal must leave the governed assignment committed. Got {type(error).__name__}.",
        )
        self.assertEqual(
            result.notice,
            "REFUSED",
            "The originating receipt must distinguish a refused notice from a queued notice.",
        )
        self.assertTrue(
            models.MembershipCondition.objects.filter(
                condition_uuid=result.condition_uuid,
                head_state_event__resulting_state="OFFERED",
            ).exists(),
            "Refusal by email must preserve the governed condition.",
        )

    def test_offer_acceptance_requires_the_named_mentor_and_replays(self):
        offer = self.service.record(self.command())
        offered = UUID(offer.condition_uuid)
        mentor = self.mentor.identity.credential
        self.assertEqual(
            self.service.own_status(
                credential=self.subject.identity.credential, condition_uuid=offered
            ),
            "OFFERED",
            "Recording must expose a pending offer to the subject.",
        )
        self.assertIn(
            offered,
            self.service.own_open_offers(credential=mentor),
            "The mentor must discover their own offer.",
        )
        self.assertNotIn(
            offered,
            self.service.own_open_offers(credential=self.second.identity.credential),
            "Another member must not discover someone else's offer.",
        )
        cmd = self.response(offer, self.mentor)
        denied, _ = _attempt(
            lambda: self.service.accept_offer(
                replace(cmd, credential=self.second.identity.credential)
            )
        )
        self.assertIs(
            type(denied),
            conditions.ConditionRefused,
            "Another member cannot accept the offer.",
        )
        error, result = _attempt(lambda: self.service.accept_offer(cmd))
        self.assertIsNone(error, "The named current mentor must be able to accept.")
        self.assertEqual(
            self.service.own_status(credential=mentor, condition_uuid=offered),
            "ACTIVE",
            "The accepted relationship must become active.",
        )
        self.assertEqual(
            result,
            self.service.accept_offer(cmd),
            "Exact acceptance replay must return its receipt.",
        )
        changed = conditions.DeclineMentorOfferCommand(
            **{field.name: getattr(cmd, field.name) for field in fields(cmd)}
        )
        denied, _ = _attempt(lambda: self.service.decline_offer(changed))
        self.assertIs(
            type(denied),
            S015CommandReplayMismatch,
            "Decline cannot reuse the acceptance key.",
        )

    def test_decline_closes_only_the_replacement_offer(self):
        first = self.service.record(self.command())
        self.service.accept_offer(self.response(first, self.mentor))
        offer = self.service.record(
            self.command(
                replaces=UUID(first.condition_uuid),
                mentor_uuid=self.second.membership_uuid,
                mentor_start=self.second.head_transition.event_uuid,
            )
        )
        cmd = self.response(offer, self.second, decline=True)
        error, result = _attempt(lambda: self.service.decline_offer(cmd))
        self.assertIsNone(
            error, "The named replacement mentor must be able to decline."
        )
        subject = self.subject.identity.credential
        self.assertEqual(
            self.service.own_status(
                credential=subject, condition_uuid=offer.condition_uuid
            ),
            "DECLINED",
            "Declining must close only the offer.",
        )
        self.assertEqual(
            self.service.own_status(
                credential=subject, condition_uuid=first.condition_uuid
            ),
            "ACTIVE",
            "The earlier mentor must remain in place.",
        )
        self.assertEqual(
            self.service.decline_offer(cmd),
            result,
            "A declined offer must replay its original receipt.",
        )

    def test_departure_of_either_party_closes_the_offer(self):
        for party in ("subject", "mentor"):
            with self.subTest(party=party):
                subject, mentor = self.fx._member(), self.fx._member()
                offer = self.service.record(
                    self.command(
                        subject_uuid=subject.membership_uuid,
                        subject_start=subject.head_transition.event_uuid,
                        mentor_uuid=mentor.membership_uuid,
                        mentor_start=mentor.head_transition.event_uuid,
                    )
                )
                member = subject if party == "subject" else mentor
                error, _ = _attempt(lambda: self.leave(member))
                self.assertIsNone(
                    error, "Departure must close an outstanding offer in the same act."
                )
                member.refresh_from_db()
                row = models.MembershipCondition.objects.get(
                    condition_uuid=offer.condition_uuid
                )
                self.assertEqual(
                    (
                        row.head_state_event.resulting_state,
                        row.head_state_event.evidence_reference,
                    ),
                    (
                        "OFFER_CLOSED",
                        f"membership-ending:{member.head_transition.event_uuid}",
                    ),
                    "An open offer must close with the departing party's exact membership event.",
                )
