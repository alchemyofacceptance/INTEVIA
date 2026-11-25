"""Presentation of facts already released to the reader of a law or an office."""

from django import template

register = template.Library()


@register.simple_tag
def adopting_body_name(issuer, organisation, organisation_name, route, board_name):
    """Name the adopting body from the visible route and the page's admitted names."""
    if issuer != organisation:
        return "Inherited from INTEVIA"
    if route == "Board vote" and board_name:
        return f"{board_name} · {organisation_name}"
    return organisation_name
