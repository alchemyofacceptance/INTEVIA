from django.db import migrations, models
from django.db.models.deletion import PROTECT


CONSENT_SQL = """
CREATE FUNCTION public.s2b_guard_office_consent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_assignment_uuid uuid; v_assignment_membership_id bigint; v_membership_uuid uuid;
    v_nominee_identity_id bigint; proposal record; previous_action text;
    previous_ordinal integer; role_uuid uuid;
        decision_row record; reference_facts jsonb;
BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S2B office consent recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := statement_timestamp();
    IF NEW.actor_state IS DISTINCT FROM 'PARTY'
       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
       OR NEW.actor_id IS DISTINCT FROM NEW.enterer_id
       OR NEW.actor_access_epoch IS NULL
       OR NEW.occurred_at > NEW.received_at
       OR NEW.received_at > NEW.recorded_at THEN
        RAISE EXCEPTION 'S2B office consent attribution or time order refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
        SELECT a.assignment_uuid, a.membership_id, m.membership_uuid, m.identity_id, d.role_uuid
         INTO v_assignment_uuid, v_assignment_membership_id, v_membership_uuid,
             v_nominee_identity_id, role_uuid
      FROM public.core_contextualroleassignment a
      JOIN public.core_organismmembership m ON m.id = a.membership_id
         JOIN public.core_organismroledefinition d ON d.id = a.role_definition_id
     WHERE a.id = NEW.assignment_id;
        IF v_assignment_uuid IS NULL
          OR NEW.nominee_membership_id IS DISTINCT FROM v_assignment_membership_id THEN
        RAISE EXCEPTION 'S2B office consent nomination context refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT event_uuid, actor_id, action, sequence, resulting_state, assignment_id
      INTO proposal FROM public.core_contextualroleassignmentstateevent
     WHERE id = NEW.proposal_event_id;
    IF proposal.event_uuid IS NULL OR proposal.assignment_id <> NEW.assignment_id
       OR proposal.sequence <> 1 OR proposal.action <> 'PROPOSE'
       OR proposal.resulting_state <> 'PROPOSED'
      OR NEW.actor_id <> v_nominee_identity_id
       OR NEW.actor_access_epoch <> (
          SELECT access_epoch FROM public.core_identity WHERE id = v_nominee_identity_id
       ) THEN
        RAISE EXCEPTION 'S2B office consent nominee or proposal refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.core_contextualroleassignmentstateevent assigned
         WHERE assigned.assignment_id = NEW.assignment_id AND assigned.action = 'ASSIGN'
           AND assigned.recorded_at <= NEW.recorded_at
    ) THEN
        RAISE EXCEPTION 'S2B office consent after appointment refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT COALESCE(MAX(ordinal), 0) INTO previous_ordinal
      FROM public.core_officeappointmentconsent WHERE assignment_id = NEW.assignment_id;
    IF NEW.ordinal <> previous_ordinal + 1 THEN
        RAISE EXCEPTION 'S2B office consent ordinal refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT action INTO previous_action FROM public.core_officeappointmentconsent
     WHERE assignment_id = NEW.assignment_id ORDER BY ordinal DESC LIMIT 1;
    IF previous_action IN ('DECLINE', 'WITHDRAW') THEN
        RAISE EXCEPTION 'S2B office consent terminal history refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF proposal.actor_id = v_nominee_identity_id THEN
        IF NEW.action <> 'WITHDRAW' OR previous_action IS NOT NULL THEN
            RAISE EXCEPTION 'S2B self-proposal consent history refused'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF (previous_action IS NULL AND NEW.action NOT IN ('CONSENT', 'DECLINE'))
       OR (previous_action = 'CONSENT' AND NEW.action <> 'WITHDRAW') THEN
        RAISE EXCEPTION 'S2B office consent sequence refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT * INTO decision_row FROM public.core_authoritydecision
     WHERE decision_reference = NEW.authority_decision_reference;
    IF decision_row.decision_reference IS NULL
       OR decision_row.outcome <> 'QUALIFIED'
       OR decision_row.action <> 'RECORD_OFFICE_CONSENT'
       OR decision_row.command_subtype <> 'RECORD_OFFICE_CONSENT'
       OR decision_row.actor_id <> NEW.actor_id
    OR decision_row.actor_access_epoch <> NEW.actor_access_epoch
    OR decision_row.target->>'assignment_uuid' IS DISTINCT FROM v_assignment_uuid::text
       OR decision_row.target->>'proposal_event_uuid' IS DISTINCT FROM proposal.event_uuid::text
    OR decision_row.target->>'membership_uuid' IS DISTINCT FROM v_membership_uuid::text
       OR decision_row.target->>'event_action' IS DISTINCT FROM NEW.action
       OR decision_row.authority_reference IS NULL
       OR decision_row.authority_reference NOT LIKE 's015a1:%' THEN
        RAISE EXCEPTION 'S2B office consent authority decision refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    reference_facts := substring(decision_row.authority_reference FROM 8)::jsonb;
        IF reference_facts->>'a' IS DISTINCT FROM v_assignment_uuid::text
          OR reference_facts->>'g' IS DISTINCT FROM role_uuid::text
          OR reference_facts->>'p' IS DISTINCT FROM proposal.event_uuid::text
          OR reference_facts->>'u' IS DISTINCT FROM v_membership_uuid::text
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_contextualroleassignment', 'reference', v_assignment_uuid::text
          ))
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_contextualroleassignmentstateevent', 'reference', proposal.event_uuid::text
          ))
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_organismmembership', 'reference', v_membership_uuid::text
          ))
          OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
              'relation', 'core_organismroledefinition', 'reference', role_uuid::text
          )) THEN
        RAISE EXCEPTION 'S2B office consent governed references refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


ASSIGN_CONSENT_SQL = """
CREATE FUNCTION public.s2b_guard_assign_consent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_assignment_uuid uuid; v_membership_uuid uuid; v_nominee_identity_id bigint;
    role_uuid uuid; role_code text; role_scope text;
        proposal record; consent_uuid uuid; consent_reference text; consent_relation text;
        decision_row record; reference_facts jsonb;
