"""
Wizard 业务域 Pydantic 模型

集中管理 6 阶段画像构建向导的所有请求/响应模型。
保持与原 routers/wizard.py 中定义完全一致（行为不变）。
"""
from pydantic import BaseModel
from typing import List, Optional, Dict, Any


# ===== 阶段 3 答题 =====

class QuizAnswer(BaseModel):
    question_id: str
    selected_option: int  # -1 表示"不知道"


class SubmitQuizRequest(BaseModel):
    answers: List[QuizAnswer]


# ===== 阶段 2 基础信息 =====

class BasicInfoRequest(BaseModel):
    major: Optional[str] = None
    degree: Optional[str] = None
    grade: Optional[str] = None
    school: Optional[str] = None
    preferred_language: Optional[str] = None


class LearningGoalsRequest(BaseModel):
    target_role: Optional[str] = None
    deadline: Optional[str] = None
    motivation: Optional[str] = None
    weekly_hours: Optional[int] = None


class PriorExperienceRequest(BaseModel):
    courses_taken: Optional[List[str]] = None
    projects: Optional[List[str]] = None
    self_assessment: Optional[str] = None


# ===== 阶段 5 确认 =====

class ProfileConfirmRequest(BaseModel):
    adjustments: Optional[Dict[str, Any]] = None  # {chapter: {kp: score_adjustment}}
    notes: Optional[str] = None


# ===== 章节题目展示 =====

class ChapterQuestion(BaseModel):
    question_id: str
    chapter: str
    question: str
    options: List[str]
    difficulty: int


# ===== 阶段 4 画像生成 =====

class GenerateProfileRequest(BaseModel):
    quiz_answers: List[Dict[str, Any]]
    basic_info: Optional[Dict[str, str]] = None
    learning_goals: Optional[Dict[str, Any]] = None
    prior_experience: Optional[Dict[str, Any]] = None


class SaveComputedProfileRequest(BaseModel):
    capability_matrix: Dict[str, int]  # { 章节名: 分数(0-100) }
    quiz_answers: Optional[List[Dict[str, Any]]] = None
    # === P1 修复：basic_info / learning_goals 也入模 ===
    basic_info: Optional[Dict[str, Any]] = None
    learning_goals: Optional[Dict[str, Any]] = None
    prior_experience: Optional[Dict[str, Any]] = None


# ===== 对话式画像构建 =====

class ChatProfileMessage(BaseModel):
    content: str


class ChatProfileRequest(BaseModel):
    messages: List[Dict[str, str]]  # [{"role": "user/assistant", "content": "..."}]


# ===== 阶段 5 合并画像 =====

class CombineProfileRequest(BaseModel):
    capability_matrix: Dict[str, int]  # 测评得分 {"线性回归": 80, ...}
    quiz_answers: List[QuizAnswer]
