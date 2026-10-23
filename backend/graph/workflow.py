"""
LangGraph 多智能体学习资源生成工作流（并行加速版）

核心改动（Day 1-2 修复）：
1. **方案 C — 节点并行化**：build_profile 后，新增 parallel_resource_generation 节点
   内部用 asyncio.gather 并行调用 doc/question/mindmap 三个 agent，节省 2/3 LLM 串行时间
2. **mock 模式跳过 video**：openmaic/seedance 在 mock 模式无意义，直接走 summary_node
3. **route_by_intent 简化**：learn/practice/ask_tutor 三个分支都走 build_profile，合并为 1 个
4. **修复 chat_history 可变默认参数**：改为 None
5. **修复节点名与 state key 重名**：保留 "summary_node" 命名
"""
import asyncio
import logging
from typing import Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from graph.state import AgentState
from agents.profile_agent import ProfileAgent
from agents.document_agent import DocumentAgent
from agents.question_agent import QuestionAgent
from agents.mindmap_agent import MindmapAgent
from agents.code_agent import CodeAgent
from agents.path_agent import PathAgent
from agents.tutor_agent import TutorAgent
from agents.video_agent import VideoAgent
from config import settings

logger = logging.getLogger(__name__)


# ===== 流程模式常量 =====
MODE_SKIP_VIDEO_IN_MOCK = settings.MODE == "mock"


def _should_skip_video(state: AgentState) -> str:
    """mock 模式跳过视频生成节点（节省 5-10s）"""
    if MODE_SKIP_VIDEO_IN_MOCK or not settings.OPENMAIC_ENABLED:
        return "summary_node"
    return "generate_video"


# ===== 流程图构建 =====

def create_learning_graph():
    """
    创建多智能体学习资源生成工作流

    三种模式（由 user_intent 决定入口分支）：
    - learn/practice/ask_tutor: build_profile → parallel → code → path → (skip video) → summary
    - view_path: 直接 plan_path → summary

    节点顺序（修复后）：
    1. analyze_intent   (意图路由)
    2. build_profile    (画像构建)
    3. parallel_resources (doc+question+mindmap 并行生成 — 核心加速)
    4. generate_code    (代码案例)
    5. plan_path        (学习路径)
    6. generate_video   (可选，mock 跳过)
    7. summary_node     (汇总)
    """
    workflow = StateGraph(AgentState)

    # ===== 添加节点 =====
    workflow.add_node("analyze_intent", intent_router_node)
    workflow.add_node("build_profile", ProfileAgent())
    workflow.add_node("parallel_resources", parallel_resource_generation_node)
    workflow.add_node("generate_code", CodeAgent())
    workflow.add_node("plan_path", PathAgent())
    workflow.add_node("tutor_answer", TutorAgent())
    workflow.add_node("generate_video", VideoAgent())
    # 修复：LangGraph 0.2 拒绝节点名与 state key 重名（原名 final_summary 与 AgentState.final_summary 冲突）
    workflow.add_node("summary_node", summary_generator_node)

    # ===== 设置入口 =====
    workflow.set_entry_point("analyze_intent")

    # ===== 条件路由（修复 H2：三个分支合并）=====
    def route_by_intent(state: AgentState) -> str:
        """根据用户意图决定走哪个分支（learn/practice/ask_tutor 共享 build_profile）"""
        intent = state.get("user_intent", "learn")
        logger.info(f"[Router] 用户意图: {intent}")

        if intent == "view_path":
            return "plan_path"
        if intent == "ask_tutor":
            return "tutor_answer"
        # learn / practice / 其它 统一从画像开始
        return "build_profile"

    workflow.add_conditional_edges(
        "analyze_intent",
        route_by_intent,
        {
            "build_profile": "build_profile",
            "plan_path": "plan_path",
            "tutor_answer": "tutor_answer",
        }
    )

    # ===== 主流程边 =====
    # 画像后：并行生成 3 类资源
    workflow.add_edge("build_profile", "parallel_resources")
    # 并行节点后：代码 → 路径
    workflow.add_edge("parallel_resources", "generate_code")
    workflow.add_edge("generate_code", "plan_path")
    # 路径后：mock 模式跳过 video，否则调 video agent
    workflow.add_conditional_edges(
        "plan_path",
        _should_skip_video,
        {
            "summary_node": "summary_node",
            "generate_video": "generate_video",
        }
    )
    # video 后再汇总
    workflow.add_edge("generate_video", "summary_node")
    workflow.add_edge("tutor_answer", "summary_node")
    workflow.add_edge("summary_node", END)

    # ===== 编译图 =====
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)
    logger.info(f"[Workflow] 图编译完成（并行加速模式, mock_skip_video={MODE_SKIP_VIDEO_IN_MOCK}）")
    return app


