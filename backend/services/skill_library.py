"""Agent Skill library — SKILL.md packs under the open Agent Skills standard.

Each skill is a directory under ``backend/skills/`` containing a ``SKILL.md``
(YAML front-matter with ``name`` / ``description`` per agentskills.io) and a
``manifest.json`` that records provenance: what was authored for DARYA, which
open methodology it is grounded in, and any vendored third-party code with its
license.  Agents load applicable packs and render them into their system
prompt, so generation is driven by skill guidance plus the live conversation
context instead of hardcoded template content.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"


class SkillError(RuntimeError):
    code = "SKILL_LIBRARY_ERROR"


@dataclass(frozen=True)
class SkillPack:
    name: str
    description: str
    body: str
    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def resource_kinds(self) -> tuple[str, ...]:
        return tuple(str(v) for v in (self.manifest.get("applies") or {}).get("resource_kinds") or ())

    @property
    def agents(self) -> tuple[str, ...]:
        return tuple(str(v) for v in (self.manifest.get("applies") or {}).get("agents") or ())

    def public_projection(self) -> dict[str, Any]:
        """Learner-safe provenance projection (never the full prompt body)."""
        source = dict(self.manifest.get("source") or {})
        return {
            "name": self.name,
            "description": self.description,
            "standard": self.manifest.get("standard"),
            "source_type": source.get("type"),
            "methodology_references": list(source.get("methodology_references") or []),
            "vendored_code": list(source.get("vendored_code") or []),
        }


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with --- front matter")
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        raise SkillError("SKILL.md front matter is not closed")
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in {"name", "description"}:
            meta[key] = value
    if not meta.get("name") or not meta.get("description"):
        raise SkillError("SKILL.md front matter requires name and description")
    body = "\n".join(lines[end + 1:]).strip()
    return meta, body


@lru_cache(maxsize=1)
def _load_all() -> tuple[SkillPack, ...]:
    packs: list[SkillPack] = []
    if not SKILLS_ROOT.exists():
        logger.warning("skill library root missing: %s", SKILLS_ROOT)
        return ()
    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        manifest_path = skill_dir / "manifest.json"
        if not skill_md.exists():
            continue
        try:
            meta, body = _parse_front_matter(skill_md.read_text(encoding="utf-8"))
        except SkillError as exc:
            logger.warning("skill %s skipped: %s", skill_dir.name, exc)
            continue
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("skill %s manifest unreadable: %s", skill_dir.name, exc)
        packs.append(SkillPack(
            name=meta["name"], description=meta["description"], body=body,
            path=skill_md, manifest=manifest,
        ))
    return tuple(packs)


def list_skills() -> tuple[SkillPack, ...]:
    return _load_all()


def get_skill(name: str) -> SkillPack | None:
    for pack in _load_all():
        if pack.name == name:
            return pack
    return None


def select_skills(
    *,
    agent: str | None = None,
    resource_kind: str | None = None,
    names: list[str] | None = None,
) -> list[SkillPack]:
    """Pick packs applicable to an agent and/or resource kind."""
    wanted = {n.strip() for n in (names or []) if n and n.strip()}
    selected: list[SkillPack] = []
    for pack in _load_all():
        if wanted and pack.name in wanted:
            selected.append(pack)
            continue
        if wanted:
            continue
        if agent and agent in pack.agents:
            selected.append(pack)
        elif resource_kind and resource_kind in pack.resource_kinds:
            selected.append(pack)
    return selected


def skill_directive(
    packs: list[SkillPack] | tuple[SkillPack, ...], *,
    max_chars_per_skill: int = 3200,
) -> str:
    """Render loaded skill bodies into a system-prompt directive section."""
    if not packs:
        return ""
    sections = []
    for pack in packs:
        body = pack.body
        if len(body) > max_chars_per_skill:
            body = body[:max_chars_per_skill].rstrip() + "\n…（指令截断）"
        sections.append(f"### Skill: {pack.name}\n{body}")
    return (
        "以下是从技能库加载的生成方法指令（SKILL.md 标准），生成必须遵循它们：\n"
        + "\n\n".join(sections)
    )


def skill_prompt_prefix(*names: str, max_chars_per_skill: int = 3200) -> str:
    """One-call helper for generators: render named skills as a prompt prefix.

    Returns an empty string when a pack is missing so call sites can compose
    unconditionally without guarding every join.
    """
    packs = [pack for pack in (get_skill(name) for name in names) if pack is not None]
    directive = skill_directive(packs, max_chars_per_skill=max_chars_per_skill)
    return directive + "\n\n" if directive else ""


def provenance_projection() -> list[dict[str, Any]]:
    """Inspectable provenance for every installed skill (no prompt bodies)."""
    return [pack.public_projection() for pack in _load_all()]
