"""Replace the Circle ACTIVE exclusion atomically with G4 qualification on every event write."""
from importlib import import_module

from django.db import migrations, models
from django.db.models import Q

offices = import_module("core.migrations.0060_circle_coordinators")

OLD_STATES = ("DORMANT", "ELIGIBLE", "SUSPENDED", "CLOSED")
NEW_STATES = ("DORMANT", "ELIGIBLE", "ACTIVE", "SUSPENDED", "CLOSED")

SQL = """
-- This function runs before the event is visible, both for the command and for raw INSERT.
-- NULL means qualified. Named refusals let a caller with the relevant mandate explain the hold.
CREATE FUNCTION public.circle_activation_problem(c_id bigint, enterer bigint, request_id uuid,
     decision text, evidence text, effective timestamptz, recorded timestamptz, executed timestamptz)
RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE c public.core_circle; opening public.core_circlestateevent; cut timestamptz; as_known timestamptz;
        found_source uuid; act text; current_state text;
BEGIN
 IF effective IS NULL OR recorded IS NULL OR executed IS NULL OR effective>recorded OR recorded>executed THEN
    RETURN 'CIRCLE_G4_TIME';
 END IF;
 SELECT * INTO STRICT c FROM public.core_circle WHERE id=c_id;
 SELECT * INTO opening FROM public.core_circlestateevent WHERE circle_id=c_id AND sequence=1;
 IF opening.id IS NULL OR opening.action<>'CREATE_DORMANT' OR opening.resulting_state<>'DORMANT'
    OR opening.actor_state<>'LIVING_ORGANISM' OR opening.entry_mode<>'TRANSPORT'
    OR opening.authority_decision_reference IS DISTINCT FROM c.founding_reference
    OR opening.effective_at>effective OR opening.received_at>recorded OR opening.recorded_at>recorded THEN
    RETURN 'CIRCLE_G4_FORMATION';
 END IF;
 -- Rulings 55 and 85. A local mandate cannot substitute for the membership decision.
 -- The snapshot can record the Birthing choice. C0 also requires working voting for live GOVERNANCE.
 IF c.kind='GOVERNANCE' THEN
    IF EXISTS (SELECT FROM public.core_livingorganismevent birth
       JOIN public.core_lobirthingsnapshot snap ON snap.living_organism_id=c.parent_organism_id
          AND birth.evidence_reference='birthing-snapshot:'||snap.snapshot_uuid
       JOIN public.core_logovernanceconfigurationversion v ON v.living_organism_id=c.parent_organism_id
          AND snap.body->'settings'->>'governance_configuration'='governance-configuration-version:'||v.version_uuid
       WHERE birth.living_organism_id=c.parent_organism_id AND birth.action='BIRTHING'
          AND birth.effective_at<=effective AND birth.received_at<=recorded AND birth.recorded_at<=recorded
          AND snap.snapshot_commitment IS NOT NULL AND snap.body->>'governance_constitution' IS NOT NULL
          AND snap.effective_at=birth.effective_at AND snap.recorded_at<=birth.recorded_at AND v.governance_enabled
          AND NOT EXISTS (SELECT FROM public.core_livingorganismevent config_effect
             JOIN public.core_logovernanceconfigurationversion later ON later.living_organism_id=c.parent_organism_id
               AND config_effect.evidence_reference='governance-configuration-version:'||later.version_uuid
             WHERE config_effect.living_organism_id=c.parent_organism_id AND config_effect.action='EFFECT_GOVERNANCE_CONFIGURATION'
               AND config_effect.sequence>birth.sequence AND config_effect.recorded_at<=executed AND config_effect.effective_at<=executed
               AND later.governance_enabled IS DISTINCT FROM true)) THEN
       RETURN 'CIRCLE_G4_OPERATIVE_VOTING';
    END IF;
    RETURN 'CIRCLE_G4_GOVERNANCE_AUTHORITY';
 END IF;
 IF c.kind IN ('APPLICATION','FINANCE','OPERATIONS') THEN RETURN 'CIRCLE_G4_DESIGNATION_AUTHORITY'; END IF;
 IF c.kind<>'GENERIC' OR NOT starts_with(opening.evidence_reference,'circle-purpose:')
    OR btrim(substr(opening.evidence_reference,16))='' THEN RETURN 'CIRCLE_G4_FORMATION'; END IF;
 act:='FORM:GENERIC:'||encode(sha256(convert_to(substr(opening.evidence_reference,16),'UTF8')),'hex');
 IF opening.authority_decision_reference IS DISTINCT FROM public.circle_office_decision(
    public.circle_lo_office(c.parent_organism_id,opening.enterer_identity_id,opening.effective_at,opening.recorded_at),
    c.circle_uuid,act,opening.enterer_identity_id,opening.enterer_identity_id) THEN RETURN 'CIRCLE_G4_FORMATION'; END IF;
 -- An already scheduled event cannot silently reverse the first activation at its future cut.
 IF EXISTS(SELECT FROM public.core_circlestateevent WHERE circle_id=c_id AND effective_at>effective) THEN
    RETURN 'CIRCLE_G4_LATER_EVENT';
 END IF;
 act:='ACTIVATE:'||opening.event_uuid||':'||request_id;
 -- Record time remains statement_timestamp(). A direct writer may have waited for the LO lock.
 -- Test execution with a fresh clock read made by the trigger after that lock, also as its knowledge cut.
 FOR cut,as_known IN SELECT * FROM (VALUES (effective,recorded),(executed,executed)) AS cuts(t,k) LOOP
    current_state:=public.s015_fold_effective_state('core_circle',c_id,cut,as_known);
    IF current_state IS NULL OR current_state NOT IN ('DORMANT','ELIGIBLE') THEN RETURN 'CIRCLE_G4_STATE'; END IF;
    found_source:=public.circle_lo_office(c.parent_organism_id,enterer,cut,as_known);
    IF enterer IS DISTINCT FROM opening.enterer_identity_id OR found_source IS NULL OR decision IS NULL
       OR decision IS DISTINCT FROM public.circle_office_decision(found_source,c.circle_uuid,act,enterer,enterer)
       OR evidence IS DISTINCT FROM ('formation:'||opening.event_uuid||'|office:'||found_source)
       THEN RETURN 'CIRCLE_G4_AUTHORITY'; END IF;
    IF NOT EXISTS (SELECT FROM public.core_circleparticipation p WHERE p.circle_id=c_id
       AND public.circle_place_at(c_id,p.membership_id,cut,as_known)) THEN RETURN 'CIRCLE_G4_PLACE'; END IF;
    IF NOT EXISTS (SELECT FROM public.circle_holders(c_id,cut,as_known)) THEN RETURN 'CIRCLE_G4_COVERAGE'; END IF;
 END LOOP;
 RETURN NULL;
END $$;

-- The closing cause is the last effective participation ending after every
-- recorded place has an ending. This does not rely on the Circle already being CLOSED.
CREATE FUNCTION public.circle_last_ending(c bigint, known timestamptz)
RETURNS TABLE(place uuid,cause uuid,at_time timestamptz,occurred timestamptz,received timestamptz,
              enterer bigint,basis bigint) LANGUAGE sql STABLE STRICT AS $$
 SELECT p.participation_uuid,e.composed_from_event_uuid,e.effective_at,e.occurred_at,e.received_at,
        e.enterer_identity_id,lo.founding_authority_basis_id
 FROM public.core_circle x JOIN public.core_livingorganism lo ON lo.id=x.parent_organism_id
 JOIN public.core_circleparticipation p ON p.circle_id=x.id
 JOIN public.core_circleparticipationend e ON e.participation_id=p.id
 WHERE x.id=c AND x.kind='GENERIC' AND NOT lo.platform_root AND e.recorded_at<=known AND e.effective_at<=known
   AND NOT EXISTS (SELECT FROM public.core_circleparticipation other WHERE other.circle_id=c AND NOT EXISTS (
       SELECT FROM public.core_circleparticipationend ended WHERE ended.participation_id=other.id
         AND ended.recorded_at<=known AND ended.received_at<=known AND ended.effective_at<=known))
 ORDER BY e.effective_at DESC,e.occurred_at DESC,e.received_at DESC,e.recorded_at DESC,e.id DESC LIMIT 1
$$;

CREATE FUNCTION public.circle_close_on_final_exit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE x public.core_circle; last_exit record; head public.core_circlestateevent;
        event_id uuid:=gen_random_uuid(); key text; ref text; fp text; now_cut timestamptz;
BEGIN
 SELECT c.* INTO STRICT x FROM public.core_circle c JOIN public.core_circleparticipation p ON p.circle_id=c.id
    WHERE p.id=NEW.participation_id;
 IF x.kind<>'GENERIC' THEN RETURN NULL; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=x.parent_organism_id)]);
 now_cut:=clock_timestamp();
 SELECT * INTO last_exit FROM public.circle_last_ending(x.id,now_cut);
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO STRICT head FROM public.core_circlestateevent WHERE circle_id=x.id ORDER BY sequence DESC LIMIT 1;
 IF head.resulting_state='CLOSED' AND head.action='DISSOLVE_CIRCLE' THEN RETURN NULL; END IF;
 IF head.resulting_state<>'ACTIVE' OR (head.effective_at,head.occurred_at,head.received_at)>
       (last_exit.at_time,last_exit.occurred,last_exit.received) THEN
    RAISE EXCEPTION 'CIRCLE_DISSOLUTION_HISTORY' USING ERRCODE='23514';
 END IF;
 ref:='last-place:'||last_exit.place||'|cause:'||last_exit.cause;
 key:='circle-close:'||last_exit.place;
 fp:=public.c2_target(ref);
 PERFORM public.s015_append_event('core_circle',x.id,'DISSOLVE_CIRCLE','CLOSED',last_exit.at_time,last_exit.occurred,
   last_exit.received,last_exit.enterer,last_exit.basis,'s015d1:'||fp,ref,key,fp,'{}',NULL,event_id,
   head.id,head.sequence,NULL,'CIRCLES:L5:LAST_MEMBER',key);
 PERFORM public.s015_refresh_governed_cache('core_circle',x.id,last_exit.at_time,clock_timestamp());
 RETURN NULL;
END $$;
CREATE TRIGGER circle_close_on_final_exit AFTER INSERT ON public.core_circleparticipationend
 FOR EACH ROW EXECUTE FUNCTION public.circle_close_on_final_exit();

CREATE FUNCTION public.circle_guard_active_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE problem text; previous public.core_circlestateevent; owner_basis bigint; last_exit record; executed timestamptz;
BEGIN
 IF (NEW.action='DISSOLVE_CIRCLE' OR NEW.prior_state='CLOSED' OR NEW.resulting_state='CLOSED')
    AND EXISTS(SELECT FROM public.core_circle c WHERE c.id=NEW.circle_id AND c.kind='GENERIC'
       AND EXISTS(SELECT FROM public.core_circlestateevent born WHERE born.circle_id=c.id AND born.action='ACTIVATE')) THEN
    SELECT * INTO last_exit FROM public.circle_last_ending(NEW.circle_id,clock_timestamp());
    IF NOT FOUND OR NEW.action<>'DISSOLVE_CIRCLE' OR NEW.resulting_state<>'CLOSED' OR NEW.prior_state IS DISTINCT FROM 'ACTIVE'
       OR NEW.actor_state<>'NONE' OR NEW.entry_mode<>'COMPOSED' OR NEW.composing_rule_reference IS DISTINCT FROM 'CIRCLES:L5:LAST_MEMBER'
       OR NEW.composer_identity_id IS DISTINCT FROM last_exit.enterer OR NEW.enterer_identity_id IS DISTINCT FROM last_exit.enterer
       OR (NEW.effective_at,NEW.occurred_at,NEW.received_at) IS DISTINCT FROM (last_exit.at_time,last_exit.occurred,last_exit.received)
       OR NEW.authority_basis_id IS DISTINCT FROM last_exit.basis OR NEW.effective_until_state<>'NONE'
       OR NEW.evidence_reference IS DISTINCT FROM 'last-place:'||last_exit.place||'|cause:'||last_exit.cause
       OR NEW.authority_decision_reference IS DISTINCT FROM 's015d1:'||public.c2_target(NEW.evidence_reference)
       OR NOT EXISTS(SELECT FROM public.core_circlestateevent h WHERE h.id=NEW.predecessor_id AND h.circle_id=NEW.circle_id
            AND h.resulting_state='ACTIVE' AND h.effective_at<=NEW.effective_at) THEN
       RAISE EXCEPTION 'CIRCLE_DISSOLUTION_SOURCE' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
 ELSIF NEW.action='DISSOLVE_CIRCLE' THEN
    RAISE EXCEPTION 'CIRCLE_DISSOLUTION_SCOPE' USING ERRCODE='23514';
 END IF;
 IF NEW.action='ACTIVATE' THEN
    IF NEW.resulting_state<>'ACTIVE' OR NEW.prior_state IS NULL OR NEW.prior_state NOT IN ('DORMANT','ELIGIBLE')
       OR NEW.sequence<=1 OR NEW.actor_state<>'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity<>'STANDING' OR NEW.entry_mode<>'TRANSPORT'
       OR NEW.effective_until_state<>'NONE' OR NEW.occurred_at<>NEW.effective_at
       OR NEW.received_at<>NEW.effective_at OR NEW.recorded_at IS NULL OR NEW.effective_at>NEW.recorded_at
       OR NOT EXISTS (SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
                      WHERE i.id=NEW.enterer_identity_id AND i.access_state='active' AND u.is_active)
       THEN RAISE EXCEPTION 'CIRCLE_G4_ENVELOPE' USING ERRCODE='23514'; END IF;
    SELECT lo.founding_authority_basis_id INTO STRICT owner_basis FROM public.core_circle c
      JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id WHERE c.id=NEW.circle_id;
    IF NEW.authority_basis_id IS DISTINCT FROM owner_basis THEN
       RAISE EXCEPTION 'CIRCLE_G4_AUTHORITY' USING ERRCODE='23514'; END IF;
    problem:=public.circle_activation_problem(NEW.circle_id,NEW.enterer_identity_id,NEW.event_uuid,
       NEW.authority_decision_reference,NEW.evidence_reference,NEW.effective_at,NEW.recorded_at,clock_timestamp());
    IF problem IS NOT NULL THEN RAISE EXCEPTION '%',problem USING ERRCODE='23514'; END IF;
 ELSIF NEW.prior_state='ACTIVE' OR NEW.resulting_state='ACTIVE' THEN
    SELECT * INTO previous FROM public.core_circlestateevent WHERE id=NEW.predecessor_id AND circle_id=NEW.circle_id;
    IF NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
       OR previous.resulting_state IS DISTINCT FROM 'ACTIVE'
       OR NEW.action NOT IN ('OPEN_WORK','LEAVE_CIRCLE','CLAIM_WORK') THEN
       RAISE EXCEPTION 'CIRCLE_G4_ACTIVE_EDGE' USING ERRCODE='23514'; END IF;
    IF NEW.action='CLAIM_WORK' AND EXISTS(SELECT FROM public.core_circle c WHERE c.id=NEW.circle_id AND c.kind='GENERIC') THEN
       executed:=clock_timestamp();
       IF NOT EXISTS(SELECT FROM public.core_circle c JOIN public.core_circlestateevent task ON task.circle_id=c.id
          WHERE c.id=NEW.circle_id AND task.action='OPEN_WORK' AND 'work:'||task.event_uuid=NEW.evidence_reference
          AND EXISTS(SELECT FROM public.circle_holders(c.id,executed,executed) holder WHERE holder.person=NEW.actor_id)
          AND public.circle_source(c.parent_organism_id,c.circle_uuid,'CIRCLE_SERVICE:'||(task.evidence_reference::jsonb->>1),
                                  NEW.actor_id,NEW.actor_id,executed,executed) IS NOT NULL) THEN
          RAISE EXCEPTION 'CIRCLE_CLAIM_EXECUTION_QUALIFICATION' USING ERRCODE='23514';
       END IF;
    END IF;
    -- Same-state work and departure require their existing specific 0059 sources.
 END IF;
 RETURN NEW;
END $$;
-- Name sorts after the 0021 registered-LO lock and database record-time triggers.
CREATE TRIGGER zz_circle_qualified_activation BEFORE INSERT ON public.core_circlestateevent
 FOR EACH ROW WHEN (NEW.action IN ('ACTIVATE','DISSOLVE_CIRCLE') OR NEW.resulting_state IN ('ACTIVE','CLOSED')
                    OR NEW.prior_state IN ('ACTIVE','CLOSED'))
 EXECUTE FUNCTION public.circle_guard_active_event();

CREATE FUNCTION public.circle_refuse_active_seed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'CIRCLE_G4_ACTIVE_SEED' USING ERRCODE='23514';
END $$;
CREATE TRIGGER circle_refuse_active_seed BEFORE INSERT ON public.core_circle
 FOR EACH ROW WHEN (NEW.state='ACTIVE') EXECUTE FUNCTION public.circle_refuse_active_seed();

CREATE FUNCTION public.circle_guard_other_office() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS (SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d
      ON d.id=a.role_definition_id WHERE a.id=NEW.assignment_id AND a.circle_id IS NOT NULL
      AND d.scope='CIRCLE' AND d.code<>'COORDINATOR') THEN
    RAISE EXCEPTION 'CIRCLE_G4_OFFICE_SOURCE' USING ERRCODE='23514';
 END IF;
 -- 0060 qualifies each Circle Coordinator appointment; 0021 owns structural mismatches.
 RETURN NEW;
END $$;
CREATE TRIGGER circle_guard_other_office BEFORE INSERT ON public.core_contextualroleassignmentstateevent
 FOR EACH ROW WHEN (NEW.resulting_state='ACTIVE') EXECUTE FUNCTION public.circle_guard_other_office();
"""


