"""Governed stages following the disposable LO's founding receipt."""
from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from django.contrib.auth import authenticate
from django.core.management.base import CommandError
from django.db import connection

from core import models
from core.organism_contract import OrganismAction
from src.intevia.services import circle_commands, cq, law_acts, law_records, library_laws, library_policy
from src.intevia.services import organism_application_service as gateway
from src.intevia.services import organism_formation_service as formation
from src.intevia.services import organism_governance_service as governance
from src.intevia.services import organism_membership_service as memberships
from src.intevia.services import organism_office_consent_service as consent
from src.intevia.services import organism_role_definition_service as definitions
from src.intevia.services import organism_role_service as roles
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import S015Authority

from . import setup_alpha_trustee as setup

NEXT = {"formation": "birth", "birth": "policies", "policies": "treasurer",
        "treasurer": "trustees", "trustees": "activate"}
TRUSTEES = ("founder", "trustee-two", "trustee-three")


def require(condition, reason, remedy):
    if not condition:
        raise setup.refuse(reason, remedy)


def keys(operation):
    return {"request_reference": "alpha-trustee:" + operation,
            "idempotency_key": str(setup.scenario_id(operation))}


def rows(query, params):
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def fold(instance):
    return rows("SELECT public.s015_fold_effective_state(%s,%s,"
                "public.intevia_clock_statement(),public.intevia_clock_statement())",
                [instance._meta.db_table, instance.pk])[0][0]


