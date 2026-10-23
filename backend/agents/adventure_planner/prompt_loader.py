from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=4)
def load_prompt(name: str) -> str:
    path = _PROMPT_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()
