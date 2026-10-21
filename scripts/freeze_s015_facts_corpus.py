"""Freeze the U-FACTS-1 equivalence corpus from the pre-change parser.

Run once, on the tree whose src/intevia/services/organism_authority.py is
the 8006f8c parser (its SHA-256 is pinned below), before the table-driven
parser lands:

    python scripts/freeze_s015_facts_corpus.py \
        --cases tests/fixtures/s015_authority_facts_cases_v1.json \
        --out tests/fixtures/s015_authority_facts_corpus_v1.json \
        --authorised-by <SHA-256 of HG_DECISIONS_U_FACTS_1_DESIGN_20261021_CAPTURE.txt>

DJANGO_SETTINGS_MODULE must be set as for the test suite; the script calls
django.setup() and uses no database (the parser reads none).

For every case the script records expected_before: the canonical JSON of
the facts the pre-change parser emits (or, for a negative case, the base
outcome as data: "ACCEPTED:<facts>" or "MALFORMED:<message>"). It then
derives expected_after without running any parser: for a case tagged with
discontinuity F2 it relabels the one core_identitytransition fact to
learner-registration; for F3 it removes the one core_application fact
whose reference is the refusal's p text; for every other accepted case
expected_after equals expected_before; for a negative case expected_after
is "MALFORMED". The output pins the baseline commit, the parser source
digest, this script's own digest, the input case list's digest and the
authorising capture's digest. It refuses to run against a parser whose
source digest is not the pinned one, and refuses any authorisation digest
other than the pinned capture digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

BASELINE_COMMIT = "8006f8c267d3c613b4db0ed736cb697a7aac3969"
BASELINE_PARSER_SOURCE_SHA256 = "6b6dd263ec7d597852ddc48d0d8a01265f00e0c0ea136c0e250f4b0093ba348a"
AUTHORISING_CAPTURE = "HG_DECISIONS_U_FACTS_1_DESIGN_20261021_CAPTURE.txt"
AUTHORISING_CAPTURE_SHA256 = "069d75b63caa1c045c691eaf26e2eb908a5df9c9e564b4ea36000c073c9e2775"
SCHEMA = "S015_AUTHORITY_FACTS_V1"
PARSER_SOURCE = Path("src/intevia/services/organism_authority.py")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(value):
    if isinstance(value, dict):
        if set(value) == {"$uuid"}:
            return UUID(value["$uuid"])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _build_request(spec, authority):
    kind = spec["type"]
    fields = {key: _decode(value) for key, value in spec.items() if key != "type"}
    for name in ("evaluated_at", "effective_at"):
        if isinstance(fields.get(name), str):
            fields[name] = datetime.fromisoformat(fields[name])
    from core.organism_contract import OrganismAction

    fields["action"] = OrganismAction(fields["action"])
    fields.setdefault("database_alias", "default")
    fields.setdefault("actor_pk", 1)
    fields.setdefault("actor_access_epoch", 0)
    fields.setdefault("request_reference", "request:fixture")
    fields.setdefault("idempotency_key", "fixture")
    fields.setdefault("evaluated_at", datetime.fromisoformat("2026-10-12T00:00:00+00:00"))
    if "target_fingerprint" not in fields:
        fields["target_fingerprint"] = authority.authority_target_fingerprint(fields.get("target") or {})
    if kind == "pre":
        return authority.S015PreFoundingAuthorityRequest(**fields)
    if kind == "own":
        fields.setdefault("actor_credential_pk", 1)
        fields.setdefault("living_organism_pk", 1)
        fields.setdefault("application_pk", None)
        return authority.S015OwnApplicantAuthorityRequest(**fields)
    fields.setdefault("active_membership_head_reference", "membership-head:fixture")
    fields.setdefault("contextual_role_head_references", ())
    return authority.S015PostFoundingAuthorityRequest(**fields)


class _Principal:
    def __init__(self, principal_uuid):
        self.principal_uuid = principal_uuid


def _derive_after(case, before_facts):
    change = case.get("discontinuity")
    if change is None:
        return before_facts
    if change == "F2":
        matches = [index for index, fact in enumerate(before_facts) if fact["relation"] == "core_identitytransition"]
        if len(matches) != 1:
            raise SystemExit(f"{case['id']}: F2 expects exactly one core_identitytransition fact, found {len(matches)}")
        index = matches[0]
        fact = before_facts[index]
        reference = fact["reference"]
        if not reference.startswith("identity-transition:"):
            raise SystemExit(f"{case['id']}: F2 fact reference lacks its prefix")
        after = list(before_facts)
        after[index] = {
            "relation": "learner-registration",
            "reference": "learner-registration:" + reference[len("identity-transition:"):],
            "eligible_at": fact["eligible_at"],
        }
        return after
    if change == "F3":
        content = json.loads(case["reference"][len("s015a1:"):])
        matches = [index for index, fact in enumerate(before_facts) if fact["relation"] == "core_application" and fact["reference"] == content["p"]]
        if len(matches) != 1:
            raise SystemExit(f"{case['id']}: F3 expects exactly one core_application fact for p, found {len(matches)}")
        after = list(before_facts)
        del after[matches[0]]
        return after
    raise SystemExit(f"{case['id']}: unknown discontinuity {change!r}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--authorised-by", required=True)
    args = parser.parse_args(argv)
    if args.authorised_by != AUTHORISING_CAPTURE_SHA256:
        print(f"refused: --authorised-by must be the SHA-256 of {AUTHORISING_CAPTURE}", file=sys.stderr)
        return 2
    if args.out.exists():
        print(f"refused: {args.out} exists; the corpus is written once", file=sys.stderr)
        return 2
    if not PARSER_SOURCE.exists():
        print(f"refused: {PARSER_SOURCE} not found; run from the repository root", file=sys.stderr)
        return 2
    parser_digest = _sha256(PARSER_SOURCE.read_bytes())
    if parser_digest != BASELINE_PARSER_SOURCE_SHA256:
        print(f"refused: {PARSER_SOURCE} is {parser_digest}, not the baseline parser {BASELINE_PARSER_SOURCE_SHA256}", file=sys.stderr)
        return 2
    if not os.environ.get("DJANGO_SETTINGS_MODULE"):
        print("refused: DJANGO_SETTINGS_MODULE is not set", file=sys.stderr)
        return 2
    import django

    django.setup()
    from src.intevia.services import organism_authority as authority

    cases_bytes = args.cases.read_bytes()
    cases_document = json.loads(cases_bytes.decode("utf-8"))
    if cases_document.get("schema") != SCHEMA or cases_document.get("baseline_commit") != BASELINE_COMMIT:
        print("refused: the case list is not for this schema and baseline", file=sys.stderr)
        return 2
    cases = cases_document["cases"]
    if len(cases) != len({case["id"] for case in cases}):
        print("refused: duplicate case ids", file=sys.stderr)
        return 2
    canonical = authority.canonical_json_bytes
    frozen = []
    for case in cases:
        request = _build_request(case["request"], authority)
        principal = _Principal(UUID(case["principal"])) if case.get("principal") else None
        try:
            facts = authority._facts_read(request, case["reference"], principal)
            outcome = ("ACCEPTED", facts)
        except authority.S015AuthorityMalformed as exc:
            outcome = ("MALFORMED", str(exc))
        record = dict(case)
        if case["group"] == "NEG":
            record["expected_before"] = outcome[0] + ":" + (canonical(outcome[1]).decode("utf-8") if outcome[0] == "ACCEPTED" else outcome[1])
            record["expected_after"] = "MALFORMED"
            record["discriminating"] = outcome[0] == "ACCEPTED" or (
                case.get("message") is not None and outcome[1] != case.get("message")
            )
        else:
            if outcome[0] != "ACCEPTED":
                print(f"refused: {case['id']} is {outcome[0]} on the baseline parser: {outcome[1]}", file=sys.stderr)
                return 2
            before = json.loads(canonical(outcome[1]).decode("utf-8"))
            after = _derive_after(case, before)
            record["expected_before"] = canonical(before).decode("utf-8")
            record["expected_after"] = canonical(after).decode("utf-8")
        frozen.append(record)
    cases_digest = _sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    document = {
        "schema": SCHEMA,
        "baseline_commit": BASELINE_COMMIT,
        "baseline_parser_source_sha256": parser_digest,
        "generator": "scripts/freeze_s015_facts_corpus.py",
        "generator_sha256": _sha256(Path(__file__).read_bytes()),
        "cases_input_sha256": _sha256(cases_bytes),
        "authorised_by": args.authorised_by,
        "authorising_capture": AUTHORISING_CAPTURE,
        "cases_sha256": cases_digest,
        "cases": frozen,
    }
    args.out.write_bytes((json.dumps(document, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"wrote {args.out}: {len(frozen)} cases; cases_sha256 {cases_digest}; file sha256 {_sha256(args.out.read_bytes())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
