"""A durable filling instruction from an effective office or trustee-place ending."""

SQL = r"""
CREATE VIEW public.elect_vacancy_sources AS
 SELECT e.event_uuid AS ending, d.living_organism_id AS organism_id, 'OFFICE'::text AS kind,d.code::text AS place,
     e.effective_at,e.recorded_at,r.membership_id, r.assignment_uuid AS occupancy,e.enterer_identity_id AS source_enterer
 FROM public.core_contextualroleassignmentstateevent e JOIN public.core_contextualroleassignment r ON r.id=e.assignment_id
 JOIN public.core_organismroledefinition d ON d.id=r.role_definition_id
 WHERE e.resulting_state='ENDED' AND e.prior_state IN ('ACTIVE','SUSPENDED') AND d.kind='BOARD_OFFICE'
   AND NOT starts_with(d.code,'INTEVIA_') AND d.code<>'COORDINATOR'
 UNION ALL
 SELECT p.participation_uuid,c.parent_organism_id,'SEAT',p.seat_id,x.effective_at,x.recorded_at,
    p.membership_id,p.participation_uuid,x.enterer_identity_id
 FROM public.core_circleparticipation p JOIN public.core_circle c ON c.id=p.circle_id
 CROSS JOIN LATERAL (SELECT e.effective_at,e.recorded_at,e.enterer_identity_id FROM public.core_circleparticipationend e
   WHERE e.participation_id=p.id ORDER BY e.effective_at,e.recorded_at,e.id LIMIT 1) x
 WHERE c.kind='GOVERNANCE' AND p.seat_id IS NOT NULL;

CREATE TABLE public.core_electvacancy (
 ending uuid PRIMARY KEY, organism_id bigint NOT NULL REFERENCES public.core_livingorganism(id),
 kind text NOT NULL, place text NOT NULL, effective_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL, instruction jsonb NOT NULL, enterer bigint NOT NULL REFERENCES public.core_identity(id),
 route_act uuid NOT NULL UNIQUE REFERENCES public.core_cqact(act_uuid) DEFERRABLE INITIALLY DEFERRED);
CREATE INDEX elect_vacancy_place ON public.core_electvacancy(organism_id,kind,place,effective_at DESC);
CREATE UNIQUE INDEX elect_vacancy_one_call ON public.core_cqact(organism_id, (request->'attachment'->'spec'->>'vacancy'))
 WHERE operation='INITIATE' AND request->'attachment'->>'kind'='ELECTION' AND request->'attachment'->'spec' ? 'vacancy';

-- Catch-up must not reopen an ending for which a successor was already recorded.
-- The currently inserting fill is linked by its AFTER trigger in this statement.
CREATE FUNCTION public.elect_already_replaced(s public.elect_vacancy_sources,p_cut timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN s.kind='SEAT' THEN EXISTS(
   SELECT FROM public.core_circleparticipation p JOIN public.core_circle c ON c.id=p.circle_id
   WHERE c.kind='GOVERNANCE' AND c.parent_organism_id=s.organism_id AND p.seat_id=s.place
     AND p.participation_uuid<>s.occupancy AND p.effective_at BETWEEN s.effective_at AND p_cut
     AND p.recorded_at<p_cut)
 ELSE EXISTS(
   SELECT FROM public.core_contextualroleassignmentstateevent e JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
   JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
   WHERE d.living_organism_id=s.organism_id AND d.kind='BOARD_OFFICE' AND d.code=s.place AND e.action='ASSIGN'
     AND a.assignment_uuid<>s.occupancy AND e.effective_at BETWEEN s.effective_at AND p_cut AND e.recorded_at<p_cut)
 END
$$;

CREATE FUNCTION public.elect_vacancy_instruction(s public.elect_vacancy_sources) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE law_ record; doc jsonb; row_ record; rule_ jsonb; selected jsonb; hits integer:=0;
 known timestamptz:=greatest(s.recorded_at,s.effective_at); arrangement public.core_governedrule; seat jsonb;
 govern bigint; evidence uuid; candidate_ record; seat_route text;
BEGIN
 SELECT x.event_uuid INTO evidence FROM public.core_coverageassessment x JOIN public.core_essentialcoveragecase k ON k.id=x.case_id
 WHERE k.living_organism_id=s.organism_id AND x.effective_at=s.effective_at AND
 (x.evidence_reference='office:'||s.ending OR EXISTS(SELECT FROM public.core_organismmembershiptransition m
 WHERE m.membership_id=s.membership_id AND m.effective_at=x.effective_at AND m.resulting_state='ENDED'
 AND x.evidence_reference='membership-ending:'||m.event_uuid)) ORDER BY x.sequence DESC LIMIT 1;
 FOR law_ IN SELECT l.binding,l.version,v.content FROM public.core_livingorganism lo
   CROSS JOIN LATERAL public.l_laws_at(lo.organism_id,s.effective_at,known) l
   JOIN public.core_libraryresourceversion v ON v.id=l.version
   WHERE lo.id=s.organism_id AND NOT l.inherited AND l.class_name IN ('ARTICLE','BYLAW') LOOP
   doc:=public.elect_document(law_.content);
   FOR row_ IN SELECT * FROM jsonb_each(CASE WHEN jsonb_typeof(doc->'vacancies')='object' THEN doc->'vacancies' ELSE '{}' END) LOOP
     rule_:=row_.value;
     IF rule_->>'type' IS DISTINCT FROM s.kind OR rule_->>'place' IS DISTINCT FROM s.place THEN CONTINUE; END IF;
     hits:=hits+1;
     selected:=jsonb_build_object('binding',law_.binding,'version',law_.version,'provision',row_.key,'rule',rule_,
          'route',rule_->'route','authority',rule_->'authority','election',rule_->'election');
   END LOOP;
 END LOOP;
 -- BY_SEAT_ROUTE is itself an express rule. On silence in LAW, follow the existing named seat instrument.
 IF s.kind='SEAT' AND hits=0 THEN
   SELECT * INTO arrangement FROM public.core_governedrule WHERE scope_organism_id=s.organism_id AND code='GOVERNANCE_CONSTITUTION'
    AND effective_from<=s.effective_at AND recorded_at<=known ORDER BY effective_from DESC,recorded_at DESC,version DESC LIMIT 1;
   SELECT j INTO seat FROM jsonb_array_elements(arrangement.body->'seats') j WHERE j->>'seat_id'=s.place;
   IF seat->>'vacancy_rule'='BY_SEAT_ROUTE' AND (seat->>'effective_from')::timestamptz<=s.effective_at THEN
     seat_route:=seat->>'admission_route';
     IF seat_route='APPOINTMENT' AND seat->>'deciding_body'='office:LO_COORDINATOR' THEN
       selected:=jsonb_build_object('route',seat_route,'authority',seat->'deciding_body',
        'seat_rule',arrangement.rule_uuid,'seat_version',arrangement.version,'provision',s.place); hits:=1;
     ELSIF seat_route IN ('ELECTION','COOPTION') THEN
       FOR candidate_ IN SELECT l.binding,l.version,j.key FROM public.core_livingorganism lo
         CROSS JOIN LATERAL public.l_laws_at(lo.organism_id,s.effective_at,known) l
         JOIN public.core_libraryresourceversion v ON v.id=l.version
         CROSS JOIN LATERAL (SELECT public.elect_document(v.content)->'elections' AS entries) d
         CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(d.entries)='object' THEN d.entries ELSE '{}' END) j
         WHERE lo.id=s.organism_id AND NOT l.inherited AND l.class_name IN ('ARTICLE','BYLAW')
         AND j.value->>'type'='SEAT' AND j.value->>'place'=s.place
         AND seat->>'deciding_body'='body:'||(j.value->>'body') LOOP
           hits:=hits+1; selected:=jsonb_build_object('route',CASE WHEN seat_route='ELECTION' THEN 'BY_ELECTION' ELSE seat_route END,
             'authority',substr(seat->>'deciding_body',6),'binding',candidate_.binding,'version',candidate_.version,
             'provision',s.place,'election',candidate_.key,'seat_rule',arrangement.rule_uuid,'seat_version',arrangement.version);
       END LOOP;
     END IF;
   END IF;
 END IF;
 IF hits=0 THEN selected:=jsonb_build_object('route','HOLD_HG','reason','The constitution supplies no filling provision for this vacancy.');
 ELSIF hits>1 THEN selected:=jsonb_build_object('route','HOLD_HG','reason','More than one current provision claims this vacancy.');
 ELSIF NOT coalesce((selected->>'route' IN ('COOPTION','BOARD_CQ') AND selected->>'authority'='GOVERNANCE'
      AND nullif(selected->>'election','') IS NOT NULL)
   OR (selected->>'route'='BY_ELECTION' AND selected->>'authority' IN ('GOVERNANCE','LO_MEMBERSHIP')
      AND nullif(selected->>'election','') IS NOT NULL)
   OR (selected->>'route'='APPOINTMENT' AND selected->>'authority' ~ '^office:[A-Za-z][A-Za-z0-9_]*$')
   OR (selected->>'route'='WAIT' AND nullif(selected->'rule'->>'until','') IS NOT NULL),false)
 THEN selected:=selected||jsonb_build_object('route','HOLD_HG','reason','The cited filling instruction requires a governed interpretation.');
 END IF;
 IF s.kind='SEAT' AND selected->>'route' IN ('COOPTION','BY_ELECTION','APPOINTMENT') THEN
   SELECT * INTO arrangement FROM public.core_governedrule WHERE scope_organism_id=s.organism_id AND code='GOVERNANCE_CONSTITUTION'
   AND effective_from<=s.effective_at AND recorded_at<=known ORDER BY effective_from DESC,recorded_at DESC,version DESC LIMIT 1;
   SELECT v INTO seat FROM jsonb_array_elements(arrangement.body->'seats') v WHERE v->>'seat_id'=s.place;
   IF seat->>'vacancy_rule' IS DISTINCT FROM 'BY_SEAT_ROUTE' OR seat->>'admission_route' IS DISTINCT FROM
      (CASE WHEN selected->>'route'='BY_ELECTION' THEN 'ELECTION' ELSE selected->>'route' END)
      OR seat->>'deciding_body' IS DISTINCT FROM
      (CASE WHEN selected->>'route'='APPOINTMENT' THEN selected->>'authority' ELSE 'body:'||(selected->>'authority') END)
   THEN selected:=selected||jsonb_build_object('route','HOLD_HG','reason','The seat arrangement and filling provision do not agree.');
   END IF;
 END IF;
 govern:=public.cq3_board(s.organism_id,s.effective_at,known);
 RETURN selected||jsonb_build_object('source',s.ending,'occupancy',s.occupancy,'assessment',evidence,
    'board_active_at_ending',govern IS NOT NULL);
END $$;

CREATE FUNCTION public.elect_board_confirmation(p_att jsonb) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_electvacancy v JOIN public.core_livingorganism lo ON lo.id=v.organism_id
  WHERE v.ending::text=p_att->'spec'->>'vacancy' AND lo.organism_id::text=p_att->'spec'->>'organism'
  AND v.instruction->>'route' IN ('BOARD_CQ','COOPTION'))
$$;

CREATE FUNCTION public.elect_vacancy_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s public.elect_vacancy_sources; cut timestamptz:=statement_timestamp();
BEGIN
 SELECT * INTO s FROM public.elect_vacancy_sources WHERE ending=NEW.ending AND organism_id=NEW.organism_id;
 IF s.ending IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.enterer AND i.access_state='active' AND u.is_active) OR
    (NEW.enterer IS DISTINCT FROM s.source_enterer AND public.disc_member_state(s.organism_id,NEW.enterer,cut) IS DISTINCT FROM 'ACTIVE')
    OR public.elect_already_replaced(s,cut) OR greatest(s.recorded_at,s.effective_at)>cut OR
    (public.cq_settings_at(s.organism_id,greatest(s.recorded_at,s.effective_at))->>'enabled')::boolean IS NOT TRUE
 THEN PERFORM public.cq_refuse('ELECT_VACANCY_SOURCE'); END IF;
 NEW.route_act:=gen_random_uuid(); NEW.kind:=s.kind; NEW.place:=s.place; NEW.effective_at:=s.effective_at; NEW.recorded_at:=cut;
 NEW.instruction:=public.elect_vacancy_instruction(s); RETURN NEW;
END $$;
CREATE TRIGGER elect_vacancy_input BEFORE INSERT ON public.core_electvacancy FOR EACH ROW EXECUTE FUNCTION public.elect_vacancy_guard();
CREATE TRIGGER elect_vacancy_immutable BEFORE UPDATE OR DELETE ON public.core_electvacancy FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_elect_vacancy_no_truncate BEFORE TRUNCATE ON public.core_electvacancy FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();

CREATE FUNCTION public.elect_start_vacancy(p_lo bigint,p_ending uuid,p_enterer bigint) RETURNS boolean LANGUAGE plpgsql VOLATILE AS $$
DECLARE writes integer;
BEGIN
 PERFORM 1 FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 INSERT INTO public.core_electvacancy(ending,organism_id,enterer)
 SELECT s.ending,s.organism_id,p_enterer FROM public.elect_vacancy_sources s WHERE s.organism_id=p_lo AND s.ending=p_ending
 AND NOT public.elect_already_replaced(s,statement_timestamp())
 AND greatest(s.effective_at,s.recorded_at)<=statement_timestamp()
 AND EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active)
 AND EXISTS(SELECT FROM public.core_circle c WHERE c.parent_organism_id=p_lo AND c.kind='GOVERNANCE')
 AND (public.cq_settings_at(p_lo,greatest(s.recorded_at,s.effective_at))->>'enabled')::boolean
 AND NOT EXISTS(SELECT FROM public.core_electvacancy v WHERE v.ending=s.ending)
 ON CONFLICT(ending) DO NOTHING;
 GET DIAGNOSTICS writes=ROW_COUNT; RETURN writes=1;
END $$;

CREATE FUNCTION public.elect_vacancy_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO public.core_cqact(act_uuid,organism_id,operation,actor_id,actor_epoch,received_at,request)
 SELECT NEW.route_act,NEW.organism_id,'ELECT_VACANCY',i.id,i.access_epoch,statement_timestamp(),jsonb_build_object('ending',NEW.ending)
 FROM public.core_identity i WHERE i.id=NEW.enterer;
 RETURN NULL;
END $$;
CREATE TRIGGER elect_vacancy_receipt AFTER INSERT ON public.core_electvacancy FOR EACH ROW EXECUTE FUNCTION public.elect_vacancy_receipt();

CREATE FUNCTION public.elect_start_from_ending() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo bigint; source_ uuid;
BEGIN
 IF TG_TABLE_NAME='core_circleparticipationend' THEN
   SELECT c.parent_organism_id,p.participation_uuid INTO lo,source_ FROM public.core_circleparticipation p
    JOIN public.core_circle c ON c.id=p.circle_id WHERE p.id=NEW.participation_id AND c.kind='GOVERNANCE';
 ELSE
   SELECT d.living_organism_id INTO lo FROM public.core_contextualroleassignment a
    JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE';
   source_:=NEW.event_uuid;
 END IF;
 IF lo IS NOT NULL THEN PERFORM public.elect_start_vacancy(lo,source_,NEW.enterer_identity_id); END IF;
 RETURN NULL;
END $$;
-- Deferred until the causal C2 assessment has been recorded in the same transaction.
CREATE CONSTRAINT TRIGGER elect_vacancy_from_office AFTER INSERT ON public.core_contextualroleassignmentstateevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.resulting_state='ENDED' AND NEW.prior_state IN ('ACTIVE','SUSPENDED'))
 EXECUTE FUNCTION public.elect_start_from_ending();
CREATE CONSTRAINT TRIGGER elect_vacancy_from_seat AFTER INSERT ON public.core_circleparticipationend
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.elect_start_from_ending();

CREATE FUNCTION public.elect_call_route(p_lo bigint,p_spec jsonb,p_rule jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v public.core_electvacancy; i jsonb;
BEGIN
 SELECT * INTO v FROM public.core_electvacancy WHERE organism_id=p_lo AND ending::text=p_spec->>'vacancy' AND effective_at<=p_cut;
 i:=v.instruction;
 IF v.ending IS NULL THEN RETURN jsonb_build_object('code','DISC_UNAVAILABLE','reason','The vacancy is unavailable.'); END IF;
 IF i->>'route'='HOLD_HG' THEN RETURN jsonb_build_object('code','ELECT_VACANCY_HOLD','reason','The vacancy is recorded. Obtain direction on the constitutional route before filling it.'); END IF;
 IF NOT EXISTS(SELECT FROM public.elect_pending_vacancy(p_lo,v.kind,v.place,p_cut) live WHERE live.ending=v.ending) THEN
    RETURN jsonb_build_object('code','ELECT_VACANCY_ROUTE','reason','Use the filling route and body named for this vacancy.'); END IF;
 IF i->>'route' NOT IN ('BY_ELECTION','BOARD_CQ','COOPTION') OR i->>'binding' IS DISTINCT FROM p_spec->>'binding'
   OR i->>'version' IS DISTINCT FROM p_spec->>'version' OR i->>'election' IS DISTINCT FROM p_spec->>'provision'
   OR i->>'authority' IS DISTINCT FROM p_rule->>'body' OR v.kind IS DISTINCT FROM p_rule->>'type'
   OR v.place IS DISTINCT FROM p_spec->>'place'
 THEN RETURN jsonb_build_object('code','ELECT_VACANCY_ROUTE','reason','Use the filling route and body named for this vacancy.'); END IF;
 IF i->>'authority'='GOVERNANCE' AND public.cq3_board(p_lo,p_cut,p_cut) IS NULL THEN
   RETURN jsonb_build_object('code','ELECT_VACANCY_BOARD','reason','The constitution requires an active GOVERNANCE Board for this vacancy.'); END IF;
 RETURN i;
END $$;
"""

