"""L P2. Durable LAW intakes, source-bound ILC operations and attributed receipts."""
from importlib import import_module

from django.db import migrations, models

prior = import_module("core.migrations.0054_c1b_relationships")
root = import_module("core.migrations.0062_root_genesis")
gateway = import_module("core.migrations.0046_se_entitlement_foundation")
writer = import_module("core.migrations.0060_circle_coordinators")
ENTRY = "ADOPT_LAW"
CHOICES = prior.SR.ENTRY_CHOICES + [(prior.ENTRY, "Transition membership condition"), (ENTRY, "Adopt law")]

FORWARD = r"""
-- Restricted to the JSON values in LAW requests. Match canonical_json_bytes:
-- UTF-8, ordered keys, preserved string bytes, no inter-token whitespace.
CREATE FUNCTION public.l_canonical(j jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE encoded text;
BEGIN
 CASE jsonb_typeof(j)
 WHEN 'array' THEN
   SELECT '['||coalesce(string_agg(public.l_canonical(x.v),',' ORDER BY x.n),'')||']' INTO encoded
   FROM jsonb_array_elements(j) WITH ORDINALITY x(v,n);
 WHEN 'object' THEN
   SELECT '{'||coalesce(string_agg(to_jsonb(x.k)::text||':'||public.l_canonical(x.v),',' ORDER BY x.k COLLATE "C"),'')||'}'
    INTO encoded FROM jsonb_each(j) x(k,v);
 ELSE encoded:=j::text;
 END CASE;
 RETURN encoded;
END $$;
-- Exact, version-bound provision link. P3 supplies its governed registration and resolver.
CREATE TABLE public.core_lawprovisionlocator (
 provision_id bigint PRIMARY KEY REFERENCES public.core_organismroledefinitionprovision,
 version_id bigint NOT NULL REFERENCES public.core_libraryresourceversion,
 locator text CHECK (length(locator)<=64 AND locator ~ '^[A-Za-z0-9]+([.-][A-Za-z0-9]+)*$')
);
CREATE FUNCTION public.l_locator_input() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_organismroledefinitionprovision p WHERE p.id=NEW.provision_id
               AND p.recorded_at = statement_timestamp()
               AND EXISTS (SELECT FROM public.core_libraryresourceversion v JOIN public.core_libraryresource r ON r.id=v.resource_id
                  WHERE v.id=NEW.version_id AND p.provision_reference='law-ref:'||r.resource_id||coalesce('#'||NEW.locator,'')
                  AND p.provision_version=v.version_number::text
                  AND EXISTS(SELECT FROM public.core_organismroledefinition d JOIN public.core_organismlawbinding b
                     ON b.version_id=v.id AND (b.scope_kind='PLATFORM' OR
                        (b.scope_kind='LIVING_ORGANISM' AND b.organism_id=d.living_organism_id))
                     WHERE d.id=p.definition_id AND public.l_visible_window(b,d.effective_at,d.recorded_at))
                  AND (CASE WHEN public.l_charter_pin(r.resource_id) IS NOT NULL THEN NEW.locator IS NULL
                            ELSE EXISTS(SELECT FROM public.l_clauses(v.content) c WHERE c.locator=NEW.locator) END))) THEN
   RAISE EXCEPTION 'A LAW clause link must be recorded with its original provision' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER l_locator_input BEFORE INSERT ON public.core_lawprovisionlocator
 FOR EACH ROW EXECUTE FUNCTION public.l_locator_input();

-- S015 stores its decision via Django get_or_create (a savepoint). xmin may be
-- a subtransaction ID. Record the database's top-level transaction explicitly.
ALTER TABLE public.core_authoritydecision ADD COLUMN l_execution_xid xid8;
ALTER TABLE public.core_authoritydecision ADD CONSTRAINT l_decision_xid_scope
 CHECK ((action='ADOPT_LAW')=(l_execution_xid IS NOT NULL));
CREATE FUNCTION public.l_decision_time() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE i public.core_commandintake; assessed jsonb;
BEGIN
 IF NEW.l_execution_xid IS NOT NULL THEN RAISE EXCEPTION 'LAW transaction identity is database assigned' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT i FROM public.core_commandintake WHERE id=(NEW.target->>'intake')::bigint;
 IF public.l_intake_matches(NEW,i) IS NOT TRUE OR NEW.effective_at<i.received_at OR NEW.effective_at>statement_timestamp()
   OR NEW.provider_identity<>'INTEVIA.S015.ApplicationAuthorityProvider'
   OR NEW.decision_payload->'target' IS DISTINCT FROM NEW.target
   OR NEW.decision_payload->>'target_fingerprint' IS DISTINCT FROM NEW.target_fingerprint
   OR NEW.decision_payload->>'action' IS DISTINCT FROM NEW.action
   OR NEW.decision_payload->>'command_subtype' IS DISTINCT FROM NEW.command_subtype
   OR (NEW.decision_payload->>'actor_pk')::bigint IS DISTINCT FROM i.actor_id
   OR (NEW.decision_payload->>'actor_identity_id')::uuid IS DISTINCT FROM i.actor_identity_id
   OR (NEW.decision_payload->>'actor_access_epoch')::bigint IS DISTINCT FROM i.actor_access_epoch
   OR NEW.decision_payload->>'request_reference' IS DISTINCT FROM i.request_reference
   OR NEW.decision_payload->>'idempotency_key' IS DISTINCT FROM i.idempotency_key
   OR (NEW.decision_payload->>'effective_at')::timestamptz IS DISTINCT FROM NEW.effective_at
   OR (NEW.decision_payload->>'evaluated_at')::timestamptz IS DISTINCT FROM NEW.evaluated_at
   OR NEW.decision_payload->>'authority_reference' IS DISTINCT FROM NEW.authority_reference
   OR NEW.decision_payload->>'refusal_code' IS DISTINCT FROM NEW.refusal_code THEN
   RAISE EXCEPTION 'LAW decision differs from the signed request and its intake' USING ERRCODE='23514';
 END IF;
 assessed:=public.l_assess_request(NEW.target->'spec',i.actor_id,i.received_at,NEW.effective_at);
 IF assessed IS DISTINCT FROM NEW.target->'plan' OR (NEW.outcome='REFUSED')<>(assessed ? 'reason') OR
    (NEW.outcome='QUALIFIED' AND NEW.authority_reference IS DISTINCT FROM
      's015a1:'||public.l_canonical(jsonb_build_object('kind','LAW_VERSION','route',assessed->>'route'))) THEN
    RAISE EXCEPTION 'LAW decision differs from the independently checked facts' USING ERRCODE='23514';
 END IF;
 NEW.l_execution_xid:=pg_current_xact_id();
 RETURN NEW;
END $$;
CREATE TRIGGER l_decision_time BEFORE INSERT ON public.core_authoritydecision
 FOR EACH ROW WHEN (NEW.action='ADOPT_LAW') EXECUTE FUNCTION public.l_decision_time();

CREATE TABLE public.core_lawact (
 event_id bigint PRIMARY KEY REFERENCES public.core_livingorganismevent,
 intake_id bigint NOT NULL UNIQUE REFERENCES public.core_commandintake,
 decision_id bigint NOT NULL UNIQUE REFERENCES public.core_authoritydecision,
 proposal uuid NOT NULL,
 kind text NOT NULL CHECK (kind IN ('ADOPT','RETIRE','DEFER','AGREE')),
 plan jsonb NOT NULL
);
CREATE UNIQUE INDEX l_one_completed_proposal ON public.core_lawact(proposal) WHERE kind<>'AGREE';

CREATE FUNCTION public.l_charter_pin(reference text) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT CASE reference
 WHEN 'intevia:charter:free-will' THEN 'ef09fca88d32a4fe4dbb7e76f3517fc5db0621ad60bbf0d813730d820eb26fc5'
 WHEN 'intevia:charter:care-constitutional' THEN '01c70ecc68f0dd5d0ec737d3877bd5dfa2d556c8136387e020dc8da3b8ee111b' END
$$;

-- Preserve all clause bytes after the marker except the two B.2 normalisations.
CREATE FUNCTION public.l_clauses(body text) RETURNS TABLE(locator text, content text)
LANGUAGE sql IMMUTABLE STRICT AS $$
 WITH lines AS (
  SELECT n,line,regexp_match(
     CASE WHEN n=1 AND left(line,1)=chr(65279) THEN substr(line,2) ELSE line END,'^[[:blank:]]*(?:§([A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)(?=[[:space:]]|$)|\[#([A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)\])(.*)$') m
  FROM regexp_split_to_table(replace(replace(normalize(body,NFC),E'\r\n',E'\n'),E'\r',E'\n'),E'\n') WITH ORDINALITY x(line,n)
 ), groups AS (SELECT *,max(n) FILTER (WHERE m IS NOT NULL) OVER (ORDER BY n) AS start FROM lines)
 SELECT max(coalesce(m[1],m[2])),string_agg(CASE WHEN n=start THEN m[3] ELSE line END,E'\n' ORDER BY n)
 FROM groups WHERE start IS NOT NULL GROUP BY start
$$;

-- Explicit name for new R-1 writers. Prior free text stays as recorded.
CREATE FUNCTION public.l_link_provision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE version_pk bigint; locator_ text;
BEGIN
 locator_:=substring(NEW.provision_reference FROM '#([A-Za-z0-9]+([.-][A-Za-z0-9]+)*)$');
 SELECT v.id INTO version_pk FROM public.core_libraryresourceversion v JOIN public.core_libraryresource r ON r.id=v.resource_id
 WHERE NEW.provision_reference='law-ref:'||r.resource_id||coalesce('#'||locator_,'')
   AND NEW.provision_version=v.version_number::text
   AND (public.l_charter_pin(r.resource_id) IS NOT NULL)=(locator_ IS NULL);
 IF version_pk IS NULL THEN
    RAISE EXCEPTION 'Cite the exact version and a clause marker, or the whole protected charter' USING ERRCODE='23514';
 END IF;
 INSERT INTO public.core_lawprovisionlocator VALUES (NEW.id,version_pk,locator_);
 RETURN NULL;
END $$;
CREATE TRIGGER l_link_provision AFTER INSERT ON public.core_organismroledefinitionprovision
 FOR EACH ROW WHEN (left(NEW.provision_reference,8)='law-ref:') EXECUTE FUNCTION public.l_link_provision();

CREATE FUNCTION public.l_pending(lo bigint, platform boolean, old_version bigint, new_version bigint,
 old_class text, new_class text, old_consequence text, new_consequence text,
 cut timestamptz, known timestamptz, previous_binding uuid) RETURNS jsonb LANGUAGE sql STABLE AS $$
 WITH nominations AS (
  SELECT a.assignment_uuid,d.role_uuid,p.provision_uuid,p.relationship,k.locator,k.version_id
  FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
  JOIN public.core_organismroledefinitionprovision p ON p.definition_id=d.id
  LEFT JOIN public.core_lawprovisionlocator k ON k.provision_id=p.id
  WHERE (d.living_organism_id=lo OR (platform AND k.version_id IS NOT NULL))
    AND old_version IS NOT NULL AND d.recorded_at<=known AND p.recorded_at<=known
    AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,cut,known)='PROPOSED'
 ), classified AS (
  SELECT n.*,prior.version_number old_number,successor.version_number new_number,
     CASE WHEN n.version_id IS NULL THEN 'UNRESOLVED'
          WHEN new_version IS NULL THEN 'RETIRED'
          WHEN n.locator IS NOT NULL AND after_clause.locator IS NULL THEN 'GONE'
          WHEN n.locator IS NULL AND convert_to(v.content,'UTF8')<>convert_to(successor.content,'UTF8') THEN 'CHANGED'
          WHEN before_clause.content IS DISTINCT FROM after_clause.content THEN 'CHANGED'
          WHEN old_class IS DISTINCT FROM new_class OR old_consequence IS DISTINCT FROM new_consequence
               THEN 'RECLASSIFIED'
     END AS effect
  FROM nominations n
  LEFT JOIN public.core_libraryresourceversion v ON v.id=n.version_id
  LEFT JOIN public.core_libraryresourceversion prior ON prior.id=old_version
  LEFT JOIN public.core_libraryresourceversion successor ON successor.id=new_version
  LEFT JOIN LATERAL public.l_clauses(v.content) before_clause ON before_clause.locator=n.locator
  LEFT JOIN LATERAL public.l_clauses(successor.content) after_clause ON after_clause.locator=n.locator
  WHERE n.version_id IS NULL OR v.resource_id=prior.resource_id
 ), rows AS (
   SELECT DISTINCT jsonb_build_object('binding_uuid',previous_binding,'definition_uuid',role_uuid,
     'relationship',relationship,'locator',locator,'prior_version',old_number,'new_version',new_number,
     'effect',effect) AS citation,assignment_uuid FROM classified WHERE effect IS NOT NULL
 )
 SELECT jsonb_build_object('citations',coalesce((SELECT jsonb_agg(citation ORDER BY public.l_canonical(citation) COLLATE "C")
    FROM (SELECT DISTINCT citation FROM rows) c),'[]'::jsonb),
   'nominations',coalesce((SELECT jsonb_agg(assignment_uuid ORDER BY assignment_uuid)
    FROM (SELECT DISTINCT assignment_uuid FROM rows) a),'[]'::jsonb))
$$;

-- The owning LO and LIBRARY resource have already been locked. Lock any historical
-- predecessor and dependent assignment/definition anchors in the documented order.
CREATE FUNCTION public.l_lock_dependencies(spec jsonb, lo bigint, person bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE prior public.core_organismlawbinding; nominations uuid[]; cutoff timestamptz:=statement_timestamp();
BEGIN
 IF spec->>'predecessor' IS NULL THEN RETURN; END IF;
 SELECT b.* INTO prior FROM public.core_organismlawbinding b JOIN public.core_libraryresource r ON r.id=b.resource_id
   WHERE b.binding_uuid=(spec->>'predecessor')::uuid AND r.resource_id=spec->>'resource' AND b.organism_id=lo
   FOR UPDATE OF b;
 IF prior.binding_uuid IS NULL THEN RETURN; END IF;
 IF spec->>'scope'='PLATFORM' THEN
   IF public.r_founder_office(lo,person,cutoff,cutoff) IS NULL THEN RETURN; END IF;
   LOCK TABLE public.core_contextualroleassignmentstateevent, public.core_organismroledefinitionprovision,
     public.core_lawprovisionlocator IN SHARE MODE;
 END IF;
 SELECT ARRAY(SELECT DISTINCT a.assignment_uuid FROM public.core_contextualroleassignment a
   JOIN public.core_organismroledefinition d ON a.role_definition_id=d.id
   JOIN public.core_organismroledefinitionprovision p ON p.definition_id=d.id
   LEFT JOIN public.core_lawprovisionlocator k ON k.provision_id=p.id
   LEFT JOIN public.core_libraryresourceversion v ON v.id=k.version_id
   WHERE (d.living_organism_id=lo OR (spec->>'scope'='PLATFORM' AND k.version_id IS NOT NULL))
      AND (k.version_id IS NULL OR v.resource_id=prior.resource_id)
      AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,cutoff,cutoff)='PROPOSED')
 INTO nominations;
 PERFORM a.id FROM public.core_contextualroleassignment a WHERE a.assignment_uuid=ANY(nominations)
 ORDER BY a.assignment_uuid FOR UPDATE;
 PERFORM d.id FROM public.core_organismroledefinition d WHERE EXISTS(SELECT FROM public.core_contextualroleassignment a
   WHERE a.role_definition_id=d.id AND a.assignment_uuid=ANY(nominations)) ORDER BY d.role_uuid FOR SHARE;
END $$;

CREATE FUNCTION public.l_resource_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF public.l_charter_pin(OLD.resource_id) IS NOT NULL OR public.l_charter_pin(NEW.resource_id) IS NOT NULL OR
   EXISTS(SELECT FROM public.core_organismlawbinding WHERE resource_id=OLD.id) THEN
   RAISE EXCEPTION 'A charter or adopted instrument keeps its recorded resource identity' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER l_resource_identity BEFORE UPDATE OF resource_id ON public.core_libraryresource
 FOR EACH ROW WHEN (OLD.resource_id IS DISTINCT FROM NEW.resource_id) EXECUTE FUNCTION public.l_resource_identity();

CREATE FUNCTION public.l_electorate(lo bigint, cut timestamptz, known timestamptz)
RETURNS jsonb LANGUAGE sql STABLE STRICT AS $$
 SELECT coalesce(jsonb_object_agg(m.identity_id::text,jsonb_build_array(m.membership_uuid,
     public.c1b_period_at(m.id,cut,known),i.access_epoch)),'{}'::jsonb)
 FROM public.core_organismmembership m JOIN public.core_identity i ON i.id=m.identity_id
 WHERE m.living_organism_id=lo AND public.s015_fold_effective_state('core_organismmembership',m.id,cut,known)='ACTIVE'
$$;

CREATE FUNCTION public.l_route(lo public.core_livingorganism, person bigint, class_name text, owner text,
 scope text, charter boolean, requested text, elect jsonb, cut timestamptz, known timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE route text; office uuid; count_ integer; born boolean; state_ text; founder_office bigint;
BEGIN
 state_:=public.s015_fold_effective_state('core_livingorganism',lo.id,cut,known);
 IF state_ IS NULL OR state_ NOT IN ('ACTIVE','FOUNDING_PENDING')
    OR NOT public.c2_basis_in_force(lo.founding_authority_basis_id,cut,known) THEN
   RETURN jsonb_build_object('reason','This organisation needs a current founding basis and an active or founding state.');
 END IF;
 IF requested IN ('CQ_MEMBERSHIP','CQ_CIRCLE','CQ_GOVERNANCE_CIRCLE','STEWARDS_AUTHORISED','STEWARDS_PROPOSE_FOUNDER_AUTHORISES') THEN
   RETURN jsonb_build_object('reason','This collective route opens when DISCUSSION provides the governed CQ vote.');
 END IF;
 SELECT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=lo.id
   AND e.action='BIRTHING' AND e.effective_at<=cut AND e.received_at<=known AND e.recorded_at<=known) INTO born;
 IF lo.platform_root THEN
   founder_office:=public.r_founder_office(lo.id,lo.founder_identity_id,cut,known);
   IF founder_office IS NULL THEN
     RETURN jsonb_build_object('reason','Root authority requires the effective Founder and Steward or a functioning, authorised Circle of Stewards.');
   END IF;
 END IF;
 IF scope='PLATFORM' OR charter OR (lo.platform_root AND class_name IN ('ARTICLE','MEMBERS_RESOLUTION','BYLAW')) THEN
   IF NOT lo.platform_root THEN RETURN jsonb_build_object('reason','Only INTEVIA can change inherited law or its charters.'); END IF;
   IF person<>lo.founder_identity_id THEN
     RETURN jsonb_build_object('reason','Only the effective Founder and Steward may authorise this protected root act before collective governance.');
   END IF;
   route:='FOUNDER_STEWARD';
 ELSIF NOT born THEN
   IF person<>lo.founder_identity_id THEN RETURN jsonb_build_object('reason','Before Birthing only the founder may change law.'); END IF;
   route:='FOUNDER_CONSTITUTIVE';
 ELSIF NOT (elect ? person::text) THEN
   RETURN jsonb_build_object('reason','This decision requires eligible membership in this organisation.');
 ELSIF class_name IN ('ARTICLE','MEMBERS_RESOLUTION','BYLAW') THEN
   SELECT count(*) INTO count_ FROM jsonb_object_keys(elect);
   IF count_>=3 THEN RETURN jsonb_build_object('reason','This protected law needs a collective vote. Its CQ route awaits DISCUSSION.'); END IF;
   IF count_=2 THEN route:='UNANIMOUS_PAIR'; ELSE route:='SOLE_MEMBER_COORDINATOR'; END IF;
 ELSIF requested='GOVERNANCE_INTERIM' OR owner='GOVERNANCE' THEN
   SELECT a.assignment_uuid INTO office FROM public.core_circle c
      CROSS JOIN LATERAL public.circle_holders(c.id,cut,known) h
      JOIN public.core_contextualroleassignment a ON a.circle_id=c.id
      JOIN public.core_organismmembership m ON m.id=a.membership_id AND m.identity_id=h.person
      JOIN public.c2_seat_facts(lo.id,cut,known) f ON f.assignment_id=a.id AND f.role_scope='CIRCLE'
         AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
      WHERE c.parent_organism_id=lo.id AND c.kind='GOVERNANCE' AND h.person=person
      AND public.s015_fold_effective_state('core_circle',c.id,cut,known)='ACTIVE' ORDER BY a.id LIMIT 1;
   IF office IS NULL THEN
     RETURN jsonb_build_object('reason','The interim board route requires an effective Coordinator of the GOVERNANCE Circle.');
   END IF;
   route:='GOVERNANCE_INTERIM';
 ELSIF owner='CIRCLE' OR requested='CIRCLE_INTERIM' THEN
   RETURN jsonb_build_object('reason','Circle delegation of law is not yet available.');
 ELSE route:='COORDINATOR_ROUTE';
 END IF;
 IF route IN ('SOLE_MEMBER_COORDINATOR','COORDINATOR_ROUTE') THEN
   SELECT a.assignment_uuid INTO office FROM public.c2_seat_facts(lo.id,cut,known) f
   JOIN public.core_contextualroleassignment a ON a.id=f.assignment_id
   JOIN public.core_organismmembership m ON m.id=f.membership_id
   WHERE f.role_scope='LIVING_ORGANISM' AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
     AND a.circle_id IS NULL AND m.identity_id=person ORDER BY a.id LIMIT 1;
   IF office IS NULL THEN RETURN jsonb_build_object('reason','This act needs the effective LO Coordinator, recorded in that capacity.'); END IF;
 END IF;
 IF lo.platform_root AND person<>lo.founder_identity_id AND NOT EXISTS (
   SELECT FROM public.core_contextualroleassignment a JOIN public.core_contextualroleassignmentstateevent e
       ON e.assignment_id=a.id JOIN public.core_authoritydecision d ON d.decision_reference=e.authority_decision_reference
    WHERE a.assignment_uuid=office AND e.action='ASSIGN' AND d.outcome='QUALIFIED' AND d.actor_id=lo.founder_identity_id
      AND e.effective_at<=cut AND e.recorded_at<=known AND public.r_founder_office(lo.id,d.actor_id,e.effective_at,e.recorded_at) IS NOT NULL
      AND e.id=(SELECT latest.id FROM public.core_contextualroleassignmentstateevent latest
         WHERE latest.assignment_id=a.id AND latest.action='ASSIGN' AND latest.effective_at<=cut AND latest.recorded_at<=known
         ORDER BY latest.effective_at DESC,latest.sequence DESC LIMIT 1)) THEN
   RETURN jsonb_build_object('reason','This ordinary root office needs a recorded authority chain to the Founder and Steward.');
 END IF;
 IF requested IS NOT NULL AND requested<>route THEN
   RETURN jsonb_build_object('reason',CASE route
     WHEN 'UNANIMOUS_PAIR' THEN 'Both eligible members must record their agreement separately.'
     WHEN 'SOLE_MEMBER_COORDINATOR' THEN 'The sole eligible member must act as the recorded LO Coordinator.'
     WHEN 'FOUNDER_STEWARD' THEN 'The effective Founder and Steward must authorise this root decision.'
     WHEN 'FOUNDER_CONSTITUTIVE' THEN 'Before Birthing, the founder must make this decision.'
     WHEN 'GOVERNANCE_INTERIM' THEN 'Use the interim route of the active GOVERNANCE Circle.'
     ELSE 'Use the effective LO Coordinator’s route for this law.' END);
 END IF;
 RETURN jsonb_build_object('route',route,'office',office,'root_office',founder_office);
END $$;

CREATE FUNCTION public.l_assess(s jsonb, person bigint, cut timestamptz, known timestamptz, own_event bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE lo public.core_livingorganism; doc public.core_libraryresource; v public.core_libraryresourceversion;
 b public.core_organismlawbinding; previous public.core_organismlawbinding; last_end public.core_organismlawending;
 c public.core_circle; scope text:=s->>'scope'; cls text:=s->>'law_class'; owner text; elector jsonb; auth jsonb;
 operation text:=s->>'operation'; intent text:=s->>'intent'; proposal uuid:=(s->>'proposal')::uuid;
 reference text; affected jsonb; affected_ref text; open_prior boolean; effect jsonb;
 pin text; agreement bigint; route text; previous_owner text;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE organism_id=(s->>'organism')::uuid;
 SELECT * INTO doc FROM public.core_libraryresource WHERE resource_id=s->>'resource';
 IF NOT EXISTS (SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
           WHERE i.id=person AND i.access_state='active' AND u.is_active) THEN
   RETURN jsonb_build_object('reason','This act needs your active identity.');
 END IF;
 IF lo.id IS NULL OR doc.id IS NULL THEN RETURN jsonb_build_object('reason','Choose an existing organisation and LIBRARY instrument.'); END IF;
 pin:=public.l_charter_pin(doc.resource_id);
 IF pin IS NOT NULL AND (s->>'feature'='END_CHARTER_PROTECTION' OR (intent='RETIRE' AND scope='PLATFORM')) THEN
   RETURN jsonb_build_object('reason','Ending this charter’s inherited constitutional protection requires an explicit ruling from the INTEVIA Founder & Steward.');
 END IF;
 IF operation IN ('RESOLVE','PROCEED_AND_CLOSE') OR s->>'resolution'='PROCEED_AND_CLOSE' THEN
   RETURN jsonb_build_object('reason','Closing or resolving nominations is not yet available. Defer the change instead.');
 END IF;
 IF s->>'feature' IS NOT NULL THEN
   RETURN jsonb_build_object('reason',CASE s->>'feature'
     WHEN 'CIRCLE_DELEGATION' THEN 'Circle delegation of law is not yet available.'
     WHEN 'ISSUE_CIRCLE_TERMS' THEN 'Board-issued Circle terms of reference are not yet available.'
     WHEN 'RESOLUTION_LABEL' THEN 'Changing the display name for members’ resolutions is not yet available.'
     ELSE 'This law change is not yet available.' END);
 END IF;
 IF operation NOT IN ('ADOPT','RETIRE','DEFER') OR intent NOT IN ('ADOPT','RETIRE')
    OR (operation<>'DEFER' AND intent<>operation) THEN
   RETURN jsonb_build_object('reason','Choose adoption, retirement or a deferral of one of those acts.');
 END IF;
 IF scope='CIRCLE' THEN
   SELECT * INTO c FROM public.core_circle WHERE parent_organism_id=lo.id AND circle_uuid=(s->>'circle')::uuid;
   IF c.id IS NULL OR cls<>'TERMS_OF_REFERENCE' THEN RETURN jsonb_build_object('reason','Choose the exact Circle governed by these terms.'); END IF;
   reference:='CIRCLE:'||c.circle_uuid;
 ELSIF scope='PLATFORM' THEN
   IF NOT lo.platform_root THEN RETURN jsonb_build_object('reason','Only INTEVIA can adopt or retire inherited platform law.'); END IF;
   reference:='PLATFORM';
 ELSIF scope='LIVING_ORGANISM' THEN reference:='LO:'||lo.organism_id;
 ELSE RETURN jsonb_build_object('reason','Choose an organisation, platform or Circle scope.'); END IF;
 IF (cls='TERMS_OF_REFERENCE')<>(scope='CIRCLE') OR
    (scope<>'CIRCLE' AND s->>'circle' IS NOT NULL) OR
    (public.l_law_rank(cls) IS NULL AND cls<>'TERMS_OF_REFERENCE') THEN
   RETURN jsonb_build_object('reason','The specified class cannot govern this scope.');
 END IF;
 IF EXISTS (SELECT FROM public.core_lawact WHERE core_lawact.proposal=(s->>'proposal')::uuid
            AND kind<>'AGREE' AND event_id IS DISTINCT FROM own_event) THEN
   RETURN jsonb_build_object('reason','This proposal already has a completed act. Submit a fresh proposal.');
 END IF;
 SELECT * INTO previous FROM public.core_organismlawbinding p
 WHERE p.resource_id=doc.id AND p.scope_key=reference AND p.adoption_event_id IS DISTINCT FROM own_event
 AND p.in_force_from<=cut ORDER BY p.in_force_from DESC LIMIT 1;
 IF previous.binding_uuid IS DISTINCT FROM (s->>'predecessor')::uuid THEN
   RETURN jsonb_build_object('reason','The named predecessor differs from this instrument’s latest binding. Review the current version.');
 END IF;
 SELECT * INTO last_end FROM public.core_organismlawending x WHERE x.binding_id=previous.binding_uuid
   AND x.end_event_id IS DISTINCT FROM own_event;
 open_prior:=previous.binding_uuid IS NOT NULL AND last_end.binding_id IS NULL;
 IF intent='RETIRE' AND (NOT open_prior OR previous.law_class<>cls) THEN
   RETURN jsonb_build_object('reason','Retirement must name the open binding at its recorded class.');
 END IF;
 IF pin IS NOT NULL AND (NOT lo.platform_root OR cls<>'ARTICLE' OR scope='CIRCLE'
    OR (intent='RETIRE' AND (scope='PLATFORM' OR NOT EXISTS (SELECT FROM public.core_organismlawbinding platform_law
        WHERE platform_law.resource_id=doc.id AND platform_law.scope_kind='PLATFORM'
          AND public.l_visible_window(platform_law,cut,known))))) THEN
   RETURN jsonb_build_object('reason','Removing charter protection requires an explicit ruling from the INTEVIA Founder & Steward.');
 END IF;
 IF intent='ADOPT' THEN
   SELECT * INTO v FROM public.core_libraryresourceversion WHERE resource_id=doc.id
       AND version_number=(s->>'version')::integer;
   IF v.id IS NULL OR doc.state='archived' OR NOT EXISTS
      (SELECT FROM public.core_libraryresourcetransition t WHERE t.version_id=v.id AND t.resource_id=doc.id
         AND t.to_state='published' AND t.occurred_at<=cut) THEN
     RETURN jsonb_build_object('reason','Adoption needs the exact published version of an unarchived LIBRARY instrument.');
   END IF;
   IF EXISTS(SELECT FROM public.core_organismlawbinding other WHERE other.resource_id=doc.id
      AND other.adoption_event_id IS DISTINCT FROM own_event AND public.l_visible_window(other,cut,known)
      AND ((scope='PLATFORM' AND other.scope_kind<>'PLATFORM' AND
           (other.shadow_side IS NOT NULL OR other.version_id<>v.id OR other.law_class<>cls))
        OR (scope<>'PLATFORM' AND other.scope_kind='PLATFORM' AND
           (NOT lo.platform_root OR scope='CIRCLE' OR other.version_id<>v.id OR other.law_class<>cls)))) THEN
      RETURN jsonb_build_object('reason','This version would shadow inherited platform law or disagree with the root copy.');
   END IF;
   IF pin IS NOT NULL AND encode(sha256(convert_to(v.content,'UTF8')),'hex')<>pin THEN
     RETURN jsonb_build_object('reason','A different charter text requires separately authorised GRP-13 amendment evidence.');
   END IF;
   IF EXISTS (SELECT FROM public.l_clauses(v.content) g GROUP BY g.locator HAVING count(*)>1 OR length(g.locator)>64) THEN
     RETURN jsonb_build_object('reason','An adopted version needs unique clause markers of at most 64 characters.');
   END IF;
   IF open_prior AND public.l_law_rank(cls)>public.l_law_rank(previous.law_class) THEN
     RETURN jsonb_build_object('reason','A lower class cannot displace higher law.');
   END IF;
   IF (s->>'resolution_effect' IS NOT DISTINCT FROM 'STANDING')<>(cls='MEMBERS_RESOLUTION')
      OR (s->>'resolution_effect' IS NOT NULL AND s->>'resolution_effect' NOT IN ('STANDING','AMENDS_ARTICLES'))
      OR ((s->>'resolution_effect' IS NULL)<>(s->>'resolution_reference' IS NULL))
      OR (s->>'resolution_effect'='AMENDS_ARTICLES' AND cls<>'ARTICLE')
      OR (coalesce((s->>'reserves_bylaws')::boolean,false) AND cls<>'ARTICLE') THEN
     RETURN jsonb_build_object('reason','Use a standing resolution for lasting law, or cite an amending resolution on its Article.');
   END IF;
 ELSE
   v.id:=previous.version_id;
   IF s->>'version' IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_libraryresourceversion ver
     WHERE ver.id=v.id AND ver.version_number=(s->>'version')::integer) THEN
     RETURN jsonb_build_object('reason','The retirement version differs from its binding.');
   END IF;
 END IF;
 IF scope='CIRCLE' AND s->>'requested_route' IN ('GOVERNANCE_INTERIM','CQ_GOVERNANCE_CIRCLE') THEN
   RETURN jsonb_build_object('reason','Board-issued Circle terms of reference are not yet available.');
 END IF;
 owner:=public.l_class_owner(cls,public.l_reservation(lo.id,cut,known) IS NOT NULL,NULL);
 IF open_prior THEN
   SELECT o.kind INTO previous_owner FROM public.l_owner_at(previous,cut,known) o;
 END IF;
 elector:=public.l_electorate(lo.id,cut,known);
 auth:=public.l_route(lo,person,cls,coalesce(previous_owner,owner),scope,pin IS NOT NULL,s->>'requested_route',elector,cut,known);
 IF auth ? 'reason' THEN RETURN auth; END IF;
 route:=auth->>'route';
 affected:=public.l_pending(lo.id,scope='PLATFORM',CASE WHEN open_prior THEN previous.version_id END,
               CASE WHEN intent='ADOPT' THEN v.id END,previous.law_class,cls,
               previous.consequence_class,s->>'consequence_class',cut,known,previous.binding_uuid);
 affected_ref:='sha256:'||encode(sha256(convert_to('INTEVIA:LAW:AFFECTED:v1:'||
    public.l_canonical(affected),'UTF8')),'hex');
 IF jsonb_array_length(affected->'nominations')>0 AND operation<>'DEFER' THEN
   RETURN jsonb_build_object('reason','Pending nominations may be affected. Defer this change; existing law continues.','affected_reference',affected_ref,'affected',affected,'affected_count',jsonb_array_length(affected->'nominations'));
 END IF;
 IF (s->>'affected_reference' IS NOT NULL AND s->>'affected_reference'<>affected_ref)
    OR (operation='DEFER' AND (jsonb_array_length(affected->'nominations')=0 OR s->>'affected_reference' IS DISTINCT FROM affected_ref)) THEN
   RETURN jsonb_build_object('reason','Deferral needs the current affected nominations and their reference. Refresh the preview.','affected_reference',affected_ref,'affected',affected,'affected_count',jsonb_array_length(affected->'nominations'));
 END IF;
 IF intent='ADOPT' THEN
   b.binding_uuid:=proposal; b.resource_id:=doc.id; b.version_id:=v.id; b.organism_id:=lo.id;
   b.scope_kind:=scope; b.scope_circle_id:=c.id; b.law_class:=cls; b.owner_kind:=owner;
   b.decision_route:=route; b.delegated_areas:='{}'; b.reserves_bylaws:=(s->>'reserves_bylaws')::boolean;
   b.consequence_class:=s->>'consequence_class'; b.predecessor_id:=previous.binding_uuid;
   b.resolution_effect:=s->>'resolution_effect'; b.resolution_reference:=s->>'resolution_reference';
   IF b.consequence_class NOT IN ('BINDING','DECLARATORY') OR (b.consequence_class='DECLARATORY' AND
      (scope='PLATFORM' OR cls IN ('ARTICLE','BYLAW'))) THEN RETURN jsonb_build_object('reason','Choose a lawful consequence for this class.'); END IF;
   effect:=public.l_adoption_facts(b);
 ELSE effect:=jsonb_build_object('binding',previous.binding_uuid,'route',route); END IF;
 IF route='UNANIMOUS_PAIR' THEN
   SELECT a.event_id INTO agreement FROM public.core_lawact a JOIN public.core_authoritydecision d ON d.id=a.decision_id
   JOIN public.core_livingorganismevent e ON e.id=a.event_id
   WHERE a.kind='AGREE' AND a.proposal=(s->>'proposal')::uuid AND d.actor_id<>person
     AND d.target->'spec'=s AND a.plan->'electorate'=elector AND a.plan->'effect'=effect
     AND a.plan->'affected'=affected AND a.plan->>'route'=route AND d.actor_access_epoch=(elector->d.actor_id::text->>2)::bigint
     AND a.event_id IS DISTINCT FROM own_event
     AND e.recorded_at<=known AND e.effective_at<=cut ORDER BY e.sequence DESC LIMIT 1;
   IF agreement IS NULL THEN
     IF EXISTS (SELECT FROM public.core_lawact a JOIN public.core_authoritydecision d ON d.id=a.decision_id
        WHERE a.kind='AGREE' AND a.proposal=(s->>'proposal')::uuid AND d.actor_id=person
          AND a.plan->'electorate'=elector AND a.event_id IS DISTINCT FROM own_event) THEN
       RETURN jsonb_build_object('reason','Your agreement is already recorded. The other eligible member must decide independently. If the proposal has changed, start a fresh proposal.');
     END IF;
     operation:='AGREE';
   END IF;
 END IF;
 RETURN auth||jsonb_build_object('kind',operation,'effect',effect,'scope_key',reference,'open_prior',open_prior,
   'electorate',CASE WHEN route IN ('UNANIMOUS_PAIR','SOLE_MEMBER_COORDINATOR') THEN elector ELSE '{}'::jsonb END,
   'agreement',agreement,'affected_reference',affected_ref,'affected',affected,'affected_count',jsonb_array_length(affected->'nominations'));
END $$;

CREATE FUNCTION public.l_assess_request(spec jsonb, person bigint, receipt timestamptz, execution timestamptz, own_event bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE current_plan jsonb; original jsonb;
BEGIN
 current_plan:=public.l_assess(spec,person,execution,execution,own_event);
 IF current_plan ? 'reason' THEN RETURN current_plan; END IF;
 -- Membership or office acquired after receipt cannot qualify this request. Differences in nominations
 -- belong to the post-lock preview check; a missing receipt-time route never supplies authority.
 original:=public.l_assess(spec,person,receipt,receipt,own_event);
 IF original->>'route' IS DISTINCT FROM current_plan->>'route' OR original->'office' IS DISTINCT FROM current_plan->'office'
    OR original->'root_office' IS DISTINCT FROM current_plan->'root_office' OR original->'electorate' IS DISTINCT FROM current_plan->'electorate' THEN
    RETURN jsonb_build_object('reason','The authority or proposal changed after receipt. Review the current facts and submit again.');
 END IF;
 RETURN current_plan;
END $$;

-- The independent source match also protects writes made without the service.
CREATE FUNCTION public.l_intake_matches(d public.core_authoritydecision, i public.core_commandintake)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT d.action='ADOPT_LAW' AND i.entry=d.action AND i.action=d.action
 AND d.command_subtype='ADOPT_LAW/LAW_VERSION'
 AND d.actor_id=i.actor_id AND d.actor_identity_id=i.actor_identity_id AND d.actor_access_epoch=i.actor_access_epoch
 AND d.request_reference=i.request_reference AND d.idempotency_key=i.idempotency_key AND d.evaluated_at=i.received_at
 AND (d.target->>'intake')::bigint=i.id AND d.target->>'fingerprint'=i.request_fingerprint
 AND i.request_fingerprint=encode(sha256(convert_to('INTEVIA:S015:COMMAND_INTAKE:v1','UTF8')||decode('00','hex')||
  convert_to(public.l_canonical(jsonb_build_object('schema',i.fingerprint_schema,'entry',i.entry,'action',i.action,
    'actor_identity_id',i.actor_identity_id,'actor_access_epoch',i.actor_access_epoch,'request_reference',i.request_reference,
    'idempotency_key',i.idempotency_key,'target',jsonb_build_object('organism',d.target->'spec'->>'organism',
     'resource',d.target->'spec'->>'resource'),'command',d.target->'spec','omitted','[]'::jsonb)),'UTF8')),'hex')
 AND d.target->'spec' ?& ARRAY['proposal','organism','resource','operation','scope','law_class','intent']
 AND d.target->>'living_organism_id'=d.target->'spec'->>'organism'
$$;
CREATE FUNCTION public.l_evidence(plan jsonb) RETURNS text LANGUAGE sql STABLE STRICT AS $$
 SELECT public.l_act_reference(plan->>'kind',
    CASE WHEN plan->>'kind' IN ('ADOPT','RETIRE') THEN plan->'effect' ELSE plan END)
$$;
CREATE OR REPLACE FUNCTION public.l_qualified_operation(event_id bigint) RETURNS boolean
LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_lawact a
 JOIN public.core_livingorganismevent e ON e.id=a.event_id
 JOIN public.core_livingorganism lo ON lo.id=e.living_organism_id
 JOIN public.core_authoritydecision d ON d.id=a.decision_id
 JOIN public.core_commandintake i ON i.id=a.intake_id
 WHERE a.event_id=$1 AND public.l_intake_matches(d,i) AND d.outcome='QUALIFIED'
 AND a.plan=d.target->'plan' AND a.kind=a.plan->>'kind' AND a.proposal=(d.target->'spec'->>'proposal')::uuid
 AND lo.organism_id=(d.target->>'living_organism_id')::uuid AND e.action=d.action
 AND e.enterer_identity_id=i.actor_id AND e.authority_basis_id=lo.founding_authority_basis_id
 AND e.actor_state='LIVING_ORGANISM' AND e.actor_capacity='STANDING' AND e.entry_mode='TRANSPORT'
 AND e.actor_id IS NULL AND e.actor_access_epoch IS NULL AND e.prior_state=e.resulting_state
 AND e.authority_decision_reference=d.decision_reference AND e.evidence_reference=public.l_evidence(a.plan)
 AND e.idempotency_key=i.idempotency_key AND e.request_reference=i.request_reference
 AND e.received_at=i.received_at AND e.effective_at=e.recorded_at AND e.payload_fingerprint=i.request_fingerprint
 AND d.authority_reference='s015a1:'||public.l_canonical(jsonb_build_object('kind','LAW_VERSION','route',a.plan->>'route'))
 AND d.provider_identity='INTEVIA.S015.ApplicationAuthorityProvider'
 AND EXISTS(SELECT FROM public.core_identity person WHERE person.id=i.actor_id AND person.access_epoch=i.actor_access_epoch)
 AND d.recorded_at<=e.effective_at AND i.received_at<=d.effective_at AND d.effective_at<=e.effective_at)
$$;

CREATE FUNCTION public.l_execute(decision_ref text) RETURNS bigint LANGUAGE plpgsql VOLATILE AS $$
DECLARE d public.core_authoritydecision; i public.core_commandintake; lo public.core_livingorganism;
 head public.core_livingorganismevent; plan jsonb; spec jsonb; kind_ text; event_pk bigint;
 cut timestamptz:=statement_timestamp(); state_ text;
BEGIN
 SELECT * INTO STRICT d FROM public.core_authoritydecision WHERE decision_reference=decision_ref;
 SELECT * INTO STRICT i FROM public.core_commandintake WHERE id=(d.target->>'intake')::bigint;
 IF public.l_intake_matches(d,i) IS NOT TRUE OR d.outcome<>'QUALIFIED'
   OR d.l_execution_xid IS DISTINCT FROM pg_current_xact_id() THEN
   RAISE EXCEPTION 'LAW requires the qualified decision for this exact intake' USING ERRCODE='23514';
 END IF;
 spec:=d.target->'spec';
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE organism_id=(spec->>'organism')::uuid FOR UPDATE;
 PERFORM 1 FROM public.core_libraryresource WHERE resource_id=spec->>'resource' FOR UPDATE;
 plan:=public.l_assess_request(spec,i.actor_id,i.received_at,cut);
 IF plan ? 'reason' OR plan IS DISTINCT FROM d.target->'plan' THEN
   RAISE EXCEPTION 'LAW authority or affected nominations changed before execution; refresh the proposal' USING ERRCODE='40001';
 END IF;
 kind_:=plan->>'kind';
 SELECT * INTO STRICT head FROM public.core_livingorganismevent WHERE id=lo.head_event_id;
 state_:=public.s015_fold_effective_state('core_livingorganism',lo.id,cut,cut);
 event_pk:=public.s015_append_event('core_livingorganism',lo.id,'ADOPT_LAW',state_,cut,i.received_at,
   i.received_at,i.actor_id,lo.founding_authority_basis_id,d.decision_reference,public.l_evidence(plan),
   i.request_reference,i.request_fingerprint,'{}'::jsonb,NULL,gen_random_uuid(),head.id,
   head.sequence::integer,NULL::bigint,NULL::text,i.idempotency_key);
 INSERT INTO public.core_lawact VALUES(event_pk,i.id,d.id,(spec->>'proposal')::uuid,kind_,plan);
 IF kind_='RETIRE' OR (kind_='ADOPT' AND (plan->>'open_prior')::boolean) THEN
   INSERT INTO public.core_organismlawending(binding_id,end_event_id,decision_route,end_kind,successor_id)
   VALUES((spec->>'predecessor')::uuid,event_pk,plan->>'route',
     CASE WHEN kind_='ADOPT' THEN 'SUPERSEDED' ELSE 'RETIRED' END,
     CASE WHEN kind_='ADOPT' THEN (spec->>'proposal')::uuid END);
 END IF;
 IF kind_='ADOPT' THEN
   INSERT INTO public.core_organismlawbinding SELECT b.* FROM jsonb_populate_record(NULL::public.core_organismlawbinding,
      (plan->'effect')||jsonb_build_object('adoption_event_id',event_pk,'in_force_from',cut)) AS b;
 END IF;
 RETURN event_pk;
END $$;

CREATE FUNCTION public.l_receipt_payload(d public.core_authoritydecision) RETURNS jsonb LANGUAGE sql STABLE STRICT AS $$
 SELECT CASE WHEN d.outcome='REFUSED' THEN jsonb_strip_nulls(jsonb_build_object(
   'status','REFUSED','message',coalesce(d.target->'plan'->>'reason','This identity cannot authorise the requested law change.'),
   'affected_reference',d.target->'plan'->>'affected_reference'))
 ELSE (SELECT jsonb_build_object('status',a.kind,'event',e.event_uuid,'proposal',a.proposal,'route',a.plan->>'route')
   FROM public.core_lawact a JOIN public.core_livingorganismevent e ON a.event_id=e.id WHERE a.decision_id=d.id) END
$$;
CREATE FUNCTION public.l_receipt_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d public.core_authoritydecision; i public.core_commandintake; lineage text;
BEGIN
 SELECT * INTO STRICT d FROM public.core_authoritydecision WHERE decision_reference=NEW.authority_decision_reference;
 SELECT * INTO STRICT i FROM public.core_commandintake WHERE id=(d.target->>'intake')::bigint;
 IF d.outcome='QUALIFIED' THEN
   SELECT e.lineage_reference INTO STRICT lineage FROM public.core_lawact a JOIN public.core_livingorganismevent e
     ON e.id=a.event_id WHERE a.decision_id=d.id AND public.l_qualified_operation(e.id);
 ELSE
   lineage:='s015l1:'||encode(sha256(convert_to(d.decision_reference||':LAW:REFUSED','UTF8')),'hex');
 END IF;
 IF d.l_execution_xid IS DISTINCT FROM pg_current_xact_id()
   OR public.l_intake_matches(d,i) IS NOT TRUE OR NEW.actor_id<>i.actor_id OR NEW.action<>i.action
   OR NEW.request_reference<>i.request_reference OR NEW.idempotency_key<>i.idempotency_key
   OR NEW.payload_fingerprint<>i.request_fingerprint OR NEW.lineage_reference IS DISTINCT FROM lineage
   OR NEW.result_payload IS DISTINCT FROM public.l_receipt_payload(d) THEN
   RAISE EXCEPTION 'LAW receipt differs from its decision or effect' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER l_receipt_input BEFORE INSERT ON public.core_organismcommandreceipt
 FOR EACH ROW WHEN (NEW.action='ADOPT_LAW') EXECUTE FUNCTION public.l_receipt_input();

CREATE FUNCTION public.l_act_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a public.core_lawact; d public.core_authoritydecision; spec jsonb; checked jsonb; cutoff timestamptz;
BEGIN
 SELECT * INTO STRICT a FROM public.core_lawact WHERE event_id=NEW.id;
 SELECT * INTO STRICT d FROM public.core_authoritydecision WHERE id=a.decision_id;
 spec:=d.target->'spec';
 IF NOT public.l_qualified_operation(NEW.id) OR NOT EXISTS(SELECT FROM public.core_organismcommandreceipt r
      WHERE r.action='ADOPT_LAW' AND r.authority_decision_reference=d.decision_reference)
   OR (a.kind='ADOPT') <> EXISTS(SELECT FROM public.core_organismlawbinding b WHERE b.adoption_event_id=NEW.id)
   OR (a.kind='RETIRE' OR (a.kind='ADOPT' AND (a.plan->>'open_prior')::boolean)) <>
       EXISTS(SELECT FROM public.core_organismlawending x WHERE x.end_event_id=NEW.id) THEN
   RAISE EXCEPTION 'LAW event requires its receipt, exact qualified operation and complete effects' USING ERRCODE='23514';
 END IF;
 checked:=public.l_assess_request(spec,d.actor_id,NEW.received_at,NEW.effective_at,NEW.id);
 cutoff:=clock_timestamp();
 IF checked IS DISTINCT FROM a.plan OR public.l_pending(NEW.living_organism_id,spec->>'scope'='PLATFORM',
   CASE WHEN (a.plan->>'open_prior')::boolean THEN
    (SELECT b.version_id FROM public.core_organismlawbinding b WHERE b.binding_uuid=(spec->>'predecessor')::uuid) END,
   CASE WHEN spec->>'intent'='ADOPT' THEN (a.plan->'effect'->>'version_id')::bigint END,
   (SELECT b.law_class FROM public.core_organismlawbinding b WHERE b.binding_uuid=(spec->>'predecessor')::uuid),
   spec->>'law_class',
   (SELECT b.consequence_class FROM public.core_organismlawbinding b WHERE b.binding_uuid=(spec->>'predecessor')::uuid),
   spec->>'consequence_class',cutoff,cutoff,(spec->>'predecessor')::uuid) IS DISTINCT FROM a.plan->'affected' THEN
   RAISE EXCEPTION 'LAW authority or affected nominations differ at the final check' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER l_act_complete AFTER INSERT ON public.core_livingorganismevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.action='ADOPT_LAW') EXECUTE FUNCTION public.l_act_complete();

CREATE FUNCTION public.l_act_input() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.id=NEW.event_id AND e.action='ADOPT_LAW'
               ) OR NOT EXISTS (
       SELECT FROM public.core_authoritydecision d WHERE d.id=NEW.decision_id AND d.l_execution_xid=pg_current_xact_id()) THEN
   RAISE EXCEPTION 'LAW recording requires its own new event' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER l_act_input BEFORE INSERT ON public.core_lawact FOR EACH ROW EXECUTE FUNCTION public.l_act_input();
CREATE TRIGGER l_immutable BEFORE UPDATE OR DELETE ON public.core_lawact
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_l_no_truncate BEFORE TRUNCATE ON public.core_lawact
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER l_immutable BEFORE UPDATE OR DELETE ON public.core_lawprovisionlocator
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_l_no_truncate BEFORE TRUNCATE ON public.core_lawprovisionlocator
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
"""

