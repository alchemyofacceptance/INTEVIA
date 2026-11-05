"""Root values and independently qualified continuity. This migration seeds no decisions."""
from importlib import import_module

from django.db import migrations, models
from django.db.models.deletion import PROTECT


SQL = r"""
CREATE FUNCTION public.r_successor(lo bigint, kind text, cut timestamptz, known timestamptz)
RETURNS bigint LANGUAGE sql STABLE STRICT AS $$
 SELECT coalesce(
   (SELECT person_id FROM public.core_rootvaluedecision WHERE organism_id=lo
    AND setting='EMERGENCY_STEWARD' AND $2='INCAPACITY' AND effective_at<=cut AND recorded_at<=known
    ORDER BY effective_at DESC,id DESC LIMIT 1),
   (SELECT n.nominee_id FROM public.core_rootsuccessornomination n
    JOIN public.core_rootgenesis g ON g.id=n.genesis_id
    JOIN public.core_livingorganism l ON l.organism_id=g.organism_uuid
    JOIN public.core_organismroledefinition d ON d.id=n.definition_id AND d.living_organism_id=l.id
    WHERE l.id=lo AND public.r_circle_root_qualified(lo,cut,known)
    AND d.code=CASE $2 WHEN 'DEATH' THEN 'INTEVIA_GUIDING_STEWARD'
                         WHEN 'INCAPACITY' THEN 'INTEVIA_EMERGENCY_STEWARD' END
    AND n.effective_at<=cut AND n.recorded_at<=known))
$$;
CREATE FUNCTION public.r_value_person_allowed(lo bigint, key text, person bigint, cut timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_livingorganism l JOIN public.core_identity i ON i.id=person
  WHERE l.id=lo AND i.access_state='active' AND person<>l.founder_identity_id
  AND CASE key WHEN 'CONFIRMER' THEN person IS DISTINCT FROM public.r_successor(lo,'DEATH',cut,known)
                                     AND person IS DISTINCT FROM public.r_successor(lo,'INCAPACITY',cut,known)
               WHEN 'EMERGENCY_STEWARD' THEN person IS DISTINCT FROM
                (SELECT v.person_id FROM public.core_rootvaluedecision v WHERE v.setting='CONFIRMER'
                 AND v.organism_id=lo AND v.effective_at<=cut AND v.recorded_at<=known
                 ORDER BY v.effective_at DESC,v.id DESC LIMIT 1) ELSE false END)
$$;
CREATE FUNCTION public.r_latest_loss(lo bigint, cut timestamptz, known timestamptz)
RETURNS bigint LANGUAGE sql STABLE STRICT AS $$
 SELECT episode.id FROM (
   SELECT id FROM public.core_rootlossconfirmation WHERE organism_id=lo
   AND effective_at<=cut AND recorded_at<=known ORDER BY effective_at DESC,id DESC LIMIT 1
 ) episode WHERE NOT EXISTS (
   SELECT FROM public.core_rootcapacityrestoration r WHERE r.loss_id=episode.id
   AND r.recorded_at<=cut AND r.recorded_at<=known
 )
$$;
-- The recovery finding is the named confirmer's own authenticated S act. Its subject is this loss.
CREATE FUNCTION public.r_restoration_office(episode bigint, evidence bigint, founder bigint,
 at_time timestamptz, known timestamptz) RETURNS bigint LANGUAGE sql STABLE STRICT AS $$
 SELECT public.r_constitutive_founder_office(lo.id,$3,at_time,known)
 FROM public.core_rootlossconfirmation c
 JOIN public.core_livingorganism lo ON lo.id=c.organism_id
 JOIN public.core_rootvaluedecision v ON v.id=c.confirmer_value_id
 JOIN public.core_governeddetermination original ON original.id=c.finding_id
 JOIN public.core_governeddetermination f ON f.id=$2
 JOIN public.core_identity confirmer ON confirmer.id=v.person_id AND confirmer.access_state='active'
 WHERE c.id=$1 AND c.event_kind='INCAPACITY' AND lo.founder_identity_id=$3
 AND c.id=public.r_latest_loss(lo.id,at_time,known) AND NOT public.c2_contested(original.id,at_time,known)
 AND v.id=(SELECT current_value.id FROM public.core_rootvaluedecision current_value
   WHERE current_value.organism_id=lo.id AND current_value.setting='CONFIRMER'
   AND current_value.effective_at<=at_time AND current_value.recorded_at<=known
   ORDER BY current_value.effective_at DESC,current_value.id DESC LIMIT 1)
 AND f.determiner_id=v.person_id AND public.r_value_person_allowed(lo.id,'CONFIRMER',v.person_id,at_time,known)
 AND f.determination_uuid=public.circle_source(lo.id,c.request_uuid,'R:REGAINED_CAPACITY',
    $3,$3,at_time,known)
 AND least(f.occurred_at,f.effective_at,f.received_at,f.recorded_at)>c.recorded_at
 AND btrim(f.evidence_reference)<>'' AND btrim(f.evidence_reference)<>btrim(original.evidence_reference)
 AND NOT EXISTS (SELECT FROM public.core_governeddetermination other
    WHERE other.scope_fingerprint=f.scope_fingerprint AND other.determiner_id=v.person_id
      AND other.determination_type=f.determination_type AND other.result='QUALIFIED'
      AND public.c2_contested(other.id,at_time,known))
$$;
CREATE FUNCTION public.r_restoration_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE stamp timestamptz:=statement_timestamp(); executed timestamptz; lo_uuid uuid;
BEGIN
 SELECT lo.organism_id INTO STRICT lo_uuid FROM public.core_rootlossconfirmation c
 JOIN public.core_livingorganism lo ON lo.id=c.organism_id WHERE c.id=NEW.loss_id;
 PERFORM public.s015_lock_organisms(ARRAY[lo_uuid]);
 executed:=clock_timestamp();
 IF NEW.founder_office_id IS NULL OR NEW.recorded_at IS NOT NULL OR NOT EXISTS (SELECT FROM public.core_identity
    WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch)
 OR public.r_restoration_office(NEW.loss_id,NEW.finding_id,NEW.actor_id,stamp,stamp)
    IS DISTINCT FROM NEW.founder_office_id
 OR public.r_restoration_office(NEW.loss_id,NEW.finding_id,NEW.actor_id,executed,executed)
    IS DISTINCT FROM NEW.founder_office_id THEN
    RAISE EXCEPTION 'R_RESTORATION_AUTHORITY_OR_EVIDENCE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp;
 RETURN NEW;
END $$;
CREATE FUNCTION public.r_value_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE stamp timestamptz:=statement_timestamp(); previous bigint;
BEGIN
 PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=NEW.organism_id)]);
 SELECT id INTO previous FROM public.core_rootvaluedecision WHERE organism_id=NEW.organism_id AND setting=NEW.setting
 ORDER BY effective_at DESC,id DESC LIMIT 1;
 IF NEW.founder_office_id IS NULL OR NEW.recorded_at IS NOT NULL OR NEW.effective_at NOT BETWEEN transaction_timestamp() AND stamp
 OR previous IS DISTINCT FROM NEW.predecessor_id
 OR EXISTS(SELECT FROM public.core_rootvaluedecision WHERE id=previous AND effective_at>NEW.effective_at)
 OR public.r_founder_office(NEW.organism_id,NEW.actor_id,NEW.effective_at,stamp) IS DISTINCT FROM NEW.founder_office_id
 OR public.r_founder_office(NEW.organism_id,NEW.actor_id,stamp,stamp) IS DISTINCT FROM NEW.founder_office_id
 OR public.r_value_person_allowed(NEW.organism_id,NEW.setting,NEW.person_id,stamp,stamp) IS NOT TRUE
 OR NOT EXISTS(SELECT FROM public.core_identity WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch) THEN
  RAISE EXCEPTION 'R_VALUE_AUTHORITY_OR_PREDECESSOR' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END $$;

CREATE FUNCTION public.r_loss_source(lo bigint, val bigint, kind text, person bigint, beneficiary bigint,
 f_id bigint, cut timestamptz, known timestamptz) RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 WITH context AS (
  SELECT b.basis_uuid,l.founder_identity_id,v.person_id, public.c2_target(format(
    '{"act":%s,"basis":"%s","beneficiary":%s,"kind":"UNAVAILABILITY_DETERMINATION","organism":"%s","schema":"S:scope:1","target":"%s","tested":%s}',
    to_json('R:CONFIRM:'||kind),b.basis_uuid,beneficiary,l.organism_id,v.request_uuid,l.founder_identity_id)) AS scope
  FROM public.core_livingorganism l JOIN public.core_authoritybasis b ON b.id=l.founding_authority_basis_id
  JOIN public.core_rootvaluedecision v ON v.id=val AND v.organism_id=l.id AND v.setting='CONFIRMER'
  WHERE l.id=lo AND public.r_circle_root_qualified(lo,cut,known) AND person=v.person_id
  AND v.id=(SELECT id FROM public.core_rootvaluedecision WHERE organism_id=lo AND setting='CONFIRMER'
    AND effective_at<=cut AND recorded_at<=known ORDER BY effective_at DESC,id DESC LIMIT 1)
  AND public.r_value_person_allowed(lo,'CONFIRMER',person,cut,known)
  AND beneficiary=public.r_successor(lo,kind,cut,known)
 ), latest AS (
  SELECT d,c.basis_uuid FROM context c JOIN public.core_governeddetermination d ON d.scope_fingerprint=c.scope
  WHERE d.determination_type='UNAVAILABILITY_DETERMINATION' AND d.tested_actor_id=c.founder_identity_id
  AND d.safeguard_beneficiary_id=beneficiary AND d.effective_at<=cut AND d.received_at<=known AND d.recorded_at<=known
  ORDER BY d.effective_at DESC,d.occurred_at DESC,d.received_at DESC,d.recorded_at DESC,d.id DESC LIMIT 1
 ) SELECT EXISTS(SELECT FROM latest WHERE (d).id=f_id AND (d).determiner_id=person AND (d).result='QUALIFIED'
   AND public.c2_finding_problem(d,basis_uuid,cut,known) IS NULL
   AND NOT EXISTS (SELECT FROM public.core_rootcapacityrestoration r
      JOIN public.core_rootlossconfirmation c ON c.id=r.loss_id
      WHERE c.organism_id=lo AND r.recorded_at<=known AND r.recorded_at>=(d).occurred_at))
$$;
CREATE FUNCTION public.r_confirm_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE stamp timestamptz:=statement_timestamp(); prior text;
BEGIN
 PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=NEW.organism_id)]);
 SELECT event_kind INTO prior FROM public.core_rootlossconfirmation
 WHERE id=public.r_latest_loss(NEW.organism_id,stamp,stamp);
 IF NEW.recorded_at IS NOT NULL OR NEW.effective_at NOT BETWEEN transaction_timestamp() AND stamp
 OR NEW.event_kind NOT IN ('DEATH','INCAPACITY') OR prior='DEATH' OR prior=NEW.event_kind
 OR EXISTS(SELECT FROM public.core_rootlossconfirmation WHERE organism_id=NEW.organism_id AND effective_at>NEW.effective_at)
 OR NOT EXISTS(SELECT FROM public.core_identity WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch)
 OR public.r_loss_source(NEW.organism_id,NEW.confirmer_value_id,NEW.event_kind,NEW.actor_id,
       NEW.beneficiary_id,NEW.finding_id,NEW.effective_at,stamp) IS NOT TRUE
 OR public.r_loss_source(NEW.organism_id,NEW.confirmer_value_id,NEW.event_kind,NEW.actor_id,
       NEW.beneficiary_id,NEW.finding_id,stamp,stamp) IS NOT TRUE THEN
  RAISE EXCEPTION 'R_CONFIRMATION_AUTHORITY_OR_EVIDENCE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END $$;

CREATE FUNCTION public.r_holder_finding(conf bigint, domains text, bounds text, ends timestamptz,
 cut timestamptz, known timestamptz) RETURNS bigint LANGUAGE sql STABLE AS $$
 SELECT d.id FROM public.core_rootlossconfirmation c JOIN public.core_governeddetermination d
 ON d.determination_uuid=public.circle_source(c.organism_id,c.request_uuid,'R:PUBLISH_OWN:'||domains||':'||
   coalesce(to_char(ends AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'NO_FIXED_END')||':'||
   encode(sha256(convert_to(bounds,'UTF8')),'hex'),c.beneficiary_id,c.beneficiary_id,cut,known)
 WHERE c.id=conf
$$;
CREATE FUNCTION public.r_can_issue(conf bigint, issuer bigint, basis_pk bigint, grant_uuid uuid, fingerprint text,
 domains text, end_stamp text, bounds_digest text, cut timestamptz, known timestamptz, epoch bigint)
RETURNS boolean LANGUAGE plpgsql STABLE STRICT AS $$
DECLARE cutoff timestamptz;
BEGIN
 IF domains NOT IN ('LIBRARY','EDUCATION','BOTH') OR bounds_digest!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
 IF end_stamp<>'NO_FIXED_END' THEN
  IF end_stamp!~'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$' THEN RETURN false; END IF;
  cutoff:=end_stamp::timestamptz;
  IF cutoff<=cut THEN RETURN false; END IF;
 END IF;
 RETURN EXISTS(SELECT FROM public.core_rootlossconfirmation c JOIN public.core_livingorganism lo ON lo.id=c.organism_id
  JOIN public.core_authoritybasis root_basis ON root_basis.id=lo.founding_authority_basis_id
  JOIN public.core_authoritybasis b ON b.id=basis_pk AND b.issuer_id=issuer
  JOIN public.core_identity i ON i.id=issuer AND i.access_epoch=epoch AND i.access_state='active'
  WHERE c.id=conf AND c.id=public.r_latest_loss(lo.id,cut,known) AND c.beneficiary_id<>issuer
  AND issuer<>lo.founder_identity_id AND (c.event_kind='DEATH' OR cutoff IS NOT NULL)
  AND public.r_circle_root_qualified(lo.id,cut,known) AND NOT public.c2_contested(c.finding_id,cut,known)
  AND b.scope_fingerprint=fingerprint AND public.c2_basis_in_force(b.id,cut,known)
  AND public.c2_basis_in_force(root_basis.id,cut,known)
  AND EXISTS(SELECT FROM public.c2_lineage(b.id,known) src WHERE src.id=root_basis.authority_principal_id)
  AND fingerprint=public.c2_target(format(
    '{"act":%s,"basis":"%s","beneficiary":%s,"kind":"EXECUTION_ELIGIBILITY_QUALIFICATION","organism":"%s","schema":"S:scope:1","target":"%s","tested":%s}',
    to_json('R:PUBLISH_OWN:'||domains||':'||end_stamp||':'||bounds_digest),root_basis.basis_uuid,
    c.beneficiary_id,lo.organism_id,c.request_uuid,c.beneficiary_id))
  AND public.circle_source(lo.id,c.request_uuid,'R:ISSUE_GRANT:'||grant_uuid::text||':'||fingerprint,
                           issuer,c.beneficiary_id,cut,known) IS NOT NULL);
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN false;
END $$;
CREATE FUNCTION public.r_constitutional_grant_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parts text[]; c public.core_rootlossconfirmation; b public.core_authoritybasis; epoch bigint;
        stamp timestamptz:=statement_timestamp();
BEGIN
 parts:=string_to_array(NEW.method_reference,'|');
 IF cardinality(parts)<>6 THEN RAISE EXCEPTION 'R_GRANT_INSTRUMENT' USING ERRCODE='23514'; END IF;
 SELECT * INTO c FROM public.core_rootlossconfirmation WHERE request_uuid::text=parts[3];
 SELECT * INTO b FROM public.core_authoritybasis WHERE basis_uuid::text=parts[2];
 SELECT access_epoch INTO epoch FROM public.core_identity WHERE id=NEW.determiner_id;
 IF c.id IS NOT NULL THEN
  PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=c.organism_id)]);
 END IF;
 IF c.id IS NULL OR b.id IS NULL OR NEW.determination_type<>'CONSTITUTIONAL_AUTHORITY_QUALIFICATION'
 OR NEW.result NOT IN ('QUALIFIED','REFUSED') OR NEW.independence_state<>'INDEPENDENT'
 OR NEW.tested_actor_id IS DISTINCT FROM c.beneficiary_id OR NEW.safeguard_beneficiary_id IS DISTINCT FROM c.beneficiary_id
 OR NEW.evidence_reference IS DISTINCT FROM b.instrument_reference OR NEW.relied_upon_authority_principal_id<>b.authority_principal_id
 OR NEW.occurred_at<>NEW.effective_at OR NEW.received_at<>NEW.effective_at
 OR NEW.effective_at NOT BETWEEN transaction_timestamp() AND stamp
 OR public.r_can_issue(c.id,NEW.determiner_id,b.id,NEW.determination_uuid,NEW.scope_fingerprint,parts[4],parts[5],parts[6],
                      NEW.effective_at,stamp,epoch) IS NOT TRUE
 OR public.r_can_issue(c.id,NEW.determiner_id,b.id,NEW.determination_uuid,NEW.scope_fingerprint,parts[4],parts[5],parts[6],
                      stamp,stamp,epoch) IS NOT TRUE THEN
  RAISE EXCEPTION 'R_GRANT_INSTRUMENT' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END $$;
CREATE FUNCTION public.r_grant_ok(conf bigint, grant_id bigint, basis_id bigint, holder_id bigint, domains text,
 bounds text, ends timestamptz, cut timestamptz, known timestamptz) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(
  SELECT FROM public.core_rootlossconfirmation c JOIN public.core_livingorganism lo ON lo.id=c.organism_id
  JOIN public.core_authoritybasis root_basis ON root_basis.id=lo.founding_authority_basis_id
  JOIN public.core_authoritybasis b ON b.id=basis_id
  JOIN public.core_governeddetermination g ON g.id=grant_id
  JOIN public.core_governeddetermination holder ON holder.id=holder_id
  JOIN public.core_governeddetermination fact ON fact.id=c.finding_id
  WHERE c.id=conf AND c.id=public.r_latest_loss(lo.id,cut,known)
  AND c.beneficiary_id=public.r_successor(lo.id,c.event_kind,cut,known)
  AND public.r_circle_root_qualified(lo.id,cut,known)
  AND NOT public.c2_contested(fact.id,cut,known)
  AND NOT EXISTS(SELECT FROM public.core_governeddetermination newer WHERE newer.scope_fingerprint=fact.scope_fingerprint
    AND newer.determination_type=fact.determination_type AND newer.effective_at<=cut AND newer.recorded_at<=known
    AND newer.received_at<=known AND (newer.effective_at,newer.occurred_at,newer.received_at,newer.recorded_at,newer.id)>
        (fact.effective_at,fact.occurred_at,fact.received_at,fact.recorded_at,fact.id))
  AND domains IN ('LIBRARY','EDUCATION','BOTH') AND btrim(bounds)<>'' AND length(bounds)<=240
  AND (ends>cut OR (ends IS NULL AND c.event_kind='DEATH'))
  AND holder.id=public.r_holder_finding(conf,domains,bounds,ends,cut,known)
  AND g.determination_type='CONSTITUTIONAL_AUTHORITY_QUALIFICATION' AND g.result='QUALIFIED'
  AND g.independence_state='INDEPENDENT' AND g.tested_actor_id=c.beneficiary_id AND g.safeguard_beneficiary_id=c.beneficiary_id
  AND g.determiner_id=b.issuer_id AND g.determiner_id<>c.beneficiary_id
  AND g.determiner_id<>lo.founder_identity_id
  AND g.method_reference=concat_ws('|','R:grant:1',b.basis_uuid::text,c.request_uuid::text,domains,
    coalesce(to_char(ends AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'NO_FIXED_END'),encode(sha256(convert_to(bounds,'UTF8')),'hex'))
  AND g.evidence_reference=b.instrument_reference
  AND g.scope_fingerprint=holder.scope_fingerprint AND b.scope_fingerprint=g.scope_fingerprint
  AND g.relied_upon_authority_principal_id=b.authority_principal_id AND public.c2_basis_in_force(b.id,cut,known)
  AND EXISTS(SELECT FROM public.c2_lineage(b.id,known) s WHERE s.id=root_basis.authority_principal_id)
  AND public.c2_basis_in_force(root_basis.id,cut,known) AND NOT public.c2_contested(g.id,cut,known)
  AND g.effective_at>=c.recorded_at AND g.effective_at<=cut AND g.received_at<=known AND g.recorded_at<=known
  AND public.circle_source(lo.id,c.request_uuid,'R:ISSUE_GRANT:'||g.determination_uuid::text||':'||g.scope_fingerprint,
       g.determiner_id,c.beneficiary_id,g.effective_at,g.received_at) IS NOT NULL
  AND NOT EXISTS(SELECT FROM public.core_governeddetermination newer
    WHERE newer.id<>g.id AND newer.method_reference=g.method_reference AND newer.scope_fingerprint=g.scope_fingerprint
    AND newer.determination_type=g.determination_type AND newer.tested_actor_id=g.tested_actor_id
    AND newer.determiner_id=g.determiner_id AND newer.relied_upon_authority_principal_id=g.relied_upon_authority_principal_id
    AND newer.effective_at<=cut AND newer.received_at<=known AND newer.recorded_at<=known
    AND (newer.effective_at,newer.occurred_at,newer.received_at,newer.recorded_at,newer.id)>
        (g.effective_at,g.occurred_at,g.received_at,g.recorded_at,g.id)))
$$;
CREATE FUNCTION public.r_activation_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE stamp timestamptz:=statement_timestamp(); loss public.core_rootlossconfirmation; lo_uuid uuid;
BEGIN
 SELECT * INTO STRICT loss FROM public.core_rootlossconfirmation WHERE id=NEW.confirmation_id;
 SELECT organism_id INTO STRICT lo_uuid FROM public.core_livingorganism WHERE id=loss.organism_id;
 PERFORM public.s015_lock_organisms(ARRAY[lo_uuid]);
 IF NEW.recorded_at IS NOT NULL OR NEW.effective_at NOT BETWEEN transaction_timestamp() AND stamp
 OR NOT EXISTS(SELECT FROM public.core_organismroledefinition d WHERE d.id=NEW.office_definition_id
      AND d.living_organism_id=loss.organism_id AND d.active AND d.scope='LIVING_ORGANISM'
      AND d.code=CASE loss.event_kind WHEN 'DEATH' THEN 'INTEVIA_GUIDING_STEWARD' ELSE 'INTEVIA_EMERGENCY_STEWARD' END)
 OR NEW.actor_id<>loss.beneficiary_id OR NOT EXISTS(SELECT FROM public.core_identity
     WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch)
 OR NOT public.r_grant_ok(loss.id,NEW.grant_id,NEW.grant_basis_id,NEW.holder_finding_id,NEW.domains,
        NEW.bounds,NEW.hard_expiry,NEW.effective_at,stamp)
 OR NOT public.r_grant_ok(loss.id,NEW.grant_id,NEW.grant_basis_id,NEW.holder_finding_id,NEW.domains,
        NEW.bounds,NEW.hard_expiry,stamp,stamp)
 OR (loss.event_kind='DEATH' AND NEW.envelope_id IS NOT NULL)
 OR (loss.event_kind='INCAPACITY' AND NOT EXISTS(SELECT FROM public.core_emergencyauthorityenvelope env
   JOIN public.core_authoritybasis b ON b.id=NEW.grant_basis_id WHERE env.id=NEW.envelope_id
   AND env.qualifying_constitutional_grant_id=NEW.grant_id AND env.beneficiary_id=NEW.actor_id
   AND env.authority_principal_id=b.authority_principal_id AND env.derivation_root_principal_id=b.derivation_root_principal_id
   AND env.hard_expiry=NEW.hard_expiry AND env.permitted_command_set='["PUBLISH_OWN"]'::jsonb
   AND env.target_scope_set=jsonb_build_array(lo_uuid::text,NEW.domains)
   AND env.proportionality_bounds=jsonb_build_object('necessity_and_bounds',NEW.bounds))) THEN
  RAISE EXCEPTION 'R_ACTIVATION_GRANT_OR_ENVELOPE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END $$;
CREATE FUNCTION public.r_publication_capacity(lo bigint, person bigint, domain text, cut timestamptz, known timestamptz)
RETURNS text LANGUAGE plpgsql STABLE STRICT AS $$
DECLARE office bigint; activation uuid;
BEGIN
 IF domain NOT IN ('EDUCATION','LIBRARY') OR NOT EXISTS(SELECT FROM public.core_identity
  WHERE id=person AND access_state='active') THEN RETURN NULL; END IF;
 office:=public.r_founder_office(lo,person,cut,known);
 IF office IS NOT NULL THEN RETURN 'FOUNDER:'||office::text; END IF;
 SELECT a.request_uuid INTO activation FROM public.core_rootcontinuityactivation a
 JOIN public.core_rootlossconfirmation c ON c.id=a.confirmation_id
 WHERE c.organism_id=lo AND a.actor_id=person AND a.domains IN (domain,'BOTH')
 AND EXISTS(SELECT FROM public.core_organismroledefinition d WHERE d.id=a.office_definition_id
   AND d.living_organism_id=lo AND d.active AND d.code=CASE c.event_kind WHEN 'DEATH'
       THEN 'INTEVIA_GUIDING_STEWARD' ELSE 'INTEVIA_EMERGENCY_STEWARD' END)
 AND a.effective_at<=cut AND a.recorded_at<=known
 AND public.r_grant_ok(c.id,a.grant_id,a.grant_basis_id,a.holder_finding_id,
                       a.domains,a.bounds,a.hard_expiry,cut,known)
 ORDER BY a.effective_at DESC,a.id DESC LIMIT 1;
 IF activation IS NULL THEN RETURN NULL; END IF;
 RETURN 'ACTIVATION:'||activation::text;
END $$;
"""


