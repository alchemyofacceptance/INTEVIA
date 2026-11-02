"""Small reusable renderers for F0's component kit."""
from django import template
from django.conf import settings
from django.urls import reverse
from core.ui import DOMAINS, initials
register = template.Library()
register.filter("initials", initials)

@register.simple_tag(takes_context=True)
def ui_frame(context):
    request = context["request"]
    signed_in = request.user.is_authenticated
    restricted = getattr(getattr(request, "intevia_identity", None), "access_state", "") == "restricted"
    name = (getattr(getattr(request, "intevia_identity", None), "display_name", "") or request.user.get_full_name()) if signed_in else ""
    you_url = reverse("restricted") + "#you" if restricted else reverse("f0-you")
    current = request.resolver_match.url_name if request.resolver_match else ""
    active = {"shell":"home", "f0-explore":"explore", "f0-organisation":"organisation", "f0-history":"history", "f0-you":"you", "login":"you", "restricted":"you"}.get(current, "organisation" if current.startswith("event-") else "history" if current in {"registration-history","attendance-history","registration-detail","attendance-detail"} else "")
    restricted_page = current == "restricted"
    tabs = [
        {"id":"home", "label":"Home", "icon":"home", "url":reverse("shell")},
        {"id":"explore", "label":"Explore", "icon":"explore", "url":reverse("f0-explore")},
        {"id":"organisation", "label":"INTEVIA", "icon":"organisation", "url":reverse("f0-organisation")},
        {"id":"history", "label":"History", "icon":"history", "url":reverse("f0-history")},
        {"id":"you", "label":"You" if signed_in else "Sign in", "icon":"you", "url":you_url if signed_in else reverse("login")},
    ]
    scope = "you" if current == "f0-you" else "organisation"
    show_domains = current in {"f0-organisation", "f0-you"} or current.startswith("event-") or current == "f0-domain" or context.get("catalogue_domains", False)
    if current == "f0-domain":
        scope = request.resolver_match.kwargs.get("scope", "organisation")
        active = "you" if scope == "you" else "organisation"
    domains=[]
    for slug,label in DOMAINS.items():
        enabled=slug=="events" and scope=="organisation"
        domains.append({"slug":slug,"label":label,"enabled":enabled,"current":enabled and current.startswith("event-"),
                        "url":reverse("event-list") if enabled else reverse("f0-domain",args=[scope,slug])})
    return {"tabs":tabs,"active":active,"initials":initials(name),"you_url":you_url,
            "restricted_page":restricted_page,"show_domains":show_domains and not restricted_page,"domains":domains,"off_style":settings.INTEVIA_OFF_DOMAIN_STYLE}

@register.simple_tag
def field_control(field):
    """Keep Django's bound widget, escaping and password non-redisplay policy."""
    described=[]
    if field.help_text: described.append(field.auto_id+"_help")
    if field.errors: described.append(field.auto_id+"_errors")
    attrs={}
    if described: attrs["aria-describedby"]=" ".join(described)
    if field.errors: attrs["aria-invalid"]="true"
    return field.as_widget(attrs=attrs)