REVERSE = r"""
DROP TRIGGER l_decision_time ON public.core_authoritydecision;
ALTER TABLE public.core_authoritydecision DROP COLUMN l_execution_xid;
DROP FUNCTION public.l_decision_time();
DROP TRIGGER l_link_provision ON public.core_organismroledefinitionprovision;
DROP FUNCTION public.l_link_provision();
DROP TRIGGER l_resource_identity ON public.core_libraryresource;
DROP TRIGGER l_act_complete ON public.core_livingorganismevent;
DROP TRIGGER l_receipt_input ON public.core_organismcommandreceipt;
DROP TABLE public.core_lawact, public.core_lawprovisionlocator;
DROP FUNCTION public.l_act_input(), public.l_act_complete(), public.l_receipt_input(),
 public.l_receipt_payload(public.core_authoritydecision), public.l_execute(text),
 public.l_evidence(jsonb), public.l_intake_matches(public.core_authoritydecision,public.core_commandintake);
DROP FUNCTION public.l_resource_identity(),public.l_lock_dependencies(jsonb,bigint,bigint),
 public.l_assess_request(jsonb,bigint,timestamptz,timestamptz,bigint),public.l_assess(jsonb,bigint,timestamptz,timestamptz,bigint),
 public.l_route(public.core_livingorganism,bigint,text,text,text,boolean,text,jsonb,timestamptz,timestamptz),
 public.l_electorate(bigint,timestamptz,timestamptz),
 public.l_pending(bigint,boolean,bigint,bigint,text,text,text,text,timestamptz,timestamptz,uuid),
 public.l_locator_input(), public.l_charter_pin(text), public.l_clauses(text), public.l_canonical(jsonb);
"""

