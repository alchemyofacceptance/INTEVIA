"""CARE assistance choices and the first qualifying Alpha Treasurer observation.

The two detail/event pairs enter and leave the schema together. A populated
history retains 0111. Empty descent restores both exact 0110 definitions.
"""
from hashlib import sha256

from django.db import migrations

from core.migrations._clock_conversion import swap


SETTING = r"""
CREATE TABLE public.core_careassistancesettingact (
    act_uuid uuid PRIMARY KEY,
    organism_id bigint NOT NULL REFERENCES public.core_livingorganism(id),
    version integer NOT NULL CHECK (version > 0),
    predecessor uuid,
    enabled boolean NOT NULL,
    actor_id bigint NOT NULL REFERENCES public.core_identity(id),
    actor_epoch bigint NOT NULL CHECK (actor_epoch >= 0),
    enterer_id bigint NOT NULL REFERENCES public.core_identity(id),
    office_uuid uuid NOT NULL REFERENCES public.core_contextualroleassignment(assignment_uuid),
    authority_source jsonb NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    received_at timestamptz NOT NULL,
    occurred_at timestamptz NOT NULL,
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL,
    execution_xid xid8 NOT NULL,

    CONSTRAINT care_setting_actor_entered CHECK (enterer_id = actor_id),
    CONSTRAINT care_setting_times CHECK (
        occurred_at = received_at AND received_at <= effective_at AND effective_at = recorded_at
    ),
    CONSTRAINT care_setting_first CHECK ((version = 1) = (predecessor IS NULL)),
    CONSTRAINT care_setting_owner_version UNIQUE (organism_id, version),
    CONSTRAINT care_setting_one_successor UNIQUE (predecessor),
    CONSTRAINT care_setting_owner_act UNIQUE (organism_id, act_uuid),
    CONSTRAINT care_setting_prior_owner FOREIGN KEY (organism_id, predecessor)
        REFERENCES public.core_careassistancesettingact(organism_id, act_uuid),
    CONSTRAINT care_setting_ilc_fk FOREIGN KEY (act_uuid)
        REFERENCES public.core_livingorganismevent(event_uuid) DEFERRABLE INITIALLY DEFERRED
);

-- The intent excludes server-derived times and heads so a legitimate retry remains comparable.
CREATE FUNCTION public.care_setting_intent(p_owner uuid, p_actor uuid, p_epoch bigint,
    p_key uuid, p_expected uuid, p_enabled boolean)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to('CARE-SETTING:1:' || public.l_canonical(
        jsonb_build_array(p_owner, p_actor, p_epoch, p_key, p_expected, p_enabled)), 'UTF8')), 'hex')
$$;

CREATE FUNCTION public.care_setting_decision(p_authority jsonb, p_intent text)
RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT 's015d1:' || encode(sha256(convert_to(
        'CARE-SETTING-OFFICE:1:' || public.l_canonical(p_authority) || ':' || p_intent, 'UTF8')), 'hex')
$$;

CREATE FUNCTION public.care_setting_input()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    actor_uuid uuid;
    owner_uuid uuid;
    current_epoch bigint;
    execution_at timestamptz;
    source_at_receipt jsonb;
    source_at_execution jsonb;
    previous public.core_careassistancesettingact;
BEGIN
    -- Only request ID, owner, expected predecessor, desired value, authenticated
    -- actor/epoch and the database receipt cut arrive as inputs.
    IF NEW.version IS NOT NULL OR NEW.enterer_id IS NOT NULL OR NEW.office_uuid IS NOT NULL
       OR NEW.authority_source IS NOT NULL OR NEW.fingerprint IS NOT NULL
       OR NEW.occurred_at IS NOT NULL OR NEW.effective_at IS NOT NULL
       OR NEW.recorded_at IS NOT NULL OR NEW.execution_xid IS NOT NULL THEN
        RAISE EXCEPTION 'CARE_SETTING_INPUT' USING ERRCODE='23514';
    END IF;
    SELECT i.identity_id, i.access_epoch INTO actor_uuid, current_epoch
    FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.actor_id AND u.is_active
    FOR NO KEY UPDATE OF i,u;
    IF NOT FOUND OR current_epoch IS DISTINCT FROM NEW.actor_epoch THEN
        RAISE EXCEPTION 'CARE_SETTING_AUTHORITY' USING ERRCODE='23514';
    END IF;
    SELECT organism_id INTO owner_uuid FROM public.core_livingorganism WHERE id=NEW.organism_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'CARE_SETTING_AUTHORITY' USING ERRCODE='23514';
    END IF;
    PERFORM public.s015_lock_organisms(ARRAY[owner_uuid]);
    execution_at := public.intevia_clock_statement();
    IF NEW.received_at IS NULL OR NEW.received_at < public.intevia_clock_transaction()
       OR NEW.received_at > execution_at THEN
        RAISE EXCEPTION 'CARE_SETTING_RECEIPT' USING ERRCODE='23514';
    END IF;

    source_at_receipt := public.disc_office(NEW.organism_id,NULL,NEW.actor_id,NEW.received_at);
    source_at_execution := public.disc_office(NEW.organism_id,NULL,NEW.actor_id,execution_at);
    IF source_at_execution IS NULL OR source_at_receipt IS DISTINCT FROM source_at_execution
       OR source_at_execution->>'route' IS DISTINCT FROM 'COORDINATOR_ROUTE'
       OR source_at_execution->>'kind' IS DISTINCT FROM 'OFFICE'
       OR source_at_execution->>'organism' IS DISTINCT FROM owner_uuid::text
       OR source_at_execution->>'circle' IS NOT NULL
       OR source_at_execution->>'reference' IS NULL
       OR public.disc_office(NEW.organism_id,NULL,NEW.actor_id,public.intevia_clock_instant())
            IS DISTINCT FROM source_at_execution THEN
        RAISE EXCEPTION 'CARE_SETTING_AUTHORITY' USING ERRCODE='23514';
    END IF;
    SELECT * INTO previous FROM public.core_careassistancesettingact
    WHERE organism_id=NEW.organism_id ORDER BY version DESC LIMIT 1;
    IF previous.act_uuid IS DISTINCT FROM NEW.predecessor THEN
        RAISE EXCEPTION 'CARE_SETTING_PREDECESSOR' USING ERRCODE='23514';
    END IF;
    IF previous.act_uuid IS NOT NULL AND previous.enabled=NEW.enabled THEN
        RAISE EXCEPTION 'CARE_SETTING_UNCHANGED' USING ERRCODE='23514';
    END IF;
    NEW.version := coalesce(previous.version,0)+1;
    NEW.enterer_id := NEW.actor_id;
    NEW.authority_source := source_at_execution;
    NEW.office_uuid := (source_at_execution->>'reference')::uuid;
    NEW.fingerprint := public.care_setting_intent(owner_uuid,actor_uuid,NEW.actor_epoch,
                                                  NEW.act_uuid,NEW.predecessor,NEW.enabled);
    NEW.occurred_at := NEW.received_at;
    NEW.effective_at := execution_at;
    NEW.recorded_at := execution_at;
    NEW.execution_xid := pg_current_xact_id();
    RETURN NEW;
END $$;

-- The same boolean is consumed by the existing gateway's future single-action branch
-- and by both deferred completion triggers. Missing detail always yields false.
CREATE FUNCTION public.care_setting_matches(e public.core_livingorganismevent)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT FROM public.core_careassistancesettingact a
        JOIN public.core_livingorganism o ON o.id=a.organism_id
        WHERE a.act_uuid=e.event_uuid AND a.organism_id=e.living_organism_id
          AND a.execution_xid=pg_current_xact_id()
          AND e.action='SET_CARE_ASSISTANCE' AND e.sequence>1 AND e.predecessor_id IS NOT NULL
          AND e.prior_state=e.resulting_state AND e.prior_state IS NOT NULL
          AND e.actor_state='LIVING_ORGANISM' AND e.actor_capacity='STANDING'
          AND e.actor_id IS NULL AND e.actor_access_epoch IS NULL
          AND e.entry_mode='TRANSPORT' AND e.enterer_identity_id=a.enterer_id
          AND e.composing_rule_state='NA_TRANSPORT' AND e.composing_rule_reference IS NULL
          AND e.composer_state='NA_TRANSPORT' AND e.composer_identity_id IS NULL
          AND e.effective_until_state='NONE' AND e.effective_until IS NULL
          AND e.authority_basis_id=o.founding_authority_basis_id
          AND e.authority_decision_reference=public.care_setting_decision(a.authority_source,a.fingerprint)
          AND e.payload_fingerprint=a.fingerprint
          AND e.evidence_reference='care-setting-act:'||a.act_uuid::text
          AND e.request_reference='care-setting:'||a.act_uuid::text
          AND e.idempotency_key=e.request_reference
          AND e.occurred_at=a.occurred_at AND e.received_at=a.received_at
          AND e.effective_at=a.effective_at AND e.recorded_at=a.recorded_at
          AND e.temporal_basis_kind IS NOT DISTINCT FROM
              (CASE WHEN a.effective_at>a.received_at THEN 'PROSPECTIVE' END)
          AND e.temporal_basis_reference IS NOT DISTINCT FROM
              (CASE WHEN a.effective_at>a.received_at THEN 's015tb1:'||a.fingerprint END)
    )
$$;

CREATE FUNCTION public.care_setting_complete()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    event_key uuid;
BEGIN
    IF TG_TABLE_NAME='core_livingorganismevent' THEN
        event_key := NEW.event_uuid;
    ELSE
        event_key := NEW.act_uuid;
    END IF;
    IF NOT EXISTS (
        SELECT FROM public.core_livingorganismevent e
        WHERE e.event_uuid=event_key AND public.care_setting_matches(e)
    ) THEN
        RAISE EXCEPTION 'CARE_SETTING_PAIR' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER care_setting_input BEFORE INSERT ON public.core_careassistancesettingact
    FOR EACH ROW EXECUTE FUNCTION public.care_setting_input();
CREATE TRIGGER care_setting_immutable BEFORE UPDATE OR DELETE ON public.core_careassistancesettingact
    FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_care_setting_no_truncate BEFORE TRUNCATE ON public.core_careassistancesettingact
    FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER care_setting_detail_complete AFTER INSERT ON public.core_careassistancesettingact
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.care_setting_complete();
CREATE CONSTRAINT TRIGGER care_setting_event_complete AFTER INSERT ON public.core_livingorganismevent
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.action='SET_CARE_ASSISTANCE')
    EXECUTE FUNCTION public.care_setting_complete();
"""

