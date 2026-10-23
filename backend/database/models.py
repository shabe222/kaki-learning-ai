from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime,
    ForeignKey, Boolean, JSON, UniqueConstraint, Index, Date, func
)
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime


class Base(DeclarativeBase):
    pass


# ===== 用户表 =====

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    email = Column(String(120), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # 关系
    profile = relationship("StudentProfile", back_populates="user", uselist=False)
    resources = relationship("LearningResource", back_populates="user")
    progress = relationship("LearningProgress", back_populates="user")
    answer_records = relationship("AnswerRecord", back_populates="user")
    chat_sessions = relationship("ChatSession", back_populates="user")
    memories = relationship("UserMemory", back_populates="user")
    ai_impressions = relationship("AIImpression", back_populates="user")


# ===== 管理员表 =====

class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(100), nullable=True)
    role = Column(String(30), nullable=False, default="super_admin", index=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    last_login_at = Column(DateTime, nullable=True)


# ===== 学生画像表（6大类 25+维度，聚焦机器学习）=====

class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # ========== 一、基础档案 (Identity) ==========
    major = Column(String(100), nullable=True, comment="专业")
    grade_level = Column(String(20), nullable=True, comment="年级（大一/大二/大三/大四/研/已工作）")
    target_role = Column(String(100), nullable=True, comment="目标职业（算法工程师/数据科学家/科研/转行）")
    prior_courses = Column(JSON, nullable=True, comment="已修课程列表 [课程名]")
    preferred_language = Column(String(30), nullable=True, comment="偏好编程语言")

    # ========== 二、技能热力图 (Skill Matrix) — ML专属细粒度评估 ==========
    skill_matrix = Column(JSON, nullable=True, comment="""
    {
      "math": {
        "linear_algebra": 1-5, "probability": 1-5,
        "calculus": 1-5, "statistics": 1-5, "optimization": 1-5
      },
      "programming": {
        "python": 1-5, "numpy": 1-5, "pandas": 1-5,
        "data_structure": 1-5, "algorithm": 1-5, "sql": 1-5
      },
      "machine_learning": {
        "supervised": 1-5, "unsupervised": 1-5,
        "neural_networks": 1-5, "deep_learning": 1-5,
        "nlp": 1-5, "cv": 1-5, "reinforcement": 1-5,
        "feature_engineering": 1-5, "model_evaluation": 1-5,
        "mlops": 1-5, "ensemble_methods": 1-5
      },
      "tools": {
        "sklearn": 1-5, "pytorch": 1-5, "tensorflow": 1-5,
        "jupyter": 1-5, "git": 1-5, "docker": 1-5,
        "matplotlib": 1-5, "mlflow": 1-5
      }
    }""")

    # ========== 三、认知画像 (Cognitive Profile) — 决定怎么教 ==========
    cognitive_profile = Column(JSON, nullable=True, comment="""
    {
      "bloom_level": "remember/understand/apply/analyze/evaluate/create",
      "learning_style": "visual/auditory/reading/kinesthetic",
      "abstraction_ability": 1-5,
      "mathematical_maturity": 1-5,
      "question_depth": "surface/medium/deep",
      "learning_pace": "slow/medium/fast",
      "prefers_theory_first": true/false,
      "needs_concrete_examples": true/false,
      "attention_to_detail": 1-5
    }""")

    # ========== 四、易错档案 (Misconception Archive) — 决定练什么 ==========
    misconception_archive = Column(JSON, nullable=True, comment="""
    {
      "concept_misunderstandings": [
        {"concept": "过拟合", "error": "认为模型越复杂越好", "frequency": 3}
      ],
      "math_blind_spots": ["矩阵求导", "贝叶斯公式"],
      "coding_bad_habits": ["不划分验证集", "特征未归一化"],
      "high_error_topics": ["梯度消失", "正则化"],
      "exam_mistake_types": [
        {"type": "推导题", "error_rate": 0.6},
        {"type": "调参实践", "error_rate": 0.3}
      ]
    }""")

    # ========== 五、学习行为 (Behavior Pattern) — 决定怎么推 ==========
    behavior_pattern = Column(JSON, nullable=True, comment="""
    {
      "completion_rate": 0.0-1.0,
      "avg_session_minutes": 数字,
      "active_time_of_day": "morning/afternoon/evening/night",
      "content_format_preference": ["document","video","code","interactive"],
      "exercise_type_preference": "multiple_choice/coding/project/open_ended",
      "hint_usage_rate": 0.0-1.0,
      "review_frequency": 0.0-1.0,
      "drop_off_topics": ["变分推断"],
      "persistence_score": 1-5
    }""")

    # ========== 六、动机画像 (Motivation Engine) — 决定推什么方向 ==========
    motivation_profile = Column(JSON, nullable=True, comment="""
    {
      "primary_motivation": "job_hunting/research/career_change/curiosity/exam",
      "urgency_level": 1-5,
      "self_efficacy": 1-5,
      "topic_passion": [
        {"topic": "GAN", "interest": 0.9},
        {"topic": "推荐系统", "interest": 0.7}
      ],
      "career_target": "算法工程师",
      "burnout_risk": "low/medium/high",
      "preferred_reward": "achievement/progress/social"
    }""")

    # ========== 七、能力矩阵（来自向导评估）==========
    # 格式: {chapter: {kp: {score, confidence, notes}}}
    # 7章 28个知识点，覆盖完整机器学习知识图谱
    capability_matrix = Column(JSON, nullable=True, comment="向导评估能力矩阵")

    overall_score = Column(Float, nullable=True, comment="综合得分 (百分制)")

    # ========== 画像引擎字段（v5.4 智能混合画像引擎）==========
    profile_data = Column(JSON, nullable=True, comment="灵活画像JSON（规则引擎+AI评估动态字段）")

    # ========== 八、向导评估结果（完整记录）==========
    profile_detail = Column(JSON, nullable=True, comment="""
    完整画像详情：
    {
      "basic_info": {major, degree, grade, school, preferred_language},
      "learning_goals": {target_role, deadline, motivation, weekly_hours},
      "prior_experience": {courses_taken, projects, self_assessment},
      "uploaded_document": {format, char_count, extracted_info},
      "quiz_result": {total_questions, correct_count, completion_rate},
      "advanced_interest": {nlp, cv, reinforcement_learning, mlops}
    }""")

    # ========== 元数据 ==========
    confidence_score = Column(Float, nullable=True, comment="画像整体置信度 (0-1)")
    profile_version = Column(Integer, default=1, comment="画像版本号（每次更新+1）")
    last_updated_from = Column(String(50), nullable=True, comment="最近一次更新来源（对话ID）")

    # 时间戳
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="profile")


# ===== 学习资源表 =====

class LearningResource(Base):
    __tablename__ = "learning_resources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String(100), nullable=True, comment="会话ID")

    # 资源类型: document/questions/mindmap/code_examples/learning_path
    resource_type = Column(String(30), nullable=False)

    # 内容
    title = Column(String(300), nullable=True)
    content = Column(JSON, nullable=True, comment="资源内容（JSON格式）")
    raw_text = Column(Text, nullable=True, comment="原始文本")

    # 元数据
    topic = Column(String(200), nullable=True, comment="学习主题", index=True)
    difficulty = Column(String(20), nullable=True, comment="初级/中级/高级")
    tokens_used = Column(Integer, default=0, comment="生成消耗的tokens")

    created_at = Column(DateTime, server_default=func.now(), index=True)

    user = relationship("User", back_populates="resources")


# ===== 学习进度表 =====

