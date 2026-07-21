"""Bounded session binding for canonical CORE Identity."""

from __future__ import annotations

import time

from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from core.models import Identity


SESSION_IDENTITY_KEY = "intevia_identity_id"
SESSION_ACCESS_EPOCH_KEY = "intevia_access_epoch"
SESSION_STARTED_AT_KEY = "intevia_session_started_at"
SESSION_ABSOLUTE_AGE_SECONDS = 8 * 60 * 60


def bind_identity_session(request, identity: Identity) -> None:
    request.session[SESSION_IDENTITY_KEY] = str(identity.identity_id)
    request.session[SESSION_ACCESS_EPOCH_KEY] = identity.access_epoch
    request.session[SESSION_STARTED_AT_KEY] = int(time.time())
    request.session.set_expiry(0)


class IdentityAccessMiddleware:
    """Fail closed when session Identity binding or lifecycle is stale."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            identity = self._valid_identity(request)
            if identity is None:
                logout(request)
                return redirect(reverse("login"))
            request.intevia_identity = identity
            restricted_path = reverse("restricted")
            logout_path = reverse("logout")
            if identity.access_state == Identity.AccessState.RESTRICTED:
                if request.path_info not in {restricted_path, logout_path}:
                    return redirect(restricted_path)
            elif request.path_info == restricted_path:
                return redirect(reverse("shell"))
        return self.get_response(request)

    @staticmethod
    def _valid_identity(request) -> Identity | None:
        try:
            identity = Identity.objects.select_related("credential").get(
                credential=request.user
            )
        except (Identity.DoesNotExist, Identity.MultipleObjectsReturned):
            return None
        if identity.access_state not in {
            Identity.AccessState.ACTIVE,
            Identity.AccessState.RESTRICTED,
        }:
            return None
        if identity.credential_id != request.user.pk:
            return None
        if request.session.get(SESSION_IDENTITY_KEY) != str(identity.identity_id):
            return None
        if request.session.get(SESSION_ACCESS_EPOCH_KEY) != identity.access_epoch:
            return None
        started_at = request.session.get(SESSION_STARTED_AT_KEY)
        if not isinstance(started_at, int):
            return None
        if int(time.time()) - started_at >= SESSION_ABSOLUTE_AGE_SECONDS:
            return None
        return identity


__all__ = [
    "IdentityAccessMiddleware",
    "SESSION_ABSOLUTE_AGE_SECONDS",
    "bind_identity_session",
]