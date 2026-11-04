"""C3 authority lifecycle, 00_COMMISSION_C3_v1_1.md.
Install the S-backed case and whole-instrument guards on the existing immutable chains.
"""
from importlib import import_module

from django.db import migrations, models
import django.db.models.deletion


SQL = """
CREATE FUNCTION public.c3_term(office bigint, at_time timestamptz, known timestamptz)
RETURNS bigint LANGUAGE sql STABLE STRICT AS $$
 SELECT authority_basis_id FROM public.core_contextualroleassignmentstateevent
 WHERE assignment_id=office AND action='ASSIGN' AND effective_at<=at_time AND received_at<=known AND recorded_at<=known
 ORDER BY effective_at DESC, occurred_at DESC, received_at DESC, recorded_at DESC, sequence DESC LIMIT 1
$$;
CREATE FUNCTION public.c3_seat(app bigint, office bigint, person bigint, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS (SELECT FROM public.core_application a
 CROSS JOIN LATERAL public.c2_seat_facts(a.living_organism_id,at_time,known) f
 JOIN public.core_organismmembership m ON m.id=f.membership_id
 WHERE a.id=app AND f.assignment_id=office AND m.identity_id=person AND f.role_scope='LIVING_ORGANISM'
 AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
 AND public.c2_basis_in_force(public.c3_term(office,at_time,known),at_time,known))
$$;
-- The first engagement is already immutable. Its assignment UUID identifies the
-- original route and both versions without putting long rule strings in S.
CREATE FUNCTION public.c3_act(app bigint, purpose text) RETURNS text LANGUAGE sql STABLE STRICT AS $$
 SELECT 'C3:'||engagement_reference||':'||purpose FROM public.core_applicationengagement
 WHERE application_id=app AND engagement_ordinal=1 AND route='LO_CSA'
$$;
-- Canonical S JSON, in the same key order as Scope.fingerprint(). The act
-- and kind come from the closed C3 vocabulary and governed UUID references.
CREATE FUNCTION public.c3_scope(act text, basis uuid, beneficiary bigint, kind text, lo uuid, target uuid, person bigint)
RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT public.c2_target(format(
 '{"act":"%s","basis":"%s","beneficiary":%s,"kind":"%s","organism":"%s","schema":"S:scope:1","target":"%s","tested":%s}',
 act,basis,beneficiary,kind,lo,target,person))
$$;
CREATE FUNCTION public.c3_findings(app bigint, purpose text, person bigint, kind text,
                                  at_time timestamptz, known timestamptz)
RETURNS SETOF public.core_governeddetermination LANGUAGE sql STABLE STRICT AS $$
 SELECT d.* FROM public.core_application a JOIN public.core_livingorganism lo ON lo.id=a.living_organism_id
 JOIN public.core_governeddetermination d ON d.tested_actor_id=person AND d.safeguard_beneficiary_id=a.applicant_id
 JOIN public.core_authoritybasis b ON b.basis_uuid::text=split_part(d.method_reference,'|',5)
 WHERE a.id=app AND d.determination_type=kind AND d.method_reference LIKE 'S:finding:1|%'
 AND d.scope_fingerprint=public.c3_scope(public.c3_act(app,purpose),b.basis_uuid,a.applicant_id,kind,lo.organism_id,a.application_uuid,person)
 AND d.evidence_reference LIKE 'C3:%' AND split_part(d.evidence_reference,':',3)=a.application_uuid::text
 AND d.effective_at<=at_time AND d.received_at<=known AND d.recorded_at<=known
 ORDER BY d.effective_at DESC,d.occurred_at DESC,d.received_at DESC,d.recorded_at DESC,d.id DESC
$$;
CREATE FUNCTION public.c3_available(app bigint, person bigint, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 -- S validated the act at issuance. Its term bounds the authority to
 -- make the finding. A later expiry does not undo an admitted release.
 SELECT coalesce((SELECT f.result='REFUSED'
    AND mandate.id IS NOT NULL
    AND NOT public.c2_contested(f.id,at_time,known)
    AND NOT public.c2_contested(mandate.id,at_time,known)
 FROM public.c3_findings(app,'STEP_ASIDE',person,'UNAVAILABILITY_DETERMINATION',at_time,known) f
 LEFT JOIN public.core_governeddetermination mandate ON mandate.determination_uuid=split_part(f.method_reference,'|',2)::uuid
 ORDER BY f.effective_at DESC,f.occurred_at DESC,f.received_at DESC,f.recorded_at DESC,f.id DESC LIMIT 1),true)
$$;
CREATE FUNCTION public.c3_independent(b bigint, previous bigint, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 WITH left_side AS (SELECT id FROM public.c2_lineage(b,known)),
 right_side AS (SELECT id FROM public.c2_lineage(previous,known)),
 roots AS (SELECT id FROM left_side UNION SELECT id FROM right_side),
 aliases AS (SELECT DISTINCT ON (candidate_namespace,candidate_governed_source_id) *
 FROM public.core_authorityprincipalaliasreview WHERE recorded_at<=known
 ORDER BY candidate_namespace,candidate_governed_source_id,recorded_at DESC,id DESC)
 SELECT NOT EXISTS (SELECT id FROM left_side INTERSECT SELECT id FROM right_side)
 AND NOT EXISTS (SELECT FROM aliases a WHERE state IN ('AUTHORITY_EQUIVALENCE_UNKNOWN','BOUND_TO_EXISTING_PRINCIPAL')
 AND (a.possible_principal_id IN (SELECT id FROM roots) OR EXISTS
 (SELECT FROM public.core_authorityprincipal p JOIN roots ON roots.id=p.id
 WHERE p.canonical_governed_source_id=a.candidate_governed_source_id)))
$$;
CREATE FUNCTION public.c3_claim_basis(c public.core_csaassignment) RETURNS bigint LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN c.lifecycle_finding_id IS NULL THEN public.c3_term(c.assignee_office_id,c.effective_at,c.recorded_at)
 ELSE (SELECT b.id FROM public.core_authoritybasis b JOIN public.core_governeddetermination d
 ON b.basis_uuid::text=split_part(d.method_reference,'|',5) WHERE d.id=c.lifecycle_finding_id) END
$$;
CREATE FUNCTION public.c3_replacement(app bigint, old_id bigint, office bigint, person bigint, at_time timestamptz, known timestamptz)
RETURNS bigint LANGUAGE sql STABLE STRICT AS $$
 SELECT f.id FROM public.core_csaassignment old JOIN public.core_contextualroleassignment seat ON seat.id=office
 CROSS JOIN LATERAL (SELECT d AS finding FROM public.c3_findings(app,'REPLACE:'||old.assignment_uuid||':'||seat.assignment_uuid,person,
 'EXECUTION_ELIGIBILITY_QUALIFICATION',at_time,known) d
 ORDER BY d.effective_at DESC,d.occurred_at DESC,d.received_at DESC,d.recorded_at DESC,d.id DESC LIMIT 1) latest
 CROSS JOIN LATERAL (SELECT (latest.finding).*) f
 JOIN public.core_authoritybasis b ON b.basis_uuid::text=split_part(f.method_reference,'|',5)
 JOIN public.core_application a ON a.id=app
 WHERE old.id=old_id AND old.application_id=app
 -- CSA-REASSIGN-1, Human Governor, 23 October: the current assignee cannot take over their own claim.
 AND old.actor_id<>person AND a.applicant_id<>person
 AND f.result='QUALIFIED' AND f.determiner_id<>old.actor_id
 AND public.c2_finding_problem(latest.finding,b.basis_uuid,at_time,known) IS NULL
 AND public.c3_independent(b.id,public.c3_claim_basis(old),known)
 AND public.c3_seat(app,office,person,at_time,known) AND public.c3_available(app,person,at_time,known)
$$;
CREATE FUNCTION public.c3_act_problem(c public.core_csaassignment, at_time timestamptz, known timestamptz)
RETURNS text LANGUAGE plpgsql STABLE AS $$
BEGIN
 IF NOT public.c3_seat(c.application_id,c.assignee_office_id,c.actor_id,at_time,known) THEN
    RETURN 'The Coordinator office, membership or its qualifying authority is no longer in force.'; END IF;
 IF NOT public.c3_available(c.application_id,c.actor_id,at_time,known) THEN
    RETURN 'The holder has stepped aside on this case or their availability needs confirmation.'; END IF;
 IF c.replaces_id IS NOT NULL AND (c.lifecycle_finding_id IS NULL OR c.lifecycle_finding_id IS DISTINCT FROM
    public.c3_replacement(c.application_id,c.replaces_id,c.assignee_office_id,c.actor_id,at_time,known)) THEN
    RETURN 'The replacement has no current independent qualification on this case.'; END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION public.c3_pin_valid(app bigint, basis text, party bigint) RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_applicationengagement first WHERE first.application_id=app AND first.engagement_ordinal=1
 AND first.route='LO_CSA' AND first.deciding_party_id=party AND basis=first.settings_basis
 AND basis LIKE 'governance-configuration-version:%' AND public.s3fu_application_settings_basis_valid(
 basis,party,first.effective_at,first.recorded_at) AND NOT EXISTS(SELECT FROM public.core_applicationengagement later
 WHERE later.application_id=app AND (later.settings_basis<>basis OR later.route<>first.route OR later.deciding_party_id<>party)))
$$;
CREATE FUNCTION public.c3_validate_basis_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ref text[]; grant_row public.core_governeddetermination; subject public.core_authoritybasis;
        acting public.core_authoritybasis; probe public.core_governeddetermination; purpose text; reason text;
BEGIN
 IF NEW.kind NOT IN ('SUPERSESSION','INVALIDATION','RESTORATION') THEN RETURN NEW; END IF;
 SELECT * INTO STRICT subject FROM public.core_authoritybasis WHERE id=NEW.basis_id FOR UPDATE;
 IF EXISTS(SELECT FROM public.core_authorityinvalidationevent WHERE basis_id=subject.id
           AND kind IN ('SUPERSESSION','INVALIDATION') AND request_reference LIKE 'C3:%') THEN
    RAISE EXCEPTION 'C3 the whole instrument has already ended' USING ERRCODE='23514'; END IF;
 IF NEW.kind='RESTORATION' THEN RETURN NEW; END IF;
 ref:=string_to_array(NEW.request_reference,':');
 IF NEW.evidence_reference IS NULL OR NEW.evidence_reference NOT LIKE 'C3:%' OR cardinality(ref)<>4 OR ref[1]<>'C3' OR NEW.actor_state<>'PARTY' OR NEW.actor_id IS DISTINCT FROM NEW.enterer_identity_id
 OR NEW.effective_at<>NEW.occurred_at OR NEW.received_at<>NEW.effective_at OR NEW.received_at>statement_timestamp()
 OR NOT EXISTS(SELECT FROM public.core_livingorganism WHERE organism_id=ref[2]::uuid) THEN
    RAISE EXCEPTION 'C3 whole-basis act lacks its exact authority or coordinates' USING ERRCODE='23514'; END IF;
 IF NEW.kind='SUPERSESSION' AND ref[4]~'^[a-f0-9]{64}$' AND NEW.action='SUPERSEDE' AND NEW.resulting_state='SUPERSEDED' THEN
    purpose:='C3:SUPERSEDE:'||ref[4];
 ELSIF NEW.kind='INVALIDATION' AND ref[4]='END' AND NEW.action='INVALIDATE' AND NEW.resulting_state='INVALIDATED' THEN
    purpose:='C3:END_WHOLE_BASIS';
 ELSE RAISE EXCEPTION 'C3 invalid basis-ending act' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT acting FROM public.core_authoritybasis WHERE id=NEW.authority_basis_id;
 SELECT * INTO grant_row FROM public.core_governeddetermination WHERE determination_uuid=ref[3]::uuid;
 probe.scope_fingerprint:=public.c3_scope(purpose,subject.basis_uuid,subject.issuer_id,
 'EXECUTION_ELIGIBILITY_QUALIFICATION',ref[2]::uuid,subject.basis_uuid,subject.issuer_id);
 probe.result:='QUALIFIED'; probe.determination_type:='EXECUTION_ELIGIBILITY_QUALIFICATION';
 probe.effective_at:=NEW.effective_at; probe.occurred_at:=NEW.occurred_at;
 probe.received_at:=NEW.received_at; probe.recorded_at:=statement_timestamp();
 probe.determiner_id:=NEW.actor_id; probe.tested_actor_id:=subject.issuer_id; probe.safeguard_beneficiary_id:=subject.issuer_id;
 probe.independence_state:='INDEPENDENT'; probe.relied_upon_authority_principal_id:=acting.authority_principal_id;
 probe.evidence_reference:=NEW.evidence_reference;
 probe.method_reference:='S:finding:1|'||ref[3]||'|'||acting.basis_uuid||'|'||split_part(grant_row.method_reference,'|',2)||'|'||subject.basis_uuid;
 IF grant_row.id IS NULL THEN RAISE EXCEPTION 'C3 missing whole-instrument mandate' USING ERRCODE='23514'; END IF;
 reason:=coalesce(public.c2_finding_problem(probe,subject.basis_uuid,NEW.effective_at,NEW.received_at),
                  public.c2_finding_problem(probe,subject.basis_uuid,statement_timestamp(),statement_timestamp()));
 IF reason IS NOT NULL THEN RAISE EXCEPTION 'C3 whole-instrument authority refused: %',reason USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.c3_successor_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE hash text;
BEGIN
 IF NEW.superseded_basis_id IS NULL THEN RETURN NEW; END IF;
 NEW.recorded_at:=statement_timestamp();
 SELECT public.c2_target(format(
 '{"authority_class":%s,"basis_uuid":"%s","evidence_reference":%s,"instrument_reference":%s,"principal_uuid":"%s","root_uuid":"%s","scope_fingerprint":%s}',
 to_json(NEW.authority_class),NEW.basis_uuid,to_json(NEW.evidence_reference),to_json(NEW.instrument_reference),
 p.principal_uuid,r.principal_uuid,to_json(NEW.scope_fingerprint))) INTO hash
 FROM public.core_authorityprincipal p,public.core_authorityprincipal r WHERE p.id=NEW.authority_principal_id AND r.id=NEW.derivation_root_principal_id;
 IF NOT EXISTS(SELECT FROM public.core_authorityinvalidationevent e WHERE e.basis_id=NEW.superseded_basis_id AND e.kind='SUPERSESSION'
 AND e.actor_id=NEW.issuer_id AND e.effective_at=NEW.effective_at AND e.effective_at=NEW.issued_at AND e.received_at=NEW.received_at
 AND e.request_reference LIKE 'C3:%' AND split_part(e.request_reference,':',4)=hash)
 THEN RAISE EXCEPTION 'C3 successor lacks the exact authorised supersession' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.c3_complete_supersession() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_authoritybasis b WHERE b.superseded_basis_id=NEW.basis_id AND b.effective_at=NEW.effective_at)
 THEN RAISE EXCEPTION 'C3 supersession lacks its new immutable grant' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION public.c3_lock_sources(app bigint, office bigint, incoming uuid[] DEFAULT '{}'::uuid[]) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 -- End/supersede and S issuance lock these same immutable source rows. The
 -- caller already holds the application. Read the authority again after locking.
 PERFORM b.id FROM public.core_authoritybasis b WHERE b.id IN (
   SELECT term.authority_basis_id FROM public.core_contextualroleassignmentstateevent term
   WHERE term.assignment_id=office AND term.action='ASSIGN'
   UNION SELECT public.c3_claim_basis(c) FROM public.core_csaassignment c WHERE c.application_id=app
 ) OR b.basis_uuid=ANY(incoming) OR b.basis_uuid::text IN (
   SELECT unnest(ARRAY[split_part(d.method_reference,'|',3),split_part(d.method_reference,'|',5)])
   FROM public.core_governeddetermination d JOIN public.core_application a ON a.id=app
   WHERE d.evidence_reference LIKE 'C3:%' AND split_part(d.evidence_reference,':',3)=a.application_uuid::text
 ) ORDER BY b.id FOR UPDATE;
END $$;
CREATE FUNCTION public.c3_case_finding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE app public.core_application; claim public.core_csaassignment; op text; base public.core_authoritybasis;
        person bigint; purpose text; kind text; seat public.core_contextualroleassignment;
        reviewer_basis bigint; fingerprint text; source uuid; lo uuid;
BEGIN
 IF NEW.method_reference NOT LIKE 'S:finding:1|%' THEN RETURN NEW; END IF;
 SELECT * INTO app FROM public.core_application WHERE application_uuid=split_part(NEW.evidence_reference,':',3)::uuid FOR UPDATE;
 IF app.id IS NULL OR EXISTS(SELECT FROM public.core_applicationdecision WHERE application_id=app.id)
    OR EXISTS(SELECT FROM public.core_applicationwithdrawal WHERE application_id=app.id) THEN
    RAISE EXCEPTION 'C3 case finding requires an open application' USING ERRCODE='23514'; END IF;
 SELECT c.* INTO claim FROM public.core_applicationengagement e JOIN public.core_csaassignment c
 ON c.application_id=e.application_id AND c.assignment_uuid::text=e.engagement_reference
 WHERE e.application_id=app.id AND e.route='LO_CSA' AND NOT EXISTS (
 SELECT FROM public.core_csaassignmentend ending WHERE ending.assignment_id=c.id)
 ORDER BY e.engagement_ordinal DESC LIMIT 1;
 IF claim.id IS NULL OR NEW.safeguard_beneficiary_id IS DISTINCT FROM app.applicant_id THEN
    RAISE EXCEPTION 'C3 finding names no current claim or the wrong beneficiary' USING ERRCODE='23514'; END IF;
 SELECT organism_id INTO lo FROM public.core_livingorganism WHERE id=app.living_organism_id;
 op:=split_part(NEW.evidence_reference,':',2); person:=NEW.tested_actor_id;
 source:=split_part(NEW.method_reference,'|',5)::uuid;
 SELECT * INTO STRICT base FROM public.core_authoritybasis WHERE basis_uuid=source;
 IF op='REPLACEMENT' THEN
    SELECT a.* INTO seat FROM public.core_contextualroleassignment a
    JOIN public.core_organismmembership m ON m.id=a.membership_id AND m.identity_id=person
    WHERE m.living_organism_id=app.living_organism_id AND public.c3_seat(app.id,a.id,person,NEW.effective_at,NEW.received_at)
    AND public.c3_seat(app.id,a.id,person,statement_timestamp(),statement_timestamp())
    AND NEW.scope_fingerprint=public.c3_scope(public.c3_act(app.id,'REPLACE:'||claim.assignment_uuid||':'||a.assignment_uuid),
      source,app.applicant_id,'EXECUTION_ELIGIBILITY_QUALIFICATION',lo,app.application_uuid,person);
    IF seat.id IS NULL OR person IN (claim.actor_id,app.applicant_id) OR NEW.determiner_id=claim.actor_id
       OR public.c3_independent(base.id,public.c3_claim_basis(claim),NEW.received_at) IS NOT TRUE
       OR public.c3_independent(base.id,public.c3_claim_basis(claim),statement_timestamp()) IS NOT TRUE THEN
       RAISE EXCEPTION 'C3 replacement lacks its independent source and effective office' USING ERRCODE='23514'; END IF;
    purpose:='REPLACE:'||claim.assignment_uuid||':'||seat.assignment_uuid; kind:='EXECUTION_ELIGIBILITY_QUALIFICATION';
 ELSIF op='PROCEDURAL_REVIEW' THEN
    SELECT id INTO STRICT reviewer_basis FROM public.core_authoritybasis WHERE basis_uuid=split_part(NEW.method_reference,'|',3)::uuid;
    IF person IS DISTINCT FROM claim.actor_id OR NOT public.c3_independent(reviewer_basis,public.c3_claim_basis(claim),NEW.received_at)
       OR NOT public.c3_available(app.id,NEW.determiner_id,NEW.effective_at,NEW.received_at)
       OR NOT public.c3_available(app.id,NEW.determiner_id,statement_timestamp(),statement_timestamp()) THEN
       RAISE EXCEPTION 'C3 procedural reviewer cannot act on this claim' USING ERRCODE='23514'; END IF;
    purpose:='REVIEW:'||claim.assignment_uuid; kind:='INDEPENDENCE_DETERMINATION';
 ELSIF op IN ('RECUSE','UNAVAILABLE','AVAILABLE') THEN
    IF (op='AVAILABLE') IS DISTINCT FROM (NEW.result='REFUSED') THEN
       RAISE EXCEPTION 'C3 availability outcome does not match the recorded act' USING ERRCODE='23514'; END IF;
    purpose:='STEP_ASIDE'; kind:='UNAVAILABILITY_DETERMINATION';
 ELSE RAISE EXCEPTION 'C3 case finding has no recognised purpose' USING ERRCODE='23514'; END IF;
 fingerprint:=public.c3_scope(public.c3_act(app.id,purpose),source,app.applicant_id,kind,lo,app.application_uuid,person);
 IF NEW.scope_fingerprint IS DISTINCT FROM fingerprint OR NEW.determination_type<>kind THEN
    RAISE EXCEPTION 'C3 finding scope differs from the case' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
"""

