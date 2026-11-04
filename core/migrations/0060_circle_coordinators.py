"""Scope the two COORDINATOR keys. Bind the Circle office chain to its nomination, consent and sources."""
from importlib import import_module

from django.db import migrations, models
from django.db.models import Q

previous = import_module("core.migrations.0059_operating_circles")
creation = previous.creation

SQL = """
CREATE FUNCTION public.circle_office_place(c bigint, m bigint, ref text, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT public.circle_place_at(c,m,at_time,known) AND EXISTS (
   SELECT FROM public.core_circleparticipation p WHERE p.circle_id=c AND p.membership_id=m
   AND ref='place:'||p.participation_uuid AND p.effective_at<=at_time AND p.recorded_at<=known AND p.received_at<=known
   AND NOT EXISTS (SELECT FROM public.core_circleparticipationend e WHERE e.participation_id=p.id
       AND e.effective_at<=at_time AND e.recorded_at<=known AND e.received_at<=known))
$$;

-- Founder self-seating is available only inside the incomplete birth. A later
-- nomination or appointment derives authority from the acting Circle office.
CREATE FUNCTION public.circle_appointing_office(c bigint, actor bigint, subject bigint, nominee_office uuid, at_time timestamptz, known timestamptz)
RETURNS uuid LANGUAGE sql STABLE STRICT AS $$
 SELECT CASE WHEN actor=subject AND EXISTS(
    SELECT FROM public.core_circleparticipation p JOIN public.core_circlestateevent e ON e.id=p.birth_event_id
    WHERE p.circle_id=c AND e.enterer_identity_id=actor AND e.circle_id=c
    AND public.circle_place_at(c,p.membership_id,at_time,known)
    AND e.authority_decision_reference=public.circle_office_decision(
        public.circle_lo_office(x.parent_organism_id,actor,at_time,known),x.circle_uuid,
        'FORM:GENERIC:'||encode(sha256(convert_to(substr(e.evidence_reference,16),'UTF8')),'hex'),actor,actor)
    AND public.s015_fold_effective_state('core_circle',c,at_time,known)='DORMANT')
 THEN public.circle_lo_office(x.parent_organism_id,actor,at_time,known)
 WHEN public.circle_vacant(c,nominee_office,at_time,known) THEN
    coalesce(public.circle_lo_office(x.parent_organism_id,actor,at_time,known),
      (SELECT p.participation_uuid FROM public.core_circleparticipation p JOIN public.core_organismmembership m
        ON m.id=p.membership_id AND m.identity_id=actor
       WHERE p.circle_id=c AND actor=subject AND public.circle_direct_volunteer(x.parent_organism_id,at_time,known)
         AND public.circle_office_place(c,m.id,'place:'||p.participation_uuid,at_time,known)))
 ELSE public.circle_current_office(c,actor,at_time,known,nominee_office) END
 FROM public.core_circle x WHERE x.id=c AND x.kind='GENERIC'
$$;

CREATE FUNCTION public.circle_appointment_marker(c bigint, incoming uuid, actor bigint, subject bigint,
    source uuid, place_ref text, at_time timestamptz, known timestamptz)
RETURNS text LANGUAGE sql STABLE STRICT AS $$
 SELECT CASE WHEN NOT public.circle_vacant(c,incoming,at_time,known) THEN ''
   WHEN source=public.circle_lo_office(x.parent_organism_id,actor,at_time,known) THEN '|CB1'
   WHEN actor=subject AND place_ref='place:'||source AND public.circle_direct_volunteer(x.parent_organism_id,at_time,known)
      THEN '|RC1' END
 FROM public.core_circle x WHERE x.id=c AND x.kind='GENERIC'
$$;

-- First lawful nomination materialises a shared mechanical definition. It confers no seat.
CREATE FUNCTION public.circle_coordinator_definition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
    IF OLD.code='COORDINATOR' AND OLD.scope='CIRCLE' THEN
       RAISE EXCEPTION 'CIRCLE_COORDINATOR_DEFINITION_FIXED' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
 END IF;
 IF NEW.code<>'COORDINATOR' OR NEW.scope<>'CIRCLE' THEN RETURN NEW; END IF;
 IF NEW.kind IS DISTINCT FROM 'COORDINATOR' OR NEW.definition_version<>1 OR NOT NEW.active
    OR NOT EXISTS (SELECT FROM public.core_organismroledefinition lo_role
       WHERE lo_role.living_organism_id=NEW.living_organism_id AND lo_role.code='COORDINATOR'
       AND lo_role.scope='LIVING_ORGANISM' AND lo_role.active)
    OR NEW.supersedes_id IS NOT NULL OR NEW.participation_required IS DISTINCT FROM true
    OR NEW.recorded_at IS NOT NULL OR NEW.effective_at IS NULL
    OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NEW.received_at IS DISTINCT FROM NEW.effective_at
    OR NEW.received_at>statement_timestamp()
    OR NEW.authority_decision_reference IS NULL OR NOT EXISTS (
       SELECT FROM public.core_identity i WHERE i.id=NEW.enterer_id AND i.access_state='active'
       AND i.access_epoch=NEW.enterer_access_epoch)
    OR num_nonnulls(NEW.appointing_authority,NEW.appointing_body_reference,NEW.alternative_route_reference,
       NEW.nomination_basis,NEW.nomination_policy,NEW.nomination_dependency,NEW.ex_officio_seat_reference,
       NEW.retired_by_decision_reference,NEW.retired_effective_at,NEW.retired_received_at,
       NEW.retired_recorded_at,NEW.retirement_reason)<>0 THEN
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_DEFINITION_SHAPE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=statement_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER circle_coordinator_definition BEFORE INSERT OR UPDATE ON public.core_organismroledefinition
 FOR EACH ROW EXECUTE FUNCTION public.circle_coordinator_definition();

CREATE FUNCTION public.circle_definition_nomination() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_contextualroleassignment a
     JOIN public.core_contextualroleassignmentstateevent p ON p.assignment_id=a.id
     WHERE a.role_definition_id=NEW.id AND p.sequence=1 AND p.action='PROPOSE'
     AND p.authority_decision_reference=NEW.authority_decision_reference AND p.actor_id=NEW.enterer_id
     AND p.actor_access_epoch=NEW.enterer_access_epoch AND p.effective_at=NEW.effective_at) THEN
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_DEFINITION_SOURCE' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER circle_definition_nomination AFTER INSERT ON public.core_organismroledefinition
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.code='COORDINATOR' AND NEW.scope='CIRCLE')
 EXECUTE FUNCTION public.circle_definition_nomination();

CREATE FUNCTION public.circle_coordinator_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE office record; proposal public.core_contextualroleassignmentstateevent; cut timestamptz;
        act text; place_ref text; decision text; consent uuid; standing uuid; known timestamptz; approving_office uuid; expected_ref text; marker text;
BEGIN
 SELECT a.id,a.assignment_uuid,a.membership_id,a.circle_id,c.circle_uuid,c.parent_organism_id AS lo_id,
        c.kind,d.active,m.identity_id,lo.platform_root,lo.founding_authority_basis_id AS basis
 INTO office FROM public.core_contextualroleassignment a
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id AND d.code='COORDINATOR' AND d.scope='CIRCLE'
 JOIN public.core_circle c ON c.id=a.circle_id JOIN public.core_organismmembership m ON m.id=a.membership_id
 JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id WHERE a.id=NEW.assignment_id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF office.kind='GENERIC' THEN
    PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=office.lo_id)]);
 END IF;
 IF NEW.action='END' AND NEW.composing_rule_reference IN ('CIRCLES:G2:U3','CIRCLES:G2:SELF_LEAVE') THEN
    RETURN NULL; -- 0059 checks the causal event and atomic endings at commit.
 END IF;
 IF NOT office.active OR NEW.authority_basis_id IS DISTINCT FROM office.basis
    OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NEW.received_at IS DISTINCT FROM NEW.effective_at
    OR NEW.effective_at>NEW.recorded_at OR NEW.entry_mode<>'TRANSPORT' OR NEW.actor_capacity<>'STANDING'
    OR NOT EXISTS(SELECT FROM public.core_identity WHERE id=NEW.enterer_identity_id AND access_state='active') THEN
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_CONTEXT' USING ERRCODE='23514';
 END IF;
 IF NEW.action='PROPOSE' AND NEW.sequence=1 AND NEW.resulting_state='PROPOSED' AND NEW.prior_state IS NULL
    AND NEW.actor_state='PARTY' AND NEW.actor_id=NEW.enterer_identity_id AND NEW.effective_until_state='NOT_ESTABLISHED'
    AND NEW.actor_access_epoch=(SELECT access_epoch FROM public.core_identity WHERE id=NEW.enterer_identity_id) THEN
    place_ref:=NEW.evidence_reference;
    act:='NOMINATE:'||office.assignment_uuid||':'||place_ref;
 ELSIF NEW.action='ASSIGN' AND NEW.sequence=2 AND NEW.prior_state='PROPOSED' AND NEW.resulting_state='ACTIVE'
    AND NEW.actor_state='LIVING_ORGANISM' AND NEW.effective_until_state IN ('FIXED','NONE')
    AND (NEW.effective_until IS NULL OR NEW.effective_until>NEW.recorded_at) THEN
    IF office.platform_root THEN
       RAISE EXCEPTION 'CIRCLE_ROOT_APPOINTMENT_SOURCE_REQUIRED' USING ERRCODE='23514';
    END IF;
    SELECT * INTO STRICT proposal FROM public.core_contextualroleassignmentstateevent
      WHERE id=NEW.predecessor_id AND assignment_id=office.id AND action='PROPOSE' AND sequence=1;
    place_ref:=proposal.evidence_reference;
    act:='APPOINT:'||office.assignment_uuid||':'||coalesce(public.s015_canonical_ts(NEW.effective_until),'NONE');
 ELSE
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_EDGE' USING ERRCODE='23514';
 END IF;
 FOR cut,known IN SELECT NEW.effective_at,NEW.recorded_at
                  UNION ALL SELECT t,t FROM (SELECT CASE WHEN office.kind='GENERIC' THEN clock_timestamp()
                                                    ELSE NEW.recorded_at END AS t) execution LOOP
    IF public.circle_office_place(office.circle_id,office.membership_id,place_ref,cut,known) IS NOT TRUE
       OR NOT EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=office.lo_id
          AND m.identity_id=NEW.enterer_identity_id
          AND public.s015_fold_effective_state('core_organismmembership',m.id,cut,known)='ACTIVE') THEN
       RAISE EXCEPTION 'CIRCLE_COORDINATOR_PLACE' USING ERRCODE='23514';
    END IF;
    IF office.kind='GENERIC' THEN
       IF NEW.action='ASSIGN' AND (NEW.effective_until_state<>'NONE' OR NEW.effective_until IS NOT NULL) THEN
          RAISE EXCEPTION 'CIRCLE_COORDINATOR_NO_TERM' USING ERRCODE='23514';
       END IF;
       approving_office:=public.circle_appointing_office(
          office.circle_id,NEW.enterer_identity_id,office.identity_id,office.assignment_uuid,cut,known);
       decision:=public.circle_office_decision(approving_office,
          office.circle_uuid,act,NEW.enterer_identity_id,office.identity_id);
    ELSE
       decision:=public.circle_decision(office.lo_id,public.circle_source(office.lo_id,office.circle_uuid,
       act,NEW.enterer_identity_id,office.identity_id,cut,known),cut,known);
    END IF;
    IF decision IS NULL OR NEW.authority_decision_reference IS DISTINCT FROM decision THEN
       RAISE EXCEPTION 'CIRCLE_COORDINATOR_MANDATE' USING ERRCODE='23514';
    END IF;
    IF NEW.action='ASSIGN' THEN
       consent:=public.circle_office_consent(office.id,cut,known);
       IF office.kind='GENERIC' THEN
          -- A later approver must already hold the office before this ASSIGN statement.
          -- Otherwise two direct writes in one statement could try to qualify one another.
          marker:=public.circle_appointment_marker(office.circle_id,office.assignment_uuid,NEW.enterer_identity_id,
                       office.identity_id,approving_office,place_ref,cut,known);
          IF marker='|RC1' AND (proposal.actor_id IS DISTINCT FROM office.identity_id
             OR NEW.enterer_identity_id IS DISTINCT FROM office.identity_id) THEN
             RAISE EXCEPTION 'CIRCLE_VOLUNTEER_SELF_REQUIRED' USING ERRCODE='23514';
          END IF;
          IF marker IS DISTINCT FROM '|RC1'
             AND NOT EXISTS(SELECT FROM public.core_contextualroleassignment issuer
               JOIN public.core_contextualroleassignmentstateevent seated ON seated.assignment_id=issuer.id AND seated.action='ASSIGN'
               WHERE issuer.assignment_uuid=approving_office AND seated.recorded_at<NEW.recorded_at) THEN
             RAISE EXCEPTION 'CIRCLE_APPROVER_PRIOR_OFFICE' USING ERRCODE='23514';
          END IF;
          IF NOT EXISTS(SELECT FROM public.core_livingorganism lo JOIN public.core_authoritybasis b ON b.id=office.basis
               WHERE lo.id=office.lo_id AND public.c2_office_standing(lo.organism_id,office.assignment_uuid,
                     office.identity_id,b.basis_uuid,cut,known)='QUALIFIED') THEN
             RAISE EXCEPTION 'CIRCLE_COORDINATOR_CONSENT_OR_STANDING' USING ERRCODE='23514';
          END IF;
          expected_ref:=public.circle_standing_text(approving_office,place_ref,consent,
               public.c1b_period_at(office.membership_id,cut,known),NEW.event_uuid)
                || marker;
       ELSE
          standing:=public.circle_source(office.lo_id,office.assignment_uuid,'COUNT_OFFICE',
               office.identity_id,office.identity_id,cut,known);
          expected_ref:='consent:'||consent||'|standing:'||standing;
       END IF;
       IF expected_ref IS NULL OR NEW.evidence_reference IS DISTINCT FROM expected_ref THEN
          RAISE EXCEPTION 'CIRCLE_COORDINATOR_CONSENT_OR_STANDING' USING ERRCODE='23514';
       END IF;
    END IF;
 END LOOP;
 RETURN NULL;
END $$;
CREATE TRIGGER circle_coordinator_event AFTER INSERT ON public.core_contextualroleassignmentstateevent
 FOR EACH ROW EXECUTE FUNCTION public.circle_coordinator_event();
"""