WRITER_BRANCH = """
 IF is_command AND token='core_livingorganism' AND action_name='ADOPT_LAW' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM statement_timestamp()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'LAW requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;
"""

GATEWAY_BRANCH = """
    IF NEW.action='ADOPT_LAW' THEN
      IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.sequence<=1 OR NEW.entry_mode<>'TRANSPORT'
        OR NEW.actor_state<>'LIVING_ORGANISM' OR NEW.actor_capacity<>'STANDING' OR NEW.actor_id IS NOT NULL
        OR NEW.actor_access_epoch IS NOT NULL OR NEW.effective_at IS DISTINCT FROM statement_timestamp()
        OR NEW.composing_rule_state<>'NA_TRANSPORT' OR NEW.composer_state<>'NA_TRANSPORT'
        OR NOT EXISTS(SELECT FROM public.core_authoritydecision d JOIN public.core_commandintake i
          ON i.id=(d.target->>'intake')::bigint JOIN public.core_livingorganism lo ON lo.id=NEW.living_organism_id
          WHERE d.decision_reference=NEW.authority_decision_reference AND public.l_intake_matches(d,i)
            AND d.outcome='QUALIFIED' AND d.target->>'living_organism_id'=lo.organism_id::text
            AND d.actor_id=NEW.enterer_identity_id AND NEW.evidence_reference=public.l_evidence(d.target->'plan')
            AND NEW.authority_basis_id=lo.founding_authority_basis_id AND NEW.received_at=i.received_at
            AND NEW.payload_fingerprint=i.request_fingerprint) THEN
        RAISE EXCEPTION 'LAW transport does not match its qualified decision' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;
"""


