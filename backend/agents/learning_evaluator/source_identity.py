from __future__ import annotations
import re
from typing import Annotated
from pydantic import AfterValidator

OPAQUE_ID_RE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
PRIVATE_PATTERNS=(re.compile(r"^[A-Za-z]:[\\/]"),re.compile(r"^/"),re.compile(r"^\\\\"),re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://"))

def validate_opaque_id(value: str) -> str:
    if not isinstance(value,str) or not OPAQUE_ID_RE.fullmatch(value):
        raise ValueError("opaque id must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    if ".." in value or any(c in value for c in "/\\:"):
        raise ValueError("opaque id contains forbidden path or scheme characters")
    return value

def looks_private(value: str) -> bool:
    return any(p.search(value or "") for p in PRIVATE_PATTERNS) or ".." in (value or "")

OpaqueId=Annotated[str,AfterValidator(validate_opaque_id)]
