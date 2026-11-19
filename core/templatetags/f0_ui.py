"""Small reusable renderers for F0's component kit."""
from django import template
from django.conf import settings
from django.urls import reverse
from core.ui import DOMAINS, initials
register = template.Library()
register.filter("initials", initials)


@register.filter
def circle_kind_label(kind):
    return "General Circle" if kind == "GENERIC" else f"{kind.title()} Circle"


@register.simple_tag(takes_context=True)
def ui_frame(context):
    request = context["request"]
    signed_in = request.user.is_authenticated
    identity = getattr(request, "intevia_identity", None) if signed_in else None
    colour_mode = getattr(identity, "colour_mode", "device")
    restricted = getattr(identity, "access_state", "") == "restricted"
    name = (getattr(identity, "display_name", "") or request.user.get_full_name()) if signed_in else ""
    you_url = reverse("restricted") + "#you" if restricted else reverse("f0-you")
    current = request.resolver_match.url_name if request.resolver_match else ""
    active = {"shell":"home", "f0-explore":"explore", "f0-organisation":"organisation", "office_page":"organisation", "organisation_home":"organisation", "circle_page":"organisation", "f0-history":"history", "f0-you":"you", "login":"you", "restricted":"you"}.get(current, "organisation" if current.startswith("event-") else "history" if current in {"registration-history","attendance-history","registration-detail","attendance-detail"} else "")
    restricted_page = current == "restricted"
    tabs = [
        {"id":"home", "label":"Home", "icon":"home", "url":reverse("shell")},
        {"id":"explore", "label":"Explore", "icon":"explore", "url":reverse("f0-explore")},
        {"id":"organisation", "label":"INTEVIA", "icon":"organisation", "url":reverse("f0-organisation")},
        {"id":"history", "label":"History", "icon":"history", "url":reverse("f0-history")},
        {"id":"you", "label":"You" if signed_in else "Sign in", "icon":"you", "url":you_url if signed_in else reverse("login")},
    ]
    scope = "you" if current == "f0-you" else "organisation"
    show_domains = current in {"f0-organisation", "f0-you", "office_page", "organisation_home", "circle_page"} or current.startswith("event-") or current == "f0-domain" or context.get("catalogue_domains", False)
    if current == "f0-domain":
        scope = request.resolver_match.kwargs.get("scope", "organisation")
        active = "you" if scope == "you" else "organisation"
    domains=[]
    for slug,label in DOMAINS.items():
        enabled=slug=="events" and scope=="organisation"
        domains.append({"slug":slug,"label":label,"enabled":enabled,"current":enabled and current.startswith("event-"),
                        "url":reverse("event-list") if enabled else reverse("f0-domain",args=[scope,slug])})
    return {"colour_mode":colour_mode,"tabs":tabs,"active":active,"initials":initials(name),"you_url":you_url,
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


_PROVISION_LABELS = {
    "OFFICE_BASIS": "Office basis",
    "APPOINTING_AUTHORITY": "Appointing authority",
    "ALTERNATIVE_ROUTE": "Alternative route",
    "NOMINATION_ROUTE": "Nomination route",
    "PARTICIPATION_REQUIRED": "Required participation",
    "EX_OFFICIO_SEAT": "Seat held by virtue of office",
}


@register.inclusion_tag("core/components/office_provisions.html")
def office_provisions(record):
    """Place released text beside its unique current source in this office payload."""
    provisions = record.get("provisions", ())
    setting = record.get("care", {})
    observation = setting.get("observation") if setting.get("enabled") else None
    source = None
    if observation:
        matches = [
            row for row in provisions
            if row["relationship"] == "OFFICE_BASIS" and row["status"] in ("cited_and_in_force", "in_force_now")
            and row.get("locator") and row["locator"] == observation["source_locator"]
            and row["version"] == observation["source_version"]
        ]
        if len(matches) == 1:
            source = matches[0]

    # Keep each historical disclosure next to the current row of the same relationship.
    ordered = sorted(provisions, key=lambda row: (row["relationship"], row["status"] == "cited_at_definition"))
    rows = [{
        "provision": row,
        "label": _PROVISION_LABELS[row["relationship"]],
        "observation": observation if row is source else None,
    } for row in ordered]
    return {"office": record["office"], "rows": rows}


@register.inclusion_tag("core/components/office_timeline.html")
def office_timeline(record):
    """Render the delivered history in order, joining only this reader's returned facts."""
    versions = {(p["adoption_record"], p["binding"]): p["version"]
                for p in record.get("provisions", ()) if p.get("adoption_record") and p.get("binding")}
    decisions = {(d["result_uuid"], d["result_link"]): d
                 for d in record.get("decisions", ()) if d.get("result_uuid") and d.get("result_link")}
    titles = {t["thread_link"]: t["title"] for t in record.get("topics", ()) if t.get("thread_link")}
    enabled = record.get("care", {}).get("enabled", False)
    rows = []
    for entry in record.get("history", ()):
        kind = entry.get("kind")
        key = (entry.get("ilc_link"), entry.get("source_link"))
        if kind == "law_adoption":
            version = versions.get(key)
            sentence = (f"Version {version} of the office provision came into force." if version is not None
                        else "The office provision came into force.")
        elif kind == "decision":
            decision = decisions.get(key, {})
            if decision.get("purpose") == "standing_adoption" and decision.get("adopted_version") is not None:
                sentence = f'The Board voted to adopt version {decision["adopted_version"]} of the Policy in force.'
            elif decision.get("purpose") == "one_payment":
                sentence = f'The Board approved the one payment {decision["label"]}.'
            else:
                sentence = "The Board carried the vote."
        elif kind == "topic":
            sentence = titles.get(entry.get("source_link"), "A Topic about this office was started.")
        elif kind == "setting_change":
            sentence = "CARE assistance was turned on." if entry["enabled"] else "CARE assistance was turned off."
        elif kind == "care_observation" and enabled:
            rows.append({"at": entry["at"], "care": True, "sentence": entry["words"],
                         "locator": entry["source_locator"], "version": entry["source_version"]})
            continue
        else:
            continue
        rows.append({"at": entry["at"], "action": entry["label"], "sentence": sentence})
    return {"rows": rows}