QUALIFIER = r"""
CREATE FUNCTION public.care_alpha_treasurer_qualify(
    p_organism uuid, p_definition uuid, p_requester bigint, p_epoch bigint,
    p_cut timestamptz, p_for_insert boolean
)
RETURNS TABLE (
    organism_id bigint, definition_uuid uuid, provision_uuid uuid, binding_uuid uuid,
    library_version_id bigint, library_version_number integer, adoption_uuid uuid,
    resource_reference text, locator text, clause_text text, observation_kind text,
    rule_reference text, sentence text, setting_act_uuid uuid, qualification_at timestamptz,
    may_create boolean
)
LANGUAGE sql STABLE STRICT SECURITY INVOKER AS $qualifier$
WITH rule AS (
    SELECT '742ce2ddbd4762c42d5b3c973b9aba2a889139d9f29423b2b147443d73e58823'::text AS approved_clause_sha256,
           'ALPHA_TREASURER_VACANCY'::text AS kind,
           'CARE:ALPHA:TREASURER_VACANCY:1'::text AS ref,
           'The cited policy says the Treasurer approves ordinary payments, and this clause does not say who approves them while the office is vacant.'::text AS words
), reader AS (
    -- The caller binds this identity under its credential lock before the owner
    -- lock. The function independently rechecks live access, epoch and membership.
    SELECT lo.id, lo.organism_id AS owner_uuid,
           public.disc_member_state(lo.id,i.id,p_cut) AS member_state
    FROM public.core_livingorganism lo
    JOIN public.core_identity i ON i.id=p_requester
    JOIN public.auth_user u ON u.id=i.credential_id
    WHERE lo.organism_id=p_organism AND p_cut=public.intevia_clock_statement()
      AND i.access_state='active' AND i.access_epoch=p_epoch AND u.is_active
), candidate AS (
    SELECT r.id AS owner_id,r.owner_uuid,r.member_state,
           d.role_uuid,d.received_at,d.recorded_at,d.effective_at,
           p.provision_uuid,k.version_id,k.locator,sw.act_uuid AS setting_act
    FROM reader r
    JOIN public.core_organismroledefinition d ON d.living_organism_id=r.id
    JOIN public.core_organismroledefinitionprovision p ON p.definition_id=d.id
    JOIN public.core_lawprovisionlocator k ON k.provision_id=p.id
    LEFT JOIN LATERAL (
        SELECT s.act_uuid,s.enabled FROM public.core_careassistancesettingact s
        WHERE s.organism_id=r.id AND s.effective_at<=p_cut AND s.recorded_at<=p_cut
        ORDER BY s.version DESC LIMIT 1
    ) sw ON true
    WHERE r.member_state IN ('ACTIVE','SUSPENDED','RESTRICTED')
      AND (NOT p_for_insert OR r.member_state='ACTIVE')
      AND coalesce(sw.enabled,true)
      AND d.role_uuid=p_definition AND d.active AND d.code='TREASURER'
      AND d.scope='LIVING_ORGANISM' AND d.kind='BOARD_OFFICE'
      AND d.effective_at<=p_cut AND d.received_at<=p_cut AND d.recorded_at<=p_cut
      AND p.relationship='OFFICE_BASIS' AND p.recorded_at<=p_cut AND k.locator='approval'
      AND NOT EXISTS (
          SELECT FROM public.core_organismroledefinitionprovision alt
          WHERE alt.definition_id=d.id AND alt.relationship='ALTERNATIVE_ROUTE'
      )
      AND NOT EXISTS (
          SELECT FROM public.core_organismroledefinition family
          JOIN public.core_contextualroleassignment a ON a.role_definition_id=family.id
          WHERE family.living_organism_id=r.id AND family.scope=d.scope AND family.code=d.code
            AND family.kind=d.kind
            AND a.circle_id IS NULL AND public.elect_office_effective(a.id,p_cut,p_cut)
      )
), original AS (
    -- Count *all* LAW origin candidates before choosing the local Policy.
    -- Matches law_records._CITATION_HISTORY's received/recorded and effective/recorded cuts.
    SELECT c.*,o.binding AS original_binding,o.resource AS original_resource
    FROM candidate c
    CROSS JOIN LATERAL (
        SELECT law.binding,law.resource,b.law_class,b.scope_kind,b.organism_id,count(*) OVER () AS n
        FROM public.l_laws_at(c.owner_uuid,c.received_at,c.recorded_at) law
        JOIN public.core_organismlawbinding b ON b.binding_uuid=law.binding
        WHERE law.version=c.version_id AND b.scope_kind IN ('LIVING_ORGANISM','PLATFORM')
          AND public.l_visible_window(b,c.effective_at,c.recorded_at)
    ) o
    WHERE o.n=1 AND o.scope_kind='LIVING_ORGANISM' AND o.organism_id=c.owner_id
      AND o.law_class='POLICY'
)
SELECT c.owner_id,c.role_uuid,c.provision_uuid,current_law.binding,current_law.version,
       current_law.version_number,current_law.act,current_law.library_reference,c.locator,cl.content,
       rule.kind,rule.ref,rule.words,c.setting_act,p_cut,(c.member_state='ACTIVE')
FROM original c
-- This is the exact member LAW text projection at presentation. The tenure
-- audience above is a subset of our_laws' current member audience in this owner.
CROSS JOIN LATERAL (
    SELECT laws.binding,laws.version,laws.version_number,laws.act,laws.library_reference,
           laws.issuer,laws.scope_kind,laws.class_name,laws.content,count(*) OVER () AS n
    FROM public.lib_laws_at(c.owner_uuid,p_cut,p_cut) laws
    WHERE laws.resource=c.original_resource AND laws.scope_kind IN ('LIVING_ORGANISM','PLATFORM')
) current_law
CROSS JOIN LATERAL (
    SELECT min(x.content) AS content FROM public.l_clauses(current_law.content) x
    WHERE x.locator=c.locator HAVING count(*)=1
) cl
CROSS JOIN rule
WHERE current_law.n=1 AND current_law.binding=c.original_binding AND current_law.version=c.version_id
  AND current_law.issuer=c.owner_uuid AND current_law.scope_kind='LIVING_ORGANISM'
  AND current_law.class_name='POLICY'
  AND encode(sha256(convert_to(cl.content,'UTF8')),'hex')=rule.approved_clause_sha256
$qualifier$;
"""

