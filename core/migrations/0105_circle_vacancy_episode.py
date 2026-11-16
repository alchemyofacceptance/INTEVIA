"""Read the cause and elapsed recovery period of a GENERIC Circle at two historical cuts."""

import json

from django.db import migrations

VACANT = "public.circle_vacant(bigint,uuid,timestamptz,timestamptz)"
EPISODE = "public.circle_episode(bigint,timestamptz,timestamptz)"
EFFECTIVE = "public.circle_effective_state(bigint,timestamptz,timestamptz)"
BEFORE_ASSIGN = "public.circle_episode_before_assignment(bigint,uuid,timestamptz,timestamptz)"

# Unmodified pg_get_functiondef from the 0104 PG17 catalogue (including its final LF).
VACANT_0104 = """CREATE OR REPLACE FUNCTION public.circle_vacant(c bigint, incoming uuid, at_time timestamp with time zone, known timestamp with time zone)
 RETURNS boolean
 LANGUAGE sql
 STABLE
AS $function$
 SELECT EXISTS(SELECT FROM public.core_circle x JOIN public.core_livingorganism lo ON lo.id=x.parent_organism_id
    WHERE x.id=c AND x.kind='GENERIC' AND (NOT lo.platform_root OR public.r_circle_root_qualified(lo.id,at_time,known))
    AND public.s015_fold_effective_state('core_livingorganism',lo.id,at_time,known)='ACTIVE'
    AND public.s015_fold_effective_state('core_circle',c,at_time,known)='ACTIVE'
    AND EXISTS(SELECT FROM public.core_circleparticipation p WHERE p.circle_id=c
               AND public.circle_place_at(c,p.membership_id,at_time,known))
    AND NOT EXISTS(SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d
       ON d.id=a.role_definition_id AND d.scope='CIRCLE' AND d.code='COORDINATOR'
       WHERE a.circle_id=c AND a.assignment_uuid IS DISTINCT FROM incoming
       AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,at_time,known)='ACTIVE'))
$function$
"""
RAW_ACTIVE = "public.s015_fold_effective_state('core_circle',c,at_time,known)='ACTIVE'"
VACANT_0105 = VACANT_0104.replace(
    RAW_ACTIVE,
    RAW_ACTIVE + """
    AND NOT EXISTS (SELECT FROM public.circle_episode_before_assignment(c,incoming,at_time,known)
                    WHERE status='EXPIRED')""",
)