BEGIN
    IF NEW.action <> 'ASSIGN' OR NEW.actor_state <> 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;
            SELECT a.assignment_uuid, m.membership_uuid, m.identity_id, d.role_uuid, d.code, d.scope
                INTO v_assignment_uuid, v_membership_uuid, v_nominee_identity_id, role_uuid, role_code, role_scope
      FROM public.core_contextualroleassignment a
      JOIN public.core_organismmembership m ON m.id = a.membership_id
            JOIN public.core_organismroledefinition d ON d.id = a.role_definition_id
     WHERE a.id = NEW.assignment_id;
    IF v_assignment_uuid IS NOT NULL
       AND (role_code IS DISTINCT FROM 'COORDINATOR'
            OR role_scope IS DISTINCT FROM 'LIVING_ORGANISM') THEN
        RETURN NEW;
    END IF;
    SELECT event_uuid, actor_id INTO proposal
      FROM public.core_contextualroleassignmentstateevent
     WHERE assignment_id = NEW.assignment_id AND sequence = 1 AND action = 'PROPOSE';
    IF proposal.event_uuid IS NULL THEN
        RAISE EXCEPTION 'S2B assignment consent refused: nomination missing'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF proposal.actor_id = v_nominee_identity_id THEN
        consent_reference := proposal.event_uuid::text;
        consent_relation := 'core_contextualroleassignmentstateevent';
        IF EXISTS (
            SELECT 1 FROM public.core_officeappointmentconsent c
             WHERE c.assignment_id = NEW.assignment_id AND c.action = 'WITHDRAW'
               AND c.effective_at <= NEW.effective_at AND c.recorded_at <= NEW.recorded_at
        ) THEN
            RAISE EXCEPTION 'S2B assignment consent refused: self-proposal withdrawn'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        SELECT c.consent_uuid INTO consent_uuid
          FROM public.core_officeappointmentconsent c
         WHERE c.assignment_id = NEW.assignment_id AND c.action = 'CONSENT'
           AND c.effective_at <= NEW.effective_at AND c.recorded_at <= NEW.recorded_at
           AND NOT EXISTS (
                SELECT 1 FROM public.core_officeappointmentconsent terminal
                 WHERE terminal.assignment_id = NEW.assignment_id
                   AND terminal.ordinal > c.ordinal
                   AND terminal.action IN ('DECLINE', 'WITHDRAW')
                   AND terminal.effective_at <= NEW.effective_at
                   AND terminal.recorded_at <= NEW.recorded_at
           )
         ORDER BY c.ordinal DESC LIMIT 1;
        IF consent_uuid IS NULL THEN
            RAISE EXCEPTION 'S2B assignment consent refused: current consent absent or withdrawn'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        consent_reference := consent_uuid::text;
        consent_relation := 'core_officeappointmentconsent';
    END IF;
    SELECT * INTO decision_row FROM public.core_authoritydecision
     WHERE decision_reference = NEW.authority_decision_reference;
    IF decision_row.decision_reference IS NULL
       OR decision_row.authority_reference IS NULL
       OR decision_row.authority_reference NOT LIKE 's015a1:%' THEN
        RAISE EXCEPTION 'S2B assignment consent decision reference refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    reference_facts := substring(decision_row.authority_reference FROM 8)::jsonb;
     IF reference_facts->>'a' IS DISTINCT FROM v_assignment_uuid::text
         OR reference_facts->>'g' IS DISTINCT FROM role_uuid::text
       OR reference_facts->>'p' IS DISTINCT FROM proposal.event_uuid::text
      OR reference_facts->>'u' IS DISTINCT FROM (
          CASE WHEN consent_relation = 'core_officeappointmentconsent' THEN 'c:' ELSE 'e:' END
          || consent_reference
      )
       OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
          'relation', 'core_contextualroleassignment', 'reference', v_assignment_uuid::text
       ))
       OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
            'relation', 'core_contextualroleassignmentstateevent', 'reference', proposal.event_uuid::text
       ))
       OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
            'relation', consent_relation, 'reference', consent_reference
      ))
      OR NOT decision_row.facts_read @> jsonb_build_array(jsonb_build_object(
          'relation', 'core_organismroledefinition', 'reference', role_uuid::text
       )) THEN
        RAISE EXCEPTION 'S2B assignment consent fact disclosure refused'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S2B office appointment consent requires PostgreSQL")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex(CONSENT_SQL)
    ex(ASSIGN_CONSENT_SQL)
    ex(
        "CREATE TRIGGER s2b_office_consent_insert BEFORE INSERT ON public.core_officeappointmentconsent "
        "FOR EACH ROW EXECUTE FUNCTION public.s2b_guard_office_consent()"
    )
    ex(
        "CREATE TRIGGER s2b_office_consent_append_only BEFORE UPDATE OR DELETE ON public.core_officeappointmentconsent "
        "FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()"
    )
    ex(
        "CREATE TRIGGER s015_0037_officeappointmentconsent_truncate BEFORE TRUNCATE ON public.core_officeappointmentconsent "
        "FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()"
    )
    ex(
        "CREATE TRIGGER s2b_20_assign_consent BEFORE INSERT ON public.core_contextualroleassignmentstateevent "
        "FOR EACH ROW EXECUTE FUNCTION public.s2b_guard_assign_consent()"
    )


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S2B office appointment consent requires PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM public.core_officeappointmentconsent) "
            "OR EXISTS (SELECT 1 FROM public.core_contextualroleassignmentstateevent "
            "WHERE actor_state = 'LIVING_ORGANISM' AND action = 'ASSIGN')"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError("S2B office consent reverse refused: consent or LO appointments exist")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex("DROP TRIGGER s2b_20_assign_consent ON public.core_contextualroleassignmentstateevent")
    ex("DROP TRIGGER s015_0037_officeappointmentconsent_truncate ON public.core_officeappointmentconsent")
    ex("DROP TRIGGER s2b_office_consent_append_only ON public.core_officeappointmentconsent")
    ex("DROP TRIGGER s2b_office_consent_insert ON public.core_officeappointmentconsent")
    ex("DROP FUNCTION public.s2b_guard_assign_consent()")
    ex("DROP FUNCTION public.s2b_guard_office_consent()")


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0036_s2b_role_attribution_guards")]

    operations = [
        migrations.CreateModel(
            name="OfficeAppointmentConsent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("consent_uuid", models.UUIDField(default=__import__("uuid").uuid4, editable=False, unique=True)),
                ("action", models.CharField(choices=[("CONSENT", "CONSENT"), ("DECLINE", "DECLINE"), ("WITHDRAW", "WITHDRAW")], max_length=8)),
                ("ordinal", models.PositiveIntegerField()),
                ("actor_state", models.CharField(default="PARTY", editable=False, max_length=15)),
                ("actor_capacity", models.CharField(default="STANDING", editable=False, max_length=16)),
                ("actor_access_epoch", models.PositiveBigIntegerField()),
                ("authority_decision_reference", models.CharField(max_length=71)),
                ("request_reference", models.CharField(max_length=128)),
                ("idempotency_key", models.CharField(max_length=120)),
                ("occurred_at", models.DateTimeField()),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("actor", models.ForeignKey(on_delete=PROTECT, related_name="office_appointment_consents", to="core.identity")),
                ("assignment", models.ForeignKey(on_delete=PROTECT, related_name="appointment_consents", to="core.contextualroleassignment")),
                ("enterer", models.ForeignKey(on_delete=PROTECT, related_name="entered_office_appointment_consents", to="core.identity")),
                ("nominee_membership", models.ForeignKey(on_delete=PROTECT, related_name="office_appointment_consents", to="core.organismmembership")),
                ("proposal_event", models.ForeignKey(on_delete=PROTECT, related_name="appointment_consents", to="core.contextualroleassignmentstateevent")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("assignment", "ordinal"), name="s2b_office_consent_ordinal_uniq"),
                    models.UniqueConstraint(fields=("assignment", "idempotency_key"), name="s2b_office_consent_idem_uniq"),
                    models.CheckConstraint(condition=models.Q(action__in=["CONSENT", "DECLINE", "WITHDRAW"]), name="s2b_office_consent_action_ck"),
                    models.CheckConstraint(condition=models.Q(actor_state="PARTY", actor_capacity="STANDING"), name="s2b_office_consent_actor_ck"),
                    models.CheckConstraint(condition=models.Q(authority_decision_reference__regex=r"^s015d1:[0-9a-f]{64}$"), name="s2b_office_consent_decision_ck"),
                    models.CheckConstraint(condition=models.Q(occurred_at__lte=models.F("received_at")), name="s2b_office_consent_occurred_ck"),
                    models.CheckConstraint(condition=(models.Q(recorded_at__isnull=True) | models.Q(received_at__lte=models.F("recorded_at"))), name="s2b_office_consent_recorded_ck"),
                ],
            },
        ),
        migrations.RunPython(install, reverse),
    ]