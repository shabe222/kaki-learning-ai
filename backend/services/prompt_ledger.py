"""A prompt string that remembers how it was built.

Answering "what did the model actually see?" used to mean opening the router,
finding eighteen appends, and then reading fourteen modules to learn what each
directive contributes. That is the whole reason this file exists.

The trick is deliberately small: the assembled prompt is a ``str`` subclass
whose ``__add__`` records what was appended. Every existing ``prompt = prompt +
directive`` keeps working unchanged, which matters because a mechanical rewrite
of eighteen multi-line expressions inside a 2,900-line route is exactly how you
break a turn that was working.

So a turn can now report:

    [prompt] total=5210 ← base=1180, tide=640, evidence=420, orchestration=900, ...

and, when asked, hand back the whole thing for reading.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Iterable, Mapping, Sequence

# Where full prompts land when dumping is switched on. Off by default: a dump
# contains whatever the learner said, so it must be opt-in, never ambient.
DUMP_ENV = "DARYA_PROMPT_DUMP"
DUMP_DIR = pathlib.Path(__file__).resolve().parents[2] / ".runtime" / "prompt-dump"

_HEADER = re.compile(r"^[\s#*]*【?([^】\n]{2,40})】?")


def _label(text: str) -> str:
    """A short name for an appended block, taken from how it opens."""
    for line in str(text).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("【") and "】" in stripped:
            return stripped[1 : stripped.index("】")][:32]
        match = _HEADER.match(stripped)
        return (match.group(1) if match else stripped)[:32]
    return "(blank)"


class PromptLedger(str):
    """The assembled prompt, plus the record of how it was assembled."""

    def __new__(cls, value: str = "") -> "PromptLedger":
        instance = super().__new__(cls, value)
        instance._sections = [("base", len(value))] if value else []
        return instance

    def __add__(self, other: object) -> "PromptLedger":
        text = str(other)
        result = PromptLedger(str(self) + text)
        result._sections = list(getattr(self, "_sections", []))
        # Whitespace joins are not sections; they are the glue between them.
        if text.strip():
            result._sections.append((_label(text), len(text)))
        return result

    def __radd__(self, other: object) -> "PromptLedger":
        return PromptLedger(str(other) + str(self))

    def __getitem__(self, key: object) -> object:
        # Slicing a str subclass would silently drop the record; keep it.
        result = super().__getitem__(key)  # type: ignore[index]
        return result

    @property
    def sections(self) -> Sequence[tuple[str, int]]:
        return tuple(getattr(self, "_sections", ()))

    def report(self) -> str:
        """One line: total size and what each part contributed."""
        parts = ", ".join(f"{name}={size}" for name, size in self.sections)
        return f"total={len(self)} ← {parts}"

    def summary(self) -> dict[str, int]:
        """Sizes folded together by section name, largest first."""
        folded: dict[str, int] = {}
        for name, size in self.sections:
            folded[name] = folded.get(name, 0) + size
        return dict(sorted(folded.items(), key=lambda item: -item[1]))


def dumping_enabled() -> bool:
    return str(os.environ.get(DUMP_ENV) or "").strip() not in ("", "0", "false", "False")


def dump(prompt: str, *, request_id: str = "turn", extra: str = "") -> pathlib.Path | None:
    """Write the full prompt out, but only when dumping was switched on."""
    if not dumping_enabled():
        return None
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(request_id or "turn"))[:60]
    path = DUMP_DIR / f"{safe}.txt"
    try:
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        body = prompt if isinstance(prompt, str) else str(prompt)
        path.write_text((extra + "\n\n" if extra else "") + body, encoding="utf-8")
        return path
    except OSError:
        return None


def sections_of(prompt: object) -> Iterable[tuple[str, int]]:
    return getattr(prompt, "sections", ())


# Empty containers cost real tokens and say nothing a missing key does not.
_EMPTY = ("", None, [], {})


def drop_empty(value, *, keep_keys: tuple[str, ...] = ()):
    """Recursively remove empty fields before serialising.

    An absent key and an empty list mean the same thing to a reader, but the
    empty list still costs its name and brackets. Measured on a plain chat turn,
    18% of the assembled prompt was serialised emptiness.
    """
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if str(key) in keep_keys:
                out[key] = item
                continue
            cleaned = drop_empty(item)
            if cleaned in _EMPTY:
                continue
            out[key] = cleaned
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [cleaned for item in value if (cleaned := drop_empty(item)) not in _EMPTY]
    return value


def dumps_compact(value, *, keep_keys: tuple[str, ...] = ()) -> str:
    """JSON for a prompt: no empty fields, no padding."""
    return json.dumps(drop_empty(value, keep_keys=keep_keys), ensure_ascii=False, separators=(",", ":"))

