from __future__ import annotations

import hashlib


def generic_public_label(index: int) -> str:
    return f"学习目标{index + 1}"


def safe_gap_code(text: str) -> str:
    return "gap-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
