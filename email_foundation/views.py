"""Read-only disposable local/peek operator view, not a product inbox."""

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from core.cache import personal_response
from core.models import Identity

from .models import Outbox


@personal_response
@require_GET
def outbox(request):
    if (
        settings.SETTINGS_MODULE
        not in {"intevia.peek_settings", "intevia.email_local_settings"}
        or not settings.DEBUG
        or not settings.INTEVIA_EMAIL_OUTBOX_PAGE
    ):
        raise Http404
    identity = getattr(request, "intevia_identity", None)
    if (
        not request.user.is_authenticated
        or identity is None
        or str(identity.identity_id) not in settings.INTEVIA_EMAIL_OPERATOR_IDENTITIES
        or not Identity.objects.filter(
            pk=identity.pk,
            credential=request.user,
            access_state=Identity.AccessState.ACTIVE,
            access_epoch=identity.access_epoch,
        ).exists()
    ):
        raise Http404
    rows = Outbox.objects.select_related("template").order_by("-created_at")[:100]
    response = render(request, "email_foundation/outbox.html", {"rows": rows})
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
