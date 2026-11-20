"""Resumable first stages of the disposable trustee demonstration."""
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Max

from core import models, root_genesis_contract
from src.intevia.services import organism_formation_service as formation, root_genesis
from src.intevia.services.clock import database_now
from src.intevia.services.identity_service import (
    LearnerRegistrationService, RegistrationServiceRefused, uuid_for_child,
)
from src.intevia.services.organism_application_authority import fixture_terms_permitted

TERMS = (
    "Disposable Alpha platform terms. Use invented demonstration accounts and data. "
    "The records may be erased after rehearsal. Acceptance applies to this Alpha only."
)
OFFERING = (
    "Disposable Alpha offering. The demonstration organisation chooses the Free offering. "
    "No fee is payable. This choice applies only to this disposable Alpha organisation."
)
TERMS_LABEL = "ALPHA-DISPOSABLE-TRUSTEE-1"
# R-D3 scope and the Governor’s A-1 designation of the issuer. Text approval is in the VC relay.
ISSUE = "sha256:1e59eeaa5b12886760a8e9adfb8fc4d0d0def8885fd2c5fd4f3c63c614aeea2f"
SCENARIO = "urn:intevia:alpha:trustees-question:v1:"
# BEGIN CANDIDATE INPUTS — amend the demonstration names and Policies here.
DEMO_LO = "Alder Beck Community Trust"
DEMO_PURPOSE = "Rehearse a trustee handover."
DUTIES_REF = "alpha-treasurer-duties"
APPROVAL = "approval"
DUTIES_TEXT = (f"[#{APPROVAL}] The organisation has a Treasurer. The Board of Trustees appoints "
               "the holder with the nominee's consent. The Treasurer approves ordinary payments.")
MINIMUM_REF = "alpha-board-minimum"
MINIMUM_TEXT = '{"board_makeup":{"minimum":3}}'
TREASURER_CODE = "TREASURER"
ROOT_USERS = {"root-founder": "alpha-root-founder", "root-nominee": "alpha-root-nominee"}
PEOPLE = {
    "founder": ("alpha-rowan", "Rowan Fell"),
    "trustee-two": ("alpha-mira", "Mira Alder"),
    "trustee-three": ("alpha-kit", "Kit Vale"),
    "treasurer": ("alpha-elin", "Elin Wren"),
    "later-reader": ("alpha-remy", "Remy Moss"),
}
# END CANDIDATE INPUTS
ROOT_FILES = {
    "principal_invocation": ("PRINCIPAL_INVOCATION_RECEIVED_v1.txt", root_genesis_contract.PRINCIPAL_SHA256),
    "designation_invocation": ("ROOT_DESIGNATION_INPUT_v1_0.md", root_genesis_contract.DESIGNATION_SHA256),
    "adoption_capture": ("HG_RULINGS_R_P3_RD1_RD2_RD3_20261105_CAPTURE.txt", root_genesis_contract.ADOPTION_SHA256),
}


def scenario_id(label):
    return uuid5(NAMESPACE_URL, SCENARIO + label)


def digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


def refuse(reason, next_step):
    return CommandError(
        f"Setup stopped. {reason} Earlier committed receipts still apply. "
        f"{next_step} The Vision Chamber can help with this rehearsal."
    )


@contextmanager
def owned_database(name):
    if not fixture_terms_permitted("default") or connection.settings_dict["NAME"] != name:
        raise refuse("The named disposable database does not qualify.",
                     "Use the exact name of your owned, disposable Alpha database.")
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise refuse("Setup needs its own PostgreSQL session and transaction boundaries.",
                     "Run the management command outside another transaction.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        if cursor.fetchone()[0] != name:
            raise refuse("The connected database differs from the named one.",
                         "Check the database connection.")
        # GENESIS owns its outer transaction. Serialize setup invocations across those commits.
        lock = int.from_bytes(sha256(SCENARIO.encode()).digest()[:8], "big", signed=True)
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock])
        if not cursor.fetchone()[0]:
            raise refuse("Another setup stage is running.", "Rerun this stage after it finishes.")
        try:
            yield
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock])


def source_bytes(directory):
    values = {}
    for field, (filename, pin) in ROOT_FILES.items():
        try:
            raw = (directory / filename).read_bytes()
        except OSError:
            raise refuse("A GENESIS source file is unavailable.", "Supply the issued records directory.") from None
        if sha256(raw).hexdigest() != pin:
            raise refuse("A GENESIS source differs from its issued bytes.",
                         "Restore the exact issued file, preserving line endings.")
        values[field] = raw
    return values


def inputs_from(path):
    if path is None:
        raise refuse("This stage needs disposable account inputs.", "Supply the local inputs file.")
    try:
        values = json.loads(path.read_bytes())
    except (ValueError, OSError):
        raise refuse("The disposable inputs file could not be read.", "Check its path and JSON format.") from None
    if not isinstance(values, dict):
        raise refuse("The disposable inputs must be an object.", "Check the file against the operator note.")
    return values