SQL += r"""
CREATE TABLE public.core_electvacancyfill (
 ending uuid PRIMARY KEY REFERENCES public.core_electvacancy(ending), event_uuid uuid UNIQUE NOT NULL,
 result uuid REFERENCES public.core_cqact(act_uuid), decision_reference text NOT NULL);
CREATE FUNCTION public.elect_pending_vacancy(p_lo bigint,p_kind text,p_place text,p_cut timestamptz)
RETURNS public.core_electvacancy LANGUAGE sql STABLE AS $$
 SELECT v.* FROM public.core_electvacancy v WHERE v.organism_id=p_lo AND v.kind=p_kind AND v.place=p_place
 AND v.effective_at<=p_cut AND NOT EXISTS(SELECT FROM public.core_electvacancyfill f WHERE f.ending=v.ending)
 ORDER BY v.effective_at DESC,v.recorded_at DESC,v.ending LIMIT 1
$$;
CREATE FUNCTION public.elect_named_appointment(p_lo bigint,p_role bigint,p_actor bigint,p_cut timestamptz,p_known timestamptz)
RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT v.ending FROM public.core_organismroledefinition d
 CROSS JOIN LATERAL public.elect_pending_vacancy(p_lo,'OFFICE',d.code,p_cut) v
 JOIN public.core_livingorganism lo ON lo.id=v.organism_id
 WHERE d.id=p_role AND d.living_organism_id=p_lo AND d.kind='BOARD_OFFICE' AND d.scope='LIVING_ORGANISM'
 AND d.active AND NOT EXISTS(SELECT FROM public.core_organismroledefinition n WHERE n.supersedes_id=d.id
    AND n.effective_at<=p_cut AND n.recorded_at<=p_known)
 AND v.instruction->>'route'='APPOINTMENT' AND v.recorded_at<=p_known
 AND (d.appointing_body_reference=v.instruction->>'authority' OR
    (d.appointing_authority='LO_COORDINATOR_BODY' AND v.instruction->>'authority'='office:LO_COORDINATOR'))
 AND (public.cq_settings_at(p_lo,p_known)->>'enabled')::boolean
 AND EXISTS(SELECT FROM public.l_laws_at(lo.organism_id,p_cut,p_known) l WHERE l.binding::text=v.instruction->>'binding'
   AND l.version::text=v.instruction->>'version')
 AND EXISTS(SELECT FROM public.c2_seats(p_lo,p_cut,p_known) s JOIN public.core_organismmembership m ON m.id=s.membership_id
   WHERE m.identity_id=p_actor AND s.standing='QUALIFIED' AND s.role_scope='LIVING_ORGANISM'
   AND s.role_code=CASE WHEN v.instruction->>'authority'='office:LO_COORDINATOR' THEN 'COORDINATOR'
                      ELSE substr(v.instruction->>'authority',7) END)
$$;

CREATE FUNCTION public.elect_link_fill() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo bigint; place_ text; kind_ text; event_ uuid; receipt_ uuid; actor_ bigint; role_ bigint; decision_ text;
 v public.core_electvacancy; mandate jsonb; source_ uuid; at_ timestamptz:=NEW.effective_at;
BEGIN
 IF TG_TABLE_NAME='core_circleparticipation' THEN
   SELECT c.parent_organism_id INTO lo FROM public.core_circle c WHERE c.id=NEW.circle_id AND c.kind='GOVERNANCE';
   kind_:='SEAT'; place_:=NEW.seat_id; event_:=NEW.participation_uuid; actor_:=NEW.enterer_identity_id;
   SELECT d.authority_decision_reference INTO decision_ FROM public.core_circleadmissiondecision d WHERE d.id=NEW.decision_id;
 ELSE
   SELECT d.living_organism_id,d.code,d.id INTO lo,place_,role_ FROM public.core_contextualroleassignment a
   JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
   WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE' AND NOT starts_with(d.code,'INTEVIA_');
   kind_:='OFFICE'; event_:=NEW.event_uuid; actor_:=NEW.enterer_identity_id; decision_:=NEW.authority_decision_reference;
 END IF;
 IF lo IS NULL THEN RETURN NULL; END IF;
 -- An ending and its replacement can share one transaction; start the order before consuming it.
 SELECT s.ending INTO source_ FROM public.elect_vacancy_sources s WHERE s.organism_id=lo AND s.kind=kind_
   AND s.place=place_ AND s.effective_at<=at_ AND s.recorded_at<=statement_timestamp()
   ORDER BY s.effective_at DESC,s.recorded_at DESC LIMIT 1;
 IF source_ IS NULL THEN RETURN NULL; END IF;
 PERFORM public.elect_start_vacancy(lo,source_,actor_);
 v:=public.elect_pending_vacancy(lo,kind_,place_,at_);
 IF v.ending IS NULL THEN RETURN NULL; END IF;
 IF v.instruction->>'route'='HOLD_HG' THEN PERFORM public.cq_refuse('ELECT_VACANCY_HOLD'); END IF;
 IF v.instruction->>'route'='APPOINTMENT' THEN
   IF kind_='OFFICE' THEN
     IF public.elect_named_appointment(lo,role_,actor_,at_,statement_timestamp()) IS DISTINCT FROM v.ending
        OR NOT EXISTS(SELECT FROM public.core_authoritydecision a WHERE a.decision_reference=decision_ AND a.outcome='QUALIFIED'
           AND a.actor_id=actor_ AND a.request_reference='elect-vacancy:'||v.ending) THEN
        PERFORM public.cq_refuse('ELECT_VACANCY_APPOINTER'); END IF;
   ELSIF NOT EXISTS(SELECT FROM public.core_circleadmissiondecision d WHERE d.id=NEW.decision_id AND d.outcome='ACCEPT'
     AND d.constitutional_route='APPOINTMENT' AND d.deciding_party=v.instruction->>'authority'
     AND d.deciding_identity_id=actor_) THEN PERFORM public.cq_refuse('ELECT_VACANCY_APPOINTER'); END IF;
 ELSE
   SELECT (i.request->>'result')::uuid,i.facts->'domain_plan' INTO receipt_,mandate FROM public.core_cqact i
    WHERE i.act_uuid=event_ AND i.organism_id=lo AND i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id()
      AND i.authority_source=public.cq3_implement_source(i);
   IF receipt_ IS NULL OR mandate->>'vacancy' IS DISTINCT FROM v.ending::text OR mandate->>'place' IS DISTINCT FROM place_
      OR mandate->>'type' IS DISTINCT FROM kind_ OR v.instruction->>'route' NOT IN ('BY_ELECTION','BOARD_CQ','COOPTION')
     THEN PERFORM public.cq_refuse('ELECT_VACANCY_ROUTE'); END IF;
 END IF;
 INSERT INTO public.core_electvacancyfill VALUES (v.ending,event_,receipt_,decision_); RETURN NULL;
END $$;
CREATE FUNCTION public.elect_fill_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF pg_trigger_depth()<2 THEN PERFORM public.cq_refuse('ELECT_VACANCY_ROUTE'); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER elect_vacancy_fill_input BEFORE INSERT ON public.core_electvacancyfill FOR EACH ROW EXECUTE FUNCTION public.elect_fill_guard();
CREATE TRIGGER elect_vacancy_fill_immutable BEFORE UPDATE OR DELETE ON public.core_electvacancyfill FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_elect_fill_no_truncate BEFORE TRUNCATE ON public.core_electvacancyfill FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER elect_vacancy_filled_office AFTER INSERT ON public.core_contextualroleassignmentstateevent
 FOR EACH ROW WHEN (NEW.action='ASSIGN') EXECUTE FUNCTION public.elect_link_fill();
CREATE TRIGGER elect_vacancy_filled_seat AFTER INSERT ON public.core_circleparticipation
 FOR EACH ROW WHEN (NEW.decision_id IS NOT NULL) EXECUTE FUNCTION public.elect_link_fill();
"""

