"""R P2: qualified root GENERIC Circles; ordinary birth descriptions and root batch receipt."""
from importlib import import_module
import uuid
from django.db import migrations,models
from django.db.models.deletion import PROTECT
old=import_module('core.migrations.0059_operating_circles')
offices=import_module('core.migrations.0060_circle_coordinators')
active=import_module('core.migrations.0061_circle_qualified_activation')
formation=import_module('core.migrations.0045_sbg_governance_formation')

QUALIFIED = """
CREATE FUNCTION public.r_circle_root_qualified(lo_id bigint, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_livingorganism lo JOIN public.core_rootgenesis g ON g.organism_uuid=lo.organism_id
 WHERE lo.id=lo_id AND lo.platform_root AND g.canonical_reference='intevia-root-lo'
 AND g.founder_id=lo.founder_identity_id AND g.basis_id=lo.founding_authority_basis_id
 AND g.effective_at<=at_time AND g.recorded_at<=known)
$$;
"""

def extract(sql,name):
 marker='CREATE FUNCTION public.'+name+'('
 if sql.count(marker)!=1:raise RuntimeError('R root Circle function source differs: '+name)
 header,body=sql.split(marker,1)[1].split(' AS $$',1)
 return marker+header,body.split('$$;',1)[0]

def replace_once(body,old,new):
 if body.count(old)!=1:raise RuntimeError('R root Circle predicate source differs')
 return body.replace(old,new,1)

def bodies():
 specs=[(old.SQL,'circle_lo_office','bigint,bigint,timestamptz,timestamptz','AND NOT l.platform_root',
  'AND (NOT l.platform_root OR public.r_circle_root_qualified(lo,at_time,known))'),
  (old.SQL,'circle_generic_offices','bigint,timestamptz,timestamptz','AND NOT lo.platform_root',
  'AND (NOT lo.platform_root OR public.r_circle_root_qualified(lo.id,at_time,known))'),
  (old.SQL,'circle_vacant','bigint,uuid,timestamptz,timestamptz','AND NOT lo.platform_root',
  'AND (NOT lo.platform_root OR public.r_circle_root_qualified(lo.id,at_time,known))'),
  (active.SQL,'circle_last_ending','bigint,timestamptz','AND NOT lo.platform_root',
  'AND (NOT lo.platform_root OR public.r_circle_root_qualified(lo.id,known,known))'),
  (offices.SQL,'circle_coordinator_event','','IF office.platform_root THEN',
  "IF office.platform_root AND (office.kind<>'GENERIC' OR public.r_circle_root_qualified(office.lo_id,NEW.effective_at,NEW.recorded_at) IS NOT TRUE) THEN")]
 result=[(name+'('+args+')',*extract(sql,name),replace_once(extract(sql,name)[1],needle,replacement))
         for sql,name,args,needle,replacement in specs]
 phase=formation.NEW_SB_GUARD_PHASE_EVENT_BODY
 replacement="acceptance.acceptance_kind = CASE WHEN organism_row.platform_root AND public.r_circle_root_qualified(organism_row.id,NEW.effective_at,NEW.recorded_at) THEN 'GENESIS' WHEN NOT organism_row.platform_root THEN 'FOUNDING' ELSE NULL END"
 result.append(('sb_guard_phase_event()','CREATE FUNCTION public.sb_guard_phase_event() RETURNS trigger LANGUAGE plpgsql',phase,replace_once(phase,"acceptance.acceptance_kind = 'FOUNDING'",replacement)))
 return result

GUARDS = """
CREATE FUNCTION public.r_circle_description_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c public.core_circle; opening public.core_circlestateevent;
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_DESCRIPTION_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=statement_timestamp();
 SELECT * INTO STRICT c FROM public.core_circle WHERE id=NEW.circle_id;
 SELECT * INTO opening FROM public.core_circlestateevent WHERE id=NEW.birth_event_id AND circle_id=c.id AND sequence=1;
 IF opening.id IS NULL OR opening.action<>'CREATE_DORMANT' OR c.kind<>'GENERIC'
    OR opening.evidence_reference IS DISTINCT FROM 'circle-purpose:'||NEW.purpose
    OR NEW.enterer_id IS DISTINCT FROM opening.enterer_identity_id
    OR btrim(NEW.name)='' OR NEW.effective_at>NEW.recorded_at
    OR NEW.effective_at<opening.effective_at
    OR NOT EXISTS(SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=NEW.enterer_id)
    OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.enterer_id AND i.access_epoch=NEW.enterer_access_epoch AND i.access_state='active') THEN
    RAISE EXCEPTION 'R_DESCRIPTION_BIRTH_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER r_circle_description_input BEFORE INSERT ON public.core_circlebirthdescription
 FOR EACH ROW EXECUTE FUNCTION public.r_circle_description_input();
CREATE FUNCTION public.r_root_circle_batch_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo public.core_livingorganism; g public.core_rootgenesis; item jsonb;
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_BATCH_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=statement_timestamp();
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=NEW.living_organism_id;
 SELECT * INTO g FROM public.core_rootgenesis WHERE organism_uuid=lo.organism_id;
 IF g.id IS NULL OR public.r_circle_root_qualified(lo.id,NEW.effective_at,NEW.recorded_at) IS NOT TRUE OR NEW.enterer_id<>g.founder_id
    OR NEW.effective_at>NEW.recorded_at OR jsonb_typeof(NEW.circles)<>'array' OR jsonb_array_length(NEW.circles)<>10
    OR public.circle_lo_office(lo.id,NEW.enterer_id,NEW.effective_at,NEW.recorded_at) IS NULL
    OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=lo.id AND e.action='BIRTHING' AND e.effective_at<=NEW.effective_at AND e.recorded_at<=NEW.recorded_at) THEN
    RAISE EXCEPTION 'R_ROOT_CIRCLE_BATCH_SOURCE' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(NEW.circles) LOOP
    IF NOT EXISTS(SELECT FROM public.core_circle c JOIN public.core_circlebirthdescription d ON d.circle_id=c.id
        WHERE c.parent_organism_id=lo.id AND c.circle_uuid::text=item->>'circle_uuid' AND d.name=item->>'name'
        AND c.kind='GENERIC' AND d.enterer_id=g.founder_id AND d.recorded_at<=NEW.recorded_at
        AND public.s015_fold_effective_state('core_circle',c.id,NEW.effective_at,NEW.recorded_at)='ACTIVE'
        AND EXISTS(SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=g.founder_id)) THEN
       RAISE EXCEPTION 'R_ROOT_CIRCLE_BATCH_INCOMPLETE' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF (SELECT count(DISTINCT value->>'name') FROM jsonb_array_elements(NEW.circles))<>10
    OR EXISTS(SELECT value->>'name' FROM jsonb_array_elements(NEW.circles) EXCEPT SELECT unnest(ARRAY[
       'INTEVIA Education','INTEVIA Library','INTEVIA Support','INTEVIA Relationships','INTEVIA Operations',
       'INTEVIA People','INTEVIA Finance','INTEVIA Legal and Compliance','INTEVIA Technology','INTEVIA Marketing and Communications'])) THEN
    RAISE EXCEPTION 'R_ROOT_CIRCLE_BATCH_INVENTORY' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER r_root_circle_batch_input BEFORE INSERT ON public.core_rootcirclebatch
 FOR EACH ROW EXECUTE FUNCTION public.r_root_circle_batch_input();
"""

