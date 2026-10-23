"""文本处理工具"""


def extract_assistant_summary(last_state: dict) -> str:
    """从工作流输出中提取助手回复文本（用于保存到会话上下文）"""
    parts = []
    if last_state.get("student_profile"):
        p = last_state["student_profile"]
        parts.append(f"[画像更新] {p.get('major','')} {p.get('grade_level','')}")
    if last_state.get("document"):
        parts.append(f"[学习文档] {last_state['document'].get('title','')}")
    if last_state.get("questions"):
        parts.append(f"[练习题] 共{len(last_state['questions'])}题")
    if last_state.get("mindmap"):
        parts.append("[思维导图] 已生成")
    if last_state.get("code_examples"):
        parts.append(f"[代码案例] 共{len(last_state['code_examples'])}个")
    if last_state.get("final_summary"):
        parts.append(last_state["final_summary"])
    return " | ".join(parts) if parts else "已处理"
