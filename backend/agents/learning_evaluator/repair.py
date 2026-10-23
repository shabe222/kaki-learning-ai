from __future__ import annotations
from .prompt import build_repair_prompt
from .parser import parse_candidate

def repair_candidate_once(provider,model_input,previous_output,report):
    return parse_candidate(provider.generate(build_repair_prompt(model_input,str(previous_output),report)))