def writer_body():
    body = offices.writer_body()
    old_actions = "action_name NOT IN ('CREATE_DORMANT','LEAVE_CIRCLE','OPEN_WORK','CLAIM_WORK')"
    inject = " SELECT string_agg"
    if body.count(old_actions) != 1 or body.count(inject) != 1:
        raise RuntimeError("The 0060 shared writer shape differs.")
    body = body.replace(old_actions, old_actions[:-1] + ",'ACTIVATE','DISSOLVE_CIRCLE')")
    return body.replace(inject, """
 IF token='core_circle' AND action_name='ACTIVATE' THEN
    IF command_rule IS NOT NULL OR access_epoch IS NOT NULL OR deadline IS NOT NULL THEN
       RAISE EXCEPTION 'Invalid institutional activation envelope'; END IF;
    values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;
""" + inject)


def event_vocabulary(editor, states):
    # The names are the installed 0021 catalogue names. Both prior and resulting states are checked.
    tokens = ", ".join("'" + state + "'" for state in states)
    for column, suffix in (("resulting_state", "resulting_state"), ("prior_state", "prior")):
        name = f"s015_0021_circleevent_{suffix}_vocab_ck"
        nullable = "prior_state IS NULL OR " if column == "prior_state" else ""
        editor.execute(f"ALTER TABLE public.core_circlestateevent DROP CONSTRAINT {name}, "
                       f"ADD CONSTRAINT {name} CHECK ({nullable}{column} IN ({tokens}))", params=None)


