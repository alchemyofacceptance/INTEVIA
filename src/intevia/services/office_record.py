"""Office facts and the post-commit boundary for first display of Alpha CARE guidance."""
from uuid import UUID, uuid4

from django.db import DatabaseError, connection, transaction
from psycopg import sql

from src.intevia.services import care_assistance as care
from src.intevia.services import discussion as disc
from src.intevia.services import cq_reads, discussion_posts, law_records, library_laws


# Only public fields leave this module. The event join also requires the stored pair.
_PUBLIC = """o.event_uuid AS event_ref,o.sentence AS words,o.provision_uuid AS source_link,
             o.locator AS source_locator,o.library_version_number AS source_version,o.qualification_at AS at"""
_PAIR = """public.core_careofficeobservation o JOIN public.core_livingorganismevent e
          ON e.event_uuid=o.event_uuid AND e.living_organism_id=o.organism_id
          AND e.action='RECORD_CARE_OFFICE_OBSERVATION'"""


def _tenure(family_ids, owner_id, at):
    # A rejoin retains the membership anchor. Bind its departure to this
    # appointment's period, using the original proposal and the actual award.
    rows = disc._rows("""
        WITH appointments AS (
            SELECT a.assignment_uuid AS occupancy,a.id AS assignment_id,m.id AS member_id,m.identity_id,
                   award.effective_at AS starts,
                   least(public.elect_office_deadline(award.id,%(at)s),
                         (SELECT min(x.effective_at) FROM public.core_contextualroleassignmentstateevent x
                           WHERE x.assignment_id=a.id AND x.resulting_state='ENDED'
                             AND greatest(x.received_at,x.recorded_at)<=%(at)s),
                         (SELECT min(x.effective_at) FROM public.core_organismmembershiptransition x
                           WHERE x.membership_id=m.id AND x.resulting_state='ENDED'
                             AND (x.recorded_at>origin.recorded_at OR x.effective_at>=award.effective_at)
                             AND greatest(x.received_at,x.recorded_at)<=%(at)s)) AS ends
              FROM public.core_contextualroleassignment a
              JOIN public.core_organismmembership m ON m.id=a.membership_id AND m.living_organism_id=%(owner_id)s
              JOIN public.core_contextualroleassignmentstateevent origin ON origin.assignment_id=a.id AND origin.sequence=1
              JOIN public.core_contextualroleassignmentstateevent award ON award.assignment_id=a.id AND award.action='ASSIGN'
             WHERE a.role_definition_id=ANY(%(versions)s::bigint[]) AND a.circle_id IS NULL
               AND award.effective_at<=%(at)s AND greatest(award.received_at,award.recorded_at)<=%(at)s
        ), terms AS (
            SELECT p.*,public.elect_term_includes(%(at)s,p.starts,p.ends)
                       AND public.elect_office_effective(p.assignment_id,%(at)s,%(at)s) AS ongoing
              FROM appointments p WHERE p.ends IS NULL OR p.starts<p.ends
        ), held AS (
            SELECT t.* FROM terms t WHERE t.ongoing OR (
                t.ends<=%(at)s AND EXISTS (
                    SELECT FROM (
                        SELECT t.starts AS moment
                        UNION ALL
                        SELECT e.effective_at FROM public.core_contextualroleassignmentstateevent e
                         WHERE e.assignment_id=t.assignment_id AND e.resulting_state='ACTIVE'
                           AND greatest(e.received_at,e.recorded_at)<=%(at)s
                        UNION ALL
                        SELECT m.effective_at FROM public.core_organismmembershiptransition m
                         WHERE m.membership_id=t.member_id AND m.resulting_state='ACTIVE'
                           AND greatest(m.received_at,m.recorded_at)<=%(at)s
                    ) probe
                    WHERE probe.moment>=t.starts AND probe.moment<t.ends
                      AND public.elect_office_effective(t.assignment_id,probe.moment,%(at)s)
                )
            )
        )
        SELECT visible_vacancy,ongoing,starts,ends,holder_name
          FROM (
            SELECT false AS visible_vacancy,h.ongoing,h.starts,h.ends,r.display_name AS holder_name,h.occupancy
              FROM held h
              LEFT JOIN public.core_identityresolution r ON r.identity_id=h.identity_id
               AND NOT EXISTS (SELECT FROM public.core_identityresolutionsevered z WHERE z.identity_id=h.identity_id)
            UNION ALL
            SELECT true,false,NULL,NULL,NULL,NULL
             WHERE NOT EXISTS (SELECT FROM terms WHERE ongoing)
          ) visible
         ORDER BY visible_vacancy DESC,ongoing DESC,starts DESC,occupancy
    """, {"versions": family_ids, "owner_id": owner_id, "at": at})
    tenure = []
    for row in rows:
        if row["visible_vacancy"]:
            tenure.append({"state": "vacant"})
            continue
        term = {"state": "ongoing" if row["ongoing"] else "former",
                "starts_at": row["starts"], "ends_at": row["ends"]}
        if row["holder_name"]:
            term["holder_name"] = row["holder_name"]
        tenure.append(term)
    return tenure


