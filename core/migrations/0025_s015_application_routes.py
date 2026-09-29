import uuid

from django.db import migrations, models
from django.db.models.deletion import PROTECT


def reference(target, *, nullable=False, unique=False, related_name=None):
    field = models.OneToOneField if unique else models.ForeignKey
    return field(to=target, on_delete=PROTECT, related_name=related_name, null=nullable, blank=nullable)


def record(name, fields, *, constraints=()):
    return migrations.CreateModel(
        name=name,
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            *fields,
            ("occurred_at", models.DateTimeField()),
            ("effective_at", models.DateTimeField()),
            ("received_at", models.DateTimeField()),
            ("recorded_at", models.DateTimeField(null=True, editable=False)),
        ],
        options={"constraints": list(constraints)},
    )


def install_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 application guards require PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        for sql in INSTALL_SQL:
            cursor.execute(sql)


def remove_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 application guards require PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        for sql in REMOVE_SQL:
            cursor.execute(sql)


TABLES = (
    "core_application", "core_csaassignment", "core_csaassignmentend", "core_applicationengagement",
    "core_applicationdecision", "core_applicationwithdrawal", "core_applicationdecisioneffect",
)

INSTALL_SQL = ["""
CREATE FUNCTION public.s3_assign_record_time() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'S3 recorded_at is database-assigned' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := statement_timestamp();
    RETURN NEW;
END $$;
""", """
CREATE FUNCTION public.s3_lock_application(p_application bigint) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM public.core_application WHERE id = p_application FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'S3 unknown application' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
END $$;
""", """
CREATE FUNCTION public.s3_claim_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE previous public.core_csaassignment%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    IF EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id)
       OR EXISTS (SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = NEW.application_id) THEN
        RAISE EXCEPTION 'S3 claim after terminal row' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.replaces_id IS NOT NULL THEN
        SELECT * INTO previous FROM public.core_csaassignment WHERE id = NEW.replaces_id AND application_id = NEW.application_id;
        IF NOT FOUND OR NOT EXISTS (
            SELECT 1 FROM public.core_csaassignmentend ending WHERE ending.assignment_id = previous.id
              AND ending.effective_at <= NEW.effective_at AND ending.recorded_at <= NEW.recorded_at
        ) THEN
            RAISE EXCEPTION 'S3 replacement requires an ended assignment on this application' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.core_csaassignment claim
        WHERE claim.application_id = NEW.application_id
                    AND claim.effective_at <= NEW.effective_at
                    AND NOT EXISTS (
                            SELECT 1 FROM public.core_csaassignmentend ending WHERE ending.assignment_id = claim.id
                                AND ending.effective_at <= NEW.effective_at AND ending.recorded_at <= NEW.recorded_at
                    )
    ) THEN
        RAISE EXCEPTION 'S3 single effective claim' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.core_organismmembership member
        JOIN public.core_application application ON application.id = NEW.application_id
        JOIN public.core_contextualroleassignment office ON office.id = NEW.assignee_office_id
        JOIN public.core_organismroledefinition definition ON definition.id = office.role_definition_id
        WHERE member.id = NEW.assignee_membership_id AND member.living_organism_id = application.living_organism_id
          AND office.membership_id = member.id AND member.identity_id = NEW.actor_id
          AND definition.code = 'COORDINATOR' AND definition.scope = 'LIVING_ORGANISM'
          AND public.s015_fold_effective_state('core_contextualroleassignment', office.id, NEW.effective_at, NEW.recorded_at) = 'ACTIVE'
    ) THEN
        RAISE EXCEPTION 'S3 claim requires an effective self-assigned Coordinator office' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
""", """
CREATE FUNCTION public.s3_end_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE application_id bigint;
BEGIN
    SELECT claim.application_id INTO application_id FROM public.core_csaassignment claim WHERE claim.id = NEW.assignment_id;
    PERFORM public.s3_lock_application(application_id);
    RETURN NEW;
END $$;
""", """
CREATE FUNCTION public.s3_engagement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE previous public.core_applicationengagement%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    IF NEW.engagement_ordinal < 1 OR NEW.route NOT IN ('LO_CQ','LO_CSA','CIRCLE_CQ','CIRCLE_CSA')
       OR EXISTS (SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = NEW.application_id)
       OR EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at) THEN
        RAISE EXCEPTION 'S3 engagement refused' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.engagement_ordinal = 1 THEN
        IF EXISTS (SELECT 1 FROM public.core_applicationengagement WHERE application_id = NEW.application_id) THEN
            RAISE EXCEPTION 'S3 first engagement must be first' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        SELECT * INTO previous FROM public.core_applicationengagement
        WHERE application_id = NEW.application_id AND engagement_ordinal = NEW.engagement_ordinal - 1;
        IF NOT FOUND OR NEW.basis_reference IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.core_csaassignment claim JOIN public.core_csaassignmentend ending ON ending.assignment_id = claim.id
            WHERE previous.route IN ('LO_CSA','CIRCLE_CSA') AND previous.engagement_reference = claim.assignment_uuid::text
              AND claim.application_id = NEW.application_id AND ending.ending_uuid::text = NEW.basis_reference
              AND ending.effective_at <= NEW.effective_at AND ending.recorded_at <= NEW.recorded_at
        ) THEN
            RAISE EXCEPTION 'S3 re-engagement requires prior UNABLE owner record' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
""", """
CREATE FUNCTION public.s3_terminal_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    IF TG_TABLE_NAME = 'core_applicationdecision' AND EXISTS (
        SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = NEW.application_id
    ) OR TG_TABLE_NAME = 'core_applicationwithdrawal' AND EXISTS (
        SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id
    ) THEN
        RAISE EXCEPTION 'S3 terminal exclusion' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
""", """
CREATE FUNCTION public.s3_withdraw_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.core_application WHERE id = NEW.application_id AND applicant_id = NEW.actor_id AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at)
       OR EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = NEW.application_id) THEN
        RAISE EXCEPTION 'S3 withdrawal requires applicant and no decision' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""]

for table in TABLES:
    INSTALL_SQL.append(
        f"CREATE TRIGGER s3_00_record_time BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s3_assign_record_time()"
    )
for table in TABLES:
    INSTALL_SQL.extend((
        f"CREATE TRIGGER s3_row_immutable BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()",
        f"CREATE TRIGGER s015_s3_truncate_immutable BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()",
    ))
for table, function in (
    ("core_csaassignment", "s3_claim_guard"), ("core_csaassignmentend", "s3_end_guard"),
    ("core_applicationengagement", "s3_engagement_guard"),
    ("core_applicationdecision", "s3_terminal_lock"), ("core_applicationwithdrawal", "s3_terminal_lock"),
    ("core_applicationwithdrawal", "s3_withdraw_guard"),
):
    INSTALL_SQL.append(f"CREATE TRIGGER s3_10_{function} BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{function}()")

INSTALL_SQL.extend(["""
CREATE FUNCTION public.s3_decision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE engagement public.core_applicationengagement%ROWTYPE;
BEGIN
    SELECT * INTO engagement FROM public.core_applicationengagement
    WHERE application_id = NEW.application_id AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at
    ORDER BY engagement_ordinal DESC LIMIT 1;
    IF NOT FOUND OR NEW.outcome NOT IN ('ACCEPT','DECLINE') OR NEW.route <> engagement.route
       OR NEW.deciding_party_id <> engagement.deciding_party_id OR engagement.settings_basis IS NULL
       OR length(engagement.settings_basis) = 0 THEN
        RAISE EXCEPTION 'S3 decision lacks current frozen engagement' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.route IN ('LO_CQ','CIRCLE_CQ') THEN
        RAISE EXCEPTION 'S3 CQ result owner unavailable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
     IF NEW.route <> 'LO_CSA'
         OR engagement.settings_basis <> 'DEFAULT (no settings store): cq_active=No, application_sole_participant_delegation=No'
         OR NEW.assignment_reference_id IS NULL
       OR NEW.responsible_capacity <> 'COORDINATOR'
       OR NEW.responsible_coordinator_id IS DISTINCT FROM NEW.enterer_id
       OR NEW.qualifying_office_id IS NULL
       OR NOT EXISTS (
          SELECT 1 FROM public.core_csaassignment claim
          JOIN public.core_organismmembership member ON member.id = claim.assignee_membership_id
          JOIN public.core_contextualroleassignment office ON office.id = claim.assignee_office_id
          JOIN public.core_organismroledefinition definition ON definition.id = office.role_definition_id
          WHERE claim.id = NEW.assignment_reference_id AND claim.application_id = NEW.application_id
            AND claim.assignment_uuid::text = engagement.engagement_reference
            AND member.identity_id = NEW.enterer_id AND member.living_organism_id = NEW.deciding_party_id
            AND office.id = NEW.qualifying_office_id AND office.membership_id = member.id
            AND definition.code = 'COORDINATOR' AND definition.scope = 'LIVING_ORGANISM'
            AND claim.effective_at <= NEW.effective_at AND claim.recorded_at <= NEW.recorded_at
            AND public.s015_fold_effective_state('core_contextualroleassignment', office.id, NEW.effective_at, NEW.recorded_at) = 'ACTIVE'
            AND public.s015_fold_effective_state('core_organismmembership', member.id, NEW.effective_at, NEW.recorded_at) = 'ACTIVE'
            AND NOT EXISTS (
                SELECT 1 FROM public.core_csaassignmentend ending
                WHERE ending.assignment_id = claim.id AND ending.effective_at <= NEW.effective_at
                  AND ending.recorded_at <= NEW.recorded_at
            )
            AND NOT EXISTS (
                SELECT 1 FROM public.core_contextualroleassignmentstateevent term
                WHERE term.assignment_id = office.id AND term.effective_until_state = 'FIXED'
                  AND term.effective_until <= NEW.effective_at AND term.recorded_at <= NEW.recorded_at
                  AND term.id = (
                      SELECT last_term.id FROM public.core_contextualroleassignmentstateevent last_term
                      WHERE last_term.assignment_id = office.id AND last_term.effective_at <= NEW.effective_at
                        AND last_term.recorded_at <= NEW.recorded_at
                      ORDER BY last_term.effective_at DESC, last_term.sequence DESC LIMIT 1
                  )
            )
       ) THEN
        RAISE EXCEPTION 'S3 CSA completion authority refused' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
""", """
CREATE FUNCTION public.s3_guard_lo_actor() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.actor_state <> 'LIVING_ORGANISM' THEN RETURN NEW; END IF;
    IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state <> 'ACTIVE'
       OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity <> 'NA_NO_PARTY' OR NEW.entry_mode <> 'TRANSPORT'
       OR NEW.composer_state <> 'NA_TRANSPORT'
       OR NEW.evidence_reference !~ '^application-decision:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RAISE EXCEPTION 'S3 malformed LO admission actor' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.core_applicationdecision decision
        JOIN public.core_application application ON application.id = decision.application_id
        JOIN public.core_organismmembership member ON member.id = NEW.membership_id
        WHERE decision.decision_uuid::text = substring(NEW.evidence_reference from 22)
          AND decision.outcome = 'ACCEPT' AND application.living_organism_id = member.living_organism_id
          AND application.applicant_id = member.identity_id
    ) THEN
        RAISE EXCEPTION 'S3 LO actor lacks matching ACCEPT decision' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