# Paired source substitutions preserve the rest of the qualified earlier bodies,
# including terminal exclusion, route, time, attribution and self-assignment.
def guards():
    a26 = import_module("core.migrations.0033_s015_application_authority")
    a25 = import_module("core.migrations.0026_s015_application_admission_time")
    claim = next(s for s in a25.guard_bodies(post_lock_time=True) if "FUNCTION public.s3_claim_guard()" in s)
    end = next(s for s in a25.guard_bodies(post_lock_time=True) if "FUNCTION public.s3_end_guard()" in s)
    engage = a26.ENGAGEMENT_BASIS_GUARD_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    decide = a26._decision_guard_sql().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    return [
        (end, "    RETURN NEW;", """    IF NOT EXISTS (SELECT FROM public.core_csaassignment displaced
          JOIN public.core_organismmembership m ON m.identity_id=NEW.actor_id
          JOIN public.core_contextualroleassignment seat ON seat.membership_id=m.id
          WHERE displaced.id=NEW.assignment_id AND NEW.enterer_id=NEW.actor_id
          AND public.c3_act_problem(displaced,NEW.effective_at,NEW.received_at) IS NOT NULL
          AND public.c3_act_problem(displaced,clock_timestamp(),clock_timestamp()) IS NOT NULL
          AND public.c3_replacement(displaced.application_id,displaced.id,seat.id,NEW.actor_id,NEW.effective_at,NEW.received_at) IS NOT NULL
          AND public.c3_replacement(displaced.application_id,displaced.id,seat.id,NEW.actor_id,clock_timestamp(),clock_timestamp()) IS NOT NULL)
        THEN RAISE EXCEPTION 'C3 no independently qualified replacement; keep the claim' USING ERRCODE='23514'; END IF;
    RETURN NEW;"""),
        (claim, "    RETURN NEW;", """    PERFORM public.c3_lock_sources(NEW.application_id,NEW.assignee_office_id);
    IF NEW.replaces_id IS NULL AND NEW.lifecycle_finding_id IS NOT NULL
        OR public.c3_act_problem(NEW,NEW.effective_at,NEW.received_at) IS NOT NULL
        OR public.c3_act_problem(NEW,clock_timestamp(),clock_timestamp()) IS NOT NULL THEN
        RAISE EXCEPTION 'C3 assignment lacks current case authority' USING ERRCODE='23514'; END IF;
    RETURN NEW;"""),
        (engage, "    IF TG_OP = 'INSERT' THEN", """    IF TG_OP='INSERT' AND NEW.engagement_ordinal>1
        AND EXISTS(SELECT FROM public.core_applicationengagement WHERE application_id=NEW.application_id
                   AND engagement_ordinal=1 AND route='LO_CSA') THEN
        IF NEW.route<>'LO_CSA' OR NOT public.c3_pin_valid(NEW.application_id,NEW.settings_basis,NEW.deciding_party_id) THEN
           RAISE EXCEPTION 'C3 replacement must retain the original governing versions' USING ERRCODE='23514'; END IF;
    ELSIF TG_OP = 'INSERT' THEN"""),
        (decide, "OR NOT public.s3fu_application_settings_basis_valid(engagement.settings_basis, engagement.deciding_party_id, engagement.effective_at, engagement.recorded_at)",
         """OR NOT (CASE WHEN engagement.engagement_ordinal=1 THEN public.s3fu_application_settings_basis_valid(
             engagement.settings_basis,engagement.deciding_party_id,engagement.effective_at,engagement.recorded_at)
             ELSE public.c3_pin_valid(engagement.application_id,engagement.settings_basis,engagement.deciding_party_id) END)
         OR EXISTS(SELECT FROM public.core_csaassignment c WHERE c.id=NEW.assignment_reference_id AND
             (public.c3_act_problem(c,NEW.effective_at,NEW.received_at) IS NOT NULL OR
              public.c3_act_problem(c,clock_timestamp(),clock_timestamp()) IS NOT NULL))"""),
    ]