# The incoming assignment is excluded only for the existing office guard's pre-insert reading.
# Normal reads always include all assignments. Choose one causal END at a simultaneous loss.
CAUSE_DEFINITION = """CREATE OR REPLACE FUNCTION public.circle_episode_before_assignment(c bigint, incoming uuid, at_time timestamp with time zone, known timestamp with time zone)
 RETURNS TABLE(cause uuid, loss timestamp with time zone, deadline timestamp with time zone, status text)
 LANGUAGE sql
 STABLE
AS $function$
 WITH offices AS MATERIALIZED (
   SELECT a.id FROM public.core_contextualroleassignment a
   JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
   WHERE a.circle_id=c AND d.scope='CIRCLE' AND d.code='COORDINATOR'
     AND a.assignment_uuid IS DISTINCT FROM incoming
 ), last_loss AS (
   SELECT e.event_uuid, e.effective_at AS lost
   FROM offices o JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=o.id
   WHERE e.action='END' AND e.prior_state='ACTIVE' AND e.resulting_state='ENDED'
     AND e.effective_at<=at_time AND e.received_at<=known AND e.recorded_at<=known
     AND (SELECT prior.resulting_state FROM public.core_contextualroleassignmentstateevent prior
          WHERE prior.assignment_id=o.id AND prior.effective_at<e.effective_at
            AND prior.received_at<=known AND prior.recorded_at<=known
          ORDER BY prior.effective_at DESC, prior.occurred_at DESC, prior.received_at DESC,
                   prior.recorded_at DESC, prior.sequence DESC, prior.event_uuid DESC LIMIT 1)='ACTIVE'
   ORDER BY e.effective_at DESC,e.occurred_at DESC,e.received_at DESC,e.recorded_at DESC,
            e.sequence DESC,e.event_uuid DESC LIMIT 1
 ), period AS (
   SELECT event_uuid,lost,lost+interval '720 hours' AS fixed_end FROM last_loss
 )
 SELECT p.event_uuid,p.lost,p.fixed_end,CASE WHEN at_time<p.fixed_end THEN 'OPEN' ELSE 'EXPIRED' END
 FROM period p JOIN public.core_circle x ON x.id=c
 JOIN public.core_livingorganism lo ON lo.id=x.parent_organism_id
 WHERE x.kind='GENERIC' AND (NOT lo.platform_root OR public.r_circle_root_qualified(lo.id,p.lost,known))
   AND public.s015_fold_effective_state('core_livingorganism',lo.id,p.lost,known)='ACTIVE'
   AND public.s015_fold_effective_state('core_circle',c,p.lost,known)='ACTIVE'
   AND EXISTS (SELECT FROM public.core_circleparticipation place WHERE place.circle_id=c
               AND public.circle_place_at(c,place.membership_id,p.lost,known))
   AND EXISTS (SELECT FROM public.core_circleparticipation place WHERE place.circle_id=c
               AND public.circle_place_at(c,place.membership_id,at_time,known))
   AND NOT EXISTS (SELECT FROM offices o WHERE
     public.s015_fold_effective_state('core_contextualroleassignment',o.id,p.lost,known)='ACTIVE'
     OR public.s015_fold_effective_state('core_contextualroleassignment',o.id,at_time,known)='ACTIVE')
   AND NOT EXISTS (SELECT FROM offices o JOIN public.core_contextualroleassignmentstateevent a
     ON a.assignment_id=o.id WHERE a.action='ASSIGN' AND a.resulting_state='ACTIVE'
     AND a.effective_at>p.lost AND a.effective_at<=at_time AND a.received_at<=known AND a.recorded_at<=known)
   AND (public.s015_fold_effective_state('core_circle',c,at_time,known)='ACTIVE'
     OR (public.s015_fold_effective_state('core_circle',c,at_time,known)='DORMANT'
       AND EXISTS (SELECT FROM public.core_circlestateevent v WHERE v.circle_id=c
         AND v.action='BECOME_DORMANT' AND v.evidence_reference='vacancy:'||p.event_uuid
         AND v.effective_at<=at_time AND v.received_at<=known AND v.recorded_at<=known)))
$function$
"""

EPISODE_DEFINITION = """CREATE OR REPLACE FUNCTION public.circle_episode(c bigint, at_time timestamp with time zone, known timestamp with time zone)
 RETURNS TABLE(cause uuid, loss timestamp with time zone, deadline timestamp with time zone, status text)
 LANGUAGE sql
 STABLE
AS $function$
 SELECT * FROM public.circle_episode_before_assignment(c,NULL,at_time,known)
$function$
"""

STATE_DEFINITION = """CREATE OR REPLACE FUNCTION public.circle_effective_state(c bigint, at_time timestamp with time zone, known timestamp with time zone)
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
 SELECT CASE WHEN EXISTS (SELECT FROM public.circle_episode(c,at_time,known) WHERE status='EXPIRED')
   THEN 'DORMANT' ELSE public.s015_fold_effective_state('core_circle',c,at_time,known) END
$function$
"""