""", """
CREATE FUNCTION public.s3_guard_effect() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.core_applicationdecision decision
        JOIN public.core_application application ON application.id = decision.application_id
        JOIN public.core_organismmembershiptransition effect ON effect.id = NEW.transition_id
        JOIN public.core_organismmembership member ON member.id = effect.membership_id
        WHERE decision.id = NEW.decision_id AND decision.outcome = 'ACCEPT'
          AND effect.actor_state = 'LIVING_ORGANISM' AND effect.prior_state = 'PROPOSED'
          AND effect.resulting_state = 'ACTIVE' AND effect.evidence_reference = 'application-decision:' || decision.decision_uuid::text
          AND application.living_organism_id = member.living_organism_id AND application.applicant_id = member.identity_id
    ) THEN
        RAISE EXCEPTION 'S3 decision/effect link mismatch' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END $$;
""", """
CREATE FUNCTION public.s3_guard_decision_effect() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT count(*) FROM public.core_applicationdecisioneffect WHERE decision_id = NEW.id)
    <> (CASE WHEN NEW.outcome = 'ACCEPT' THEN 1 ELSE 0 END) THEN
        RAISE EXCEPTION 'S3 ACCEPT requires exactly one effect and DECLINE none' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END $$;
""", """
CREATE FUNCTION public.s3_guard_lo_effect() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.actor_state = 'LIVING_ORGANISM' AND
       (SELECT count(*) FROM public.core_applicationdecisioneffect WHERE transition_id = NEW.id) <> 1 THEN
        RAISE EXCEPTION 'S3 LO admission requires exactly one decision link' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END $$;
