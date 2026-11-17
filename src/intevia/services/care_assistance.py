"""The organisation's CARE setting and its interim Organisation Coordinator command."""
from uuid import UUID

from django.db import IntegrityError, connection, transaction
from psycopg import sql

from src.intevia.services import cq_reads, discussion as disc, discussion_posts
from src.intevia.services.organism_command_support import _lock_registered_organism


_MEMBER_STATES = ("ACTIVE", "SUSPENDED", "RESTRICTED", "PROBATIONARY")

_MESSAGES = {
    "CARE_SETTING_INPUT": "Supply exact references and choose on or off.",
    "CARE_SETTING_UNAVAILABLE": "This office record is unavailable to you.",
    "CARE_SETTING_AUTHORITY": "Only the Organisation Coordinator can change CARE assistance.",
    "CARE_SETTING_PREDECESSOR": "The CARE setting has changed. Reload it before choosing again.",
    "CARE_SETTING_REPLAY": "This request reference was already used for a different action.",
}


class SettingRefused(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(_MESSAGES[code])


def _actor(credential):
    if getattr(credential, "pk", None) is None:
        raise SettingRefused("CARE_SETTING_UNAVAILABLE")
    try:
        return disc._actor(credential)
    except disc.DiscussionUnavailable:
        raise SettingRefused("CARE_SETTING_UNAVAILABLE") from None


def _lock_scope(owner_uuid, actor):
    # Check possible access before the registered lock, which can report an unknown owner.
    rows = disc._rows("""
        SELECT lo.id,public.disc_member_state(lo.id,%s,public.intevia_clock_statement()) AS state
          FROM public.core_livingorganism lo WHERE lo.organism_id=%s
    """, [actor.pk, owner_uuid])
    if not rows or rows[0]["state"] not in _MEMBER_STATES:
        raise SettingRefused("CARE_SETTING_UNAVAILABLE")
    _lock_registered_organism("default", owner_uuid)
    return rows[0]["id"]


def _authorize(owner_id, actor, received):
    # Recheck standing after the wait, even for a replay or a no-op.
    scope = disc._rows("""
        SELECT public.disc_member_state(%(owner)s,%(actor)s,public.intevia_clock_statement()) AS state,
               public.disc_office(%(owner)s,NULL,%(actor)s,%(received)s) AS at_receipt,
               public.disc_office(%(owner)s,NULL,%(actor)s,public.intevia_clock_statement()) AS at_execution
    """, {"owner": owner_id, "actor": actor.pk, "received": received})[0]
    if scope["state"] not in _MEMBER_STATES:
        raise SettingRefused("CARE_SETTING_UNAVAILABLE")
    source = scope["at_execution"]
    if not source or source != scope["at_receipt"]:
        raise SettingRefused("CARE_SETTING_AUTHORITY")


def _existing_request(params):
    # One global key lookup covers both uniqueness domains, including an unrelated ILC action.
    rows = disc._rows("""
        SELECT a.organism_id=%(owner)s AND e.living_organism_id=a.organism_id
               AND e.action='SET_CARE_ASSISTANCE'
               AND a.fingerprint=public.care_setting_intent(%(organisation)s,%(person)s,
                   %(epoch)s,%(key)s,%(prior)s,%(enabled)s) AS exact
          FROM (SELECT act_uuid,organism_id,fingerprint FROM public.core_careassistancesettingact
                 WHERE act_uuid=%(key)s) a
          FULL JOIN (SELECT event_uuid,living_organism_id,action FROM public.core_livingorganismevent
                      WHERE event_uuid=%(key)s) e ON e.event_uuid=a.act_uuid
    """, params)
    if rows and not rows[0]["exact"]:
        raise SettingRefused("CARE_SETTING_REPLAY")
    return bool(rows)


def _receipt(owner_id, key):
    return disc._rows("""
        SELECT 'recorded'::text AS status,act_uuid,version,predecessor,enabled,received_at,effective_at,recorded_at
          FROM public.core_careassistancesettingact WHERE organism_id=%s AND act_uuid=%s
    """, [owner_id, key])[0]


def _write_pair(owner_id, actor, key, prior, enabled, received):
    # Both guards see one execution clock. Use only the detail values derived by the input guard.
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("""
            DO $care$
            DECLARE
                choice public.core_careassistancesettingact;
                org public.core_livingorganism;
                head public.core_livingorganismevent;
                state_at_cut text;
            BEGIN
                INSERT INTO public.core_careassistancesettingact
                    (act_uuid,organism_id,predecessor,enabled,actor_id,actor_epoch,received_at)
                VALUES ({key},{owner},{prior},{enabled},{actor},{epoch},{received})
                RETURNING * INTO STRICT choice;
                SELECT * INTO STRICT org FROM public.core_livingorganism WHERE id=choice.organism_id;
                SELECT * INTO STRICT head FROM public.core_livingorganismevent WHERE id=org.head_event_id;
                state_at_cut:=public.s015_fold_effective_state(
                    'core_livingorganism',org.id,choice.effective_at,choice.recorded_at);
                PERFORM public.s015_append_event(
                    'core_livingorganism',org.id,'SET_CARE_ASSISTANCE',state_at_cut,
                    choice.effective_at,choice.occurred_at,choice.received_at,choice.actor_id,
                    org.founding_authority_basis_id,public.care_setting_decision(choice.authority_source,choice.fingerprint),
                    'care-setting-act:'||choice.act_uuid,'care-setting:'||choice.act_uuid,
                    choice.fingerprint,jsonb_build_object(),NULL,choice.act_uuid,
                    head.id,head.sequence::integer,NULL,NULL,'care-setting:'||choice.act_uuid);
            END $care$;
        """).format(key=sql.Literal(key), owner=sql.Literal(owner_id), prior=sql.Literal(prior),
                    enabled=sql.Literal(enabled), actor=sql.Literal(actor.pk), epoch=sql.Literal(actor.access_epoch),
                    received=sql.Literal(received)))