# Prevent ordinary basis and office machinery from becoming a suspension route for the Founder.
ACCOUNTABILITY = """
CREATE FUNCTION public.r_protect_founder_basis() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT FROM public.core_rootgenesis g WHERE g.basis_id=NEW.basis_id) THEN
  RAISE EXCEPTION 'R_FOUNDER_CENSURE_ONLY' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_r_constitutive_basis BEFORE INSERT ON public.core_authorityinvalidationevent
 FOR EACH ROW EXECUTE FUNCTION public.r_protect_founder_basis();
CREATE FUNCTION public.r_protect_founder_standing() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.determination_type='EXECUTION_ELIGIBILITY_QUALIFICATION' AND EXISTS(
  SELECT FROM public.core_rootgenesis g JOIN public.core_livingorganism lo ON lo.organism_id=g.organism_uuid
  JOIN public.core_authoritybasis b ON b.id=g.basis_id
  JOIN public.core_organismmembership m ON m.living_organism_id=lo.id AND m.identity_id=g.founder_id
  JOIN public.core_contextualroleassignment a ON a.membership_id=m.id AND a.circle_id IS NULL
  JOIN public.core_organismroledefinition r ON r.id=a.role_definition_id AND r.code='INTEVIA_FOUNDER_STEWARD'
  WHERE g.founder_id=NEW.tested_actor_id
  AND NEW.scope_fingerprint=public.c2_office_scope(lo.organism_id,a.assignment_uuid,g.founder_id,b.basis_uuid)) THEN
  RAISE EXCEPTION 'R_FOUNDER_CENSURE_ONLY' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER a_r_constitutive_office BEFORE INSERT ON public.core_governeddetermination
 FOR EACH ROW EXECUTE FUNCTION public.r_protect_founder_standing();
"""


