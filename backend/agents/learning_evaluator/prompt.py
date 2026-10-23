from __future__ import annotations
import json
from .model_input import LearningEvaluationModelInput
from .models import ValidationReport

SYSTEM_PROMPT="""你是学习效果评估候选解释器。只能基于输入中的安全证据摘要，不能改变分数、证据事实或节点状态。误区必须关联至少两条真实证据；不确定内容必须保守。只输出EvaluationCandidate JSON。"""

def build_prompt(model_input:LearningEvaluationModelInput)->str:
    return SYSTEM_PROMPT+"\nMODEL_INPUT:\n"+json.dumps(model_input.model_dump(mode="json"),ensure_ascii=False,sort_keys=True)

def build_repair_prompt(model_input:LearningEvaluationModelInput,previous_output:str,report:ValidationReport)->str:
    safe={"allowed_evidence_ids":model_input.allowed_evidence_ids,"allowed_node_ids":model_input.allowed_node_ids,"evaluation_mode":model_input.evaluation_mode,
          "issues":[i.model_dump(mode="json") for i in report.issues],"model_input":model_input.model_dump(mode="json"),"previous_output":previous_output[:10000]}
    return SYSTEM_PROMPT+"\nREPAIR_ONCE:\n"+json.dumps(safe,ensure_ascii=False,sort_keys=True)