def install(apps, editor):
    editor.execute("""LOCK TABLE public.core_circle, public.core_circlestateevent,
        public.core_contextualroleassignmentstateevent IN ACCESS EXCLUSIVE MODE""", params=None)
    # Earlier guards permitted raw Circle offices of other codes. Keep their records for reconciliation.
    with editor.connection.cursor() as cur:
        cur.execute("""SELECT EXISTS (SELECT FROM public.core_contextualroleassignment a
            JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
            WHERE a.circle_id IS NOT NULL AND d.scope='CIRCLE' AND d.code<>'COORDINATOR'
            AND (public.s015_fold_effective_state('core_contextualroleassignment',a.id,statement_timestamp(),
                 statement_timestamp())='ACTIVE' OR EXISTS (
                 SELECT FROM public.core_contextualroleassignmentstateevent e WHERE e.assignment_id=a.id
                 AND e.resulting_state='ACTIVE' AND e.effective_at>statement_timestamp())))""")
        if cur.fetchone()[0]:
            raise RuntimeError("A live or scheduled non-Coordinator Circle office needs reconciliation before G4.")
    offices.switch_writer(editor, offices.writer_body(), writer_body())
    editor.execute(SQL, params=None)
    event_vocabulary(editor, NEW_STATES)
    editor.execute("ALTER TABLE public.core_circlestateevent DROP CONSTRAINT s015_0021_circlestateevent_no_active_ck")