""", "CREATE TRIGGER s3_20_s3_decision_guard BEFORE INSERT ON public.core_applicationdecision FOR EACH ROW EXECUTE FUNCTION public.s3_decision_guard()",
"ALTER TABLE public.core_organismmembershiptransition DROP CONSTRAINT s015_0022_organismmembershiptransition_actor_state_vocab_ck",
"ALTER TABLE public.core_organismmembershiptransition ADD CONSTRAINT s015_0022_organismmembershiptransition_actor_state_vocab_ck CHECK (actor_state IN ('PARTY','NONE','NOT_ESTABLISHED','LIVING_ORGANISM'))",
"CREATE TRIGGER s3_lo_actor_scope BEFORE INSERT ON public.core_organismmembershiptransition FOR EACH ROW EXECUTE FUNCTION public.s3_guard_lo_actor()",
"CREATE CONSTRAINT TRIGGER s3_effect_check AFTER INSERT ON public.core_applicationdecisioneffect DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s3_guard_effect()",
"CREATE CONSTRAINT TRIGGER s3_decision_effect_check AFTER INSERT ON public.core_applicationdecision DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s3_guard_decision_effect()",
"CREATE CONSTRAINT TRIGGER s3_lo_effect_check AFTER INSERT ON public.core_organismmembershiptransition DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.s3_guard_lo_effect()",
])

REMOVE_SQL = [
    *[f"DROP TRIGGER IF EXISTS s3_{name} ON public.{table}" for table, name in (
        ("core_organismmembershiptransition", "lo_actor_scope"),
        ("core_organismmembershiptransition", "lo_effect_check"),
        ("core_applicationdecisioneffect", "effect_check"),
        ("core_applicationdecision", "decision_effect_check"),
    )],
    "ALTER TABLE public.core_organismmembershiptransition DROP CONSTRAINT s015_0022_organismmembershiptransition_actor_state_vocab_ck",
    "ALTER TABLE public.core_organismmembershiptransition ADD CONSTRAINT s015_0022_organismmembershiptransition_actor_state_vocab_ck CHECK (actor_state IN ('PARTY','NONE','NOT_ESTABLISHED'))",
    *[f"DROP TRIGGER IF EXISTS s3_10_{name} ON public.{table}" for table, name in (
        ("core_csaassignment", "s3_claim_guard"), ("core_csaassignmentend", "s3_end_guard"),
        ("core_applicationengagement", "s3_engagement_guard"), ("core_applicationdecision", "s3_terminal_lock"),
        ("core_applicationwithdrawal", "s3_terminal_lock"),
        ("core_applicationwithdrawal", "s3_withdraw_guard"),
    )],
    "DROP TRIGGER IF EXISTS s3_20_s3_decision_guard ON public.core_applicationdecision",
    *[f"DROP TRIGGER IF EXISTS s3_row_immutable ON public.{table}" for table in TABLES],
    *[f"DROP TRIGGER IF EXISTS s015_s3_truncate_immutable ON public.{table}" for table in TABLES],
    *[f"DROP TRIGGER IF EXISTS s3_00_record_time ON public.{table}" for table in TABLES],
    *[f"DROP FUNCTION IF EXISTS public.{name}()" for name in (
        "s3_guard_effect", "s3_guard_decision_effect", "s3_guard_lo_effect", "s3_guard_lo_actor",
        "s3_decision_guard", "s3_withdraw_guard", "s3_terminal_lock", "s3_engagement_guard",
        "s3_end_guard", "s3_claim_guard", "s3_assign_record_time",
    )],
    "DROP FUNCTION IF EXISTS public.s3_lock_application(bigint)",
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0024_s015_compact_commitment_preimages")]

    operations = [
        record("Application", [
            ("application_uuid", models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
            ("applicant", reference("core.identity", related_name="organism_applications")),
            ("living_organism", reference("core.livingorganism", related_name="applications")),
            ("predecessor_reference", reference("core.application", nullable=True)),
            ("actor", reference("core.identity", related_name="submitted_organism_applications")),
            ("enterer", reference("core.identity", related_name="entered_organism_applications")),
        ]),
        record("CSAAssignment", [
            ("assignment_uuid", models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
            ("application", reference("core.application", related_name="csa_assignments")),
            ("replaces", reference("core.csaassignment", unique=True, nullable=True, related_name="replacement")),
            ("assignee_membership", reference("core.organismmembership")),
            ("assignee_office", reference("core.contextualroleassignment")),
            ("actor", reference("core.identity", related_name="claimed_csa_assignments")),
            ("enterer", reference("core.identity", related_name="entered_csa_assignments")),
            ("completion_due_at", models.DateTimeField()),
        ]),
        record("CSAAssignmentEnd", [
            ("ending_uuid", models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
            ("assignment", reference("core.csaassignment", unique=True, related_name="ending")),
            ("actor", reference("core.identity", related_name="ended_csa_assignments")),
            ("enterer", reference("core.identity", related_name="entered_csa_assignment_ends")),
        ]),
        record("ApplicationEngagement", [
            ("application", reference("core.application", related_name="engagements")),
            ("engagement_ordinal", models.PositiveIntegerField()),
            ("route", models.CharField(max_length=16, choices=[(r, r) for r in ("LO_CQ", "LO_CSA", "CIRCLE_CQ", "CIRCLE_CSA")])),
            ("deciding_party", reference("core.livingorganism")),
            ("engagement_reference", models.CharField(max_length=255)),
            ("settings_basis", models.CharField(max_length=255)),
            ("basis_reference", models.CharField(max_length=255, null=True, blank=True)),
            ("actor", reference("core.identity", related_name="engaged_organism_applications")),
            ("enterer", reference("core.identity", related_name="entered_application_engagements")),
        ], constraints=[models.UniqueConstraint(fields=("application", "engagement_ordinal"), name="s3_engagement_ordinal_uniq")]),
        record("ApplicationDecision", [
            ("decision_uuid", models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
            ("application", reference("core.application", unique=True, related_name="decision")),
            ("route", models.CharField(max_length=16, choices=[(r, r) for r in ("LO_CQ", "LO_CSA", "CIRCLE_CQ", "CIRCLE_CSA")])),
            ("deciding_party", reference("core.livingorganism")),
            ("outcome", models.CharField(max_length=7, choices=[("ACCEPT", "ACCEPT"), ("DECLINE", "DECLINE")])),
            ("assignment_reference", reference("core.csaassignment", nullable=True)),
            ("supporting_result_reference", models.CharField(max_length=255, null=True, blank=True)),
            ("responsible_coordinator", reference("core.identity", nullable=True, related_name="coordinated_application_decisions")),
            ("responsible_capacity", models.CharField(max_length=48, null=True, blank=True)),
            ("qualifying_office", reference("core.contextualroleassignment", nullable=True)),
            ("authority_decision_reference", models.CharField(max_length=71)),
            ("executor", reference("core.identity", related_name="executed_application_decisions")),
            ("enterer", reference("core.identity", related_name="entered_application_decisions")),
        ]),
        record("ApplicationWithdrawal", [
            ("application", reference("core.application", unique=True, related_name="withdrawal")),
            ("actor", reference("core.identity", related_name="withdrawn_organism_applications")),
            ("enterer", reference("core.identity", related_name="entered_application_withdrawals")),
        ]),
        migrations.CreateModel(
            name="ApplicationDecisionEffect",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision", reference("core.applicationdecision", unique=True, related_name="membership_effect")),
                ("transition", reference("core.organismmembershiptransition", unique=True, related_name="application_effect")),
                ("occurred_at", models.DateTimeField()),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(null=True, editable=False)),
            ],
        ),
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.AlterField(
                model_name="organismmembershiptransition", name="actor",
                field=reference("core.identity", nullable=True, related_name="+"),
            ),
            migrations.AlterField(
                model_name="organismmembershiptransition", name="actor_access_epoch",
                field=models.PositiveBigIntegerField(null=True, blank=True),
            ),
        ], database_operations=[]),
        migrations.RunPython(install_guards, remove_guards),
    ]