def _office_facts(params, access, setting):
    at = access["evaluated_at"]
    office = {"organism_uuid": params["owner"], "requested_definition": params["office"],
              "requested_version": access["definition_version"]}
    page = {"evaluated_at": at, "office": office, "care": setting, "tenure": [], "history": []}
    # An exact Topic pin identifies its own definition/version. It grants neither
    # a family lookup nor a read of a holder, an ending or the fact of a vacancy.
    if access["state"] not in ("ACTIVE", "SUSPENDED", "RESTRICTED"):
        return page
    code = access["code"]
    office.update(code=code, label=code)  # OrganismRoleDefinition.office_label for BOARD_OFFICE.
    current = disc._rows("""
        WITH RECURSIVE family AS (
            SELECT id,role_uuid,definition_version,supersedes_id,active FROM public.core_organismroledefinition
             WHERE living_organism_id=%(owner)s AND scope='LIVING_ORGANISM' AND kind='BOARD_OFFICE'
               AND code=%(code)s AND effective_at<=%(at)s AND received_at<=%(at)s AND recorded_at<=%(at)s
        ), lineage AS (
            SELECT id AS current_id,id,role_uuid,supersedes_id FROM family WHERE active
            UNION
            SELECT l.current_id,p.id,p.role_uuid,p.supersedes_id
              FROM lineage l JOIN family p ON p.id=l.supersedes_id
        )
        SELECT c.id,c.role_uuid,c.definition_version,array_agg(l.id) AS family_ids
          FROM lineage l JOIN family c ON c.id=l.current_id
         GROUP BY c.id,c.role_uuid,c.definition_version HAVING bool_or(l.role_uuid=%(requested)s)
    """, {"owner": params["owner_id"], "code": code, "at": at, "requested": params["office"]})
    if len(current) == 1:
        role = current[0]
        office.update(current_definition=role["role_uuid"], current_version=role["definition_version"])
        page["tenure"] = _tenure(role["family_ids"], params["owner_id"], at)
    return page


# The LAW register's decision_route describes who adopted the instrument.
# In particular an issuing organisation alone does not establish a Board vote.
_ADOPTIONS = {
    "FOUNDER_CONSTITUTIVE": "Founder constitutive decision",
    "COORDINATOR_ROUTE": "Organisation Coordinator decision",
    "SOLE_MEMBER_COORDINATOR": "Sole-member Organisation Coordinator decision",
    "UNANIMOUS_PAIR": "Unanimous member agreement",
    "GOVERNANCE_INTERIM": "Interim Board Coordinator decision",
    "CIRCLE_INTERIM": "Interim Circle decision",
    "CQ_MEMBERSHIP": "Membership vote",
    "CQ_GOVERNANCE_CIRCLE": "Board vote",
    "CQ_CIRCLE": "Circle vote",
    "FOUNDER_STEWARD": "Founder Steward decision",
    "STEWARDS_PROPOSE_FOUNDER_AUTHORISES": "Stewards propose and Founder authorises",
    "STEWARDS_AUTHORISED": "Authorised stewards",
}


