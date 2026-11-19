from functools import wraps

from django.urls import Resolver404, resolve
from django.utils.cache import patch_cache_control, patch_vary_headers


def _mark_personal(response):
    patch_cache_control(response, private=True, no_store=True)
    patch_vary_headers(response, ("Cookie",))
    return response


def appearance_privacy(get_response):
    """Keep personal appearance and office reads private, including refusals before the view."""
    def middleware(request):
        response = get_response(request)
        match = request.resolver_match
        if match is None:
            try:
                match = resolve(request.path_info, urlconf=getattr(request, "urlconf", None))
            except Resolver404:
                return response
        if getattr(match.func, "personal_response", False):
            return _mark_personal(response)
        return response

    return middleware


def personal_response(view):
    @wraps(view)
    def protected(request, *args, **kwargs):
        return _mark_personal(view(request, *args, **kwargs))

    protected.personal_response = True
    return protected


__all__ = ["personal_response", "appearance_privacy"]