def writer_body():
    old = previous.writer_body()
    marker = "IF token='core_contextualroleassignment' AND ("
    envelope = """IF token='core_contextualroleassignment' AND action_name IN ('PROPOSE','ASSIGN') THEN
    IF command_rule IS NOT NULL OR NOT EXISTS (
       SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d
       ON d.id=a.role_definition_id WHERE a.id=anchor AND a.circle_id IS NOT NULL
       AND d.scope='CIRCLE' AND d.code='COORDINATOR') THEN
       RAISE EXCEPTION 'Invalid Circle Coordinator envelope';
    END IF;
 ELSIF token='core_contextualroleassignment' AND ("""
    add = """ IF token='core_contextualroleassignment' THEN
    IF action_name='ASSIGN' THEN
       values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
    ELSIF action_name='PROPOSE' THEN
       values_:=values_||jsonb_build_object('effective_until_state','NOT_ESTABLISHED');
    END IF;
 END IF;
"""
    if old.count(marker) != 1 or old.count(" SELECT string_agg") != 1:
        raise RuntimeError("The 0059 writer shape differs.")
    return old.replace(marker, envelope).replace(" SELECT string_agg", add + " SELECT string_agg")


def switch_writer(editor, old, new):
    with editor.connection.cursor() as cursor:
        cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [creation.SIGNATURE])
        if cursor.fetchone() != (old,):
            raise RuntimeError("The installed shared writer differs. No function was replaced.")
    editor.execute(creation.HEADER.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
                   + " AS $$" + new + "$$;", params=None)