OBSERVATION = r"""
CREATE TABLE public.core_careofficeobservation (
    event_uuid uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organism_id bigint NOT NULL REFERENCES public.core_livingorganism(id),
    definition_uuid uuid NOT NULL REFERENCES public.core_organismroledefinition(role_uuid),
    provision_uuid uuid NOT NULL REFERENCES public.core_organismroledefinitionprovision(provision_uuid),
    binding_uuid uuid NOT NULL REFERENCES public.core_organismlawbinding(binding_uuid),
    library_version_id bigint NOT NULL REFERENCES public.core_libraryresourceversion(id),
    library_version_number integer NOT NULL CHECK (library_version_number > 0),
    adoption_uuid uuid NOT NULL REFERENCES public.core_livingorganismevent(event_uuid),
    resource_reference text NOT NULL,
    locator text NOT NULL,
    clause_text text NOT NULL,
    observation_kind text NOT NULL,
    rule_reference text NOT NULL,
    sentence text NOT NULL,
    setting_act_uuid uuid,
    requester_id bigint NOT NULL REFERENCES public.core_identity(id),
    requester_epoch bigint NOT NULL CHECK (requester_epoch >= 0),
    received_at timestamptz NOT NULL,
    qualification_at timestamptz NOT NULL,
    occurred_at timestamptz NOT NULL,
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL,
    execution_xid xid8 NOT NULL,
    payload_fingerprint text NOT NULL CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT care_observation_source_key UNIQUE
        (organism_id,definition_uuid,provision_uuid,binding_uuid,observation_kind),
    CONSTRAINT care_observation_setting_owner FOREIGN KEY (organism_id,setting_act_uuid)
        REFERENCES public.core_careassistancesettingact(organism_id,act_uuid),
    CONSTRAINT care_observation_time CHECK (
        occurred_at=received_at AND received_at<=qualification_at
        AND effective_at=qualification_at AND recorded_at=qualification_at),
    CONSTRAINT care_observation_ilc_fk FOREIGN KEY (event_uuid)
        REFERENCES public.core_livingorganismevent(event_uuid) DEFERRABLE INITIALLY DEFERRED
);

-- Hash the public source evidence. The immutable detail binds requester and epoch separately.
-- Time coordinates use integral microseconds, independent of session time zone.
CREATE FUNCTION public.care_observation_payload(o public.core_careofficeobservation)
RETURNS text LANGUAGE sql IMMUTABLE STRICT SECURITY INVOKER AS $$
    SELECT encode(sha256(convert_to('CARE-OFFICE-OBSERVATION:1:' || public.l_canonical(
        jsonb_build_array(o.event_uuid,o.organism_id,o.definition_uuid,o.provision_uuid,
            o.binding_uuid,o.library_version_id,o.library_version_number,o.adoption_uuid,
            o.resource_reference,o.locator,o.clause_text,o.observation_kind,o.rule_reference,
            o.sentence,o.setting_act_uuid,
            (extract(epoch FROM o.received_at)*1000000)::numeric(30,0),
            (extract(epoch FROM o.qualification_at)*1000000)::numeric(30,0))), 'UTF8')), 'hex')
$$;

CREATE FUNCTION public.care_observation_decision(p_rule text,p_payload text)
RETURNS text LANGUAGE sql IMMUTABLE STRICT SECURITY INVOKER AS $$
    SELECT 's015d1:' || encode(sha256(convert_to('CARE-OFFICE-RULE:1:' ||
        public.l_canonical(jsonb_build_array(p_rule,p_payload)), 'UTF8')), 'hex')
$$;

CREATE FUNCTION public.care_observation_input()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER AS $$
DECLARE
    owner_uuid uuid;
    stamp timestamptz := public.intevia_clock_statement();
    evidence record;
    fingerprint text;
BEGIN
    IF NEW.qualification_at IS NOT NULL OR NEW.occurred_at IS NOT NULL OR NEW.effective_at IS NOT NULL
       OR NEW.recorded_at IS NOT NULL OR NEW.execution_xid IS NOT NULL THEN
        RAISE EXCEPTION 'CARE_OBSERVATION_INPUT' USING ERRCODE='23514';
    END IF;
    IF NEW.received_at IS NULL OR NEW.received_at<public.intevia_clock_transaction()
       OR NEW.received_at>stamp THEN
        RAISE EXCEPTION 'CARE_OBSERVATION_RECEIPT' USING ERRCODE='23514';
    END IF;
    -- The normal caller already holds these locks. A contending raw proposal
    -- must retry in a fresh statement, never carry a pre-wait qualification forward.
    PERFORM 1 FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.requester_id AND i.access_epoch=NEW.requester_epoch AND u.is_active
    FOR NO KEY UPDATE OF i,u NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION 'CARE_OBSERVATION_SOURCE' USING ERRCODE='23514'; END IF;
    SELECT lo.organism_id INTO owner_uuid FROM public.core_livingorganism lo
    WHERE lo.id=NEW.organism_id FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION 'CARE_OBSERVATION_SOURCE' USING ERRCODE='23514'; END IF;
    PERFORM public.s015_lock_organisms(ARRAY[owner_uuid]);

    SELECT q.* INTO evidence FROM public.care_alpha_treasurer_qualify(
        owner_uuid,NEW.definition_uuid,NEW.requester_id,NEW.requester_epoch,stamp,true) q;
    IF NOT FOUND THEN RAISE EXCEPTION 'CARE_OBSERVATION_SOURCE' USING ERRCODE='23514'; END IF;
    IF evidence.may_create IS DISTINCT FROM true OR evidence.qualification_at IS DISTINCT FROM stamp
       OR ROW(NEW.organism_id,NEW.definition_uuid,NEW.provision_uuid,NEW.binding_uuid,
              NEW.library_version_id,NEW.library_version_number,NEW.adoption_uuid,NEW.resource_reference,
              NEW.locator,NEW.clause_text,NEW.observation_kind,NEW.rule_reference,NEW.sentence,NEW.setting_act_uuid)
          IS DISTINCT FROM
          ROW(evidence.organism_id,evidence.definition_uuid,evidence.provision_uuid,evidence.binding_uuid,
              evidence.library_version_id,evidence.library_version_number,evidence.adoption_uuid,evidence.resource_reference,
              evidence.locator,evidence.clause_text,evidence.observation_kind,evidence.rule_reference,evidence.sentence,evidence.setting_act_uuid)
    THEN RAISE EXCEPTION 'CARE_OBSERVATION_SOURCE' USING ERRCODE='23514'; END IF;
    NEW.qualification_at := stamp;
    NEW.occurred_at := NEW.received_at;
    NEW.effective_at := stamp;
    NEW.recorded_at := stamp;
    NEW.execution_xid := pg_current_xact_id();
    fingerprint := public.care_observation_payload(NEW::public.core_careofficeobservation);
    IF NEW.payload_fingerprint IS NOT NULL AND NEW.payload_fingerprint IS DISTINCT FROM fingerprint THEN
        RAISE EXCEPTION 'CARE_OBSERVATION_FINGERPRINT' USING ERRCODE='23514';
    END IF;
    NEW.payload_fingerprint := fingerprint;
    RETURN NEW;
END $$;

CREATE FUNCTION public.care_observation_matches(e public.core_livingorganismevent)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER AS $$
    SELECT EXISTS (
        SELECT FROM public.core_careofficeobservation o
        JOIN public.core_livingorganism lo ON lo.id=o.organism_id
        WHERE e.action='RECORD_CARE_OFFICE_OBSERVATION'
          AND o.event_uuid=e.event_uuid AND o.organism_id=e.living_organism_id
          AND o.execution_xid=pg_current_xact_id()
          AND e.sequence>1 AND e.predecessor_id IS NOT NULL
          AND e.prior_state=e.resulting_state AND e.prior_state IS NOT NULL
          AND e.actor_state='NONE' AND e.actor_capacity='NA_NO_PARTY'
          AND e.actor_id IS NULL AND e.actor_access_epoch IS NULL
          AND e.entry_mode='COMPOSED' AND e.composing_rule_state='REFERENCE'
          AND e.composing_rule_reference=o.rule_reference
          AND e.composer_state='NOT_ESTABLISHED' AND e.composer_identity_id IS NULL
          AND e.enterer_identity_id=o.requester_id
          AND e.authority_basis_id=lo.founding_authority_basis_id
          AND e.payload_fingerprint=o.payload_fingerprint
          AND e.authority_decision_reference=public.care_observation_decision(o.rule_reference,o.payload_fingerprint)
          AND e.request_reference='care-office-presentation:'||o.event_uuid::text
          AND e.idempotency_key=e.request_reference
          AND e.evidence_reference='care-office-observation:'||o.event_uuid::text
          AND e.effective_until_state='NONE' AND e.effective_until IS NULL
          AND e.occurred_at=o.occurred_at AND e.received_at=o.received_at
          AND e.effective_at=o.effective_at AND e.recorded_at=o.recorded_at
          AND e.temporal_basis_kind IS NOT DISTINCT FROM
              (CASE WHEN o.effective_at>o.received_at THEN 'PROSPECTIVE' END)
          AND e.temporal_basis_reference IS NOT DISTINCT FROM
              (CASE WHEN o.effective_at>o.received_at THEN 's015tb1:'||o.payload_fingerprint END)
    )
$$;

CREATE FUNCTION public.care_observation_complete()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER AS $$
BEGIN
    IF NOT EXISTS (SELECT FROM public.core_livingorganismevent e
        WHERE e.event_uuid=NEW.event_uuid AND public.care_observation_matches(e)) THEN
        RAISE EXCEPTION 'CARE_OBSERVATION_PAIR' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER care_observation_input BEFORE INSERT ON public.core_careofficeobservation
    FOR EACH ROW EXECUTE FUNCTION public.care_observation_input();
CREATE TRIGGER care_observation_immutable BEFORE UPDATE OR DELETE ON public.core_careofficeobservation
    FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_care_observation_no_truncate BEFORE TRUNCATE ON public.core_careofficeobservation
    FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER care_observation_detail_complete AFTER INSERT ON public.core_careofficeobservation
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.care_observation_complete();
CREATE CONSTRAINT TRIGGER care_observation_event_complete AFTER INSERT ON public.core_livingorganismevent
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.action='RECORD_CARE_OFFICE_OBSERVATION')
    EXECUTE FUNCTION public.care_observation_complete();
"""

