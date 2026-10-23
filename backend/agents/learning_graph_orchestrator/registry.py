from __future__ import annotations
from dataclasses import dataclass
from .enums import ResourceType
from .models import ResourceCapability

@dataclass(frozen=True)
class CapabilityRegistry:
    capabilities: dict[ResourceType, ResourceCapability]

    @classmethod
    def from_items(cls, items: list[ResourceCapability]) -> "CapabilityRegistry":
        values: dict[ResourceType, ResourceCapability] = {}
        for item in items:
            if item.resource_type in values:
                raise ValueError(f"duplicate capability: {item.resource_type}")
            values[item.resource_type] = item
        return cls(values)

    def supports(self, resource_type: ResourceType) -> bool:
        item=self.capabilities.get(resource_type)
        return bool(item and item.enabled)

    def enabled_types(self) -> set[ResourceType]:
        return {k for k,v in self.capabilities.items() if v.enabled}

    def agent_for(self, resource_type: ResourceType) -> str:
        item=self.capabilities.get(resource_type)
        if not item or not item.enabled:
            raise KeyError(f"unsupported resource type: {resource_type}")
        return item.agent_key
