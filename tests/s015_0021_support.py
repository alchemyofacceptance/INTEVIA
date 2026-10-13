"""S015 migration 0021 test support: raw-SQL fixture builders on the lawful route.

Change B (UFUND-2): every event row also carries the fourteen columns migration 0022 added, as one lawful act by
default - a party acting and entering the record themselves. The parts commitment of an event with no parts is derived
here by the published s015p1 rule; the predecessor's l1_commitment is read from the predecessor row as an input to the
chain properties (it establishes nothing about L1 correctness, which test_s015_0024_commitment_preimages checks
independently); l1_commitment itself is never supplied - the database assigns it.
"""
import datetime as dt
import hashlib
import json
import sys
import uuid

from django.conf import settings
from django.contrib.auth.models import User
from django.db import DatabaseError, connection, connections, transaction
from django.db.models import Q

from core.identity import canonical_username_v1
from core.models import (
    Identity,
    LearnerRegistration,
    PlatformTermsAcceptance,
    PlatformTermsVersion,
)
from src.intevia.services.identity_service import (
    LearnerRegistrationService,
    RegistrationServiceRefused,
)


def database_now():
    """Read the authoritative test timestamp once from PostgreSQL."""
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def current_core_leaf(connection):
    from django.db.migrations.executor import MigrationExecutor
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes("core")
    if len(leaves) != 1:
        raise AssertionError(f"expected one core migration leaf, found {leaves!r}")
    return leaves


def current_applied_core_head(connection):
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    graph = MigrationExecutor(connection).loader.graph
    applied = MigrationRecorder(connection).applied_migrations()
    applied_core = {
        key for key in applied if key[0] == "core" and key in graph.node_map
    }
    leaves = sorted(
        key
        for key in applied_core
        if not any(
            child.key in applied_core for child in graph.node_map[key].children
        )
    )
    if len(leaves) != 1:
        raise AssertionError(f"expected one applied core migration head, found {leaves!r}")
    return leaves


def restore_migration_targets(connection, targets):
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder
    from django.core.management.color import no_style

    executor = MigrationExecutor(connection)
    formation = ("core", "0045_sbg_governance_formation")
    entitlement = ("core", "0046_se_entitlement_foundation")
    applied = MigrationRecorder(connection).applied_migrations()
    core_targets = [target for target in targets if target[0] == "core"]
    restores_entitlement = any(
        entitlement in executor.loader.graph.forwards_plan(target)
        for target in core_targets
    )

    if (
        current_applied_core_head(connection) == [formation]
        and entitlement not in applied
        and restores_entitlement
    ):
        apps = executor.loader.project_state([formation]).apps
        governed_rule = apps.get_model("core", "GovernedRule")
        with connection.cursor() as cursor:
            for statement in connection.ops.sequence_reset_sql(
                no_style(), [governed_rule]
            ):
                cursor.execute(statement)

    executor = MigrationExecutor(connection)
    executor.migrate(targets)


def migrate_to_target_with_restore(test_case, target):
    from django.db.migrations.executor import MigrationExecutor

    received_targets = tuple(
        MigrationExecutor(connection).loader.graph.leaf_nodes()
    )
    restored = {"done": False}

    def restore():
        if not restored["done"]:
            restore_migration_targets(connection, received_targets)
            restored["done"] = True

    test_case.addCleanup(restore)
    test_case.restore_received_migrations = restore
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    return executor.loader.project_state([target]).apps


T0 = dt.datetime(2026, 9, 5, 10, 0, 0, tzinfo=dt.timezone.utc)
T_MINUS_1D = T0 - dt.timedelta(days=1)
SIX_CODES = ["INTEVIA_FOUNDER_STEWARD", "INTEVIA_STEWARD", "INTEVIA_GUIDING_STEWARD", "INTEVIA_EMERGENCY_STEWARD", "INTEVIA_PRIVACY_COORDINATOR", "COORDINATOR"]
FIVE_RESERVED = SIX_CODES[:5]
ANCHOR_FK = {
    "core_livingorganismevent": "living_organism_id", "core_circlestateevent": "circle_id",
    "core_organismmembershiptransition": "membership_id", "core_contextualroleassignmentstateevent": "assignment_id",
    "core_membershipconditionstateevent": "condition_id", "core_authorityinvalidationevent": "basis_id",
    "core_determinationcontestevent": "case_id", "core_coverageassessment": "case_id",
    "core_restrictedcontinuityevent": "case_id", "core_visibilitygrantstateevent": "grant_id",
    "core_obligationstateevent": "case_id", "core_planningclassificationevent": "case_id",
}
STATE_COL = {"core_coverageassessment": "result", "core_planningclassificationevent": "result"}
DEFAULT_PRIOR = object()


def hx(seed):
    return hashlib.sha256(seed.encode()).hexdigest()


def u(seed):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "urn:u21g:test:" + seed))


def fresh():
    return uuid.uuid4().hex[:10]


_DISPOSABLE_TEST_DATABASE_PREFIX = "test_intevia_living_organism_"