def switch(editor, forward):
    for original, old, new in guards():
        if original.count(old) != 1:
            raise RuntimeError("C3 guard source differs from the qualified migration")
        altered = original.replace(old, new)
        if "FUNCTION public.s3_decision_guard_a26()" in original:
            marker = "    SELECT * INTO engagement"
            if altered.count(marker) != 1:
                raise RuntimeError("C3 decision lock insertion point changed")
            altered = altered.replace(marker, "    PERFORM public.c3_lock_sources(NEW.application_id,NEW.qualifying_office_id);\n" + marker)
        before, after = (original, altered) if forward else (altered, original)
        signature = before.split("FUNCTION public.", 1)[1].split(" RETURNS", 1)[0]
        with editor.connection.cursor() as cur:
            cur.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", ["public." + signature])
            if cur.fetchone() != (before.split("AS $$", 1)[1].rsplit("$$;", 1)[0],):
                raise RuntimeError("C3 installed guard differs: " + signature)
        editor.execute(after, params=None)


def install(apps, editor):
    editor.execute(SQL, params=None)
    switch(editor, True)
    editor.execute("""
        CREATE TRIGGER a_c3_case_finding BEFORE INSERT ON public.core_governeddetermination
        FOR EACH ROW WHEN (NEW.evidence_reference LIKE 'C3:%') EXECUTE FUNCTION public.c3_case_finding_guard();
        CREATE TRIGGER c3_whole_basis BEFORE INSERT ON public.core_authorityinvalidationevent
        FOR EACH ROW EXECUTE FUNCTION public.c3_validate_basis_event();
        CREATE TRIGGER c3_successor BEFORE INSERT ON public.core_authoritybasis
        FOR EACH ROW WHEN (NEW.superseded_basis_id IS NOT NULL) EXECUTE FUNCTION public.c3_successor_check();
        CREATE CONSTRAINT TRIGGER c3_complete_supersession AFTER INSERT ON public.core_authorityinvalidationevent
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.kind='SUPERSESSION')
        EXECUTE FUNCTION public.c3_complete_supersession();
        CREATE FUNCTION public.c3_complete_transfer() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS(SELECT FROM public.core_csaassignment c JOIN public.core_applicationengagement e
              ON e.application_id=c.application_id AND e.engagement_reference=c.assignment_uuid::text
              WHERE c.replaces_id=NEW.assignment_id AND c.actor_id=NEW.actor_id
              AND e.basis_reference=NEW.ending_uuid::text AND e.actor_id=c.actor_id AND e.route='LO_CSA')
            THEN RAISE EXCEPTION 'C3 handover must record the qualified replacement and its engagement together'
              USING ERRCODE='23514'; END IF;
            RETURN NULL;
        END $$;
        CREATE CONSTRAINT TRIGGER c3_complete_transfer AFTER INSERT ON public.core_csaassignmentend
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.c3_complete_transfer();
    """, params=None)


