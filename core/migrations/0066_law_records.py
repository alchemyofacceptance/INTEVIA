"""LAW-1/F. SQL-owned projections of the shared ILC. No product rows are seeded."""

from django.db import migrations

FORWARD = r"""
-- The built-in range GiST operator classes require no extension.

CREATE FUNCTION public.l_law_rank(class_name text) RETURNS integer
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
 SELECT array_position(ARRAY['ARTICLE','MEMBERS_RESOLUTION','BYLAW','POLICY','PROCEDURE'], class_name)
$$;

-- The ToR names each delegated area. An empty area object uses the ruled defaults.
CREATE FUNCTION public.l_valid_areas(areas jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE area record; entry record;
BEGIN
 IF jsonb_typeof(areas) <> 'object' THEN RETURN false; END IF;
 FOR area IN SELECT * FROM jsonb_each(areas) LOOP
   IF btrim(area.key) = '' OR jsonb_typeof(area.value) <> 'object' THEN RETURN false; END IF;
   FOR entry IN SELECT * FROM jsonb_each_text(area.value) LOOP
     IF entry.key NOT IN ('POLICY','PROCEDURE') OR entry.value IS NULL
        OR entry.value NOT IN ('PROPOSE','ADOPT') THEN RETURN false; END IF;
   END LOOP;
 END LOOP;
 RETURN true;
END $$;

CREATE FUNCTION public.l_tor_right(areas jsonb, area text, law_class text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT CASE WHEN public.l_valid_areas(areas) AND areas ? area AND law_class IN ('POLICY','PROCEDURE')
   THEN coalesce(areas -> area ->> law_class, CASE law_class WHEN 'POLICY' THEN 'PROPOSE' ELSE 'ADOPT' END)
 END
$$;

CREATE FUNCTION public.l_valid_route(route text) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT route IN ('FOUNDER_CONSTITUTIVE','COORDINATOR_ROUTE','SOLE_MEMBER_COORDINATOR','UNANIMOUS_PAIR',
    'GOVERNANCE_INTERIM','CIRCLE_INTERIM','CQ_MEMBERSHIP','CQ_GOVERNANCE_CIRCLE','CQ_CIRCLE',
    'FOUNDER_STEWARD','STEWARDS_PROPOSE_FOUNDER_AUTHORISES','STEWARDS_AUTHORISED')
$$;

CREATE TABLE public.core_organismlawbinding (
 binding_uuid uuid PRIMARY KEY,
 resource_id bigint NOT NULL REFERENCES public.core_libraryresource,
 version_id bigint NOT NULL REFERENCES public.core_libraryresourceversion,
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism,
 scope_kind text NOT NULL CHECK (scope_kind IN ('LIVING_ORGANISM','PLATFORM','CIRCLE')),
 scope_key text NOT NULL,
 scope_circle_id bigint REFERENCES public.core_circle,
 law_class text NOT NULL CHECK (law_class IN
   ('ARTICLE','MEMBERS_RESOLUTION','BYLAW','POLICY','PROCEDURE','TERMS_OF_REFERENCE')),
 consequence_class text NOT NULL CHECK (consequence_class IN ('BINDING','DECLARATORY')),
 decision_route text NOT NULL CHECK (public.l_valid_route(decision_route)),
 owner_kind text NOT NULL CHECK (owner_kind IN ('MEMBERS','GOVERNANCE','LO_COORDINATOR','CIRCLE')),
 owner_circle_id bigint REFERENCES public.core_circle,
 proposing_circle_id bigint REFERENCES public.core_circle,
 terms_binding_id uuid REFERENCES public.core_organismlawbinding,
 area text CHECK (area IS NULL OR btrim(area) <> ''),
 delegated_areas jsonb NOT NULL DEFAULT '{}' CHECK (public.l_valid_areas(delegated_areas)),
 reserves_bylaws boolean NOT NULL DEFAULT false,
 resolution_effect text CONSTRAINT l_resolution_effect_kind CHECK (resolution_effect IN ('STANDING','AMENDS_ARTICLES')),
 resolution_reference text CHECK (resolution_reference IS NULL OR btrim(resolution_reference) <> ''),
 predecessor_id uuid UNIQUE REFERENCES public.core_organismlawbinding,
 adoption_event_id bigint NOT NULL UNIQUE REFERENCES public.core_livingorganismevent,
 in_force_from timestamptz NOT NULL CHECK (isfinite(in_force_from)),
 in_force_until timestamptz CHECK (isfinite(in_force_until) AND in_force_until > in_force_from),
 -- Set from the verified root discriminator and scope by the BEFORE INSERT trigger.
 -- Root's own LO binding and its inherited copy may coexist.
 shadow_side smallint CHECK (shadow_side IN (1,2)),
 CHECK ((scope_kind = 'CIRCLE') = (scope_circle_id IS NOT NULL)),
 CHECK ((scope_kind = 'CIRCLE') = (law_class = 'TERMS_OF_REFERENCE')),
 CHECK ((owner_kind = 'CIRCLE') = (owner_circle_id IS NOT NULL)),
 CHECK (scope_circle_id IS NULL OR owner_circle_id IS DISTINCT FROM scope_circle_id),
 CHECK ((proposing_circle_id IS NULL) = (terms_binding_id IS NULL)
   AND (terms_binding_id IS NULL) = (area IS NULL)),
 CHECK (owner_kind <> 'CIRCLE' OR (proposing_circle_id IS NOT NULL AND proposing_circle_id = owner_circle_id)),
 CHECK (NOT reserves_bylaws OR law_class = 'ARTICLE'),
 CHECK (delegated_areas = '{}' OR law_class = 'TERMS_OF_REFERENCE'),
 CHECK (resolution_effect IS DISTINCT FROM 'STANDING' OR law_class='MEMBERS_RESOLUTION'),
 CHECK (law_class<>'MEMBERS_RESOLUTION' OR resolution_effect IS NOT NULL),
 CHECK ((resolution_effect IS NULL) = (resolution_reference IS NULL)),
 CHECK (resolution_effect IS DISTINCT FROM 'AMENDS_ARTICLES' OR law_class = 'ARTICLE'),
 CHECK (consequence_class = 'BINDING' OR (scope_kind <> 'PLATFORM' AND law_class NOT IN ('ARTICLE','BYLAW'))),
 CHECK (predecessor_id IS DISTINCT FROM binding_uuid),
 CONSTRAINT l_one_version_per_scope EXCLUDE USING gist
   (numrange(resource_id::numeric,resource_id::numeric,'[]') WITH &&,
    int4range((CASE scope_kind WHEN 'PLATFORM' THEN 0 WHEN 'CIRCLE' THEN 1 ELSE 2 END),
              (CASE scope_kind WHEN 'PLATFORM' THEN 0 WHEN 'CIRCLE' THEN 1 ELSE 2 END),'[]') WITH &&,
    numrange((CASE scope_kind WHEN 'PLATFORM' THEN 0 WHEN 'CIRCLE' THEN scope_circle_id ELSE organism_id END)::numeric,
             (CASE scope_kind WHEN 'PLATFORM' THEN 0 WHEN 'CIRCLE' THEN scope_circle_id ELSE organism_id END)::numeric,'[]') WITH &&,
    tstzrange(in_force_from,in_force_until,'[)') WITH &&)
   DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX l_binding_history ON public.core_organismlawbinding (organism_id,resource_id,in_force_from);
CREATE INDEX l_resource_window ON public.core_organismlawbinding (resource_id,in_force_until);
CREATE INDEX l_scope_history ON public.core_organismlawbinding (resource_id,scope_key,in_force_from);

CREATE TABLE public.core_organismlawending (
 binding_id uuid PRIMARY KEY REFERENCES public.core_organismlawbinding,
 end_event_id bigint NOT NULL UNIQUE REFERENCES public.core_livingorganismevent,
 decision_route text NOT NULL CHECK (public.l_valid_route(decision_route)),
 end_kind text NOT NULL CHECK (end_kind IN ('SUPERSEDED','RETIRED')),
 successor_id uuid UNIQUE REFERENCES public.core_organismlawbinding DEFERRABLE INITIALLY DEFERRED,
 CHECK ((end_kind = 'SUPERSEDED') = (successor_id IS NOT NULL)),
 CHECK (binding_id IS DISTINCT FROM successor_id)
);

CREATE TABLE public.core_lawresolutionlabel (
 event_id bigint PRIMARY KEY REFERENCES public.core_livingorganismevent,
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism,
 display_label text NOT NULL CHECK (btrim(display_label) <> '')
);

-- An SE-owner mapping is required before any of the subscriber-only paths can open.
-- The capability catalog at the commissioned base has no suitable entry.
CREATE FUNCTION public.l_subscriber_capability(feature text, lo uuid, cut timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT coalesce((SELECT public.s015_entitlement_effective(lo, m.code, cut)
   FROM (VALUES ('CIRCLE_DELEGATION',NULL::text), ('ISSUE_CIRCLE_TERMS',NULL::text),
                ('RESOLUTION_LABEL',NULL::text)) AS m(feature_name,code)
   WHERE m.feature_name=feature AND m.code IS NOT NULL), false)
$$;

CREATE FUNCTION public.l_class_owner(law_class text, reserved boolean, delegated_right text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE
   WHEN law_class IN ('ARTICLE','MEMBERS_RESOLUTION') OR (law_class = 'BYLAW' AND reserved) THEN 'MEMBERS'
   WHEN law_class = 'BYLAW' THEN 'GOVERNANCE'
   WHEN law_class IN ('POLICY','PROCEDURE') AND delegated_right = 'ADOPT' THEN 'CIRCLE'
   WHEN law_class IN ('POLICY','PROCEDURE') AND delegated_right = 'PROPOSE' THEN 'GOVERNANCE'
   WHEN law_class IN ('POLICY','PROCEDURE','TERMS_OF_REFERENCE') THEN 'LO_COORDINATOR'
 END
$$;

-- Root may carry a local copy of the platform instrument. At an overlapping cut
-- those two records must cite the same exact version.


CREATE FUNCTION public.l_check_platform_pair() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 -- l_prepare_binding already takes a new version of the shared resource row.
 -- Read final windows, including any predecessor closed in this transaction.
 IF EXISTS (SELECT FROM public.core_organismlawbinding p JOIN public.core_organismlawbinding other
      ON other.resource_id=p.resource_id AND other.scope_kind<>'PLATFORM'
      AND tstzrange(other.in_force_from,other.in_force_until,'[)') && tstzrange(p.in_force_from,p.in_force_until,'[)')
      WHERE p.resource_id=NEW.resource_id AND p.scope_kind='PLATFORM'
      AND (other.shadow_side IS NOT NULL OR other.version_id<>p.version_id OR other.law_class<>p.law_class)) THEN
   RAISE EXCEPTION 'A local law would shadow or disagree with inherited platform law'
       USING ERRCODE='23P01', CONSTRAINT='l_no_platform_shadow';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER l_no_platform_shadow AFTER INSERT ON public.core_organismlawbinding
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.l_check_platform_pair();

CREATE FUNCTION public.l_visible_window(b public.core_organismlawbinding, cut timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT b.in_force_from <= cut AND EXISTS (
   SELECT FROM public.core_livingorganismevent a
   WHERE a.id = b.adoption_event_id AND a.recorded_at <= known)
 AND NOT EXISTS (
   SELECT FROM public.core_organismlawending x
   JOIN public.core_livingorganismevent e ON e.id = x.end_event_id
   WHERE x.binding_id = b.binding_uuid AND e.effective_at <= cut AND e.recorded_at <= known)
$$;

CREATE FUNCTION public.l_reservation(lo bigint, cut timestamptz, known timestamptz)
RETURNS uuid LANGUAGE sql STABLE STRICT AS $$
 SELECT b.binding_uuid FROM public.core_organismlawbinding b
 WHERE b.organism_id = lo AND b.scope_kind = 'LIVING_ORGANISM' AND b.reserves_bylaws
 AND public.l_visible_window(b,cut,known) ORDER BY b.in_force_from DESC,b.binding_uuid LIMIT 1
$$;

CREATE FUNCTION public.l_prepare_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo public.core_livingorganism; circle public.core_circle; terms public.core_organismlawbinding;
        doc public.core_libraryresource; prior public.core_organismlawbinding; right_ text;
        expected_owner text; receipt timestamptz;
BEGIN
 -- Same aggregate order as the command route. Lock and read in separate queries.
 PERFORM 1 FROM public.core_livingorganism WHERE id = NEW.organism_id FOR UPDATE;
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id = NEW.organism_id;
 -- A new row version makes an older REPEATABLE READ archive transaction retry
 -- even when the library fields have exactly the same values after adoption.
 UPDATE public.core_libraryresource r SET updated_at=r.updated_at WHERE r.id=NEW.resource_id
   RETURNING r.* INTO STRICT doc;
 SELECT received_at INTO STRICT receipt FROM public.core_livingorganismevent WHERE id = NEW.adoption_event_id;
 IF NEW.in_force_until IS NOT NULL THEN
   RAISE EXCEPTION 'An ending needs its own recorded cause' USING ERRCODE='23514';
 END IF;
 IF doc.state = 'archived' OR EXISTS (
   SELECT FROM public.core_libraryresourcetransition WHERE resource_id=NEW.resource_id
   AND to_state='archived' AND occurred_at<=receipt)
 OR NOT EXISTS (
   SELECT FROM public.core_libraryresourceversion v JOIN public.core_libraryresourcetransition t
     ON t.version_id=v.id AND t.resource_id=v.resource_id
   WHERE v.id=NEW.version_id AND v.resource_id=NEW.resource_id
     AND t.to_state='published' AND t.occurred_at<=NEW.in_force_from) THEN
   RAISE EXCEPTION 'Law needs publication of this exact version on an unarchived resource' USING ERRCODE='23514';
 END IF;
 IF NEW.scope_kind = 'PLATFORM' THEN
   IF NOT lo.platform_root OR NEW.decision_route NOT IN
      ('FOUNDER_STEWARD','STEWARDS_PROPOSE_FOUNDER_AUTHORISES','STEWARDS_AUTHORISED') THEN
     RAISE EXCEPTION 'Only the root LAW route may adopt inherited law' USING ERRCODE='23514';
   END IF;
   NEW.scope_key := 'PLATFORM';
   NEW.shadow_side := 1;
 ELSIF NEW.scope_kind = 'CIRCLE' THEN
   SELECT * INTO STRICT circle FROM public.core_circle WHERE id = NEW.scope_circle_id AND parent_organism_id=lo.id;
   NEW.scope_key := 'CIRCLE:' || circle.circle_uuid::text;
   NEW.shadow_side := 2;
 ELSE
   NEW.scope_key := 'LO:' || lo.organism_id::text;
   NEW.shadow_side := CASE WHEN lo.platform_root THEN NULL ELSE 2 END;
 END IF;
 IF NEW.predecessor_id IS NOT NULL THEN
   SELECT * INTO STRICT prior FROM public.core_organismlawbinding WHERE binding_uuid=NEW.predecessor_id;
   IF prior.resource_id<>NEW.resource_id OR prior.scope_key<>NEW.scope_key
      OR prior.in_force_from>=NEW.in_force_from
      OR (public.l_law_rank(NEW.law_class)>public.l_law_rank(prior.law_class) AND NOT EXISTS
         (SELECT FROM public.core_organismlawending x WHERE x.binding_id=prior.binding_uuid
            AND x.end_kind='RETIRED' AND prior.in_force_until<=NEW.in_force_from)) THEN
     RAISE EXCEPTION 'A different scope, instrument or lower class cannot supersede this law' USING ERRCODE='23514';
   END IF;
 END IF;
 IF NEW.terms_binding_id IS NOT NULL THEN
   SELECT * INTO terms FROM public.core_organismlawbinding WHERE binding_uuid=NEW.terms_binding_id;
   IF NOT FOUND OR terms.scope_kind<>'CIRCLE' OR terms.organism_id<>lo.id
      OR terms.scope_circle_id IS DISTINCT FROM NEW.proposing_circle_id
      OR EXISTS (SELECT FROM public.core_circlestateevent closing WHERE closing.circle_id=NEW.proposing_circle_id
                 AND closing.resulting_state='CLOSED' AND closing.effective_at<=NEW.in_force_from
                 AND closing.recorded_at<=NEW.in_force_from)
      OR NOT public.l_visible_window(terms,NEW.in_force_from,NEW.in_force_from) THEN
     RAISE EXCEPTION 'The proposal needs this Circle’s governing terms at execution' USING ERRCODE='23514';
   END IF;
   right_ := public.l_tor_right(terms.delegated_areas,NEW.area,NEW.law_class);
   IF right_ IS NULL OR NOT public.l_subscriber_capability('CIRCLE_DELEGATION',lo.organism_id,NEW.in_force_from) THEN
     RAISE EXCEPTION 'Circle delegation needs a named ToR area and an effective SE capability' USING ERRCODE='23514';
   END IF;
 END IF;
 expected_owner := public.l_class_owner(NEW.law_class,
   public.l_reservation(lo.id,NEW.in_force_from,NEW.in_force_from) IS NOT NULL,right_);
 IF NEW.law_class='TERMS_OF_REFERENCE' AND
    (NEW.owner_kind='GOVERNANCE' OR NEW.delegated_areas <> '{}' OR NEW.decision_route IN ('GOVERNANCE_INTERIM','CQ_GOVERNANCE_CIRCLE')) THEN
   IF NOT public.l_subscriber_capability('ISSUE_CIRCLE_TERMS',lo.organism_id,NEW.in_force_from) THEN
     RAISE EXCEPTION 'Board-issued Circle terms require an effective SE capability' USING ERRCODE='23514';
   END IF;
   expected_owner := 'GOVERNANCE';
 END IF;
 IF NEW.owner_kind IS DISTINCT FROM expected_owner THEN
   RAISE EXCEPTION 'LAW ownership does not match the class, Article reservation and ToR area' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER l_prepare_binding BEFORE INSERT ON public.core_organismlawbinding
 FOR EACH ROW EXECUTE FUNCTION public.l_prepare_binding();

-- Historical ownership records the transfer cause without changing the adoption.
-- Circle closure already has an immutable chain. The parent in this schema is the LO.
CREATE FUNCTION public.l_owner_at(b public.core_organismlawbinding, cut timestamptz, known timestamptz)
RETURNS TABLE(kind text, circle_id bigint, parent_lo_id bigint, cause uuid)
LANGUAGE sql STABLE STRICT AS $$
 WITH closing AS (
   SELECT e.event_uuid FROM public.core_circlestateevent e
   WHERE e.circle_id=b.owner_circle_id AND e.resulting_state='CLOSED'
     AND e.effective_at<=cut AND e.recorded_at<=known
   ORDER BY e.effective_at,e.sequence LIMIT 1
 ), reservation AS (SELECT public.l_reservation(b.organism_id,cut,known) AS binding),
 assignment AS (SELECT e.event_uuid FROM public.core_livingorganismevent e WHERE e.id=b.adoption_event_id)
 SELECT CASE WHEN closing.event_uuid IS NOT NULL THEN 'PARENT_LO'
             WHEN b.law_class='BYLAW' THEN CASE WHEN reservation.binding IS NULL THEN 'GOVERNANCE' ELSE 'MEMBERS' END
             ELSE b.owner_kind END,
        CASE WHEN closing.event_uuid IS NULL THEN b.owner_circle_id END, b.organism_id,
        coalesce(closing.event_uuid, marker.event_uuid, release.event_uuid, assignment.event_uuid)
 FROM assignment CROSS JOIN reservation LEFT JOIN closing ON true
 LEFT JOIN public.core_organismlawbinding article ON b.law_class='BYLAW' AND article.binding_uuid=reservation.binding
 LEFT JOIN public.core_livingorganismevent marker ON marker.id=article.adoption_event_id
 LEFT JOIN LATERAL (
   SELECT e.event_uuid FROM public.core_organismlawbinding old_article
   JOIN public.core_organismlawending x ON x.binding_id=old_article.binding_uuid
   JOIN public.core_livingorganismevent e ON e.id=x.end_event_id
   WHERE old_article.organism_id=b.organism_id AND old_article.scope_kind='LIVING_ORGANISM'
     AND old_article.reserves_bylaws AND e.effective_at>b.in_force_from
     AND e.effective_at<=cut AND e.recorded_at<=known
   ORDER BY e.effective_at DESC,e.sequence DESC LIMIT 1
 ) release ON b.law_class='BYLAW' AND reservation.binding IS NULL
$$;

CREATE FUNCTION public.l_prepare_ending() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b public.core_organismlawbinding; e public.core_livingorganismevent;
BEGIN
 SELECT * INTO STRICT b FROM public.core_organismlawbinding WHERE binding_uuid=NEW.binding_id;
 PERFORM 1 FROM public.core_livingorganism WHERE id=b.organism_id FOR UPDATE;
 PERFORM 1 FROM public.core_libraryresource WHERE id=b.resource_id FOR UPDATE;
 SELECT * INTO STRICT b FROM public.core_organismlawbinding WHERE binding_uuid=NEW.binding_id FOR UPDATE;
 SELECT * INTO STRICT e FROM public.core_livingorganismevent WHERE id=NEW.end_event_id;
 IF b.scope_kind='PLATFORM' AND NEW.decision_route NOT IN
    ('FOUNDER_STEWARD','STEWARDS_PROPOSE_FOUNDER_AUTHORISES','STEWARDS_AUTHORISED') THEN
   RAISE EXCEPTION 'Only the root LAW route may end inherited law' USING ERRCODE='23514';
 END IF;
 IF e.living_organism_id<>b.organism_id OR b.in_force_until IS NOT NULL OR e.effective_at<=b.in_force_from THEN
   RAISE EXCEPTION 'A LAW ending needs an open binding and a later act of the same organisation' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER l_prepare_ending BEFORE INSERT ON public.core_organismlawending
 FOR EACH ROW EXECUTE FUNCTION public.l_prepare_ending();

CREATE FUNCTION public.l_apply_ending() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 UPDATE public.core_organismlawbinding SET in_force_until=e.effective_at
 FROM public.core_livingorganismevent e WHERE binding_uuid=NEW.binding_id AND e.id=NEW.end_event_id;
 RETURN NULL;
END $$;
CREATE TRIGGER l_apply_ending AFTER INSERT ON public.core_organismlawending
 FOR EACH ROW EXECUTE FUNCTION public.l_apply_ending();

CREATE FUNCTION public.l_guard_window() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (to_jsonb(NEW)-'in_force_until') IS DISTINCT FROM (to_jsonb(OLD)-'in_force_until')
   OR OLD.in_force_until IS NOT NULL OR NEW.in_force_until IS NULL OR NOT EXISTS (
     SELECT FROM public.core_organismlawending x JOIN public.core_livingorganismevent e ON e.id=x.end_event_id
     WHERE x.binding_id=OLD.binding_uuid AND e.effective_at=NEW.in_force_until) THEN
   RAISE EXCEPTION 'Only a recorded LAW ending may close the original window' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER l_guard_window BEFORE UPDATE ON public.core_organismlawbinding
 FOR EACH ROW EXECUTE FUNCTION public.l_guard_window();



CREATE FUNCTION public.l_act_reference(kind text, facts jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT 'sha256:' || encode(sha256(convert_to('INTEVIA:LAW:v1:' || kind || ':' || facts::text,'UTF8')),'hex')
$$;

CREATE FUNCTION public.l_adoption_facts(b public.core_organismlawbinding) RETURNS jsonb
LANGUAGE sql STABLE STRICT AS $$
 SELECT to_jsonb(b) - ARRAY['adoption_event_id','scope_key','shadow_side','in_force_from','in_force_until']
$$;

-- P2 replaces ONLY this closed gateway with the check of qualified command,
-- receipt, actor, effective route and exact effect. Neither a bare chain event
-- nor a caller's assertion of a route can authorise a committed P1 projection.
CREATE FUNCTION public.l_qualified_operation(event_id bigint) RETURNS boolean
LANGUAGE sql STABLE STRICT AS $$ SELECT false $$;

CREATE FUNCTION public.l_require_source(event_id bigint, lo bigint, cut timestamptz, kind text, facts jsonb)
RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE e public.core_livingorganismevent;
BEGIN
 SELECT * INTO STRICT e FROM public.core_livingorganismevent WHERE id=event_id;
 IF e.living_organism_id IS DISTINCT FROM lo OR e.action IS DISTINCT FROM 'ADOPT_LAW'
    OR e.effective_at IS DISTINCT FROM cut OR e.recorded_at IS DISTINCT FROM cut
    OR e.evidence_reference IS DISTINCT FROM public.l_act_reference(kind,facts)
    OR NOT public.l_qualified_operation(e.id) THEN
   RAISE EXCEPTION 'LAW needs the qualified act of this organisation at execution, bound to this exact effect'
     USING ERRCODE='23514', CONSTRAINT='l_qualified_effect';
 END IF;
END $$;

CREATE FUNCTION public.l_check_adoption() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE last_binding uuid; x public.core_organismlawending;
BEGIN
 PERFORM public.l_require_source(NEW.adoption_event_id,NEW.organism_id,NEW.in_force_from,'ADOPT',public.l_adoption_facts(NEW));
 SELECT b.binding_uuid INTO last_binding FROM public.core_organismlawbinding b
 WHERE b.resource_id=NEW.resource_id AND b.scope_key=NEW.scope_key AND b.in_force_from<NEW.in_force_from
 ORDER BY b.in_force_from DESC LIMIT 1;
 IF last_binding IS DISTINCT FROM NEW.predecessor_id THEN
   RAISE EXCEPTION 'LAW predecessor must name the last binding, including across a retirement gap' USING ERRCODE='23514';
 END IF;
 IF NEW.predecessor_id IS NOT NULL THEN
   SELECT * INTO x FROM public.core_organismlawending WHERE binding_id=NEW.predecessor_id;
   IF NOT FOUND OR (x.end_kind='SUPERSEDED' AND (x.successor_id<>NEW.binding_uuid OR x.end_event_id<>NEW.adoption_event_id)) THEN
     RAISE EXCEPTION 'Supersession must identify both bindings and the same adoption act' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER l_check_adoption AFTER INSERT ON public.core_organismlawbinding
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.l_check_adoption();

CREATE FUNCTION public.l_check_ending() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b public.core_organismlawbinding; successor public.core_organismlawbinding;
BEGIN
 SELECT * INTO STRICT b FROM public.core_organismlawbinding WHERE binding_uuid=NEW.binding_id;
 IF NEW.end_kind='SUPERSEDED' THEN
   SELECT * INTO successor FROM public.core_organismlawbinding WHERE binding_uuid=NEW.successor_id;
   IF NOT FOUND OR successor.predecessor_id IS DISTINCT FROM NEW.binding_id
     OR successor.adoption_event_id<>NEW.end_event_id OR successor.in_force_from<>b.in_force_until
     OR successor.decision_route<>NEW.decision_route THEN
     RAISE EXCEPTION 'A supersession ending must match the recorded successor and execution' USING ERRCODE='23514';
   END IF;
 ELSE
   PERFORM public.l_require_source(NEW.end_event_id,b.organism_id,b.in_force_until,'RETIRE',
     jsonb_build_object('binding',NEW.binding_id,'route',NEW.decision_route));
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER l_check_ending AFTER INSERT ON public.core_organismlawending
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.l_check_ending();

CREATE FUNCTION public.l_check_label() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo uuid; cut timestamptz;
BEGIN
 SELECT organism_id INTO STRICT lo FROM public.core_livingorganism WHERE id=NEW.organism_id;
 SELECT effective_at INTO STRICT cut FROM public.core_livingorganismevent WHERE id=NEW.event_id;
 IF NOT public.l_subscriber_capability('RESOLUTION_LABEL',lo,cut) THEN
   RAISE EXCEPTION 'A custom resolution label requires an effective SE capability' USING ERRCODE='23514';
 END IF;
 PERFORM public.l_require_source(NEW.event_id,NEW.organism_id,cut,'RESOLUTION_LABEL',
     jsonb_build_object('label',NEW.display_label));
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER l_check_label AFTER INSERT ON public.core_lawresolutionlabel
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.l_check_label();

CREATE FUNCTION public.l_check_archive() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE resource_pk bigint; scope_count integer; classes text;
BEGIN
 IF TG_TABLE_NAME='core_libraryresource' THEN
   resource_pk:=NEW.id;
 ELSE
   resource_pk:=NEW.resource_id;
   -- A raw archive transition also participates in the cross-scope write fence.
   UPDATE public.core_libraryresource r SET updated_at=r.updated_at WHERE r.id=resource_pk;
 END IF;
 PERFORM 1 FROM public.core_libraryresource WHERE id=resource_pk FOR UPDATE;
 SELECT count(DISTINCT scope_key),string_agg(DISTINCT law_class,', ' ORDER BY law_class)
 INTO scope_count, classes FROM public.core_organismlawbinding
 WHERE resource_id=resource_pk AND (in_force_until IS NULL OR in_force_until>statement_timestamp());
 IF scope_count>0 THEN
   RAISE EXCEPTION 'Archive refused: % in force in % scope(s)',classes,scope_count USING ERRCODE='23514',CONSTRAINT='l_archive_in_force';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER l_check_archive BEFORE INSERT ON public.core_libraryresourcetransition
 FOR EACH ROW WHEN (NEW.to_state='archived') EXECUTE FUNCTION public.l_check_archive();
CREATE TRIGGER l_check_resource_archive BEFORE UPDATE OF state ON public.core_libraryresource
 FOR EACH ROW WHEN (NEW.state='archived') EXECUTE FUNCTION public.l_check_archive();

-- One row for each visible binding. Root can distinguish its own adoption and its
-- platform adoption. Ordinary organisations receive a platform record once.
CREATE FUNCTION public.l_laws_at(subject uuid, cut timestamptz, known timestamptz)
RETURNS TABLE(binding uuid, resource bigint, version bigint, version_number integer, class_name text,
 scope text, inherited boolean, owner text, owner_circle bigint, owner_organism bigint, owner_cause uuid,
 decision_route text, act uuid, predecessor uuid, effective_from timestamptz, effective_until timestamptz,
 proposing_circle bigint, terms_binding uuid, display_class text)
LANGUAGE sql STABLE STRICT AS $$
 SELECT b.binding_uuid,b.resource_id,b.version_id,v.version_number,b.law_class,
   b.scope_key,b.scope_kind='PLATFORM',o.kind,o.circle_id,o.parent_lo_id,o.cause,
   b.decision_route,e.event_uuid,b.predecessor_id,b.in_force_from,
   (SELECT last_act.effective_at FROM public.core_organismlawending x
    JOIN public.core_livingorganismevent last_act ON last_act.id=x.end_event_id
    WHERE x.binding_id=b.binding_uuid AND last_act.recorded_at<=known),
   b.proposing_circle_id,b.terms_binding_id,
   CASE b.law_class WHEN 'MEMBERS_RESOLUTION' THEN coalesce(lbl.display_label,'Members'' Resolution')
        WHEN 'TERMS_OF_REFERENCE' THEN 'Terms of reference' ELSE initcap(lower(b.law_class)) END
 FROM public.core_livingorganism lo JOIN public.core_organismlawbinding b
   ON (b.organism_id=lo.id OR b.scope_kind='PLATFORM')
 JOIN public.core_libraryresourceversion v ON v.id=b.version_id
 JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
 CROSS JOIN LATERAL public.l_owner_at(b,cut,known) o
 LEFT JOIN LATERAL (
   SELECT l.display_label FROM public.core_lawresolutionlabel l
   JOIN public.core_livingorganismevent a ON a.id=l.event_id
   WHERE l.organism_id=lo.id AND a.effective_at<=cut AND a.recorded_at<=known
   ORDER BY a.effective_at DESC,a.sequence DESC LIMIT 1
 ) lbl ON b.law_class='MEMBERS_RESOLUTION'
 WHERE lo.organism_id=subject AND public.l_visible_window(b,cut,known)
   AND EXISTS (SELECT FROM public.core_livingorganismevent life WHERE life.living_organism_id=lo.id
               AND life.effective_at<=cut AND life.recorded_at<=known)
 ORDER BY b.resource_id,b.scope_key
$$;

CREATE TRIGGER l_immutable BEFORE DELETE ON public.core_organismlawbinding
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_l_no_truncate BEFORE TRUNCATE ON public.core_organismlawbinding
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();

CREATE TRIGGER l_immutable BEFORE UPDATE OR DELETE ON public.core_organismlawending
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_l_no_truncate BEFORE TRUNCATE ON public.core_organismlawending
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();

CREATE TRIGGER l_immutable BEFORE UPDATE OR DELETE ON public.core_lawresolutionlabel
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_l_no_truncate BEFORE TRUNCATE ON public.core_lawresolutionlabel
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();

CREATE TRIGGER l_immutable BEFORE UPDATE OR DELETE ON public.core_libraryresourceversion
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_l_no_truncate BEFORE TRUNCATE ON public.core_libraryresourceversion
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();

CREATE TRIGGER l_immutable BEFORE UPDATE OR DELETE ON public.core_libraryresourcetransition
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_l_no_truncate BEFORE TRUNCATE ON public.core_libraryresourcetransition
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
"""

