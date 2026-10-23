"""
画像生成规则引擎 — 综合文档解析 + 问答结果 → 生成 capability_matrix

规则：
1. 每题对应 1-3 个知识点
2. 正确 → 知识点得分 +1（累计），错误 → 0
3. 最终得分 = 正确次数 / 总出现次数 × 5（映射到 1-5 分）
4. 未考查知识点 → 文档提到"学过"给 3 分，否则给 1 分
5. overall_score = 所有知识点得分平均值 × 20
6. confidence_score = 文档完整度 × 0.4 + 答题完整度 × 0.6
"""

from __future__ import annotations

import logging
from typing import Dict, List, Any
from services.question_bank import (
    QUESTIONS, ALL_KNOWLEDGE_POINTS, KNOWLEDGE_POINT_TO_CHAPTER,
    CHAPTER_ALLOCATION, get_chapter_names
)

logger = logging.getLogger(__name__)


class ProfileEngine:
    """画像生成引擎"""

    @staticmethod
    def build_capability_matrix(
        quiz_answers: List[Dict[str, Any]],
        doc_info: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        综合问答结果和文档信息，生成 capability_matrix

        Args:
            quiz_answers: [{"question_id": "math_1", "selected_option": 1, "is_correct": true}, ...]
            doc_info: 文档提取的结构化信息

        Returns:
            full capability_matrix with scores, confidence, notes
        """
        # 1. 统计每个知识点的正确/总次数
        kp_correct = {}
        kp_total = {}
        for answer in quiz_answers:
            qid = answer.get("question_id", "")
            is_correct = answer.get("is_correct", False)
            # 跳过兴趣探测题
            if qid == "adv_1":
                continue
            question = _find_question(qid)
            if question:
                for kp in question["knowledge_points"]:
                    kp_total[kp] = kp_total.get(kp, 0) + 1
                    if is_correct:
                        kp_correct[kp] = kp_correct.get(kp, 0) + 1

        # 2. 构建完整 capability_matrix
        capability_matrix = {}
        chapter_names = get_chapter_names()

        for chapter, knowledge_points in ALL_KNOWLEDGE_POINTS.items():
            chapter_data = {}
            for kp in knowledge_points:
                total = kp_total.get(kp, 0)
                correct = kp_correct.get(kp, 0)

                if total > 0:
                    score = round(correct / total * 5, 1)
                    confidence = 0.7 + total * 0.1  # 考查次数越多越可信
                    notes = _infer_notes(chapter, kp, score, doc_info)
                else:
                    # 未被考查，根据文档推断
                    score, confidence, notes = _infer_from_doc(chapter, kp, doc_info)

                score = max(1.0, min(5.0, score))  # 限制 1-5
                confidence = max(0.3, min(0.95, confidence))

                chapter_data[kp] = {
                    "score": round(score, 1),
                    "confidence": round(confidence, 2),
                    "notes": notes
                }
            capability_matrix[chapter] = chapter_data

        # 3. 计算总体指标
        all_scores = []
        for ch_data in capability_matrix.values():
            for kp_data in ch_data.values():
                all_scores.append(kp_data["score"])

        overall_score = round(sum(all_scores) / max(len(all_scores), 1) * 20, 1)

        # 答题完整度
        max_quiz = sum(c["total"] for c in CHAPTER_ALLOCATION.values()) - 1  # 减去兴趣题
        actual_quiz = len([a for a in quiz_answers if a.get("question_id") != "adv_1"])
        quiz_completeness = min(1.0, actual_quiz / 15)

        # 文档完整度
        doc_completeness = ProfileEngine._calc_doc_completeness(doc_info) if doc_info else 0.3

        confidence_score = round(doc_completeness * 0.4 + quiz_completeness * 0.6, 2)

        return {
            "capability_matrix": capability_matrix,
            "overall_score": overall_score,
            "confidence_score": confidence_score,
            "total_knowledge_points": len(all_scores),
            "quiz_completeness": round(quiz_completeness, 2),
            "doc_completeness": round(doc_completeness, 2)
        }

    @staticmethod
    def get_weak_points(capability_matrix: Dict[str, Any], threshold: float = 2.5) -> List[Dict]:
        """获取薄弱知识点列表（score ≤ threshold）"""
        weak_points = []
        for chapter, kps in capability_matrix.items():
            for kp, data in kps.items():
                if data["score"] <= threshold:
                    weak_points.append({
                        "chapter": chapter,
                        "knowledge_point": kp,
                        "score": data["score"],
                        "confidence": data["confidence"],
                        "notes": data.get("notes", "")
                    })
        return sorted(weak_points, key=lambda x: x["score"])

    @staticmethod
    def get_strong_points(capability_matrix: Dict[str, Any], threshold: float = 3.5) -> List[Dict]:
        """获取强项知识点列表（score ≥ threshold）"""
        strong = []
        for chapter, kps in capability_matrix.items():
            for kp, data in kps.items():
                if data["score"] >= threshold:
                    strong.append({
                        "chapter": chapter,
                        "knowledge_point": kp,
                        "score": data["score"]
                    })
        return sorted(strong, key=lambda x: -x["score"])

    @staticmethod
    def _calc_doc_completeness(doc_info: Dict) -> float:
        """计算文档信息完整度 (0-1)"""
        score = 0.0
        if doc_info.get("education") and doc_info["education"].get("raw_lines"):
            score += 0.25
        if doc_info.get("courses_taken"):
            score += 0.25
        if doc_info.get("projects"):
            score += 0.25
        if doc_info.get("self_assessment"):
            score += 0.25
        return min(1.0, score)


def _find_question(qid: str) -> dict | None:
    for q in QUESTIONS:
        if q["id"] == qid:
            return q
    return None


def _infer_from_doc(chapter: str, kp: str, doc_info: Dict = None) -> tuple:
    """根据文档推断知识点得分"""
    if not doc_info:
        return 1.0, 0.5, f"未被考查，无文档参考"

    raw_text = " ".join(
        doc_info.get("raw_lines", []) if isinstance(doc_info, dict) else []
    )
    courses = " ".join(doc_info.get("courses_taken", []) if isinstance(doc_info, dict) else [])
    combined = (raw_text + " " + courses).lower()

    # 关键词匹配
    kp_keywords = {
        "linear_algebra": ["线性代数", "矩阵", "向量", "特征值"],
        "probability_stats": ["概率", "统计", "贝叶斯", "分布"],
        "calculus": ["微积分", "导数", "梯度", "偏导"],
        "optimization": ["优化", "梯度下降", "凸优化", "拉格朗日"],
        "python": ["python", "Python"],
        "numpy": ["numpy", "NumPy", "ndarray"],
        "pandas": ["pandas", "DataFrame", "读取csv"],
        "linear_regression": ["线性回归", "回归模型"],
        "logistic_regression": ["逻辑回归", "分类模型"],
        "decision_tree": ["决策树", "随机森林"],
        "svm": ["SVM", "支持向量机", "核函数"],
        "ensemble": ["集成学习", "bagging", "boosting", "xgboost"],
        "kmeans": ["k-means", "聚类"],
        "pca": ["PCA", "降维", "主成分"],
        "perceptron": ["感知机", "神经网络入门"],
        "backpropagation": ["反向传播", "梯度计算"],
        "cnn": ["CNN", "卷积", "图像识别"],
        "rnn": ["RNN", "LSTM", "序列模型"],
        "overfitting": ["过拟合", "overfitting"],
        "cross_validation": ["交叉验证", "验证集", "K折"],
        "metrics": ["精确率", "召回率", "F1", "评估指标"],
        "regularization": ["正则化", "L1", "L2", "dropout"],
        "nlp": ["NLP", "自然语言", "文本处理"],
        "cv": ["CV", "计算机视觉", "目标检测"],
        "reinforcement_learning": ["强化学习", "RL"],
        "mlops": ["MLOps", "部署", "CI/CD"]
    }

    keywords = kp_keywords.get(kp, [kp])
    matched = any(k.lower() in combined for k in keywords)

    if matched:
        return 3.0, 0.6, "文档中提及相关概念，推断有一定基础"
    return 1.0, 0.4, "未被考查，文档中也未提及"


def _infer_notes(chapter: str, kp: str, score: float, doc_info: Dict = None) -> str:
    """根据得分和章节推断备注"""
    if score <= 2:
        notes_map = {
            "linear_algebra": "矩阵运算可以，特征值/特征向量需要加强",
            "probability_stats": "概率基础知识薄弱",
            "calculus": "导数偏导需复习",
            "optimization": "不了解梯度下降原理",
            "python": "Python基础需加强",
            "numpy": "数组操作不熟练",
            "pandas": "数据处理能力弱",
            "logistic_regression": "混淆分类和回归概念",
            "svm": "不了解核函数概念",
            "backpropagation": "反向传播理解不足",
            "cnn": "卷积概念模糊",
            "regularization": "不知道正则化方法",
            "metrics": "评估指标概念混淆"
        }
        return notes_map.get(kp, f"需要加强学习")

    if score >= 4:
        notes_map = {
            "linear_algebra": "矩阵运算扎实",
            "calculus": "微积分基础扎实",
            "python": "Python 使用熟练",
            "pandas": "数据处理熟练",
            "linear_regression": "线性回归理解到位",
            "decision_tree": "决策树掌握较好",
        }
        return notes_map.get(kp, f"掌握良好")

    return "有一定基础，部分概念需巩固"


# 单例
profile_engine = ProfileEngine()