def _provision_at(citation, binding, allowed, status):
    if not binding:
        return None
    law = allowed.get((binding["binding_uuid"], binding["version_id"]))
    if not law or law["act"] != binding["adoption_act"] or law["library_reference"] != citation["resource_reference"]:
        return None
    result = {"relationship": citation["relationship"], "status": status, "binding": law["binding"],
              "version": law["version_number"], "adoption_record": law["act"], "adopting_body": law["issuer"],
              "in_force_from": law["in_force_from"], "recorded_at": law["adoption_recorded_at"]}
    locator = citation["locator"]
    if locator is not None:
        clauses = disc._rows("SELECT content FROM public.l_clauses(%s) WHERE locator=%s", [law["content"], locator])
        if len(clauses) != 1:
            return None
        result.update(locator=locator, clause=clauses[0]["content"])
    else:
        # LAW recognises a whole-document citation only for a protected charter.
        charter = disc._rows("SELECT public.l_charter_pin(%s) IS NOT NULL AS protected", [law["library_reference"]])
        if not charter[0]["protected"]:
            return None
        result["clause"] = law["content"]
    route = _ADOPTIONS.get(law["decision_route"])
    if route:
        result["adoption_route"] = route
    source = law["authority_source"] or {}
    result_post = source.get("result_post")
    if source.get("kind") == "CQ_RESULT" and result_post and source.get("reference") == result_post:
        # Only the adoption vote. A referral is a different act, even when visible.
        result["adoption_result_link"] = result_post
    return result


def _provisions(credential, owner, definition):
    candidates = disc._rows("""
        SELECT p.provision_uuid FROM public.core_organismroledefinitionprovision p
          JOIN public.core_organismroledefinition d ON d.id=p.definition_id
          JOIN public.core_livingorganism lo ON lo.id=d.living_organism_id
         WHERE lo.organism_id=%s AND d.role_uuid=%s AND d.scope='LIVING_ORGANISM' AND d.kind='BOARD_OFFICE'
         ORDER BY p.relationship
    """, [owner, definition])
    cuts = {}

    def allowed(at, known_at):
        key = (at, known_at)
        if key not in cuts:
            try:
                laws = library_laws.our_laws(credential=credential, organism_uuid=owner, at=at, known_at=known_at)["laws"]
            except library_laws.LawReadUnavailable:
                laws = []
            cuts[key] = {(law["binding"], law["version"]): law for law in laws}
        return cuts[key]

    provisions = []
    policies = []
    for candidate in candidates:
        views = law_records.resolve_citation(owner, candidate["provision_uuid"])
        if not views:
            continue
        cited = views["cited"]
        origin = cited["binding"]
        if not origin or origin["version_id"] != cited["cited_version"]:
            continue
        historical = _provision_at(cited, origin, allowed(cited["received_at"], cited["recorded_at"]),
                                   "cited_at_definition")
        now = views["now"]
        current = None
        if now["governing"]:
            governing = now["governing"]
            permitted = allowed(now["at"], now["known_at"])
            current = _provision_at(cited, governing, permitted, "in_force_now")
            if current and cited["relationship"] == "OFFICE_BASIS":
                law = permitted[(governing["binding_uuid"], governing["version_id"])]
                if law["class_name"] == "POLICY":
                    policies.append(law)
        if historical and current and historical["binding"] == current["binding"]:
            current["status"] = "cited_and_in_force"
            provisions.append(current)
        else:
            if historical:
                provisions.append(historical)
            if current:
                provisions.append(current)
    return provisions, policies


def _topics(rows):
    return [{"title": row["title"], "created_at": row["created_at"], "office_definition": row["office_definition_uuid"],
             "office_version": row["office_definition_version"], "board_uuid": row["board_uuid"],
             "circle_uuid": row["circle_uuid"], "category_uuid": row["category_uuid"], "thread_link": row["thread_uuid"]}
            for row in rows]