class LearningProgress(Base):
    __tablename__ = "learning_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # 进度信息
    stage_name = Column(String(100), nullable=False, comment="阶段名称")
    stage_index = Column(Integer, nullable=False, comment="阶段序号")
    progress_pct = Column(Float, default=0.0, comment="完成百分比")
    status = Column(String(20), default="pending", comment="pending/in_progress/completed")

    # 关联数据
    completed_exercises = Column(Integer, default=0)
    total_exercises = Column(Integer, default=0)
    time_spent_minutes = Column(Float, default=0.0)

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="progress")


# ===== 对话会话表 =====

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True, comment="LangGraph线程ID")

    # 对话内容
    messages = Column(JSON, nullable=True, comment="完整对话记录 [{role, content}]")
    message_count = Column(Integer, default=0)
    response_style = Column(String(20), nullable=True)
    mode = Column(String(20), nullable=True, comment="assistant/tutor")
    active_material_id = Column(String(64), nullable=True)

    # 会话状态
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    last_message_at = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="chat_sessions")


class TideSharedPerspectiveSnapshot(Base):
    """Immutable, thread-bounded Shared Perspective revision for Tide.

    ``fields`` stores only the current collaborative work context.  It is not a
    user profile or a cross-thread memory surface.  Each field carries its own
    value, epistemic status, source reference, and update timestamp.
    """
    __tablename__ = "tide_shared_perspective_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "thread_id", "revision",
            name="uq_tide_shared_perspective_thread_revision",
        ),
        Index(
            "ix_tide_shared_perspective_thread_latest",
            "user_id", "thread_id", "revision",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    parent_snapshot_id = Column(
        Integer,
        ForeignKey("tide_shared_perspective_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    fields = Column(JSON, nullable=False, default=dict)
    changed_by = Column(String(30), nullable=False, default="candidate")
    created_at = Column(DateTime, server_default=func.now(), index=True)


class TideWorkspaceObject(Base):
    """One living, thread-bounded work target shared by a user and Tide.

    This is coordination state, not an asset or file.  Real outputs continue to
    live in GeneratedItem / GeneratedItemVersion and are referenced by identity.
    """
    __tablename__ = "tide_workspace_objects"
    __table_args__ = (
        Index(
            "ix_tide_workspace_object_current",
            "user_id", "thread_id", "is_current",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    title = Column(String(240), nullable=False)
    purpose = Column(String(500), nullable=False)
    status = Column(String(30), nullable=False, default="exploring")
    related_artifact_references = Column(JSON, nullable=False, default=list)
    related_decisions = Column(JSON, nullable=False, default=list)
    next_action = Column(String(500), nullable=True)
    is_current = Column(Boolean, nullable=False, default=True, index=True)
    is_confirmed = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    source_perspective_revision = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class TideArtifactAction(Base):
    """A user-controlled improvement proposal against one immutable artifact version."""
    __tablename__ = "tide_artifact_actions"
    __table_args__ = (
        UniqueConstraint("request_key_digest", name="uq_tide_artifact_action_request"),
        Index("ix_tide_artifact_action_workspace_created", "workspace_object_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    workspace_object_id = Column(
        Integer, ForeignKey("tide_workspace_objects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    artifact_id = Column(Integer, ForeignKey("generated_items.id", ondelete="CASCADE"), nullable=False, index=True)
    source_version_id = Column(
        Integer, ForeignKey("generated_item_versions.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    target_version_id = Column(
        Integer, ForeignKey("generated_item_versions.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    action_type = Column(String(50), nullable=False)
    reason = Column(String(1000), nullable=False)
    status = Column(String(30), nullable=False, default="analyzing", index=True)
    proposal = Column(JSON, nullable=False, default=dict)
    candidate_payload = Column(JSON, nullable=True)
    request_key_digest = Column(String(64), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WorkspaceMaterial(Base):
    """One user-uploaded source file available to chat and future workspace agents."""
    __tablename__ = "workspace_materials"
    __table_args__ = (UniqueConstraint("material_id", name="uq_workspace_material_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    material_id = Column(String(64), nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(20), nullable=False, index=True)
    mime_type = Column(String(120), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    storage_path = Column(String(600), nullable=False)
    parse_status = Column(String(20), nullable=False, default="parsing", index=True)
    parse_error = Column(String(500), nullable=True)
    page_count = Column(Integer, nullable=True)
    section_count = Column(Integer, nullable=False, default=0)
    char_count = Column(Integer, nullable=False, default=0)
    structure = Column(JSON, nullable=False, default=list)
    parse_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WorkspaceMaterialChunk(Base):
    """A bounded, source-addressable text chunk used for retrieval."""
    __tablename__ = "workspace_material_chunks"
    __table_args__ = (
        UniqueConstraint("material_id", "chunk_index", name="uq_workspace_material_chunk"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    material_id = Column(String(64), ForeignKey("workspace_materials.material_id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    heading = Column(String(300), nullable=True)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    content = Column(Text, nullable=False)
    char_count = Column(Integer, nullable=False)
    source_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now())


class WorkspaceConversationMaterial(Base):
    """Durable current-conversation reference to a library material."""
    __tablename__ = "workspace_conversation_materials"
    __table_args__ = (
        UniqueConstraint("user_id", "thread_id", "material_id", name="uq_workspace_conversation_material"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    material_id = Column(String(64), ForeignKey("workspace_materials.material_id"), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    attached_at = Column(DateTime, server_default=func.now(), index=True)
    last_referenced_at = Column(DateTime, nullable=True)


class TideUserContextClaim(Base):
    """One provenance-backed, thread-bounded claim derived from a user document.

    This table is deliberately separate from ``user_memories``. Confirmed rows
    may be selected for the current Tide thread, but are not durable personal
    memory and are never promoted implicitly.
    """
    __tablename__ = "tide_user_context_claims"
    __table_args__ = (
        Index("ix_tide_user_context_thread_status", "user_id", "thread_id", "status"),
        Index("ix_tide_user_context_material", "material_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    material_id = Column(
        String(64), ForeignKey("workspace_materials.material_id", ondelete="CASCADE"), nullable=False, index=True,
    )
    kind = Column(String(30), nullable=False, index=True)
    value = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="USER_PROVIDED", index=True)
    confidence = Column(Float, nullable=False, default=1.0)
    source_reference = Column(JSON, nullable=False, default=dict)
    scope = Column(String(30), nullable=False, default="thread")
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class DaryaStudyTime(Base):
    """How long someone actually sat with DARYA, one row per day.

    Nothing in this database recorded a learner's *time*: messages carry no
    timestamp, `learning_progress.time_spent_minutes` is never written, and
    `answer_records.time_spent_sec` only covers assessment answers. So presence
    is measured directly -- the browser reports a heartbeat while the tab is
    visible and the person is at the keyboard -- and accumulated here per day.

    This is deliberately not inference from message gaps: one message after a
    four-hour pause is not four hours of study, and a number the learner cannot
    trust is worse than no number.
    """
    __tablename__ = "darya_study_time"
    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_darya_study_time_user_day"),
        Index("ix_darya_study_time_user_day", "user_id", "day"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    day = Column(Date, nullable=False, index=True)
    seconds = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class DaryaLongTermMemory(Base):
    """Compact, provenance-backed personal memory used across Tide threads.

    Conversation transcripts remain in ``chat_sessions``.  This table stores
    only explicitly saved durable meaning.  Superseded and forgotten rows are
    retained as non-serving audit/tombstone records so obsolete values cannot
    silently return from an older source claim.
    """
    __tablename__ = "darya_long_term_memories"
    __table_args__ = (
        Index("ix_darya_memory_user_state", "user_id", "state", "updated_at"),
        Index("ix_darya_memory_user_topic", "user_id", "topic", "state"),
        Index("ix_darya_memory_source_claim", "user_id", "source_claim_id", "state"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    memory_type = Column(String(30), nullable=False, index=True)
    topic = Column(String(120), nullable=False, index=True)
    content = Column(Text, nullable=False)
    provenance = Column(JSON, nullable=False, default=dict)
    epistemic_status = Column(String(40), nullable=False, default="USER_CONFIRMED")
    confidence = Column(Float, nullable=False, default=1.0)
    scope = Column(String(20), nullable=False, default="ACCOUNT", index=True)
    source_context_id = Column(String(100), nullable=True)
    source_claim_id = Column(Integer, ForeignKey("tide_user_context_claims.id", ondelete="SET NULL"), nullable=True)
    source_thread_id = Column(String(100), nullable=False)
    source_message_id = Column(String(100), nullable=True)
    freshness = Column(String(30), nullable=False, default="STABLE")
    revalidate_after_at = Column(DateTime, nullable=True)
    state = Column(String(30), nullable=False, default="ACTIVE", index=True)
    revision = Column(Integer, nullable=False, default=1)
    supersedes_id = Column(Integer, ForeignKey("darya_long_term_memories.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    last_confirmed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)


class DaryaOpenLoop(Base):
    """Timeline continuity: one not-yet-finished commitment, request, or plan.

    Foreground DARYA promises or starts things; the Librarian records them as
    open loops so a later session knows what is still unfinished. Lifecycle is
    explicit: PLANNED / IN_PROGRESS / WAITING / BLOCKED are open; COMPLETED /
    CANCELLED / SUPERSEDED are closed and must not resurface as reminders.
    """

    __tablename__ = "darya_open_loops"
    __table_args__ = (
        Index("ix_darya_open_loops_user_status", "user_id", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    loop_key = Column(String(80), nullable=False, index=True)
    origin = Column(String(30), nullable=False, default="USER_REQUEST")
    statement = Column(String(400), nullable=False)
    status = Column(String(20), nullable=False, default="IN_PROGRESS", index=True)
    detail = Column(JSON, nullable=False, default=dict)
    evidence_span = Column(String(400), nullable=True)
    source_message_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    closed_at = Column(DateTime, nullable=True)



class DaryaCurrentPerspectiveSnapshot(Base):
    """Immutable, thread-scoped Phase 3.0 perspective decision.

    This is replaceable current-work state, not durable personal memory.  The
    JSON contract keeps observations and inferences distinguishable and stores
    only the context selected for one turn.
    """
    __tablename__ = "darya_current_perspective_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "thread_id", "revision",
            name="uq_darya_current_perspective_thread_revision",
        ),
        Index(
            "ix_darya_current_perspective_latest",
            "user_id", "thread_id", "revision",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    parent_snapshot_id = Column(
        Integer,
        ForeignKey("darya_current_perspective_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_message_id = Column(String(100), nullable=False)
    state = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class DaryaLearningLoopState(Base):
    """Thread- and node-scoped observable learner state.

    This is observable learning evidence, not Long-Term Memory and not a broad
    psychological profile.  The formal Learning Evaluator remains authoritative
    for graph-owned assessments; this row only keeps the live learning kernel
    coherent across consecutive Tide conversation turns.
    """
    __tablename__ = "darya_learning_loop_states"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "thread_id", "concept_key",
            name="uq_darya_learning_loop_thread_concept",
        ),
        Index(
            "ix_darya_learning_loop_latest",
            "user_id", "thread_id", "updated_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    concept_key = Column(String(120), nullable=False, index=True)
    mastery_estimate = Column(Float, nullable=False, default=0.2)
    current_misconception = Column(String(500), nullable=True)
    diagnosis_confidence = Column(Float, nullable=False, default=0.0)
    support_level = Column(String(10), nullable=False, default="L2")
    last_observation = Column(Text, nullable=True)
    evidence = Column(JSON, nullable=False, default=list)
    change_history = Column(JSON, nullable=False, default=list)
    latest_cycle = Column(JSON, nullable=False, default=dict)
    cycle_count = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)


class DaryaLearningRouteState(Base):
    """Small thread-scoped adaptive route; separate from per-node evidence."""
    __tablename__ = "darya_learning_route_states"
    __table_args__ = (
        UniqueConstraint("user_id", "thread_id", name="uq_darya_learning_route_thread"),
        Index("ix_darya_learning_route_latest", "user_id", "thread_id", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    domain_id = Column(String(120), nullable=False)
    learner_goal = Column(Text, nullable=False)
    target_node_id = Column(String(120), nullable=False, index=True)
    current_node_id = Column(String(120), nullable=False, index=True)
    latest_plan = Column(JSON, nullable=False, default=dict)
    route_history = Column(JSON, nullable=False, default=list)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)


class CanonicalGameState(Base):
    """User-scoped DARYA story state, isolated from every learning authority."""
    __tablename__ = "canonical_game_states"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_canonical_game_states_user"),
        Index("ix_canonical_game_states_user_revision", "user_id", "revision"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    current_scene = Column(String(32), nullable=False, default="scene01", server_default="scene01")
    story_state = Column(JSON, nullable=False, default=dict)
    presentation_checkpoint = Column(JSON, nullable=False, default=dict)
    revision = Column(Integer, nullable=False, default=1, server_default="1")
    schema_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)


class WorkspaceAgentTask(Base):
    """Server-owned lifecycle for one semantic workspace task."""
    __tablename__ = "workspace_agent_tasks"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_workspace_agent_tasks_task_id"),
        UniqueConstraint("execution_key", name="uq_workspace_agent_tasks_execution_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    agent_type = Column(String(30), nullable=False, index=True)
    state = Column(String(40), nullable=False, index=True)
    slots = Column(JSON, nullable=False, default=dict)
    version = Column(Integer, nullable=False, default=1)
    confirmed_version = Column(Integer, nullable=True)
    execution_key = Column(String(140), nullable=True)
    artifact_id = Column(Integer, nullable=True, index=True)
    error_code = Column(String(80), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class UserMemory(Base):
    __tablename__ = "user_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_memories_user_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    memory_type = Column(String(30), nullable=False)
    key = Column(String(50), nullable=False)
    value = Column(JSON, nullable=False)
    status = Column(String(20), nullable=False, default="confirmed")
    evidence_text = Column(String(200), nullable=True)
    source_thread_id = Column(String(100), nullable=False)
    source_message_id = Column(String(100), nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="memories")


class AIImpression(Base):
    """Revocable, evidence-backed inference kept separate from explicit memory/profile."""
    __tablename__ = "ai_impressions"
    __table_args__ = (
        UniqueConstraint("user_id", "impression_key", name="uq_ai_impressions_user_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    impression_key = Column(String(100), nullable=False)
    dimension = Column(String(50), nullable=False, index=True)
    summary = Column(String(240), nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)
    scope = Column(String(80), nullable=False)
    evidence_count = Column(Integer, nullable=False, default=0)
    evidence_refs = Column(JSON, nullable=False, default=list)
    first_observed_at = Column(DateTime, nullable=False)
    last_observed_at = Column(DateTime, nullable=False)
    status = Column(String(30), nullable=False, default="active")
    user_feedback = Column(JSON, nullable=True)
    source_type = Column(String(30), nullable=False, default="ai_inference")
    confirmed_by_user = Column(Boolean, nullable=False, default=False)
    profile_mapping_key = Column(String(100), nullable=True)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="ai_impressions")


# ===== 学习材料表 =====

class LearningMaterial(Base):
    __tablename__ = "learning_materials"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chapter_id = Column(String(50), nullable=False, index=True)
    stage = Column(String(20), nullable=False, comment="seedling/sprout/practice/advanced/master")
    mastery_at_creation = Column(Float, default=0.0)
    content = Column(JSON, nullable=True, comment="{document: {title, content, sections}, questions: [...], doc_sections: [{id, title, content}]}")
    profile_snapshot = Column(JSON, nullable=True)
    weak_points = Column(JSON, nullable=True, comment="[knowledge_point, ...]")
    is_current = Column(Boolean, default=True)
    review_count = Column(Integer, default=0)
    last_reviewed_at = Column(DateTime, nullable=True)
    next_review_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ===== 掌握度历史表 =====

class MasteryHistory(Base):
    __tablename__ = "mastery_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chapter_id = Column(String(50), nullable=False, index=True)
    mastery = Column(Float, nullable=False)
    source = Column(String(30), nullable=False, comment="quiz/doc/review/initial/generation")
    detail = Column(JSON, nullable=True, comment="{old_mastery, new_mastery, delta, question_id, ...}")
    created_at = Column(DateTime, server_default=func.now())


# ===== 薄弱点追踪表 =====

class WeakPoint(Base):
    __tablename__ = "weak_points"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chapter_id = Column(String(50), nullable=False, index=True)
    knowledge_point = Column(String(200), nullable=False)
    doc_section_id = Column(String(50), nullable=True)
    status = Column(String(20), default="active", comment="active/resolved")
    occurrence_count = Column(Integer, default=1)
    first_seen_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)


# ===== 学习计划表 =====

class LearningPlan(Base):
    __tablename__ = "learning_plans"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    goal_type = Column(String(30), nullable=False, comment="exam/project/paper/intro/remedial/extend/custom")
    goal_detail = Column(String(300), nullable=True, comment="目标描述")
    target_role = Column(String(100), nullable=True, comment="目标职业", index=True)
    deadline = Column(DateTime, nullable=True)
    weekly_hours = Column(Float, default=10.0)
    chapter_ids = Column(JSON, nullable=False, comment="[chapter_id, ...]")
    schedule = Column(JSON, nullable=True, comment="[{date, chapter_id, task_type, duration_min, status}]")
    status = Column(String(20), default="active", comment="active/completed/abandoned", index=True)
    profile_snapshot = Column(JSON, nullable=True)
    progress_pct = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ===== 计划子任务表 =====

class PlanTask(Base):
    __tablename__ = "plan_tasks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("learning_plans.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chapter_id = Column(String(50), nullable=False, index=True)
    name = Column(String(100), nullable=True, comment="任务中文名（章节名）")
    task_type = Column(String(30), nullable=False, comment="new_learn/review/quiz/derivation/code_lab/exam/compare/report")
    task_date = Column(DateTime, nullable=True)
    duration_min = Column(Integer, default=60)
    status = Column(String(20), default="pending", comment="pending/in_progress/completed/skipped")
    result = Column(JSON, nullable=True, comment="{score, correct_rate, time_spent, ...}")
    order_index = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

# ===== 考试/试卷表 =====

class ExamPaper(Base):
    __tablename__ = "exam_papers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    exam_type = Column(String(30), nullable=False, comment="mock/derivation/compare/code_lab/report")
    chapter_ids = Column(JSON, nullable=False)
    questions = Column(JSON, nullable=False, comment="[{type, content, answer, options, steps, ...}]")
    answers = Column(JSON, nullable=True, comment="用户提交的答案")
    score = Column(Float, nullable=True)
    total_score = Column(Float, default=100.0)
    time_limit_min = Column(Integer, default=120)
    time_spent_sec = Column(Integer, default=0)
    status = Column(String(20), default="draft", comment="draft/in_progress/completed")
    profile_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)


# ===== 答题记录表 =====

class AnswerRecord(Base):
    """答题记录——驱动画像自动更新的数据源"""
    __tablename__ = "answer_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    topic = Column(String(100), nullable=False, comment="知识点名称")
    question_id = Column(String(50), nullable=False)
    correct = Column(Boolean, nullable=False)
    time_spent_sec = Column(Float, default=0)
    source = Column(String(20), nullable=False, comment="exam/wizard/quiz")
    created_at = Column(DateTime, server_default=func.now(), index=True)

    user = relationship("User", back_populates="answer_records")


# ===== 生成产物表（创作页持久化） =====

class GeneratedItem(Base):
    __tablename__ = "generated_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_id = Column(Integer, nullable=True, index=True, comment="关联学习计划ID")
    task_id = Column(Integer, nullable=True, index=True, comment="关联计划任务ID")
    type = Column(String(50), nullable=False, comment="document/mindmap/question/code/video/quiz_result/search/chat_profile/progress/report/tutor_action:*")
    title = Column(String(200), nullable=False)
    chapter = Column(String(120), nullable=True, comment="摘要预览")
    content = Column(Text, nullable=True, comment="当前正式版本兼容投影（完整内容 JSON/文本）")
    raw_data = Column(JSON, nullable=True, comment="当前正式版本兼容投影（公开元数据 + 原有运行元数据）")
    current_version_id = Column(Integer, nullable=True, index=True, comment="当前 GeneratedItemVersion.id；兼容旧库时可为空")
    current_version_number = Column(Integer, nullable=False, default=0, server_default="0", index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)

    user = relationship("User")
    versions = relationship(
        "GeneratedItemVersion", foreign_keys="GeneratedItemVersion.artifact_id",
        back_populates="artifact", cascade="all, delete-orphan", order_by="GeneratedItemVersion.version_number",
    )


class GeneratedItemVersion(Base):
    """Immutable child versions for one stable GeneratedItem artifact identity."""
    __tablename__ = "generated_item_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version_number", name="uq_generated_item_versions_artifact_number"),
        UniqueConstraint("artifact_id", "created_by_operation", name="uq_generated_item_versions_artifact_operation"),
        Index("ix_generated_item_versions_artifact_created", "artifact_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(Integer, ForeignKey("generated_items.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    parent_version_id = Column(Integer, ForeignKey("generated_item_versions.id"), nullable=True, index=True)
    title_snapshot = Column(String(200), nullable=False)
    chapter_snapshot = Column(String(120), nullable=True)
    content_snapshot = Column(Text, nullable=True)
    public_data_snapshot = Column(JSON, nullable=False, default=dict)
    private_data_snapshot = Column(JSON, nullable=True, comment="服务端版本事实；永不进入Artifact公开投影")
    content_digest = Column(String(64), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="generated", server_default="generated", index=True)
    created_by_operation = Column(String(64), nullable=False)
    operation_guard = Column(String(64), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)

    artifact = relationship("GeneratedItem", foreign_keys=[artifact_id], back_populates="versions")
    parent_version = relationship("GeneratedItemVersion", remote_side=[id], foreign_keys=[parent_version_id])


class ArtifactReview(Base):
    """Append-only review fact bound to one exact immutable artifact version."""
    __tablename__ = "artifact_reviews"
    __table_args__ = (
        UniqueConstraint("user_id", "operation_key", name="uq_artifact_reviews_owner_operation"),
        UniqueConstraint(
            "artifact_version_id", "round", "reviewer_role",
            name="uq_artifact_reviews_version_round_role",
        ),
        Index("ix_artifact_reviews_artifact_created", "artifact_id", "created_at"),
    )

    review_id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    artifact_id = Column(Integer, ForeignKey("generated_items.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_version_id = Column(
        Integer, ForeignKey("generated_item_versions.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    version_digest = Column(String(64), nullable=False, index=True)
    review_contract_version = Column(String(64), nullable=False)
    round = Column(Integer, nullable=False)
    reviewer_role = Column(String(40), nullable=False)
    # A formal verdict is authoritative only when produced by server-trusted review work.
    authority_kind = Column(String(32), nullable=False, default="UNTRUSTED", index=True)
    provider = Column(String(80), nullable=True)
    model = Column(String(120), nullable=True)
    verdict = Column(String(20), nullable=False, index=True)
    scores = Column(JSON, nullable=False, default=dict)
    safe_summary = Column(Text, nullable=False)
    issues_json = Column(JSON, nullable=False, default=list)
    preserve_json = Column(JSON, nullable=False, default=list)
    operation_key = Column(String(64), nullable=False)
    operation_guard = Column(String(64), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)


class ArtifactLineageEdge(Base):
    """Append-only semantic transform edge; source IDs intentionally survive deletion."""
    __tablename__ = "artifact_lineage_edges"
    __table_args__ = (
        UniqueConstraint("user_id", "operation_key", name="uq_artifact_lineage_owner_operation"),
        Index("ix_artifact_lineage_target_created", "target_artifact_id", "created_at"),
        Index("ix_artifact_lineage_source", "source_artifact_id", "source_version_id"),
    )

    lineage_edge_id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # No source FK: deletion must leave a safe tombstoned identity + digest.
    source_artifact_id = Column(Integer, nullable=False, index=True)
    source_version_id = Column(Integer, nullable=False, index=True)
    source_version_digest = Column(String(64), nullable=False)
    source_block_refs = Column(JSON, nullable=False, default=list)
    target_artifact_id = Column(
        Integer, ForeignKey("generated_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    target_version_id = Column(
        Integer, ForeignKey("generated_item_versions.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    transform_type = Column(String(60), nullable=False)
    transform_contract_version = Column(String(64), nullable=False)
    knowledge_refs = Column(JSON, nullable=False, default=list)
    source_refs = Column(JSON, nullable=False, default=list)
    learner_context_ref = Column(String(200), nullable=True)
    safe_operation_summary = Column(Text, nullable=False)
    generator_provenance = Column(JSON, nullable=False, default=dict)
    operation_key = Column(String(64), nullable=False)
    operation_guard = Column(String(64), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)


class GeneratedItemCodeRun(Base):
    """Durable process fact for one concrete code artifact version.

    Process truth (exit code / timeout / stderr) is intentionally separate from
    the learning observation so a successful process can still yield an
    unexpected educational result without being mislabeled as a failed run.
    """
    __tablename__ = "generated_item_code_runs"
    __table_args__ = (
        UniqueConstraint("artifact_version_id", "run_number", name="uq_code_runs_version_number"),
        UniqueConstraint("artifact_version_id", "execution_key_digest", name="uq_code_runs_version_execution"),
        Index("ix_code_runs_artifact_version_created", "artifact_id", "artifact_version_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    artifact_id = Column(Integer, ForeignKey("generated_items.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_version_id = Column(Integer, ForeignKey("generated_item_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    run_number = Column(Integer, nullable=False)
    execution_key_digest = Column(String(64), nullable=False)
    request_guard = Column(String(64), nullable=False)
    status = Column(String(30), nullable=False, default="PENDING", server_default="PENDING", index=True)
    language = Column(String(30), nullable=False, default="python", server_default="python")
    entrypoint = Column(String(200), nullable=False, default="main.py", server_default="main.py")
    parameters_public = Column(JSON, nullable=False, default=dict)
    stdout_public = Column(Text, nullable=False, default="", server_default="")
    stderr_public = Column(Text, nullable=False, default="", server_default="")
    exit_code = Column(Integer, nullable=True)
    timed_out = Column(Boolean, nullable=False, default=False, server_default="0")
    duration_ms = Column(Integer, nullable=True)
    error_category = Column(String(80), nullable=True)
    error_line = Column(Integer, nullable=True)
    output_refs_public = Column(JSON, nullable=False, default=list)
    observation_status = Column(String(30), nullable=False, default="NOT_ANALYZED", server_default="NOT_ANALYZED")
    created_at = Column(DateTime, server_default=func.now(), index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class InteractiveQuestionAttempt(Base):
    """Immutable server-judged answer attempt for an interactive lesson item."""
    __tablename__ = "interactive_question_attempts"
    __table_args__ = (
        UniqueConstraint(
            "artifact_version_id", "item_identity", "attempt_number",
            name="uq_interactive_attempt_version_item_number",
        ),
        UniqueConstraint(
            "artifact_version_id", "item_identity", "submission_key_digest",
            name="uq_interactive_attempt_submission",
        ),
        Index("ix_interactive_attempt_artifact_item", "artifact_id", "artifact_version_id", "item_identity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    artifact_id = Column(Integer, ForeignKey("generated_items.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_version_id = Column(Integer, ForeignKey("generated_item_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    item_identity = Column(String(120), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    submission_key_digest = Column(String(64), nullable=False)
    request_guard = Column(String(64), nullable=False)
    selected_answer_public = Column(JSON, nullable=False)
    correct = Column(Boolean, nullable=False)
    hint_used = Column(Boolean, nullable=False, default=False, server_default="0")
    solution_viewed = Column(Boolean, nullable=False, default=False, server_default="0")
    status = Column(String(30), nullable=False, default="SUBMITTED", server_default="SUBMITTED", index=True)
    submitted_at = Column(DateTime, server_default=func.now(), index=True)
    event_id = Column(Integer, ForeignKey("workspace_learning_events.id"), nullable=True, unique=True, index=True)


class WorkspaceLearningEvent(Base):
    """Evidence events emitted by standalone interactive learning artifacts."""
    __tablename__ = "workspace_learning_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    artifact_id = Column(Integer, ForeignKey("generated_items.id"), nullable=False, index=True)
    event_type = Column(String(60), nullable=False, index=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class TutorSession(Base):
    """Program-owned state for one graph-driven tutor conversation."""
    __tablename__ = "tutor_sessions"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_tutor_sessions_session_id"),
        UniqueConstraint("user_id", "thread_id", name="uq_tutor_sessions_user_thread"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    graph_id = Column(String(80), nullable=False, index=True)
    goal = Column(String(300), nullable=False)
    duration_days = Column(Integer, nullable=False, default=1)
    daily_minutes = Column(Integer, nullable=False, default=30)
    status = Column(String(30), nullable=False, default="active", index=True)
    current_node_id = Column(String(100), nullable=True, index=True)
    plan_nodes = Column(JSON, nullable=False, default=list)
    node_states = Column(JSON, nullable=False, default=dict)
    progress_pct = Column(Float, nullable=False, default=0.0)
    next_action = Column(JSON, nullable=False, default=dict)
    workflow_checkpoint = Column(JSON, nullable=False, default=dict)
    session_summary = Column(JSON, nullable=False, default=dict)
    learner_state_version = Column(String(100), nullable=True)
    workflow_revision = Column(Integer, nullable=False, default=0, server_default="0")
    last_transition_key = Column(String(128), nullable=True, index=True)
    pause_reason = Column(String(300), nullable=True)
    paused_at = Column(DateTime, nullable=True)
    resumed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class TutorLearningEvent(Base):
    """Immutable evidence used to calculate tutor mastery and progress."""
    __tablename__ = "tutor_learning_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tutor_session_id = Column(String(64), ForeignKey("tutor_sessions.session_id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    node_id = Column(String(100), nullable=False, index=True)
    event_type = Column(String(60), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class ProactiveTutorEvent(Base):
    """Server-owned trigger consumed by the deterministic proactive policy."""
    __tablename__ = "proactive_tutor_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_proactive_tutor_event_id"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_proactive_event_user_dedupe"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(80), nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    conversation_id = Column(String(100), nullable=True, index=True)
    tutor_session_id = Column(String(64), ForeignKey("tutor_sessions.session_id"), nullable=True, index=True)
    event_type = Column(String(60), nullable=False, index=True)
    trigger_ref = Column(String(160), nullable=False, index=True)
    evidence_ids = Column(JSON, nullable=False, default=list)
    payload = Column(JSON, nullable=False, default=dict)
    learner_state_version = Column(String(100), nullable=True)
    dedupe_key = Column(String(160), nullable=False, index=True)
    available_after = Column(DateTime, nullable=True, index=True)
    status = Column(String(30), nullable=False, default="pending", server_default="pending", index=True)
    policy_result = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    processed_at = Column(DateTime, nullable=True)


class ProactiveTutorMessage(Base):
    """Durable outgoing Tutor message with server-side unread state."""
    __tablename__ = "proactive_tutor_messages"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_proactive_tutor_message_id"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_proactive_message_user_dedupe"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(80), nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    conversation_id = Column(String(100), nullable=False, index=True)
    tutor_session_id = Column(String(64), ForeignKey("tutor_sessions.session_id"), nullable=True, index=True)
    proactive_event_id = Column(String(80), nullable=False, index=True)
    trigger_event = Column(String(60), nullable=False, index=True)
    trigger_event_id = Column(String(160), nullable=False)
    evidence_ids = Column(JSON, nullable=False, default=list)
    learner_state_version = Column(String(100), nullable=True)
    resource_artifact_ids = Column(JSON, nullable=False, default=list)
    dedupe_key = Column(String(160), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="柿小吉 AI 导师")
    content = Column(Text, nullable=False)
    priority = Column(String(20), nullable=False, default="normal", index=True)
    recommended_action = Column(JSON, nullable=False, default=dict)
    resource_action = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    read_at = Column(DateTime, nullable=True, index=True)
    dismissed_at = Column(DateTime, nullable=True)
    actioned_at = Column(DateTime, nullable=True)


class ProactiveTutorPreference(Base):
    """Per-user deterministic contact limits and opt-out state."""
    __tablename__ = "proactive_tutor_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_proactive_tutor_preference_user"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    quiet_start_hour = Column(Integer, nullable=False, default=22, server_default="22")
    quiet_end_hour = Column(Integer, nullable=False, default=8, server_default="8")
    cooldown_minutes = Column(Integer, nullable=False, default=180, server_default="180")
    max_messages_per_day = Column(Integer, nullable=False, default=3, server_default="3")
    ignored_count = Column(Integer, nullable=False, default=0, server_default="0")
    paused_until = Column(DateTime, nullable=True, index=True)
    category_preferences = Column(JSON, nullable=False, default=dict)
    last_contact_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ===== 智能体调用记录表（Agent 记录页数据源） =====
#
# 每次 agent.invoke_single_agent 落库一行；Agent 记录页从这张表拉
# 列表 / 统计 / 趋势，并 join generated_items 拿产物详情。
#
class AgentInvocation(Base):
    __tablename__ = "agent_invocations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    agent_name = Column(String(50), nullable=False, index=True, comment="agent 短名：profile/document/question/mindmap/code/path/tutor/video")
    agent_label = Column(String(100), nullable=True, comment="中文展示名，如「学情分析智能体」")
    target = Column(String(100), nullable=True, comment="前端传的 target 字段")
    status = Column(String(20), nullable=False, default="success", index=True, comment="success / failed / running")
    duration_ms = Column(Integer, nullable=True, comment="耗时（毫秒）")
    input_summary = Column(String(255), nullable=True, comment="任务摘要（来自 input.topic / input.question 等）")
    output_id = Column(Integer, nullable=True, index=True, comment="对应 generated_items.id")
    output_type = Column(String(50), nullable=True, comment="产物类型，与 generated_items.type 对齐")
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)

    user = relationship("User")


# ===== 首页独立业务系统：消息 / 待办 / 活动 / 建议 =====

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_type = Column(String(50), nullable=False, default="system", index=True)
    source_id = Column(String(100), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=True)
    target_route = Column(String(120), nullable=True)
    is_read = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    read_at = Column(DateTime, nullable=True)


class DashboardTodo(Base):
    __tablename__ = "dashboard_todos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_type = Column(String(50), nullable=False, default="learning_path", index=True)
    source_id = Column(String(100), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    due_label = Column(String(50), nullable=True)
    status = Column(String(20), default="pending", index=True)
    target_route = Column(String(120), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime, nullable=True)


class DashboardActivity(Base):
    __tablename__ = "dashboard_activities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_type = Column(String(50), nullable=False, default="learning_path", index=True)
    source_id = Column(String(100), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    time_label = Column(String(50), nullable=True)
    target_route = Column(String(120), nullable=True)
    opened_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    last_opened_at = Column(DateTime, nullable=True)


class LearningSuggestion(Base):
    __tablename__ = "learning_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_type = Column(String(50), nullable=False, default="learning_path", index=True)
    source_id = Column(String(100), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    target_route = Column(String(120), nullable=True)
    status = Column(String(20), default="pending", index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    applied_at = Column(DateTime, nullable=True)


# ===== 管理端：公告系统 =====

class Announcement(Base):
    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    level = Column(String(20), nullable=False, default="info", index=True)
    status = Column(String(20), nullable=False, default="draft", index=True)
    target_scope = Column(String(30), nullable=False, default="all_users")
    published_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class UserAnnouncementRead(Base):
    __tablename__ = "user_announcement_reads"
    __table_args__ = (
        UniqueConstraint("user_id", "announcement_id", name="uq_user_announcement_read"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    announcement_id = Column(Integer, ForeignKey("announcements.id"), nullable=False, index=True)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


# ===== 管理端：审计日志 =====

class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    action = Column(String(50), nullable=False, index=True)
    target_type = Column(String(50), nullable=False, index=True)
    target_id = Column(String(100), nullable=True)
    success = Column(Boolean, default=True)
    summary = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)


# ===== 用户笔记 =====

class UserNote(Base):
    """用户笔记 — 独立的学习笔记系统"""
    __tablename__ = "user_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(300), nullable=False)
    content = Column(Text, nullable=True, default="")
    plain_text = Column(Text, nullable=True, default="")
    graph_node_id = Column(Integer, ForeignKey("graph_nodes.id"), nullable=True, index=True)
    material_ids = Column(JSON, nullable=True, default=list, comment="关联资料ID列表")
    artifact_ids = Column(JSON, nullable=True, default=list, comment="关联产物ID列表")
    tags = Column(JSON, nullable=True, default=list)
    category = Column(String(50), nullable=True, default="general", index=True)
    is_pinned = Column(Boolean, default=False)
    is_favorite = Column(Boolean, default=False)
    word_count = Column(Integer, default=0)
    content_format = Column(String(20), default="plain", comment="plain / markdown")
    summary = Column(Text, nullable=True, comment="AI 生成的摘要")
    status = Column(String(20), default="active", index=True, comment="active / archived / deleted")
    graph_id = Column(Integer, ForeignKey("learning_graphs.id"), nullable=True, index=True, comment="关联图谱")
    last_saved_at = Column(DateTime, nullable=True)
    ai_suggestions = Column(JSON, nullable=True, default=list, comment="AI建议列表")
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ===== 笔记关联 (NoteLink) =====

class NoteLink(Base):
    """笔记稳定关联 — graph/node/artifact/material/conversation"""
    __tablename__ = "note_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(Integer, ForeignKey("user_notes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    link_type = Column(String(30), nullable=False, index=True, comment="learning_graph / graph_node / artifact / material / conversation")
    target_id = Column(Integer, nullable=False, index=True)
    target_title = Column(String(300), nullable=True, comment="关联时的标题快照")
    created_at = Column(DateTime, server_default=func.now())


# ===== 笔记版本 (NoteRevision) =====

class NoteRevision(Base):
    """笔记版本历史 — 仅实质性变化时创建"""
    __tablename__ = "note_revisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(Integer, ForeignKey("user_notes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    title_snapshot = Column(String(300), nullable=False)
    content_snapshot = Column(Text, nullable=False)
    change_source = Column(String(30), nullable=False, default="user", comment="user / ai_applied / restore")
    suggestion_id = Column(Integer, ForeignKey("note_ai_suggestions.id"), nullable=True, comment="关联 AI 建议")
    created_at = Column(DateTime, server_default=func.now(), index=True)


# ===== AI 建议 (NoteAISuggestion) =====

class NoteAISuggestion(Base):
    """AI 笔记建议 — 独立保存，不直接覆盖原文"""
    __tablename__ = "note_ai_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(Integer, ForeignKey("user_notes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action_type = Column(String(30), nullable=False, index=True, comment="expand/polish/structure/summarize/check_understanding/偏差分析/下一步建议/extract_knowledge/extract_questions/generate_exercises/outline/mindmap_plan")
    source_revision = Column(Integer, nullable=True, comment="基于哪个版本的建议")
    suggestion_content = Column(Text, nullable=True, comment="AI 建议正文(Markdown)")
    structured_result = Column(JSON, nullable=True, comment="结构化分析结果")
    status = Column(String(20), default="generating", index=True, comment="generating / ready / partially_applied / applied / dismissed / failed / undone")
    apply_mode = Column(String(30), default="replace_content", comment="replace_content / append_content / display_only / artifact_only")
    source_content_hash = Column(String(32), nullable=True, comment="应用前的笔记内容 hash")
    pre_apply_revision_id = Column(Integer, nullable=True, comment="应用前保存的版本 ID")
    applied_revision_id = Column(Integer, nullable=True, comment="应用后保存的版本 ID")
    applied_content_hash = Column(String(32), nullable=True, comment="应用后的笔记内容 hash")
    provider = Column(String(30), nullable=True)
    model = Column(String(50), nullable=True)
    tokens_used = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    applied_at = Column(DateTime, nullable=True)
    undone_at = Column(DateTime, nullable=True, comment="撤销时间")
    undo_revision_id = Column(Integer, nullable=True, comment="撤销操作创建的版本 ID")


# ===== 学习图谱（多图谱支持）=====

class LearningGraph(Base):
    """用户学习图谱 — 支持多张图谱，一张为主图谱"""
    __tablename__ = "learning_graphs"
    __table_args__ = (
        UniqueConstraint("user_id", "external_id", name="uq_learning_graphs_user_external"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    external_id = Column(String(100), nullable=True, index=True, comment="v0.1.3 durable graph identity")
    graph_revision = Column(Integer, nullable=False, default=0, comment="optimistic graph revision")
    graph_summary = Column(Text, nullable=True, comment="public graph summary")
    title = Column(String(200), nullable=False)
    goal = Column(String(500), nullable=True, comment="学习目标描述")
    goal_type = Column(String(30), nullable=False, default="custom", comment="exam/project/intro/custom/image/upload/conversation")
    source_type = Column(String(30), nullable=True, comment="onboarding/image/upload/chat/manual")
    source_detail = Column(JSON, nullable=True, comment="{image_url, material_ids, conversation_id, ...}")
    planning_rationale = Column(Text, nullable=True, comment="LLM 个性化规划理由")
    profile_version = Column(Integer, nullable=True, comment="创建时的画像版本号")
    status = Column(String(20), nullable=False, default="active", index=True, comment="draft/active/paused/archived")
    is_primary = Column(Boolean, default=False, index=True)
    total_nodes = Column(Integer, default=0)
    completed_nodes = Column(Integer, default=0)
    progress_pct = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GraphNode(Base):
    """图谱节点 — 每个节点代表一个学习单元"""
    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("graph_id", "external_id", name="uq_graph_nodes_graph_external"),
        UniqueConstraint("graph_id", "continuity_key", name="uq_graph_nodes_graph_continuity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    external_id = Column(String(100), nullable=True, index=True, comment="v0.1.3 durable node identity")
    continuity_key = Column(String(100), nullable=True, index=True)
    unit_id = Column(String(100), nullable=True, index=True, comment="durable GraphLearningUnit.external_id")
    summary = Column(Text, nullable=True)
    node_type = Column(String(30), nullable=True)
    learning_objectives = Column(JSON, nullable=True, default=list)
    key_concepts = Column(JSON, nullable=True, default=list)
    mastery_evidence = Column(JSON, nullable=True, default=list)
    source_refs = Column(JSON, nullable=True, default=list)
    resource_plan_ids = Column(JSON, nullable=True, default=list)
    parent_id = Column(Integer, ForeignKey("graph_nodes.id"), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    knowledge_type = Column(String(30), nullable=False, default="theory", comment="theory/code/math/visual/practice/review")
    difficulty = Column(Integer, default=3, comment="1-5")
    estimated_minutes = Column(Integer, default=30)
    order_index = Column(Integer, default=0)
    status = Column(String(20), nullable=False, default="planned", index=True,
                    comment="planned/generating/generated/learning/completed/needs_review/failed")
    mastery_score = Column(Float, default=0.0, comment="0-100")
    resources = Column(JSON, nullable=True, default=list,
                       comment='[{artifact_id, type, title, status, agent, created_at}]')
    depends_on = Column(JSON, nullable=True, default=list, comment='[node_id, ...]')
    tags = Column(JSON, nullable=True, default=list, comment='["tag1", "tag2"]')
    is_hidden = Column(Boolean, default=False, comment="用户隐藏的节点")
    is_custom = Column(Boolean, default=False, comment="用户自定义节点")
    learning_goal = Column(Text, nullable=True, comment="节点学习目标")
    self_assessment = Column(String(20), nullable=True, comment="用户自评: mastered/understood/unclear")
    auto_generated = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GraphLearningUnit(Base):
    """First-class unit inside the existing LearningGraph aggregate."""
    __tablename__ = "graph_learning_units"
    __table_args__ = (
        UniqueConstraint("graph_id", "external_id", name="uq_graph_units_graph_external"),
        UniqueConstraint("graph_id", "continuity_key", name="uq_graph_units_graph_continuity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id = Column(String(100), nullable=False, index=True)
    continuity_key = Column(String(100), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    summary = Column(Text, nullable=False, default="")
    node_ids = Column(JSON, nullable=False, default=list)
    unit_order = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="active", index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GraphKnowledgeEdge(Base):
    """Typed edge inside the existing LearningGraph aggregate."""
    __tablename__ = "graph_knowledge_edges"
    __table_args__ = (
        UniqueConstraint("graph_id", "external_id", name="uq_graph_edges_graph_external"),
        UniqueConstraint(
            "graph_id", "source_node_id", "target_node_id", "edge_type",
            name="uq_graph_edges_signature",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id = Column(String(100), nullable=False, index=True)
    source_node_id = Column(Integer, ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    target_node_id = Column(Integer, ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    edge_type = Column(String(30), nullable=False, index=True)
    reason = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GraphResourcePlan(Base):
    """Durable lazy resource plan; GeneratedItem remains the artifact fact source."""
    __tablename__ = "graph_resource_plans"
    __table_args__ = (
        UniqueConstraint("graph_id", "external_id", name="uq_graph_resource_plans_graph_external"),
        UniqueConstraint("node_id", "continuity_key", name="uq_graph_resource_plans_node_continuity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id = Column(String(100), nullable=False, index=True)
    continuity_key = Column(String(100), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_type = Column(String(40), nullable=False, index=True)
    purpose = Column(Text, nullable=False)
    generation_spec = Column(JSON, nullable=False, default=dict)
    required_inputs = Column(JSON, nullable=False, default=list)
    trigger = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="planned", index=True)
    priority = Column(Integer, nullable=False, default=50)
    artifact_id = Column(Integer, ForeignKey("generated_items.id"), nullable=True, index=True)
    private_annotations = Column(JSON, nullable=False, default=dict)
    execution_task_id = Column(String(140), nullable=True, index=True)
    failure_metadata = Column(JSON, nullable=False, default=dict)
    thread_id = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GraphResourceGenerationRun(Base):
    """Immutable execution-key ledger for explicit resource materialization attempts."""
    __tablename__ = "graph_resource_generation_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "execution_key",
            name="uq_graph_resource_runs_user_execution_key",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_plan_id = Column(Integer, ForeignKey("graph_resource_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    execution_key = Column(String(140), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="claimed", index=True)
    artifact_id = Column(Integer, ForeignKey("generated_items.id"), nullable=True, index=True)
    failure_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GraphOrchestrationRun(Base):
    """Idempotency and revision ledger for formal graph orchestration."""
    __tablename__ = "graph_orchestration_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_graph_runs_user_idempotency"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    idempotency_key = Column(String(140), nullable=False, index=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=True, index=True)
    request_hash = Column(String(64), nullable=False)
    graph_revision = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="claimed", index=True)
    result_public = Column(JSON, nullable=True)
    error_code = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ===== Learning Evaluator durable facts (v0.1.10 formal adapter) =====

class LearningEvaluation(Base):
    """Evaluator-owned aggregate. Graph, evidence and profile facts remain external authorities."""
    __tablename__ = "learning_evaluations"
    __table_args__ = (
        UniqueConstraint("user_id", "graph_id", "node_id", name="uq_learning_evaluation_user_graph_node"),
        UniqueConstraint("external_id", name="uq_learning_evaluation_external_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(100), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=False, index=True)
    graph_continuity_key = Column(String(100), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    node_external_id = Column(String(100), nullable=False, index=True)
    current_revision = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class LearningEvaluationExecution(Base):
    """Durable reservation/operation ledger. Raw request keys and reservation tokens are never stored."""
    __tablename__ = "learning_evaluation_executions"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_external_id", "request_lookup_digest",
            name="uq_learning_eval_execution_request_digest",
        ),
        UniqueConstraint(
            "evaluation_external_id", "idempotency_lookup_digest",
            name="uq_learning_eval_execution_idempotency_digest",
        ),
        UniqueConstraint("reservation_lookup_digest", name="uq_learning_eval_execution_reservation_digest"),
        UniqueConstraint("active_scope_key", name="uq_learning_eval_execution_active_scope"),
        UniqueConstraint("result_ref", name="uq_learning_eval_execution_result_ref"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    evaluation_id = Column(Integer, ForeignKey("learning_evaluations.id", ondelete="CASCADE"), nullable=False, index=True)
    evaluation_external_id = Column(String(100), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=False, index=True)
    graph_external_id = Column(String(100), nullable=False, index=True)
    graph_continuity_key = Column(String(100), nullable=False, index=True)

    request_lookup_digest = Column(String(64), nullable=False, index=True)
    request_collision_guard_digest = Column(String(64), nullable=False)
    idempotency_lookup_digest = Column(String(64), nullable=False, index=True)
    idempotency_collision_guard_digest = Column(String(64), nullable=False)
    reservation_lookup_digest = Column(String(64), nullable=False, index=True)
    reservation_collision_guard_digest = Column(String(64), nullable=False)

    evaluation_mode = Column(String(40), nullable=False)
    base_revision = Column(Integer, nullable=False)
    target_revision = Column(Integer, nullable=False)
    request_digest = Column(String(100), nullable=False)
    existing_state_digest = Column(String(100), nullable=False)
    trusted_context_digest = Column(String(100), nullable=False)
    authority_digest = Column(String(100), nullable=False)
    node_contract_set_digest = Column(String(100), nullable=False)
    precommit_digest = Column(String(100), nullable=False)
    operation_binding_digest = Column(String(100), nullable=True)

    status = Column(String(20), nullable=False, default="ACTIVE", index=True)
    active_scope_key = Column(String(260), nullable=True, index=True)
    result_ref = Column(String(100), nullable=True, index=True)
    result_digest = Column(String(100), nullable=True)
    public_issue_code = Column(String(80), nullable=True)
    terminal_tombstone = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    terminal_at = Column(DateTime, nullable=True, index=True)


class LearningEvaluationResultRecord(Base):
    """Private sealed result plus positive-whitelist public projection."""
    __tablename__ = "learning_evaluation_results"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "evaluation_revision", name="uq_learning_eval_result_revision"),
        UniqueConstraint("result_ref", name="uq_learning_eval_result_ref"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    evaluation_id = Column(Integer, ForeignKey("learning_evaluations.id", ondelete="CASCADE"), nullable=False, index=True)
    execution_id = Column(Integer, ForeignKey("learning_evaluation_executions.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    evaluation_revision = Column(Integer, nullable=False)
    graph_revision = Column(Integer, nullable=False)
    result_ref = Column(String(100), nullable=False, index=True)
    canonical_result_digest = Column(String(100), nullable=False)
    node_contract_digest = Column(String(100), nullable=False)
    authority_context_digest = Column(String(100), nullable=False)
    authenticity_envelope_sealed = Column(Text, nullable=False)
    public_projection = Column(JSON, nullable=False)
    evidence_event_ids = Column(JSON, nullable=False, default=list)
    provider_runtime = Column(String(40), nullable=False, default="FORMAL_PROVIDER")
    created_at = Column(DateTime, server_default=func.now(), index=True)


class LearningEvaluationEvidenceRef(Base):
    """Stable evaluator reference to an authoritative WorkspaceLearningEvent."""
    __tablename__ = "learning_evaluation_evidence_refs"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "workspace_event_id", name="uq_learning_eval_evidence_event"),
        UniqueConstraint("evaluation_id", "evidence_external_id", name="uq_learning_eval_evidence_external"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    evaluation_id = Column(Integer, ForeignKey("learning_evaluations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    graph_id = Column(Integer, ForeignKey("learning_graphs.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_event_id = Column(Integer, ForeignKey("workspace_learning_events.id", ondelete="CASCADE"), nullable=False, index=True)
    generated_item_id = Column(Integer, ForeignKey("generated_items.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_external_id = Column(String(100), nullable=False, index=True)
    source_system = Column(String(80), nullable=False)
    adapter_contract_version = Column(String(80), nullable=False)
    structured_features = Column(JSON, nullable=False)
    occurred_at = Column(DateTime, nullable=False, index=True)
    first_seen_revision = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class StorybookProviderConfig(Base):
    """视觉绘本的提供方配置（如 GLM Key）。只有管理端可写；用户端经只读接口取用。"""
    __tablename__ = "storybook_provider_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(80), nullable=False, unique=True, index=True)
    config_value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