REVERSE = r"""
DROP TRIGGER l_no_platform_shadow ON public.core_organismlawbinding;
DROP FUNCTION public.l_check_platform_pair();
DROP TRIGGER s015_l_no_truncate ON public.core_libraryresourcetransition;
DROP TRIGGER l_immutable ON public.core_libraryresourcetransition;
DROP TRIGGER s015_l_no_truncate ON public.core_libraryresourceversion;
DROP TRIGGER l_immutable ON public.core_libraryresourceversion;
DROP TRIGGER s015_l_no_truncate ON public.core_lawresolutionlabel;
DROP TRIGGER l_immutable ON public.core_lawresolutionlabel;
DROP TRIGGER s015_l_no_truncate ON public.core_organismlawending;
DROP TRIGGER l_immutable ON public.core_organismlawending;
DROP TRIGGER s015_l_no_truncate ON public.core_organismlawbinding;
DROP TRIGGER l_immutable ON public.core_organismlawbinding;
DROP TRIGGER l_check_resource_archive ON public.core_libraryresource;
DROP TRIGGER l_check_archive ON public.core_libraryresourcetransition;
DROP TRIGGER l_check_label ON public.core_lawresolutionlabel;
DROP TRIGGER l_check_ending ON public.core_organismlawending;
DROP TRIGGER l_check_adoption ON public.core_organismlawbinding;
DROP TRIGGER l_guard_window ON public.core_organismlawbinding;
DROP TRIGGER l_apply_ending ON public.core_organismlawending;
DROP TRIGGER l_prepare_ending ON public.core_organismlawending;
DROP TRIGGER l_prepare_binding ON public.core_organismlawbinding;
DROP FUNCTION public.l_laws_at(uuid,timestamptz,timestamptz);
DROP FUNCTION public.l_check_archive();
DROP FUNCTION public.l_check_label();
DROP FUNCTION public.l_check_ending();
DROP FUNCTION public.l_check_adoption();
DROP FUNCTION public.l_require_source(bigint,bigint,timestamptz,text,jsonb);
DROP FUNCTION public.l_qualified_operation(bigint);
DROP FUNCTION public.l_adoption_facts(public.core_organismlawbinding);
DROP FUNCTION public.l_act_reference(text,jsonb);
DROP FUNCTION public.l_guard_window();
DROP FUNCTION public.l_apply_ending();
DROP FUNCTION public.l_prepare_ending();
DROP FUNCTION public.l_owner_at(public.core_organismlawbinding,timestamptz,timestamptz);
DROP FUNCTION public.l_prepare_binding();
DROP FUNCTION public.l_reservation(bigint,timestamptz,timestamptz);
DROP FUNCTION public.l_visible_window(public.core_organismlawbinding,timestamptz,timestamptz);
DROP FUNCTION public.l_class_owner(text,boolean,text);
DROP FUNCTION public.l_subscriber_capability(text,uuid,timestamptz);
DROP TABLE public.core_lawresolutionlabel, public.core_organismlawending, public.core_organismlawbinding;
DROP FUNCTION public.l_tor_right(jsonb,text,text), public.l_valid_areas(jsonb), public.l_law_rank(text), public.l_valid_route(text);
"""


def install(apps, schema_editor):
    schema_editor.execute(FORWARD, params=None)


def remove(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "LOCK TABLE public.core_organismlawbinding, public.core_organismlawending, "
            "public.core_lawresolutionlabel IN ACCESS EXCLUSIVE MODE"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM public.core_organismlawbinding) "
            "OR EXISTS (SELECT FROM public.core_lawresolutionlabel)"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "LAW 0066 reversal refused while an adoption or label decision exists"
            )
    schema_editor.execute(REVERSE, params=None)


class Migration(migrations.Migration):
    dependencies = [("core", "0065_root_continuity")]
    operations = [migrations.RunPython(install, remove)]