def _require_disposable_fixture_database():
    database_name = connection.settings_dict.get("NAME")
    if (
        type(database_name) is not str
        or not database_name.startswith(_DISPOSABLE_TEST_DATABASE_PREFIX)
        or database_name == _DISPOSABLE_TEST_DATABASE_PREFIX
    ):
        raise RuntimeError("labelled founding fixtures require an attempt-owned disposable database")


def empty_fp(cur, token, identity):
    cur.execute("SELECT public.s015_digest_envelope(public.s015_canonical_envelope(%s, %s::uuid, '{}'::text[]))", (token, identity))
    return cur.fetchone()[0]


def empty_parts_commitment(ev_table, event_uuid):
    """s015p1 commitment of an event with no parts, by vectors/S015_COMMITMENT_FORMS_s015r1_s015p1_RULE_v0_1.md section 3
    (compact array; table and uuid as JSON strings; empty list). Derived, never read from the database."""
    preimage = '["s015p1",' + json.dumps(ev_table, ensure_ascii=False) + "," + json.dumps(str(uuid.UUID(str(event_uuid)))) + ",[]]"
    return "s015p1:" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def contract_columns(actor_id):
    """The 0022 columns for a party acting and entering the record themselves (0022 EVENT_BASE_CHECKS)."""
    return {
        "actor_state": "PARTY", "actor_capacity": "STANDING", "enterer_identity_id": actor_id, "entry_mode": "TRANSPORT",
        "composing_rule_state": "NA_TRANSPORT", "composing_rule_reference": None, "composer_state": "NA_TRANSPORT",
        "composer_identity_id": None, "effective_until": None, "effective_until_state": "NONE", "l2_commitment": None,
    }


def predecessor_l1(cur, ev_table, cols):
    """l1_commitment of the predecessor row, as assigned by the database; None for a first event or when no row matches."""
    if (cols.get("sequence") or 1) <= 1:
        return None
    if cols.get("predecessor_id") is not None:
        cur.execute(f"SELECT l1_commitment FROM public.{ev_table} WHERE id = %s", (cols["predecessor_id"],))
    else:
        cur.execute(f"SELECT l1_commitment FROM public.{ev_table} WHERE {ANCHOR_FK[ev_table]} = %s AND sequence = %s",
                    (cols.get(ANCHOR_FK[ev_table]), cols["sequence"] - 1))
    row = cur.fetchone()
    return row[0] if row else None


def append_event(cur, ev_table, anchor_id, seq, action, prior, result, actor_id, basis_id, tag, pred_id=None,
                 eff=T0, occ=T0, rec=T0, extra=None, recorded_at=None, event_uuid=None, overrides=None):
    cols = {
        "event_uuid": event_uuid or u(f"{ev_table}:{anchor_id}:{seq}:{tag}"),
        ANCHOR_FK[ev_table]: anchor_id,
        "sequence": seq,
        "predecessor_id": pred_id,
        "predecessor_sequence": (seq - 1) if seq > 1 else None,
        "action": action,
        "prior_state": prior,
        STATE_COL.get(ev_table, "resulting_state"): result,
        "actor_id": actor_id,
        "actor_access_epoch": 0,
        "authority_basis_id": basis_id,
        "authority_decision_reference": "s015d1:" + hx(f"decision:{ev_table}:{anchor_id}:{seq}:{tag}"),
        "evidence_reference": f"u21g:evidence:{tag}",
        "request_reference": f"u21g:request:{tag}",
        "idempotency_key": f"u21g:{ev_table}:{anchor_id}:{seq}:{tag}",
        "payload_fingerprint": hx(f"payload:{tag}"),
        "lineage_reference": "s015l1:" + hx(f"lineage:{ev_table}:{anchor_id}:{seq}:{tag}"),
        "temporal_basis_kind": None if eff == rec else ("RETROSPECTIVE" if eff < rec else "PROSPECTIVE"),
        "temporal_basis_reference": None if eff == rec else f"u21g:basis:{tag}",
        "occurred_at": occ, "effective_at": eff, "received_at": rec, "recorded_at": recorded_at,
    }
    cols.update(contract_columns(actor_id))
    if extra:
        cols.update(extra)
    if overrides:
        cols.update(overrides)
    if "parts_commitment" not in cols:
        cols["parts_commitment"] = empty_parts_commitment(ev_table, cols["event_uuid"])
    if "predecessor_l1_commitment" not in cols:
        cols["predecessor_l1_commitment"] = predecessor_l1(cur, ev_table, cols)
    names = ", ".join(cols)
    ph = ", ".join(["%s"] * len(cols))
    cur.execute(f"INSERT INTO public.{ev_table} ({names}) VALUES ({ph}) RETURNING id, recorded_at", list(cols.values()))
    return cur.fetchone()


def refresh(cur, token, anchor_id, state_at=T0):
    caller = sys._getframe(1)
    call_site = f"{caller.f_code.co_filename}:{caller.f_lineno} in {caller.f_code.co_name}"
    try:
        cur.execute(
            "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
            (token, anchor_id, state_at),
        )
        return cur.fetchone()[0]
    except DatabaseError as exc:
        if "coordinates must satisfy state_at <= known_at <= now" not in str(exc):
            raise
        raise RuntimeError(
            "S015 refresh clock refusal: "
            f"state_at={state_at!r}, known_at=statement clock_timestamp(), "
            f"call_site={call_site}, original_error={exc!r}"
        ) from exc


