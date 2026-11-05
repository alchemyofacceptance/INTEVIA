"""R P1: replace the root prohibition with a byte-bound, atomic GENESIS route.

No data is seeded. Earlier ordinary branches and their predicates remain intact.
The issued input is disposable alpha only; beta needs its own fresh invocation.
"""
from importlib import import_module
import uuid

from django.db import migrations, models
from django.db.models.deletion import PROTECT
import core.models

sr = import_module('core.migrations.0053_sr_office_safeguards')
last = import_module('core.migrations.0061_circle_qualified_activation')
creation = last.offices.creation

SQL = r"""
CREATE FUNCTION public.r_genesis_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p public.core_authorityprincipal; b public.core_authoritybasis; request jsonb;
BEGIN
 IF NEW.recorded_at IS NOT NULL OR NEW.insert_xid IS NOT NULL THEN
    RAISE EXCEPTION 'R_GENESIS_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=statement_timestamp(); NEW.insert_xid:=pg_current_xact_id();
 IF NEW.canonical_reference IS DISTINCT FROM 'intevia-root-lo'
    OR NEW.scope IS DISTINCT FROM 'DISPOSABLE_ALPHA'
    OR encode(sha256(NEW.principal_invocation),'hex') IS DISTINCT FROM '57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7'
    OR encode(sha256(NEW.designation_invocation),'hex') IS DISTINCT FROM 'd1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831'
    OR encode(sha256(NEW.adoption_capture),'hex') IS DISTINCT FROM '3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9'
    OR NEW.founder_id=NEW.nominee_id OR NEW.effective_at IS DISTINCT FROM NEW.received_at
    OR NEW.received_at>NEW.recorded_at
    OR encode(sha256(NEW.request_bytes),'hex') IS DISTINCT FROM NEW.request_fingerprint THEN
    RAISE EXCEPTION 'R_GENESIS_INPUT_BINDING' USING ERRCODE='23514'; END IF;
 request:=convert_from(NEW.request_bytes,'UTF8')::jsonb;
 IF request->>'schema' IS DISTINCT FROM 'R:GENESIS:1'
    OR request->>'organism_uuid' IS DISTINCT FROM NEW.organism_uuid::text
    OR request->>'canonical_reference' IS DISTINCT FROM NEW.canonical_reference
    OR request->>'scope' IS DISTINCT FROM NEW.scope
    OR request->>'request_reference' IS DISTINCT FROM NEW.request_reference
    OR request->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key
    OR request->>'principal_sha256' IS DISTINCT FROM '57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7'
    OR request->>'designation_sha256' IS DISTINCT FROM 'd1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831'
    OR request->>'adoption_sha256' IS DISTINCT FROM '3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9'
    OR request->>'founder' IS DISTINCT FROM (SELECT identity_id::text FROM public.core_identity WHERE id=NEW.founder_id)
    OR request->>'nominee' IS DISTINCT FROM (SELECT identity_id::text FROM public.core_identity WHERE id=NEW.nominee_id)
    OR request->>'founder_username' IS DISTINCT FROM (SELECT canonical_username FROM public.core_identity WHERE id=NEW.founder_id)
    OR request->>'nominee_username' IS DISTINCT FROM (SELECT canonical_username FROM public.core_identity WHERE id=NEW.nominee_id)
    OR (SELECT count(*) FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
        WHERE i.id IN (NEW.founder_id,NEW.nominee_id) AND i.access_state='active' AND u.is_active)<>2
    OR NOT EXISTS(SELECT FROM public.core_learnerregistration r JOIN public.core_platformtermsacceptance a ON r.evidence_reference='platform-terms-acceptance:'||a.acceptance_uuid AND a.acceptance_kind='REGISTRATION'
       JOIN public.core_platformtermsversion v ON v.id=a.terms_version_id
       JOIN public.core_registrationreceipt receipt ON receipt.id=r.registration_receipt_id
       WHERE r.identity_id=NEW.founder_id AND v.is_fixture AND v.terms_version_uuid::text=request->>'terms_version_uuid' AND receipt.transaction_id=txid_current())
    OR NOT EXISTS(SELECT FROM public.core_learnerregistration r JOIN public.core_platformtermsacceptance a ON r.evidence_reference='platform-terms-acceptance:'||a.acceptance_uuid AND a.acceptance_kind='REGISTRATION'
       JOIN public.core_platformtermsversion v ON v.id=a.terms_version_id
       JOIN public.core_registrationreceipt receipt ON receipt.id=r.registration_receipt_id
       WHERE r.identity_id=NEW.nominee_id AND v.is_fixture AND v.terms_version_uuid::text=request->>'terms_version_uuid' AND receipt.transaction_id=txid_current()) THEN
    RAISE EXCEPTION 'R_GENESIS_ACCOUNT_BINDING' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT b FROM public.core_authoritybasis WHERE id=NEW.basis_id;
 SELECT * INTO STRICT p FROM public.core_authorityprincipal WHERE id=b.authority_principal_id;
 IF p.governed_source_namespace IS DISTINCT FROM 'INTEVIA_RESERVED_GENESIS_AUTHORITY_V1'
    OR p.canonical_governed_source_id IS DISTINCT FROM 'urn:intevia:governed-source:reserved-genesis:human-governor:carmian-owen:intevia-v1.0'
    OR p.bootstrap_invocation_fingerprint IS DISTINCT FROM '57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7'
    OR b.derivation_root_principal_id IS DISTINCT FROM p.id OR b.issuer_id IS DISTINCT FROM NEW.founder_id
    OR b.authority_class IS DISTINCT FROM 'GENESIS' OR b.scope_fingerprint IS DISTINCT FROM NEW.request_fingerprint
    OR b.instrument_reference IS DISTINCT FROM 'sha256:d1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831'
    OR b.evidence_reference IS DISTINCT FROM 'sha256:3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9'
    OR b.effective_at IS DISTINCT FROM NEW.effective_at THEN
    RAISE EXCEPTION 'R_GENESIS_PRINCIPAL_BINDING' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT FROM public.core_livingorganism WHERE platform_root) THEN
    RAISE EXCEPTION 'R_GENESIS_ALREADY_EXISTS' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER r_genesis_input BEFORE INSERT ON public.core_rootgenesis
 FOR EACH ROW EXECUTE FUNCTION public.r_genesis_input();

CREATE FUNCTION public.r_genesis_context(lo_id bigint, decision text, actor bigint, at_time timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_rootgenesis g JOIN public.core_livingorganism lo ON lo.organism_id=g.organism_uuid
 JOIN public.core_authoritydecision d ON d.decision_reference=g.decision_reference
 WHERE lo.id=lo_id AND lo.platform_root AND lo.founding_insert_xid=pg_current_xact_id()
 AND g.insert_xid=pg_current_xact_id() AND g.decision_reference=decision AND g.founder_id=actor
 AND g.effective_at=at_time AND d.action='GENESIS' AND d.command_subtype='GENESIS'
 AND d.outcome='QUALIFIED' AND d.actor_id=actor AND d.effective_at=at_time AND d.evaluated_at=g.received_at
 AND d.target->>'organism_uuid'=g.organism_uuid::text AND d.target->>'request_fingerprint'=g.request_fingerprint
 AND d.request_reference=g.request_reference AND d.idempotency_key=g.idempotency_key
 AND d.actor_identity_id=(SELECT identity_id FROM public.core_identity WHERE id=actor)
 AND d.actor_access_epoch=(SELECT access_epoch FROM public.core_identity WHERE id=actor)
 AND d.decision_payload->>'request_fingerprint'=g.request_fingerprint
 AND d.decision_payload->>'actor'=d.actor_identity_id::text
 AND d.decision_payload->>'outcome'='QUALIFIED')
$$;

CREATE FUNCTION public.r_genesis_root_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.platform_root AND NOT EXISTS(SELECT FROM public.core_rootgenesis g
     WHERE g.organism_uuid=NEW.organism_id AND g.insert_xid=pg_current_xact_id()
       AND NEW.founder_identity_id IS NULL AND NEW.founding_authority_basis_id IS NULL) THEN
    RAISE EXCEPTION 'R_GENESIS_DESIGNATION_REQUIRED' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER r_genesis_root_insert BEFORE INSERT ON public.core_livingorganism
 FOR EACH ROW EXECUTE FUNCTION public.r_genesis_root_insert();

CREATE FUNCTION public.r_reserved_definition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 -- 0021 supplies the platform-root-only diagnostic for these five reserved codes.
 IF NEW.code=ANY(public.s015_reserved_role_codes()) AND NOT EXISTS(
    SELECT FROM public.core_livingorganism WHERE id=NEW.living_organism_id AND platform_root
 ) THEN RETURN NEW; END IF;
 IF NEW.scope IS DISTINCT FROM 'LIVING_ORGANISM' OR NEW.definition_version<>1
    OR NOT (NEW.code=ANY(public.s015_reserved_role_codes()))
    OR public.r_genesis_context(NEW.living_organism_id,NEW.authority_decision_reference,NEW.enterer_id,NEW.effective_at) IS NOT TRUE THEN
    RAISE EXCEPTION 'SR definition refused: reserved root office' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER r_reserved_definition BEFORE INSERT ON public.core_organismroledefinition
 FOR EACH ROW WHEN (starts_with(NEW.code,'INTEVIA_')) EXECUTE FUNCTION public.r_reserved_definition();

CREATE FUNCTION public.r_genesis_found_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g public.core_rootgenesis;
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_livingorganism WHERE id=NEW.living_organism_id AND platform_root) THEN RETURN NEW; END IF;
 IF NEW.sequence<>1 THEN RETURN NEW; END IF;
 SELECT g0.* INTO STRICT g FROM public.core_rootgenesis g0 JOIN public.core_livingorganism lo ON lo.organism_id=g0.organism_uuid
 WHERE lo.id=NEW.living_organism_id;
 IF public.r_genesis_context(NEW.living_organism_id,NEW.authority_decision_reference,NEW.actor_id,NEW.effective_at) IS NOT TRUE
    OR NEW.action IS DISTINCT FROM 'FOUND' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
    OR NEW.actor_state IS DISTINCT FROM 'PARTY' OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
    OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT' OR NEW.actor_id IS DISTINCT FROM NEW.enterer_identity_id
    OR NEW.authority_basis_id IS DISTINCT FROM g.basis_id OR NEW.received_at IS DISTINCT FROM g.received_at
    OR NEW.occurred_at IS DISTINCT FROM g.effective_at OR NEW.payload_fingerprint IS DISTINCT FROM g.request_fingerprint
    OR NEW.actor_access_epoch IS DISTINCT FROM (SELECT access_epoch FROM public.core_identity WHERE id=g.founder_id) THEN
    RAISE EXCEPTION 'R_GENESIS_FOUND_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_r_genesis_found BEFORE INSERT ON public.core_livingorganismevent
 FOR EACH ROW EXECUTE FUNCTION public.r_genesis_found_event();

CREATE FUNCTION public.r_genesis_nomination() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_NOMINATION_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=statement_timestamp();
 IF NOT EXISTS(SELECT FROM public.core_rootgenesis g
   JOIN public.core_livingorganism lo ON lo.organism_id=g.organism_uuid AND lo.platform_root
   JOIN public.core_organismroledefinition d ON d.living_organism_id=lo.id AND d.id=NEW.definition_id
   WHERE g.id=NEW.genesis_id AND g.insert_xid=pg_current_xact_id() AND NEW.nominee_id=g.nominee_id
   AND NEW.entered_by_id=g.founder_id AND NEW.effective_at=g.effective_at
   AND d.code IN ('INTEVIA_GUIDING_STEWARD','INTEVIA_EMERGENCY_STEWARD') AND d.scope='LIVING_ORGANISM' AND d.active
   AND public.r_genesis_context(lo.id,g.decision_reference,g.founder_id,g.effective_at)) THEN
    RAISE EXCEPTION 'R_NOMINATION_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER r_genesis_nomination BEFORE INSERT ON public.core_rootsuccessornomination
 FOR EACH ROW EXECUTE FUNCTION public.r_genesis_nomination();

CREATE FUNCTION public.r_genesis_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo public.core_livingorganism; required_code text;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE organism_id=NEW.organism_uuid AND platform_root;
 IF lo.id IS NULL OR lo.founder_identity_id IS DISTINCT FROM NEW.founder_id
    OR lo.founding_authority_basis_id IS DISTINCT FROM NEW.basis_id
    OR public.r_genesis_context(lo.id,NEW.decision_reference,NEW.founder_id,NEW.effective_at) IS NOT TRUE THEN
    RAISE EXCEPTION 'R_GENESIS_INCOMPLETE' USING ERRCODE='23514'; END IF;
 FOREACH required_code IN ARRAY ARRAY['INTEVIA_FOUNDER_STEWARD','INTEVIA_PRIVACY_COORDINATOR','COORDINATOR'] LOOP
    IF (SELECT count(*) FROM public.core_contextualroleassignment a
       JOIN public.core_organismmembership m ON m.id=a.membership_id
       JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       WHERE m.identity_id=NEW.founder_id AND m.living_organism_id=lo.id AND d.code=required_code AND d.scope='LIVING_ORGANISM'
       AND a.circle_id IS NULL AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,NEW.effective_at,statement_timestamp())='ACTIVE')<>1 THEN
       RAISE EXCEPTION 'R_GENESIS_FOUNDER_OFFICE_MISSING: %',required_code USING ERRCODE='23514'; END IF;
 END LOOP;
 IF (SELECT count(*) FROM public.core_rootsuccessornomination WHERE genesis_id=NEW.id)<>2
    OR EXISTS(SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismmembership m ON m.id=a.membership_id
       JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       WHERE d.living_organism_id=lo.id AND starts_with(d.code,'INTEVIA_') AND m.identity_id<>NEW.founder_id) THEN
    RAISE EXCEPTION 'R_GENESIS_NOMINATION_NOT_ACTIVATION' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER r_genesis_complete AFTER INSERT ON public.core_rootgenesis
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.r_genesis_complete();
"""