PREFLIGHT = """
-- First reverse operation in the single 0111 migration transaction.
-- Hold all three write exclusions through both function restores and schema removal.
LOCK TABLE public.core_careassistancesettingact,
           public.core_careofficeobservation,
           public.core_livingorganismevent
    IN SHARE ROW EXCLUSIVE MODE;

-- A separate statement takes a fresh snapshot after the locks on READ COMMITTED.
DO $care_history$
BEGIN
    IF EXISTS (SELECT 1 FROM public.core_careassistancesettingact)
       OR EXISTS (SELECT 1 FROM public.core_careofficeobservation)
       OR EXISTS (
           SELECT 1 FROM public.core_livingorganismevent
           WHERE action IN ('SET_CARE_ASSISTANCE', 'RECORD_CARE_OFFICE_OBSERVATION')
       ) THEN
        RAISE EXCEPTION 'CARE_OFFICE_RECORD_POPULATED' USING ERRCODE='23514',
            HINT='Keep 0111 applied to preserve the recorded CARE history.';
    END IF;
END;
$care_history$;
"""

# Only the new action branches are interpolated into catalogue definitions.
# Pin the entire result on both sides and use the existing OID/ACL-preserving swap.
WRITER = 'public.s015_append_event(text,bigint,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone,bigint,bigint,text,text,text,text,jsonb,timestamp with time zone,uuid,bigint,integer,bigint,text,text)'
WRITER_ADDITIONS = """ IF action_name='SET_CARE_ASSISTANCE' THEN
   IF token IS DISTINCT FROM 'core_livingorganism' OR NOT is_command
      OR supplied_head IS NULL OR supplied_sequence IS NULL OR supplied_sequence < 1
      OR prior IS NULL OR outcome IS DISTINCT FROM prior
      OR at_time IS DISTINCT FROM public.intevia_clock_statement()
      OR received IS NULL OR occurred IS DISTINCT FROM received OR received > at_time
      OR actor IS NULL OR basis IS NULL OR access_epoch IS NOT NULL
      OR command_rule IS NOT NULL OR deadline IS NOT NULL OR metadata IS DISTINCT FROM '{}'::jsonb THEN
     RAISE EXCEPTION 'CARE_SETTING_ENVELOPE' USING ERRCODE='23514';
   END IF;
   -- The organisation acts. The generic transport keeps the actual human enterer.
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF action_name='RECORD_CARE_OFFICE_OBSERVATION' THEN
   IF token IS DISTINCT FROM 'core_livingorganism' OR NOT is_command
      OR supplied_head IS NULL OR supplied_sequence IS NULL OR supplied_sequence < 1
      OR prior IS NULL OR outcome IS DISTINCT FROM prior
      OR at_time IS DISTINCT FROM public.intevia_clock_statement()
      OR received IS NULL OR occurred IS DISTINCT FROM received OR received > at_time
      OR actor IS NULL OR basis IS NULL OR access_epoch IS NOT NULL
      OR nullif(btrim(command_rule),'') IS NULL OR deadline IS NOT NULL OR metadata IS DISTINCT FROM '{}'::jsonb THEN
     RAISE EXCEPTION 'CARE_OBSERVATION_ENVELOPE' USING ERRCODE='23514';
   END IF;
   -- The generic composed event already has no actor and keeps the requester as enterer.
   -- The rule supplied the words. No human composer was established.
   values_:=values_||jsonb_build_object('composer_state','NOT_ESTABLISHED','composer_identity_id',NULL);
 END IF;

"""