def seed_test_gateway(
    cur,
    ids,
    at=T0,
    *,
    include_governance_configuration=False,
    include_settings=True,
    include_question=True,
):
    """Test-only version/effect rows with an explicitly labelled fixture name; never a production LO-name source."""
    cur.execute("INSERT INTO core_loapplicationgateway (living_organism_id) VALUES (%s) RETURNING id", [ids["lo"]])
    result = {}
    event_id = ids["events"]["core_livingorganismevent"]
    version_specs = []
    if include_settings:
        version_specs.append((
            "core_loapplicationsettingsversion", "EFFECT_APPLICATION_SETTINGS", "application-settings-version:",
            {"applications_open": True, "automatic_acceptance": False, "processing_responsibility": "LO"},
        ))
    if include_governance_configuration:
        version_specs.insert(1, (
            "core_logovernanceconfigurationversion", "EFFECT_GOVERNANCE_CONFIGURATION",
            "governance-configuration-version:",
            {"cq_active": False, "inherit_portal_cq_settings": False,
             "require_cq_to_change_lo_settings": False, "require_cq_to_change_application_question": False},
        ))
    if include_question:
        version_specs.append((
            "core_loapplicationquestionversion", "EFFECT_APPLICATION_QUESTION", "application-question-version:",
            {"question_text": "Why do you want to join TEST-FIXTURE-NAME: Riverside Commons?",
             "answer_format": "LONG_ANSWER", "options": None},
        ))
    version_uuids = {}
    for table, action, prefix, values in version_specs:
        version_uuid = str(uuid.uuid4())
        columns = {"version_uuid": version_uuid, "living_organism_id": ids["lo"],
                   "actor_id": ids["identity"], "enterer_id": ids["identity"],
                   "occurred_at": at, "effective_at": at, "received_at": at, **values}
        cur.execute(f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING id", list(columns.values()))
        result[table] = cur.fetchone()[0]
        version_uuids[table] = version_uuid
        cur.execute("SELECT sequence, resulting_state FROM core_livingorganismevent WHERE id = %s", [event_id])
        sequence, state = cur.fetchone()
        event_id, _ = append_event(
            cur, "core_livingorganismevent", ids["lo"], sequence + 1, action,
            state, state, None, ids["basis"], fresh(), pred_id=event_id,
            eff=at, occ=at, rec=at,
            extra={"actor_state": "LIVING_ORGANISM", "actor_access_epoch": None,
                   "enterer_identity_id": ids["identity"], "evidence_reference": prefix + version_uuid},
        )
        refresh(cur, "core_livingorganism", ids["lo"], at)
    if include_governance_configuration:
        if include_settings:
            result["settings_basis"] = (
                f"governance-configuration-version:{version_uuids['core_logovernanceconfigurationversion']};"
                f"application-settings-version:{version_uuids['core_loapplicationsettingsversion']}"
            )
        cur.execute("SELECT sequence FROM core_livingorganismevent WHERE id = %s", [event_id])
        result["head_sequence"] = cur.fetchone()[0]
    result["head_event"] = event_id
    return result


def seed_identity(cur, tag):
    with transaction.atomic():
        username = f"u21g_{tag}"
        display_name = f"U21g {tag}"
        canonical_username = canonical_username_v1(username)
        cur.execute("INSERT INTO auth_user (password,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined) "
                    "VALUES ('!unusable', false, %s, '', '', '', false, true, now()) RETURNING id", (username,))
        user_id = cur.fetchone()[0]
        cur.execute("INSERT INTO core_identity (display_name, created_at, credential_id, identity_id, canonical_username, access_state, access_epoch, updated_at) "
                    "VALUES (%s, now(), %s, %s, %s, 'active', 0, now()) RETURNING id", (display_name, user_id, u(f"identity:{tag}"), canonical_username))
        identity_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO core_identityresolution "
            "(identity_id, display_name, canonical_username, credential_link) "
            "VALUES (%s, %s, %s, %s)",
            (identity_id, display_name, canonical_username, user_id),
        )
        return identity_id


def create_test_identity(*, using="default", username=None, **identity_fields):
    credential = identity_fields.pop("credential", None)
    credential_id = identity_fields.pop("credential_id", None)
    if credential is not None and credential_id is not None:
        raise ValueError("provide credential or credential_id, not both")

    with transaction.atomic(using=using):
        user_manager = User.objects.db_manager(using)
        if credential is None:
            if credential_id is not None:
                credential = user_manager.get(pk=credential_id)
            else:
                if not username:
                    raise ValueError("username is required when creating a test credential")
                is_active = (
                    identity_fields.get("access_state", Identity.AccessState.ACTIVE)
                    != Identity.AccessState.DEACTIVATED
                )
                credential = user_manager.create_user(
                    username=username,
                    password=None,
                    is_active=is_active,
                )

        identity = Identity.objects.db_manager(using).create(
            credential=credential,
            **identity_fields,
        )
        with connections[using].cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.core_identityresolution "
                "(identity_id, display_name, canonical_username, credential_link) "
                "VALUES (%s, %s, %s, %s)",
                (
                    identity.pk,
                    identity.display_name,
                    identity.canonical_username,
                    identity.credential_id,
                ),
            )
        return identity


