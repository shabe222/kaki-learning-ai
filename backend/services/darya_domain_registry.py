"""Installed Domain Knowledge Pack registry.

Registry selection is the only place that knows which bounded domains are
installed.  Teaching behavior remains in the generic learning kernel.
"""
from __future__ import annotations

from services.darya_domain_knowledge import DomainKnowledgePack
from services.darya_physics_learning_domain import PACK as PHYSICS_PACK
from services.darya_python_learning_domain import PACK as PYTHON_PACK


INSTALLED_PACKS: dict[str, DomainKnowledgePack] = {
    PYTHON_PACK.domain_id: PYTHON_PACK,
    PHYSICS_PACK.domain_id: PHYSICS_PACK,
}
DEFAULT_PACK = PYTHON_PACK


def installed_packs() -> tuple[DomainKnowledgePack, ...]:
    return tuple(INSTALLED_PACKS.values())


def get_domain_pack(domain_id: str | None, *, default: DomainKnowledgePack | None = None) -> DomainKnowledgePack | None:
    if domain_id:
        return INSTALLED_PACKS.get(str(domain_id))
    return default


def pack_for_node(node_id: str, *, default: DomainKnowledgePack | None = DEFAULT_PACK) -> DomainKnowledgePack | None:
    for pack in INSTALLED_PACKS.values():
        if node_id in pack.nodes:
            return pack
    return default


def registry_contract() -> dict[str, object]:
    return {
        "contract_version": "darya_domain_pack_registry_v1",
        "installed_domains": [
            {"domain_id": pack.domain_id, "title": pack.title, "scope": pack.scope}
            for pack in INSTALLED_PACKS.values()
        ],
        "unsupported_behavior": "Return to ordinary DARYA interaction without claiming an installed learning domain.",
    }