def _decision_result(credential, owner, reference, *, plain=False):
    # Resolve the exact destination privately before invoking its own public reader.
    matches = disc._rows("""
        SELECT r.board_uuid,c.circle_uuid,r.thread_uuid,r.effective_at,r.recorded_at,
               NOT EXISTS (SELECT FROM public.core_cqact i
                   WHERE i.organism_id=r.organism_id AND i.thread_uuid=r.thread_uuid
                     AND i.operation='INITIATE' AND i.request ? 'attachment') AS plain
          FROM public.core_cqact r
          JOIN public.core_livingorganism lo ON lo.id=r.organism_id
          JOIN public.core_discboard b ON b.board_uuid=r.board_uuid AND b.organism_id=lo.id
          JOIN public.core_circle c ON c.id=b.circle_id AND c.parent_organism_id=lo.id AND c.kind='GOVERNANCE'
         WHERE lo.organism_id=%s AND r.act_uuid=%s AND r.operation='SETTLE'
    """, [owner, reference])
    if len(matches) != 1 or (plain and not matches[0]["plain"]):
        return None
    target = matches[0]
    scope = dict(credential=credential, organism_uuid=owner, circle_uuid=target["circle_uuid"],
                 thread_uuid=target["thread_uuid"], result_uuid=reference)
    try:
        result = cq_reads.result_post(**scope)
    except disc.DiscussionUnavailable:
        return None
    if (result["post_uuid"] != reference or result["event_kind"] != "DECISION"
            or result["count"]["kind"] != "QUALIFIED" or result.get("restarted")):
        return None
    row = dict(body=target["circle_uuid"], result_uuid=reference, outcome="Carried.",
               effective_at=target["effective_at"], recorded_at=target["recorded_at"], result_link=reference)
    try:
        cq_reads.reporting_decision(**scope)
    except disc.DiscussionUnavailable:
        pass
    else:
        row["reporting_rule_link"] = reference
    return row, scope


def _decisions(credential, owner, policies):
    if len(policies) != 1:
        return []
    law = policies[0]
    # The binding's real Board adoption establishes the Policy's authority to cite.
    # Its result may be withheld independently of a readable payment result.
    adoptions = disc._rows("""
        SELECT r.act_uuid AS result
          FROM public.core_organismlawbinding b
          JOIN public.core_livingorganism lo ON lo.id=b.organism_id
          JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id AND e.living_organism_id=lo.id
          JOIN public.core_cqact i ON i.act_uuid=e.event_uuid AND i.organism_id=lo.id
               AND i.operation='IMPLEMENT' AND i.facts->'domain_plan'->>'domain'='LAW'
          JOIN public.core_cqact r ON r.act_uuid::text=i.request->>'result' AND r.organism_id=lo.id
               AND r.board_uuid=i.board_uuid AND r.thread_uuid=i.thread_uuid AND r.operation='SETTLE'
          JOIN public.core_discboard board ON board.board_uuid=r.board_uuid AND board.organism_id=lo.id
          JOIN public.core_circle c ON c.id=board.circle_id AND c.parent_organism_id=lo.id AND c.kind='GOVERNANCE'
         WHERE lo.organism_id=%s AND b.binding_uuid=%s AND b.version_id=%s AND e.event_uuid=%s
           AND b.law_class='POLICY' AND b.scope_kind='LIVING_ORGANISM'
           AND b.decision_route='CQ_GOVERNANCE_CIRCLE' AND r.facts->'count'->>'kind'='QUALIFIED'
           AND r.facts->>'disposition'='APPLIED'
    """, [owner, law["binding"], law["version"], law["act"]])
    if len(adoptions) != 1:
        return []
    decisions = []
    adoption = _decision_result(credential, owner, adoptions[0]["result"])
    if adoption is not None:
        row, scope = adoption
        try:
            effect = cq_reads.implementation_record(**scope)["effect"]
        except disc.DiscussionUnavailable:
            effect = None
        if (effect and effect.get("domain") == "LAW" and effect.get("implementation") == law["act"]
                and effect.get("adopted_binding") == law["binding"]):
            decisions.append(dict(row, purpose="standing_adoption", label="adopted the Policy in force",
                                  adopted_binding=law["binding"], adopted_version=law["version_number"]))
    markers = disc._rows("SELECT content FROM public.l_clauses(%s) WHERE locator='payment-result'", [law["content"]])
    if len(markers) != 1:
        return decisions
    text = markers[0]["content"].strip()
    try:
        reference = UUID(text)
    except ValueError:
        return decisions
    if str(reference) != text.lower():
        return decisions
    payment = _decision_result(credential, owner, reference, plain=True)
    if payment is not None:
        row, _ = payment
        decisions.append(dict(row, purpose="one_payment", label="cited by the Policy in force"))
    return decisions