# ===== 辅助节点函数 =====

async def intent_router_node(state: AgentState) -> dict:
    """意图识别节点 - 如果已有显式意图则跳过LLM判断"""
    # 显式意图优先（跳过LLM调用）
    explicit = state.get("user_intent", "")
    if explicit and explicit in ("learn", "practice", "ask_tutor", "view_path", "search"):
        logger.info(f"[Router] 使用显式意图: {explicit}")
        return {"user_intent": explicit}

    from services.llm_service import get_llm_service

    llm = await get_llm_service()

    # 获取最近10条历史消息作为上下文
    messages = state.get('messages', [])
    recent_context = messages[-10:] if messages else []
    context_str = "\n".join(
        [f"{m['role']}: {str(m.get('content', ''))[:200]}" for m in recent_context]
    ) if recent_context else "无历史对话"

    prompt = f"""分析用户的意图，返回以下之一：
- learn: 想学习新知识/新概念
- practice: 想做练习题/测试
- ask_tutor: 有具体问题需要解答
- view_path: 想查看/修改学习计划

对话历史上下文：
{context_str}

用户最新输入：{state.get('user_query', '')}

只返回意图关键词，不要解释。"""

    try:
        result = await llm.chat([{"role": "user", "content": prompt}])
        intent = result["content"].strip().lower()
    except Exception as e:
        # 真实 LLM 不可用（aiohttp 缺失 / API key 错 / 网络问题）→ 降级规则
        logger.warning(f"[intent_router] LLM 不可用，降级为规则意图: {e}")
        intent = _fallback_intent(state.get('user_query', ''))

    valid_intents = ["learn", "practice", "ask_tutor", "view_path"]
    if intent not in valid_intents:
        intent = "learn"

    return {"user_intent": intent}


def _fallback_intent(user_query: str) -> str:
    """无 LLM 时的规则降级"""
    q = (user_query or "").lower()
    if any(k in q for k in ["练习", "做题", "测试", "考试", "练习题", "题"]):
        return "practice"
    if any(k in q for k in ["问", "为什么", "怎么", "什么", "吗", "？"]):
        return "ask_tutor"
    if any(k in q for k in ["路径", "计划", "进度", "周", "月"]):
        return "view_path"
    return "learn"


async def parallel_resource_generation_node(state: AgentState) -> dict:
    """
    并行生成文档 / 题目 / 思维导图（方案 C — 核心加速节点）

    用 asyncio.gather 同时调用 3 个 agent，节省 2/3 LLM 串行时间。
    mock 模式下从 ~15s 降到 ~5s；production 模式从 ~30-60s 降到 ~10-20s。
    """
    logger.info("[Parallel] 启动 doc/question/mindmap 三路并行")

    doc_agent = DocumentAgent()
    question_agent = QuestionAgent()
    mindmap_agent = MindmapAgent()

    # 构造各 agent 的输入（与单独节点时保持一致）
    doc_content = ""
    doc = state.get("document")
    if isinstance(doc, dict) and doc.get("content"):
        doc_content = doc["content"]
    shared_input = {
        "user_query": state.get("user_query", ""),
        "topic": state.get("user_query", ""),
        "profile": state.get("student_profile"),
        "student_profile": state.get("student_profile"),
        "messages": state.get("messages", []),
        "user_id": int(state.get("user_id") or 0),
        "grade": state.get("grade", "") or state.get("grade_level", ""),  # 兼容两种命名
        "target_role": state.get("target_role", ""),                       # 下游 agent 读取的目标职业
        "intent": state.get("user_intent", "learn"),
        "document_content": doc_content,
        "question_count": state.get("question_count", 5),
        "material_context": state.get("material_context", ""),
    }

    async def _safe_call(agent, key: str):
        """包装单个 agent 调用，失败时记录完整堆栈 + 标记错误字段，不阻塞其它分支"""
        try:
            return await agent._process(shared_input)
        except Exception as e:
            logger.exception(f"[{agent.__class__.__name__}] 调用失败: {e}")
            return {"_error": str(e), "_agent": agent.__class__.__name__, "_key": key}

    # 关键：asyncio.gather 并发执行
    doc_result, question_result, mindmap_result = await asyncio.gather(
        _safe_call(doc_agent, "document"),
        _safe_call(question_agent, "question"),
        _safe_call(mindmap_agent, "mindmap"),
    )

    # 合并到 state：保留 agent 自身的 _build_result 包装
    # 防御：_safe_call 失败时返回的错误 dict 里有 _error 标记，必须跳过 _build_result
    # （question_agent/mindmap_agent 会对 output_data["xxx"] 取值，空 dict 会 KeyError）
    merged = {}
    if doc_result and "_error" not in doc_result:
        merged.update(doc_agent._build_result(doc_result))
    elif doc_result and "_error" in doc_result:
        logger.warning(f"[Parallel] document agent 失败，跳过合并: {doc_result.get('_error')}")
    if question_result and "_error" not in question_result:
        merged.update(question_agent._build_result(question_result))
    elif question_result and "_error" in question_result:
        logger.warning(f"[Parallel] question agent 失败，跳过合并: {question_result.get('_error')}")
    if mindmap_result and "_error" not in mindmap_result:
        merged.update(mindmap_agent._build_result(mindmap_result))
    elif mindmap_result and "_error" in mindmap_result:
        logger.warning(f"[Parallel] mindmap agent 失败，跳过合并: {mindmap_result.get('_error')}")

    merged["current_agent"] = "parallel_resources"
    logger.info(f"[Parallel] 完成，产出 keys: {list(merged.keys())}")
    return merged


