"""Guard the repository's host-clock reads against the commissioned site table."""

from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

from django.test import SimpleTestCase


_ROOT = Path(__file__).resolve().parents[1]
_CENSUS_PATH = _ROOT / "verification" / "clock" / "clock_census_v1_2.py"
_CENSUS_SHA256 = "5711d889a1358b50bfbd2685bf6b1bc1eb9cc008e2076b63bd44d81947abed6f"
_HOST_READ_CATEGORIES = {
    "HOST_CLOCK_CALL",
    "HOST_CLOCK_POSSIBLE",
    "HOST_CLOCK_REFERENCE",
    "HOST_CLOCK_UNRESOLVED",
    "MODEL_AUTO_TIME",
}


def _site(path: str, scope: str, category: str, form: str, ordinal: int = 1):
    return path, scope, category, form, ordinal


_PERMANENT_ALLOW_LIST = {
    _site("core/models.py", "Identity", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "Identity", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now=True)"),
    _site("core/models.py", "Role", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "ProfileRole", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "Contribution", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "Contribution", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now=True)"),
    _site("core/models.py", "ContributionVersion", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "EvidenceReference", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "Event", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now=True)"),
    _site("core/models.py", "EventAttendance", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "EventAttendance", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now=True)"),
    _site("core/models.py", "EventAttendanceTransition", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "EventAttendanceEligibilityReceipt", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "LibraryResource", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now=True)"),
    _site("core/models.py", "Service", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now=True)"),
    _site("core/models.py", "ServiceActivity", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now=True)"),
    _site("core/models.py", "LivingOrganism", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "Circle", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "OrganismMembership", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "OrganismRoleDefinition", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/models.py", "ContextualRoleAssignment", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"),
    _site("core/session.py", "bind_identity_session", "HOST_CLOCK_CALL", "time.time"),
    _site("core/session.py", "IdentityAccessMiddleware._valid_identity", "HOST_CLOCK_CALL", "time.time"),
}