def account_data(inputs, label, *, accept=False):
    accounts = inputs.get("accounts")
    data = accounts.get(label) if isinstance(accounts, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("password"), str) or not data["password"]:
        raise refuse(f"The disposable account input for {label} is missing.", "Supply its local account entry.")
    if accept and data.get("accept_terms") is not True:
        raise refuse(f"The disposable {label} registration has no explicit terms acceptance.",
                     "Record that account’s own Alpha terms choice.")
    return data["password"]


def texts(*, provision=False):
    # The commission authorises issuing these two disposable inputs; institutional acts use services.
    with transaction.atomic():
        terms = models.PlatformTermsVersion.objects.filter(terms_version_uuid=scenario_id("terms")).first()
        offering = models.OrganismEntitlementPolicyText.objects.filter(text_sha256=digest(OFFERING)).first()
        if provision and terms is None:
            if models.PlatformTermsVersion.objects.filter(version_label=TERMS_LABEL).exists():
                raise refuse("The Alpha terms label already identifies a different issuance.",
                             "Check its existing receipt before issuing these texts.")
            terms = models.PlatformTermsVersion.objects.create(
                terms_version_uuid=scenario_id("terms"), version_label=TERMS_LABEL,
                text_body=TERMS, text_commitment="s015r1:" + digest(TERMS),
                cited_rulings=[], published_by_authority_reference=ISSUE,
                is_fixture=True, effective_from=database_now(),
            )
        if provision and offering is None:
            latest = models.OrganismEntitlementPolicyText.objects.aggregate(version=Max("policy_version"))["version"]
            offering = models.OrganismEntitlementPolicyText.objects.create(
                policy_version=(latest or 0) + 1, accepted_text=OFFERING, text_sha256=digest(OFFERING),
            )
        if (terms is None or not terms.is_fixture or terms.version_label != TERMS_LABEL
                or terms.text_body != TERMS or terms.text_commitment != "s015r1:" + digest(TERMS)
                or terms.published_by_authority_reference != ISSUE or offering is None
                or offering.accepted_text != OFFERING):
            raise refuse("The issued Alpha texts are missing or differ.", "Check the texts stage and its receipts.")
        return terms, offering


def root_record(terms, raw):
    record = models.RootGenesis.objects.select_related("founder", "nominee").first()
    if record is None:
        return None
    request = json.loads(bytes(record.request_bytes))
    if (request.get("organism_uuid") != str(record.organism_uuid) or record.canonical_reference != root_genesis_contract.CANONICAL_ROOT
            or record.scope != root_genesis_contract.SCOPE or record.request_reference != "alpha-trustee:root"
            or record.idempotency_key != str(scenario_id("genesis"))
            or request.get("terms_version_uuid") != str(terms.terms_version_uuid)
            or request.get("founder_username") != ROOT_USERS["root-founder"]
            or request.get("nominee_username") != ROOT_USERS["root-nominee"]
            or any(bytes(getattr(record, field)) != value for field, value in raw.items())
            or not models.LivingOrganism.objects.filter(organism_id=record.organism_uuid, platform_root=True).exists()):
        raise refuse("The existing root belongs to a different GENESIS request.",
                     "Check the recorded designation before continuing.")
    return record


def create_root(terms, raw, inputs):
    if inputs.get("accept_genesis_terms") is not True:
        raise refuse("The founder’s GENESIS acceptance is missing.", "Record the disposable GENESIS choice.")
    accounts = {
        label: root_genesis.GenesisAccount(username, account_data(inputs, label, accept=True), True)
        for label, username in ROOT_USERS.items()
    }
    root_genesis.execute(root_genesis.GenesisCommand(
        organism_uuid=uuid4(), founder=accounts["root-founder"], nominee=accounts["root-nominee"],
        terms_version_uuid=terms.terms_version_uuid, genesis_terms_accepted=True,
        request_reference="alpha-trustee:root", idempotency_key=str(scenario_id("genesis")), **raw,
    ))


def registered_person(label, terms):
    # Use the supported registration's activation receipt; never acquire an account by name alone.
    correlation = scenario_id("register:" + label)
    transition = models.IdentityTransition.objects.filter(
        correlation_id=uuid_for_child(correlation, "activate"),
        action=models.IdentityTransition.Action.ACTIVATE,
    ).first()
    if transition is None:
        return None
    receipt = models.LearnerRegistration.objects.select_related("identity").get(identity_id=transition.identity_id)
    if (transition.authority_reference != f"learner-registration:{receipt.registration_uuid}"
            or receipt.authority_reference != f"platform-terms-version:{terms.terms_version_uuid}#registration"
            or receipt.identity.canonical_username != PEOPLE[label][0]
            or receipt.entry_mode != models.LearnerRegistration.EntryMode.SUPPORTED):
        raise refuse("The registration receipt does not match the demonstration account.",
                     "Check the activation and terms receipts.")
    return receipt


