"""
视频搜索客户端 — 直调本地视频搜索引擎（整合自 C:/Users/wuzbo/Documents/agent）
"""
from __future__ import annotations
import asyncio
import copy
import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional

from services.video_search_engine import (
    search_videos as _engine_search,
    DEFAULT_SOURCES,
)
from services.llm_service import get_llm_service

logger = logging.getLogger("rjianb.video_search")

_CLASSROOM_CACHE: Dict[str, Dict[str, Any]] = {}
_CLASSROOM_LOCKS: Dict[str, asyncio.Lock] = {}
_CACHE_TTL_SECONDS = 24 * 60 * 60


def clear_classroom_cache() -> None:
    """Clear process-local classroom cache. Kept public for tests and admin hooks."""
    _CLASSROOM_CACHE.clear()
    _CLASSROOM_LOCKS.clear()


def _live_generation_enabled() -> bool:
    return (os.getenv("LLM_LIVE_GENERATION") or "").strip().lower() in {"1", "true", "yes", "on"}


def _profile_cache_fingerprint(profile: Optional[dict]) -> str:
    if not profile:
        return "default"
    stable = {
        "major": profile.get("major"),
        "grade_level": profile.get("grade_level"),
        "target_role": profile.get("target_role"),
        "preferred_language": profile.get("preferred_language"),
    }
    raw = repr(sorted(stable.items()))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def _classroom_cache_key(topic: str, profile: Optional[dict]) -> str:
    normalized_topic = " ".join((topic or "").strip().lower().split())
    return f"{normalized_topic}:{_profile_cache_fingerprint(profile)}"


def _get_cached_classroom(key: str) -> Optional[dict]:
    cached = _CLASSROOM_CACHE.get(key)
    if not cached:
        return None
    if cached["expires_at"] < time.time():
        _CLASSROOM_CACHE.pop(key, None)
        return None
    result = copy.deepcopy(cached["result"])
    result["_from_cache"] = True
    return result


def _set_cached_classroom(key: str, result: dict) -> None:
    stored = copy.deepcopy(result)
    stored["_from_cache"] = False
    _CLASSROOM_CACHE[key] = {
        "expires_at": time.time() + _CACHE_TTL_SECONDS,
        "result": stored,
    }


def _compose_local_classroom(topic: str, profile: Optional[dict] = None) -> dict:
    """Build a complete interactive classroom without live LLM cost."""
    t = (topic or "本节知识点").strip()
    preferred_language = (profile or {}).get("preferred_language") or "Python"
    slides = {
        "title": f"{t}互动课堂",
        "slides": [
            {
                "type": "title",
                "title": t,
                "subtitle": "从概念到应用的快速课堂",
                "narration": f"同学你好，这节课我们用一个清晰的路线来理解{t}。先抓住它解决什么问题，再看核心概念，最后用一个小练习确认你真的掌握了。",
            },
            {
                "type": "goals",
                "items": [f"说清楚{t}的作用", "理解关键步骤", "能判断常见误区"],
                "narration": f"这节课有三个目标。第一，知道{t}为什么有用；第二，理解它的核心步骤；第三，遇到题目时能避开最常见的误区。",
            },
            {
                "type": "content",
                "title": "核心直觉",
                "body": f"{t}不是孤立的定义，而是一套帮助我们把问题拆清楚的方法。学习时先问输入是什么、输出是什么、判断依据是什么。",
                "narration": f"先建立直觉。学{t}不要一上来背术语，先看它处理什么输入，产出什么结果，以及中间依靠什么规则做判断。",
            },
            {
                "type": "content",
                "title": "学习步骤",
                "body": "第一步识别任务，第二步列出关键条件，第三步按规则推导，第四步用例子验证。",
                "narration": "可以按四步走：先识别任务，再列关键条件，然后按规则推导，最后拿一个具体例子检查结果是否合理。",
            },
            {
                "type": "code",
                "title": "最小示例",
                "lang": preferred_language.lower(),
                "code": "# 把概念落成步骤\nsteps = ['识别任务', '列条件', '按规则推导', '用例子验证']\nfor step in steps:\n    print(step)",
                "narration": f"如果用{preferred_language}表达，可以先把学习过程拆成步骤。代码不重要，重要的是你能把抽象概念变成可执行流程。",
            },
            {
                "type": "quiz",
                "question": f"学习{t}时，最先应该确认什么？",
                "options": ["A. 直接背公式", "B. 输入、输出和判断依据", "C. 跳过例子", "D. 只看结论"],
                "answer": 1,
                "narration": "来做一个小检查。不要急着背公式，先确认输入、输出和判断依据，这是理解大多数知识点的起点。",
            },
            {
                "type": "summary",
                "points": [f"{t}要先抓作用", "用步骤拆解过程", "用例子验证理解"],
                "narration": f"总结一下，学习{t}要先抓作用，再拆步骤，最后用例子验证。你能讲清这三点，就已经建立了稳定的理解框架。",
            },
        ],
    }
    return {
        "script": __import__("json").dumps(slides, ensure_ascii=False),
        "slides": slides,
        "provider": "local_composer",
        "topic": t,
        "_from_cache": False,
    }


