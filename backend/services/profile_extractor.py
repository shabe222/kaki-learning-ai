"""
画像提取器 — 从 LLM 输出中提取并应用学生画像

包含两个核心函数：
- extract_profile_from_document : 从上传文档中提取结构化画像
- apply_profile_extraction      : 将提取结果落库到 StudentProfile
"""
import json
import logging
from datetime import datetime
from typing import Dict, Any

from database.models import StudentProfile

logger = logging.getLogger(__name__)


async def extract_profile_from_document(doc_text: str, extracted_info: Dict[str, Any]) -> Dict[str, Any]:
    """调用LLM从文档中提取结构化画像数据"""
    from services.llm_service import get_llm_service

    llm = await get_llm_service()

    education_text = json.dumps(extracted_info.get("education", {}), ensure_ascii=False)
    courses_text = json.dumps(extracted_info.get("courses_taken", []), ensure_ascii=False)
    projects_text = json.dumps(extracted_info.get("projects", []), ensure_ascii=False)
    skills_text = json.dumps(extracted_info.get("self_assessment", []), ensure_ascii=False)

    prompt = f"""你是机器学习教育画像分析专家。请从以下学生提交的文档中提取结构化画像数据。

【文档内容】：
{doc_text}

【已提取的结构化线索】：
教育背景：{education_text}
已修课程：{courses_text}
项目经历：{projects_text}
技能自述：{skills_text}

请从以上信息中提取以下 6 大类画像数据。不确定的字段用 null，不要编造。

返回纯 JSON（不要用 markdown 代码块包裹）："""

    prompt += r"""
{
  "major": "专业名称或null",
  "grade_level": "大一/大二/大三/大四/研一/研二/研三/已工作 或 null",
  "target_role": "目标职业或null",
  "prior_courses": ["课程1", "课程2"] 或 [],
  "preferred_language": "Python/R/Julia 或 null",

  "skill_matrix": {
    "math": {"linear_algebra": 1-5或null, "probability": 1-5或null, "calculus": 1-5或null, "statistics": 1-5或null, "optimization": 1-5或null},
    "programming": {"python": 1-5或null, "numpy": 1-5或null, "pandas": 1-5或null, "data_structure": 1-5或null, "algorithm": 1-5或null, "sql": 1-5或null},
    "machine_learning": {"supervised": 1-5或null, "unsupervised": 1-5或null, "neural_networks": 1-5或null, "deep_learning": 1-5或null, "nlp": 1-5或null, "cv": 1-5或null, "reinforcement": 1-5或null, "feature_engineering": 1-5或null, "model_evaluation": 1-5或null, "mlops": 1-5或null, "ensemble_methods": 1-5或null},
    "tools": {"sklearn": 1-5或null, "pytorch": 1-5或null, "tensorflow": 1-5或null, "jupyter": 1-5或null, "git": 1-5或null, "docker": 1-5或null, "matplotlib": 1-5或null, "mlflow": 1-5或null}
  },

  "cognitive_profile": {
    "bloom_level": "remember/understand/apply/analyze/evaluate/create 或 null",
    "learning_style": "visual/auditory/reading/kinesthetic 或 null",
    "abstraction_ability": 1-5或null,
    "mathematical_maturity": 1-5或null,
    "learning_pace": "slow/medium/fast 或 null",
    "prefers_theory_first": true/false/null,
    "needs_concrete_examples": true/false/null
  },

  "motivation_profile": {
    "primary_motivation": "job_hunting/research/career_change/curiosity/exam 或 null",
    "urgency_level": 1-5或null,
    "self_efficacy": 1-5或null,
    "topic_passion": [{"topic": "主题", "interest": 0.0-1.0}],
    "career_target": "目标岗位或null"
  },

  "confidence_score": 0.0-1.0
}

要求：
1. 从文档中尽可能多地提取信息，不确定的字段给 null
2. skill_matrix 中，如果文档提到"学过/掌握/熟悉"某技能给 3-4 分，"了解"给 2 分，"精通"给 5 分
3. prior_courses 从课程列表和技能描述中提取
4. motivation_profile 从项目经历和目标描述中推断
5. confidence_score 表示你对提取结果的信心（0-1），信息越多分数越高
"""

    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="你是机器学习教育画像分析专家。从文档中精确提取学生特征，不编造不确定的信息。返回纯JSON。",
            temperature=0.2,
            max_tokens=2000,
        )
        content = result.get("content", "")
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            if content.startswith("json"):
                content = content[4:].strip()
        return json.loads(content)
    except Exception as e:
        logger.error(f"[profile_extractor] LLM画像提取失败: {e}")
        return {}


def apply_profile_extraction(profile: StudentProfile, data: Dict[str, Any]):
    """将LLM提取的画像数据应用到数据库模型"""
    if not data:
        return

    if data.get("major"):
        profile.major = data["major"]
    if data.get("grade_level"):
        profile.grade_level = data["grade_level"]
    if data.get("target_role"):
        profile.target_role = data["target_role"]
    if data.get("prior_courses"):
        profile.prior_courses = data["prior_courses"]
    if data.get("preferred_language"):
        profile.preferred_language = data["preferred_language"]
    if data.get("skill_matrix"):
        existing = profile.skill_matrix or {}
        for category, skills in data["skill_matrix"].items():
            if isinstance(skills, dict):
                if category not in existing:
                    existing[category] = {}
                for sk, sv in skills.items():
                    if sv is not None:
                        existing[category][sk] = sv
        profile.skill_matrix = existing
    if data.get("cognitive_profile"):
        existing_cog = profile.cognitive_profile or {}
        cog = data["cognitive_profile"]
        for k, v in cog.items():
            if v is not None:
                existing_cog[k] = v
        profile.cognitive_profile = existing_cog
    if data.get("motivation_profile"):
        profile.motivation_profile = data["motivation_profile"]

    existing_detail = profile.profile_detail or {}
    existing_detail["llm_extracted"] = data
    existing_detail["llm_extracted_at"] = datetime.now().isoformat()
    profile.profile_detail = existing_detail
    profile.profile_version = (profile.profile_version or 0) + 1
