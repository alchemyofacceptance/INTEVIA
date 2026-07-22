from functools import wraps

from django.utils.cache import patch_cache_control, patch_vary_headers


def personal_response(view):
    @wraps(view)
    def protected(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        patch_cache_control(response, private=True, no_store=True)
        patch_vary_headers(response, ("Cookie",))
        return response

    return protected


__all__ = ["personal_response"]