def _history_row(kind, at, label, source, event, **fields):
    # The office page names no Enterer. The destination ILC applies its ordinary
    # Actor, Composer and Enterer rule; this link is not an embedded ILC entry.
    return dict(kind=kind, at=at, label=label, source_link=source,
                ilc_link=event, ilc_label="View in ILC", **fields)


def _human_history(page, params):
    rows = {}
    for law in page["provisions"]:
        key = ("law_adoption", law["adoption_record"])
        rows[key] = _history_row("law_adoption", law["in_force_from"], "Office provision adopted",
                                 law["binding"], law["adoption_record"])
    for decision in page["decisions"]:
        key = ("decision", decision["result_uuid"])
        rows[key] = _history_row("decision", decision["effective_at"], "Board vote carried",
                                 decision["result_link"], decision["result_uuid"])
    # Only independently readable openings reach this loop. A hidden Topic has
    # neither a row nor a chain pointer here, even when its pin exists privately.
    for topic in page["topics"]:
        entries = disc._rows("""
            SELECT t.creation_act FROM public.core_discthread t
              JOIN public.core_discpostact a ON a.act_uuid=t.creation_act
              JOIN public.core_discboard b ON b.board_uuid=a.board_uuid
              JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
             WHERE t.thread_uuid=%s AND b.organism_id=%s AND e.living_organism_id=b.organism_id
        """, [topic["thread_link"], params["owner_id"]])
        if len(entries) == 1:
            event = entries[0]["creation_act"]
            rows[("topic", event)] = _history_row("topic", topic["created_at"], "Topic started: " + topic["title"],
                                                  topic["thread_link"], event)
    choices = disc._rows("""
        SELECT a.act_uuid,a.enabled,a.effective_at FROM public.core_careassistancesettingact a
          JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
           AND e.living_organism_id=a.organism_id AND e.action='SET_CARE_ASSISTANCE'
         WHERE a.organism_id=%s AND a.effective_at<=public.intevia_clock_statement()
           AND a.recorded_at<=public.intevia_clock_statement() ORDER BY a.version
    """, [params["owner_id"]])
    for choice in choices:
        key = choice["act_uuid"]
        rows[("setting_change", key)] = _history_row(
            "setting_change", choice["effective_at"],
            "CARE assistance turned " + ("On" if choice["enabled"] else "Off"), key, key,
            enabled=choice["enabled"], switch_receipt=key)
    return list(rows.values())


def _observation_history(credential, page, params):
    # Historical disclosure uses the stored clause and the viewer's current LAW
    # rights, not today's vacancy or today's governing version. Off gates the
    # entire route before retrieving any observation or its source coordinates.
    if (not page["care"]["enabled"] or not page["tenure"]
            or page["office"].get("current_definition") is None):
        return []
    candidates = disc._rows(f"""
        WITH RECURSIVE family AS (
            SELECT id,role_uuid,supersedes_id,code FROM public.core_organismroledefinition
             WHERE living_organism_id=%(owner_id)s AND role_uuid=%(current)s
               AND scope='LIVING_ORGANISM' AND kind='BOARD_OFFICE'
            UNION
            SELECT p.id,p.role_uuid,p.supersedes_id,p.code FROM public.core_organismroledefinition p
              JOIN family c ON c.supersedes_id=p.id AND c.code=p.code
             WHERE p.living_organism_id=%(owner_id)s AND p.scope='LIVING_ORGANISM' AND p.kind='BOARD_OFFICE'
        )
        SELECT {_PUBLIC},o.binding_uuid,o.library_version_id,o.adoption_uuid,o.resource_reference,o.clause_text
          FROM {_PAIR} JOIN family d ON d.role_uuid=o.definition_uuid
         WHERE o.organism_id=%(owner_id)s
           AND o.recorded_at<=public.intevia_clock_statement()
         ORDER BY o.qualification_at,o.event_uuid
    """, {**params, "current": page["office"]["current_definition"]})
    allowed = {}
    history = []
    for record in candidates:
        cut = record["at"]
        if cut not in allowed:
            try:
                laws = library_laws.our_laws(credential=credential, organism_uuid=params["owner"],
                                              at=cut, known_at=cut)["laws"]
            except library_laws.LawReadUnavailable:
                laws = []
            allowed[cut] = {(law["binding"], law["version"]): law for law in laws}
        law = allowed[cut].get((record["binding_uuid"], record["library_version_id"]))
        if (not law or law["act"] != record["adoption_uuid"]
                or law["library_reference"] != record["resource_reference"]):
            continue
        clause = disc._rows("SELECT content FROM public.l_clauses(%s) WHERE locator=%s",
                            [law["content"], record["source_locator"]])
        if len(clause) != 1 or clause[0]["content"] != record["clause_text"]:
            continue
        history.append(_history_row("care_observation", cut, "CARE", record["source_link"], record["event_ref"],
                                     **{name: record[name] for name in
                                        ("words", "event_ref", "source_version", "source_locator")}))
    return history