REVERSE = r"""
DROP TRIGGER elect_vacancy_filled_office ON public.core_contextualroleassignmentstateevent;
DROP TRIGGER elect_vacancy_filled_seat ON public.core_circleparticipation;
DROP TRIGGER elect_vacancy_from_office ON public.core_contextualroleassignmentstateevent;
DROP TRIGGER elect_vacancy_from_seat ON public.core_circleparticipationend;
DROP FUNCTION public.elect_link_fill(),public.elect_start_from_ending(),public.elect_named_appointment(bigint,bigint,bigint,timestamptz,timestamptz),
 public.elect_pending_vacancy(bigint,text,text,timestamptz),public.elect_call_route(bigint,jsonb,jsonb,timestamptz),
 public.elect_start_vacancy(bigint,uuid,bigint);
DROP FUNCTION public.elect_board_confirmation(jsonb);
DROP TABLE public.core_electvacancyfill, public.core_electvacancy;
DROP FUNCTION public.elect_vacancy_receipt(),public.elect_fill_guard(),public.elect_vacancy_guard(),public.elect_vacancy_instruction(public.elect_vacancy_sources);
DROP FUNCTION public.elect_already_replaced(public.elect_vacancy_sources,timestamptz);
DROP INDEX public.elect_vacancy_one_call; DROP VIEW public.elect_vacancy_sources;
"""

SQL += r"""
CREATE FUNCTION public.elect_admin_mandate(p_lo bigint,p_att jsonb,p_cut timestamptz) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object('scope',public.elect_run_scope(p_att->'spec'),'transfer',t.act_uuid,'result',t.decision,
      'holder',coalesce(t.transfer->>'to','CONSTITUTIONAL_BODY'))
 FROM (SELECT 1) anchor LEFT JOIN LATERAL public.elect_last_transfer(p_lo,public.elect_run_scope(p_att->'spec'),p_cut) t ON NOT public.elect_board_confirmation(p_att)
$$;
"""
