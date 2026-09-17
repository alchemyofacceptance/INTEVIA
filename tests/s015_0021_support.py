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
import uuid

from django.db import connection

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
    cur.execute("SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())", (token, anchor_id, state_at))
    return cur.fetchone()[0]


def seed_identity(cur, tag):
    cur.execute("INSERT INTO auth_user (password,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined) "
                "VALUES ('!unusable', false, %s, '', '', '', false, true, now()) RETURNING id", (f"u21g_{tag}",))
    user_id = cur.fetchone()[0]
    cur.execute("INSERT INTO core_identity (display_name, created_at, credential_id, identity_id, canonical_username, access_state, access_epoch, updated_at) "
                "VALUES (%s, now(), %s, %s, %s, 'active', 0, now()) RETURNING id", (f"U21g {tag}", user_id, u(f"identity:{tag}"), f"u21g_{tag}"))
    return cur.fetchone()[0]


def seed_authority(cur, tag, issuer_id):
    cur.execute("INSERT INTO core_authorityprincipal (principal_uuid, canonical_governed_source_id, governed_source_namespace, display_label, recorded_at) "
                "VALUES (%s, %s, 'U21G_THROWAWAY', %s, now()) RETURNING id", (u(f"principal:{tag}"), f"urn:intevia:governed-source:u21g:{tag}", f"U21g principal {tag}"))
    principal_id = cur.fetchone()[0]
    cur.execute("INSERT INTO core_authoritybasis (basis_uuid, authority_class, scope_fingerprint, instrument_reference, evidence_reference, issued_at, effective_at, received_at, recorded_at, issuer_id, authority_principal_id, derivation_root_principal_id) "
                "VALUES (%s, 'U21G_THROWAWAY', %s, %s, %s, now(), now(), now(), now(), %s, %s, %s) RETURNING id",
                (u(f"basis:{tag}"), hx(f"scope:{tag}"), f"u21g:instrument:{tag}", f"u21g:evidence:{tag}", issuer_id, principal_id, principal_id))
    return principal_id, cur.fetchone()[0]


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
):
    """Complete founding population for organism `tag`.

    root=True → six active definitions, INTEVIA_FOUNDER_STEWARD and INTEVIA_PRIVACY_COORDINATOR assignments on the founder membership.
    root=False → an active COORDINATOR definition only (specification v0.8 §5.5.1 forbids the reserved five).
    codes/active_versions override the definition inventory: {code: [(version, active), ...]}.
    skip names limbs to omit for negative tests. assign_founder overrides whether INTEVIA_FOUNDER_STEWARD is assigned.
    """
    ids = {"identity": seed_identity(cur, tag), "events": {}, "assignment_events": {}}
    ids["principal"], ids["basis"] = seed_authority(cur, tag, ids["identity"])
    lo_uuid = u(f"organism:{tag}")
    cur.execute("INSERT INTO core_livingorganism (organism_id, slug, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, constitutional_spine_reference, platform_root) "
                "VALUES (%s, %s, 'FOUNDING_PENDING', now(), %s, %s, %s, %s, %s) RETURNING id",
                (lo_uuid, f"u21g-{tag}", T0, T0, empty_fp(cur, "core_livingorganism", lo_uuid), f"u21g:spine:{tag}", root))
    ids["lo"] = lo = cur.fetchone()[0]
    act, basis = ids["identity"], ids["basis"]
    if "lo_event" not in skip:
        ids["lo_event"] = append_event(cur, "core_livingorganismevent", lo, 1, "FOUND", None, "ACTIVE", act, basis, tag)[0]
        ids["events"]["core_livingorganismevent"] = ids["lo_event"]
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
            event_id = append_event(
                cur, "core_contextualroleassignmentstateevent", ids["assignments"][code], 1, "ASSIGN", None,
                assignment_state, act, basis, f"{tag}:{code}"
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