def _source(params):
    # Reuse uses the same current disclosure and source predicate as first display.
    return disc._rows(f"""
        SELECT q.may_create,{_PUBLIC}
          FROM public.care_alpha_treasurer_qualify(%(owner)s,%(office)s,%(reader)s,%(epoch)s,
                                                   public.intevia_clock_statement(),false) q
          LEFT JOIN ({_PAIR}) ON (o.organism_id,o.definition_uuid,o.provision_uuid,o.binding_uuid,o.observation_kind)
                    =(q.organism_id,q.definition_uuid,q.provision_uuid,q.binding_uuid,q.observation_kind)
    """, params)


def _record(params, received):
    key = uuid4()
    with connection.cursor() as cur:
        cur.execute(sql.SQL("""
            DO $care_display$
            DECLARE
                q record;
                d public.core_careofficeobservation;
                lo public.core_livingorganism;
                h public.core_livingorganismevent;
                state_at_cut text;
                stamp timestamptz := public.intevia_clock_statement();
            BEGIN
                SELECT eligible.* INTO q FROM (
                    SELECT candidate.*,count(*) OVER () AS source_count
                      FROM public.care_alpha_treasurer_qualify({owner},{office},{reader},{epoch},stamp,true) candidate
                ) eligible WHERE eligible.source_count=1;
                IF NOT FOUND THEN RETURN; END IF;
                INSERT INTO public.core_careofficeobservation
                    (event_uuid,organism_id,definition_uuid,provision_uuid,binding_uuid,library_version_id,
                     library_version_number,adoption_uuid,resource_reference,locator,clause_text,observation_kind,
                     rule_reference,sentence,setting_act_uuid,requester_id,requester_epoch,received_at)
                VALUES ({key},q.organism_id,q.definition_uuid,q.provision_uuid,q.binding_uuid,q.library_version_id,
                        q.library_version_number,q.adoption_uuid,q.resource_reference,q.locator,q.clause_text,
                        q.observation_kind,q.rule_reference,q.sentence,q.setting_act_uuid,{reader},{epoch},{received})
                RETURNING * INTO STRICT d;
                SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=d.organism_id;
                SELECT * INTO STRICT h FROM public.core_livingorganismevent WHERE id=lo.head_event_id;
                state_at_cut:=public.s015_fold_effective_state('core_livingorganism',lo.id,stamp,stamp);
                PERFORM public.s015_append_event('core_livingorganism',lo.id,'RECORD_CARE_OFFICE_OBSERVATION',
                    state_at_cut,stamp,d.occurred_at,d.received_at,d.requester_id,lo.founding_authority_basis_id,
                    public.care_observation_decision(d.rule_reference,d.payload_fingerprint),
                    'care-office-observation:'||d.event_uuid,'care-office-presentation:'||d.event_uuid,
                    d.payload_fingerprint,jsonb_build_object(),NULL,d.event_uuid,h.id,h.sequence::integer,
                    NULL,d.rule_reference,'care-office-presentation:'||d.event_uuid);
            END $care_display$;
        """).format(owner=sql.Literal(params["owner"]), office=sql.Literal(params["office"]),
                    reader=sql.Literal(params["reader"]), epoch=sql.Literal(params["epoch"]),
                    key=sql.Literal(key), received=sql.Literal(received)))
    # The only possible new result is the pair established at the write cut above.
    return disc._rows(f"SELECT {_PUBLIC} FROM {_PAIR} WHERE o.event_uuid=%s AND o.organism_id=%s",
                      [key, params["owner_id"]])