async def summary_generator_node(state: AgentState) -> dict:
    """最终总结节点 - 整合所有Agent的产出"""

    summary_parts = []

    if state.get("document"):
        summary_parts.append(f"📄 已生成讲解文档《{state['document']['title']}》")

    if state.get("questions"):
        summary_parts.append(f"✏️ 已生成{len(state['questions'])}道练习题")

    if state.get("mindmap"):
        summary_parts.append("🗺️ 已生成思维导图")

    if state.get("code_examples"):
        summary_parts.append(f"💻 已生成{len(state['code_examples'])}个代码案例")

    if state.get("learning_path"):
        summary_parts.append(f"🛤️ 已制定{len(state['learning_path'])}步学习路径")

    if state.get("tutor_response"):
        summary_parts.append("🎓 智能辅导回复已准备")

    final_text = "\n".join(summary_parts) if summary_parts else "✅ 处理完成"

    return {
        "final_summary": final_text,
        "current_agent": "summary"
    }


# ===== 工作流执行器 =====

async def execute_workflow(
    user_query: str,
    chat_history: Optional[list] = None,
    thread_id: str = "default",
    existing_profile: Optional[dict] = None,
    explicit_intent: Optional[str] = None,
    material_ids: Optional[list] = None,
    question_count: int = 5,
    material_context: str = "",
):
    """
    执行多Agent工作流（主入口）

    Args:
        user_query: 用户输入
        chat_history: 对话历史 [{"role": "user/content", ...}]
        thread_id: 会话ID（用于状态恢复）
        existing_profile: 已有的学生画像（用于多轮对话中优化而非重建）
        explicit_intent: 显式意图（跳过LLM意图识别）
        material_ids: 资料ID列表（practice模式基于资料出题）
        question_count: 出题数量
        material_context: 资料内容上下文（已拼接好的文本）

    Yields:
        每个节点完成时的 event 字典 {node_name: node_output}
    """
    if chat_history is None:
        chat_history = []

    graph = create_learning_graph()

    initial_state: AgentState = {
        "messages": chat_history,
        "user_query": user_query,
        "user_intent": explicit_intent or "",
        "user_id": None,
        "material_ids": material_ids,
        "question_count": question_count,
        "student_profile": existing_profile,  # 注入已有画像 → 优化而非重建
        "profile_confidence": 0.0,
        "document": None,
        "mindmap": None,
        "questions": None,
        "code_examples": None,
        "reading_materials": None,
        "video_result": None,
        "video_skipped": False,
        "learning_path": None,
        "tutor_response": None,
        "final_summary": None,
        "current_agent": "",
        "execution_log": [],
        "errors": [],
        "processing_time": {},
        "total_tokens_used": 0
    }

    config = {"configurable": {"thread_id": thread_id}}

    final_state = None
    async for event in graph.astream(initial_state, config=config):
        node_name = list(event.keys())[0]
        logger.info(f"[Workflow] 节点 '{node_name}' 完成")
        yield event
        final_state = event