def switch(editor, *, backwards):
    base_writer = root.writer_body()
    law_writer = root.replace_once(base_writer, " SELECT string_agg", WRITER_BRANCH + " SELECT string_agg")
    old = gateway._ENTITLEMENT_ELECTION_GATEWAY_BODY
    new = root.replace_once(old, "BEGIN\n", "BEGIN\n" + GATEWAY_BRANCH)
    signature = "public.s3fu_guard_gateway_effect()"
    with editor.connection.cursor() as cur:
        cur.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [signature])
        if cur.fetchone()[0].strip() != (new if backwards else old).strip():
            raise RuntimeError("LAW stopped: the installed gateway guard differs from its commissioned body.")
    writer.switch_writer(editor, law_writer if backwards else base_writer, base_writer if backwards else law_writer)
    editor.execute("CREATE OR REPLACE FUNCTION " + signature + " RETURNS trigger LANGUAGE plpgsql AS $$"
                   + (old if backwards else new) + "$$;", params=None)


def install(apps, editor):
    editor.execute(FORWARD, params=None)
    switch(editor, backwards=False)


def reverse(apps, editor):
    with editor.connection.cursor() as cur:
        cur.execute("LOCK TABLE public.core_commandintake,public.core_authoritydecision,public.core_lawact,"
                    "public.core_lawprovisionlocator IN ACCESS EXCLUSIVE MODE")
        cur.execute("SELECT EXISTS(SELECT FROM public.core_commandintake WHERE entry='ADOPT_LAW')"
                    " OR EXISTS(SELECT FROM public.core_authoritydecision WHERE action='ADOPT_LAW')"
                    " OR EXISTS(SELECT FROM public.core_lawact) OR EXISTS(SELECT FROM public.core_lawprovisionlocator)")
        if cur.fetchone()[0]:
            raise RuntimeError("LAW reversal refused while an intake, citation or decision exists.")
    switch(editor, backwards=True)
    editor.execute("CREATE OR REPLACE FUNCTION public.l_qualified_operation(event_id bigint) RETURNS boolean "
                   "LANGUAGE sql STABLE STRICT AS $$ SELECT false $$;", params=None)
    editor.execute(REVERSE, params=None)


class Migration(migrations.Migration):
    dependencies = [("core", "0066_law_records")]
    operations = [
        migrations.AlterField(model_name="commandintake", name="entry",
                              field=models.CharField(max_length=40, choices=CHOICES)),
        migrations.RemoveConstraint(model_name="commandintake", name=prior.SR.INTAKE_CHECK),
        migrations.AddConstraint(model_name="commandintake", constraint=models.CheckConstraint(
            condition=models.Q(entry__in=[choice for choice, _label in CHOICES]), name=prior.SR.INTAKE_CHECK)),
        migrations.RunPython(install, reverse),
    ]
