from django.db import migrations


CAPACITY_SQL = """
CREATE FUNCTION public.s2b_guard_lo_capacity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo_id bigint;
BEGIN
    IF NEW.actor_state <> 'LIVING_ORGANISM' THEN RETURN NEW; END IF;
    SELECT m.living_organism_id INTO lo_id
      FROM public.core_contextualroleassignment a
      JOIN public.core_organismmembership m ON m.id = a.membership_id
     WHERE a.id = NEW.assignment_id;
    IF lo_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.core_livingorganismevent founding
        JOIN public.core_livingorganism lo ON lo.id = founding.living_organism_id
        WHERE lo.id = lo_id AND lo.founding_authority_basis_id IS NOT NULL
          AND lo.founder_identity_id IS NOT NULL AND founding.sequence = 1
          AND founding.effective_at <= NEW.effective_at
          AND founding.received_at <= NEW.recorded_at
          AND founding.recorded_at <= NEW.recorded_at
    ) THEN
        RAISE EXCEPTION 'S2B LO capacity refused: no founding record'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.core_livingorganismevent closed
        WHERE closed.living_organism_id = lo_id AND closed.resulting_state = 'CLOSED'
          AND closed.effective_at < NEW.effective_at
          AND closed.received_at <= NEW.recorded_at
          AND closed.recorded_at <= NEW.recorded_at
    ) THEN
        RAISE EXCEPTION 'S2B LO capacity refused: dissolved'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


ATTRIBUTION_SQL = """
CREATE FUNCTION public.s2b_guard_office_attribution() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo_id bigint; lo_uuid uuid; founding_xid xid8; membership_uuid uuid;
                membership_identity_id bigint; role_uuid uuid; role_code text; role_scope text; decision_row record;
