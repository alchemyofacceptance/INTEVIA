"""C1b: bind conditions to evidence already committed in their first event.

A read-only view avoids changing an anchor or any historical event bytes.
Widen command intake in the same atomic migration. No new trigger or seed.
"""

from importlib import import_module

from django.db import migrations, models

SR = import_module("core.migrations.0051_sr_role_definitions")
SBG = import_module("core.migrations.0045_sbg_governance_formation")
ENTRY = "TRANSITION_MEMBERSHIP_CONDITION"

PERIOD = """
CREATE FUNCTION public.c1b_period_at(bigint, timestamptz, timestamptz) RETURNS uuid
LANGUAGE sql STABLE STRICT AS $$
    WITH eligible AS (
        SELECT * FROM public.core_organismmembershiptransition
         WHERE membership_id=$1 AND effective_at <= $2 AND recorded_at <= $3 AND received_at <= $3
    ), head AS (
        SELECT * FROM eligible ORDER BY effective_at DESC, occurred_at DESC, received_at DESC,
            recorded_at DESC, sequence DESC, event_uuid DESC LIMIT 1
    )
    SELECT start.event_uuid FROM eligible start, head
     WHERE head.resulting_state IN ('ACTIVE','PROBATIONARY') AND start.sequence <= head.sequence
       AND ((start.sequence=1 AND start.resulting_state IN ('PROPOSED','ACTIVE','PROBATIONARY')) OR (start.prior_state='ENDED' AND start.resulting_state='PROPOSED'))
       AND NOT EXISTS (SELECT 1 FROM eligible e WHERE e.resulting_state='ENDED'
                       AND e.sequence BETWEEN start.sequence AND head.sequence)
     ORDER BY start.sequence DESC LIMIT 1
$$;
CREATE VIEW public.c1b_condition_period AS
    SELECT c.id AS condition_id,
           public.c1b_period_at(c.subject_membership_id, e.effective_at, e.recorded_at) AS subject_start,
           public.c1b_period_at(c.mentor_membership_id, e.effective_at, e.recorded_at) AS mentor_start
      FROM public.core_membershipcondition c
      LEFT JOIN public.core_membershipconditionstateevent e ON e.condition_id=c.id AND e.sequence=1;
"""

