"""TT-1 template (clone) mode for the S015 verification route's test runners - one implementation for every runner.

Extracted from IsolationRunner.setup_databases by CI-CLONE-1 (Human Governor, 31 Oct 2026: clone mode wherever a
database is created) so that MutationRunner shares it rather than carrying a second implementation. This module
imports neither verification.route nor verification.isolation.runner at load time: route imports mutations, which
imports this mixin, and runner imports run_plan, which imports route.

CI-CLONE-1 v1.1 (Advisor F2, F3, F4): the CI route's build policy for its children, the final-inspection function the
route's TPL-FINAL child runs, and finally-safe setup timing.
"""
import os
import re
import sys
import time
from pathlib import Path

from verification.ownership import install

from .template import (
    TemplateIdentityUnavailable,
    TemplateNameCollision,
    TemplateReceiptInvalid,
    TemplateSealingRefused,
    _build_template,
    _database_record,
    collect_template_identity,
    complete_identity,
    install_clone_creation,
    prepare_template,
    read_template_receipt,
    recover_template_builds,
    runtime_input_lines,
    source_input_lines,
    template_database_name,
    template_receipt_path,
    verify_registered_template,
)

# The CI route's build policy for its children. Unset: TT-1's development semantics (prepare_template: identity-validated
# reuse between invocations, a build when no receipt matches - preserved under the Human Governor's decision 3A).
# "build-once": this child may make the run's sole fresh build, into an empty run-owned store, and refuses any
# pre-existing template evidence. "reuse-only": this child must find the template the route recorded
# (EXPECTED_ENV = name:oid:identity), verify it, and refuse otherwise; it never enters the recovery-and-build path.
POLICY_ENV = "VERIFICATION_TEMPLATE_POLICY"
EXPECTED_ENV = "INTEVIA_TEMPLATE_EXPECTED"


def _store_evidence(store):
    directory = Path(store)
    if not directory.exists():
        return []
    return sorted(str(path.relative_to(directory)) for path in directory.rglob("*.json"))


def prepare_template_build_once(connection, alias, store):
    """The run's sole fresh build: refuse pre-existing template evidence, build once, never retry."""
    Path(store).mkdir(parents=True, exist_ok=True)
    evidence = _store_evidence(store)
    if evidence:
        raise TemplateReceiptInvalid(
            "R2 build-once: the run's template store already holds %d record(s) (%s); a pre-existing template is never "
            "adopted as this run's fresh-build proof" % (len(evidence), ", ".join(evidence[:5])))
    source_lines = source_input_lines()
    _, identity = complete_identity(source_lines + runtime_input_lines(connection))
    name = template_database_name(identity)
    if _database_record(connection, name) is not None:
        raise TemplateNameCollision(
            "R2 build-once: a database named %s already exists on the server before this run's build; left untouched" % name)
    evidence_root_path = os.environ.get("INTEVIA_EVIDENCE_ROOT_PATH")
    if not evidence_root_path:
        raise TemplateIdentityUnavailable("authorized evidence root path is required for the template build")
    try:
        receipt, built = _build_template(connection, alias, store, source_lines)
    except Exception:
        # TT-1 recovery removes only corroborated owned remnants; the failure propagates and nothing retries the build
        recover_template_builds(connection, evidence_root_path)
        raise
    if not built:
        raise TemplateReceiptInvalid("R2 build-once: the build path returned an existing template instead of building one")
    return receipt, True


def parse_expected_template(value):
    parts = (value or "").split(":")
    if len(parts) != 3 or not parts[0] or not parts[1].isdigit() or re.fullmatch(r"[0-9a-f]{64}", parts[2]) is None:
        raise TemplateReceiptInvalid("R2 reuse-only: %s must be <template name>:<oid>:<identity>, got %r" % (EXPECTED_ENV, value))
    return {"name": parts[0], "oid": int(parts[1]), "identity": parts[2]}


def prepare_template_reuse_only(connection, alias, store, expected):
    """Reuse the template the route recorded, or refuse: no recovery, no build, no other receipt."""
    path = template_receipt_path(store, expected["identity"])
    receipt = read_template_receipt(path, expected["identity"])
    if receipt is None:
        raise TemplateReceiptInvalid("R2 reuse-only: the run's template receipt is absent at %s" % path)
    if receipt.get("name") != expected["name"] or receipt.get("oid") != expected["oid"]:
        raise TemplateReceiptInvalid(
            "R2 reuse-only: the receipt names %s (oid %s), not the run's template %s (oid %s)"
            % (receipt.get("name"), receipt.get("oid"), expected["name"], expected["oid"]))
    if receipt.get("run_nonce") != os.environ.get("VERIFICATION_RUN_NONCE"):
        raise TemplateReceiptInvalid("R2 reuse-only: the receipt belongs to another run")
    if receipt.get("candidate_binding") != os.environ.get("INTEVIA_CANDIDATE_BINDING_SHA256"):
        raise TemplateReceiptInvalid("R2 reuse-only: the receipt's candidate binding differs from this run's")
    listing, identity = collect_template_identity(connection=connection)
    if identity != expected["identity"] or listing != receipt.get("listing"):
        raise TemplateIdentityUnavailable("R2 reuse-only: the current identity inputs differ from the recorded template identity")
    verify_registered_template(connection, receipt, identity, listing)
    return receipt, False