def switch(editor,back=False):
 for sig,header,before,after in bodies():
  with editor.connection.cursor() as c:
   c.execute('SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure',['public.'+sig])
   if c.fetchone()!=(after if back else before,):raise RuntimeError('R installed Circle function differs: '+sig)
 for sig,header,before,after in bodies():editor.execute(header.replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1)+' AS $$'+(before if back else after)+'$$;',params=None)

def install(apps,editor):
 editor.execute(QUALIFIED,params=None);switch(editor);editor.execute(GUARDS,params=None)
 for table in ('core_circlebirthdescription','core_rootcirclebatch'):
  editor.execute(f'CREATE TRIGGER r_immutable BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()',params=None)
  editor.execute(f'CREATE TRIGGER s015_r_no_truncate BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()',params=None)

def reverse(apps,editor):
 editor.execute('LOCK TABLE public.core_circle,public.core_circlebirthdescription,public.core_rootcirclebatch IN ACCESS EXCLUSIVE MODE',params=None)
 with editor.connection.cursor() as c:
  c.execute("SELECT EXISTS(SELECT FROM public.core_circlebirthdescription) OR EXISTS(SELECT FROM public.core_rootcirclebatch) OR EXISTS(SELECT FROM public.core_circle c JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id WHERE lo.platform_root) OR EXISTS(SELECT FROM public.core_livingorganismevent e JOIN public.core_livingorganism lo ON lo.id=e.living_organism_id WHERE lo.platform_root AND e.action='BIRTHING')")
  if c.fetchone()[0]:raise RuntimeError('R Circle history exists; reversal refused')
 switch(editor,True)
 for table in ('core_circlebirthdescription','core_rootcirclebatch'):
  for name in ('r_immutable','s015_r_no_truncate'):editor.execute(f'DROP TRIGGER {name} ON public.{table}',params=None)
 editor.execute('DROP TRIGGER r_circle_description_input ON public.core_circlebirthdescription; DROP TRIGGER r_root_circle_batch_input ON public.core_rootcirclebatch; DROP FUNCTION public.r_circle_description_input(); DROP FUNCTION public.r_root_circle_batch_input(); DROP FUNCTION public.r_circle_root_qualified(bigint,timestamptz,timestamptz);',params=None)

class Migration(migrations.Migration):
 atomic=True
 dependencies=[('core','0062_root_genesis')]
 operations=[
  migrations.CreateModel(name='CircleBirthDescription',fields=[
   ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
   ('circle',models.OneToOneField(to='core.circle',on_delete=PROTECT,related_name='birth_description')),
   ('birth_event',models.OneToOneField(to='core.circlestateevent',on_delete=PROTECT,related_name='+')),
   ('name',models.CharField(max_length=90)),('purpose',models.CharField(max_length=240)),
   ('enterer',models.ForeignKey(to='core.identity',on_delete=PROTECT,related_name='+')),
   ('enterer_access_epoch',models.PositiveBigIntegerField()),('effective_at',models.DateTimeField()),
   ('recorded_at',models.DateTimeField(null=True,blank=True,editable=False))]),
  migrations.CreateModel(name='RootCircleBatch',fields=[
   ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
   ('batch_uuid',models.UUIDField(default=uuid.uuid4,unique=True,editable=False)),
   ('living_organism',models.OneToOneField(to='core.livingorganism',on_delete=PROTECT,related_name='root_circle_batch')),
   ('enterer',models.ForeignKey(to='core.identity',on_delete=PROTECT,related_name='+')),
   ('circles',models.JSONField()),('effective_at',models.DateTimeField()),('recorded_at',models.DateTimeField(null=True,blank=True,editable=False))]),
  migrations.RunPython(install,reverse)]
