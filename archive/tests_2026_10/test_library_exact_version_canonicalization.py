from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from unittest import TestCase

from src.intevia.services.library_exact_version_contract import (
    DOMAIN_SEPARATOR,
    DeterminationPayload,
    canonical_payload_bytes,
    envelope_for,
)


AUTHORITY_JSON = r'''{"action":"CREATE","actor_access_epoch":"7","actor_identity_id":"11111111-2222-4333-8444-555555555555","authority_binding_reference":"lib-authority-binding:binding.alpha:v1","basis_code":"AUTHORITY_EXPLICIT_BINDING_QUALIFIED","binding_kind":"ACTION","binding_reference":"lib-authority-binding:binding.alpha:v1","binding_version":"1","canonicalization":"RFC8785+INTEVIA-S011A-v1","consumer_reference":"consumer.event-s011b","determination_kind":"AUTHORITY","environment":"internal-pre-alpha","evaluated_at":"2026-07-23T18:30:00.000000Z","policy_reference":"policy:LIB-EXACT-VERSION-PREALPHA-001:v1","provider_snapshot_reference":"lib-binding-snapshot:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","request_reference":"request.alpha","requested_at":"2026-07-23T18:29:59.123456Z","resource_id":"lib.resource~alpha","resource_version_pk":"9007199254740993","result":"QUALIFIED","revalidation_boundary":"CONSEQUENTIAL_ACTION_SAME_TRANSACTION","schema_id":"intevia.s011a.library-determination","schema_version":1,"source_state":"PUBLISHED","unresolved_limitation_code":null,"version_number":"42","viewer_access_epoch":null,"viewer_identity_id":null}'''
LINKABILITY_JSON = r'''{"action":null,"actor_access_epoch":null,"actor_identity_id":null,"authority_binding_reference":null,"basis_code":"STATE_DEPRECATED_NOT_LINKABLE","binding_kind":null,"binding_reference":null,"binding_version":null,"canonicalization":"RFC8785+INTEVIA-S011A-v1","consumer_reference":null,"determination_kind":"LINKABILITY","environment":"internal-pre-alpha","evaluated_at":"2026-07-23T18:30:00.000000Z","policy_reference":"policy:LIB-EXACT-VERSION-PREALPHA-001:v1","provider_snapshot_reference":null,"request_reference":null,"requested_at":null,"resource_id":"lib.resource~alpha","resource_version_pk":"9007199254740993","result":"NOT_LINKABLE","revalidation_boundary":"CURRENT_EVALUATION_ONLY","schema_id":"intevia.s011a.library-determination","schema_version":1,"source_state":"DEPRECATED","unresolved_limitation_code":null,"version_number":"42","viewer_access_epoch":null,"viewer_identity_id":null}'''
DISCLOSURE_JSON = r'''{"action":null,"actor_access_epoch":null,"actor_identity_id":null,"authority_binding_reference":null,"basis_code":"VIEWER_BOUND_DEPRECATED_CONTENT_VISIBLE","binding_kind":"VIEWER","binding_reference":"lib-authority-binding:viewer.alpha:v1","binding_version":"3","canonicalization":"RFC8785+INTEVIA-S011A-v1","consumer_reference":null,"determination_kind":"DISCLOSURE","environment":"internal-pre-alpha","evaluated_at":"2026-07-23T18:30:00.000000Z","policy_reference":"policy:LIB-EXACT-VERSION-PREALPHA-001:v1","provider_snapshot_reference":"lib-binding-snapshot:sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","request_reference":null,"requested_at":null,"resource_id":"lib.resource~alpha","resource_version_pk":"9007199254740993","result":"CONTENT_VISIBLE","revalidation_boundary":"READ_TIME_ONLY","schema_id":"intevia.s011a.library-determination","schema_version":1,"source_state":"DEPRECATED","unresolved_limitation_code":null,"version_number":"42","viewer_access_epoch":"12","viewer_identity_id":"66666666-7777-4888-8999-aaaaaaaaaaaa"}'''

VECTORS = (
    (AUTHORITY_JSON, 1170, "a0902fd3a3be83dad2d4c1aa471938a01d56d2c971f05950d190d88335a292e1"),
    (LINKABILITY_JSON, 899, "6c14970e7da1b4838c27a7275ca738dda072dfc03f007c2c8770f4d455e0f512"),
    (DISCLOSURE_JSON, 1064, "217009782057f8a6eb1bc393a3bda0aa952a20419443fd3e48ac1470cc510e30"),
)


