"""S015 migration 0024 - the L1 (s015r1) and parts (s015p1) commitment preimages as compact canonical text.

Requirement: design v0.6 section 5.4 and section 6 binding contract row 2; Human rulings L1-a and L1-d (16 Sep 2026);
the byte contract vectors/S015_COMMITMENT_FORMS_s015r1_s015p1_RULE_v0_1.md.

Expected bytes are never read from the database. They come from the published vectors (derived by an independent
serialiser) or from the serialiser in this module, which applies the published rule to values this test supplies or
reads as plain column values. Commitments assigned or verified by the database are compared with them, never used
to derive them.

Node identity: tests/test_s015_0024_commitment_preimages.py::<Class>::<test>.
"""
import datetime as dt
import hashlib
import json
import pathlib
import uuid
from unittest import skipUnless

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TransactionTestCase

POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "S015 commitment preimages are PostgreSQL functions")
ROOT = pathlib.Path(__file__).resolve().parents[1]
T0 = dt.datetime(2026, 9, 5, 10, 0, 0, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------- the published rule, applied independently of SQL
def json_string(value):
    return json.dumps(value, ensure_ascii=False)


def canonical_ts(value):
    moment = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(value)
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_value(pg_type, value):
    if value is None:
        return "null"
    if pg_type == "timestamptz":
        return json_string(canonical_ts(value))
    if pg_type == "uuid":
        return json_string(str(uuid.UUID(str(value))))
    if pg_type in ("varchar", "text"):
        return json_string(value)
    if pg_type in ("bigint", "integer", "smallint"):
        return str(int(value))
    raise AssertionError("no rule for type " + pg_type)


def l1_preimage(table, inventory, row):
    values = dict(row)
    values["l1_commitment"] = None
    record = "{" + ",".join(json_string(c["column"]) + ":" + canonical_value(c["pg_type"], values[c["column"]]) for c in inventory) + "}"
    return '["s015r1",' + json_string(table) + "," + record + "]"


def parts_preimage(table, event_uuid, parts):
    body = ",".join("[" + canonical_value("integer", p["part_ordinal"]) + "," + canonical_value("varchar", p["part_class"]) + ","
                    + canonical_value("varchar", p["posture"]) + "," + canonical_value("varchar", p["ground"]) + ","
                    + canonical_value("varchar", p["office"]) + "]" for p in sorted(parts, key=lambda p: p["part_ordinal"]))
    return '["s015p1",' + json_string(table) + "," + canonical_value("uuid", event_uuid) + ",[" + body + "]]"


def digest(tag, preimage):
    return tag + ":" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def corpus(name):
    return json.loads((ROOT / "vectors" / name).read_text(encoding="utf-8"))


@POSTGRESQL_ONLY
class CompactCommitmentVectors(TransactionTestCase):
    """The installed functions reproduce every published s015r1 and s015p1 vector byte for byte."""

    def test_l1_preimage_reproduces_every_published_vector(self):
        vectors = corpus("s015r1_s015p1_fixed_vectors.json")["l1_vectors"]
        self.assertEqual(len(vectors), 9)
        with connection.cursor() as cur:
            for v in vectors:
                with self.subTest(vector=v["id"]):
                    cur.execute("SELECT public.s015_0022_l1_preimage(%s::regclass, %s::jsonb)", (f"public.{v['event_table']}", json.dumps(v["row"], ensure_ascii=False)))
                    preimage = cur.fetchone()[0]
                    self.assertEqual(preimage, v["preimage"])
                    cur.execute("SELECT public.s015_0022_digest('s015r1', %s::text)", (preimage,))
                    self.assertEqual(cur.fetchone()[0], v["digest"])

    def test_parts_preimage_reproduces_every_published_vector(self):
        vectors = corpus("s015r1_s015p1_fixed_vectors.json")["parts_vectors"]
        self.assertEqual(len(vectors), 4)
        for v in vectors:
            with self.subTest(vector=v["id"]):
                observed = {}

                class _Rollback(Exception):
                    pass

                try:
                    with transaction.atomic(), connection.cursor() as cur:
                        # inserted parts are read by the function and then rolled back; no commit, no deferred guardian
                        for p in v["parts"]:
                            cur.execute("INSERT INTO public.core_governedeventpart (event_table, event_uuid, part_ordinal, part_class, posture, ground, office) "
                                        "VALUES (%s, %s, %s, %s, %s, %s, %s)", (v["event_table"], v["event_uuid"], p["part_ordinal"], p["part_class"], p["posture"], p["ground"], p["office"]))
                        cur.execute("SELECT public.s015_0022_parts_preimage(%s::text, %s::uuid)", (v["event_table"], v["event_uuid"]))
                        observed["preimage"] = cur.fetchone()[0]
                        cur.execute("SELECT public.s015_0022_digest('s015p1', %s::text)", (observed["preimage"],))
                        observed["digest"] = cur.fetchone()[0]
                        raise _Rollback()
                except _Rollback:
                    pass
                self.assertEqual(observed["preimage"], v["preimage"])
                self.assertEqual(observed["digest"], v["digest"])

    def test_both_preimages_refuse_a_table_that_is_not_an_event_table(self):
        for sql, args in (("SELECT public.s015_0022_l1_preimage('public.core_livingorganism'::regclass, '{}'::jsonb)", ()),
                          ("SELECT public.s015_0022_parts_preimage('core_livingorganism', %s::uuid)", (str(uuid.uuid4()),))):
            with self.subTest(sql=sql.split("(")[0]):
                try:
                    with transaction.atomic(), connection.cursor() as cur:
                        cur.execute(sql, args)
                        cur.fetchone()
                except DatabaseError as exc:
                    self.assertIn("is not an S015 event table", str(exc))
                else:
                    self.fail("the preimage function accepted a table that is not an S015 event table")


@POSTGRESQL_ONLY
class LawfulRecordWithParts(TransactionTestCase):
    """End to end: a lawful founding whose first organism event carries two parts. The expected parts fingerprint and
    then the expected L1 fingerprint are derived here, independently, and compared with the values the database
    verified and assigned. A second event declares its predecessor link from the independently derived L1 value."""

    PARTS = [
        {"part_ordinal": 1, "part_class": "decision", "posture": "OPEN", "ground": "Recorded at the founding meeting", "office": None},
        {"part_ordinal": 2, "part_class": "subject", "posture": "HELD_CLOSED", "ground": "Personal data of the founder", "office": "PRIVACY"},
    ]

    def _hx(self, seed):
        return hashlib.sha256(seed.encode()).hexdigest()

    def _event(self, cur, table, fk, anchor, sequence, action, prior, result, actor, basis, tag, *, parts_commitment=None,
               predecessor=None, predecessor_l1=None, at=T0, extra=None):
        event_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"urn:ufund2:0024:{table}:{sequence}:{tag}"))
        if parts_commitment is None:   # no parts: the empty-list commitment, derived here
            parts_commitment = digest("s015p1", parts_preimage(table, event_uuid, []))
        cols = {
            "event_uuid": event_uuid, fk: anchor, "sequence": sequence, "predecessor_id": predecessor,
            "predecessor_sequence": sequence - 1 if sequence > 1 else None, "action": action, "prior_state": prior,
            ("result" if table == "core_coverageassessment" else "resulting_state"): result,
            "actor_id": actor, "actor_access_epoch": 0, "authority_basis_id": basis,
            "authority_decision_reference": "s015d1:" + self._hx(f"decision:{table}:{sequence}:{tag}"),
            "evidence_reference": f"ufund2:0024:evidence:{tag}", "request_reference": f"ufund2:0024:request:{tag}",
            "idempotency_key": f"ufund2:0024:{table}:{sequence}:{tag}", "payload_fingerprint": self._hx(f"payload:{table}:{sequence}:{tag}"),
            "lineage_reference": "s015l1:" + self._hx(f"lineage:{table}:{sequence}:{tag}"),
            "temporal_basis_kind": None, "temporal_basis_reference": None,
            "occurred_at": at, "effective_at": at, "received_at": at, "recorded_at": None,
            "actor_state": "PARTY", "actor_capacity": "STANDING", "enterer_identity_id": actor, "entry_mode": "TRANSPORT",
            "composing_rule_state": "NA_TRANSPORT", "composing_rule_reference": None, "composer_state": "NA_TRANSPORT",
            "composer_identity_id": None, "effective_until": None, "effective_until_state": "NONE",
            "parts_commitment": parts_commitment, "predecessor_l1_commitment": predecessor_l1, "l2_commitment": None,
        }
        cols.update(extra or {})
        cur.execute(f"INSERT INTO public.{table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                    list(cols.values()))
        return cur.fetchone()[0], event_uuid

    def _empty_fp(self, cur, token, identity):
        cur.execute("SELECT public.s015_digest_envelope(public.s015_canonical_envelope(%s, %s::uuid, '{}'::text[]))", (token, identity))
        return cur.fetchone()[0]

    def _commit_or_fail(self, what, fn):
        try:
            with transaction.atomic(), connection.cursor() as cur:
                return fn(cur)
        except IntegrityError as exc:
            self.fail(f"lawful {what} refused at commit: {str(exc).strip().splitlines()[0]}")

    def test_parts_and_l1_fingerprints_equal_independent_derivation(self):
        tag = "e2e"
        inventory = corpus("s015f4_fixed_vectors.json")["inventories"]["core_livingorganismevent"]["canonical_columns"]
        state = {}

        def found(cur):
            cur.execute("INSERT INTO auth_user (password,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined) "
                        "VALUES ('!unusable', false, 'ufund2_0024', '', '', '', false, true, now()) RETURNING id")
            user = cur.fetchone()[0]
            cur.execute("INSERT INTO core_identity (display_name, created_at, credential_id, identity_id, canonical_username, access_state, access_epoch, updated_at) "
                        "VALUES ('UFUND2 0024', now(), %s, %s, 'ufund2_0024', 'active', 0, now()) RETURNING id", (user, str(uuid.uuid4())))
            actor = cur.fetchone()[0]
            cur.execute("INSERT INTO core_authorityprincipal (principal_uuid, canonical_governed_source_id, governed_source_namespace, display_label, recorded_at) "
                        "VALUES (%s, 'urn:intevia:governed-source:ufund2:0024', 'UFUND2_THROWAWAY', 'UFUND2 0024', now()) RETURNING id", (str(uuid.uuid4()),))
            principal = cur.fetchone()[0]
            cur.execute("INSERT INTO core_authoritybasis (basis_uuid, authority_class, scope_fingerprint, instrument_reference, evidence_reference, issued_at, effective_at, received_at, recorded_at, issuer_id, authority_principal_id, derivation_root_principal_id) "
                        "VALUES (%s, 'UFUND2_THROWAWAY', %s, 'ufund2:instrument', 'ufund2:evidence', now(), now(), now(), now(), %s, %s, %s) RETURNING id",
                        (str(uuid.uuid4()), self._hx("scope"), actor, principal, principal))
            basis = cur.fetchone()[0]
            lo_uuid = str(uuid.uuid4())
            cur.execute("INSERT INTO core_livingorganism (organism_id, slug, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, constitutional_spine_reference, platform_root) "
                        "VALUES (%s, 'ufund2-0024', 'FOUNDING_PENDING', now(), %s, %s, %s, 'ufund2:spine', false) RETURNING id", (lo_uuid, T0, T0, self._empty_fp(cur, "core_livingorganism", lo_uuid)))
            lo = cur.fetchone()[0]
            event_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"urn:ufund2:0024:core_livingorganismevent:1:{tag}"))
            expected_parts = digest("s015p1", parts_preimage("core_livingorganismevent", event_uuid, self.PARTS))
            state["expected_parts"] = expected_parts
            lo_event, _ = self._event(cur, "core_livingorganismevent", "living_organism_id", lo, 1, "FOUND", None, "ACTIVE", actor, basis, tag, parts_commitment=expected_parts)
            for p in self.PARTS:
                cur.execute("INSERT INTO public.core_governedeventpart (event_table, event_uuid, part_ordinal, part_class, posture, ground, office) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            ("core_livingorganismevent", event_uuid, p["part_ordinal"], p["part_class"], p["posture"], p["ground"], p["office"]))
            m_uuid = str(uuid.uuid4())
            cur.execute("INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) "
                        "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id", (m_uuid, T0, T0, self._empty_fp(cur, "core_organismmembership", m_uuid), actor, lo))
            membership = cur.fetchone()[0]
            self._event(cur, "core_organismmembershiptransition", "membership_id", membership, 1, "JOIN", None, "ACTIVE", actor, basis, tag, extra={"reason_class": "FOUNDING"})
            cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) VALUES (%s, 'COORDINATOR', 'LIVING_ORGANISM', 1, true, now(), %s) RETURNING id", (str(uuid.uuid4()), lo))
            role = cur.fetchone()[0]
            a_uuid = str(uuid.uuid4())
            cur.execute("INSERT INTO core_contextualroleassignment (assignment_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, membership_id, role_definition_id) "
                        "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id", (a_uuid, T0, T0, self._empty_fp(cur, "core_contextualroleassignment", a_uuid), membership, role))
            assignment = cur.fetchone()[0]
            self._event(cur, "core_contextualroleassignmentstateevent", "assignment_id", assignment, 1, "ASSIGN", None, "ACTIVE", actor, basis, tag)
            cur.execute("INSERT INTO core_essentialcoveragerequirement (requirement_uuid, roster_version, responsibility_domain_code, qualifying_role_code, minimum_active_occupants, scope, effective_from, effective_until, living_organism_id) "
                        "VALUES (%s, 1, 'GOVERNANCE', 'COORDINATOR', 1, 'LIVING_ORGANISM', %s, NULL, %s)", (str(uuid.uuid4()), T0 - dt.timedelta(days=1), lo))
            cur.execute("INSERT INTO core_essentialcoveragecase (case_uuid, living_organism_id) VALUES (%s, %s) RETURNING id", (str(uuid.uuid4()), lo))
            case = cur.fetchone()[0]
            self._event(cur, "core_coverageassessment", "case_id", case, 1, "ASSESS", None, "SATISFIED", actor, basis, tag,
                        extra={"roster_version": 1, "evaluated_state_at": T0, "evaluated_known_at": T0, "determiner_capacity_reference": "ufund2:capacity"})
            for token, anchor in (("core_livingorganism", lo), ("core_organismmembership", membership), ("core_contextualroleassignment", assignment)):
                cur.execute("SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())", (token, anchor, T0))
            cur.execute("UPDATE core_livingorganism SET founding_authority_basis_id = %s, founder_identity_id = %s WHERE id = %s", (basis, actor, lo))
            state.update(lo=lo, lo_event=lo_event, event_uuid=event_uuid, actor=actor, basis=basis)

        self._commit_or_fail("founding with parts", found)

        with connection.cursor() as cur:
            cur.execute("SELECT to_jsonb(t)::text FROM public.core_livingorganismevent t WHERE t.id = %s", (state["lo_event"],))
            row = json.loads(cur.fetchone()[0])
        # 1. parts fingerprint: the value the database verified at commit equals the independent derivation
        self.assertEqual(row["parts_commitment"], state["expected_parts"])
        # 2. L1 fingerprint: derive from the stored plain column values with the published rule, then compare
        expected_l1 = digest("s015r1", l1_preimage("core_livingorganismevent", inventory, row))
        self.assertEqual([c["column"] for c in inventory], sorted(row, key=lambda s: s.encode("utf-8")))
        self.assertEqual(row["l1_commitment"], expected_l1)

        # 3. predecessor link: a second event declares the independently derived L1 value; the database verifies it at commit
        def second(cur):
            self._event(cur, "core_livingorganismevent", "living_organism_id", state["lo"], 2, "RESTRICT", "ACTIVE", "DORMANT",
                        state["actor"], state["basis"], tag, predecessor=state["lo_event"], predecessor_l1=expected_l1,
                        at=T0 + dt.timedelta(hours=1))

        self._commit_or_fail("second event declaring the derived predecessor L1", second)