def _attach_observation(page, params, received, present):
    candidates = _source(params)
    if len(candidates) != 1:
        return
    record = candidates[0]
    if record["event_ref"] is None:
        if not present or not record["may_create"]:
            return
        created = _record(params, received)
        if not created:
            return
        record = created[0]
    public = {name: record[name] for name in ("words", "event_ref", "source_version", "source_locator", "source_link")}
    public["label"] = "CARE"
    if present:
        page["care"]["observation"] = public


class _PresentationFailed(Exception):
    """No response was delivered. The owning transaction has exited."""


def _retryable(error):
    native = getattr(error, "__cause__", None)
    return (getattr(native, "sqlstate", None) in ("40001", "40P01") or
            getattr(getattr(native, "diag", None), "constraint_name", None) == "care_observation_source_key")


def _attempt(credential, owner, office, present, publish, *, facts_only=False):
    optional = False
    publication_started = False

    def deliver():
        nonlocal publication_started
        publication_started = True
        publish(page)

    try:
        with transaction.atomic(durable=True):
            actor = care._actor(credential)
            received = disc._rows("SELECT public.intevia_clock_statement() AS receipt")[0]["receipt"]
            owner_id = care._lock_scope(owner, actor)
            access = care._office_access_locked(credential, owner, office, actor, owner_id)
            setting = care._setting_projection(access, actor, owner)
            params = dict(owner=owner, owner_id=owner_id, office=office, reader=actor.pk, epoch=actor.access_epoch)
            page = _office_facts(params, access, setting)
            current = page["office"].get("current_definition")
            page["provisions"], policies = _provisions(credential, owner, current or office)
            page["decisions"] = _decisions(credential, owner, policies)
            topics = access.get("visible_topics")
            if topics is None:
                topics = discussion_posts.topics_about_office(credential=credential, organism_uuid=owner,
                                                              office_definition_uuid=office)
            page["topics"] = _topics(topics)
            page["history"] = _human_history(page, params)
            if not facts_only:
                with transaction.atomic():
                    if setting["enabled"] and current is not None:
                        optional = True
                        _attach_observation(page, {**params, "office": current}, received, present)
                    page["history"].extend(_observation_history(credential, page, params))
            page["history"].sort(key=lambda row: (row["at"], str(row["ilc_link"]), row["kind"]), reverse=True)
            transaction.on_commit(deliver)
    except DatabaseError as exc:
        if facts_only or publication_started or (not optional and not _retryable(exc)):
            raise
        raise _PresentationFailed() from exc
    if not publication_started:
        if optional:
            raise _PresentationFailed()
        raise RuntimeError("The office facts could not be committed for delivery.")


def publish_office_record(*, credential, organism_uuid, office_definition_uuid, publish, present_care=False):
    """Deliver a permitted payload to the response consumer after the owning transaction commits."""
    if (not isinstance(organism_uuid, UUID) or not isinstance(office_definition_uuid, UUID)
            or type(present_care) is not bool):
        raise care.SettingRefused("CARE_SETTING_UNAVAILABLE")
    args = (credential, organism_uuid, office_definition_uuid, present_care, publish)
    for attempt in range(2):
        try:
            _attempt(*args)
            return
        except _PresentationFailed as exc:
            if attempt or not _retryable(exc.__cause__):
                break
    _attempt(*args, facts_only=True)


def office_record(*, credential, organism_uuid, office_definition_uuid, present_care=False):
    """Synchronous office read. Its return value comes from the post-commit publisher."""
    delivered = []
    publish_office_record(credential=credential, organism_uuid=organism_uuid, office_definition_uuid=office_definition_uuid,
                          present_care=present_care, publish=delivered.append)
    return delivered[0]
