"""R P2 declaration Decision. No declaration, acceptance or publication is seeded."""
import uuid
from django.db import migrations,models
from django.db.models.deletion import PROTECT
DECLARATION_SHA256 = 'bbce006ebf7326b848803c8fbe9ba71ac2adc9fc7c43cbc275d8dddd403b1acf'
SOURCE_JSON = '{"HG_RULINGS_OQ47_OQ48_20261009.md":"20766b6a84c58e6eb2370cd8761dc6cf5b13ddaa889c42693e2e936f56fc6e1e","HG_RULING_OQ46_FOUNDER_STEWARD_EXCEPTION_20261009.md":"d3de399e38330e00c226d2ddf976a56df9288598d938222ceeba15c0f0078be8","HG_RULING_OQ46_SUCCESSION_CORRECTION_20261009.md":"70d626c03d7cbcbbc62c9318ef40b392e16b4ede8f2661cab1b8e1b8b5fd5240","VC_B14_RS107_VERIFICATION_RECORD_v1_0.md":"e9f7dfb81e9f6ae7e7e0cf4b0f1210269b1973b87ce13d3afcdd88e1f9b5836c"}'

SQL = """
CREATE FUNCTION public.r_founder_office(lo_id bigint,person bigint,at_time timestamptz,known timestamptz)
RETURNS bigint LANGUAGE sql STABLE STRICT AS $$
 SELECT a.id FROM public.c2_seat_facts(lo_id,at_time,known) f
 JOIN public.core_contextualroleassignment a ON a.id=f.assignment_id
 JOIN public.core_organismmembership m ON m.id=f.membership_id
 JOIN public.core_livingorganism lo ON lo.id=m.living_organism_id
 WHERE f.role_scope='LIVING_ORGANISM' AND f.role_code='INTEVIA_FOUNDER_STEWARD' AND f.standing='QUALIFIED'
 AND m.identity_id=person AND lo.founder_identity_id=person AND a.circle_id IS NULL
 AND public.r_circle_root_qualified(lo_id,at_time,known)
 AND public.s015_fold_effective_state('core_livingorganism',lo_id,at_time,known)='ACTIVE'
 AND public.c2_basis_in_force(lo.founding_authority_basis_id,at_time,known)
 ORDER BY a.id LIMIT 1
$$;
CREATE FUNCTION public.r_publication_declaration_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE executed timestamptz:=statement_timestamp();
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_DECLARATION_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=executed;
 IF NEW.founder_office_id IS NULL OR NEW.record_class IS DISTINCT FROM 'DECISION'
    OR NEW.access_posture IS DISTINCT FROM 'PRIVATE_DECLARING_ACTOR'
    OR NEW.verification_reference IS DISTINCT FROM 'sha256:e9f7dfb81e9f6ae7e7e0cf4b0f1210269b1973b87ce13d3afcdd88e1f9b5836c'
    OR NOT EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=NEW.living_organism_id AND lo.head_event_id=NEW.prior_root_event_id)
    OR NEW.decision_kind IS DISTINCT FROM 'PUBLICATION_EXCEPTION'
    OR NEW.declared_capacity IS DISTINCT FROM 'HUMAN_GOVERNOR_AND_INTEVIA_FOUNDER_STEWARD'
    OR NEW.text_sha256 IS DISTINCT FROM '__TEXT_SHA256__'
    OR encode(sha256(convert_to(NEW.text,'UTF8')),'hex') IS DISTINCT FROM NEW.text_sha256
    OR NEW.source_hashes IS DISTINCT FROM '__SOURCE_JSON__'::jsonb
    OR NEW.effective_at IS DISTINCT FROM NEW.received_at OR NEW.received_at>executed
    OR public.r_founder_office(NEW.living_organism_id,NEW.actor_id,NEW.effective_at,executed) IS DISTINCT FROM NEW.founder_office_id
    OR public.r_founder_office(NEW.living_organism_id,NEW.actor_id,executed,executed) IS DISTINCT FROM NEW.founder_office_id
    OR NOT EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=NEW.living_organism_id AND lo.founding_authority_basis_id=NEW.authority_basis_id)
    OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_access_epoch AND i.access_state='active') THEN
    RAISE EXCEPTION 'R_DECLARATION_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER r_publication_declaration_input BEFORE INSERT ON public.core_rootpublicationdeclaration
 FOR EACH ROW EXECUTE FUNCTION public.r_publication_declaration_input();
CREATE TRIGGER r_immutable BEFORE UPDATE OR DELETE ON public.core_rootpublicationdeclaration
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_r_no_truncate BEFORE TRUNCATE ON public.core_rootpublicationdeclaration
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
""".replace('__TEXT_SHA256__',DECLARATION_SHA256).replace('__SOURCE_JSON__',SOURCE_JSON)

def install(apps,editor):editor.execute(SQL,params=None)
def reverse(apps,editor):
 editor.execute('LOCK TABLE public.core_rootpublicationdeclaration IN ACCESS EXCLUSIVE MODE',params=None)
 with editor.connection.cursor() as cur:
  cur.execute('SELECT EXISTS(SELECT FROM public.core_rootpublicationdeclaration)')
  if cur.fetchone()[0]:raise RuntimeError('Declaration history exists; reversal refused')
 editor.execute('DROP TRIGGER r_publication_declaration_input ON public.core_rootpublicationdeclaration; DROP TRIGGER r_immutable ON public.core_rootpublicationdeclaration; DROP TRIGGER s015_r_no_truncate ON public.core_rootpublicationdeclaration; DROP FUNCTION public.r_publication_declaration_input(); DROP FUNCTION public.r_founder_office(bigint,bigint,timestamptz,timestamptz);',params=None)
class Migration(migrations.Migration):
 atomic=True
 dependencies=[('core','0063_root_circles')]
 operations=[migrations.CreateModel(name='RootPublicationDeclaration',fields=[
  ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
  ('declaration_uuid',models.UUIDField(default=uuid.uuid4,unique=True,editable=False)),
  ('living_organism',models.OneToOneField(to='core.livingorganism',on_delete=PROTECT,related_name='publication_declaration')),
  ('actor',models.ForeignKey(to='core.identity',on_delete=PROTECT,related_name='+')),
  ('actor_access_epoch',models.PositiveBigIntegerField()),
  ('founder_office',models.ForeignKey(to='core.contextualroleassignment',on_delete=PROTECT,related_name='+')),
  ('authority_basis',models.ForeignKey(to='core.authoritybasis',on_delete=PROTECT,related_name='+')),
  ('text',models.TextField()),('text_sha256',models.CharField(max_length=64)),('source_hashes',models.JSONField()),
  ('record_class',models.CharField(max_length=8,default='DECISION')),
  ('prior_root_event',models.ForeignKey(to='core.livingorganismevent',on_delete=PROTECT,related_name='+')),
  ('verification_reference',models.CharField(max_length=71)),('access_posture',models.CharField(max_length=32)),
  ('decision_kind',models.CharField(max_length=32,default='PUBLICATION_EXCEPTION')),
  ('declared_capacity',models.CharField(max_length=64,default='HUMAN_GOVERNOR_AND_INTEVIA_FOUNDER_STEWARD')),
  ('request_uuid',models.UUIDField(unique=True)),('effective_at',models.DateTimeField()),('received_at',models.DateTimeField()),
  ('recorded_at',models.DateTimeField(null=True,blank=True,editable=False))]),migrations.RunPython(install,reverse)]