DEFINITION_BRANCH = """
    IF v_decision_row.action = 'GENESIS' THEN
        IF public.r_genesis_context(NEW.living_organism_id,NEW.authority_decision_reference,NEW.enterer_id,NEW.effective_at) IS NOT TRUE
           OR NOT (NEW.code=ANY(public.s015_reserved_role_codes()) OR NEW.code='COORDINATOR')
           OR NEW.scope<>'LIVING_ORGANISM' OR NEW.definition_version<>1 OR NEW.supersedes_id IS NOT NULL
           OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NEW.received_at IS DISTINCT FROM NEW.effective_at
           OR NEW.enterer_access_epoch IS DISTINCT FROM v_decision_row.actor_access_epoch THEN
            RAISE EXCEPTION 'R_GENESIS_DEFINITION_SOURCE' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
"""
OFFICE_BRANCH = """
    IF starts_with(v_role_code,'INTEVIA_') AND public.r_genesis_context(
         v_lo_id,NEW.authority_decision_reference,NEW.actor_id,NEW.effective_at) THEN
        IF v_role_code NOT IN ('INTEVIA_FOUNDER_STEWARD','INTEVIA_PRIVACY_COORDINATOR')
           OR v_membership_identity_id IS DISTINCT FROM NEW.actor_id OR NEW.sequence<>1 OR NEW.prior_state IS NOT NULL
           OR NEW.action IS DISTINCT FROM 'ASSIGN' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
           OR NEW.actor_state IS DISTINCT FROM 'PARTY' OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
           OR NEW.enterer_identity_id IS DISTINCT FROM NEW.actor_id OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
           OR NEW.effective_until_state IS DISTINCT FROM 'NONE' OR NEW.effective_until IS NOT NULL
           OR NEW.received_at IS DISTINCT FROM NEW.effective_at OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at
           OR v_role_active IS DISTINCT FROM true OR NOT EXISTS(
              SELECT FROM public.core_rootgenesis g JOIN public.core_livingorganismevent f ON f.living_organism_id=v_lo_id AND f.sequence=1
              WHERE g.organism_uuid=v_lo_uuid AND NEW.authority_basis_id=g.basis_id
              AND NEW.actor_access_epoch=(SELECT access_epoch FROM public.core_identity WHERE id=g.founder_id)
              AND NEW.evidence_reference='founding-batch:'||f.event_uuid
              AND NEW.payload_fingerprint=g.request_fingerprint) THEN
            RAISE EXCEPTION 'R_GENESIS_OFFICE_SOURCE' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
"""