async def _generate_live_classroom_locked(key: str, topic: str, profile: Optional[dict]) -> dict:
    """Call live LLM for classroom generation while the caller holds the key lock."""
    import json as _json

    llm = await get_llm_service()
    prompt = f"""为知识点「{topic}」生成一份互动课堂。输出严格 JSON：
{{
  "title": "课程标题",
  "slides": [
    {{"type": "title", "title": "大标题", "subtitle": "副标题", "narration": "老师开场白，80-150字口语化"}},
    {{"type": "goals", "items": ["目标"], "narration": "老师用口语介绍学习目标，60-100字"}},
    {{"type": "content", "title": "小节标题", "body": "板书要点", "narration": "老师讲解本节内容，80-120字"}},
    {{"type": "code", "title": "代码示例", "code": "print(1)", "lang": "python", "narration": "老师解释代码，60-100字"}},
    {{"type": "quiz", "question": "问题？", "options": ["A","B","C","D"], "answer": 0, "narration": "老师念题，40-60字"}},
    {{"type": "summary", "points": ["要点"], "narration": "老师总结，60-80字"}}
  ]
}}

关键要求：
- 每张幻灯片都有 narration 字段，是老师自然的口语讲解，不是照念 slide 文字
- 生成 6-10 张幻灯片
- 纯 JSON，不包裹```json```"""
    response = await llm.generate(prompt, max_tokens=1800)
    text = (response or "").strip().replace("\ufeff", "")
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        text = text[s:e + 1]
    try:
        data = _json.loads(text)
        result = {"script": text, "slides": data, "provider": "deepseek", "topic": topic, "_from_cache": False}
    except _json.JSONDecodeError as ex:
        logger.warning(f"[classroom] JSON parse failed: {ex}, raw[:100]={text[:100]}")
        result = _compose_local_classroom(topic, profile)
        result["provider"] = "local_composer_after_llm_parse_error"
        result["llm_raw_preview"] = text[:300]
    _set_cached_classroom(key, result)
    return result


async def search_videos(
    q: str,
    limit: int = 10,
    sources: Optional[str] = None,
) -> Dict[str, Any]:
    """搜索外部视频资源（DuckDuckGo + 多平台）"""
    q = (q or "").strip()
    if not q:
        return {"success": False, "results": [], "source": "empty_query", "diagnostics": ["搜索关键词不能为空"], "elapsed_ms": 0}
    src_list = (sources or "").split(",") if sources else list(DEFAULT_SOURCES)
    src_list = [s.strip() for s in src_list if s.strip()]
    if not src_list:
        src_list = list(DEFAULT_SOURCES)
    start = time.time()
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: _engine_search(q, limit=limit, sources=src_list))
        elapsed = int((time.time() - start) * 1000)
        return {
            "success": True,
            "results": [{
                "title": r.title, "platform": r.platform, "url": r.url,
                "cover": r.cover, "snippet": r.snippet
            } for r in (results or [])],
            "source": "local_engine", "diagnostics": [],
            "elapsed_ms": elapsed, "query": q, "platforms": src_list, "count": len(results or [])
        }
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        logger.warning(f"[video_search] 搜索失败: {type(e).__name__}: {e}")
        return {"success": False, "results": [], "diagnostics": [f"搜索异常: {type(e).__name__}"], "elapsed_ms": elapsed}


