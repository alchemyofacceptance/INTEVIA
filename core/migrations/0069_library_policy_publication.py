"""LIB P2: exact Policy provenance, authorship, release and root publication facts."""
from django.db import migrations

FORWARD = r"""
CREATE FUNCTION public.lib_policy_source(lo_id bigint, person bigint, route text, cut timestamptz, known timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE STRICT AS $$
DECLARE lo public.core_livingorganism; answer jsonb; capacity text;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE id=lo_id;
 IF lo.id IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=person AND i.access_state='active' AND u.is_active) THEN RETURN NULL; END IF;
 IF route='ROOT_EXCEPTION' THEN
  capacity:=public.r_publication_capacity(lo_id,person,'LIBRARY',cut,known);
  IF capacity IS NULL THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('kind','ROOT_PUBLICATION_CAPACITY','reference',capacity,'route',route,'organism',lo.organism_id);
 END IF;
 IF route NOT IN ('COORDINATOR_ROUTE','GOVERNANCE_INTERIM') THEN RETURN NULL; END IF;
 answer:=public.l_route(lo,person,'POLICY',CASE route WHEN 'GOVERNANCE_INTERIM' THEN 'GOVERNANCE' ELSE 'LO_COORDINATOR' END,
  'LIVING_ORGANISM',false,route,public.l_electorate(lo_id,cut,known),cut,known);
 IF answer ? 'reason' OR answer->>'office' IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('kind','OFFICE','reference',answer->>'office','route',route,'organism',lo.organism_id);
END $$;

CREATE TABLE public.core_libpolicyversion (
 version_id bigint PRIMARY KEY REFERENCES public.core_libraryresourceversion,
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism,
 request_uuid uuid NOT NULL UNIQUE,
 actor_id bigint NOT NULL REFERENCES public.core_identity,
 actor_epoch bigint NOT NULL,
 authority_source jsonb NOT NULL,
 declared_self boolean NOT NULL,
 author_ids bigint[] NOT NULL CHECK(cardinality(author_ids)>0 AND array_position(author_ids,NULL) IS NULL),
 content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
 received_at timestamptz NOT NULL,
 effective_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 recorded_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
CREATE TABLE public.core_libauthorclaim (
 claim_uuid uuid PRIMARY KEY,
 version_id bigint NOT NULL REFERENCES public.core_libpolicyversion,
 author_id bigint NOT NULL REFERENCES public.core_identity,
 actor_id bigint NOT NULL REFERENCES public.core_identity,
 actor_epoch bigint NOT NULL,
 inherited_from uuid REFERENCES public.core_libauthorclaim,
 declaration text NOT NULL,
 received_at timestamptz NOT NULL,
 effective_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 recorded_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
-- Carried attribution and an author's own declaration are distinct immutable facts.
-- Each kind remains unique for the exact author/version; neither replaces the other.
CREATE UNIQUE INDEX lib_author_own_once ON public.core_libauthorclaim(version_id,author_id)
 WHERE inherited_from IS NULL;
CREATE UNIQUE INDEX lib_author_carried_once ON public.core_libauthorclaim(version_id,author_id)
 WHERE inherited_from IS NOT NULL;
CREATE TABLE public.core_libreleasepermission (
 permission_uuid uuid PRIMARY KEY,
 version_id bigint NOT NULL UNIQUE REFERENCES public.core_libpolicyversion,
 actor_id bigint NOT NULL REFERENCES public.core_identity,
 actor_epoch bigint NOT NULL,
 authority_source jsonb NOT NULL,
 received_at timestamptz NOT NULL,
 effective_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 recorded_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
CREATE TABLE public.core_libpublicationfact (
 fact_uuid uuid PRIMARY KEY,
 operation_uuid uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('ACCEPTANCE','PUBLICATION')),
 version_id bigint NOT NULL REFERENCES public.core_libpolicyversion,
 organism_id bigint NOT NULL REFERENCES public.core_livingorganism,
 actor_id bigint NOT NULL REFERENCES public.core_identity,
 actor_epoch bigint NOT NULL,
 authority_source jsonb NOT NULL,
 permission_uuid uuid NOT NULL REFERENCES public.core_libreleasepermission,
 declaration_id bigint REFERENCES public.core_rootpublicationdeclaration,
 transition_id bigint REFERENCES public.core_libraryresourcetransition,
 self_accepted boolean NOT NULL,
 independent_review boolean NOT NULL CHECK(NOT independent_review),
 received_at timestamptz NOT NULL,
 effective_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 recorded_at timestamptz NOT NULL DEFAULT statement_timestamp(),
 UNIQUE(operation_uuid,kind), UNIQUE(version_id,kind),
 CHECK((kind='PUBLICATION')=(transition_id IS NOT NULL)),
 CHECK((declaration_id IS NOT NULL)=self_accepted),
 CHECK(kind<>'ACCEPTANCE' OR declaration_id IS NOT NULL)
);

CREATE FUNCTION public.lib_policy_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v public.core_libraryresourceversion; old public.core_libpolicyversion; stamp timestamptz:=statement_timestamp();
BEGIN
 NEW.recorded_at:=stamp; NEW.effective_at:=stamp;
 IF NEW.received_at>stamp OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active) THEN
  RAISE EXCEPTION 'LIB: the signed-in actor or act time is unavailable.' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='core_libpolicyversion' THEN
  SELECT * INTO STRICT v FROM public.core_libraryresourceversion WHERE id=NEW.version_id;
  PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
  SELECT * INTO old FROM public.core_libpolicyversion WHERE version_id=v.predecessor_id;
  IF (NEW.declared_self AND NOT NEW.actor_id=ANY(NEW.author_ids))
    OR NEW.author_ids IS DISTINCT FROM ARRAY(SELECT DISTINCT a FROM unnest(NEW.author_ids) a ORDER BY a)
    OR NEW.content_sha256 IS DISTINCT FROM encode(sha256(convert_to(v.content,'UTF8')),'hex')
    OR v.created_by_id<>NEW.actor_id OR v.created_at<>NEW.received_at
    OR NOT EXISTS(SELECT FROM public.core_libraryresource r WHERE r.id=v.resource_id AND r.current_version_id=v.id AND r.state='draft')
    OR (v.predecessor_id IS NOT NULL AND (old.version_id IS NULL OR old.organism_id<>NEW.organism_id OR NOT old.author_ids<@NEW.author_ids))
    OR NOT EXISTS(SELECT FROM public.core_libraryresourcetransition t WHERE t.version_id=v.id AND t.actor_id=NEW.actor_id
      AND t.command IN ('create_library_resource','create_library_resource_version')
      AND t.authority_reference='lib-policy:'||NEW.authority_source::text) THEN
    RAISE EXCEPTION 'LIB: Policy provenance must name the exact new version and retain prior authors.' USING ERRCODE='23514'; END IF;
  IF public.lib_policy_source(NEW.organism_id,NEW.actor_id,NEW.authority_source->>'route',NEW.received_at,NEW.received_at) IS DISTINCT FROM NEW.authority_source
    OR public.lib_policy_source(NEW.organism_id,NEW.actor_id,NEW.authority_source->>'route',stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
    RAISE EXCEPTION 'LIB: this office does not authorise the Policy act.' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='core_libreleasepermission' THEN
  SELECT x.* INTO STRICT v FROM public.core_libraryresourceversion x WHERE x.id=NEW.version_id;
  PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
  SELECT * INTO STRICT old FROM public.core_libpolicyversion WHERE version_id=v.id;
  IF public.lib_policy_source(old.organism_id,NEW.actor_id,NEW.authority_source->>'route',NEW.received_at,NEW.received_at) IS DISTINCT FROM NEW.authority_source
    OR public.lib_policy_source(old.organism_id,NEW.actor_id,NEW.authority_source->>'route',stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
    RAISE EXCEPTION 'LIB: release permission requires this organisation''s effective authority for the exact version.' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.lib_author_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE meta public.core_libpolicyversion; v public.core_libraryresourceversion; prior public.core_libauthorclaim;
 stamp timestamptz:=statement_timestamp();
BEGIN
 NEW.recorded_at:=stamp; NEW.effective_at:=stamp;
 SELECT * INTO STRICT meta FROM public.core_libpolicyversion WHERE version_id=NEW.version_id;
 SELECT * INTO STRICT v FROM public.core_libraryresourceversion WHERE id=NEW.version_id;
 PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
 IF NEW.received_at>stamp OR NOT NEW.author_id=ANY(meta.author_ids)
    OR EXISTS(SELECT FROM public.core_libpublicationfact WHERE version_id=v.id)
    OR EXISTS(SELECT FROM public.core_libraryresourceversion WHERE predecessor_id=v.id)
    OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active)
    OR NEW.declaration IS DISTINCT FROM (CASE WHEN NEW.inherited_from IS NULL
      THEN 'I declare my authorship of this exact version and confirm its recorded author list is complete.'
      ELSE 'Attribution carried from the cited predecessor declaration; no new declaration by that author.' END) THEN
  RAISE EXCEPTION 'LIB: authorship needs an explicit declaration before publication or a successor.' USING ERRCODE='23514'; END IF;
 IF NEW.inherited_from IS NULL THEN
  IF NEW.actor_id<>NEW.author_id THEN RAISE EXCEPTION 'LIB: an author must declare in their own identity.' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO STRICT prior FROM public.core_libauthorclaim WHERE claim_uuid=NEW.inherited_from;
  IF prior.version_id IS DISTINCT FROM v.predecessor_id OR prior.author_id<>NEW.author_id OR NEW.actor_id<>meta.actor_id THEN
    RAISE EXCEPTION 'LIB: carried attribution must cite the predecessor author''s declaration.' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.lib_publication_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE stamp timestamptz:=statement_timestamp(); meta public.core_libpolicyversion; v public.core_libraryresourceversion;
 permission public.core_libreleasepermission; decl public.core_rootpublicationdeclaration;
BEGIN
 NEW.recorded_at:=stamp; NEW.effective_at:=stamp;
 SELECT * INTO STRICT meta FROM public.core_libpolicyversion WHERE version_id=NEW.version_id;
 SELECT * INTO STRICT v FROM public.core_libraryresourceversion WHERE id=NEW.version_id;
 PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
 SELECT * INTO STRICT permission FROM public.core_libreleasepermission WHERE permission_uuid=NEW.permission_uuid;
 IF NEW.received_at>stamp OR permission.version_id<>v.id OR permission.recorded_at>stamp
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active)
   OR EXISTS(SELECT FROM unnest(meta.author_ids) a WHERE NOT EXISTS(SELECT FROM public.core_libauthorclaim c WHERE c.version_id=v.id AND c.author_id=a))
   OR public.lib_policy_source(NEW.organism_id,NEW.actor_id,NEW.authority_source->>'route',NEW.received_at,NEW.received_at) IS DISTINCT FROM NEW.authority_source
   OR public.lib_policy_source(NEW.organism_id,NEW.actor_id,NEW.authority_source->>'route',stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
   RAISE EXCEPTION 'LIB: publication needs complete authorship, exact release permission and effective authority.' USING ERRCODE='23514'; END IF;
 IF NEW.authority_source->>'route'='ROOT_EXCEPTION' THEN
  SELECT * INTO decl FROM public.core_rootpublicationdeclaration WHERE id=NEW.declaration_id;
  IF decl.id IS NULL OR decl.living_organism_id<>NEW.organism_id OR decl.effective_at>NEW.received_at OR decl.recorded_at>NEW.received_at
     OR meta.author_ids IS DISTINCT FROM ARRAY[NEW.actor_id] OR NOT NEW.self_accepted THEN
    RAISE EXCEPTION 'LIB: the root exception needs its declaration and the publisher as sole recorded author.' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.organism_id<>meta.organism_id OR NEW.declaration_id IS NOT NULL OR NEW.self_accepted OR NEW.kind<>'PUBLICATION' THEN
    RAISE EXCEPTION 'LIB: ordinary release cannot use the root''s self-acceptance exception.' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.lib_policy_commit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE version_ bigint; meta public.core_libpolicyversion; fact public.core_libpublicationfact; stamp timestamptz:=statement_timestamp();
BEGIN
 version_:=NEW.version_id;
 SELECT * INTO STRICT meta FROM public.core_libpolicyversion WHERE version_id=version_;
 IF TG_TABLE_NAME='core_libpolicyversion' THEN
  IF public.lib_policy_source(meta.organism_id,meta.actor_id,meta.authority_source->>'route',stamp,stamp) IS DISTINCT FROM meta.authority_source THEN
   RAISE EXCEPTION 'LIB: Policy authority changed before commit.' USING ERRCODE='23514'; END IF;
  IF meta.declared_self AND NOT EXISTS(SELECT FROM public.core_libauthorclaim WHERE version_id=version_
     AND author_id=meta.actor_id AND actor_id=meta.actor_id AND inherited_from IS NULL) THEN
   RAISE EXCEPTION 'LIB: the declared own-authorship act must be recorded with this version.' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME='core_libreleasepermission' THEN
  IF public.lib_policy_source(meta.organism_id,NEW.actor_id,NEW.authority_source->>'route',stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
   RAISE EXCEPTION 'LIB: release authority changed before commit.' USING ERRCODE='23514'; END IF;
 END IF;
 IF NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'LIB: the act''s signed-in actor changed before commit.' USING ERRCODE='23514'; END IF;
 FOR fact IN SELECT * FROM public.core_libpublicationfact WHERE version_id=version_ LOOP
  IF public.lib_policy_source(fact.organism_id,fact.actor_id,fact.authority_source->>'route',stamp,stamp) IS DISTINCT FROM fact.authority_source THEN
   RAISE EXCEPTION 'LIB: publication authority changed before commit.' USING ERRCODE='23514'; END IF;
  IF fact.kind='ACCEPTANCE' AND NOT EXISTS(SELECT FROM public.core_libpublicationfact pub WHERE pub.operation_uuid=fact.operation_uuid
    AND pub.kind='PUBLICATION' AND pub.version_id=fact.version_id AND pub.actor_id=fact.actor_id AND pub.authority_source=fact.authority_source
    AND pub.declaration_id=fact.declaration_id AND pub.permission_uuid=fact.permission_uuid) THEN
   RAISE EXCEPTION 'LIB: acceptance and publication must commit together.' USING ERRCODE='23514'; END IF;
  IF fact.kind='PUBLICATION' AND (NOT EXISTS(SELECT FROM public.core_libraryresourcetransition t
      WHERE t.id=fact.transition_id AND t.version_id=fact.version_id AND t.actor_id=fact.actor_id
      AND t.command='publish_library_resource' AND t.to_state='published' AND t.authority_reference='lib-policy:'||fact.authority_source::text)
    OR (fact.declaration_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_libpublicationfact acc
      WHERE acc.operation_uuid=fact.operation_uuid AND acc.kind='ACCEPTANCE' AND acc.version_id=fact.version_id
      AND acc.actor_id=fact.actor_id AND acc.authority_source=fact.authority_source AND acc.declaration_id=fact.declaration_id
      AND acc.permission_uuid=fact.permission_uuid))) THEN
   RAISE EXCEPTION 'LIB: publication must cite its exact transition and required acceptance.' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NULL;
END $$;

CREATE FUNCTION public.lib_tracked_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT FROM public.core_libpolicyversion WHERE version_id=NEW.version_id) AND NEW.command='publish_library_resource'
   AND NOT EXISTS(SELECT FROM public.core_libpublicationfact WHERE transition_id=NEW.id) THEN
  RAISE EXCEPTION 'LIB: this Policy requires its governed publication fact.' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER lib_tracked_publication AFTER INSERT ON public.core_libraryresourcetransition
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.lib_tracked_transition();

CREATE FUNCTION public.lib_law_authority_source(decision bigint, facts jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT jsonb_build_object('kind','LAW_AUTHORITY_DECISION','decision_id',decision,'route',facts->>'route',
  'office',facts->'office','root_office',facts->'root_office','electorate',facts->'electorate')
$$;
ALTER TABLE public.core_lawact ADD COLUMN authority_source jsonb GENERATED ALWAYS AS (public.lib_law_authority_source(decision_id,plan)) STORED;

CREATE TRIGGER lib_input BEFORE INSERT ON public.core_libpolicyversion FOR EACH ROW EXECUTE FUNCTION public.lib_policy_input();
CREATE TRIGGER lib_immutable BEFORE UPDATE OR DELETE ON public.core_libpolicyversion FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_lib_no_truncate BEFORE TRUNCATE ON public.core_libpolicyversion FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER lib_commit AFTER INSERT ON public.core_libpolicyversion DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.lib_policy_commit();

CREATE TRIGGER lib_input BEFORE INSERT ON public.core_libauthorclaim FOR EACH ROW EXECUTE FUNCTION public.lib_author_input();
CREATE TRIGGER lib_immutable BEFORE UPDATE OR DELETE ON public.core_libauthorclaim FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_lib_no_truncate BEFORE TRUNCATE ON public.core_libauthorclaim FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER lib_commit AFTER INSERT ON public.core_libauthorclaim DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.lib_policy_commit();

CREATE TRIGGER lib_input BEFORE INSERT ON public.core_libreleasepermission FOR EACH ROW EXECUTE FUNCTION public.lib_policy_input();
CREATE TRIGGER lib_immutable BEFORE UPDATE OR DELETE ON public.core_libreleasepermission FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_lib_no_truncate BEFORE TRUNCATE ON public.core_libreleasepermission FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER lib_commit AFTER INSERT ON public.core_libreleasepermission DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.lib_policy_commit();

CREATE TRIGGER lib_input BEFORE INSERT ON public.core_libpublicationfact FOR EACH ROW EXECUTE FUNCTION public.lib_publication_input();
CREATE TRIGGER lib_immutable BEFORE UPDATE OR DELETE ON public.core_libpublicationfact FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_lib_no_truncate BEFORE TRUNCATE ON public.core_libpublicationfact FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER lib_commit AFTER INSERT ON public.core_libpublicationfact DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.lib_policy_commit();

DROP FUNCTION public.lib_laws_at(uuid,timestamptz,timestamptz);
CREATE FUNCTION public.lib_laws_at(subject uuid, cut timestamptz, known timestamptz)
RETURNS TABLE(
 binding uuid, resource bigint, version bigint, version_number integer, class_name text,
 scope text, inherited boolean, owner text, owner_circle bigint, owner_organism bigint,
 owner_cause uuid, decision_route text, act uuid, predecessor uuid,
 effective_from timestamptz, effective_until timestamptz, proposing_circle bigint,
 terms_binding uuid, display_class text,
 library_reference text, scope_kind text, scope_circle uuid, owner_circle_uuid uuid,
 owner_organism_uuid uuid, consequence_class text, adoption_received_at timestamptz,
 adoption_recorded_at timestamptz, authority_decision_reference text, issuer uuid,
 content text, authority_source jsonb)
LANGUAGE sql STABLE STRICT AS $$
 SELECT l.*, r.resource_id::text, b.scope_kind::text, scoped.circle_uuid,
        owning.circle_uuid, parent.organism_id, b.consequence_class::text,
        e.received_at, e.recorded_at, e.authority_decision_reference::text,
        issuing.organism_id, v.content, act.authority_source
 FROM public.l_laws_at(subject,cut,known) l
 JOIN public.core_organismlawbinding b ON b.binding_uuid=l.binding
 JOIN public.core_libraryresource r ON r.id=l.resource
 JOIN public.core_libraryresourceversion v ON v.id=l.version
 JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
 JOIN public.core_lawact act ON act.event_id=e.id
 JOIN public.core_livingorganism issuing ON issuing.id=b.organism_id
 JOIN public.core_livingorganism parent ON parent.id=l.owner_organism
 LEFT JOIN public.core_circle scoped ON scoped.id=b.scope_circle_id
 LEFT JOIN public.core_circle owning ON owning.id=l.owner_circle
$$;
"""
REVERSE = r"""
LOCK TABLE public.core_libpolicyversion,public.core_libauthorclaim,public.core_libreleasepermission,public.core_libpublicationfact IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT FROM public.core_libpolicyversion) OR EXISTS(SELECT FROM public.core_libpublicationfact) THEN
  RAISE EXCEPTION 'LIB publication history exists; reversal refused'; END IF;
END $$;
DROP FUNCTION public.lib_laws_at(uuid,timestamptz,timestamptz);
CREATE FUNCTION public.lib_laws_at(subject uuid, cut timestamptz, known timestamptz)
RETURNS TABLE(
 binding uuid, resource bigint, version bigint, version_number integer, class_name text,
 scope text, inherited boolean, owner text, owner_circle bigint, owner_organism bigint,
 owner_cause uuid, decision_route text, act uuid, predecessor uuid,
 effective_from timestamptz, effective_until timestamptz, proposing_circle bigint,
 terms_binding uuid, display_class text,
 library_reference text, scope_kind text, scope_circle uuid, owner_circle_uuid uuid,
 owner_organism_uuid uuid, consequence_class text, adoption_received_at timestamptz,
 adoption_recorded_at timestamptz, authority_decision_reference text, issuer uuid,
 content text)
LANGUAGE sql STABLE STRICT AS $$
 SELECT l.*, r.resource_id::text, b.scope_kind::text, scoped.circle_uuid,
        owning.circle_uuid, parent.organism_id, b.consequence_class::text,
        e.received_at, e.recorded_at, e.authority_decision_reference::text,
        issuing.organism_id, v.content
 FROM public.l_laws_at(subject,cut,known) l
 JOIN public.core_organismlawbinding b ON b.binding_uuid=l.binding
 JOIN public.core_libraryresource r ON r.id=l.resource
 JOIN public.core_libraryresourceversion v ON v.id=l.version
 JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
 JOIN public.core_livingorganism issuing ON issuing.id=b.organism_id
 JOIN public.core_livingorganism parent ON parent.id=l.owner_organism
 LEFT JOIN public.core_circle scoped ON scoped.id=b.scope_circle_id
 LEFT JOIN public.core_circle owning ON owning.id=l.owner_circle
$$;
ALTER TABLE public.core_lawact DROP COLUMN authority_source;
DROP FUNCTION public.lib_law_authority_source(bigint,jsonb);
DROP TRIGGER lib_tracked_publication ON public.core_libraryresourcetransition;
DROP TABLE public.core_libpublicationfact,public.core_libreleasepermission,public.core_libauthorclaim,public.core_libpolicyversion;
DROP FUNCTION public.lib_tracked_transition(),public.lib_policy_commit(),public.lib_publication_input(),public.lib_author_input(),public.lib_policy_input();
DROP FUNCTION public.lib_policy_source(bigint,bigint,text,timestamptz,timestamptz);
"""

class Migration(migrations.Migration):
    dependencies = [("core", "0068_library_laws_read")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