def replace_once(body, marker, replacement):
    if body.count(marker) != 1:
        raise RuntimeError('R GENESIS expected guard fragment is missing or repeated')
    return body.replace(marker, replacement, 1)


def writer_body():
    body = last.writer_body()
    body = replace_once(body, "(is_command AND token IN ('core_circle'",
                        "(is_command AND token IN ('core_organismmembership','core_essentialcoveragecase','core_circle'")
    body = replace_once(body, "WHERE token<>'core_authoritybasis' OR k<>'kind'", """WHERE NOT (
       (token='core_authoritybasis' AND k='kind') OR
       (token='core_organismmembership' AND k='reason_class') OR
       (token='core_essentialcoveragecase' AND k IN ('roster_version','evaluated_state_at','evaluated_known_at','determiner_capacity_reference')))""")
    marker = " IF token='core_contextualroleassignment' AND action_name IN ('PROPOSE','ASSIGN') THEN"
    branch = """
 IF is_command AND token IN ('core_organismmembership','core_essentialcoveragecase') AND (
    command_rule IS NOT NULL OR NOT EXISTS(SELECT FROM public.core_rootgenesis g JOIN public.core_livingorganism lo
     ON lo.organism_id=g.organism_uuid WHERE g.insert_xid=pg_current_xact_id() AND g.decision_reference=decision
     AND g.founder_id=actor AND g.basis_id=basis AND g.request_fingerprint=payload AND g.effective_at=at_time
     AND ((token='core_organismmembership' AND EXISTS(SELECT FROM public.core_organismmembership m
            WHERE m.id=anchor AND m.living_organism_id=lo.id AND m.identity_id=actor)
            AND action_name IN ('OPEN','ACTIVATE') AND metadata->>'reason_class'='FOUNDING')
       OR (token='core_essentialcoveragecase' AND EXISTS(SELECT FROM public.core_essentialcoveragecase c
            WHERE c.id=anchor AND c.living_organism_id=lo.id) AND action_name='ASSESS' AND outcome='SATISFIED')))) THEN
    RAISE EXCEPTION 'R_GENESIS_WRITER_SOURCE'; END IF;
 IF token='core_contextualroleassignment' AND action_name='ASSIGN' AND EXISTS(
    SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
    WHERE a.id=anchor AND a.circle_id IS NULL AND d.scope='LIVING_ORGANISM'
    AND public.r_genesis_context(d.living_organism_id,decision,actor,at_time)) THEN
    IF command_rule IS NOT NULL OR access_epoch IS NULL OR deadline IS NOT NULL THEN
       RAISE EXCEPTION 'R_GENESIS_WRITER_OFFICE'; END IF;
 ELSIF token='core_contextualroleassignment' AND action_name IN ('PROPOSE','ASSIGN') THEN"""
    body = replace_once(body, marker, branch)
    body = replace_once(body, "    IF action_name='ASSIGN' THEN\n       values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM'",
        "    IF action_name='ASSIGN' AND EXISTS(SELECT FROM public.core_contextualroleassignment a WHERE a.id=anchor AND a.circle_id IS NOT NULL) THEN\n       values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM'")
    return body


