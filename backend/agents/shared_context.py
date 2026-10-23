"""
8 Agent 共享上下文池 - 让 agent 之间的数据传递像查字典一样简单

设计目标：
1. 任何 agent 都能读取上游所有产出（profile / document / questions / mindmap / code / path / video）
2. 任何 agent 写入的产出都会被自动持久化到 DB + 推送给下游
3. 与 LangGraph state 兼容，但提供 Pythonic 的 getter/setter API
4. 强类型 (Pydantic BaseModel)，IDE 可补全，类型检查有效

用法：
    # 子 agent 内：
    profile = self.ctx.get('student_profile')         # 读
    self.ctx.set('document', {'title': '...'})        # 写
    snapshot = self.ctx.snapshot()                    # 导出给 LangGraph

    # router 中：
    ctx = SharedContext(user_id=uid, thread_id=tid)
    ctx.set('profile', await load_profile(uid))
    agent = QuestionAgent(ctx=ctx)
    await agent({'user_query': '生成 5 道题', 'topic': '线性代数'}, ctx=ctx)
"""
from typing import Any, Optional, Dict, List
from pydantic import BaseModel, Field, validator


class SharedContext(BaseModel):
    """
    8 Agent 共享上下文 - 强类型，可序列化
    字段命名严格对齐 graph/state.py:AgentState，保证 LangGraph 合并无歧义
    """
    # === 基础 ===
    user_id: int
    thread_id: str = "default"

    # === 画像层（Profile Agent 写，其他 Agent 读）===
    student_profile: Optional[Dict[str, Any]] = None
    profile_confidence: float = 0.0

    # === 资源层（每个 Agent 写自己的字段，其他 Agent 读）===
    document: Optional[Dict[str, Any]] = None
    mindmap: Optional[Dict[str, Any]] = None
    questions: Optional[List[Dict[str, Any]]] = None
    code_examples: Optional[List[Dict[str, Any]]] = None
    learning_path: Optional[List[Dict[str, Any]]] = None
    video_result: Optional[Dict[str, Any]] = None
    tutor_response: Optional[str] = None

    # === 元数据 ===
    user_query: str = ""
    user_intent: str = "learn"
    errors: List[str] = Field(default_factory=list)
    execution_log: List[Dict[str, Any]] = Field(default_factory=list)
    processing_time: Dict[str, float] = Field(default_factory=dict)
    current_agent: str = ""

    # === 字段白名单（get/set 安全校验用）===
    _WRITABLE_FIELDS = frozenset({
        "student_profile", "profile_confidence",
        "document", "mindmap", "questions", "code_examples",
        "learning_path", "video_result", "tutor_response",
        "user_query", "user_intent",
        "errors", "execution_log", "processing_time", "current_agent",
    })

    # ===== Pythonic 读写 API =====
    def get(self, key: str, default: Any = None) -> Any:
        """读 - profile_agent.get('document')"""
        if not hasattr(self, key):
            return default
        val = getattr(self, key, default)
        return val if val is not None else default

    def set(self, key: str, value: Any) -> None:
        """写 - profile_agent.set('document', {...})"""
        if key not in self._WRITABLE_FIELDS:
            raise ValueError(
                f"SharedContext 拒绝写未知字段 '{key}'。"
                f"可写字段: {sorted(self._WRITABLE_FIELDS)}"
            )
        setattr(self, key, value)

    def has(self, key: str) -> bool:
        """判断字段是否有非空值"""
        if not hasattr(self, key):
            return False
        val = getattr(self, key)
        if val is None:
            return False
        if isinstance(val, (list, dict, str)) and len(val) == 0:
            return False
        return True

    # ===== 序列化 =====
    def snapshot(self) -> Dict[str, Any]:
        """返回当前所有非空字段 - 用于持久化或推送给 LangGraph"""
        snap = {}
        for f in self._WRITABLE_FIELDS:
            val = getattr(self, f, None)
            if val is not None and val != [] and val != "" and val != {}:
                snap[f] = val
        # 基础字段
        snap["user_id"] = self.user_id
        snap["thread_id"] = self.thread_id
        return snap

    def to_agent_state(self) -> Dict[str, Any]:
        """转成 LangGraph AgentState 格式（graph/state.py 的 TypedDict 期望）"""
        return {
            "messages": [],
            "user_query": self.user_query,
            "user_intent": self.user_intent,
            "student_profile": self.student_profile,
            "profile_confidence": self.profile_confidence,
            "document": self.document,
            "mindmap": self.mindmap,
            "questions": self.questions,
            "code_examples": self.code_examples,
            "reading_materials": None,
            "video_result": self.video_result,
            "video_skipped": self.video_result is None,
            "learning_path": self.learning_path,
            "tutor_response": self.tutor_response,
            "final_summary": None,
            "current_agent": self.current_agent,
            "execution_log": self.execution_log,
            "errors": self.errors,
            "processing_time": self.processing_time,
            "total_tokens_used": 0,
        }

    @classmethod
    def from_agent_state(cls, state: Dict[str, Any], user_id: int) -> "SharedContext":
        """从 LangGraph AgentState 构造 - 单一入口"""
        return cls(
            user_id=user_id,
            thread_id=state.get("thread_id", "default"),
            user_query=state.get("user_query", ""),
            user_intent=state.get("user_intent", "learn"),
            student_profile=state.get("student_profile"),
            profile_confidence=state.get("profile_confidence", 0.0),
            document=state.get("document"),
            mindmap=state.get("mindmap"),
            questions=state.get("questions"),
            code_examples=state.get("code_examples"),
            learning_path=state.get("learning_path"),
            video_result=state.get("video_result"),
            tutor_response=state.get("tutor_response"),
            errors=state.get("errors", []) or [],
            execution_log=state.get("execution_log", []) or [],
            processing_time=state.get("processing_time", {}) or {},
            current_agent=state.get("current_agent", ""),
        )

    # ===== Agent 间显式依赖声明 =====
    @staticmethod
    def dependencies(agent_name: str) -> List[str]:
        """返回该 agent 依赖的上游字段（用于静态分析和 UI 提示）"""
        return {
            "profile":  ["messages", "user_query"],
            "document": ["user_query", "student_profile"],
            "question": ["user_query", "student_profile"],
            "mindmap":  ["user_query", "student_profile"],
            "code":     ["user_query", "student_profile"],
            "path":     ["student_profile", "document", "questions"],
            "tutor":    ["messages", "student_profile", "document", "questions"],
            "video":    ["user_query", "student_profile"],
        }.get(agent_name, [])

    @staticmethod
    def produces(agent_name: str) -> str:
        """返回该 agent 产出的字段名"""
        return {
            "profile":  "student_profile",
            "document": "document",
            "question": "questions",
            "mindmap":  "mindmap",
            "code":     "code_examples",
            "path":     "learning_path",
            "tutor":    "tutor_response",
            "video":    "video_result",
        }.get(agent_name, "")


# ===== Agent 注册表（router 用）=====
AGENT_REGISTRY: Dict[str, type] = {}


def register_agent(short_name: str, agent_cls: type):
    """注册 agent - 在各 agent 文件末尾调用"""
    AGENT_REGISTRY[short_name] = agent_cls


# 字段名 → short_name 映射（用于持久化）
AGENT_WRITES: Dict[str, str] = {
    "student_profile": "profile",
    "document": "document",
    "questions": "question",
    "mindmap": "mindmap",
    "code_examples": "code",
    "learning_path": "path",
    "tutor_response": "tutor",
    "video_result": "video",
}