TABLES = {
    "core_rootcapacityrestoration": "r_restoration_input",
    "core_rootvaluedecision": "r_value_input",
    "core_rootlossconfirmation": "r_confirm_input",
    "core_rootcontinuityactivation": "r_activation_input",
}
SIGNATURES = (
    "r_publication_capacity(bigint,bigint,text,timestamptz,timestamptz)",
    "r_activation_input()",
    "r_grant_ok(bigint,bigint,bigint,bigint,text,text,timestamptz,timestamptz,timestamptz)",
    "r_constitutional_grant_input()",
    "r_can_issue(bigint,bigint,bigint,uuid,text,text,text,text,timestamptz,timestamptz,bigint)",
    "r_holder_finding(bigint,text,text,timestamptz,timestamptz,timestamptz)",
    "r_confirm_input()",
    "r_loss_source(bigint,bigint,text,bigint,bigint,bigint,timestamptz,timestamptz)",
    "r_restoration_input()",
    "r_restoration_office(bigint,bigint,bigint,timestamptz,timestamptz)",
    "r_value_input()",
    "r_latest_loss(bigint,timestamptz,timestamptz)",
    "r_value_person_allowed(bigint,text,bigint,timestamptz,timestamptz)",
    "r_successor(bigint,text,timestamptz,timestamptz)",
)


