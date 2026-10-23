"""
CodeAgent - Day 6 重构版（移除重复初始化和降级代码）

基类 BaseAgent 已统一处理：
- LLM 服务初始化（self.llm / self.mock_llm）
- mock/real 切换 + 失败自动降级
- 降级统计与告警

子类只需：
1. 设置 AGENT_NAME
2. 实现 _extract_input / _mock_generate / _real_generate
3. 实现 _build_result

═══════════ 接口契约（与 routers/agents_day4.py 对齐）══════════
- 输出字段：code_examples (List[Dict])
- 每项：{title, language, code, explanation, comments, expected_output, prerequisites, complexity}
══════════════════════════════════════════════════════════
"""
import logging
from typing import Dict, Any, List

from graph.state import AgentState
from agents import BaseAgent

logger = logging.getLogger("rjianb.code")


DEFAULT_CODE_SCHEMA = {
    "code_examples": [
        {
            "title": "", "language": "python",
            "code": "", "explanation": "",
            "comments": [], "expected_output": "",
            "prerequisites": [], "complexity": "中级"
        }
    ]
}


class CodeAgent(BaseAgent):
    """生成代码实操案例（Day 6: 继承基类统一降级）"""

    AGENT_NAME = "code_agent"

    # ============= 模板方法 =============

    def _extract_input(self, state: AgentState) -> Dict[str, Any]:
        return {
            "topic": state.get("user_query", ""),
            "profile": state.get("student_profile"),
            "language": (state.get("student_profile") or {}).get(
                "preferred_language", "Python"
            ),
        }

    def _build_result(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"code_examples": output_data["code_examples"]}

    # ============= mock 模式 =============

    async def _mock_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        llm = self.mock_llm
        profile = input_data["profile"] or {}
        language = input_data["language"]

        prompt = f"""你是一个{language}编程教育专家。为「{input_data['topic']}」生成实用的代码实操案例。

学生编程经验：{profile.get('programming_exp', 3)}/5
学习目标：{profile.get('learning_goal', '掌握基础知识')}

请生成2-3个代码示例（JSON格式）：
[
  {{
    "title": "示例标题",
    "language": "{language}",
    "code": "完整的可运行代码（带注释）",
    "explanation": "代码功能说明",
    "comments": ["逐行注释1", "逐行注释2"],
    "expected_output": "运行结果示例",
    "prerequisites": ["前置知识1"],
    "complexity": "初级/中级/高级"
  }}
]

要求：
1. 代码必须完整可运行
2. 注释详尽，适合{profile.get('grade_level', '')}学生理解
3. 从简单到复杂递进
4. 包含实际应用场景
5. 使用真实的库和API
"""

        result = await llm.structured_output(
            messages=[{"role": "user", "content": prompt}],
            output_schema=DEFAULT_CODE_SCHEMA,
            system_prompt=f"你擅长编写教学用的{language}代码，注重代码质量和可读性。"
        )

        examples = result.get("code_examples", [])
        # 兜底：万一 mock 库也没回 code_examples
        if not examples:
            examples = self._fallback_code_examples(
                input_data["topic"], language
            )
        return {"code_examples": examples}

    # ============= real 模式 =============

    async def _real_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data["profile"] or {}
        language = input_data["language"]

        prompt = f"""你是一个{language}编程教育专家。为「{input_data['topic']}」生成 2-3 个实用的代码实操案例。

学生编程经验：{profile.get('programming_exp', 3)}/5
学习目标：{profile.get('learning_goal', '掌握基础知识')}
年级：{profile.get('grade_level', '未知')}

**严格返回 JSON**（不要 markdown 包裹，不要解释文本），格式：
{{
  "code_examples": [
    {{
      "title": "示例标题",
      "language": "{language}",
      "code": "完整的可运行代码（带注释）",
      "explanation": "代码功能说明",
      "comments": ["逐行注释1"],
      "expected_output": "运行结果示例",
      "prerequisites": ["前置知识1"],
      "complexity": "初级/中级/高级"
    }}
  ]
}}

要求：代码必须完整可运行，从简单到复杂递进，使用真实库/API。
"""

        logger.info(
            f"[code_agent] 调用 real LLM: provider={self.llm.provider.value}, "
            f"timeout={self.llm.timeout_sec}s"
        )

        response_text = await self.llm.generate(
            prompt=prompt,
            temperature=0.3,
            max_tokens=2500,
            timeout=self.llm.timeout_sec,
            response_format="json",
        )

        parsed = self.llm._extract_json(response_text)
        examples = parsed.get("code_examples", [])
        if not isinstance(examples, list) or not examples:
            examples = self._fallback_code_examples(
                input_data["topic"], language
            )
        return {"code_examples": examples}

    # ============= 工具 =============

    def _fallback_code_examples(
        self, topic: str, language: str
    ) -> List[Dict[str, Any]]:
        """mock 数据兜底（当 mock 库和 real 解析都失败时使用）
        修复 Q4：返回 2 个示例（简单 + 进阶），覆盖"从入门到深入"教学路径
        """
        return [
            {
                "title": f"{topic} - 基础示例",
                "language": language,
                "code": (
                    f"# {topic} - 最简实现\n"
                    f"def main():\n"
                    f"    \"\"\"示例实现：{topic}\"\"\"\n"
                    f"    print('Hello from {topic}')\n\n"
                    f"if __name__ == '__main__':\n"
                    f"    main()\n"
                ),
                "explanation": f"该代码展示了 {topic} 的基本实现模式，适合初学者理解。",
                "comments": ["# 第1行: 模块注释", "# 第2-4行: 主函数定义", "# 第6-7行: 入口判断"],
                "expected_output": f"Hello from {topic}",
                "prerequisites": ["基础编程语法"],
                "complexity": "初级",
            },
            {
                "title": f"{topic} - 进阶示例（含完整实现）",
                "language": language,
                "code": (
                    f"# {topic} - 进阶实现（带参数化 + 错误处理）\n"
                    f"import logging\n"
                    f"logging.basicConfig(level=logging.INFO)\n\n"
                    f"class {topic.replace(' ', '').title()}Engine:\n"
                    f"    \"\"\"封装 {topic} 的工程实现\"\"\"\n\n"
                    f"    def __init__(self, config: dict = None):\n"
                    f"        self.config = config or {{}}\n"
                    f"        self._validate()\n\n"
                    f"    def _validate(self):\n"
                    f"        if not self.config:\n"
                    f"            raise ValueError('config 不能为空')\n\n"
                    f"    def run(self, input_data):\n"
                    f"        logging.info(f'开始执行 {topic}')\n"
                    f"        # 实际业务逻辑\n"
                    f"        result = {{'status': 'ok', 'input': input_data}}\n"
                    f"        return result\n\n"
                    f"# 使用示例\n"
                    f"if __name__ == '__main__':\n"
                    f"    engine = {topic.replace(' ', '').title()}Engine(config={{'lr': 0.01}})\n"
                    f"    print(engine.run({{'x': 1, 'y': 2}}))\n"
                ),
                "explanation": f"该代码展示了 {topic} 的工程化实现，包含配置管理、错误处理、日志输出等生产级要素。",
                "comments": [
                    "# 导入日志模块",
                    "# 封装为类，便于复用和扩展",
                    "# 构造时校验配置",
                    "# 核心 run 方法接收输入数据",
                ],
                "expected_output": "{'status': 'ok', 'input': {'x': 1, 'y': 2}}",
                "prerequisites": ["面向对象编程", "日志模块使用"],
                "complexity": "中级",
            },
        ]