class CanonicalizationTests(TestCase):
    @staticmethod
    def payload(text):
        return DeterminationPayload(**json.loads(text))

    def test_normative_python_vectors(self):
        for text, length, digest in VECTORS:
            with self.subTest(digest=digest):
                payload = self.payload(text)
                canonical = canonical_payload_bytes(payload)
                self.assertEqual(canonical, text.encode("utf-8"))
                self.assertEqual(len(canonical), length)
                self.assertEqual(sha256(DOMAIN_SEPARATOR + canonical).hexdigest(), digest)
                self.assertEqual(envelope_for(payload).determination_reference, f"lib-determination:sha256:{digest}")

    def test_wide_integer_negative_families_rejected(self):
        payload = self.payload(AUTHORITY_JSON)
        for value in ("", "00", "01", "-1", "+1", "1.0", "1e3", " 1", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_payload_bytes(replace(payload, binding_version=value))

    def test_timestamp_nfc_and_float_rejected(self):
        payload = self.payload(AUTHORITY_JSON)
        invalid = (
            replace(payload, evaluated_at="2026-07-23T18:30:00Z"),
            replace(payload, evaluated_at="2026-07-23T18:30:00.00000Z"),
            replace(payload, resource_id="e\u0301"),
            replace(payload, schema_version=1.0),
        )
        for candidate in invalid:
            with self.assertRaises(ValueError):
                canonical_payload_bytes(candidate)

    def test_identity_epoch_and_domain_changes_change_digest(self):
        payload = self.payload(DISCLOSURE_JSON)
        original = envelope_for(payload)
        self.assertNotEqual(original, envelope_for(replace(payload, viewer_access_epoch="13")))
        self.assertNotEqual(original, envelope_for(replace(payload, viewer_identity_id="11111111-2222-4333-8444-555555555555")))
        self.assertNotEqual(
            sha256(DOMAIN_SEPARATOR + original.canonical_payload).digest(),
            sha256(b"different-domain\n" + original.canonical_payload).digest(),
        )

    def test_independent_node_witness(self):
        script = """
const crypto=require('crypto');
const vectors=JSON.parse(process.argv[1]);
for(const text of vectors){
 const obj=JSON.parse(text);
 const canonical=JSON.stringify(obj,Object.keys(obj).sort());
 const digest=crypto.createHash('sha256').update('INTEVIA:S011A:LIB-DETERMINATION:v1\\n').update(canonical).digest('hex');
 process.stdout.write(Buffer.byteLength(canonical)+':'+digest+'\\n');
}
"""
        node = os.environ["S011A_NODE_EXECUTABLE"]
        self.assertTrue(Path(node).is_absolute())
        completed = subprocess.run(
            [node, "-e", script, json.dumps([item[0] for item in VECTORS])],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.stdout.splitlines(),
            [f"{length}:{digest}" for _, length, digest in VECTORS],
        )

    def test_independent_node_rejects_malformed_families(self):
        script = r"""
const payload=JSON.parse(process.argv[1]);
const expected=JSON.parse(process.argv[2]);
const decimal=/^(0|[1-9][0-9]*)$/;
const timestamp=/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
function valid(value){
 if(Object.keys(value).sort().join('|')!==expected.join('|')) return false;
 for(const key of ['actor_access_epoch','binding_version','resource_version_pk','version_number','viewer_access_epoch']){
  if(value[key]!==null&&(typeof value[key]!=='string'||!decimal.test(value[key]))) return false;
 }
 if(!timestamp.test(value.evaluated_at)) return false;
 if(value.requested_at!==null&&!timestamp.test(value.requested_at)) return false;
 if(value.actor_identity_id!==null&&!uuid.test(value.actor_identity_id)) return false;
 if(value.viewer_identity_id!==null&&!uuid.test(value.viewer_identity_id)) return false;
 return Object.values(value).every(item=>typeof item!=='number'||Number.isSafeInteger(item));
}
const malformed=[];
malformed.push({...payload,unknown_field:null});
const missing={...payload};delete missing.action;malformed.push(missing);
for(const value of ['', '00', '01', '-1', '+1', '1.0', '1e3', ' 1']) malformed.push({...payload,binding_version:value});
malformed.push({...payload,binding_version:1});
malformed.push({...payload,evaluated_at:'2026-07-23T18:30:00Z'});
malformed.push({...payload,actor_identity_id:'11111111-2222-4333-8444-AAAAAAAAAAAA'});
if(malformed.some(valid)) process.exit(2);
process.stdout.write('REJECTED='+malformed.length+'\n');
"""
        payload = json.loads(AUTHORITY_JSON)
        node = os.environ["S011A_NODE_EXECUTABLE"]
        completed = subprocess.run(
            [node, "-e", script, json.dumps(payload), json.dumps(sorted(payload))],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.stdout, "REJECTED=13\n")