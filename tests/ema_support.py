"""Email-only fixtures, never a production notice or an authority provider."""

from django.conf import settings
from django.urls import path

from core.models import OrganismCommandReceipt
from email_foundation.backend import CaptureBackend, NotAccepted
from email_foundation.contracts import Template
from email_foundation.views import outbox
from intevia.urls import urlpatterns as main_urlpatterns

NOTICE = Template(
    "fixture.notice",
    1,
    "NOTICE",
    "A test record changed",
    "Open your authorised test record in INTEVIA.",
    "/history/",
    "tests.ema_support.qualifier",
)
ESSENTIAL = Template(
    "fixture.essential",
    1,
    "ESSENTIAL",
    "A test security record changed",
    "Open your authorised test record in INTEVIA.",
    "/history/",
    "tests.ema_support.qualifier",
)


def qualifier(*, origin, recipient, context):
    if not getattr(settings, "INTEVIA_TEST_CONTEXT", False):
        return False
    act = OrganismCommandReceipt.objects.filter(
        receipt_uuid=context["record_id"], lineage_reference=origin.reference
    ).first()
    if act is None:
        return False
    expected = {
        "organisation": str(origin.organisation_id),
        "recipient": recipient.pk,
        "outcome": "UNCHANGED_TEST_ACT",
    }
    if act.result_payload == expected:
        return True
    payload = act.result_payload
    return (
        set(payload) == {"organisation", "recipients", "outcome"}
        and payload["organisation"] == str(origin.organisation_id)
        and payload["outcome"] == "UNCHANGED_TEST_ACT"
        and recipient.pk in payload["recipients"]
    )


class OutageBackend(CaptureBackend):
    def send_messages(self, messages):
        raise NotAccepted("test outage: definitely not accepted")


class UncertainBackend(CaptureBackend):
    def send_messages(self, messages):
        raise TimeoutError("test acceptance unknown")

    def lookup(self, intent):
        return "UNCERTAIN"


urlpatterns = [
    path("development/email-outbox/", outbox, name="ema-outbox"),
    *main_urlpatterns,
]