def registration_terms_version_in_force(
    *,
    effective_at,
    received_at,
):
    eligible_versions = PlatformTermsVersion.objects.filter(
        effective_from__lte=effective_at,
        recorded_at__lte=received_at,
    ).filter(
        Q(effective_until__isnull=True)
        | Q(effective_until__gt=effective_at)
    ).order_by("-effective_from", "-recorded_at", "-pk")
    for candidate in eligible_versions:
        current = LearnerRegistrationService._current_terms_version(
            terms_version_uuid=candidate.terms_version_uuid,
            effective_at=effective_at,
            received_at=received_at,
        )
        if current is not None:
            return current
    return None


def _registration_refusal_with_receipt_details(error):
    if "Registration receipt diagnostics:" in str(error):
        return error
    version = error.submitted_version
    details = (
        f"terms_version_uuid="
        f"{error.submitted_version_uuid}; "
        f"effective_from={version.effective_from if version is not None else None}; "
        f"effective_until={version.effective_until if version is not None else None}; "
        f"recorded_at={version.recorded_at if version is not None else None}; "
        f"effective_at={error.effective_at}; received_at={error.received_at}"
    )
    return RegistrationServiceRefused(
        error.code,
        f"{error.message}\nRegistration receipt diagnostics: {details}",
        reason=error.reason,
        submitted_version=version,
        submitted_version_uuid=error.submitted_version_uuid,
        current_version=error.current_version,
        effective_at=error.effective_at,
        received_at=error.received_at,
    )


def register_with_current_terms(**registration_kwargs):
    if registration_kwargs.get("terms_version_uuid") is None:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            selection_time = cursor.fetchone()[0]
        current_terms = registration_terms_version_in_force(
            effective_at=selection_time,
            received_at=selection_time,
        )
        registration_kwargs["terms_version_uuid"] = (
            current_terms.terms_version_uuid if current_terms is not None else None
        )
    try:
        return LearnerRegistrationService.register(**registration_kwargs)
    except RegistrationServiceRefused as error:
        raise _registration_refusal_with_receipt_details(error) from error


def register_test_learner(
    *,
    username,
    password="governed-test-password",
    display_name=None,
    terms_version=None,
    correlation_id=None,
):
    _require_disposable_fixture_database()
    if terms_version is None:
        if not PlatformTermsVersion.objects.exists():
            now = database_now()
            terms_body = "Labelled FIXTURE-DEV-1 registration terms."
            PlatformTermsVersion.objects.create(
                terms_version_uuid=uuid.uuid4(),
                version_label="FIXTURE-DEV-1",
                text_commitment="s015r1:" + hashlib.sha256(
                    terms_body.encode("utf-8")
                ).hexdigest(),
                text_body=terms_body,
                cited_rulings=[],
                published_by_authority_reference="fixture:development",
                is_fixture=True,
                effective_from=now - dt.timedelta(days=1),
            )
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            now = cursor.fetchone()[0]
        terms_version = registration_terms_version_in_force(
            effective_at=now,
            received_at=now,
        )
    try:
        registration = register_with_current_terms(
        username=username,
        password=password,
        display_name=display_name or username,
        terms_version_uuid=(
            terms_version.terms_version_uuid if terms_version is not None else None
        ),
        terms_accepted=True,
        correlation_id=correlation_id or uuid.uuid4(),
        entry_mode=LearnerRegistration.EntryMode.SUPPORTED,
        enterer_executor="test:registration_fixture",
        )
    except RegistrationServiceRefused as error:
        detailed_error = _registration_refusal_with_receipt_details(error)
        if detailed_error is error:
            raise
        raise detailed_error from error
    return Identity.objects.select_related("credential").get(
        pk=registration.identity_id
    )


def seed_authority(cur, tag, issuer_id):
    cur.execute("INSERT INTO core_authorityprincipal (principal_uuid, canonical_governed_source_id, governed_source_namespace, display_label, recorded_at) "
                "VALUES (%s, %s, 'U21G_THROWAWAY', %s, now()) RETURNING id", (u(f"principal:{tag}"), f"urn:intevia:governed-source:u21g:{tag}", f"U21g principal {tag}"))
    principal_id = cur.fetchone()[0]
    cur.execute("INSERT INTO core_authoritybasis (basis_uuid, authority_class, scope_fingerprint, instrument_reference, evidence_reference, issued_at, effective_at, received_at, recorded_at, issuer_id, authority_principal_id, derivation_root_principal_id) "
                "VALUES (%s, 'U21G_THROWAWAY', %s, %s, %s, now(), now(), now(), now(), %s, %s, %s) RETURNING id",
                (u(f"basis:{tag}"), hx(f"scope:{tag}"), f"u21g:instrument:{tag}", f"u21g:evidence:{tag}", issuer_id, principal_id, principal_id))
    return principal_id, cur.fetchone()[0]