GATEWAY = 'public.s3fu_guard_gateway_effect()'
GATEWAY_ADDITIONS = """    IF NEW.action='SET_CARE_ASSISTANCE' THEN
      IF public.care_setting_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'CARE_SETTING_PAIR' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='RECORD_CARE_OFFICE_OBSERVATION' THEN
      IF public.care_observation_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'CARE_OBSERVATION_PAIR' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

"""

REPLACEMENTS = (
    (WRITER,
     " IF token='core_contextualroleassignment' AND action_name='ASSIGN' AND (public.elect_office_source(", WRITER_ADDITIONS,
     "1a5a28da471b96c04e1cbc36f936d2a1188f63d6b1e940ec8e8d24d656b0ae63",
     "39c40cfdb3def4fefba837f49c9cc9685031a907face3c11d28749ba3a7dff4f"),
    (GATEWAY,
     "    IF NEW.action='RECORD_CQ' THEN\n", GATEWAY_ADDITIONS,
     "ac69095df8c59d0d9dadfdede5f49b409fd1af8007e78d3db9667ad811786ca7",
     "496afd9498b1d672636772f954e89d156bc43fda990890c8b6109dc5f5169a15"),
)

REMOVE = """
DROP TRIGGER care_observation_event_complete ON public.core_livingorganismevent;
DROP TRIGGER care_setting_event_complete ON public.core_livingorganismevent;

DROP TRIGGER care_observation_detail_complete ON public.core_careofficeobservation;
DROP TRIGGER s015_care_observation_no_truncate ON public.core_careofficeobservation;
DROP TRIGGER care_observation_immutable ON public.core_careofficeobservation;
DROP TRIGGER care_observation_input ON public.core_careofficeobservation;
DROP FUNCTION public.care_observation_complete();
DROP FUNCTION public.care_observation_matches(public.core_livingorganismevent);
DROP FUNCTION public.care_observation_input();
DROP FUNCTION public.care_observation_decision(text,text);
DROP FUNCTION public.care_observation_payload(public.core_careofficeobservation);
DROP TABLE public.core_careofficeobservation;

DROP FUNCTION public.care_alpha_treasurer_qualify(uuid,uuid,bigint,bigint,timestamptz,boolean);
DROP TRIGGER care_setting_detail_complete ON public.core_careassistancesettingact;
DROP TRIGGER s015_care_setting_no_truncate ON public.core_careassistancesettingact;
DROP TRIGGER care_setting_immutable ON public.core_careassistancesettingact;
DROP TRIGGER care_setting_input ON public.core_careassistancesettingact;
DROP FUNCTION public.care_setting_complete();
DROP FUNCTION public.care_setting_matches(public.core_livingorganismevent);
DROP FUNCTION public.care_setting_input();
DROP FUNCTION public.care_setting_decision(jsonb,text);
DROP FUNCTION public.care_setting_intent(uuid,uuid,bigint,uuid,uuid,boolean);
DROP TABLE public.core_careassistancesettingact;
"""