def founder_bodies():
    prior = import_module("core.migrations.0064_root_publication_declaration")
    header, tail = prior.SQL.strip().split(" AS $$", 1)
    body = tail.split("$$;", 1)[0]
    marker = " ORDER BY a.id LIMIT 1"
    if body.count(marker) != 1:
        raise RuntimeError("The expected Founder-office definition differs.")
    revised = body.replace(marker, " AND public.r_latest_loss(lo_id,at_time,known) IS NULL\n" + marker)
    return header, body, revised


def replace_founder(editor, reverse=False):
    header, prior, revised = founder_bodies()
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT prosrc FROM pg_proc WHERE oid="
                       "'public.r_founder_office(bigint,bigint,timestamptz,timestamptz)'::regprocedure")
        if cursor.fetchone() != (revised if reverse else prior,):
            raise RuntimeError("Installed Founder qualification differs. Inspect before proceeding.")
    editor.execute(header.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
                   + " AS $$" + (prior if reverse else revised) + "$$;", params=None)


def install(apps, editor):
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT FROM public.core_governeddetermination "
                       "WHERE starts_with(method_reference,'R:grant:1|'))")
        if cursor.fetchone()[0]:
            raise RuntimeError("The root grant namespace already contains evidence. Inspect before installation.")
    # The pre-loss constitutive office remains a read-only fact. Only restoration consumes this reader.
    header, constitutive, _ = founder_bodies()
    editor.execute(header.replace("public.r_founder_office(", "public.r_constitutive_founder_office(")
                   + " AS $$" + constitutive + "$$;", params=None)
    editor.execute(SQL, params=None)
    replace_founder(editor)
    editor.execute(ACCOUNTABILITY, params=None)
    editor.execute("CREATE TRIGGER r_constitutional_grant_input BEFORE INSERT ON public.core_governeddetermination "
                   "FOR EACH ROW WHEN (NEW.method_reference LIKE 'R:grant:1|%') "
                   "EXECUTE FUNCTION public.r_constitutional_grant_input()", params=None)
    for table, function in TABLES.items():
        editor.execute(f"CREATE TRIGGER r_input BEFORE INSERT ON public.{table} "
                       f"FOR EACH ROW EXECUTE FUNCTION public.{function}()", params=None)
        editor.execute(f"CREATE TRIGGER r_immutable BEFORE UPDATE OR DELETE ON public.{table} "
                       "FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()", params=None)
        editor.execute(f"CREATE TRIGGER s015_r_no_truncate BEFORE TRUNCATE ON public.{table} "
                       "FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()", params=None)


