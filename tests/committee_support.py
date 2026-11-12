"""Small real-CQ fixtures shared with the election-mandate suite."""

import json
from dataclasses import replace
from uuid import uuid4

from core import models
from src.intevia.services import circle_commands as circles, cq, committees
from src.intevia.services import discussion as disc
from tests import test_law_acts as law


def published(founder, contents):
    doc = law.Document(founder)
    version = doc.service.create_successor_version(
        identity=doc.author,
        resource_id=doc.name,
        content=json.dumps(contents, sort_keys=True),
        evidence_reference="fixture:committee:" + uuid4().hex,
    )
    doc.transition("publish_library_resource")
    return doc, version


def instrument(lo, doc, version, cls="POLICY", **changes):
    spec = replace(
        law.adoption(lo, doc, cls), version=version.version_number, **changes
    ).content()
    return {"kind": "LAW", "text": version.content, "spec": spec}


def mandate(
    lo,
    founder,
    circle,
    level,
    *,
    areas=None,
    terms=None,
    chair=None,
    publisher=published,
    policy_fields=None,
    **attributes,
):
    reference, version = publisher(
        founder,
        {"areas": areas if areas is not None else {"reserves": {"POLICY": "ADOPT"}}}
        | (terms or {}),
    )
    holder = models.ContextualRoleAssignment.objects.get(
        circle=circle,
        membership__identity=chair or founder,
        role_definition__code="COORDINATOR",
    )
    body = {
        "circle": str(circle.circle_uuid),
        "chair": str(holder.assignment_uuid),
        "kind": "STANDING",
        "level": level,
        "terms": {
            "binding": str(uuid4()),
            "resource": reference.name,
            "version": version.version_number,
        },
    } | attributes
    doc, law_version = publisher(founder, {"committee": body} | (policy_fields or {}))
    return instrument(lo, doc, law_version), body


def join(scope, circle, person):
    member = models.OrganismMembership.objects.get(
        identity=person, living_organism__organism_id=scope["organism_uuid"]
    )
    local = scope | {"circle_uuid": circle.circle_uuid}
    req = circles.request_place(
        **(local | {"credential": person.credential}),
        membership_uuid=member.membership_uuid,
        request_uuid=uuid4(),
        consent="I request this committee place.",
    )
    return circles.admit(**local, request_uuid=req.request_uuid)


def start(case, scope, att):
    journey, _, post = case.topic(
        scope, title="Decide the precise committee act", markdown=att["text"]
    )
    cq.put_forward(
        **journey, request_uuid=uuid4(), version_uuid=post["version"], attachment=att
    )
    return journey


def step(scope, mandate_uuid, action, **fields):
    return committees.attachment(
        organism_uuid=scope["organism_uuid"], mandate=mandate_uuid, act=action, **fields
    )


def first_version(founder, contents):
    """Publish the exact fixture text at creation; no dummy version or successor."""
    document = law.Document(founder, content=json.dumps(contents, sort_keys=True))
    return document, document.first


def formation_board(case, *, offering, seats=3, recognise_founder=None, activation_probe=None, charter_fields=None, after_article=None):
    """Seat the actual voters while GOVERNANCE is dormant, then activate the Board."""
    case.topic_category_ids = {}
    lo, founder = law.ordinary_birth(
        offering_choice=offering,
        board_seats=seats,
        seat_founder=recognise_founder is None,
        before_birthing=recognise_founder,
        board_minimum=None if recognise_founder is None else law.FIXTURE_BOARD_MINIMUM,
        birth_chair_choice=False if activation_probe is None else None,
    )
    if recognise_founder is None:
        charter, version = first_version(
            founder, law.board_makeup_fields(law.FIXTURE_BOARD_MINIMUM)
            | {"committees": {"chair_board_member": False}} | (charter_fields or {})
        )
        article = replace(
            law.adoption(lo, charter, "ARTICLE"), version=version.version_number
        )
        receipt = law.law.OrganismLawService().adopt_law(law.command(founder, article))
        case.assertEqual(receipt.status, "ADOPT")
        if after_article is not None:
            after_article(lo, founder, article.proposal, version.pk)
    extra = []
    if seats > 3:
        from tests.cq_authority_support import seat_trustee

        governance = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        extra = [
            seat_trustee(lo, founder, governance, seat) for seat in range(3, seats)
        ]
    lo, general, board, voters = case.activate_board(lo, founder, activation_probe=activation_probe)
    return lo, general, board, voters + extra