def finalise_route_template(connection, settings, store, identity, final_name, capture=None, result=None):
    """CI-CLONE-1 final inspection, run by the route's TPL-FINAL child: one final clone, its CP-0 compared with the
    fresh-build reference and recorded (TT-1 record_final_clone_cp0, which refuses a difference), then the template
    dropped by its receipt's oid (TT-1 cleanup_template). Cleanup is never reached on a mismatch. The connection's
    database name is restored on every exit path. The result record is populated IN PLACE (pass `result`), so what was
    established before a refusal - matches False, both digests, the clone's setup time - survives the raise (Argon G1).
    Returns the record; raises on a failure."""
    from . import state
    from .template import cleanup_template, create_clone_database, record_final_clone_cp0

    capture = capture or (lambda: state.digest(state.capture()))
    if result is None:
        result = {}
    result.update({"final_clone": final_name, "identity": identity, "matches": None, "template_dropped": False,
                   "parity_recorded": False})
    receipt_path = template_receipt_path(store, identity)
    receipt = read_template_receipt(receipt_path, identity)
    if receipt is None:
        raise TemplateReceiptInvalid("template receipt is absent: %s" % receipt_path)
    result.update(template=receipt["name"], template_oid=receipt["oid"], template_cp0_digest=receipt["cp0_digest"])
    started = time.perf_counter()
    create_clone_database(connection, receipt, final_name)
    result["clone_setup_seconds"] = round(time.perf_counter() - started, 6)
    original_name = connection.settings_dict["NAME"]
    original_setting = settings.DATABASES["default"]["NAME"]
    connection.close()
    connection.settings_dict["NAME"] = final_name
    settings.DATABASES["default"]["NAME"] = final_name
    try:
        digest = capture()
    finally:
        connection.close()
        connection.settings_dict["NAME"] = original_name
        settings.DATABASES["default"]["NAME"] = original_setting
    result["final_clone_cp0_digest"] = digest
    result["matches"] = digest == receipt["cp0_digest"]
    if not result["matches"]:
        raise TemplateReceiptInvalid(
            "the template integrity check failed: the final clone's compared CP-0 properties (%s) do not match the "
            "fresh-build reference (%s); the template was left untouched by this cleanup path" % (digest, receipt["cp0_digest"]))
    record_final_clone_cp0(receipt_path, identity, digest)
    result["parity_recorded"] = True
    cleanup_template(connection, receipt_path, identity)
    result["template_dropped"] = True
    return result


