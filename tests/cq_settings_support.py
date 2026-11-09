"""Real setting changes used by DISC2–4 witnesses after collective authority starts."""

from uuid import uuid4

from core import models
from src.intevia.services import cq, cq_reads
from src.intevia.services import discussion as disc


def voters(scope):
    rows = disc._rows(
        """SELECT m.identity_id FROM public.core_organismmembership m
        JOIN public.core_livingorganism lo ON lo.id=m.living_organism_id
        WHERE lo.organism_id=%s
          AND public.disc_member_state(lo.id,m.identity_id,statement_timestamp())='ACTIVE'
          AND public.disc_audience(lo.id,NULL,m.identity_id,'MEMBERS',statement_timestamp())""",
        [scope["organism_uuid"]],
    )
    return list(
        models.Identity.objects.select_related("credential")
        .filter(pk__in=[row["identity_id"] for row in rows])
        .order_by("-pk")
    )


def propose(case, scope, changes, predecessor, *, reporting_effective_from=None):
    proposal = cq.settings_attachment(
        organism_uuid=scope["organism_uuid"],
        predecessor=predecessor,
        changes=changes,
        reporting_effective_from=reporting_effective_from,
    )
    journey, _, topic = case.topic(
        scope, title="Change voting settings", markdown=proposal["text"]
    )
    cq.put_forward(
        **journey,
        request_uuid=uuid4(),
        version_uuid=topic["version"],
        attachment=proposal,
    )
    return journey, topic["version"]


def open_motion(scope, wording, people):
    cq.mark_prepared(**scope, request_uuid=uuid4())
    if cq.phase(**scope)["phase"] == "CLARITY":
        for person in people:
            cq.confirm_clarity(
                **(scope | {"credential": person.credential}),
                request_uuid=uuid4(),
                version_uuid=wording,
            )
        return cq.finish_clarity(**scope, request_uuid=uuid4())
    return cq_reads.vote_status(**scope)["ballot"]


def prepared_ballot(case, scope):
    """Start an ordinary motion under an explicitly disabled Clarity stage."""
    journey, _, topic = case.topic(scope)
    cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=topic["version"])
    cq.mark_prepared(**journey, request_uuid=uuid4())
    ballot = cq_reads.vote_status(**journey)["ballot"]
    case.assertIsNotNone(ballot, "Preparation must open a real ballot when Clarity is off.")
    return journey, ballot


def settle(scope, ballot, people, choices=None):
    choices = ["QUALIFIED"] * len(people) if choices is None else choices
    for person, choice in zip(people, choices, strict=True):
        cq.answer(
            **(scope | {"credential": person.credential}),
            ballot_uuid=ballot,
            request_uuid=uuid4(),
            choice=choice,
        )
    return cq_reads.vote_status(**scope, ballot_uuid=ballot)["result"]


def configure(case, scope, changes, previous=None, *, reporting_effective_from=None):
    if previous is not None and cq.settings(**scope)["settings"]["enabled"]:
        people = voters(scope)
        if len(people) >= 3:
            journey, wording = propose(
                case,
                scope,
                changes,
                previous,
                reporting_effective_from=reporting_effective_from,
            )
            ballot = open_motion(journey, wording, people)
            result = settle(journey, ballot, people)
            case.assertEqual(result["disposition"], "APPLIED", result)
            return cq_reads.implementation_record(
                **journey, result_uuid=result["post_uuid"]
            )["effect"]["settings_version"]
    return cq.change_settings(
        **scope,
        changes=changes,
        predecessor=previous,
        request_uuid=uuid4(),
        reporting_effective_from=reporting_effective_from,
    )
