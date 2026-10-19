"""Install the append-only H100 request-intake record and its database guards.

Implements R-1 as captured in HG_RULING_R1_20261014.md and
HG_DECISIONS_ON_RETURN_1_20261014.txt, with the adopted Stop 1/Stop 2
decisions in HG_DECISION_STOP1_ADOPTION_AND_STOP2_AUTHORISATION_20261019_CAPTURE.txt
and HG_DECISION_STOP2_ADOPTION_AND_STOP3_AUTHORISATION_20261019_CAPTURE.txt.
The database assigns receipt time, arbitrates the natural key, and refuses
row mutation; application admission and recognition remain the service
boundary's responsibility.
"""

from django.db import migrations, models
import django.db.models.deletion


MIGRATION = "0049_h100_command_intake"
PREVIOUS_MIGRATION = "0048_se_entitlement_granted_event_guard"

ENTRY_VALUES = (
    "FOUND_LIVING_ORGANISM",
    "ADVANCE_PHASE",
    "BIRTHING",
    "ADOPT_GOVERNING_INSTRUMENT",
    "CREATE_GOVERNANCE_CIRCLE",
    "REQUEST_GOVERNANCE_SEAT",
    "DECIDE_GOVERNANCE_SEAT",
    "DECIDE_GOVERNANCE_SEAT_COMPOUND",
    "SEAT_GOVERNANCE_CIRCLE",
    "SUBMIT_APPLICATION",
    "WITHDRAW_APPLICATION",
    "CLAIM_APPLICATION",
    "REASSIGN_APPLICATION",
    "DECIDE_APPLICATION",
    "SAVE_GATEWAY_DRAFT",
    "EFFECT_GATEWAY_VERSION",
    "OPEN_MEMBERSHIP",
    "TRANSITION_MEMBERSHIP",
    "PROPOSE_ASSIGNMENT",
    "TRANSITION_ASSIGNMENT",
    "RECORD_OFFICE_CONSENT",
    "ACTIVATE_BETA",
)