def set_assistance(*, credential, organism_uuid, enabled, predecessor, request_uuid):
    """Record an immediate choice, return the original receipt on replay, or report the current source."""
    if (not isinstance(organism_uuid, UUID) or not isinstance(request_uuid, UUID)
            or (predecessor is not None and not isinstance(predecessor, UUID)) or type(enabled) is not bool):
        raise SettingRefused("CARE_SETTING_INPUT")
    try:
        with transaction.atomic(durable=True):
            # Bind person and epoch at the database receipt, before either lock can wait.
            initial = disc._rows("""
                SELECT i.id,i.access_epoch,public.intevia_clock_statement() AS received
                  FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
                 WHERE u.id=%s AND u.is_active AND i.access_state='active'
            """, [getattr(credential, "pk", None)])
            if not initial:
                raise SettingRefused("CARE_SETTING_UNAVAILABLE")
            receipt = initial[0]
            actor = _actor(credential)
            if (actor.pk, actor.access_epoch) != (receipt["id"], receipt["access_epoch"]):
                raise SettingRefused("CARE_SETTING_UNAVAILABLE")
            owner_id = _lock_scope(organism_uuid, actor)
            _authorize(owner_id, actor, receipt["received"])
            params = dict(owner=owner_id, organisation=organism_uuid, person=actor.identity_id, epoch=actor.access_epoch,
                          key=request_uuid, prior=predecessor, enabled=enabled)
            if _existing_request(params):
                return _receipt(owner_id, request_uuid)
            current = disc._rows("""
                SELECT act_uuid,enabled FROM public.core_careassistancesettingact
                 WHERE organism_id=%s ORDER BY version DESC LIMIT 1
            """, [owner_id])
            current_key = current[0]["act_uuid"] if current else None
            if predecessor != current_key:
                raise SettingRefused("CARE_SETTING_PREDECESSOR")
            if current and current[0]["enabled"] == enabled:
                return {"status": "already on" if enabled else "already off", "source_act_uuid": current_key}
            _write_pair(owner_id, actor, request_uuid, predecessor, enabled, receipt["received"])
            return _receipt(owner_id, request_uuid)
    except IntegrityError as exc:
        cause = exc.__cause__
        diag = getattr(cause, "diag", None)
        code = getattr(diag, "message_primary", None)
        if code in ("CARE_SETTING_AUTHORITY", "CARE_SETTING_PREDECESSOR"):
            raise SettingRefused(code) from None
        # A different owner can commit the same key between the global lookup and this INSERT.
        if (getattr(cause, "sqlstate", None) == "23505" and getattr(diag, "constraint_name", None) in
                ("core_careassistancesettingact_pkey", "care_setting_owner_act", "core_livingorganismevent_event_uuid_key")):
            raise SettingRefused("CARE_SETTING_REPLAY") from None
        raise