_TEMPORARY_BASELINE = {
    _site("src/intevia/services/event_attendance_service.py", "EventAttendanceService", "HOST_CLOCK_REFERENCE", "django.utils.timezone.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/services/care_service.py", "CareService._at", "HOST_CLOCK_CALL", "django.utils.timezone.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/services/contribution_service.py", "ContributionService._at", "HOST_CLOCK_CALL", "django.utils.timezone.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/services/event_read_service.py", "EventReadService.present_event", "HOST_CLOCK_CALL", "datetime.datetime.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/services/event_registration_service.py", "EventRegistrationService._at", "HOST_CLOCK_CALL", "django.utils.timezone.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/services/event_service.py", "EventService._at", "HOST_CLOCK_CALL", "django.utils.timezone.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/services/identity_service.py", "IdentityLifecycleService._at", "HOST_CLOCK_CALL", "django.utils.timezone.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/services/identity_service.py", "IdentityLifecycleService._transition", "HOST_CLOCK_CALL", "django.utils.timezone.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/services/library_service.py", "LibraryService._at", "HOST_CLOCK_CALL", "django.utils.timezone.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/services/service_service.py", "GovernedService._at", "HOST_CLOCK_CALL", "django.utils.timezone.now"):
        ("PKT-CLK-2", "PKT-CLK-2"),
    _site("src/intevia/core/activity.py", "Activity", "HOST_CLOCK_CALL", "datetime.datetime.now"):
        ("PKT-CLK-4", "PKT-CLK-4"),
    _site("src/intevia/core/contribution.py", "HumanDecision", "HOST_CLOCK_CALL", "datetime.datetime.now"):
        ("PKT-CLK-4", "PKT-CLK-4"),
    _site("src/intevia/core/contribution.py", "Contribution", "HOST_CLOCK_CALL", "datetime.datetime.now"):
        ("PKT-CLK-4", "PKT-CLK-4"),
    _site("src/intevia/core/contribution.py", "Contribution.submit", "HOST_CLOCK_CALL", "datetime.datetime.now"):
        ("PKT-CLK-4", "PKT-CLK-4"),
    _site("src/intevia/core/contribution.py", "Contribution.begin_review", "HOST_CLOCK_CALL", "datetime.datetime.now"):
        ("PKT-CLK-4", "PKT-CLK-4"),
    _site("core/models.py", "EventResourceRelationshipEvidence", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"):
        ("PKT-CLK-5", "PKT-CLK-5"),
    _site("core/models.py", "AuthorityPrincipal", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"):
        ("PKT-CLK-5", "PKT-CLK-5"),
    _site("core/models.py", "AuthorityPrincipalAliasReview", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"):
        ("PKT-CLK-5", "PKT-CLK-5"),
    _site("core/models.py", "AuthorityDerivationEdge", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"):
        ("PKT-CLK-5", "PKT-CLK-5"),
    _site("core/models.py", "AuthorityBasis", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"):
        ("PKT-CLK-5", "PKT-CLK-5"),
    _site("core/models.py", "GovernedDetermination", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"):
        ("PKT-CLK-5", "PKT-CLK-5"),
    _site("core/models.py", "EmergencyAuthorityEnvelope", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"):
        ("PKT-CLK-5", "PKT-CLK-5"),
    _site("core/models.py", "GovernedVisibilityGrant", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"):
        ("PKT-CLK-5", "PKT-CLK-5"),
    _site("core/models.py", "OrganismCommandReceipt", "MODEL_AUTO_TIME", "models.DateTimeField(auto_now_add=True)"):
        ("PKT-CLK-5", "PKT-CLK-5"),
}

_EXPECTED_SITES = {
    **{site: ("PERMANENT", None) for site in _PERMANENT_ALLOW_LIST},
    **_TEMPORARY_BASELINE,
}


def _load_census():
    data = _CENSUS_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != _CENSUS_SHA256:
        raise AssertionError(f"carried detector SHA-256 mismatch: {digest}")
    spec = importlib.util.spec_from_file_location(
        "intevia_clock_census_v1_2",
        _CENSUS_PATH,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("carried detector could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scan_tree(census):
    listing = subprocess.run(
        [
            "git",
            "-C",
            str(_ROOT),
            "--no-optional-locks",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "src",
            "core",
            "intevia",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    sources = {}
    for rel in listing:
        path = Path(rel)
        if path.suffix != ".py" or rel.startswith("core/migrations/"):
            continue
        sources[rel] = (_ROOT / path).read_text(encoding="utf-8-sig")

    first_pass = {
        rel: census.scan_python(rel, source)
        for rel, source in sources.items()
    }
    wrappers, method_wrappers = {}, {}
    for result in first_pass.values():
        found_wrappers, found_methods = census.find_wrappers(result)
        wrappers.update(found_wrappers)
        method_wrappers.update(found_methods)

    findings = []
    for rel, source in sources.items():
        result = census.scan_python(rel, source, wrappers, method_wrappers)
        findings.extend(
            finding
            for finding in result.findings
            if finding["category"] in _HOST_READ_CATEGORIES
        )
    return findings


def _key_findings(findings):
    ordinals = Counter()
    keyed = {}
    for finding in findings:
        base = (
            finding["path"],
            finding["scope"],
            finding["category"],
            finding["detail"],
        )
        ordinals[base] += 1
        key = (*base, ordinals[base])
        keyed[key] = finding
    return keyed


def _display(site):
    path, scope, category, form, ordinal = site
    return f"{path}:{scope}:{category}:{form}#{ordinal}"


class ClockGuardTests(SimpleTestCase):
    def test_carried_detector_digest_and_all_forms(self):
        census = _load_census()
        passed, results = census.run_self_test()
        self.assertTrue(passed, "\n".join(results))
        self.assertTrue(results[-1].startswith("SELF_TEST=PASS (32 of 32"))

    def test_each_commissioned_host_clock_form_is_detected(self):
        census = _load_census()
        fixtures = (
            (
                "timezone.now()",
                "from django.utils import timezone\n"
                "timezone.now()\n",
            ),
            (
                "bound timezone now import",
                "from django.utils.timezone import now\n"
                "now()\n",
            ),
            (
                "aliased timezone module call",
                "from django.utils import timezone as tz\n"
                "tz.now()\n",
            ),
            (
                "localtime() without an argument",
                "from django.utils.timezone import localtime\n"
                "localtime()\n",
            ),
            (
                "localdate() without an argument",
                "from django.utils.timezone import localdate\n"
                "localdate()\n",
            ),
            (
                "datetime.now()",
                "from datetime import datetime\n"
                "datetime.now()\n",
            ),
            (
                "datetime.utcnow()",
                "from datetime import datetime\n"
                "datetime.utcnow()\n",
            ),
            (
                "datetime.today()",
                "from datetime import datetime\n"
                "datetime.today()\n",
            ),
            (
                "date.today()",
                "from datetime import date\n"
                "date.today()\n",
            ),
            ("time.time()", "import time\ntime.time()\n"),
            ("time.time_ns()", "import time\ntime.time_ns()\n"),
            ("time.gmtime()", "import time\ntime.gmtime()\n"),
            ("time.localtime()", "import time\ntime.localtime()\n"),
            ("time.ctime()", "import time\ntime.ctime()\n"),
            (
                "time.strftime(fmt) without a time",
                "import time\ntime.strftime('%Y')\n",
            ),
            (
                "captured clock=timezone.now default",
                "from django.utils import timezone\n"
                "def consume(clock=timezone.now):\n"
                "    return clock\n",
            ),
            (
                "captured default=timezone.now",
                "from django.db import models\n"
                "from django.utils import timezone\n"
                "class Fixture(models.Model):\n"
                "    created = models.DateTimeField(default=timezone.now)\n",
            ),
            (
                "captured datetime.now default_factory",
                "from dataclasses import dataclass, field\n"
                "from datetime import datetime\n"
                "@dataclass\n"
                "class Fixture:\n"
                "    created: datetime = field(default_factory=lambda: datetime.now())\n",
            ),
            (
                "auto_now",
                "from django.db import models\n"
                "class Fixture(models.Model):\n"
                "    updated = models.DateTimeField(auto_now=True)\n",
            ),
            (
                "auto_now_add",
                "from django.db import models\n"
                "class Fixture(models.Model):\n"
                "    created = models.DateTimeField(auto_now_add=True)\n",
            ),
        )
        self.assertEqual(len(fixtures), 20)
        for index, (form, source) in enumerate(fixtures):
            with self.subTest(form=form):
                findings = census.scan_python(
                    f"src/__clock_guard_form_{index}.py",
                    source,
                ).findings
                host_reads = [
                    finding
                    for finding in findings
                    if finding["category"] in _HOST_READ_CATEGORIES
                ]
                self.assertTrue(host_reads, form)

    def test_quiet_clock_forms_are_not_host_reads(self):
        census = _load_census()
        fixtures = (
            (
                "database Now()",
                "from django.db.models.functions import Now\n"
                "Now()\n",
            ),
            (
                "database clock_timestamp SQL",
                'cursor.execute("SELECT clock_timestamp()")\n',
            ),
            (
                "database_now()",
                "from src.intevia.services.clock import database_now\n"
                "database_now()\n",
            ),
        )
        for index, (form, source) in enumerate(fixtures):
            with self.subTest(form=form):
                findings = census.scan_python(
                    f"src/__clock_guard_quiet_{index}.py",
                    source,
                ).findings
                host_reads = [
                    finding
                    for finding in findings
                    if finding["category"] in _HOST_READ_CATEGORIES
                ]
                self.assertFalse(host_reads, form)

    def test_repository_sites_equal_allow_list_and_owned_baseline(self):
        census = _load_census()
        findings = _scan_tree(census)

        # This KEEP row forwards the constructor's captured default; the default site itself is counted above.
        forwarded = [
            item
            for item in findings
            if item["path"] == "src/intevia/services/event_attendance_service.py"
            and item["scope"] == "EventAttendanceService.__init__"
            and item["category"] == "HOST_CLOCK_REFERENCE"
            and item["source"] == "self.clock = clock"
        ]
        self.assertEqual(len(forwarded), 1)
        observed = _key_findings(
            item for item in findings if item not in forwarded
        )
        expected = set(_EXPECTED_SITES)
        self.assertEqual(
            set(observed),
            expected,
            "unexpected host-clock sites: "
            + ", ".join(
                sorted(_display(site) for site in set(observed) ^ expected)
            ),
        )

        owner_counts = Counter(owner for owner, _ in _EXPECTED_SITES.values())
        baseline_counts = Counter(
            owner for owner, _ in _TEMPORARY_BASELINE.values()
        )
        self.assertEqual(
            owner_counts,
            Counter(
                {
                    "PERMANENT": 23,
                    "PKT-CLK-2": 10,
                    "PKT-CLK-4": 5,
                    "PKT-CLK-5": 9,
                }
            ),
        )
        self.assertEqual(
            baseline_counts,
            Counter({"PKT-CLK-2": 10, "PKT-CLK-4": 5, "PKT-CLK-5": 9}),
        )

    def test_new_host_read_positive_control_is_named_and_rejected(self):
        census = _load_census()
        path = "src/__clock_guard_positive_control__.py"
        source = (
            "from django.utils import timezone\n"
            "def accidental_host_read():\n"
            "    return timezone.now()\n"
        )
        findings = census.scan_python(path, source).findings
        host_reads = [
            item for item in findings
            if item["category"] in _HOST_READ_CATEGORIES
        ]
        observed = _key_findings(host_reads)
        rejected = set(observed) - set(_EXPECTED_SITES)
        self.assertTrue(rejected)
        self.assertTrue(
            any(path in _display(site) for site in rejected),
            "positive control was not named in the rejected sites",
        )