def decide_instrument(case, scope, attachment, voters):
    """Commit each public command; read only the ballot key and linked result needed here."""
    journey = start(case, scope, attachment)
    thread = journey["thread_uuid"]
    cq.mark_prepared(**journey, request_uuid=uuid4())
    opened = disc._rows(
        """SELECT act_uuid FROM public.core_cqact WHERE thread_uuid=%s
           AND operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE')""",
        [thread],
    )
    case.assertEqual(len(opened), 1, "The trustees need one real open ballot.")
    ballot = opened[0]["act_uuid"]
    for voter in voters:
        cq.answer(
            **(journey | {"credential": voter.credential}),
            ballot_uuid=ballot,
            choice="QUALIFIED",
            request_uuid=uuid4(),
        )
    receipts = disc._rows(
        """SELECT i.act_uuid AS implementation,s.act_uuid AS result,i.facts->'domain_plan' AS plan
           FROM public.core_cqact s JOIN public.core_cqact i
           ON i.operation='IMPLEMENT' AND i.request->>'result'=s.act_uuid::text
           AND i.thread_uuid=s.thread_uuid AND i.board_uuid=s.board_uuid
           WHERE s.thread_uuid=%s AND s.request->>'ballot'=%s AND s.operation='SETTLE'
           AND s.facts->>'disposition'='APPLIED'""",
        [thread, str(ballot)],
    )
    case.assertEqual(len(receipts), 1, "One settlement must apply this exact proposal.")
    return receipts[0]


def assigned_activity(founder, circle, binding, *, worker=None, with_service=False, board_place=None):
    """Create the real published Service and the chair’s assigned activity.

    With ``board_place``, the activity is the Board's own, attached to that Circle as its Board and
    assigned on the basis of that Board place, as Board work is.
    """
    from dataclasses import asdict
    from src.intevia.services import service_activity_service as activities
    from src.intevia.services.service_service import GovernedService
    from src.intevia.services.service_authority import (
        ServiceCommandAuthority,
        ServiceCommandAuthorityResponse,
    )
    from tests.test_law_records import LibraryFixtureCapability

    from tests import s015_0021_support as support

    class ActivityGrant:
        def authorise(self, *, request):
            return ServiceCommandAuthorityResponse(
                **asdict(request), authority_reference="fixture:reserve-work"
            )

    source = GovernedService(
        authority=law.ContributionAuthority(LibraryFixtureCapability())
    )
    catalog = source.create_service(
        identity=founder.credential,
        service_id="reserves-" + uuid4().hex,
        capability_purpose="Review reserves",
        domain_intent="Service",
        evidence_reference="fixture:reserves",
    )
    source.transition_service(
        identity=founder.credential,
        service_id=catalog.service_id,
        command="publish_service",
        evidence_reference="fixture:publish",
    )
    service = activities.ServiceActivityService(
        authority=ServiceCommandAuthority(provider=ActivityGrant()),
        clock=support.database_now,
    )
    activity_id = uuid4()

    def command():
        return dict(
            credential=founder.credential,
            request_reference=uuid4().hex,
            idempotency_key=uuid4().hex,
            occurred_at=support.database_now(),
            activity_id=activity_id,
        )

    service.create_service_activity(
        activities.CreateServiceActivityCommand(
            **command(),
            service_version_pk=catalog.current_version_id,
            initiating_domain=activities.InitiatingDomain.SERVICE,
            initiating_domain_reference=("committee:" if board_place is None else "board:") + str(circle.circle_uuid),
            activity_basis_reference=binding,
        )
    )
    service.assign_service_activity(
        activities.AssignServiceActivityCommand(
            **command(),
            assignee_identity_id=(worker or founder).identity_id,
            assignment_reference="reserve-review",
            assignment_basis_reference=("fixture:committee-chair" if board_place is None
                                        else str(board_place.participation_uuid)),
        )
    )
    return (activity_id, service) if with_service else activity_id