def seed_fixture_substitute_authority(cur, tag):
    if not settings.INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED:
        raise RuntimeError("labelled fixture authority seeding is not permitted")
    _require_disposable_fixture_database()
    cur.execute(
        "INSERT INTO public.core_authorityprincipal "
        "(principal_uuid, canonical_governed_source_id, governed_source_namespace, display_label, recorded_at) "
        "VALUES (%s, %s, 'INTEVIA_FIXTURE_AUTHORITY_V1', %s, now()) "
        "RETURNING id, principal_uuid",
        (
            u(f"fixture-authority-principal:{tag}"),
            f"urn:intevia:governed-source:fixture-authority:{tag}",
            f"Labelled fixture substitute {tag}",
        ),
    )
    principal_id, principal_uuid = cur.fetchone()
    return {"id": principal_id, "principal_uuid": str(principal_uuid)}


def drop_unset_check(cur):
    cur.execute("ALTER TABLE public.core_livingorganism DROP CONSTRAINT IF EXISTS s015_platform_root_unset_ck")


def restore_unset_check(cur):
    cur.execute("ALTER TABLE public.core_livingorganism ADD CONSTRAINT s015_platform_root_unset_ck CHECK (NOT platform_root)")


def found_organism(
    cur,
    tag,
    root,
    codes=None,
    active_versions=None,
    skip=frozenset(),
    do_refresh=True,
    assign_founder=None,
    assignment_states=None,
    skip_refresh=frozenset(),
    identity_id=None,
    founding_assignment_overrides=None,
):
    """Complete founding population for organism `tag`.

    root=True → six active definitions, INTEVIA_FOUNDER_STEWARD and INTEVIA_PRIVACY_COORDINATOR assignments on the founder membership.
    root=False → an active COORDINATOR definition only (specification v0.8 §5.5.1 forbids the reserved five).
    codes/active_versions override the definition inventory: {code: [(version, active), ...]}.
    skip names limbs to omit for negative tests. assign_founder overrides whether INTEVIA_FOUNDER_STEWARD is assigned.
    """
    _require_disposable_fixture_database()
    ids = {
        "identity": seed_identity(cur, tag) if identity_id is None else identity_id,
        "events": {},
        "assignment_events": {},
    }
    ids["principal"], ids["basis"] = seed_authority(cur, tag, ids["identity"])
    lo_uuid = u(f"organism:{tag}")
    cur.execute("INSERT INTO core_livingorganism (organism_id, slug, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, constitutional_spine_reference, platform_root) "
                "VALUES (%s, %s, 'FOUNDING_PENDING', now(), %s, %s, %s, %s, %s) RETURNING id",
                (lo_uuid, f"u21g-{tag}", T0, T0, empty_fp(cur, "core_livingorganism", lo_uuid), f"u21g:spine:{tag}", root))
    ids["lo"] = lo = cur.fetchone()[0]
    act, basis = ids["identity"], ids["basis"]
    cur.execute(
        "SELECT to_regclass('public.core_platformtermsversion'), "
        "to_regclass('public.core_platformtermsacceptance')"
    )
    terms_version_table, terms_acceptance_table = cur.fetchone()
    if (terms_version_table is None) != (terms_acceptance_table is None):
        raise RuntimeError("fixture platform-terms tables are only partially installed")
    if terms_version_table is not None:
        cur.execute(
            "SELECT id, is_fixture, published_by_authority_reference "
            "FROM public.core_platformtermsversion "
            "WHERE version_label = %s ORDER BY id LIMIT 1",
            ("FIXTURE-DEV-1",),
        )
        terms_version = cur.fetchone()
        if terms_version is None:
            terms_body = "Labelled development-only fixture terms for disposable tests."
            cur.execute(
                "INSERT INTO public.core_platformtermsversion "
                "(terms_version_uuid, version_label, text_commitment, text_body, cited_rulings, "
                "published_by_authority_reference, is_fixture, effective_from) "
                "VALUES (%s, 'FIXTURE-DEV-1', %s, %s, '[]'::jsonb, "
                "'fixture:development', TRUE, %s) RETURNING id",
                (
                    u("platform-terms-version:FIXTURE-DEV-1"),
                    "s015r1:" + hx(terms_body),
                    terms_body,
                    T0,
                ),
            )
            terms_version_id = cur.fetchone()[0]
        else:
            terms_version_id, is_fixture, publisher_reference = terms_version
            if not is_fixture or publisher_reference != "fixture:development":
                raise RuntimeError("FIXTURE-DEV-1 must remain a labelled fixture terms version")
        if "terms_acceptance" not in skip:
            cur.execute("SELECT access_epoch FROM public.core_identity WHERE id = %s", (act,))
            founder_access_epoch = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.core_platformtermsacceptance "
                "(acceptance_uuid, terms_version_id, acceptance_kind, subject_reference, "
                "accepted_by_identity_id, accepted_by_access_epoch, accepted_at, received_at, "
                "acceptance_commitment) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    u(f"platform-terms-acceptance:{lo_uuid}"),
                    terms_version_id,
                    "GENESIS" if root else "FOUNDING",
                    f"living-organism:{lo_uuid}",
                    act,
                    founder_access_epoch,
                    T0,
                    T0,
                    "s015r1:" + hx(f"fixture-acceptance:{lo_uuid}"),
                ),
            )
    if "lo_event" not in skip:
        ids["lo_event"] = append_event(cur, "core_livingorganismevent", lo, 1, "FOUND", None, "ACTIVE", act, basis, tag)[0]
        ids["events"]["core_livingorganismevent"] = ids["lo_event"]
        cur.execute(
            "SELECT event_uuid, authority_decision_reference "
            "FROM public.core_livingorganismevent WHERE id = %s",
            (ids["lo_event"],),
        )
        ids["founding_event_uuid"], ids["founding_authority_decision_reference"] = cur.fetchone()
    m_uuid = u(f"membership:{tag}")
    cur.execute("INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
                "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id", (m_uuid, T0, T0, empty_fp(cur, "core_organismmembership", m_uuid), act, lo))
    ids["membership"] = cur.fetchone()[0]
    if "membership_event" not in skip:
        ids["events"]["core_organismmembershiptransition"] = append_event(
            cur, "core_organismmembershiptransition", ids["membership"], 1, "JOIN", None, "ACTIVE", act, basis, tag,
            extra={"reason_class": "FOUNDING"}
        )[0]
    inventory = active_versions or {c: [(1, True)] for c in (codes if codes is not None else (SIX_CODES if root else ["COORDINATOR"]))}
    ids["roles"] = {}
    for code, versions in inventory.items():
        if code in skip:
            continue
        for version, active in versions:
            cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) "
                        "VALUES (%s, %s, 'LIVING_ORGANISM', %s, %s, now(), %s) RETURNING id", (u(f"role:{tag}:{code}:{version}"), code, version, active, lo))
            ids["roles"].setdefault(code, cur.fetchone()[0])
    ids["assignments"] = {}
    if assign_founder is None:
        assign_founder = root
    assignment_states = assignment_states or {}
    to_assign = (
        (["INTEVIA_FOUNDER_STEWARD"] if assign_founder else [])
        + (["INTEVIA_PRIVACY_COORDINATOR"] if root and "pc_assignment" not in skip else [])
        + (["COORDINATOR"] if not root else [])
    )
    for code in [c for c in to_assign if c in ids["roles"]]:
        assignment_state = assignment_states.get(code, "ACTIVE")
        a_uuid = u(f"assignment:{tag}:{code}")
        cur.execute("INSERT INTO core_contextualroleassignment (assignment_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, membership_id, role_definition_id) "
                    "VALUES (%s, %s, now(), %s, %s, %s, %s, %s) RETURNING id", (a_uuid, assignment_state, T0, T0, empty_fp(cur, "core_contextualroleassignment", a_uuid), ids["membership"], ids["roles"][code]))
        ids["assignments"][code] = cur.fetchone()[0]
        if "assignment_event" not in skip:
            assignment_event_overrides = None
            if code == "COORDINATOR":
                if "founding_event_uuid" not in ids:
                    raise RuntimeError(
                        "founding Coordinator ASSIGN requires the sequence-1 FOUND event"
                    )
                assignment_event_overrides = {
                    "evidence_reference": f"founding-batch:{ids['founding_event_uuid']}",
                    "authority_decision_reference": ids[
                        "founding_authority_decision_reference"
                    ],
                }
                assignment_event_overrides.update(founding_assignment_overrides or {})
            event_id = append_event(
                cur, "core_contextualroleassignmentstateevent", ids["assignments"][code], 1, "ASSIGN", None,
                assignment_state, act, basis, f"{tag}:{code}", overrides=assignment_event_overrides
            )[0]
            ids["assignment_events"][code] = event_id
            ids["events"].setdefault("core_contextualroleassignmentstateevent", event_id)
    if "requirement" not in skip:
        cur.execute("INSERT INTO core_essentialcoveragerequirement (requirement_uuid, roster_version, responsibility_domain_code, qualifying_role_code, minimum_active_occupants, scope, effective_from, effective_until, living_organism_id) "
                    "VALUES (%s, 1, 'GOVERNANCE', 'COORDINATOR', 1, 'LIVING_ORGANISM', %s, NULL, %s) RETURNING id", (u(f"req:{tag}"), T_MINUS_1D, lo))
        ids["requirement"] = cur.fetchone()[0]
    if "coverage_case" not in skip:
        cur.execute("INSERT INTO core_essentialcoveragecase (case_uuid, living_organism_id) VALUES (%s, %s) RETURNING id", (u(f"covcase:{tag}"), lo))
        ids["coverage_case"] = cur.fetchone()[0]
        if "assessment" not in skip:
            ids["assessment"] = append_event(cur, "core_coverageassessment", ids["coverage_case"], 1, "ASSESS", None, "SATISFIED", act, basis, tag,
                                             extra={"roster_version": 1, "evaluated_state_at": T0, "evaluated_known_at": T0, "determiner_capacity_reference": f"u21g:capacity:{tag}"})[0]
            ids["events"]["core_coverageassessment"] = ids["assessment"]
    if do_refresh:
        if "core_livingorganism" not in skip_refresh:
            refresh(cur, "core_livingorganism", lo)
        if "core_organismmembership" not in skip_refresh:
            refresh(cur, "core_organismmembership", ids["membership"])
        if "core_contextualroleassignment" not in skip_refresh:
            for a in ids["assignments"].values():
                refresh(cur, "core_contextualroleassignment", a)
    if "completion" not in skip:
        cur.execute("UPDATE core_livingorganism SET founding_authority_basis_id=%s, founder_identity_id=%s WHERE id=%s", (basis, act, lo))
    return ids