@transaction.atomic
def read_assistance(*, credential, organism_uuid, office_definition_uuid):
    """Read the current choice for someone entitled to identify this Board office."""
    if not isinstance(organism_uuid, UUID) or not isinstance(office_definition_uuid, UUID):
        raise SettingRefused("CARE_SETTING_UNAVAILABLE")
    actor = _actor(credential)
    owner_id = _lock_scope(organism_uuid, actor)
    access = _office_access_locked(credential, organism_uuid, office_definition_uuid, actor, owner_id)
    return _setting_projection(access, actor, organism_uuid)


def _office_access_locked(credential, organism_uuid, office_definition_uuid, actor, owner_id):
    """Admit the requested Board definition under the actor and registered owner locks."""
    rows = disc._rows("""
        SELECT d.definition_version,d.code,public.intevia_clock_statement() AS evaluated_at,
               public.disc_member_state(d.living_organism_id,%(reader)s,public.intevia_clock_statement()) AS state,
               public.disc_office(d.living_organism_id,NULL,%(reader)s,public.intevia_clock_statement()) IS NOT NULL AS can_change,
               a.enabled,a.act_uuid,a.authority_source,j.identity_id AS enterer_identity_uuid
          FROM public.core_organismroledefinition d
          LEFT JOIN LATERAL (
              SELECT act_uuid,enabled,authority_source,enterer_id FROM public.core_careassistancesettingact
               WHERE organism_id=d.living_organism_id AND effective_at<=public.intevia_clock_statement()
                 AND recorded_at<=public.intevia_clock_statement() ORDER BY version DESC LIMIT 1
          ) a ON true
          LEFT JOIN public.core_identity j ON j.id=a.enterer_id
         WHERE d.living_organism_id=%(owner)s AND d.role_uuid=%(office)s
           AND d.scope='LIVING_ORGANISM' AND d.kind='BOARD_OFFICE'
           AND d.received_at<=public.intevia_clock_statement() AND d.recorded_at<=public.intevia_clock_statement()
           AND d.effective_at<=public.intevia_clock_statement()
    """, {"owner": owner_id, "reader": actor.pk, "office": office_definition_uuid})
    if not rows or rows[0]["state"] not in _MEMBER_STATES:
        raise SettingRefused("CARE_SETTING_UNAVAILABLE")
    row = rows[0]
    if row["state"] == "PROBATIONARY":
        topics = discussion_posts.topics_about_office(credential=credential, organism_uuid=organism_uuid,
                                                      office_definition_uuid=office_definition_uuid)
        if not any(topic["office_definition_uuid"] == office_definition_uuid for topic in topics):
            raise SettingRefused("CARE_SETTING_UNAVAILABLE")
        row["visible_topics"] = topics
    disc.handoff.mark(organism_uuid, actor)
    return row


def _setting_projection(row, actor, organism_uuid):
    if row["act_uuid"] is None:
        return {"enabled": True, "choice_source": "default", "can_change": row["can_change"]}
    # Apply the landed settings-source/enterer policy before selecting the public fields.
    choice = cq_reads._permitted_versions([row], actor, organism_uuid)[0]
    result = {"enabled": choice["enabled"], "choice_source": "chosen", "can_change": choice["can_change"],
              "switch_receipt": choice["act_uuid"]}
    if choice.get("enterer_identity_uuid") is not None:
        result["switched_by"] = choice["enterer_identity_uuid"]
    return result