async def health() -> Dict[str, Any]:
    return {"online": True, "engine": "local"}


async def generate_lecture_script_md(topic: str, profile: Optional[dict] = None) -> dict:
    """AI 讲稿模式：用 DeepSeek 生成教学讲稿（Markdown 格式）"""
    llm = await get_llm_service()
    prompt = f"""为知识点「{topic}」生成一份教学讲稿。输出为 Markdown 格式：

# 课程标题

## 学习目标
- 目标1
- 目标2

## 核心讲解

### 1. 引入
（用生活化场景引入概念，约200字）

### 2. 核心概念
（清晰定义，用类比辅助理解，约300字）

### 3. 推导/示例
（step-by-step 推导或代码示例，约300字）

### 4. 小结
（一句话总结关键要点）

## 互动问答
Q1: （问题）
A1: （答案）
Q2: （问题）
A2: （答案）

请生成完整内容，纯 Markdown 格式，用中文。"""
    response = await llm.generate(prompt, max_tokens=2000)
    return {"script": (response or "").strip(), "slides": None, "provider": "deepseek", "topic": topic}


async def generate_classroom_script(topic: str, profile: Optional[dict] = None) -> dict:
    """
    替代 OpenMAIC：用 DeepSeek 生成互动课堂幻灯片（含逐页老师讲稿）
    """
    key = _classroom_cache_key(topic, profile)
    cached = _get_cached_classroom(key)
    if cached:
        return cached

    lock = _CLASSROOM_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _get_cached_classroom(key)
        if cached:
            return cached

        if not _live_generation_enabled():
            result = _compose_local_classroom(topic, profile)
            _set_cached_classroom(key, result)
            return result
        return await _generate_live_classroom_locked(key, topic, profile)

    llm = await get_llm_service()
    prompt = f"""为知识点「{topic}」生成一份互动课堂。输出严格 JSON：

{{
  "title": "课程标题",
  "slides": [
    {{"type": "title", "title": "大标题", "subtitle": "副标题", "narration": "老师开场白，欢迎学生，介绍本节课主题（80-150字口语化）"}},
    {{"type": "goals", "items": ["目标"], "narration": "老师用口语介绍学习目标（60-100字）"}},
    {{"type": "content", "title": "小节标题", "body": "板书要点", "narration": "老师讲解本节内容（80-120字口语化，用类比、举例）"}},
     {{"type": "code", "title": "代码示例", "code": "print(1)", "lang": "python", "narration": "老师解释代码（60-100字）"}},
     {{"type": "quiz", "question": "问题？", "options": ["A","B","C","D"], "answer": 0, "narration": "老师念题（40-60字）"}},
     {{"type": "summary", "points": ["要点"], "narration": "老师总结（60-80字）"}}
  ]
}}

关键要求：
- 每张幻灯片都有 narration 字段，是老师自然的口语讲解，不是照念 slide 文字
- narration 用口语化中文，像真人讲课：有语气词、停顿、强调、举例
- 生成 6-10 张幻灯片，不同类型的 narration 长度各异
- 纯 JSON，不包裹```json```"""
    import json as _json
    response = await llm.generate(prompt, max_tokens=3500)
    text = (response or "").strip()
    text = text.replace("\ufeff","")
    if text.startswith("```"): text = text.split("\n",1)[-1].rsplit("```",1)[0].strip()
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s: text = text[s:e+1]
    try:
        data = _json.loads(text)
        result = {"script": text, "slides": data, "provider": "deepseek", "topic": topic, "_from_cache": False}
        _set_cached_classroom(key, result)
        return result
    except _json.JSONDecodeError as ex:
        logger.warning(f"[classroom] JSON 解析失败: {ex}, raw[:100]={text[:100]}")
        result = _compose_local_classroom(topic, profile)
        result["provider"] = "local_composer_after_llm_parse_error"
        result["llm_raw_preview"] = text[:300]
        _set_cached_classroom(key, result)
        return result