def add_remaining_aggregates(cur, ids, tag, do_refresh=True):
    """Circle, condition, invalidation, contest, continuity, grant, obligation, planning — one anchor + one event each."""
    lo, act, basis = ids["lo"], ids["identity"], ids["basis"]
    c_uuid = u(f"circle:{tag}")
    cur.execute("INSERT INTO core_circle (circle_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, founding_reference, parent_organism_id) "
                "VALUES (%s, 'DORMANT', now(), %s, %s, %s, %s, %s) RETURNING id", (c_uuid, T0, T0, empty_fp(cur, "core_circle", c_uuid), f"u21g:circle:{tag}", lo))
    ids["circle"] = cur.fetchone()[0]
    ids["events"]["core_circlestateevent"] = append_event(
        cur, "core_circlestateevent", ids["circle"], 1, "CREATE_DORMANT", None, "DORMANT", act, basis, tag
    )[0]
    if do_refresh:
        refresh(cur, "core_circle", ids["circle"])
    cur.execute("INSERT INTO core_membershipcondition (condition_uuid, condition_kind, subject_membership_id, mentor_membership_id) VALUES (%s, 'PROBATION', %s, NULL) RETURNING id", (u(f"cond:{tag}"), ids["membership"]))
    ids["condition"] = cur.fetchone()[0]
    ids["condition_event"] = append_event(cur, "core_membershipconditionstateevent", ids["condition"], 1, "OPEN", None, "ACTIVE", act, basis, tag)[0]
    ids["events"]["core_membershipconditionstateevent"] = ids["condition_event"]
    ids["events"]["core_authorityinvalidationevent"] = append_event(
        cur, "core_authorityinvalidationevent", basis, 1, "SUSPEND", None, "SUSPENDED", act, basis, tag,
        extra={"kind": "SUSPENSION"}
    )[0]
    cur.execute("INSERT INTO core_governeddetermination (determination_uuid, determination_type, result, evidence_reference, method_reference, scope_fingerprint, independence_state, occurred_at, effective_at, received_at, recorded_at, determiner_id, relied_upon_authority_principal_id) "
                "VALUES (%s, 'INDEPENDENCE_DETERMINATION', 'U21G', 'u21g:d', 'u21g:m', %s, 'U21G', now(), now(), now(), now(), %s, %s) RETURNING id", (u(f"det:{tag}"), hx(f"det:{tag}"), act, ids["principal"]))
    ids["determination"] = cur.fetchone()[0]
    cur.execute("INSERT INTO core_determinationcontestcase (case_uuid, determination_id) VALUES (%s, %s) RETURNING id", (u(f"contest:{tag}"), ids["determination"]))
    ids["contest_case"] = cur.fetchone()[0]
    ids["events"]["core_determinationcontestevent"] = append_event(
        cur, "core_determinationcontestevent", ids["contest_case"], 1, "CONTEST", None, "CONTESTED", act, basis, tag
    )[0]
    cur.execute("INSERT INTO core_restrictedcontinuitycase (case_uuid, living_organism_id) VALUES (%s, %s) RETURNING id", (u(f"rcc:{tag}"), lo))
    ids["continuity_case"] = cur.fetchone()[0]
    ids["events"]["core_restrictedcontinuityevent"] = append_event(
        cur, "core_restrictedcontinuityevent", ids["continuity_case"], 1, "ENTER", None, "NOT_RESTRICTED", act, basis,
        tag, extra={"permitted_measures_reference": "u21g:permitted", "prohibited_effects_reference": "u21g:prohibited",
                    "triggering_assessment_id": ids["assessment"], "accountable_actor_id": act}
    )[0]
    cur.execute("INSERT INTO core_governedvisibilitygrant (grant_uuid, subject_type, subject_id, audience_type, audience_id, capacity_binding, fields_permitted, purpose_reference, expiry, lineage_reference, recorded_at, granting_authority_id) "
                "VALUES (%s, 'ORGANISM', %s, 'IDENTITY', %s, 'u21g:capacity', '[]'::jsonb, 'u21g:purpose', now() + interval '30 days', %s, now(), %s) RETURNING id",
                (u(f"grant:{tag}"), u(f"organism:{tag}"), u(f"identity:{tag}"), "s015l1:" + hx(f"grantlineage:{tag}"), basis))
    ids["grant"] = cur.fetchone()[0]
    ids["events"]["core_visibilitygrantstateevent"] = append_event(
        cur, "core_visibilitygrantstateevent", ids["grant"], 1, "ISSUE", None, "ISSUED", act, basis, tag
    )[0]
    o_uuid = u(f"obligation:{tag}")
    cur.execute("INSERT INTO core_governedobligationcase (case_uuid, asserted_obligation_reference, affected_scope_reference, responsible_capacities_reference, operational_escalation_at, status_state, legal_deadline_status, qualified_legal_deadline, effective_deadline, status_state_at, status_known_at, status_source_event_set_fingerprint, legal_basis_determination_id) "
                "VALUES (%s, 'u21g:obligation', 'u21g:scope', 'u21g:capacities', %s, 'OBLIGATION_BLOCKED_PENDING_AUTHORITY', 'UNKNOWN', NULL, NULL, %s, %s, %s, NULL) RETURNING id",
                (o_uuid, T0 + dt.timedelta(days=30), T0, T0, empty_fp(cur, "core_governedobligationcase", o_uuid)))
    ids["obligation_case"] = cur.fetchone()[0]
    ids["events"]["core_obligationstateevent"] = append_event(
        cur, "core_obligationstateevent", ids["obligation_case"], 1, "ASSERT", None,
        "OBLIGATION_BLOCKED_PENDING_AUTHORITY", act, basis, tag, extra={"legal_deadline_status": "UNKNOWN"}
    )[0]
    if do_refresh:
        refresh(cur, "core_governedobligationcase", ids["obligation_case"])
    cur.execute("INSERT INTO core_planningclassificationcase (case_uuid, item_identifier) VALUES (%s, %s) RETURNING id", (u(f"plan:{tag}"), f"u21g:item:{tag}"))
    ids["planning_case"] = cur.fetchone()[0]
    ids["events"]["core_planningclassificationevent"] = append_event(
        cur, "core_planningclassificationevent", ids["planning_case"], 1, "CLASSIFY", None, "ROUTINE_ELIGIBLE", act,
        basis, tag, extra={"criterion_reference": "u21g:criterion"}
    )[0]
    return ids


