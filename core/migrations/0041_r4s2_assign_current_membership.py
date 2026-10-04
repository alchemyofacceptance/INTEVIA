"""S015 PKT-B R4 slice 2 (the Human Governor's decision, 4 Oct 2026): a Learner who has left the LO cannot be appointed.

An ASSIGN row on the office chain is admitted only if the office's membership folds ACTIVE at the row's record time
(known at record time) AND at the row's effective_at (known at record time). Earlier eligibility, or a supplied earlier
effective_at, does not satisfy the guard once the departure is on the record. The condition is a function so it can be
witnessed directly; the trigger is BEFORE INSERT and fires after 0021's record-time trigger (name order).

Historical-record questions (a backdated appointment recorded against a still-current member; cross-chain ordering)
are outside this guard: it admits a backdated ASSIGN for a member who has not departed.

New objects only (SR-2): no installed function or guard body is replaced. The reverse drops the guard and restores the
previous behaviour, including its known defect (F-R4S1-1, F-R4S1-2); it restores no operational clearance.

DEPENDENCY: `LEAF` is the leaf migration at the accepted commit head; the file number follows it. Nothing else in this
file depends on it.
"""
from django.db import migrations

LEAF = "0040_s2b_role_actor_nullable_state"

FUNCTIONS_SQL = """
CREATE FUNCTION public.s015_r4s2_assign_membership_current(p_assignment bigint, p_effective_at timestamptz, p_known_at timestamptz)
RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE v_membership bigint; v_proposal_recorded_at timestamptz;
BEGIN
    SELECT a.membership_id, proposal.recorded_at
      INTO v_membership, v_proposal_recorded_at
      FROM public.core_contextualroleassignment a
      LEFT JOIN public.core_contextualroleassignmentstateevent proposal
        ON proposal.assignment_id = a.id
       AND proposal.sequence = 1
       AND proposal.action = 'PROPOSE'
     WHERE a.id = p_assignment;
    IF v_membership IS NULL THEN RETURN FALSE; END IF;
    RETURN public.s015_fold_effective_state('core_organismmembership', v_membership, p_known_at, p_known_at) = 'ACTIVE'
       AND public.s015_fold_effective_state('core_organismmembership', v_membership, p_effective_at, p_known_at) = 'ACTIVE'
       AND (
            v_proposal_recorded_at IS NULL
            OR NOT EXISTS (
            SELECT 1
              FROM public.core_organismmembershiptransition departure
             WHERE departure.membership_id = v_membership
               AND departure.resulting_state = 'ENDED'
               AND departure.recorded_at > v_proposal_recorded_at
            )
       );
END $$;

CREATE FUNCTION public.s015_r4s2_guard_assign_current_membership() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_known timestamptz;
BEGIN
    IF NEW.action <> 'ASSIGN' THEN RETURN NEW; END IF;
    v_known := coalesce(NEW.recorded_at, statement_timestamp());
    IF NOT public.s015_r4s2_assign_membership_current(NEW.assignment_id, NEW.effective_at, v_known) THEN
        RAISE EXCEPTION 'S015 R4S2: ASSIGN requires the office''s membership to be ACTIVE at record time (assignment %)', NEW.assignment_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER s015_r4s2_assign_current_membership BEFORE INSERT ON public.core_contextualroleassignmentstateevent
    FOR EACH ROW EXECUTE FUNCTION public.s015_r4s2_guard_assign_current_membership();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS s015_r4s2_assign_current_membership ON public.core_contextualroleassignmentstateevent;
DROP FUNCTION IF EXISTS public.s015_r4s2_guard_assign_current_membership();
DROP FUNCTION IF EXISTS public.s015_r4s2_assign_membership_current(bigint, timestamptz, timestamptz);
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0038 requires PostgreSQL")
    schema_editor.execute(FUNCTIONS_SQL, params=None)


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0038 requires PostgreSQL")
    schema_editor.execute(REVERSE_SQL, params=None)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", LEAF)]
    operations = [migrations.RunPython(install, reverse)]
