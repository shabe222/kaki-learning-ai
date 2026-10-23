from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Protocol

class ProviderError(RuntimeError): pass
class GraphProvider(Protocol):
    name: str
    model_id: str
    async def generate(self,prompt: str) -> str | dict[str,Any]: ...

class UnavailableProvider:
    name="unavailable"; model_id="unavailable"
    def __init__(self,reason="provider not configured"): self.reason=reason
    async def generate(self,prompt): raise ProviderError(self.reason)

@dataclass
class StaticProvider:
    responses: list[str | dict[str,Any]]
    name: str="static"
    model_id: str="fixture"
    calls: int=0
    async def generate(self,prompt):
        if self.calls>=len(self.responses): raise ProviderError("no static response left")
        value=self.responses[self.calls]; self.calls+=1; return value