REDUCE_0104 = """CREATE OR REPLACE FUNCTION public.s015_reduce_projection(p_token text, p_event_ids bigint[])
 RETURNS text
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE m record; v_state text;
BEGIN
    IF p_event_ids IS NULL OR cardinality(p_event_ids) = 0 THEN RETURN NULL; END IF;
    SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = p_token;
    EXECUTE format('SELECT %I FROM public.%I WHERE id = $1', m.state_col, m.event_table)
        INTO v_state USING p_event_ids[cardinality(p_event_ids)];
    RETURN v_state;
END $function$
"""
REDUCE = "public.s015_reduce_projection(text,bigint[])"
REDUCE_0105 = REDUCE_0104.replace(
    "    RETURN v_state;",
    """    IF p_token='core_circle' AND v_state='ACTIVE' AND EXISTS (
        SELECT FROM public.core_circlestateevent e WHERE e.id=ANY(p_event_ids)
        AND e.action='BECOME_DORMANT' AND e.resulting_state='DORMANT') THEN
        WITH facts AS MATERIALIZED (
          SELECT seq.ord,e.circle_id,e.action,e.prior_state,e.resulting_state,
                 e.effective_at,e.recorded_at,e.evidence_reference,
                 (e.action IN ('OPEN_WORK','CLAIM_WORK','LEAVE_CIRCLE')
                  AND e.prior_state='ACTIVE' AND e.resulting_state='ACTIVE') IS TRUE AS neutral
          FROM unnest(p_event_ids) WITH ORDINALITY seq(id,ord)
          JOIN public.core_circlestateevent e ON e.id=seq.id
        ), boundary AS (SELECT * FROM facts WHERE NOT neutral ORDER BY ord DESC LIMIT 1)
        SELECT CASE WHEN EXISTS (
          SELECT FROM boundary b WHERE b.action='BECOME_DORMANT' AND b.prior_state='ACTIVE'
          AND b.resulting_state='DORMANT' AND starts_with(b.evidence_reference,'vacancy:')
          AND EXISTS (SELECT FROM facts f WHERE f.ord>b.ord)
          AND NOT EXISTS (SELECT FROM facts f WHERE f.ord>b.ord AND
              (f.circle_id<>b.circle_id OR f.effective_at<b.effective_at OR f.recorded_at>=b.recorded_at))
        ) THEN 'DORMANT' ELSE v_state END INTO v_state;
    END IF;
    RETURN v_state;""",
)

# A single cut after excluding conflicting writes. Routine operation takes none of
# these migration locks. Locking the two views also locks their underlying tables.
ROLLOUT_LOCKS = """LOCK TABLE public.core_livingorganism IN EXCLUSIVE MODE NOWAIT"""
EVIDENCE_LOCKS = """LOCK TABLE public.core_authoritybasis,public.core_authorityderivationedge,
 public.core_authorityinvalidationevent,public.core_authorityprincipal,public.core_authorityprincipalaliasreview,
 public.core_circle,public.core_circleparticipation,public.core_circleparticipationend,public.core_circlestateevent,
 public.core_contextualroleassignment,public.core_contextualroleassignmentstateevent,public.core_cqact,
 public.core_determinationcontestcase,public.core_determinationcontestevent,public.core_governeddetermination,
 public.core_lawresolutionlabel,public.core_libraryresourceversion,public.core_livingorganismevent,
 public.core_organismlawbinding,public.core_organismlawending,public.core_organismmembership,
 public.core_organismmembershiptransition,public.core_organismroledefinition,public.core_rootgenesis,
 public.cmt_mandates,public.cq_platform_defaults IN SHARE MODE NOWAIT"""

# END sequence can legitimately be followed by another event. Refuse only mutually
# exclusive terminal facts within one tenure; independent offices may end together.
ROLLOUT_ROWS = """
 WITH vacant AS MATERIALIZED (
   SELECT c.id,c.circle_uuid,lo.organism_id FROM public.core_circle c
   JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
   WHERE c.kind='GENERIC' AND public.circle_vacant(c.id,NULL,%(cut)s,%(cut)s)
 ), ends AS MATERIALIZED (
   SELECT v.id AS circle_id,a.id AS office,e.event_uuid,e.sequence,e.effective_at,e.resulting_state
   FROM vacant v JOIN public.core_contextualroleassignment a ON a.circle_id=v.id
   JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
   JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=a.id
   WHERE d.scope='CIRCLE' AND d.code='COORDINATOR' AND e.action='END' AND e.prior_state='ACTIVE'
     AND e.effective_at<=%(cut)s AND e.received_at<=%(cut)s AND e.recorded_at<=%(cut)s
 )
 SELECT v.circle_uuid,v.organism_id,p.cause,p.loss,p.deadline,p.status,
   CASE WHEN EXISTS (SELECT FROM ends a WHERE a.circle_id=v.id AND a.resulting_state<>'ENDED')
     THEN 'Coordinator END from ACTIVE declares a non-ENDED result'
   WHEN EXISTS (SELECT FROM ends a JOIN ends b ON b.office=a.office AND b.sequence>a.sequence
     WHERE a.circle_id=v.id AND a.effective_at<>b.effective_at
     AND NOT EXISTS (SELECT FROM public.core_contextualroleassignmentstateevent r
       WHERE r.assignment_id=a.office AND r.action='ASSIGN' AND r.resulting_state='ACTIVE'
         AND r.sequence>a.sequence AND r.sequence<b.sequence
         AND r.effective_at<=%(cut)s AND r.received_at<=%(cut)s AND r.recorded_at<=%(cut)s))
     THEN 'One Coordinator tenure declares two different effective END times' END AS gap
 FROM vacant v LEFT JOIN LATERAL public.circle_episode(v.id,%(cut)s,%(cut)s) p ON true
 ORDER BY v.circle_uuid
"""