def append_second_event(cur, ids, event_table, tag, *, prior=DEFAULT_PRIOR, eff=None, overrides=None):
    """Append a lawful sequence-2 row for any one of the twelve recorded-chain tables."""
    assignment_id = next(iter(ids["assignments"].values()))
    cases = {
        "core_livingorganismevent": (ids["lo"], "RESTRICT", "ACTIVE", "DORMANT", {}),
        "core_circlestateevent": (ids["circle"], "ELIGIBLE", "DORMANT", "ELIGIBLE", {}),
        "core_organismmembershiptransition": (ids["membership"], "SUSPEND", "ACTIVE", "SUSPENDED", {"reason_class": "TEST"}),
        "core_contextualroleassignmentstateevent": (assignment_id, "SUSPEND", "ACTIVE", "SUSPENDED", {}),
        "core_membershipconditionstateevent": (ids["condition"], "CLOSE", "ACTIVE", "ENDED", {}),
        "core_authorityinvalidationevent": (ids["basis"], "RESTORE", "SUSPENDED", "RESTORED", {"kind": "RESTORATION"}),
        "core_determinationcontestevent": (ids["contest_case"], "RESOLVE", "CONTESTED", "RESOLVED", {}),
        "core_coverageassessment": (
            ids["coverage_case"], "REASSESS", "SATISFIED", "UNSATISFIED",
            {"roster_version": 1, "evaluated_state_at": T0, "evaluated_known_at": T0,
             "determiner_capacity_reference": f"u21g:capacity:{tag}"},
        ),
        "core_restrictedcontinuityevent": (
            ids["continuity_case"], "RESTRICT", "NOT_RESTRICTED", "RESTRICTED",
            {"permitted_measures_reference": "u21g:permitted", "prohibited_effects_reference": "u21g:prohibited",
             "triggering_assessment_id": ids["assessment"], "accountable_actor_id": ids["identity"]},
        ),
        "core_visibilitygrantstateevent": (ids["grant"], "REVOKE", "ISSUED", "REVOKED", {}),
        "core_obligationstateevent": (
            ids["obligation_case"], "REASSERT", "OBLIGATION_BLOCKED_PENDING_AUTHORITY",
            "OBLIGATION_BLOCKED_PENDING_AUTHORITY", {"legal_deadline_status": "UNKNOWN"},
        ),
        "core_planningclassificationevent": (
            ids["planning_case"], "RECLASSIFY", "ROUTINE_ELIGIBLE", "UNKNOWN",
            {"criterion_reference": "u21g:criterion"},
        ),
    }
    anchor_id, action, default_prior, result, extra = cases[event_table]
    event_time = eff or (T0 + dt.timedelta(hours=1))
    return append_event(
        cur,
        event_table,
        anchor_id,
        2,
        action,
        default_prior if prior is DEFAULT_PRIOR else prior,
        result,
        ids["identity"],
        ids["basis"],
        tag,
        pred_id=ids["events"][event_table],
        eff=event_time,
        occ=event_time,
        rec=T0 + dt.timedelta(hours=1),
        extra=extra,
        overrides=overrides,
    )


def found_root(cur, tag=None, **kw):
    """Root founding on the disposable database (unset CHECK must already be dropped by the test class)."""
    return found_organism(cur, tag or f"root-{fresh()}", root=True, **kw)


def found_nonroot(cur, tag=None, **kw):
    """Found a non-root organism through the 0021-scoped shipped completion guardian."""
    return found_organism(cur, tag or f"nonroot-{fresh()}", root=False, **kw)


def first_line(exc):
    return f"{type(exc).__name__}: {str(exc).strip().splitlines()[0]}"