class Command(BaseCommand):
    help = "Stage the disposable trustee demonstration through A0."

    def add_arguments(self, parser):
        parser.add_argument("stage", choices=("texts", "root", "people", "found", "formation", "birth",
                                             "policies", "treasurer", "trustees", "activate", "depart"))
        parser.add_argument("--owned-database", required=True, help="Exact name of the disposable database you own.")
        parser.add_argument("--records", type=Path, default=Path(__file__).resolve().parents[3] / "docs/governance/root_genesis")
        parser.add_argument("--inputs", type=Path, help="Local disposable inputs JSON. Keep it outside source control.")

    def handle(self, *args, **options):
        stage = options["stage"]
        with owned_database(options["owned_database"]):
            terms, offering = texts(provision=stage == "texts")
            if stage == "texts":
                self.stdout.write(f"Terms {terms.terms_version_uuid}; offering version {offering.policy_version}, "
                                  f"SHA-256 {offering.text_sha256}. Next: root, available now.")
                return
            raw = source_bytes(options["records"])
            root = root_record(terms, raw)
            if stage == "root":
                if root is None:
                    try:
                        create_root(terms, raw, inputs_from(options.get("inputs")))
                    except (RegistrationServiceRefused, root_genesis.GenesisRefused, ValidationError):
                        raise refuse("The governed GENESIS or registration check refused the request.",
                                     "Check the issued inputs and disposable account acceptances.") from None
                    root = root_record(terms, raw)
                self.stdout.write(f"GENESIS {root.designation_uuid}; root {root.organism_uuid}; "
                                  f"decision {root.decision_reference}. Next: people, available now.")
                return
            if root is None:
                raise refuse("The issued root has not been founded.", "Run the root stage.")
            if stage == "people":
                self.register_people(terms, options.get("inputs"))
            elif stage == "found":
                self.found(terms, inputs_from(options.get("inputs")))

            else:
                from ._alpha_trustee_stages import Journey
                journey = Journey(terms, offering, root, options.get("inputs"), self.stdout.write)
                journey.run(stage)

    def register_people(self, terms, path):
        inputs = None
        for label, (username, display) in PEOPLE.items():
            receipt = registered_person(label, terms)
            if receipt is None:
                if inputs is None:
                    inputs = inputs_from(path)
                try:
                    receipt = LearnerRegistrationService.register(
                        username=username, password=account_data(inputs, label, accept=True), display_name=display,
                        terms_version_uuid=terms.terms_version_uuid, terms_accepted=True,
                        correlation_id=scenario_id("register:" + label),
                        entry_mode=models.LearnerRegistration.EntryMode.SUPPORTED,
                        enterer_executor="management:provision_learner",
                    )
                except (RegistrationServiceRefused, ValidationError):
                    raise refuse(f"The governed registration for {label} was refused.",
                                 "Check its inputs and any existing registration.") from None
            self.stdout.write(f"{label} registration {receipt.registration_uuid}.")
        self.stdout.write("Next: found, available now.")

    def found(self, terms, inputs):
        label = "founder"
        registered = registered_person(label, terms)
        if registered is None:
            raise refuse("The demonstration founder has no registration receipt.", "Run the people stage.")
        actor = authenticate(username=PEOPLE[label][0], password=account_data(inputs, label))
        if actor is None or actor.pk != registered.identity.credential_id:
            raise refuse("The demonstration founder could not be authenticated.", "Check the founder’s local inputs.")
        if inputs.get("accept_founding_terms") is not True:
            raise refuse("The founding terms choice is missing.", "Record the demonstration founder’s choice.")
        found = formation.OrganismFormationService(clock=database_now).found_living_organism(
            formation.FoundLivingOrganismCommand(
                credential=actor, name=DEMO_LO, purpose=DEMO_PURPOSE,
                request_reference="alpha-trustee:found", idempotency_key=str(scenario_id("found")),
                accepted_terms_version_uuid=terms.terms_version_uuid,
            )
        )
        self.stdout.write(f"Demonstration LO {found.living_organism_uuid}; founding event {found.event_uuid}.")
        phase = formation.OrganismPhaseAdvanceService(clock=database_now).advance_phase(
            formation.PhaseAdvanceCommand(
                credential=actor, living_organism_uuid=found.living_organism_uuid,
                from_phase="CONCEIVING", to_phase="GERMINATING", request_reference="alpha-trustee:germinate",
                idempotency_key=str(scenario_id("germinate")),
            )
        )
        self.stdout.write(f"GERMINATING event {phase.event_uuid}. "
                          "Next: formation, available now.")