def run_final_inspection(connection, settings, store, identity, final_name, out_path, capture=None):
    """The TPL-FINAL child's entry point (importable, so T4 exercises it; Argon §3): runs the final inspection, writes
    the result record to out_path on every path - with `error` set and whatever the inspection established (matches,
    digests, clone setup time) kept on a failure - and returns the child's exit status (0 only when the template was
    inspected, matched and dropped)."""
    import json

    result = {"final_clone": final_name, "identity": identity, "matches": None, "template_dropped": False}
    try:
        finalise_route_template(connection, settings, store, identity, final_name, capture=capture, result=result)
        status = 0
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        status = 1
    with open(out_path, "x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1)
    return status


class TemplateModeRunnerMixin:
    """TT-1 template mode for the route's test runners: clone (the default) or Human-authorised fresh.

    One implementation, shared by IsolationRunner and MutationRunner (CI-CLONE-1, 31 Oct 2026: clone mode wherever a
    database is created). In clone mode the template is prepared - under the CI route's policy (POLICY_ENV: build-once
    for the one building step, reuse-only for every later step) or, with no policy set, under TT-1's development
    semantics (prepare_template) - and the runner's test database is created as a clone of it (install_clone_creation).
    A clone pre-flight refusal is an error; fresh-mode fallback is prohibited (R9). The mixin calls
    DiscoverRunner.setup_databases directly, after installing the owned creation path on every alias, exactly as
    IsolationRunner did before the extraction. Clone creation is installed on the default alias only; under a CI policy a
    further alias with its own physical database is refused before setup (it would be created fresh); a mirror is fine.
    """

    def setup_databases(self, **kwargs):
        from django.db import connections
        from django.test.runner import DiscoverRunner

        from .runner import _runner_print

        candidate_binding = os.environ.get("INTEVIA_CANDIDATE_BINDING_SHA256")
        if not isinstance(candidate_binding, str) or re.fullmatch(r"[0-9a-f]{64}", candidate_binding) is None:
            # Commission §1: unavailable candidate identity is an evidence gap, never a successful fallback.
            raise RuntimeError("R9: INTEVIA_CANDIDATE_BINDING_SHA256 is missing or invalid")
        mode = os.environ.get("VERIFICATION_TEMPLATE_MODE", "clone")
        if mode not in ("fresh", "clone"):
            raise RuntimeError("R2: template mode must be clone or authorized fresh")
        self.template_mode = mode
        policy = os.environ.get(POLICY_ENV)
        if policy not in (None, "", "build-once", "reuse-only"):
            raise RuntimeError("R2: %s must be unset, build-once or reuse-only, got %r" % (POLICY_ENV, policy))
        self.template_policy = policy or None
        if mode == "fresh":
            from .run_plan import validate_fresh_authority

            self.fresh_authority = validate_fresh_authority(
                os.environ.get("INTEVIA_FRESH_AUTHORITY_FILE"),
                os.environ.get("INTEVIA_FRESH_AUTHORITY_SHA256"),
                os.environ.get("INTEVIA_RUN_SUFFIX", ""),
            )
        else:
            self.fresh_authority = "NONE"
        self.template_receipt = None
        self.template_prepare_seconds = 0.0
        self.template_build_seconds = 0.0
        self.template_rebuilt = False
        self.clone_setup_seconds = None
        self.setup_outcome = "not started"
        try:
            return self._prepare_and_set_up(connections, DiscoverRunner, mode, kwargs)
        finally:
            # Argon G4: the outcome is printed on every exit - a pre-flight refusal reads "not started", a failed clone
            # "failed", a completed clone "succeeded" - so the route names what happened instead of finding no line
            _runner_print("TT1_SETUP_OUTCOME=%s" % self.setup_outcome, file=sys.stderr, flush=True)

    def _prepare_and_set_up(self, connections, DiscoverRunner, mode, kwargs):
        from .runner import _runner_print

        for alias in connections:
            install(connections[alias].creation)

        if mode == "clone":
            store = os.environ.get("VERIFICATION_TEMPLATE_STORE")
            if not store:
                raise RuntimeError("VERIFICATION_TEMPLATE_STORE is required for clone mode")
            connection = connections["default"]
            if self.template_policy is not None:
                for alias in connections:
                    settings_dict = connections[alias].settings_dict
                    if alias != "default" and not (settings_dict.get("TEST") or {}).get("MIRROR"):
                        raise RuntimeError(
                            "R2: database alias %r has its own physical test database, which the CI clone path does not "
                            "cover and would create fresh; the step was refused before any database was created" % alias)
            template_started = time.perf_counter()
            try:
                if self.template_policy == "build-once":
                    self.template_receipt, built = prepare_template_build_once(connection, "default", store)
                elif self.template_policy == "reuse-only":
                    expected = parse_expected_template(os.environ.get(EXPECTED_ENV))
                    self.template_receipt, built = prepare_template_reuse_only(connection, "default", store, expected)
                else:
                    self.template_receipt, built = prepare_template(connection, "default", store)
            except TemplateSealingRefused:
                raise
            except (TemplateIdentityUnavailable, TemplateNameCollision, TemplateReceiptInvalid) as exc:
                raise RuntimeError("R9: clone pre-flight refused (%s); fresh-mode fallback is prohibited" % exc) from exc
            else:
                self.template_prepare_seconds = time.perf_counter() - template_started
                self.template_rebuilt = bool(built)
                if built:
                    self.template_build_seconds = self.template_prepare_seconds
                # F3: the build time is reported as soon as the preparation completed, before the clone is attempted
                _runner_print(
                    "TT1_TEMPLATE_BUILD_SECONDS=%.6f" % self.template_build_seconds
                    if self.template_rebuilt else "TT1_TEMPLATE_BUILD_SECONDS=NOT_APPLICABLE",
                    file=sys.stderr,
                    flush=True,
                )
                install_clone_creation(connection.creation, self.template_receipt)

        setup_started = time.perf_counter()
        self.setup_outcome = "started"
        try:
            old_config = DiscoverRunner.setup_databases(self, **kwargs)
        except BaseException:
            self.setup_outcome = "failed"
            raise
        else:
            self.setup_outcome = "succeeded"
        finally:
            setup_seconds = time.perf_counter() - setup_started
            self.clone_setup_seconds = setup_seconds
            label = "TT1_CLONE_SETUP_SECONDS" if mode == "clone" and self.template_receipt is not None else "TT1_FRESH_SETUP_SECONDS"
            _runner_print("%s=%.6f" % (label, setup_seconds), file=sys.stderr, flush=True)
        self.observed_aliases = sorted(
            {connection.alias for connection, _, _ in old_config}
        )
        self.extra_aliases = [
            alias for alias in self.observed_aliases if alias != "default"
        ]
        self.alias_refs = {}
        return old_config
