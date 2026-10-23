"""
PathAgent - Day 6 重构版（移除重复初始化和降级代码）

基类 BaseAgent 已统一处理：
- LLM 服务初始化（self.llm / self.mock_llm）
- mock/real 切换 + 失败自动降级
- 降级统计与告警

子类只需：
1. 设置 AGENT_NAME
2. 实现 _extract_input / _mock_generate / _real_generate
3. 实现 _build_result

═══════════ 接口契约（与 routers/agents_day4.py 对齐）══════════
- 输出字段：learning_path (List[Dict])
  - 每项：step_number, topic, description, resources, estimated_hours, prerequisites, status
══════════════════════════════════════════════════════════
"""
import logging
from typing import Dict, Any, List

from graph.state import AgentState
from agents import BaseAgent

logger = logging.getLogger("rjianb.path")


DEFAULT_PATH_SCHEMA = {
    "learning_path": [
        {
            "step_number": 1, "topic": "", "description": "",
            "resources": [], "estimated_hours": 10,
            "prerequisites": [], "status": "not_started"
        }
    ]
}


class PathAgent(BaseAgent):
    """规划个性化学习路径（Day 6: 继承基类统一降级）"""

    AGENT_NAME = "path_agent"

    # ============= 模板方法 =============

    def _extract_input(self, state: AgentState) -> Dict[str, Any]:
        return {
            "goal": state.get("user_query", ""),
            "profile": state.get("student_profile"),
            "generated_resources": {
                "has_document": bool(state.get("document")),
                "has_questions": bool(state.get("questions")),
                "has_code": bool(state.get("code_examples")),
                "has_mindmap": bool(state.get("mindmap")),
            }
        }

    def _build_result(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"learning_path": output_data["path"]}

    # ============= mock 模式 =============

    async def _mock_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        llm = self.mock_llm
        profile = input_data["profile"] or {}

        prompt = f"""你是一个学习路径规划专家。根据学生的画像和学习目标，制定个性化的学习路径。

学习目标：{input_data['goal']}

学生画像：
- 专业：{profile.get('major', '未知')}
- 年级：{profile.get('grade_level', '未知')}
- 数学基础：{profile.get('math_foundation', 3)}/5
- 编程经验：{profile.get('programming_exp', 3)}/5
- ML知识：{profile.get('ml_knowledge', 2)}/5
- 每周可用时间：{profile.get('weekly_hours', 10)}小时
- 学习风格：{profile.get('learning_style', '综合型')}
- 薄弱点：{', '.join(profile.get('weak_points', []))}
- 兴趣方向：{', '.join(profile.get('interests', []))}

已生成的资源：
{input_data['generated_resources']}

请制定4-6个阶段的学习路径（JSON格式）：
[
  {{
    "step_number": 1,
    "topic": "阶段主题",
    "description": "详细描述",
    "resources": [{{"type": "文档/视频/代码/练习", "title": "资源名称"}}],
    "estimated_hours": 10,
    "prerequisites": ["前置知识"],
    "status": "not_started"
  }}
]

要求：
1. 路径要符合认知规律，由浅入深
2. 每个阶段有明确的学习目标和产出
3. 考虑学生的时间安排（每周{profile.get('weekly_hours', 10)}小时）
4. 针对薄弱环节加强训练
5. 结合学生的兴趣方向
6. 总时长控制在8-12周
"""

        result = await llm.structured_output(
            messages=[{"role": "user", "content": prompt}],
            output_schema=DEFAULT_PATH_SCHEMA,
            system_prompt="你是教育规划专家，擅长根据学生特点制定科学的学习路径。"
        )
        path = result.get("learning_path", [])
        if not path:
            path = self._fallback_path(input_data["goal"])
        return {"path": path}

    # ============= real 模式 =============

    async def _real_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data["profile"] or {}

        prompt = f"""为以下学生制定 4-6 个阶段的个性化学习路径。

学习目标：{input_data['goal']}
专业：{profile.get('major', '未知')}
年级：{profile.get('grade_level', '未知')}
数学基础：{profile.get('math_foundation', 3)}/5
编程经验：{profile.get('programming_exp', 3)}/5
ML知识：{profile.get('ml_knowledge', 2)}/5
每周可用时间：{profile.get('weekly_hours', 10)}小时
薄弱点：{', '.join(profile.get('weak_points', []))}
兴趣方向：{', '.join(profile.get('interests', []))}

**严格返回 JSON**（不要 markdown 包裹），格式：
{{
  "learning_path": [
    {{
      "step_number": 1,
      "topic": "阶段主题",
      "description": "详细描述",
      "resources": [{{"type": "文档", "title": "资源名"}}],
      "estimated_hours": 10,
      "prerequisites": ["前置知识"],
      "status": "not_started"
    }}
  ]
}}

要求：4-6 阶段，由浅入深，总时长 8-12 周，路径合理，针对薄弱环节加强。
"""

        logger.info(
            f"[path_agent] 调用 real LLM: provider={self.llm.provider.value}, "
            f"timeout={self.llm.timeout_sec}s"
        )

        response_text = await self.llm.generate(
            prompt=prompt,
            temperature=0.4,
            max_tokens=2500,
            timeout=self.llm.timeout_sec,
            response_format="json",
        )

        parsed = self.llm._extract_json(response_text)
        path = parsed.get("learning_path", [])
        if not isinstance(path, list) or not path:
            path = self._fallback_path(input_data["goal"])
        return {"path": path}

    # ============= 工具 =============

    def _fallback_path(self, goal: str) -> List[Dict[str, Any]]:
        return [
            {
                "step_number": i + 1,
                "topic": f"阶段 {i + 1} - {goal}",
                "description": f"本阶段聚焦 {goal} 的关键能力建设。",
                "resources": [
                    {"type": "文档", "title": f"阶段 {i + 1} 入门教材"},
                    {"type": "练习", "title": f"阶段 {i + 1} 配套练习"},
                ],
                "estimated_hours": 12,
                "prerequisites": [],
                "status": "not_started",
            }
            for i in range(4)
        ]