ENTRY_CHOICES = [
    ("FOUND_LIVING_ORGANISM", "Found living organism"),
    ("ADVANCE_PHASE", "Advance phase"),
    ("BIRTHING", "Birthing"),
    ("ADOPT_GOVERNING_INSTRUMENT", "Adopt governing instrument"),
    ("CREATE_GOVERNANCE_CIRCLE", "Create governance circle"),
    ("REQUEST_GOVERNANCE_SEAT", "Request governance seat"),
    ("DECIDE_GOVERNANCE_SEAT", "Decide governance seat"),
    ("DECIDE_GOVERNANCE_SEAT_COMPOUND", "Decide governance seat (compound)"),
    ("SEAT_GOVERNANCE_CIRCLE", "Seat governance circle"),
    ("SUBMIT_APPLICATION", "Submit application"),
    ("WITHDRAW_APPLICATION", "Withdraw application"),
    ("CLAIM_APPLICATION", "Claim application"),
    ("REASSIGN_APPLICATION", "Reassign application"),
    ("DECIDE_APPLICATION", "Decide application"),
    ("SAVE_GATEWAY_DRAFT", "Save gateway draft"),
    ("EFFECT_GATEWAY_VERSION", "Effect gateway version"),
    ("OPEN_MEMBERSHIP", "Open membership"),
    ("TRANSITION_MEMBERSHIP", "Transition membership"),
    ("PROPOSE_ASSIGNMENT", "Propose assignment"),
    ("TRANSITION_ASSIGNMENT", "Transition assignment"),
    ("RECORD_OFFICE_CONSENT", "Record office consent"),
    ("ACTIVATE_BETA", "Activate beta"),
]


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(
        """
        CREATE INDEX h100_intake_decision_history_idx
            ON public.core_authoritydecision (actor_id, action, idempotency_key)
        """
    )
    schema_editor.execute(
        """
        CREATE FUNCTION public.s015_h100_assign_command_intake_time()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.received_at IS NOT NULL THEN
                RAISE EXCEPTION 'H100 command intake received_at is database-assigned'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            NEW.received_at := clock_timestamp();
            RETURN NEW;
        END
        $$
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER s015_h100_command_intake_assign_time
        BEFORE INSERT ON public.core_commandintake
        FOR EACH ROW
        EXECUTE FUNCTION public.s015_h100_assign_command_intake_time()
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER s015_h100_command_intake_update_immutable
        BEFORE UPDATE OF
            id, actor_id, actor_identity_id, actor_access_epoch, entry, action,
            idempotency_key, request_reference, request_fingerprint,
            fingerprint_schema, target_organism_id, received_at
        ON public.core_commandintake
        FOR EACH ROW
        EXECUTE FUNCTION public.s015_refuse_row_mutation()
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER s015_h100_command_intake_delete_immutable
        BEFORE DELETE ON public.core_commandintake
        FOR EACH ROW
        EXECUTE FUNCTION public.s015_refuse_row_mutation()
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER s015_h100_command_intake_truncate_immutable
        BEFORE TRUNCATE ON public.core_commandintake
        FOR EACH STATEMENT
        EXECUTE FUNCTION public.s015_refuse_row_mutation()
        """
    )


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM public.core_commandintake)")
        if cursor.fetchone()[0]:
            raise RuntimeError(
                f"{MIGRATION} reverse refused while core_commandintake rows exist"
            )
    schema_editor.execute(
        "DROP TRIGGER s015_h100_command_intake_assign_time "
        "ON public.core_commandintake"
    )
    schema_editor.execute(
        "DROP TRIGGER s015_h100_command_intake_update_immutable "
        "ON public.core_commandintake"
    )
    schema_editor.execute(
        "DROP TRIGGER s015_h100_command_intake_delete_immutable "
        "ON public.core_commandintake"
    )
    schema_editor.execute(
        "DROP TRIGGER s015_h100_command_intake_truncate_immutable "
        "ON public.core_commandintake"
    )
    schema_editor.execute("DROP INDEX public.h100_intake_decision_history_idx")
    schema_editor.execute("DROP FUNCTION public.s015_h100_assign_command_intake_time()")


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", PREVIOUS_MIGRATION)]

    operations = [
        migrations.CreateModel(
            name="CommandIntake",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("actor_identity_id", models.UUIDField()),
                ("actor_access_epoch", models.BigIntegerField()),
                ("entry", models.CharField(choices=ENTRY_CHOICES, max_length=40)),
                ("action", models.CharField(max_length=64)),
                ("idempotency_key", models.CharField(max_length=120)),
                ("request_reference", models.CharField(max_length=128)),
                ("request_fingerprint", models.CharField(max_length=64)),
                ("fingerprint_schema", models.CharField(max_length=32)),
                ("target_organism_id", models.UUIDField(blank=True, null=True)),
                ("received_at", models.DateTimeField(editable=False)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="command_intakes",
                        to="core.identity",
                    ),
                ),
            ],
            options={
                "db_table": "core_commandintake",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("actor", "action", "idempotency_key"),
                        name="h100_intake_actor_action_key_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("entry__in", ENTRY_VALUES)),
                        name="h100_intake_entry_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("request_fingerprint__regex", r"^[0-9a-f]{64}$")
                        ),
                        name="h100_intake_fingerprint_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("actor_access_epoch__gte", 0)),
                        name="h100_intake_epoch_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                (
                                    "entry__in",
                                    (
                                        "DECIDE_GOVERNANCE_SEAT_COMPOUND",
                                        "SEAT_GOVERNANCE_CIRCLE",
                                    ),
                                )
                            )
                            & models.Q(("target_organism_id__isnull", False))
                        )
                        | (
                            ~models.Q(
                                (
                                    "entry__in",
                                    (
                                        "DECIDE_GOVERNANCE_SEAT_COMPOUND",
                                        "SEAT_GOVERNANCE_CIRCLE",
                                    ),
                                )
                            )
                            & models.Q(("target_organism_id__isnull", True))
                        ),
                        name="h100_intake_compound_target_match",
                    ),
                ],
            },
        ),
        migrations.RunPython(install, uninstall),
    ]
