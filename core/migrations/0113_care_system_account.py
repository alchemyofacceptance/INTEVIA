"""Install CARE's identity, credential and public name as one unit."""

from hashlib import sha256
from importlib import import_module
from unicodedata import normalize

from django.db import migrations

from core.migrations._clock_conversion import swap

CARE_ID = "b79c7d99-67b3-4dd0-8ab7-ee1328d1e183"
UNUSABLE_MARKER = "!"
NAME_TAKEN = (
    "CARE's account could not be installed. An existing account already holds the "
    "reserved {kind} in {table}. No account was created, renamed or adopted. Ask the database "
    "operator to resolve the name by hand and then run the migration again."
)
IN_USE = (
    "CARE's account could not be removed. Another record refers to it. "
    "The account and its history have been kept. Keep this migration in place. "
    "Ask the database operator to review the references before any further action."
)
INCOMPLETE = (
    "CARE's account could not be removed. Its three linked records could not be "
    "identified together. Nothing was removed. Keep this migration in place "
    "and ask the database operator to check the records."
)


def install(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        # Keep the names stable from the preflight through the three inserts.
        cursor.execute("""
            LOCK TABLE public.auth_user, public.core_identity, public.core_identityresolution
            IN SHARE ROW EXCLUSIVE MODE
        """)
        # Freeze the same Unicode comparison here so future route changes cannot
        # change the meaning of this historical migration.
        for table, statement in (
            ("auth_user", "SELECT username, NULL FROM public.auth_user"),
            ("core_identity", "SELECT canonical_username, display_name FROM public.core_identity"),
            (
                "core_identityresolution",
                "SELECT canonical_username, display_name FROM public.core_identityresolution",
            ),
        ):
            cursor.execute(statement)
            for username, display_name in cursor:
                for kind, value in (("username", username), ("display name", display_name)):
                    if value is not None and normalize("NFKC", value).casefold().strip() == "care":
                        raise RuntimeError(NAME_TAKEN.format(kind=kind, table=table))
        cursor.execute(
            """
            INSERT INTO public.auth_user
                (username, password, first_name, last_name, email, is_active,
                 is_staff, is_superuser, date_joined)
            VALUES ('care', %s, '', '', '', true, false, false, public.intevia_clock_statement())
            RETURNING id, date_joined
        """,
            [UNUSABLE_MARKER],
        )
        credential_id, installed_at = cursor.fetchone()
        cursor.execute(
            """
            INSERT INTO public.core_identity
                (identity_id, credential_id, canonical_username, display_name, access_state,
                 access_epoch, created_at, updated_at, deactivated_at)
            VALUES (%s, %s, 'care', 'CARE', 'deactivated', 0, %s, %s, %s)
            RETURNING id
        """,
            [CARE_ID, credential_id, installed_at, installed_at, installed_at],
        )
        identity_pk = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO public.core_identityresolution
                (identity_id, canonical_username, display_name, credential_link)
            VALUES (%s, 'care', 'CARE', %s)
        """,
            [identity_pk, credential_id],
        )


# P2b changes only this trigger body. The historical 0111 source is also the
# exact downgrade target. Preserve function identity, properties and grants.
INPUT_SIGNATURE = "public.care_observation_input()"
INPUT_PREIMAGE = "d6f59a2b82b8fb50a0b668a365f6f4e08b8f7f2dfbeaac0ea142ddefa2d3880b"
INPUT_EDITS = (
    (
        "    owner_uuid uuid;",
        "    opener bigint := NEW.requester_id;\n    opener_epoch bigint := NEW.requester_epoch;\n    owner_uuid uuid;",
    ),
    (
        "i.id=NEW.requester_id AND i.access_epoch=NEW.requester_epoch AND u.is_active",
        "i.id=opener AND i.access_epoch=opener_epoch AND i.access_state='active' AND u.is_active",
    ),
    (
        "owner_uuid,NEW.definition_uuid,NEW.requester_id,NEW.requester_epoch,stamp,true",
        "owner_uuid,NEW.definition_uuid,opener,opener_epoch,stamp,true",
    ),
    (
        "    NEW.qualification_at := stamp;",
        "    -- The opener and epoch qualify this insert. Only the public source survives in the detail.\n"
        "    NEW.requester_id := NULL;\n    NEW.requester_epoch := NULL;\n    NEW.qualification_at := stamp;",
    ),
)


def replace_observation_input(cursor, forward):
    # 0111 owns the source of the predecessor. Pin its entire body before
    # deriving the single successor used by both directions and by swap().
    predecessor = import_module("core.migrations.0111_care_office_record").OBSERVATION
    start = "CREATE FUNCTION public.care_observation_input()\nRETURNS trigger LANGUAGE plpgsql SECURITY INVOKER AS $$"
    before = predecessor.partition(start)[2].partition("$$;")[0]
    if sha256(before.encode()).hexdigest() != INPUT_PREIMAGE:
        raise RuntimeError(
            "CARE's observation input has an unexpected source definition. "
            "The migration stopped. Its changes will roll back. "
            "Keep the current schema and ask the database operator to check its version."
        )
    after = before
    for anchor, replacement in INPUT_EDITS:
        if after.count(anchor) != 1:
            raise RuntimeError(
                "CARE's observation input has no unique replacement point. "
                "The migration stopped and its changes will roll back. "
                "Keep the current schema and ask the database operator to check its version."
            )
        after = after.replace(anchor, replacement, 1)
    expected, replacement = (before, after) if forward else (after, before)
    swap(cursor, INPUT_SIGNATURE, expected, replacement)


# CARE's public UUID is stable across installations. Always resolve the local
# identity primary key on this database; never embed the seed's sequence value.
WRITER_ENTRY = """ care_pk bigint;
BEGIN
 SELECT id INTO care_pk FROM public.core_identity WHERE identity_id='{care}'::uuid;
 IF action_name='RECORD_CARE_OFFICE_OBSERVATION' THEN
    IF NOT is_command OR token IS DISTINCT FROM 'core_livingorganism'
       OR care_pk IS NULL OR actor IS DISTINCT FROM care_pk THEN
       RAISE EXCEPTION 'CARE_WRITER_PRINCIPAL' USING ERRCODE='23514';
    END IF;
 ELSIF actor=care_pk THEN
    RAISE EXCEPTION 'CARE_WRITER_PRINCIPAL' USING ERRCODE='23514';
 END IF;
 SELECT m.event_table""".format(care=CARE_ID)

GATEWAY_ENTRY = """        care_pk bigint;
BEGIN
    SELECT id INTO care_pk FROM public.core_identity WHERE identity_id='{care}'::uuid;
    IF NEW.actor_id=care_pk THEN
      RAISE EXCEPTION 'CARE_GATEWAY_ACTOR' USING ERRCODE='23514';
    END IF;
    IF NEW.action='RECORD_CARE_OFFICE_OBSERVATION' THEN
      IF care_pk IS NULL
         OR NEW.composer_identity_id IS DISTINCT FROM care_pk
         OR NEW.enterer_identity_id IS DISTINCT FROM care_pk THEN
        RAISE EXCEPTION 'CARE_GATEWAY_PAIR' USING ERRCODE='23514';
      END IF;
      IF TG_RELID <> 'public.core_livingorganismevent'::regclass THEN
        RAISE EXCEPTION 'CARE_GATEWAY_TABLE' USING ERRCODE='23514';
      END IF;
    ELSIF NEW.composer_identity_id=care_pk OR NEW.enterer_identity_id=care_pk THEN
      RAISE EXCEPTION 'CARE_GATEWAY_ROLE' USING ERRCODE='23514';
    END IF;

    IF NEW.action='SET_CARE_ASSISTANCE'""".format(care=CARE_ID)

OLD_MAPPING = """   -- The generic composed event already has no actor and keeps the requester as enterer.
   -- The rule supplied the words. No human composer was established.
   values_:=values_||jsonb_build_object('composer_state','NOT_ESTABLISHED','composer_identity_id',NULL);
"""
NEW_MAPPING = """   -- The generic composed envelope records the supplied rule, no Actor,
   -- and the checked CARE principal as both Composer and Enterer.
"""
OLD_ROLES = """          AND e.composer_state='NOT_ESTABLISHED' AND e.composer_identity_id IS NULL
          AND e.enterer_identity_id=o.requester_id"""
NEW_ROLES = """          AND e.composer_state='REFERENCE'
          AND EXISTS (SELECT FROM public.core_identity i
                      JOIN public.core_identityresolution r
                        ON r.identity_id=i.id AND r.credential_link=i.credential_id
                      WHERE i.identity_id='{care}'::uuid
                        AND i.id=e.composer_identity_id AND i.id=e.enterer_identity_id)""".format(care=CARE_ID)

# Digests cover the complete pg_get_functiondef captured on PostgreSQL 17.
# The 0111 replacement targets are our preimages for the two shared functions.
def replace_event_functions(cursor, forward):
    previous = import_module("core.migrations.0111_care_office_record")
    specs = (
        ("CARE's history writer", previous.WRITER, previous.REPLACEMENTS[0][-1], "9993ec0bff9ddd87dbd5330ce150af274154c6259a6fee959b8fd5f733231273",
         (("BEGIN\n SELECT m.event_table", WRITER_ENTRY), (OLD_MAPPING, NEW_MAPPING))),
        ("the history gateway", previous.GATEWAY, previous.REPLACEMENTS[1][-1], "c4be26c5eb58f66008b5a804f24ea4c9bf0f18197864dcdb512445c5ba3fdba1",
         (("BEGIN\n\n    IF NEW.action='SET_CARE_ASSISTANCE'", GATEWAY_ENTRY),)),
        ("CARE's observation check", "public.care_observation_matches(public.core_livingorganismevent)", "143854059979571826356840aace0cdd3bf2582db1b80baff07fac53bd4b3f58",
         "26cb18bcfb30637f0188a0a3ed25a74b4e308ff6eabbfc93f5d39f030f7c34dd", ((OLD_ROLES, NEW_ROLES),)),
    )
    for label, signature, before, after, edits in specs if forward else reversed(specs):
        source_pin, target_pin = (before, after) if forward else (after, before)
        source = previous.installed_definition(cursor, signature)
        previous.require_digest(source, source_pin, signature)
        target = source
        for old, new in edits if forward else reversed(edits):
            anchor, replacement = (old, new) if forward else (new, old)
            if target.count(anchor) != 1:
                raise RuntimeError(
                    f"The database definition for {label} has no unique replacement point. "
                    "The migration stopped. Its changes will roll back. "
                    "Keep the current schema and ask the database operator to check its version."
                )
            target = target.replace(anchor, replacement, 1)
        previous.require_digest(target, target_pin, signature)
        swap(cursor, signature, source.split("$function$")[1], target.split("$function$")[1])
        previous.require_digest(previous.installed_definition(cursor, signature), target_pin, signature)


MEMBERSHIP_GUARD = """
CREATE FUNCTION public.care_membership_subject() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT FROM public.core_identity WHERE id=NEW.identity_id AND identity_id='{care}'::uuid) THEN
        RAISE EXCEPTION 'CARE_MEMBERSHIP_SUBJECT' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER care_membership_subject BEFORE INSERT ON public.core_organismmembership
    FOR EACH ROW EXECUTE FUNCTION public.care_membership_subject();
