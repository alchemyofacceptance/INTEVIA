"""Trusted per-package templates and audience adapters; no public enqueue endpoint."""

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.validators import validate_email
from django.utils.module_loading import import_string


class MailRefused(ValueError):
    """A request was refused before an intent was created; no support case implied."""


@dataclass(frozen=True)
class Origin:
    reference: str
    organisation_id: object | None = None


@dataclass(frozen=True)
class Template:
    name: str
    version: int
    category: str
    subject: str
    text: str
    path_prefix: str
    qualifier: str
    # qualifier resolves persisted act, authority and audience in caller's transaction;
    # False means refuse. The worker invokes it again before sending the link.

    def digest(self):
        return digest(self.__dict__)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
        ).encode()
    ).hexdigest()


def templates():
    result = {}
    for path in getattr(settings, "INTEVIA_EMAIL_TEMPLATES", ()):
        template = import_string(path)
        if not isinstance(template, Template) or template.category not in {
            "ESSENTIAL",
            "NOTICE",
        }:
            raise ImproperlyConfigured(
                "Email templates must be reviewed ESSENTIAL or NOTICE contracts."
            )
        if (
            template.name.startswith("ema.")
            or (template.name, template.version) in result
        ):
            raise ImproperlyConfigured(
                "Email template identity is reserved or repeated."
            )
        if (
            not re.fullmatch(r"[a-z0-9_.-]{1,100}", template.name)
            or template.version < 1
        ):
            raise ImproperlyConfigured(
                "Email template needs a stable name and positive version."
            )
        if not template.qualifier or not template.path_prefix.startswith("/"):
            raise ImproperlyConfigured(
                "Email template needs its audience adapter and local link prefix."
            )
        safe_header(template.subject)
        if len(template.subject) > 200 or len(template.text) > 2000:
            raise ImproperlyConfigured("Email template text exceeds its bounded size.")
        result[template.name, template.version] = template
    return result


def safe_header(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise MailRefused(
            "Email was not requested: the address or header is invalid. No email was queued; correct the value and try again."
        )
    return value


def address(value):
    safe_header(value)
    if len(value) > 254:
        raise MailRefused(
            "Email was not requested: the address is too long. No email was queued; use one valid inbox address."
        )
    try:
        validate_email(value)
    except Exception as exc:
        raise MailRefused(
            "Email was not requested: use one valid inbox address. No email was queued; correct the address and try again."
        ) from exc
    # Preserve local-part case; do not silently equate addresses across Identities.
    local, domain = value.rsplit("@", 1)
    return local + "@" + domain.lower()


def base_url():
    value = getattr(settings, "INTEVIA_EMAIL_BASE_URL", "http://localhost:8000")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ImproperlyConfigured(
            "Email base URL must be an origin without credentials, path or query."
        )
    if parsed.scheme == "http" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ImproperlyConfigured(
            "Email links require HTTPS except on loopback development hosts."
        )
    return value.rstrip("/")


def context_path(context, template):
    # Only an opaque record UUID is accepted: no names, prose, address, audience or URL.
    import uuid

    if not isinstance(context, dict) or set(context) != {"record_id"}:
        raise MailRefused(
            "Email was not requested: this template accepts only its record reference. No email was queued; use the reviewed package adapter."
        )
    try:
        record = str(uuid.UUID(str(context["record_id"])))
    except (ValueError, TypeError, AttributeError) as exc:
        raise MailRefused(
            "Email was not requested: the record reference is not a valid UUID. No email was queued; the calling package must supply its recorded identifier."
        ) from exc
    prefix = template.path_prefix
    if not re.fullmatch(r"/[a-z0-9/_-]+/", prefix):
        raise ImproperlyConfigured(
            "Email link prefix must be a fixed local record path."
        )
    return prefix + record + "/"


def qualify(template, origin, recipient, context):
    return (
        import_string(template.qualifier)(
            origin=origin, recipient=recipient, context=context
        )
        is True
    )


def number(name, default, minimum=1):
    value = getattr(settings, name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ImproperlyConfigured(
            name + " must be an integer within its allowed range."
        )
    return value
