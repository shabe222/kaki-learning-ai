from pydantic import BaseModel, Field, validator
from typing import List, Literal, Optional
from urllib.parse import urlparse

# 单条消息与历史的硬上限。这两个字段以前完全没有边界，
# 而它们直接决定上游付费模型要处理多少 token。
MAX_MESSAGE_CHARS = 20_000
MAX_HISTORY_MESSAGES = 60
MAX_HISTORY_TOTAL_CHARS = 120_000


class ChatMessage(BaseModel):
    role: str = Field(max_length=32)
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class SourceContext(BaseModel):
    material_ids: List[str] = Field(default_factory=list)
    scope: Optional[str] = Field(default=None, max_length=200)
    policy: Literal["auto", "only", "exclude"] = "auto"

    @validator("material_ids")
    def validate_workspace_material_ids(cls, value):
        if len(value) > 12:
            raise ValueError("source_context supports at most 12 materials")
        if any(not isinstance(item, str) or not item.startswith("material_") or len(item) > 64 for item in value):
            raise ValueError("source_context material_ids must be stable material IDs")
        return value


class AttachmentReference(BaseModel):
    material_id: str
    file_name: Optional[str] = Field(default=None, max_length=255)
    type: Optional[str] = Field(default=None, max_length=20)

    @validator("material_id")
    def validate_material_id(cls, value):
        if not value.startswith("material_") or len(value) > 64:
            raise ValueError("attachment material_id must be a stable material ID")
        return value


class SelectionContext(BaseModel):
    selected_text: str = Field(min_length=1, max_length=1200)
    range_label: Optional[str] = Field(default=None, max_length=120)
    purpose: Optional[str] = Field(default="解释选中内容", max_length=80)
    version_digest: Optional[str] = Field(default=None, min_length=64, max_length=71)
    block_id: Optional[str] = Field(default=None, min_length=3, max_length=100)
    text_range: Optional[dict] = None
    timestamp: Optional[dict] = None
    knowledge_refs: List[str] = Field(default_factory=list, max_length=100)
    source_refs: List[str] = Field(default_factory=list, max_length=100)
    artifact_purpose: Optional[dict] = None

    @validator("selected_text")
    def validate_selected_text(cls, value):
        normalized = value.replace("\x00", "").strip()
        if not normalized:
            raise ValueError("selection selected_text must not be blank")
        return normalized

    @validator("version_digest")
    def validate_version_digest(cls, value):
        if value is None:
            return None
        normalized = value.removeprefix("sha256:").lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("selection version_digest must be sha256")
        return "sha256:" + normalized


class RuntimeLLMConfig(BaseModel):
    provider: Literal["deepseek", "openai_compatible"] = "deepseek"
    model: str = Field(default="deepseek-v4.1-flash", min_length=1, max_length=100)
    base_url: Optional[str] = Field(default=None, max_length=300)
    api_key: Optional[str] = Field(default=None, max_length=500)

    @validator("base_url")
    def validate_base_url(cls, value):
        if not value: return None
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("base_url must be an HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("remote model endpoints must use HTTPS")
        return value.rstrip("/")


class ChatRequest(BaseModel):
    message: str = Field(max_length=MAX_MESSAGE_CHARS)
    history: List[ChatMessage] = []
    thread_id: str = "default"
    user_id: Optional[int] = None
    intent: Optional[str] = None                       # learn/practice/ask_tutor/view_path/search
    material_ids: Optional[List[int]] = None           # 上传资料ID列表（practice模式基于资料出题）
    question_count: Optional[int] = Field(default=5, ge=1, le=20)  # 出题数量（1-20）
    create_exam: Optional[bool] = False                # 是否创建真实考试
    # —— 17.docx 答疑模式：默认自然聊天 chat + 简短 concise；仅在用户明确要求时才切到 ask_tutor/detailed ——
    mode: Optional[str] = None                         # chat / ask_tutor / generate_video / generate_ppt / generate_exam / summarize_material / create_learning_plan
    style: Optional[str] = None                        # concise / normal / detailed（仅对 chat / ask_tutor 生效）
    previous_tutor_response: Optional[str] = None      # 追问时上一轮回答（前端 useModuleActions 透传）
    question_context: Optional[dict] = None            # 错题/蒙对题/收藏题上下文
    source_context: Optional[SourceContext] = None      # 当前会话上传资料候选及范围
    attachment_refs: List[AttachmentReference] = Field(default_factory=list)
    reply_to_message_id: Optional[str] = Field(default=None, max_length=100)
    target_artifact_id: Optional[int] = Field(default=None, ge=1)
    target_version_id: Optional[int] = Field(default=None, ge=1)
    selection_context: Optional[SelectionContext] = None
    retry: bool = False                                # 同一 message_id 的显式重试
    message_id: Optional[str] = None                   # 服务端发送锁与幂等键
    runtime_llm_config: Optional[RuntimeLLMConfig] = None  # 当前页面会话配置；密钥不落库
    experience: Optional[Literal["tide_v1"]] = None       # Tide Phase 1C 可选交互契约
    # 这一轮的回答会被朗读出来（语音通话）。默认 False，打字对话一个字都不变。
    #
    # 以前通话走的是和打字完全相同的请求，模型不知道自己说的话是要被听的：
    # 它按「正常长度」写，前端再用 speakableText() 截到 600 字去念——截断发生在
    # 生成之后，模型没有机会为「被听」而写短。2026-09-22 实测：整份 system prompt
    # 里与「说多少、怎么说」有关的只有【回答风格】那 34 个字。
    speech: bool = False

    @validator("intent")
    def validate_intent(cls, v):
        if v is not None:
            valid = {"learn", "practice", "ask_tutor", "view_path", "search"}
            if v not in valid:
                raise ValueError(f"intent must be one of {valid}, got: {v}")
        return v

    @validator("history")
    def validate_history(cls, v):
        """历史直接进上游 prompt，必须封顶，否则单次请求就能推任意多 token。"""
        if len(v) > MAX_HISTORY_MESSAGES:
            # 保留最近的部分：早期轮次对当前回答的价值最低。
            v = v[-MAX_HISTORY_MESSAGES:]
        total = sum(len(item.content) for item in v)
        while v and total > MAX_HISTORY_TOTAL_CHARS:
            total -= len(v[0].content)
            v = v[1:]
        return v

    @validator("mode")
    def validate_mode(cls, v):
        if v is None or v == "":
            return None
        valid = {"chat", "ask_tutor", "generate_video", "generate_ppt",
                 "generate_exam", "summarize_material", "create_learning_plan"}
        if v not in valid:
            # 不抛错：未知 mode 兜底为 None（不影响主流程）
            return None
        return v

    @validator("style")
    def validate_style(cls, v):
        if v is None or v == "":
            return None
        valid = {"concise", "normal", "detailed"}
        if v not in valid:
            return None
        return v

    @validator("material_ids")
    def validate_material_ids(cls, v):
        if v is not None:
            if not isinstance(v, list):
                raise ValueError("material_ids must be a list")
            if len(v) == 0:
                return None  # 空列表视为 None
            if any(not isinstance(x, int) or x <= 0 for x in v):
                raise ValueError("material_ids must be positive integers")
        return v