""".format(care=CARE_ID)


def install_migration(apps, schema_editor):
    # The separately callable install() above also serves the test backend's data reseed.
    install(apps, schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            ALTER TABLE public.core_careofficeobservation
              ALTER COLUMN requester_id DROP NOT NULL,
              ALTER COLUMN requester_epoch DROP NOT NULL,
              ADD CONSTRAINT care_observation_requester_erased
                CHECK (requester_id IS NULL AND requester_epoch IS NULL) NOT VALID
        """)
        replace_observation_input(cursor, forward=True)
        replace_event_functions(cursor, forward=True)
        cursor.execute(MEMBERSHIP_GUARD)


# Build one EXISTS per installed FK, including composite keys. Only the seed's
# own identity-to-credential and resolution rows can be removed together.
REFERENCE_CHECKS = """
    SELECT format(
        'SELECT EXISTS (SELECT FROM %I.%I child JOIN %I.%I parent ON %s '
        'WHERE parent.%I = %%s %s)',
        ns.nspname, child.relname, pns.nspname, parent.relname,
        string_agg(format('child.%I = parent.%I', ca.attname, pa.attname),
                   ' AND ' ORDER BY keys.ordinality),
        CASE WHEN parent.relname = 'core_identityresolution' THEN 'identity_id' ELSE 'id' END,
        CASE WHEN child.oid = 'public.core_identityresolution'::regclass THEN 'AND child.identity_id <> %s'
             WHEN child.oid = 'public.core_identity'::regclass THEN 'AND child.id <> %s'
             ELSE '' END),
        parent.oid = 'public.auth_user'::regclass,
        child.oid IN ('public.core_identityresolution'::regclass, 'public.core_identity'::regclass)
    FROM pg_catalog.pg_constraint fk
    JOIN pg_catalog.pg_class child ON child.oid = fk.conrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = child.relnamespace
    JOIN pg_catalog.pg_class parent ON parent.oid = fk.confrelid
    JOIN pg_catalog.pg_namespace pns ON pns.oid = parent.relnamespace
    CROSS JOIN LATERAL unnest(fk.conkey, fk.confkey) WITH ORDINALITY keys(c, p, ordinality)
    JOIN pg_catalog.pg_attribute ca ON ca.attrelid = child.oid AND ca.attnum = keys.c
    JOIN pg_catalog.pg_attribute pa ON pa.attrelid = parent.oid AND pa.attnum = keys.p
    WHERE fk.contype = 'f'
      AND fk.confrelid IN ('public.core_identity'::regclass, 'public.auth_user'::regclass,
                           'public.core_identityresolution'::regclass)
    GROUP BY fk.oid, child.oid, ns.nspname, parent.oid, pns.nspname,
             child.relname, parent.relname
"""


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        # Hold insertions through the preflight and both NOT NULL restorations.
        cursor.execute("LOCK TABLE public.core_careofficeobservation IN ACCESS EXCLUSIVE MODE")
        # The typed evidence snapshots below have no FK lock on CARE's
        # identity. Hold their writers until the reverse commits or rolls back.
        cursor.execute("""
            LOCK TABLE public.core_eventresourcerelationshipevidence, public.core_commandintake,
                       public.core_authoritydecision IN SHARE MODE
        """)
        cursor.execute(
            """
            SELECT i.id, u.id
            FROM public.core_identity i
            JOIN public.auth_user u ON u.id = i.credential_id
            JOIN public.core_identityresolution r ON r.identity_id = i.id AND r.credential_link = u.id
            WHERE i.identity_id = %s FOR UPDATE OF i, u, r
        """,
            [CARE_ID],
        )
        seed = cursor.fetchone()
        if seed is None:
            raise RuntimeError(INCOMPLETE)
        identity_pk, credential_id = seed
        # These evidence snapshots store typed identifiers independently of their FKs.
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT FROM public.core_eventresourcerelationshipevidence
                WHERE actor_identity_id = %s OR viewer_identity_id = %s
            ) OR EXISTS (
                SELECT FROM public.core_commandintake WHERE actor_identity_id = %s
            ) OR EXISTS (
                SELECT FROM public.core_authoritydecision WHERE actor_identity_id = %s OR actor_credential_pk = %s
            )
        """,
            [CARE_ID, CARE_ID, CARE_ID, CARE_ID, credential_id],
        )
        if cursor.fetchone()[0]:
            raise RuntimeError(IN_USE)
        cursor.execute(REFERENCE_CHECKS)
        for query, references_credential, exclude_own_row in cursor.fetchall():
            params = [credential_id if references_credential else identity_pk]
            if exclude_own_row:
                params.append(identity_pk)
            cursor.execute(query, params)
            if cursor.fetchone()[0]:
                raise RuntimeError(IN_USE)
        # The old schema cannot represent a new-format detail, even when no
        # committed event yet references CARE (for example within a deferred pair).
        cursor.execute("""
            SELECT EXISTS (SELECT FROM public.core_careofficeobservation
                           WHERE requester_id IS NULL OR requester_epoch IS NULL)
        """)
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "CARE's migration could not be reversed. An observation uses the new private record format. "
                "Its history has been kept. Keep this migration in place and ask the database operator "
                "to review the observation history."
            )
        cursor.execute("DROP TRIGGER care_membership_subject ON public.core_organismmembership")
        cursor.execute("DROP FUNCTION public.care_membership_subject()")
        replace_event_functions(cursor, forward=False)
        replace_observation_input(cursor, forward=False)
        cursor.execute("""
            ALTER TABLE public.core_careofficeobservation
              DROP CONSTRAINT care_observation_requester_erased,
              ALTER COLUMN requester_id SET NOT NULL,
              ALTER COLUMN requester_epoch SET NOT NULL
        """)
        cursor.execute(
            "ALTER TABLE public.core_identityresolution DISABLE TRIGGER s015_0022_resolution_severance"
        )
        cursor.execute("DELETE FROM public.core_identityresolution WHERE identity_id = %s", [identity_pk])
        cursor.execute(
            "ALTER TABLE public.core_identityresolution ENABLE TRIGGER s015_0022_resolution_severance"
        )
        cursor.execute("DELETE FROM public.core_identity WHERE identity_id = %s", [CARE_ID])
        cursor.execute("DELETE FROM public.auth_user WHERE id = %s", [credential_id])


class Migration(migrations.Migration):
    atomic = True
    dependencies = (("core", "0112_identity_colour_mode"),)
    operations = (migrations.RunPython(install_migration, uninstall),)
