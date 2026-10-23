"""
MindmapAgent - Day 6 重构版（移除重复初始化和降级代码）

基类 BaseAgent 已统一处理：
- LLM 服务初始化（self.llm / self.mock_llm）
- mock/real 切换 + 失败自动降级
- 降级统计与告警

子类只需：
1. 设置 AGENT_NAME
2. 实现 _extract_input / _mock_generate / _real_generate
3. 实现 _build_result

═══════════ 接口契约（与 routers/agents_day4.py 对齐）══════════
- 输出字段：mindmap (str | Dict)
  - 字符串场景：Markdown 思维导图文本（mock 默认）
══════════════════════════════════════════════════════════
"""
import logging
from typing import Dict, Any

from graph.state import AgentState
from agents import BaseAgent

logger = logging.getLogger("rjianb.mindmap")


class MindmapAgent(BaseAgent):
    """生成思维导图（Markdown 格式）（Day 6: 继承基类统一降级）"""

    AGENT_NAME = "mindmap_agent"

    # ============= 模板方法 =============

    def _extract_input(self, state: AgentState) -> Dict[str, Any]:
        return {
            "topic": state.get("user_query", ""),
            "profile": state.get("student_profile"),
            "style": state.get("style", ""),
            "document_content": (
                state.get("document", {}).get("content", "")
                if state.get("document") else ""
            ),
        }

    def _build_result(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        # 关键修复：原实现直接返回 str，与 SharedContext.mindmap: Dict[str, Any] 契约不符。
        # 后续节点 BaseAgent._ensure_ctx 同步 mindmap 到 ctx 时，触发 Pydantic 验证错误
        # （"Input should be a valid dictionary"），导致 /api/chat 整体 500。
        # 包装成 {"content": str, "format": "markdown"} 既符合契约、又保留原始 markdown 文本。
        content = output_data.get("mindmap_content") or ""
        return {
            "mindmap": {
                "content": content,
                "format": "markdown",
                "topic": output_data.get("topic", ""),
            }
        }

    # ============= mock 模式 =============

    async def _mock_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        llm = self.mock_llm
        profile = input_data["profile"] or {}

        prompt = f"""你是一个知识结构化专家。为「{input_data['topic']}」这个知识点生成思维导图内容。

学生年级：{profile.get('grade_level', '未知')}
已有文档内容摘要：{input_data['document_content'][:500] if input_data['document_content'] else '无'}

请以Markdown格式生成思维导图（使用缩进表示层级关系）：

# {input_data['topic']}

## 核心概念
### 定义
### 特点
### 应用场景

## 关键公式/定理
### 公式1
### 公式2

## 算法流程
### 步骤1
### 步骤2

## 相关概念
### 概念A
### 概念B

## 常见问题
### 问题1
### 问题2

要求：
1. 层级清晰，一般3-4层
2. 覆盖该知识点的完整体系
3. 重点突出核心概念
4. 适合用Markmap等工具渲染
"""

        result = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="你擅长将复杂知识体系整理成清晰的思维导图结构。"
        )
        content = result["content"] or self._fallback_content(input_data["topic"])
        return {"mindmap_content": content}

    # ============= real 模式 =============

    async def _real_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""为「{input_data['topic']}」生成 Markdown 格式的思维导图（3-4 层缩进）。

格式：
# {input_data['topic']}

## 核心概念
### 定义
### 特点

## 关键公式/定理
### 公式1

## 算法流程
### 步骤1

## 相关概念

## 常见问题

直接输出 Markdown 思维导图，不要其他解释。
"""

        logger.info(
            f"[mindmap_agent] 调用 real LLM: provider={self.llm.provider.value}, "
            f"timeout={self.llm.timeout_sec}s"
        )

        response_text = await self.llm.generate(
            prompt=prompt,
            temperature=0.3,
            max_tokens=2000,
            timeout=self.llm.timeout_sec,
        )
        content = self.llm._validate_response(response_text)
        return {"mindmap_content": content}

    # ============= 工具 =============

    def _fallback_content(self, topic: str) -> str:
        return (
            f"# {topic}\n\n"
            f"## 核心概念\n### 定义\n### 特点\n\n"
            f"## 关键公式\n### 公式1\n\n"
            f"## 应用场景\n### 案例1\n\n"
            f"## 常见问题\n### 问题1\n"
        )