def rollout_classification(row, cut):
    circle, lo, cause, loss, deadline, status, gap = row
    return {
        "circle": circle, "lo": lo, "checked_cut": cut, "cause": cause,
        "loss": loss, "deadline": deadline,
        "classification": "CANNOT_CLASSIFY" if gap else status or "NO_PROVABLE_LOSS",
        "evidence_gap": gap or (None if cause else "No qualifying last-Coordinator loss can be proved at this cut"),
    }


def rollout(cursor):
    checked = None
    try:
        cursor.execute(ROLLOUT_LOCKS)
        cursor.execute(EVIDENCE_LOCKS)
        cursor.execute("SELECT public.intevia_clock_instant()")
        checked = cursor.fetchone()[0]
        cursor.execute(ROLLOUT_ROWS, {"cut": checked})
        records = [rollout_classification(row, checked) for row in cursor.fetchall()]
    except Exception as error:
        # Avoid database exception text in the public-facing migration transcript.
        print(json.dumps({"recipient": "Carmian Owen, Human Governor", "checked_cut": checked,
                          "result": "INCOMPLETE", "error_class": type(error).__name__}, default=str), flush=True)
        raise
    blocked = any(row["classification"] == "CANNOT_CLASSIFY" for row in records)
    print(json.dumps({"recipient": "Carmian Owen, Human Governor", "checked_cut": checked,
                      "result": "REFUSED" if blocked else "CLASSIFIED", "circles": records},
                     default=str), flush=True)
    if blocked:
        raise RuntimeError("0105 cannot classify the vacant Circle population; see the Human Governor's report.")


ADDITIONS = ((BEFORE_ASSIGN, CAUSE_DEFINITION), (EPISODE, EPISODE_DEFINITION),
             (EFFECTIVE, STATE_DEFINITION))


def definition(cursor, signature):
    cursor.execute("SELECT pg_catalog.pg_get_functiondef(pg_catalog.to_regprocedure(%s))", [signature])
    return cursor.fetchone()[0]


def expect(cursor, signature, expected):
    if definition(cursor, signature) != expected:
        raise RuntimeError("0105 installed definition differs: " + signature)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, VACANT, VACANT_0104)
        expect(cursor, REDUCE, REDUCE_0104)
        for signature, _ in ADDITIONS:
            expect(cursor, signature, None)
        for signature, sql in ADDITIONS:
            cursor.execute(sql)
            expect(cursor, signature, sql)
        rollout(cursor)  # The 0104 physical reader and reducer are still installed here.
        cursor.execute(REDUCE_0105)
        expect(cursor, REDUCE, REDUCE_0105)
        cursor.execute(VACANT_0105)
        expect(cursor, VACANT, VACANT_0105)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, VACANT, VACANT_0105)
        expect(cursor, REDUCE, REDUCE_0105)
        for signature, sql in ADDITIONS:
            expect(cursor, signature, sql)
        cursor.execute(REDUCE_0104)
        expect(cursor, REDUCE, REDUCE_0104)
        cursor.execute(VACANT_0104)
        expect(cursor, VACANT, VACANT_0104)
        for signature, _ in reversed(ADDITIONS):
            cursor.execute("DROP FUNCTION " + signature)
            expect(cursor, signature, None)


class Migration(migrations.Migration):
    dependencies = (("core", "0104_clock_automatic_time_fields"),)
    operations = (migrations.RunPython(install, uninstall),)
