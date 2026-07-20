"""Bounded authority evaluation for the governed Contribution lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from django.contrib.auth.models import User

from core.models import Profile, ProfileRole


class NotAuthorised(PermissionError):
    """Raised when a governed Contribution action is not authorised."""


class EntitlementCapability(Protocol):
    """Replaceable cross-cutting capability consumed by Slice 001."""

    def authorise(
        self,
        *,
        identity: Profile,
        action: str,
        target: object,
        timestamp: datetime,
    ) -> str | None:
        """Return a durable authority reference, or no authority."""


class ContributionAuthority:
    """Resolve Django identity and consume bounded entitlement capability."""

    def __init__(self, capability: EntitlementCapability) -> None:
        if capability is None or not callable(
            getattr(capability, "authorise", None)
        ):
            raise TypeError("capability must implement authorise")
        self._capability = capability

    def evaluate(
        self,
        *,
        identity: User,
        action: str,
        target: object,
        timestamp: datetime,
    ) -> tuple[Profile, str]:
        if not isinstance(identity, User) or not identity.is_active:
            raise NotAuthorised("an active Django identity is required")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise NotAuthorised("authority timestamp must be timezone-aware")

        try:
            profile = Profile.objects.get(user=identity)
        except (Profile.DoesNotExist, Profile.MultipleObjectsReturned) as exc:
            raise NotAuthorised(
                "identity must resolve to exactly one Profile"
            ) from exc

        if not ProfileRole.objects.filter(profile=profile).exists():
            raise NotAuthorised("an active role assignment is required")

        authority_reference = self._capability.authorise(
            identity=profile,
            action=action,
            target=target,
            timestamp=timestamp,
        )
        if not isinstance(authority_reference, str):
            raise NotAuthorised("the requested action is not authorised")
        authority_reference = authority_reference.strip()
        if not authority_reference:
            raise NotAuthorised("the requested action is not authorised")
        if len(authority_reference) > 255:
            raise NotAuthorised("authorisation reference is too long")
        return profile, authority_reference


__all__ = [
    "ContributionAuthority",
    "EntitlementCapability",
    "NotAuthorised",
]