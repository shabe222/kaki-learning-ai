from __future__ import annotations
from typing import Any
from .models import LearningGraphRequest

ALLOWED_REQUEST_KEYS = {
    "request_id", "graph_id", "graph_revision", "user_goal", "domain", "locale",
    "source_refs", "source_outline", "source_summaries", "existing_graph_summary",
    "resource_capabilities", "constraints", "update_mode", "replaced_source_ref_ids",
}
PRIVATE_METADATA_KEYS={"path","local_path","absolute_path","owner_email","token","secret","raw_content","database_id"}

class AdapterError(ValueError): pass

def _sanitize_source_ref(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict): raise AdapterError("source_refs entries must be objects")
    allowed={"ref_id","kind","title","content_hash","metadata"}
    unknown=set(item)-allowed
    if unknown: raise AdapterError(f"unsupported source_ref fields: {sorted(unknown)}")
    data=dict(item)
    metadata=data.get("metadata") or {}
    if not isinstance(metadata, dict): raise AdapterError("source_ref.metadata must be an object")
    leaked=PRIVATE_METADATA_KEYS.intersection(metadata)
    if leaked: raise AdapterError(f"private source metadata is forbidden: {sorted(leaked)}")
    data["metadata"]={k:v for k,v in metadata.items() if isinstance(v,(str,int,float,bool)) or v is None}
    return data

def adapt_request(payload: dict[str, Any]) -> LearningGraphRequest:
    if not isinstance(payload, dict): raise AdapterError("request payload must be an object")
    unknown=set(payload)-ALLOWED_REQUEST_KEYS
    if unknown: raise AdapterError(f"unsupported request fields: {sorted(unknown)}")
    data={k:payload[k] for k in ALLOWED_REQUEST_KEYS if k in payload}
    data["source_refs"]=[_sanitize_source_ref(x) for x in payload.get("source_refs",[])]
    return LearningGraphRequest.model_validate(data)