BEGIN
        SELECT lo.id, lo.organism_id, lo.founding_insert_xid, m.membership_uuid,
                     m.identity_id, d.role_uuid, d.code, d.scope
            INTO lo_id, lo_uuid, founding_xid, membership_uuid,
                     membership_identity_id, role_uuid, role_code, role_scope
      FROM public.core_contextualroleassignment a
      JOIN public.core_organismmembership m ON m.id = a.membership_id
      JOIN public.core_livingorganism lo ON lo.id = m.living_organism_id
      JOIN public.core_organismroledefinition d ON d.id = a.role_definition_id
     WHERE a.id = NEW.assignment_id;
    IF lo_uuid IS NULL THEN
        RAISE EXCEPTION 'S2B office attribution refused: assignment context missing'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF role_code IS DISTINCT FROM 'COORDINATOR'
       OR role_scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;
    IF NEW.action = 'PROPOSE' THEN
        IF NEW.prior_state IS NOT NULL OR NEW.resulting_state IS DISTINCT FROM 'PROPOSED'
           OR NEW.actor_state IS DISTINCT FROM 'PARTY'
           OR NEW.actor_id IS DISTINCT FROM NEW.enterer_identity_id
           OR NEW.actor_access_epoch IS NULL THEN
            RAISE EXCEPTION 'S2B office attribution refused: PROPOSE must be the proposers act'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action IN ('ASSIGN', 'END', 'REASSIGN') THEN
        IF NEW.action = 'ASSIGN'
           AND NEW.sequence = 1
           AND NEW.prior_state IS NULL
           AND NEW.resulting_state = 'ACTIVE'
           AND role_code = 'COORDINATOR'
           AND NEW.actor_state = 'PARTY'
           AND NEW.actor_capacity = 'STANDING'
           AND NEW.actor_id = membership_identity_id
           AND NEW.actor_id = NEW.enterer_identity_id
           AND NEW.actor_access_epoch IS NOT NULL
           AND NEW.entry_mode = 'TRANSPORT'
           AND NEW.composing_rule_state = 'NA_TRANSPORT'
           AND NEW.composer_state = 'NA_TRANSPORT'
           AND founding_xid = pg_catalog.pg_current_xact_id()
           AND EXISTS (
               SELECT 1 FROM public.core_livingorganismevent founding
                WHERE founding.living_organism_id = lo_id
                  AND founding.sequence = 1
                  AND founding.xmin = founding_xid::xid
                  AND founding.actor_id = NEW.actor_id
                  AND founding.actor_access_epoch = NEW.actor_access_epoch
                  AND founding.authority_basis_id = NEW.authority_basis_id
                  AND founding.actor_state = 'PARTY'
           ) THEN
            RETURN NEW;
        END IF;
        IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
           OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
           OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
           OR NEW.enterer_identity_id IS NULL
           OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
           OR NEW.composing_rule_state IS DISTINCT FROM 'NA_TRANSPORT'
           OR NEW.composer_state IS DISTINCT FROM 'NA_TRANSPORT'
           OR (NEW.action = 'ASSIGN' AND (NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'))
           OR (NEW.action IN ('END', 'REASSIGN') AND (NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'ENDED')) THEN
            RAISE EXCEPTION 'S2B office attribution refused: LO decision attribution required'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        RAISE EXCEPTION 'S2B office attribution refused: unmapped office act'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT * INTO decision_row FROM public.core_authoritydecision
     WHERE decision_reference = NEW.authority_decision_reference;
    IF decision_row.decision_reference IS NULL
       OR decision_row.outcome <> 'QUALIFIED'
       OR decision_row.action <> 'TRANSITION_CONTEXTUAL_ROLE'
    OR decision_row.command_subtype <> 'TRANSITION_CONTEXTUAL_ROLE'
       OR decision_row.actor_id <> NEW.enterer_identity_id
       OR decision_row.authority_reference IS DISTINCT FROM NEW.evidence_reference
       OR decision_row.target->>'living_organism_id' IS DISTINCT FROM lo_uuid::text
       OR decision_row.target->>'event_action' IS DISTINCT FROM NEW.action
       OR (NEW.action = 'PROPOSE' AND (
            decision_row.target->>'membership_uuid' IS DISTINCT FROM membership_uuid::text
            OR decision_row.target->>'role_definition_uuid' IS DISTINCT FROM role_uuid::text
       ))
       OR (NEW.action <> 'PROPOSE' AND
            decision_row.target->>'assignment_uuid' IS DISTINCT FROM (
                SELECT assignment_uuid::text FROM public.core_contextualroleassignment WHERE id = NEW.assignment_id
            )) THEN
        RAISE EXCEPTION 'S2B office attribution refused: governed decision does not match event'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


MEMBERSHIP_END_SQL = """
CREATE FUNCTION public.s2b_guard_membership_proposed_end_actor() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE member_identity_id bigint;
BEGIN
    IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ENDED'
       OR NEW.actor_state IS DISTINCT FROM 'PARTY' THEN
        RETURN NEW;
    END IF;
    SELECT identity_id INTO member_identity_id FROM public.core_organismmembership
     WHERE id = NEW.membership_id;
    IF member_identity_id IS NULL OR NEW.actor_id IS DISTINCT FROM member_identity_id THEN
        RAISE EXCEPTION 'S2B membership ending refused: PARTY actor must be the proposed member'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S2B role attribution guards require PostgreSQL")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex(CAPACITY_SQL)
    ex(ATTRIBUTION_SQL)
    ex(MEMBERSHIP_END_SQL)
    ex(
        "CREATE TRIGGER s2b_00_capacity BEFORE INSERT ON public.core_contextualroleassignmentstateevent "
        "FOR EACH ROW EXECUTE FUNCTION public.s2b_guard_lo_capacity()"
    )
    ex(
        "CREATE TRIGGER s2b_10_attribution BEFORE INSERT ON public.core_contextualroleassignmentstateevent "
        "FOR EACH ROW EXECUTE FUNCTION public.s2b_guard_office_attribution()"
    )
    ex(
        "CREATE TRIGGER s2b_10_membership_proposed_end BEFORE INSERT ON public.core_organismmembershiptransition "
        "FOR EACH ROW EXECUTE FUNCTION public.s2b_guard_membership_proposed_end_actor()"
    )


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S2B role attribution guards require PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM public.core_contextualroleassignmentstateevent "
            "WHERE actor_state = 'LIVING_ORGANISM'"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError("S2B role attribution reverse refused: LO-actor office events exist")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex("DROP TRIGGER s2b_10_membership_proposed_end ON public.core_organismmembershiptransition")
    ex("DROP TRIGGER s2b_10_attribution ON public.core_contextualroleassignmentstateevent")
    ex("DROP TRIGGER s2b_00_capacity ON public.core_contextualroleassignmentstateevent")
    ex("DROP FUNCTION public.s2b_guard_membership_proposed_end_actor()")
    ex("DROP FUNCTION public.s2b_guard_office_attribution()")
    ex("DROP FUNCTION public.s2b_guard_lo_capacity()")


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0035_s015_role_chain_actor_capacity")]

    operations = [migrations.RunPython(install, reverse)]