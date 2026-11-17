"""Shared database fixtures for CARE settings and office-record tests."""
import json
from uuid import uuid4

from django.db import connection
from psycopg import sql

from core.management.commands import setup_alpha_trustee as alpha
from src.intevia.services import law_acts, library_policy
from src.intevia.services import organism_role_definition_service as definitions
from tests import test_law_acts as laws
from tests.test_discussion_office_links import record_office


def a1_adopt_policy(owner, founder, resource, body, *, prior_version=None, prior_binding=None):
    scope = {"credential": founder.credential, "organism_uuid": owner.organism_id}
    version = library_policy.draft_policy(
        **scope, resource_reference=resource, content=body, authors=[founder.identity_id],
        declare_own_authorship=True, request_uuid=uuid4(),
        predecessor_version_id=prior_version.pk if prior_version else None)
    library_policy.publish_policy(**scope, version_id=version.pk, request_uuid=uuid4())
    proposal = law_acts.LawProposal(
        organism=owner.organism_id, resource=resource, proposal=uuid4(), predecessor=prior_binding,
        operation="ADOPT", intent="ADOPT", law_class="POLICY", version=version.version_number)
    receipt = law_acts.OrganismLawService().adopt_law(laws.command(founder, proposal))
    return version, proposal.proposal, receipt


def a1_definition(resource, version):
    basis = definitions.DefinitionProvision("OFFICE_BASIS", f"law-ref:{resource}#approval", str(version))
    return definitions.DefinitionContent(code=alpha.TREASURER_CODE, provisions=(basis,))


def approved_office(*, board_seats=1):
    owner, founder = laws.ordinary_birth(board_seats=board_seats)
    resource = "a2-duties-" + uuid4().hex
    version, binding, receipt = a1_adopt_policy(owner, founder, resource, alpha.DUTIES_TEXT)
    if receipt.status != "ADOPT":
        raise AssertionError("The Treasurer fixture requires an adopted local duties Policy.")
    definition = a1_definition(resource, version.version_number)
    office = record_office(owner, founder, definition)
    return owner, founder, office, definition, version, binding


def observation_footprint(owner):
    with connection.cursor() as cur:
        cur.execute("""SELECT ARRAY(SELECT event_uuid FROM public.core_careofficeobservation
                                    WHERE organism_id=o.id ORDER BY event_uuid),
                             ARRAY(SELECT event_uuid FROM public.core_livingorganismevent
                                   WHERE living_organism_id=o.id AND action='RECORD_CARE_OFFICE_OBSERVATION'
                                   ORDER BY event_uuid),o.head_event_id
                      FROM public.core_livingorganism o WHERE o.id=%s""", [owner.pk])
        return cur.fetchone()


def submit_pair(owner, actor, key, mode, prior=None, enabled=False, epoch=None):
    with connection.cursor() as cur:
        cur.execute(sql.SQL("""
            DO $pair$
            DECLARE
                org public.core_livingorganism;
                head public.core_livingorganismevent;
                src jsonb;
                fp text;
                state_at_cut text;
                stamp timestamptz := public.intevia_clock_statement();
                key_ uuid := {key};
                prior_ uuid := {prior};
                actor_ bigint := {actor_pk};
                epoch_ bigint := {epoch};
                enabled_ boolean := {enabled};
                mode_ text := {mode};
            BEGIN
                SELECT * INTO STRICT org FROM public.core_livingorganism WHERE id={owner_pk};
                src := public.disc_office(org.id,NULL,actor_,stamp);
                fp := public.care_setting_intent(org.organism_id,{actor_uuid}::uuid,
                                                 epoch_,key_,prior_,enabled_);
                IF mode_ <> 'event_only' THEN
                    INSERT INTO public.core_careassistancesettingact
                        (act_uuid,organism_id,predecessor,enabled,actor_id,actor_epoch,received_at)
                    VALUES (key_,org.id,prior_,enabled_,actor_,epoch_,stamp);
                END IF;
                IF mode_ <> 'detail_only' THEN
                    SELECT * INTO STRICT head FROM public.core_livingorganismevent
                    WHERE id=org.head_event_id;
                    state_at_cut := public.s015_fold_effective_state('core_livingorganism',org.id,stamp,stamp);
                    PERFORM public.s015_append_event(
                        'core_livingorganism',org.id,'SET_CARE_ASSISTANCE',state_at_cut,
                        stamp,stamp,stamp,actor_,org.founding_authority_basis_id,
                        CASE WHEN mode_='wrong_authority' THEN 's015d1:'||repeat('0',64)
                             ELSE public.care_setting_decision(src,fp) END,'care-setting-act:'||key_,
                        'care-setting:'||key_,
                        CASE WHEN mode_='wrong_fingerprint' THEN repeat('0',64) ELSE fp END,
                        jsonb_build_object(),NULL,key_,head.id,head.sequence::integer,NULL,NULL,'care-setting:'||key_);
                END IF;
            END $pair$;
        """).format(key=sql.Literal(key), prior=sql.Literal(prior), actor_pk=sql.Literal(actor.pk),
                    actor_uuid=sql.Literal(actor.identity_id), owner_pk=sql.Literal(owner.pk),
                    enabled=sql.Literal(enabled), mode=sql.Literal(mode),
                    epoch=sql.Literal(actor.access_epoch if epoch is None else epoch)))
        # Select the action-completion constraint before the deferred FK backstop.
        cur.execute("SET CONSTRAINTS care_setting_detail_complete, care_setting_event_complete IMMEDIATE")
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


