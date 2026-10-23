from __future__ import annotations

import re

OPAQUE_SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_PATH = re.compile(r"^\\\\")


def validate_opaque_source_id(value: str) -> bool:
    """Return True only for conservative opaque identifiers, never paths or URLs."""
    if not isinstance(value, str) or not OPAQUE_SOURCE_ID_PATTERN.fullmatch(value):
        return False
    if ".." in value or any(char in value for char in ("/", "\\", ":")):
        return False
    return True


def looks_like_private_path_or_url(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip()
    return bool(
        _URL_SCHEME.match(text)
        or _WINDOWS_ABSOLUTE.match(text)
        or text.startswith("/")
        or _UNC_PATH.match(text)
        or text.startswith("../")
        or text.startswith("..\\")
    )
