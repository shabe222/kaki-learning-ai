from __future__ import annotations
import re
from .enums import NodeType, ResourceType

def normalized_meaning(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()

def fallback_node_type(domain: str, title: str) -> NodeType:
    t=(domain+" "+title).lower()
    if any(x in t for x in ["基础","导论","概述","basic","introduction"]): return NodeType.FOUNDATION
    if any(x in t for x in ["实验","操作","步骤","算法","编程","procedure","workflow"]): return NodeType.PROCEDURE
    if any(x in t for x in ["应用","案例","迁移","application"]): return NodeType.APPLICATION
    if any(x in t for x in ["综合","总结","整合","integration"]): return NodeType.INTEGRATION
    if any(x in t for x in ["测验","评估","复习","assessment"]): return NodeType.ASSESSMENT
    return NodeType.CONCEPT

def extra_fallback_resources(domain: str, node_type: NodeType, title: str) -> list[ResourceType]:
    t=(domain+" "+title).lower(); out=[]
    if node_type in {NodeType.PROCEDURE,NodeType.SKILL} and any(x in t for x in ["python","编程","代码","算法"]): out.append(ResourceType.CODE)
    if node_type in {NodeType.CONCEPT,NodeType.INTEGRATION} and any(x in t for x in ["结构","关系","体系","概念"]): out.append(ResourceType.MIND_MAP)
    if node_type in {NodeType.PROCEDURE,NodeType.APPLICATION} and any(x in t for x in ["过程","实验","操作","代谢"]): out.append(ResourceType.VIDEO)
    return out