def reverse(apps, editor):
    with editor.connection.cursor() as cur:
        cur.execute("SELECT EXISTS(SELECT FROM public.core_csaassignment WHERE lifecycle_finding_id IS NOT NULL) "
                    "OR EXISTS(SELECT FROM public.core_governeddetermination WHERE evidence_reference LIKE 'C3:%') "
                    "OR EXISTS(SELECT FROM public.core_authorityinvalidationevent WHERE request_reference LIKE 'C3:%')")
        if cur.fetchone()[0]:
            raise RuntimeError("C3 reverse refused: case authority evidence remains")
    editor.execute("DROP TRIGGER a_c3_case_finding ON public.core_governeddetermination")
    editor.execute("DROP TRIGGER c3_whole_basis ON public.core_authorityinvalidationevent")
    editor.execute("DROP TRIGGER c3_successor ON public.core_authoritybasis")
    editor.execute("DROP TRIGGER c3_complete_supersession ON public.core_authorityinvalidationevent")
    editor.execute("DROP TRIGGER c3_complete_transfer ON public.core_csaassignmentend")
    editor.execute("DROP FUNCTION public.c3_complete_transfer()")
    switch(editor, False)
    for signature in (
        "c3_case_finding_guard()",
        "c3_lock_sources(bigint,bigint,uuid[])",
        "c3_complete_supersession()",
        "c3_successor_check()",
        "c3_validate_basis_event()",
        "c3_pin_valid(bigint,text,bigint)",
        "c3_act_problem(public.core_csaassignment,timestamptz,timestamptz)",
        "c3_replacement(bigint,bigint,bigint,bigint,timestamptz,timestamptz)",
        "c3_claim_basis(public.core_csaassignment)",
        "c3_independent(bigint,bigint,timestamptz)",
        "c3_available(bigint,bigint,timestamptz,timestamptz)",
        "c3_findings(bigint,text,bigint,text,timestamptz,timestamptz)",
        "c3_scope(text,uuid,bigint,text,uuid,uuid,bigint)",
        "c3_act(bigint,text)",
        "c3_seat(bigint,bigint,bigint,timestamptz,timestamptz)",
        "c3_term(bigint,timestamptz,timestamptz)",
    ):
        editor.execute(f"DROP FUNCTION public.{signature}")



class Migration(migrations.Migration):
    dependencies = [("core", "0055_c2_coverage")]
    operations = [
        migrations.AddField(model_name="csaassignment", name="lifecycle_finding",
                            field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT,
                                                    related_name="+", to="core.governeddetermination")),
        migrations.RunPython(install, reverse),
    ]
