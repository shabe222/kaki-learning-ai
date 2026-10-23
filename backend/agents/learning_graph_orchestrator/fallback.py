from __future__ import annotations
from collections import defaultdict
from .calibrator import extra_fallback_resources, fallback_node_type, normalized_meaning
from .compiler import compile_blueprint
from .enums import EdgeType, ResourcePlanStatus, ResourceTrigger, ResourceType
from .models import (
    CandidateBlueprint, CandidateKnowledgeEdge, CandidateKnowledgeNode, CandidateLearningUnit,
    CandidateResourcePlan, LearningGraphRequest,
)
from .registry import CapabilityRegistry

def build_fallback_candidate(request: LearningGraphRequest) -> CandidateBlueprint:
    registry=CapabilityRegistry.from_items(request.resource_capabilities)
    outline=sorted(request.source_outline,key=lambda x:(x.order,x.level,x.outline_id))
    level1=[x for x in outline if x.level==min(y.level for y in outline)]
    children=defaultdict(list)
    for x in outline:
        if x.parent_outline_id: children[x.parent_outline_id].append(x)
    units=[]; nodes=[]; resources=[]; unit_node_keys=[]
    for ui,top in enumerate(level1):
        candidates=sorted(children.get(top.outline_id,[]),key=lambda x:(x.order,x.outline_id)) or [top]
        keys=[]
        for ni,item in enumerate(candidates):
            key=f"outline:{item.outline_id}"; keys.append(key); ref_ids=[item.source_ref_id] if item.source_ref_id else ([request.source_refs[0].ref_id] if request.source_refs else [])
            ntype=fallback_node_type(request.domain,item.title)
            nodes.append(CandidateKnowledgeNode(
                key=key,continuity_key=normalized_meaning(item.title),title=item.title,
                summary=f"围绕「{item.title}」整理的学习节点，具体内容以资料原文为准。",
                node_type=ntype,learning_objectives=[f"能够说明{item.title}的核心范围",f"能够完成与{item.title}相关的基础检验"],
                key_concepts=[item.title],mastery_evidence=[f"用自己的话解释{item.title}",f"完成一组关于{item.title}的检查题"],
                difficulty_band="basic",estimated_effort_minutes=30,source_ref_ids=ref_ids,status="planned",
            ))
            planned=[ResourceType.HANDOUT,ResourceType.QUESTION]+extra_fallback_resources(request.domain,ntype,item.title)
            seen=set()
            for pri,rtype in enumerate(planned):
                if rtype in seen or not registry.supports(rtype): continue
                seen.add(rtype)
                resources.append(CandidateResourcePlan(
                    key=f"{key}:{rtype.value}",continuity_key=normalized_meaning(f"{item.title}:{rtype.value}"),node_key=key,resource_type=rtype,
                    purpose=f"帮助学习和检验「{item.title}」",
                    generation_spec={"topic":item.title,"source_ref_ids":ref_ids,"fallback_spec":True},
                    required_inputs=["node_title","source_ref_ids"],trigger=ResourceTrigger.USER_STARTS_LEARNING,
                    status=ResourcePlanStatus.PLANNED,priority=80-pri*5,
                ))
        units.append(CandidateLearningUnit(key=f"unit:{top.outline_id}",title=top.title,summary=f"围绕「{top.title}」展开的学习单元。",node_keys=keys,unit_order=ui))
        unit_node_keys.extend(keys)
    # If only one outline root produced one node, split conservatively using goal without inventing details.
    if len(nodes)==1 and request.constraints.min_nodes>=2:
        base=nodes[0]; review_key=base.key+":assessment"
        nodes.append(CandidateKnowledgeNode(
            key=review_key,continuity_key=normalized_meaning(base.title+"综合检验"),title=base.title+"综合检验",
            summary="对前面所学内容进行综合回顾与检验。",node_type="assessment",
            learning_objectives=[f"整合并复述{base.title}的主要内容"],key_concepts=[base.title],
            mastery_evidence=[f"完成{base.title}的综合检验并说明理由"],difficulty_band="basic",estimated_effort_minutes=20,
            source_ref_ids=base.source_ref_ids,status="planned",
        ))
        units[0].node_keys.append(review_key); unit_node_keys.append(review_key)
        if registry.supports(ResourceType.QUESTION):
            resources.append(CandidateResourcePlan(key=review_key+":QUESTION",continuity_key=normalized_meaning(f"{base.title}综合检验:QUESTION"),node_key=review_key,resource_type=ResourceType.QUESTION,purpose="用于综合检验这一阶段的学习效果",generation_spec={"fallback_spec":True},required_inputs=["node_title"],trigger=ResourceTrigger.USER_STARTS_LEARNING,status=ResourcePlanStatus.PLANNED,priority=75))
    edges=[]
    for a,b in zip(unit_node_keys,unit_node_keys[1:]): edges.append(CandidateKnowledgeEdge(source_key=a,target_key=b,edge_type=EdgeType.PREREQUISITE,reason="按资料目录顺序排列学习先后"))
    return CandidateBlueprint(
        graph_title=level1[0].title if len(level1)==1 else request.user_goal[:80],
        graph_summary="这份学习图谱根据资料目录整理，先覆盖主要脉络，细节会在学习中逐步补充。",
        learning_units=units,nodes=nodes,edges=edges,resource_plans=resources,
        validation_hints=["fallback_used=true","needs_review=true"],continuity_keys=[normalized_meaning(x.title) for x in nodes[:10]],
    )

def build_fallback_blueprint(request: LearningGraphRequest):
    return compile_blueprint(request,build_fallback_candidate(request),fallback_used=True)