def reverse(apps, editor):
    editor.execute("LOCK TABLE public.core_circle, public.core_circlestateevent IN ACCESS EXCLUSIVE MODE", params=None)
    with editor.connection.cursor() as cur:
        cur.execute("""SELECT EXISTS (SELECT FROM public.core_circlestateevent
                       WHERE resulting_state='ACTIVE' OR prior_state='ACTIVE')
                       OR EXISTS (SELECT FROM public.core_circle WHERE state='ACTIVE')""")
        if cur.fetchone()[0]:
            raise RuntimeError("A Circle activation is recorded. Keep G4 installed.")
    offices.switch_writer(editor, writer_body(), offices.writer_body())
    event_vocabulary(editor, OLD_STATES)
    editor.execute("""
      ALTER TABLE public.core_circlestateevent ADD CONSTRAINT s015_0021_circlestateevent_no_active_ck
        CHECK (resulting_state <> 'ACTIVE' AND (prior_state IS NULL OR prior_state <> 'ACTIVE'));
      DROP TRIGGER circle_close_on_final_exit ON public.core_circleparticipationend;
      DROP FUNCTION public.circle_close_on_final_exit();
      DROP TRIGGER circle_guard_other_office ON public.core_contextualroleassignmentstateevent;
      DROP FUNCTION public.circle_guard_other_office();
      DROP TRIGGER circle_refuse_active_seed ON public.core_circle;
      DROP FUNCTION public.circle_refuse_active_seed();
      DROP TRIGGER zz_circle_qualified_activation ON public.core_circlestateevent;
      DROP FUNCTION public.circle_guard_active_event();
      DROP FUNCTION public.circle_last_ending(bigint,timestamptz);
      DROP FUNCTION public.circle_activation_problem(bigint,bigint,uuid,text,text,timestamptz,timestamptz,timestamptz);
    """, params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0060_circle_coordinators")]
    operations = [
        migrations.RemoveConstraint("circle", "s015_0021_circle_state_vocab_ck"),
        migrations.AddConstraint("circle", models.CheckConstraint(condition=Q(state__in=list(NEW_STATES)),
                                                               name="s015_0021_circle_state_vocab_ck")),
        migrations.RunPython(install, reverse),
    ]