# Extend the existing deferred completeness check only on the confirmed route.
# Do not alter the historical U3 condition, trigger binding or timing.
OLD = SBG._MEMBERSHIP_END_COMPLETENESS_BODY
EXTRA = """
    IF NEW.actor_state='PARTY' AND NEW.prior_state IN ('ACTIVE','PROBATIONARY') THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.core_authoritydecision d
            JOIN public.core_organismmembership m ON m.id=NEW.membership_id
            JOIN public.core_commandintake i ON i.actor_id=d.actor_id AND i.action=d.action
                                             AND i.idempotency_key=d.idempotency_key
            WHERE d.decision_reference=NEW.authority_decision_reference AND d.outcome='QUALIFIED'
              AND NEW.action='TRANSITION_MEMBERSHIP' AND d.action=NEW.action AND d.command_subtype=NEW.action
              AND NEW.actor_id=m.identity_id AND d.actor_id=NEW.actor_id AND i.entry=NEW.action
              AND i.request_reference=NEW.request_reference AND i.received_at=NEW.received_at
              AND i.request_fingerprint=d.target->>'intake_fingerprint'
              AND i.actor_access_epoch=NEW.actor_access_epoch AND d.actor_access_epoch=NEW.actor_access_epoch
              AND d.request_reference=NEW.request_reference AND d.idempotency_key=NEW.idempotency_key
              AND d.effective_at=NEW.effective_at AND d.evaluated_at=NEW.received_at
              AND d.target->>'membership_uuid'=m.membership_uuid::text AND d.target->>'resulting_state'='ENDED'
              AND d.target->'confirmed'='true'::jsonb AND d.authority_reference=NEW.evidence_reference
        ) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_CONFIRMATION_REQUIRED' USING ERRCODE='integrity_constraint_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM public.core_contextualroleassignment a
                   JOIN public.core_contextualroleassignmentstateevent h ON h.id=a.head_state_event_id
                   WHERE a.membership_id=NEW.membership_id AND
                       (h.resulting_state NOT IN ('PROPOSED','ENDED') OR h.effective_at>NEW.effective_at)) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_OFFICE_INCOMPLETE' USING ERRCODE='integrity_constraint_violation';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.core_membershipcondition c
            JOIN public.c1b_condition_period b ON b.condition_id=c.id
            JOIN public.core_membershipconditionstateevent h ON h.id=c.head_state_event_id
            WHERE (c.subject_membership_id=NEW.membership_id
                   AND b.subject_start=public.c1b_period_at(NEW.membership_id,NEW.effective_at,NEW.received_at)
                   AND h.resulting_state NOT IN ('ENDED','SUPERSEDED','DECLINED','OFFER_CLOSED'))
               OR (c.mentor_membership_id=NEW.membership_id
                   AND b.mentor_start=public.c1b_period_at(NEW.membership_id,NEW.effective_at,NEW.received_at)
                   AND (h.resulting_state NOT IN ('ENDED','SUPERSEDED','DECLINED','OFFER_CLOSED','MENTOR_UNRESOLVED')
                        OR (h.prior_state='OFFERED' AND h.resulting_state='MENTOR_UNRESOLVED')))
        ) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_CONDITION_INCOMPLETE' USING ERRCODE='integrity_constraint_violation';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.core_organismmembership other JOIN public.core_organismmembership m
              ON m.living_organism_id=other.living_organism_id WHERE m.id=NEW.membership_id AND other.id<>m.id
              AND public.s015_fold_effective_state('core_organismmembership',other.id,NEW.effective_at,
                                                    clock_timestamp()) IN ('ACTIVE','PROBATIONARY')
        ) AND NOT EXISTS (
            SELECT 1 FROM public.core_livingorganismevent e JOIN public.core_organismmembership m
              ON m.living_organism_id=e.living_organism_id WHERE m.id=NEW.membership_id
              AND e.resulting_state='CLOSED' AND e.effective_at=NEW.effective_at
              AND e.evidence_reference='membership-ending:'||NEW.event_uuid::text
        ) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_DISSOLUTION_INCOMPLETE' USING ERRCODE='integrity_constraint_violation';
        END IF;
    END IF;
"""
marker = "    RETURN NEW;\nEND"
assert OLD.count(marker) == 1
NEW = OLD.replace(marker, EXTRA + marker)


def replace_guard(schema_editor, before, after):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT prosrc FROM pg_proc WHERE oid='public.sbg_guard_membership_end_completeness()'::regprocedure"
        )
        if cursor.fetchone()[0].strip() != before.strip():
            raise RuntimeError(
                "C1b stopped: installed departure guard differs from the expected body."
            )
    schema_editor.execute(
        "CREATE OR REPLACE FUNCTION public.sbg_guard_membership_end_completeness() "
        "RETURNS trigger LANGUAGE plpgsql AS $$" + after + "$$",
        params=None,
    )


def install(apps, schema_editor):
    replace_guard(schema_editor, OLD, NEW)


def reverse(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """SELECT EXISTS(SELECT 1 FROM public.core_commandintake WHERE entry=%s)
                          OR EXISTS(SELECT 1 FROM public.core_authoritydecision WHERE action='TRANSITION_MEMBERSHIP' AND target ? 'confirmed')""",
            [ENTRY],
        )
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "C1b reversal refused: governed C1b requests or decisions exist. Nothing was removed."
            )
    replace_guard(schema_editor, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [("core", "0053_sr_office_safeguards")]
    operations = [
        migrations.AlterField(
            model_name="commandintake",
            name="entry",
            field=models.CharField(
                choices=SR.ENTRY_CHOICES + [(ENTRY, "Transition membership condition")],
                max_length=40,
            ),
        ),
        migrations.RemoveConstraint(model_name="commandintake", name=SR.INTAKE_CHECK),
        migrations.AddConstraint(
            model_name="commandintake",
            constraint=models.CheckConstraint(
                condition=models.Q(entry__in=[*SR.ENTRY_VALUES, ENTRY]),
                name=SR.INTAKE_CHECK,
            ),
        ),
        migrations.RunSQL(
            PERIOD,
            "DROP VIEW public.c1b_condition_period; "
            "DROP FUNCTION public.c1b_period_at(bigint,timestamptz,timestamptz);",
        ),
        migrations.RunPython(install, reverse),
    ]