def footprint(owner):
    with connection.cursor() as cur:
        cur.execute("""SELECT head_event_id,
            ARRAY(SELECT act_uuid FROM public.core_careassistancesettingact
                  WHERE organism_id=o.id ORDER BY version),
            ARRAY(SELECT event_uuid FROM public.core_livingorganismevent
                  WHERE living_organism_id=o.id AND action='SET_CARE_ASSISTANCE' ORDER BY sequence)
            FROM public.core_livingorganism o WHERE id=%s""", [owner.pk])
        return cur.fetchone()


def read_source_candidate(owner, reader, office):
    with connection.cursor() as cur:
        cur.execute("""SELECT to_jsonb(q) FROM public.care_alpha_treasurer_qualify(
                       %s,%s,%s,%s,public.intevia_clock_statement(),true) q""",
                    [owner.organism_id, office, reader.pk, reader.access_epoch])
        rows = cur.fetchall()
    if len(rows) != 1:
        raise AssertionError("The lawful vacant Treasurer fixture must have exactly one qualified source.")
    value = rows[0][0]
    return json.loads(value) if isinstance(value, str) else value


def propose_observation(owner, reader, claim, key, mode="matched", event_owner=None, enterer=None, epoch=None):
    """Use the runtime role, two real locks, one execution statement and the normal shared writer."""
    event_owner = event_owner or owner
    enterer = enterer or reader
    with connection.cursor() as cur:
        cur.execute("SELECT public.intevia_clock_statement()")
        received, = cur.fetchone()
        cur.execute("""SELECT i.id FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
                       WHERE i.id=%s FOR NO KEY UPDATE OF i,u""", [reader.pk])
        owners = sorted({owner.organism_id, event_owner.organism_id})
        cur.execute("SELECT public.s015_lock_organisms(%s::uuid[])", [owners])
        # Times and the proposed digest are mutually consistent even for an invented clause.
        # No part of the source fabrication bypasses the guarded INSERT.
        cur.execute(sql.SQL("""
            DO $case$
            DECLARE
                d public.core_careofficeobservation;
                lo public.core_livingorganism;
                h public.core_livingorganismevent;
                stamp timestamptz := public.intevia_clock_statement();
                received timestamptz := {received};
                fp text;
                state_at_cut text;
            BEGIN
                SELECT * INTO d FROM jsonb_populate_record(NULL::public.core_careofficeobservation,{claim}::jsonb);
                d.event_uuid:={key}; d.requester_id:={reader}; d.requester_epoch:={epoch};
                d.received_at:=received; d.qualification_at:=stamp;
                fp:=public.care_observation_payload(d);
                d.payload_fingerprint:=fp;
                d.qualification_at:=NULL;
                IF {mode} <> 'event_only' THEN
                    INSERT INTO public.core_careofficeobservation SELECT (d).*;
                END IF;
                IF {mode} <> 'detail_only' THEN
                    SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id={event_owner};
                    SELECT * INTO STRICT h FROM public.core_livingorganismevent WHERE id=lo.head_event_id;
                    state_at_cut:=public.s015_fold_effective_state('core_livingorganism',lo.id,stamp,stamp);
                    PERFORM public.s015_append_event('core_livingorganism',lo.id,'RECORD_CARE_OFFICE_OBSERVATION',
                        state_at_cut,stamp,received,received,{enterer},lo.founding_authority_basis_id,
                        public.care_observation_decision(d.rule_reference,fp),'care-office-observation:'||d.event_uuid,
                        'care-office-presentation:'||d.event_uuid,fp,jsonb_build_object(),NULL,d.event_uuid,
                        h.id,h.sequence::integer,NULL,d.rule_reference,'care-office-presentation:'||d.event_uuid);
                END IF;
            END $case$;
        """).format(claim=sql.Literal(json.dumps(claim, default=str)), key=sql.Literal(key),
                    reader=sql.Literal(reader.pk), epoch=sql.Literal(reader.access_epoch if epoch is None else epoch),
                    received=sql.Literal(received),
                    event_owner=sql.Literal(event_owner.pk), enterer=sql.Literal(enterer.pk), mode=sql.Literal(mode)))
        cur.execute("SET CONSTRAINTS care_observation_detail_complete, care_observation_event_complete IMMEDIATE")
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
