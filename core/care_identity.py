"""The public identifier and name reserved for INTEVIA's CARE account."""

from unicodedata import normalize
from uuid import UUID

CARE_ID = UUID("b79c7d99-67b3-4dd0-8ab7-ee1328d1e183")
RESERVED_NAME = (
    "This name belongs to CARE and is reserved by INTEVIA. "
    "Nothing was registered or changed. Choose another name and try again. "
    "If you think this is wrong, contact INTEVIA support."
)


def reserved_name(username, display_name=""):
    return any(
        isinstance(value, str) and normalize("NFKC", value).casefold().strip() == "care"
        for value in (username, display_name)
    )
