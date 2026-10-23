try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict
from typing import List, Dict, Any, Optional


# ===== 画像子结构 =====

class SkillMatrix(TypedDict, total=False):
    """ML 技能热力图 - 4 个子领域 + 28 个细分技能点"""
    math: Dict[str, int]          # {linear_algebra, probability, calculus, statistics, optimization}
    programming: Dict[str, int]   # {python, numpy, pandas, data_structure, algorithm, sql}
    machine_learning: Dict[str, int]  # {supervised, unsupervised, neural_networks, deep_learning, nlp, cv, reinforcement, feature_engineering, model_evaluation, mlops, ensemble_methods}
    tools: Dict[str, int]         # {sklearn, pytorch, tensorflow, jupyter, git, docker, matplotlib, mlflow}


class CognitiveProfile(TypedDict, total=False):
    """认知画像 - 决定教学策略"""
    bloom_level: str              # remember/understand/apply/analyze/evaluate/create
    learning_style: str           # visual/auditory/reading/kinesthetic
    abstraction_ability: int      # 1-5
    mathematical_maturity: int    # 1-5
    question_depth: str           # surface/medium/deep
    learning_pace: str            # slow/medium/fast
    prefers_theory_first: bool
    needs_concrete_examples: bool
    attention_to_detail: int      # 1-5


class MisconceptionArchive(TypedDict, total=False):
    """易错档案 - 驱动练习生成"""
    concept_misunderstandings: List[Dict[str, Any]]
    math_blind_spots: List[str]
    coding_bad_habits: List[str]
    high_error_topics: List[str]
    exam_mistake_types: List[Dict[str, Any]]


class BehaviorPattern(TypedDict, total=False):
    """学习行为 - 驱动推荐策略"""
    completion_rate: float        # 0-1
    avg_session_minutes: int
    active_time_of_day: str       # morning/afternoon/evening/night
    content_format_preference: List[str]
    exercise_type_preference: str
    hint_usage_rate: float        # 0-1
    review_frequency: float       # 0-1
    drop_off_topics: List[str]
    persistence_score: int        # 1-5


class MotivationProfile(TypedDict, total=False):
    """动机画像 - 驱动内容方向"""
    primary_motivation: str       # job_hunting/research/career_change/curiosity/exam
    urgency_level: int            # 1-5
    self_efficacy: int            # 1-5
    topic_passion: List[Dict[str, Any]]
    career_target: str
    burnout_risk: str             # low/medium/high
    preferred_reward: str         # achievement/progress/social


class StudentProfile(TypedDict, total=False):
    """
    完整学生画像 - 6 大类 25+ 维度

    所有维度均通过自然语言对话自动提取，无需表单填写。
    每次对话后增量优化，不重建。
    """
    # 一、基础档案
    major: str
    grade_level: str
    target_role: str
    prior_courses: List[str]
    preferred_language: str

    # 二、技能热力图
    skill_matrix: SkillMatrix

    # 三、认知画像
    cognitive_profile: CognitiveProfile

    # 四、易错档案
    misconception_archive: MisconceptionArchive

    # 五、学习行为
    behavior_pattern: BehaviorPattern

    # 六、动机画像
    motivation_profile: MotivationProfile

    # 元数据
    confidence_score: float       # 0-1
    profile_version: int


class GeneratedDocument(TypedDict):
    """生成的文档资源"""
    content: str
    title: str
    topic: str
    difficulty: str
    estimated_time: int
    tags: List[str]


class QuestionItem(TypedDict):
    """练习题目"""
    id: str
    type: str                     # choice/fill_blank/coding/case_study
    question: str
    options: Optional[List[str]]
    answer: str
    explanation: str
    difficulty: int               # 1-5
    knowledge_point: str
    hints: List[str]


class CodeExample(TypedDict):
    """代码实操案例"""
    title: str
    language: str
    code: str
    explanation: str
    comments: List[str]
    expected_output: str
    prerequisites: List[str]
    complexity: str


class LearningPathStep(TypedDict):
    """学习路径步骤"""
    step_number: int
    topic: str
    description: str
    resources: List[Dict]
    estimated_hours: int
    prerequisites: List[str]
    status: str                   # not_started/in_progress/completed


class VideoResult(TypedDict):
    """教学视频生成结果"""
    url: str
    duration: int
    ratio: str
    resolution: str
    object_name: str
    stored_in_oss: bool


class AgentState(TypedDict):
    """
    LangGraph 共享状态 - 7 个 Agent 在此读写

    核心原则：
    1. profile_agent 读取 messages/user_query → 输出 student_profile
    2. 其他 Agent 读取 student_profile → 个性化生成资源
    3. LangGraph 自动合并返回的状态
    """

    # === 用户输入层 ===
    messages: List[dict]
    user_query: str
    user_intent: str              # learn/practice/ask_tutor/view_path
    user_id: Optional[int]        # 当前用户ID
    material_ids: Optional[List[int]]  # 资料ID列表（practice模式）
    question_count: Optional[int]      # 出题数量

    # === 学生画像层（6大类）===
    student_profile: Optional[StudentProfile]
    profile_confidence: float     # 0-1

    # === 资源生成层 ===
    document: Optional[GeneratedDocument]
    mindmap: Optional[str]
    questions: Optional[List[QuestionItem]]
    code_examples: Optional[List[CodeExample]]
    reading_materials: Optional[List[Dict]]
    video_result: Optional[VideoResult]
    video_skipped: bool

    # === 规划与输出层 ===
    learning_path: Optional[List[LearningPathStep]]
    tutor_response: Optional[str]
    final_summary: Optional[str]

    # === 控制与元数据 ===
    current_agent: str
    execution_log: List[Dict]
    errors: List[str]
    processing_time: Dict[str, float]
    total_tokens_used: int