def reverse(apps, editor):
    tables = ", ".join("public." + table for table in TABLES)
    editor.execute("LOCK TABLE " + tables + " IN ACCESS EXCLUSIVE MODE", params=None)
    with editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"SELECT EXISTS(SELECT FROM public.{table})")
            if cursor.fetchone()[0]:
                raise RuntimeError("Root continuity or value history exists. Reversal refused.")
    for table in TABLES:
        for trigger in ("s015_r_no_truncate", "r_immutable", "r_input"):
            editor.execute(f"DROP TRIGGER {trigger} ON public.{table}", params=None)
    editor.execute("DROP TRIGGER r_constitutional_grant_input ON public.core_governeddetermination", params=None)
    editor.execute("DROP TRIGGER a_r_constitutive_basis ON public.core_authorityinvalidationevent; "
                   "DROP TRIGGER a_r_constitutive_office ON public.core_governeddetermination; "
                   "DROP FUNCTION public.r_protect_founder_basis(); DROP FUNCTION public.r_protect_founder_standing();", params=None)
    replace_founder(editor, reverse=True)
    for signature in SIGNATURES:
        editor.execute("DROP FUNCTION public." + signature, params=None)
    editor.execute("DROP FUNCTION public.r_constitutive_founder_office(bigint,bigint,timestamptz,timestamptz)",
                   params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0064_root_publication_declaration")]
    operations = [
        migrations.CreateModel(
            name="RootValueDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_uuid", models.UUIDField(unique=True)),
                ("organism", models.ForeignKey(to='core.livingorganism', on_delete=PROTECT, related_name='+')),
                ("setting", models.CharField(max_length=24)),
                ("person", models.ForeignKey(to='core.identity', on_delete=PROTECT, related_name='+')),
                ("actor", models.ForeignKey(to='core.identity', on_delete=PROTECT, related_name='+')),
                ("actor_epoch", models.PositiveBigIntegerField()),
                ("founder_office", models.ForeignKey(to='core.contextualroleassignment', on_delete=PROTECT, related_name='+')),
                ("predecessor", models.ForeignKey(to='self', on_delete=PROTECT, null=True, blank=True, related_name='+')),
                ("effective_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(null=True, blank=True, editable=False)),
            ],
        ),
        migrations.CreateModel(
            name="RootLossConfirmation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_uuid", models.UUIDField(unique=True)),
                ("organism", models.ForeignKey(to='core.livingorganism', on_delete=PROTECT, related_name='+')),
                ("event_kind", models.CharField(max_length=12)),
                ("confirmer_value", models.ForeignKey(to='core.rootvaluedecision', on_delete=PROTECT, related_name='+')),
                ("actor", models.ForeignKey(to='core.identity', on_delete=PROTECT, related_name='+')),
                ("actor_epoch", models.PositiveBigIntegerField()),
                ("beneficiary", models.ForeignKey(to='core.identity', on_delete=PROTECT, related_name='+')),
                ("finding", models.OneToOneField(to='core.governeddetermination', on_delete=PROTECT, related_name='+')),
                ("effective_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(null=True, blank=True, editable=False)),
            ],
        ),
        migrations.CreateModel(
            name="RootContinuityActivation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_uuid", models.UUIDField(unique=True)),
                ("confirmation", models.ForeignKey(to='core.rootlossconfirmation', on_delete=PROTECT, related_name='+')),
                ("actor", models.ForeignKey(to='core.identity', on_delete=PROTECT, related_name='+')),
                ("actor_epoch", models.PositiveBigIntegerField()),
                ("grant", models.OneToOneField(to='core.governeddetermination', on_delete=PROTECT, related_name='+')),
                ("grant_basis", models.ForeignKey(to='core.authoritybasis', on_delete=PROTECT, related_name='+')),
                ("office_definition", models.ForeignKey(to="core.organismroledefinition", on_delete=PROTECT, related_name="+")),
                ("holder_finding", models.ForeignKey(to='core.governeddetermination', on_delete=PROTECT, related_name='+')),
                ("envelope", models.OneToOneField(to='core.emergencyauthorityenvelope', on_delete=PROTECT, null=True, blank=True, related_name='+')),
                ("domains", models.CharField(max_length=12)),
                ("bounds", models.CharField(max_length=240)),
                ("hard_expiry", models.DateTimeField(null=True, blank=True)),
                ("effective_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(null=True, blank=True, editable=False)),
            ],
        ),
        migrations.CreateModel(
            name="RootCapacityRestoration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_uuid", models.UUIDField(unique=True)),
                ("loss", models.OneToOneField(to="core.rootlossconfirmation", on_delete=PROTECT, related_name="+")),
                ("finding", models.OneToOneField(to="core.governeddetermination", on_delete=PROTECT, related_name="+")),
                ("actor", models.ForeignKey(to="core.identity", on_delete=PROTECT, related_name="+")),
                ("actor_epoch", models.PositiveBigIntegerField()),
                ("founder_office", models.ForeignKey(to="core.contextualroleassignment", on_delete=PROTECT, related_name="+")),
                ("recorded_at", models.DateTimeField(null=True, blank=True, editable=False)),
            ],
        ),
        migrations.RunPython(install, reverse),
    ]