class Journey:
    def __init__(self, terms, offering, root, path, write):
        self.inputs = setup.inputs_from(path)
        self.terms, self.offering, self.write = terms, offering, write
        self.people = {}
        self.actors = {}
        self.user("founder")
        receipt = self.receipt("found")
        require(receipt, "The founding receipt is missing.", "Run found.")
        self.lo = models.LivingOrganism.objects.get(
            organism_id=receipt["living_organism_uuid"], founder_identity=self.person("founder"),
            platform_root=False)
        self.uuid = self.lo.organism_id
        self.scope = {"credential": self.user("founder"), "organism_uuid": self.uuid}
        self.rule = setup.scenario_id("board-rule")
        services = {"authority": S015Authority(provider=ProductionApplicationAuthorityProvider()),
                    "clock": setup.database_now}
        self.gateway = gateway.OrganismApplicationService(**services)
        self.governance = governance.OrganismGovernanceService(**services)
        self.roles = roles.OrganismRoleService(**services)
        self.memberships = memberships.OrganismMembershipService(**services)
        self.definitions = definitions.OrganismRoleDefinitionService(**services)
        self.consent = consent.OfficeAppointmentConsentService(**services)
        # Only original request times go here. Every outcome is read from PostgreSQL.
        self.journal = Path(path).parent / f".alpha-trustee-{root.organism_uuid}-{self.uuid}.json"
        self.signature = setup.digest(json.dumps(
            [setup.DEMO_LO, setup.PEOPLE, setup.TREASURER_CODE, setup.DUTIES_REF,
             setup.DUTIES_TEXT, setup.MINIMUM_REF, setup.MINIMUM_TEXT], sort_keys=True))
        self.times = {}
        if self.journal.exists():
            saved = json.loads(self.journal.read_bytes())
            require(saved.get("inputs") == self.signature, "Candidate inputs differ from this run.",
                    "Use the original inputs or start a fresh disposable database.")
            self.times = saved["times"]

    def person(self, label):
        if label not in self.people:
            registered = setup.registered_person(label, self.terms)
            require(registered, f"The registration for {label} is missing.", "Run people.")
            self.people[label] = registered.identity
        return self.people[label]

    def user(self, label):
        if label not in self.actors:
            person = self.person(label)
            user = authenticate(username=setup.PEOPLE[label][0],
                                password=setup.account_data(self.inputs, label))
            require(user and user.pk == person.credential_id, f"Authentication failed for {label}.",
                    "Check the local inputs.")
            self.actors[label] = user
        return self.actors[label]

    def time(self, op):
        if op not in self.times:
            self.times[op] = setup.database_now().isoformat()
            with NamedTemporaryFile("w", dir=self.journal.parent, delete=False) as temp:
                json.dump({"inputs": self.signature, "times": self.times}, temp)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp.name, self.journal)
        return datetime.fromisoformat(self.times[op])

    def receipt(self, op, label="founder"):
        try:
            return models.OrganismCommandReceipt.objects.get(actor=self.person(label), **keys(op)).result_payload
        except models.OrganismCommandReceipt.DoesNotExist:
            return None

    def need(self, op, stage, label="founder"):
        result = self.receipt(op, label)
        require(result, f"The {op} receipt is missing.", f"Run {stage}.")
        return result

    def command(self, op, method, command_type, *, label="founder", times="both", **fields):
        self.operation = op
        credential = self.user(label)
        result = self.receipt(op, label)
        if result is None:
            args = dict(keys(op), credential=credential, **fields)
            if times in ("both", "effective"):
                args["effective_at"] = self.time(op)
            if times == "both":
                args["occurred_at"] = args["effective_at"]
            method(command_type(**args))
            result = self.need(op, "this stage", label)
        identifiers = [f"{k}={v}" for k, v in result.items() if k.endswith("_uuid") or k in (
            "version", "definition_version", "lineage_reference", "resulting_state", "offering_choice")]
        self.write(f"{op} " + " ".join(identifiers))
        return result

    def run(self, stage):
        self.operation = stage
        try:
            self.run_stage(stage)
        except CommandError:
            raise
        except Exception as error:
            # Never echo a product exception's arguments or dump its request.
            kind = type(error).__name__.replace("CQ", "Vote")
            raise setup.refuse(f"{stage} stopped at {self.operation} ({kind}).",
                               "Inspect the last printed receipt, then rerun this stage.") from None

    def run_stage(self, stage):
        if stage in {"treasurer", "trustees", "activate"} and self.receipt("depart", "treasurer"):
            activation = self.activation()
            prior = self.need("appoint", "treasurer")
            self.write(f"A0 was completed at {activation.event_uuid}; original Treasurer appointment "
                       f"{prior['event_uuid']}. The departure supersedes that occupancy.")
            self.depart()
            self.write("A1 already complete. Stop here.")
            return
        getattr(self, stage)()
        if stage in NEXT:
            self.write(f"Next: {NEXT[stage]}, available now.")
        elif stage == "activate":
            self.write("A0 complete. Treasurer in office, three active Board places, voting On. Next: depart when the Treasurer confirms.")
        else:
            self.write("A1 complete. Capture the vacancy. Stop here.")

    @property
    def circle(self):
        created = self.need("circle", "formation")
        return models.Circle.objects.get(parent_organism=self.lo, kind="GOVERNANCE",
                                         circle_uuid=created["record_uuid"])

    def formation(self):
        self.need("germinate", "found")
        configurations = [
            ("governance", OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
             dict(cq_active=False, governance_enabled=True, inherit_portal_cq_settings=False,
                  require_cq_to_change_lo_settings=False, require_cq_to_change_application_question=False)),
            ("applications", OrganismAction.EFFECT_APPLICATION_SETTINGS,
             dict(applications_open=True, automatic_acceptance=False, processing_responsibility="LO")),
            ("question", OrganismAction.EFFECT_APPLICATION_QUESTION,
             dict(question_text="Why would you like to join?", answer_format="LONG_ANSWER")),
        ]
        for name, action, fields in configurations:
            op = "effect-" + name
            ready = self.receipt(op)
            if ready:
                version = UUID(ready["version_uuid"])
            else:
                draft_op = "draft-" + name
                self.operation = draft_op
                at = self.time(draft_op)
                draft = self.gateway.save_gateway_draft(gateway.GatewayDraftCommand(
                    credential=self.user("founder"), living_organism_id=self.uuid, action=action,
                    fields=fields, occurred_at=at, effective_at=at, **keys(draft_op)))
                require(isinstance(draft.version_uuid, UUID), "The governed draft has no visible version UUID.",
                        "Inspect the original gateway draft as its Coordinator.")
                version = draft.version_uuid
            self.command(op, self.gateway.effect_gateway_version, gateway.EffectGatewayVersionCommand,
                         living_organism_id=self.uuid, action=action, version_uuid=version)

        at = self.time("arrangement")
        seats = [dict(seat_id=f"trustee-{n}", eligibility="LO_MEMBER", admission_route="APPOINTMENT",
                      deciding_body="office:LO_COORDINATOR", term=None, vacancy_rule="BY_SEAT_ROUTE",
                      effective_from=at.isoformat()) for n in range(len(TRUSTEES))]
        self.command("arrangement", self.governance.adopt_governing_instrument,
                     governance.AdoptGoverningInstrumentCommand, times="effective", living_organism_uuid=self.uuid,
                     rule_uuid=self.rule, version=1, effective_from=at,
                     body={"seats": seats, "officer_roles": [], "election_schedule": None})
        self.command("circle", self.governance.create_governance_circle, governance.CreateGovernanceCircleCommand,
                     times="effective", living_organism_uuid=self.uuid)

    def birth(self):
        circle = self.circle
        place = models.CircleParticipation.objects.filter(circle=circle, seat_rule_uuid=self.rule,
            seat_id="trustee-0", membership__identity=self.person("founder")).first()
        if place is None:
            self.governance.seat_governance_circle(governance.SeatFoundingGovernanceCircleCommand(
                credential=self.user("founder"), living_organism_uuid=self.uuid, circle_uuid=circle.circle_uuid,
                seat_id="trustee-0", seat_rule_uuid=self.rule, effective_at=self.time("founder-seat"),
                **keys("founder-seat")))
            place = models.CircleParticipation.objects.get(circle=circle, seat_rule_uuid=self.rule,
                seat_id="trustee-0", membership__identity=self.person("founder"))
        self.write(f"Founder seat request {place.decision.request.request_uuid}, decision "
                   f"{place.decision.decision_uuid}, participation {place.participation_uuid}.")
        self.command("birth-phase", formation.OrganismPhaseAdvanceService(clock=setup.database_now).advance_phase,
                     formation.PhaseAdvanceCommand, times=None, living_organism_uuid=self.uuid,
                     from_phase="GERMINATING", to_phase="BIRTHING")
        birth = self.command("birth", formation.OrganismBirthingService(clock=setup.database_now).birth_living_organism,
                             formation.BirthingCommand, times=None, living_organism_uuid=self.uuid,
                             offering_choice="FREE", policy_version=self.offering.policy_version,
                             policy_text_sha256=self.offering.text_sha256, committee_chair_board_member=False)
        snapshot = models.LOBirthingSnapshot.objects.get(
            snapshot_uuid=birth["snapshot_uuid"], living_organism=self.lo)
        self.write(f"Birthing snapshot {snapshot.snapshot_uuid}; Free election {birth['entitlement_event_uuid']}.")
        expected = self.need("effect-governance", "formation")["version_uuid"]
        require(snapshot.body["settings"]["governance_configuration"] == "governance-configuration-version:" + expected
                and snapshot.body.get("governance_constitution") == f"rule:{self.rule}@1"
                and snapshot.body["settings"].get("committee_chair_board_member") is False,
                "The Birthing snapshot names a different configuration.", "Inspect the original Birthing receipt.")
        office = rows("SELECT public.circle_lo_office(%s,%s,public.intevia_clock_statement(),"
                     "public.intevia_clock_statement())", [self.lo.pk, self.person("founder").pk])[0][0]
        require(office is not None, "The founder has no current Organisation Coordinator office.", "Inspect the office lineage.")
        self.write(f"Continuing Organisation Coordinator office {office}.")
        if not self.receipt("appoint"):
            self.before_voting()

    def before_voting(self):
        require(fold(self.circle) in ("DORMANT", "ELIGIBLE")
                and cq.settings(**self.scope)["settings"]["enabled"] is False,
                "These acts need a dormant Board and voting Off.", "Inspect the Board and voting receipts.")

    def law(self, reference, content, *, create=False):
        proposal_id = setup.scenario_id("adopt-" + reference)
        laws = library_laws.our_laws(**self.scope)["laws"]
        record = next((law for law in laws if law["binding"] == proposal_id), None)
        if record is None and create:
            self.before_voting()
            self.operation = "draft-" + reference
            version = library_policy.draft_policy(
                **self.scope, resource_reference=reference, content=content,
                authors=[self.person("founder").identity_id], declare_own_authorship=True,
                request_uuid=setup.scenario_id("draft-" + reference), route="COORDINATOR_ROUTE")
            self.operation = "publish-" + reference
            library_policy.publish_policy(**self.scope, version_id=version.pk, route="COORDINATOR_ROUTE",
                                         request_uuid=setup.scenario_id("publish-" + reference))
            self.operation = "adopt-" + reference
            adopted = law_acts.OrganismLawService().adopt_law(law_acts.AdoptLawCommand(
                credential=self.user("founder"), **keys("adopt-" + reference),
                proposal=law_acts.LawProposal(organism=self.uuid, resource=reference, proposal=proposal_id,
                    law_class="POLICY", operation="ADOPT", intent="ADOPT", version=version.version_number)))
            require((adopted.status, adopted.route) == ("ADOPT", "COORDINATOR_ROUTE"),
                    "The Policy has no Coordinator adoption receipt.", "Inspect the local law register.")
            return self.law(reference, content)
        require(record and record["library_reference"] == reference and record["content"] == content
                and record["decision_route"] == "COORDINATOR_ROUTE" and record["class_name"] == "POLICY"
                and record["version_number"] == 1 and not record["inherited"],
                f"The exact local Policy {reference} is missing or differs.", "Check the policies stage.")
        publications = rows("SELECT fact_uuid FROM public.core_libpublicationfact WHERE operation_uuid=%s "
                            "AND organism_id=%s AND version_id=%s AND actor_id=%s AND kind='PUBLICATION' "
                            "AND authority_source->>'route'='COORDINATOR_ROUTE'",
                            [setup.scenario_id("publish-" + reference), self.lo.pk, record["version"],
                             self.person("founder").pk])
        require(len(publications) == 1, "The Policy publication receipt is missing.", "Inspect publication.")
        self.write(f"{reference} version {record['version']} (v{record['version_number']}), "
                   f"publication {publications[0][0]}, binding {record['binding']}, adoption {record['act']}, "
                   f"in force from {record['in_force_from']}, {record['decision_route']}.")
        return record

    def minimum(self, law):
        minimum, problem = rows("SELECT p->>'minimum', p->>'problem' FROM public.c108_board_makeup("
                                "%s,public.intevia_clock_statement(),public.intevia_clock_statement()) p",
                                [self.lo.pk])[0]
        require(minimum == "3" and problem is None, "The operative Board minimum differs.",
                "Inspect the local makeup law.")
        self.write(f"Effective Board minimum {minimum}; local Policy {law['binding']}.")

    def policies(self):
        self.need("birth", "birth")
        self.law(setup.DUTIES_REF, setup.DUTIES_TEXT, create=True)
        self.minimum(self.law(setup.MINIMUM_REF, setup.MINIMUM_TEXT, create=True))

    def admit(self, label):
        if not self.receipt("accept-" + label):
            self.before_voting()
        submitted = self.command("apply-" + label, self.gateway.submit_application, gateway.SubmitApplicationCommand,
                                 label=label, living_organism_id=self.uuid,
                                 answer="I would like to take part in the community trust.")
        application = UUID(submitted["application_uuid"])
        self.command("claim-" + label, self.gateway.claim_application, gateway.ClaimApplicationCommand,
                     application_uuid=application)
        accepted = self.command("accept-" + label, self.gateway.decide_application, gateway.DecideApplicationCommand,
                     application_uuid=application, outcome="ACCEPT")
        member = self.member(label)
        effect = models.ApplicationDecisionEffect.objects.get(
            decision__decision_uuid=accepted["record_uuid"], decision__application__application_uuid=application,
            transition__membership=member, transition__resulting_state="ACTIVE")
        self.write(f"{label} ACTIVE membership {member.membership_uuid}, opening {effect.transition.event_uuid}.")
        return member

    def member(self, label):
        member = models.OrganismMembership.objects.get(living_organism=self.lo, identity=self.person(label))
        require(fold(member) == "ACTIVE", f"The membership for {label} is not active.", "Inspect its transitions.")
        return member

    def citation(self, role, law):
        provision = models.OrganismRoleDefinitionProvision.objects.get(
            definition__living_organism=self.lo, definition__role_uuid=role, relationship="OFFICE_BASIS")
        view = law_records.resolve_citation(self.uuid, provision.provision_uuid)
        cited = view["cited"] if view else {}
        require(view and view["at_act"]["status"] == view["now"]["status"] == "IN_FORCE"
                and cited["cited_version"] == law["version"] and cited["locator"] == setup.APPROVAL
                and cited["binding"] and cited["binding"]["binding_uuid"] == law["binding"],
                "The office basis does not resolve to the approved clause in force.",
                "Inspect the definition and the local law register.")
        self.write(f"Office basis {provision.provision_uuid}, {setup.DUTIES_REF}#{setup.APPROVAL}, "
                   f"v{law['version_number']}, binding {law['binding']}, IN_FORCE at definition and now.")

    def holder(self):
        assigned = self.need("appoint", "treasurer")
        holder = models.ContextualRoleAssignment.objects.get(
            assignment_uuid=assigned["assignment_uuid"], membership=self.member("treasurer"),
            role_definition__role_uuid=self.need("define", "treasurer")["role_definition_uuid"])
        require(fold(holder) == "ACTIVE", "The separate Treasurer is not in office.",
                "Inspect the appointment lineage.")
        return holder

    def treasurer(self):
        duties = self.law(setup.DUTIES_REF, setup.DUTIES_TEXT)
        self.minimum(self.law(setup.MINIMUM_REF, setup.MINIMUM_TEXT))
        if not self.receipt("appoint"):
            self.before_voting()
        member = self.admit("treasurer")
        basis = definitions.DefinitionProvision("OFFICE_BASIS", f"law-ref:{setup.DUTIES_REF}#{setup.APPROVAL}",
                                                str(duties["version_number"]))
        defined = self.command("define", self.definitions.record_role_definition, definitions.RecordRoleDefinitionCommand,
                               living_organism_uuid=self.uuid, event_action="DEFINE",
                               content=definitions.DefinitionContent(code=setup.TREASURER_CODE,
                                   kind="BOARD_OFFICE", appointing_authority="LO_COORDINATOR_BODY", provisions=(basis,)))
        role = UUID(defined["role_definition_uuid"])
        self.citation(role, duties)
        proposed = self.command("propose", self.roles.propose_assignment, roles.ProposeAssignmentCommand,
                                membership_uuid=member.membership_uuid, role_definition_uuid=role,
                                effective_until_state="NOT_ESTABLISHED", effective_until=None)
        assignment = UUID(proposed["assignment_uuid"])
        self.command("consent", self.consent.record_office_consent, consent.RecordOfficeConsentCommand,
                     label="treasurer", assignment_uuid=assignment, proposal_event_uuid=UUID(proposed["event_uuid"]),
                     action="CONSENT")
        self.command("appoint", self.roles.transition_assignment, roles.TransitionAssignmentCommand,
                     assignment_uuid=assignment, action="ASSIGN", resulting_state="ACTIVE",
                     effective_until_state="NONE", effective_until=None)
        self.write(f"Treasurer ACTIVE assignment {self.holder().assignment_uuid}.")

    def roster(self):
        circle = self.circle
        actual = rows("""
            SELECT p.seat_id, m.identity_id, p.seat_rule_uuid, p.participation_uuid
            FROM public.core_circleparticipation p JOIN public.core_organismmembership m ON m.id=p.membership_id
            CROSS JOIN (SELECT public.intevia_clock_statement() AS cut) t
            WHERE p.circle_id=%s AND p.effective_at<=t.cut AND p.recorded_at<=t.cut
              AND NOT EXISTS (SELECT FROM public.core_circleparticipationend e WHERE e.participation_id=p.id
                              AND e.effective_at<=t.cut AND e.recorded_at<=t.cut)
              AND public.circle_place_at(p.circle_id,m.id,t.cut,t.cut)
              AND public.disc_member_state(m.living_organism_id,m.identity_id,t.cut)='ACTIVE'
            ORDER BY p.seat_id
            """, [circle.pk])
        expected = [(f"trustee-{n}", self.person(label).pk, self.rule) for n, label in enumerate(TRUSTEES)]
        require([row[:3] for row in actual] == expected and self.person("treasurer").pk not in
                {row[1] for row in actual}, "The three effective seats do not match the appointed people.",
                "Inspect the three participation receipts.")
        for seat, _, _, participation in actual:
            self.write(f"{seat} participation {participation}.")
        return actual

    def trustees(self):
        self.holder()
        circle = self.circle
        for n, label in enumerate(TRUSTEES[1:], 1):
            if not self.receipt("seat-" + label):
                self.before_voting()
            self.admit(label)
            request = self.command("request-" + label, self.governance.request_governance_seat,
                                   governance.RequestGovernanceSeatCommand, label=label, times="effective",
                                   living_organism_uuid=self.uuid, circle_uuid=circle.circle_uuid,
                                   seat_rule_uuid=self.rule, seat_id=f"trustee-{n}")
            self.command("seat-" + label, self.governance.decide_governance_seat,
                         governance.DecideGovernanceSeatCommand, times="effective", living_organism_uuid=self.uuid,
                         request_uuid=UUID(request["request_uuid"]), outcome="ACCEPT")
        self.roster()
        self.admit("later-reader")

    def activate(self):
        self.need("seat-trustee-three", "trustees")
        self.need("accept-later-reader", "trustees")
        self.holder()
        self.roster()
        self.citation(UUID(self.need("define", "treasurer")["role_definition_uuid"]),
                      self.law(setup.DUTIES_REF, setup.DUTIES_TEXT))
        self.minimum(self.law(setup.MINIMUM_REF, setup.MINIMUM_TEXT))
        circle = self.circle
        settings = cq.settings(**self.scope)
        key = setup.scenario_id("voting-on")
        if settings["version"] != key:
            self.before_voting()
            self.operation = "enable voting"
            act = cq.change_settings(**self.scope, predecessor=settings["version"], request_uuid=key,
                                     changes={"enabled": True, "clarity_stage": True})
            require(act == key, "The voting settings act differs.", "Inspect the settings history.")
            settings = cq.settings(**self.scope)
        require(settings["version"] == key and settings["settings"]["enabled"] is True
                and settings["settings"]["clarity_stage"] is True,
                "The enabled voting version is not current.", "Inspect the voting settings history.")
        self.write(f"Voting On, Clarity on; settings act {key}.")
        self.operation = "activate Board"
        event = circle_commands.activate(**self.scope, circle_uuid=circle.circle_uuid,
                                          request_uuid=setup.scenario_id("activate"), committee_chair_board_member=False)
        activation = models.CircleStateEvent.objects.get(event_uuid=event, circle=circle, action="ACTIVATE")
        require(fold(circle) == "ACTIVE" and fold(self.lo) == "ACTIVE",
                "The Board or the LO is not ACTIVE.", "Inspect the activation and Birthing receipts.")
        self.write(f"ACTIVE Board {circle.circle_uuid}, activation {activation.event_uuid}, "
                   f"basis {activation.evidence_reference}.")

    def activation(self):
        activation = models.CircleStateEvent.objects.filter(event_uuid=setup.scenario_id("activate"),
            circle=self.circle, action="ACTIVATE", resulting_state="ACTIVE").first()
        require(activation, "The A0 activation receipt is missing.", "Complete activate before departure.")
        return activation

    def depart(self):
        activation = self.activation()
        before = self.roster()
        role = UUID(self.need("define", "treasurer")["role_definition_uuid"])
        member = models.OrganismMembership.objects.get(living_organism=self.lo, identity=self.person("treasurer"))
        assignment = models.ContextualRoleAssignment.objects.get(
            assignment_uuid=self.need("appoint", "treasurer")["assignment_uuid"],
            membership=member, role_definition__role_uuid=role)
        if not self.receipt("depart", "treasurer"):
            self.holder()
            require(self.inputs.get("confirm_treasurer_departure") is True,
                    "The Treasurer has not confirmed departure.", "Record the Treasurer's explicit choice.")
            require(fold(self.circle) == "ACTIVE" and fold(self.lo) == "ACTIVE"
                    and cq.settings(**self.scope)["version"] == setup.scenario_id("voting-on"),
                    "The A0 state has changed since activation.", "Inspect the setup receipts.")
        result = self.command("depart", self.memberships.leave_organism, memberships.LeaveOrganismCommand,
                              label="treasurer", membership_uuid=member.membership_uuid,
                              confirmed=True, reason_class="VOLUNTARY")
        end = models.OrganismMembershipTransition.objects.get(
            membership=member, sequence=result["transition_sequence"], resulting_state="ENDED",
            reason_class="VOLUNTARY", **keys("depart"))
        closed = models.ContextualRoleAssignmentStateEvent.objects.get(
            assignment=assignment, action="END", resulting_state="ENDED", effective_at=end.effective_at,
            received_at=end.received_at, payload_fingerprint=end.payload_fingerprint, **keys("depart"))
        require(models.AuthorityDecision.objects.filter(
            decision_reference=closed.authority_decision_reference, outcome="QUALIFIED",
            target__departure_event=str(end.event_uuid),
            target__departure_decision=end.authority_decision_reference).exists(),
            "The appointment ending does not cite the membership departure.",
            "Inspect the two authority decisions.")
        vacancy = next((v for v in definitions.board_office_vacancies(self.uuid) if v.role_identity == role), None)
        require(vacancy and not vacancy.filled and fold(member) == fold(assignment) == "ENDED",
                "The Treasurer departure and vacancy do not agree.", "Inspect both ending events.")
        expected_places = [
            models.CircleParticipation.objects.get(circle=self.circle, seat_rule_uuid=self.rule,
                seat_id="trustee-0", membership__identity=self.person("founder")).participation_uuid,
            *(UUID(self.need("seat-" + label, "trustees")["participation_uuid"]) for label in TRUSTEES[1:])]
        after = self.roster()
        require(before == after and [row[3] for row in after] == expected_places
                and fold(self.circle) == fold(self.lo) == "ACTIVE" and cq.settings(**self.scope)["settings"]["enabled"],
                "The original places, active states or voting changed.", "Inspect the Board and departure lineage.")
        self.write(f"Member END {end.event_uuid}; appointment END {closed.event_uuid}; unfilled office {role}. "
                   f"LO {self.uuid} ACTIVE; Board activation {activation.event_uuid} and its three places continue.")