def installed_definition(cursor, signature):
    cursor.execute("SELECT pg_catalog.pg_get_functiondef(pg_catalog.to_regprocedure(%s))", [signature])
    return cursor.fetchone()[0]


def require_digest(text, expected, signature):
    if text is None or sha256(text.encode("utf-8")).hexdigest() != expected:
        raise RuntimeError("0111 found an unexpected function definition for " + signature)


def replace_functions(cursor, forward):
    for signature, anchor, additions, old_hash, new_hash in (REPLACEMENTS if forward else reversed(REPLACEMENTS)):
        before = installed_definition(cursor, signature)
        expected, target = (old_hash, new_hash) if forward else (new_hash, old_hash)
        require_digest(before, expected, signature)
        original, replacement = (anchor, additions + anchor) if forward else (additions + anchor, anchor)
        if before.count(original) != 1:
            raise RuntimeError("0111 requires one exact insertion point for " + signature)
        after = before.replace(original, replacement, 1)
        require_digest(after, target, signature)
        # The verified pg_get_functiondef used this delimiter. Retain every byte of prosrc.
        swap(cursor, signature, before.split("$function$")[1], after.split("$function$")[1])
        require_digest(installed_definition(cursor, signature), target, signature)


def install(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SETTING)
        cursor.execute(QUALIFIER)
        cursor.execute(OBSERVATION)
        replace_functions(cursor, forward=True)


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(PREFLIGHT)
        replace_functions(cursor, forward=False)
        cursor.execute(REMOVE)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0110_discussion_office_link")]
    operations = [migrations.RunPython(install, uninstall)]
