"""Canonical CORE identity values shared by models and services."""

from __future__ import annotations

import unicodedata

from django.core.exceptions import ValidationError


CANONICAL_USERNAME_VERSION = "canonical_username_v1"
CANONICAL_USERNAME_MAX_LENGTH = 255


def canonical_username_v1(username: str) -> str:
    if not isinstance(username, str):
        raise ValidationError("username must be text")

    canonical = unicodedata.normalize("NFKC", username).casefold().strip()
    if not canonical:
        raise ValidationError("username is empty after normalisation")
    if any(character.isspace() for character in canonical):
        raise ValidationError("username must not contain internal whitespace")
    if any(
        unicodedata.category(character) in {"Cc", "Cf"}
        for character in canonical
    ):
        raise ValidationError(
            "username must not contain control or format characters"
        )
    if len(canonical) > CANONICAL_USERNAME_MAX_LENGTH:
        raise ValidationError("canonical username is too long")
    return canonical


__all__ = [
    "CANONICAL_USERNAME_MAX_LENGTH",
    "CANONICAL_USERNAME_VERSION",
    "canonical_username_v1",
]