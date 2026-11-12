"""TIMED EVENTS handoff: from one confirmed, signed-in request to one engine attempt.

Three things must all be true before a request leaves anything for the engine:

1. A landed service marked the LO, at the point where it had established the
   person's standing there (``mark``). Only the first LO marked in a request counts.
2. The view confirmed success on its success path (``confirm``). A handled refusal,
   a caught exception or a failure branch never calls it.
3. The middleware's checks pass (``note``): the host switch is on; the person is
   signed in; their identity is ACTIVE and is the one the service marked; the
   response is not streaming and is not an error.

The mark lives on a slip that exists only while the view runs. The middleware opens
it before the view and discards it in a ``finally``. If all three conditions hold,
what the slip held is bound to that exact response (``bind``): the attempt runs
from the response's own ``close``, after Django's own close, once. If the response
is never closed, the note dies with it. Nothing is carried from one request to the
next.

A mark confers nothing: no office, grant or authority. The engine checks the
person again when it runs.
"""

from contextvars import ContextVar
from dataclasses import dataclass

from django.conf import settings

from core.models import Identity
from core.session import SESSION_ACCESS_EPOCH_KEY
from src.intevia.services import timed_events


@dataclass
class Slip:
    """What one request has marked and confirmed so far. It lives only while the view runs."""

    organism_uuid: str | None = None
    identity_id: int | None = None
    confirmed: bool = False
    token: object = None


_SLIP = ContextVar("intevia_timed_events_slip", default=None)


def open_slip():
    """Open an empty slip for the request about to be handled."""
    slip = Slip()
    slip.token = _SLIP.set(slip)
    return slip


def discard(slip):
    """Close the request's slip, whether the view returned or raised."""
    try:
        _SLIP.reset(slip.token)
    except ValueError:
        _SLIP.set(None)


def mark(organism_uuid, actor):
    """Called by a landed service once it has established this person's standing in this LO."""
    slip = _SLIP.get()
    if slip is None or slip.organism_uuid is not None:
        return
    slip.organism_uuid = str(organism_uuid)
    slip.identity_id = actor.pk


def confirm():
    """Called by a view on its success path only."""
    slip = _SLIP.get()
    if slip is not None:
        slip.confirmed = True


def note(request, response, slip):
    """The Wake this request leaves for the engine, or None."""
    if getattr(settings, "INTEVIA_TIMED_EVENTS_WAKE", False) is not True:
        return None
    if not slip.confirmed or slip.organism_uuid is None:
        return None
    if getattr(response, "streaming", False) or response.status_code >= 400:
        return None
    user = getattr(request, "user", None)
    identity = getattr(request, "intevia_identity", None)
    if user is None or not user.is_authenticated or identity is None:
        return None
    if identity.access_state != Identity.AccessState.ACTIVE or identity.pk != slip.identity_id:
        return None
    epoch = request.session.get(SESSION_ACCESS_EPOCH_KEY)
    if type(epoch) is not int:
        return None
    return timed_events.Wake(slip.organism_uuid, identity.pk, identity.credential_id, epoch)


def bind(response, wake, *, alias):
    """Bind the note to this exact response: one attempt, from its own close, after Django's.

    Django's close runs first and unchanged, so its closers and ``request_finished``
    run as they always do. The note is marked consumed before anything else, so a
    second call to ``close`` does nothing. The attempt never raises.
    """
    django_close = response.close
    consumed = []

    def close():
        if consumed:
            return
        consumed.append(True)
        django_close()
        timed_events.run(wake, alias=alias)

    response.close = close