def require_no_circle_definition(apps, editor):
    # Keep the reconciliation check true across the atomic DDL cut, also on rollback.
    editor.execute("LOCK TABLE public.core_organismroledefinition IN ACCESS EXCLUSIVE MODE", params=None)
    if apps.get_model("core", "OrganismRoleDefinition").objects.using(editor.connection.alias).filter(
        code="COORDINATOR", scope="CIRCLE"
    ).exists():
        raise RuntimeError("A Circle Coordinator definition is recorded. Reconcile its history before changing keys.")


def install(apps, editor):
    switch_writer(editor, previous.writer_body(), writer_body())
    editor.execute(SQL, params=None)


def reverse(apps, editor):
    require_no_circle_definition(apps, editor)
    switch_writer(editor, writer_body(), previous.writer_body())
    editor.execute("""
      DROP TRIGGER circle_coordinator_event ON public.core_contextualroleassignmentstateevent;
      DROP FUNCTION public.circle_coordinator_event();
      DROP TRIGGER circle_definition_nomination ON public.core_organismroledefinition;
      DROP FUNCTION public.circle_definition_nomination();
      DROP TRIGGER circle_coordinator_definition ON public.core_organismroledefinition;
      DROP FUNCTION public.circle_coordinator_definition();
      DROP FUNCTION public.circle_appointment_marker(bigint,uuid,bigint,bigint,uuid,text,timestamptz,timestamptz);
      DROP FUNCTION public.circle_appointing_office(bigint,bigint,bigint,uuid,timestamptz,timestamptz);
      DROP FUNCTION public.circle_office_place(bigint,bigint,text,timestamptz,timestamptz);
    """, params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0059_operating_circles")]
    operations = [
        migrations.RunPython(require_no_circle_definition, migrations.RunPython.noop),
        migrations.RemoveConstraint("organismroledefinition", "s015_role_definition_version_uniq"),
        migrations.RemoveConstraint("organismroledefinition", "s015_0021_role_active_uniq"),
        migrations.AddConstraint("organismroledefinition", models.UniqueConstraint(
            fields=("living_organism", "code", "scope", "definition_version"),
            condition=Q(code="COORDINATOR"), name="s015_role_definition_version_uniq")),
        migrations.AddConstraint("organismroledefinition", models.UniqueConstraint(
            fields=("living_organism", "code", "scope"), condition=Q(code="COORDINATOR", active=True),
            name="s015_0021_role_active_uniq")),
        migrations.AddConstraint("organismroledefinition", models.UniqueConstraint(
            fields=("living_organism", "code", "definition_version"), condition=~Q(code="COORDINATOR"),
            name="cir_other_role_version_uniq")),
        migrations.AddConstraint("organismroledefinition", models.UniqueConstraint(
            fields=("living_organism", "code"), condition=Q(active=True) & ~Q(code="COORDINATOR"),
            name="cir_other_role_active_uniq")),
        migrations.RunPython(install, reverse),
    ]