def bodies():
    return [
        ('public.sr_definition_insert_guard()', sr.NEW_SR_DEFINITION_INSERT_GUARD_BODY,
         replace_once(sr.NEW_SR_DEFINITION_INSERT_GUARD_BODY, "    IF v_decision_row.action = 'FOUND_LIVING_ORGANISM' THEN",
                      DEFINITION_BRANCH + "    IF v_decision_row.action = 'FOUND_LIVING_ORGANISM' THEN")),
        ('public.s2b_guard_office_attribution()', sr.NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY,
         replace_once(sr.NEW_S2B_GUARD_OFFICE_ATTRIBUTION_BODY, "    IF starts_with(v_role_code, 'INTEVIA_') THEN",
                      OFFICE_BRANCH + "    IF starts_with(v_role_code, 'INTEVIA_') THEN")),
        (creation.SIGNATURE, last.writer_body(), writer_body()),
    ]


def switch(editor, reverse=False):
    specs=bodies()
    with editor.connection.cursor() as cur:
        for sig,old,new in specs:
            cur.execute('SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure',[sig])
            if cur.fetchone() != (new if reverse else old,):
                raise RuntimeError(f'R GENESIS installed body differs: {sig}')
    for sig,old,new in specs:
        header = (creation.HEADER.replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1)
                  if sig==creation.SIGNATURE else f'CREATE OR REPLACE FUNCTION {sig} RETURNS trigger LANGUAGE plpgsql')
        editor.execute(header+' AS $$'+(old if reverse else new)+'$$;',params=None)


