"""规则引擎——纯 Python 匹配，0 LLM 调用，0ms 延迟"""
from typing import Dict, List, Any


def apply(current_profile: Dict[str, Any], results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    根据答题结果即时调整画像。

    Args:
        current_profile: 当前画像 dict
        results: 答题结果列表 [{topic, correct, time_spent_sec}, ...]

    Returns:
        更新后的画像 dict（拷贝，不修改入参）
    """
    import copy
    profile = copy.deepcopy(current_profile)

    # 安全初始化字段
    profile.setdefault("ml_knowledge_level", 5)
    profile.setdefault("weak_points", [])
    profile.setdefault("mastered_topics", [])
    profile.setdefault("confidence", 0.5)
    profile.setdefault("learning_style", "综合型")

    # 统计每个知识点的对错
    topic_stats = {}  # {topic: {"correct": n, "wrong": n}}
    correct_streak = 0
    for r in results:
        topic = (r.get("topic") or "").strip()
        if not topic:
            continue
        if topic not in topic_stats:
            topic_stats[topic] = {"correct": 0, "wrong": 0}
        if r.get("correct"):
            topic_stats[topic]["correct"] += 1
            correct_streak += 1
        else:
            topic_stats[topic]["wrong"] += 1
            correct_streak = 0

    # 规则 1: 同一知识点答对 ≥ 3 次 → mastered_topics
    # 规则 4: 同一知识点答错 ≥ 2 次 → 重点薄弱点
    for topic, stats in topic_stats.items():
        if stats["correct"] >= _MASTER_THRESHOLD and topic not in profile["mastered_topics"]:
            profile["mastered_topics"].append(topic)
        if stats["wrong"] >= _ERROR_PATTERN_THRESHOLD:
            existing = [w for w in profile["weak_points"] if isinstance(w, dict) and w.get("name") == topic]
            if existing:
                existing[0]["severity"] = "high"
            else:
                profile["weak_points"].append({"name": topic, "severity": "high", "count": stats["wrong"]})

    # 规则 2: 答错任意题 → weak_points
    for r in results:
        topic = (r.get("topic") or "").strip()
        if not r.get("correct") and topic:
            already = [w for w in profile["weak_points"] if (isinstance(w, dict) and w.get("name") == topic) or (isinstance(w, str) and w == topic)]
            if not already:
                profile["weak_points"].append(topic)

    # 规则 3: 连续答对 ≥ 5 题 → confidence +0.1
    if correct_streak >= _CONFIDENCE_STREAK_THRESHOLD:
        profile["confidence"] = round(min(profile["confidence"] + 0.1, 1.0), 2)

    # 答错降 confidence
    wrong_count = sum(1 for r in results if not r.get("correct"))
    if wrong_count > 0:
        profile["confidence"] = round(max(profile["confidence"] - 0.05 * wrong_count, 0.0), 2)

    # 规则 5: 速度快 + 正确率高 → learning_style
    fast_correct = [r for r in results if r.get("correct") and r.get("time_spent_sec", 999) < _FAST_THRESHOLD_SEC]
    if len(fast_correct) >= 3 and len(fast_correct) / max(len(results), 1) > 0.6:
        profile["learning_style"] = "高效型"

    # knowledge 微调
    total_correct = sum(1 for r in results if r.get("correct"))
    total_wrong = sum(1 for r in results if not r.get("correct"))
    delta = (total_correct // 3) - (total_wrong // 3)
    profile["ml_knowledge_level"] = max(1, min(10, profile["ml_knowledge_level"] + delta))

    profile["_last_rule_update"] = len(results)

    return profile


# ===== 可配置阈值 =====
_MASTER_THRESHOLD = 3
_ERROR_PATTERN_THRESHOLD = 2
_CONFIDENCE_STREAK_THRESHOLD = 5
_FAST_THRESHOLD_SEC = 30
