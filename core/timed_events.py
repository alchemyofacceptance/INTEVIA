"""The request side of TIMED EVENTS: one slip per request, one note per eligible response."""

from src.intevia.services import timed_events_handoff as handoff


class TimedEventsWakeMiddleware:
    """Open a slip for the view; afterwards bind an eligible note to the response's close.

    It sits after ``core.session.IdentityAccessMiddleware``, which supplies the
    signed-in identity. With the host switch off (``INTEVIA_TIMED_EVENTS_WAKE``)
    nothing is ever bound. Marks made by services use the ``default`` database
    alias, so the attempt is given that alias by name.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        slip = handoff.open_slip()
        try:
            response = self.get_response(request)
        finally:
            handoff.discard(slip)
        wake = handoff.note(request, response, slip)
        if wake is not None:
            handoff.bind(response, wake, alias="default")
        return response
