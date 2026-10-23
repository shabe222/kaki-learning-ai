"""
TutorAgent - Day 6 重构版（移除重复初始化和降级代码）

基类 BaseAgent 已统一处理：
- LLM 服务初始化（self.llm / self.mock_llm）
- mock/real 切换 + 失败自动降级
- 降级统计与告警

子类只需：
1. 设置 AGENT_NAME
2. 实现 _extract_input / _mock_generate / _real_generate
3. 实现 _build_result

═══════════ 接口契约（与 routers/agents_day4.py 对齐）══════════
- 输出字段：tutor_response (str)
══════════════════════════════════════════════════════════
"""
import logging
from typing import Dict, Any

from graph.state import AgentState
from agents import BaseAgent

logger = logging.getLogger("rjianb.tutor")


class TutorAgent(BaseAgent):
    """智能答疑辅导（Day 6: 继承基类统一降级）"""

    AGENT_NAME = "tutor_agent"

    # ============= 模板方法 =============

    def _extract_input(self, state: AgentState) -> Dict[str, Any]:
        return {
            "question": state.get("user_query", ""),
            "context": state.get("messages", []),
            "profile": state.get("student_profile"),
        }

    def _build_result(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"tutor_response": output_data["response"]}

    # ============= mock 模式 =============

    async def _mock_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        llm = self.mock_llm
        profile = input_data["profile"] or {}

        prompt = f"""你是一个耐心的机器学习辅导老师。学生提出了以下问题：

问题：{input_data['question']}

对话上下文：
{input_data['context'][-5:] if input_data['context'] else '无'}

学生背景：
- 年级：{profile.get('grade_level', '未知')}
- 基础水平：数学{profile.get('math_foundation', 3)}/5，编程{profile.get('programming_exp', 3)}/5
- 薄弱点：{', '.join(profile.get('weak_points', []))}
- 学习风格：{profile.get('learning_style', '综合型')}

请给出详细的解答，包括：
1. **直接回答**：简洁明了的回答
2. **原理解释**：用通俗易懂的方式解释原理（根据学生水平调整深度）
3. **举例说明**：用具体例子帮助理解
4. **代码演示**（如适用）：提供{profile.get('preferred_language', 'Python')}代码示例
5. **常见误区**：指出初学者容易犯的错误
6. **延伸思考**：引导性的后续学习建议

回答要求：
- 语言亲切友好，像一对一辅导
- 循序渐进，不要一次性灌输太多
- 鼓励式教学，增强学生信心
- 字数500-800字
"""

        result = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="你是一位经验丰富的机器学习导师，善于因材施教，能用最简单的语言解释复杂的概念。"
        )
        content = result["content"] or self._fallback_response(input_data["question"])
        return {"response": content}

    # ============= real 模式 =============

    async def _real_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data["profile"] or {}

        prompt = f"""你是一位耐心的机器学习辅导老师。学生问：「{input_data['question']}」

学生背景：
- 年级：{profile.get('grade_level', '未知')}
- 基础水平：数学{profile.get('math_foundation', 3)}/5，编程{profile.get('programming_exp', 3)}/5
- 薄弱点：{', '.join(profile.get('weak_points', []))}

请给出 500-800 字的详细解答，结构：
1. 直接回答
2. 原理解释（根据学生水平调整深度）
3. 举例说明
4. 代码演示（如适用）
5. 常见误区
6. 延伸思考

请直接输出解答正文，不要任何额外包裹。
"""

        logger.info(
            f"[tutor_agent] 调用 real LLM: provider={self.llm.provider.value}, "
            f"timeout={self.llm.timeout_sec}s"
        )

        response_text = await self.llm.generate(
            prompt=prompt,
            temperature=0.5,
            max_tokens=2500,
            timeout=self.llm.timeout_sec,
        )
        content = self.llm._validate_response(response_text)
        return {"response": content}

    # ============= 工具 =============

    def _fallback_response(self, question: str) -> str:
        return (
            f"关于「{question}」，可以从以下几个角度理解：\n"
            f"1. 基本定义\n"
            f"2. 关键特征\n"
            f"3. 实际应用\n"
            f"4. 与相近概念的区别\n\n"
            f"如需更深入的讲解，请告诉我你的具体困惑点。"
        )