def install(apps, editor):
    if editor.connection.vendor != 'postgresql':
        raise RuntimeError('R GENESIS requires PostgreSQL')
    editor.execute(SQL,params=None)
    switch(editor)
    for table in ('core_rootgenesis','core_rootsuccessornomination'):
        editor.execute(f'CREATE TRIGGER r_immutable BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()',params=None)
        editor.execute(f'CREATE TRIGGER s015_r_no_truncate BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()',params=None)


def reverse(apps, editor):
    editor.execute('LOCK TABLE public.core_rootgenesis, public.core_livingorganism IN ACCESS EXCLUSIVE MODE',params=None)
    with editor.connection.cursor() as cur:
        cur.execute('SELECT EXISTS(SELECT FROM public.core_rootgenesis) OR EXISTS(SELECT FROM public.core_livingorganism WHERE platform_root)')
        if cur.fetchone()[0]:
            raise RuntimeError('R GENESIS history exists; reversal refused without deleting history')
    switch(editor,reverse=True)
    for table in ('core_rootgenesis','core_rootsuccessornomination'):
        for name in ('r_immutable','s015_r_no_truncate'):
            editor.execute(f'DROP TRIGGER {name} ON public.{table}',params=None)
    for name,table in [('r_genesis_complete','core_rootgenesis'),('r_genesis_input','core_rootgenesis'),
                       ('r_genesis_root_insert','core_livingorganism'),('zz_r_genesis_found','core_livingorganismevent'),
                       ('r_genesis_nomination','core_rootsuccessornomination'),('r_reserved_definition','core_organismroledefinition')]:
        editor.execute(f'DROP TRIGGER {name} ON public.{table}',params=None)
    for name in ('r_reserved_definition()','r_genesis_complete()','r_genesis_nomination()','r_genesis_found_event()',
                 'r_genesis_root_insert()','r_genesis_context(bigint,text,bigint,timestamptz)','r_genesis_input()'):
        editor.execute(f'DROP FUNCTION public.{name}',params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [('core','0061_circle_qualified_activation')]
    operations = [
        migrations.CreateModel(name='RootGenesis', fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('designation_uuid',models.UUIDField(default=uuid.uuid4,unique=True,editable=False)),
            ('canonical_reference',models.CharField(max_length=90,unique=True)),
            ('organism_uuid',models.UUIDField(unique=True)),('scope',models.CharField(max_length=32)),
            ('founder',models.ForeignKey(to='core.identity',on_delete=PROTECT,related_name='+')),
            ('nominee',models.ForeignKey(to='core.identity',on_delete=PROTECT,related_name='+')),
            ('basis',models.ForeignKey(to='core.authoritybasis',on_delete=PROTECT,related_name='+')),
            ('principal_invocation',models.BinaryField()),('designation_invocation',models.BinaryField()),
            ('adoption_capture',models.BinaryField()),('request_bytes',models.BinaryField()),
            ('request_fingerprint',models.CharField(max_length=64)),
            ('decision_reference',models.CharField(max_length=71,unique=True)),
            ('request_reference',models.CharField(max_length=128)),('idempotency_key',models.CharField(max_length=120,unique=True)),
            ('effective_at',models.DateTimeField()),('received_at',models.DateTimeField()),
            ('recorded_at',models.DateTimeField(null=True,blank=True,editable=False)),
            ('insert_xid',core.models.FoundingTransactionIdField(null=True,blank=True,editable=False)),
        ]),
        migrations.CreateModel(name='RootSuccessorNomination',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('nomination_uuid',models.UUIDField(default=uuid.uuid4,unique=True,editable=False)),
            ('genesis',models.ForeignKey(to='core.rootgenesis',on_delete=PROTECT,related_name='nominations')),
            ('definition',models.ForeignKey(to='core.organismroledefinition',on_delete=PROTECT,related_name='+')),
            ('nominee',models.ForeignKey(to='core.identity',on_delete=PROTECT,related_name='+')),
            ('entered_by',models.ForeignKey(to='core.identity',on_delete=PROTECT,related_name='+')),
            ('effective_at',models.DateTimeField()),('recorded_at',models.DateTimeField(null=True,blank=True,editable=False)),
        ],options={'constraints':[models.UniqueConstraint(fields=('genesis','definition'),name='r_initial_nomination_uniq')]}),
        migrations.RunPython(install,reverse),
        migrations.RemoveConstraint(model_name='livingorganism',name='s015_platform_root_unset_ck'),
    ]
