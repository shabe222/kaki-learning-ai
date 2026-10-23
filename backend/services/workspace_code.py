"""Deterministic, safe Python snippet generator for the standalone AI workspace.

Goals
-----
- Generate small, runnable Python code that *demonstrates* a knowledge point
  (e.g. ``for`` loop, ``while`` loop, list comprehension) without requiring a
  long prompt and without a free-form LLM.
- Always return code that can run in :mod:`services.code_executor` and
  produce a non-empty stdout for the most common topics.
- For topics that look custom, fall back to a short LLM call (DeepSeek) and
  sanitise the returned code so it remains under the sandbox rules.

This module never fabricates stdout; the only truth about what the snippet
prints comes from running the code in the sandbox.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from services.code_executor import (
    extract_knowledge_points,
    explain_python_lines,
    run_python,
    CodeRunResult,
)
from services.llm_service_real import extract_llm_text, get_llm_service

logger = logging.getLogger(__name__)


_FOR_LOOP_BODIES = {
    "for 循环": (
        "for i in range(1, 6):\n"
        "    print('hello', i)\n"
        "print('done')\n"
    ),
    "for循环": (
        "for i in range(1, 6):\n"
        "    print('hello', i)\n"
        "print('done')\n"
    ),
    "循环": (
        "for i in range(1, 6):\n"
        "    print('hello', i)\n"
        "print('done')\n"
    ),
    "列表推导式": (
        "squares = [n * n for n in range(1, 6)]\n"
        "print(squares)\n"
    ),
    "列表生成式": (
        "squares = [n * n for n in range(1, 6)]\n"
        "print(squares)\n"
    ),
    "字典推导式": (
        "cube = {n: n ** 3 for n in range(1, 5)}\n"
        "print(cube)\n"
    ),
    "while 循环": (
        "i = 1\n"
        "while i < 6:\n"
        "    print('step', i)\n"
        "    i += 1\n"
        "print('done')\n"
    ),
    "while循环": (
        "i = 1\n"
        "while i < 6:\n"
        "    print('step', i)\n"
        "    i += 1\n"
        "print('done')\n"
    ),
    "冒泡排序": (
        "nums = [5, 2, 4, 6, 1, 3]\n"
        "n = len(nums)\n"
        "for i in range(n - 1):\n"
        "    for j in range(n - 1 - i):\n"
        "        if nums[j] > nums[j + 1]:\n"
        "            nums[j], nums[j + 1] = nums[j + 1], nums[j]\n"
        "print(nums)\n"
    ),
    "装饰器": (
        "def announce(func):\n"
        "    def wrapper(*args, **kwargs):\n"
        "        print('call', func.__name__)\n"
        "        return func(*args, **kwargs)\n"
        "    return wrapper\n"
        "\n"
        "@announce\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "print(add(2, 3))\n"
    ),
    "递归求斐波那契": (
        "def fib(n):\n"
        "    if n < 2:\n"
        "        return n\n"
        "    return fib(n - 1) + fib(n - 2)\n"
        "\n"
        "for i in range(8):\n"
        "    print(i, fib(i))\n"
    ),
    "函数式编程": (
        "from functools import reduce\n"
        "nums = [1, 2, 3, 4]\n"
        "squared = list(map(lambda n: n * n, nums))\n"
        "evens = list(filter(lambda n: n % 2 == 0, nums))\n"
        "total = reduce(lambda a, b: a + b, nums)\n"
        "print('squared', squared)\n"
        "print('evens', evens)\n"
        "print('total', total)\n"
    ),
    "lambda": (
        "double = lambda n: n * 2\n"
        "print(double(7))\n"
        "print(list(map(lambda n: n + 1, [1, 2, 3])))\n"
    ),
    "异常处理": (
        "def safe_div(a, b):\n"
        "    try:\n"
        "        return a / b\n"
        "    except ZeroDivisionError:\n"
        "        return 'no division by zero'\n"
        "\n"
        "print(safe_div(8, 2))\n"
        "print(safe_div(8, 0))\n"
    ),
    "if 条件分支": (
        "score = 78\n"
        "if score >= 90:\n"
        "    print('A')\n"
        "elif score >= 60:\n"
        "    print('pass')\n"
        "else:\n"
        "    print('retry')\n"
    ),
}


_TITLE_BY_KEY = {
    "for 循环": "Python for 循环示例",
    "for循环": "Python for 循环示例",
    "循环": "Python for 循环示例",
    "while 循环": "Python while 循环示例",
    "while循环": "Python while 循环示例",
    "列表推导式": "Python 列表推导式示例",
    "列表生成式": "Python 列表推导式示例",
    "字典推导式": "Python 字典推导式示例",
    "冒泡排序": "Python 冒泡排序示例",
    "装饰器": "Python 装饰器示例",
    "递归求斐波那契": "Python 递归求斐波那契示例",
    "函数式编程": "Python 函数式编程示例",
    "lambda": "Python lambda 示例",
    "异常处理": "Python 异常处理示例",
    "if 条件分支": "Python if 条件分支示例",
}


def _normalise_topic(topic: str) -> str:
    text = re.sub(r"\s+", " ", str(topic or "")).strip().lower()
    text = text.replace("python", "").strip()
    return text


def _lookup_template(topic: str) -> Optional[str]:
    """Find a deterministic code template by topic.

    The lookup is intentionally loose: it strips Python / 示例 / 代码 /
    例 and tries both the full topic and a keyword fallback.  When the
    user mentions an unknown topic we fall back to a free-form LLM call
    instead of fabricating code in the test environment.
    """
    cleaned = re.sub(r"(?:python|py|示例|例(?:子)?|代码|程序|脚本)", "", topic, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    if not cleaned:
        return None
    if cleaned in _FOR_LOOP_BODIES:
        return _FOR_LOOP_BODIES[cleaned]
    for key, body in _FOR_LOOP_BODIES.items():
        if key in cleaned or cleaned in key:
            return body
    return None


def _title_for(topic: str) -> str:
    cleaned = _normalise_topic(topic)
    if cleaned in _TITLE_BY_KEY:
        return _TITLE_BY_KEY[cleaned]
    for key, value in _TITLE_BY_KEY.items():
        if key in cleaned or cleaned in key:
            return value
    return f"Python {topic or '代码'} 示例"


def _explanation_for(topic: str) -> str:
    cleaned = _normalise_topic(topic)
    if "for" in cleaned and "循环" in cleaned:
        return (
            "这段代码演示了 Python for 循环的基本用法。"
            "range(1, 6) 会生成 1, 2, 3, 4, 5 五个数，每次循环把数字 i 传给 print。"
            "range 函数的最后一个参数是 stop 边界（不包含），所以 range(1, 6) 一共跑 5 次。"
        )
    if "while" in cleaned and "循环" in cleaned:
        return (
            "这段代码用 while 循环按条件重复执行。"
            "只要 i < 6 为真，循环体就会一直跑，每轮打印 step i 并把 i 加 1。"
            "i += 1 是循环能终止的关键，否则会无限循环。"
        )
    if "列表推导式" in cleaned or "列表生成式" in cleaned:
        return (
            "列表推导式用一行表达式生成新列表。"
            "这里对 range(1, 6) 中的每个 n 计算 n*n，得到 5 个平方数。"
        )
    if "字典推导式" in cleaned:
        return "字典推导式用一行表达式生成新字典，结构是 {key: value for ...}。"
    if "冒泡排序" in cleaned:
        return (
            "冒泡排序通过两两比较相邻元素，把最大的逐步冒泡到末尾。"
            "外层循环控制轮数，内层循环控制本轮比较的上界。"
        )
    if "装饰器" in cleaned:
        return (
            "装饰器是一个返回新函数的函数，用来在不修改原函数代码的前提下增加额外行为。"
            "这里 announce 在调用 add 之前先打印一行日志。"
        )
    if "斐波那契" in cleaned or "递归" in cleaned:
        return (
            "递归函数在函数体里调用自己。"
            "fib(n) = fib(n-1) + fib(n-2)，并以 fib(0)=0, fib(1)=1 为终止条件。"
        )
    if "lambda" in cleaned or "函数式" in cleaned:
        return (
            "lambda 用来快速定义小函数，常与 map / filter / reduce 配合使用，"
            "把列表按某种规则整体转换或聚合。"
        )
    if "异常" in cleaned or "try" in cleaned:
        return (
            "try/except 让程序在出错时不崩溃。"
            "这里把 8/0 拦截为'no division by zero'，避免 ZeroDivisionError。"
        )
    if "if" in cleaned and "分支" in cleaned:
        return "if/elif/else 按条件分支执行；只要第一个满足的条件命中，下面的分支就跳过。"
    return (
        f"这是一段关于「{topic}」的可运行示例。"
        "你可以在输入框继续问它的每一行、把它改写成其它写法，或者让我基于它出一道练习题。"
    )


async def _llm_generate(topic: str, language: str, instruction: str) -> Optional[str]:
    """Skill-driven LLM generation of a runnable example (primary path).

    The LLM is constrained to a single ``<code>...</code>`` block and a
    final explanation.  The code block is sanitised against the sandbox
    before being returned, so an LLM that mentions ``os`` or ``subprocess``
    will not reach the executor.  Skipped entirely under the mock provider so
    offline mode keeps its deterministic template behaviour.
    """
    from config import settings as app_settings

    if language != "python" or str(app_settings.LLM_PROVIDER).lower() == "mock":
        return None
    skill_directive = ""
    try:
        from services.skill_library import get_skill, skill_directive as _render
        skill = get_skill("code-lab-authoring")
        if skill is not None:
            skill_directive = _render([skill]) + "\n\n"
    except Exception:  # noqa: BLE001 - skill load failure must not block generation
        logger.warning("code generator: skill load failed", exc_info=True)
    try:
        llm = await get_llm_service()
    except Exception as exc:  # pragma: no cover - factory failures are recovered by the caller
        logger.warning("code generator: llm factory failed: %s", exc)
        return None
    prompt = (
        skill_directive
        + "你是「柿小吉」AI 学习助手。请为「"
        + topic
        + "」生成一段简短、可运行的 Python 示例代码。\n"
        "约束：\n"
        "1. 只返回 1 个代码块，包在 ```python 和 ``` 之间；\n"
        "2. 代码不超过 30 行；\n"
        "3. 必须能直接执行并向 stdout 打印可读结果；\n"
        "4. 不允许任何 import；只能使用 Python 基础语法和内置函数，确保与当前安全执行沙箱一致；\n"
        "5. 不要打开文件、不要读网络；\n"
        "6. 代码块之外只允许一行简短中文解释，不要使用 Markdown 标题。\n"
        + ("\n用户原始需求：" + instruction if instruction else "")
    )
    try:
        text = extract_llm_text(await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=900,
        ))
    except Exception as exc:
        logger.warning("code generator: llm call failed: %s", exc)
        return None
    match = re.search(r"```python\s*(.+?)```", text, re.S)
    if not match:
        match = re.search(r"```\s*(.+?)```", text, re.S)
    if not match:
        return None
    candidate = match.group(1).strip()
    if not candidate:
        return None
    # Sanity check: re-parse with the executor's AST scan before accepting.
    from services.code_executor import _scan_for_forbidden  # local import to avoid cycle
    try:
        _scan_for_forbidden(candidate)
    except Exception:
        return None
    return candidate


async def generate_python_code(topic: str, language: str = "python", instruction: str = "") -> dict:
    """Return ``{title, source, explanation, language, knowledge_points, run}``.

    ``run`` is the *real* sandboxed execution result for the generated
    source.  We never invent stdout/stderr — if execution fails the
    caller will see the actual error message.

    Generation is LLM-first under the code-lab-authoring skill; the bundled
    topic templates are only an offline fallback for when no real provider is
    configured or the model call fails.
    """
    topic_text = (str(topic or "").strip()) or "代码示例"
    body = None
    if language == "python":
        body = await _llm_generate(topic_text, language, instruction)
    if body is None:
        body = _lookup_template(topic_text)
    if body is None:
        # Last resort: a tiny no-op so the executor still returns real stdout.
        body = "print('没有可用代码示例')\n"
    run = run_python(body)
    return {
        "title": _title_for(topic_text),
        "language": language,
        "source": run.source or body,
        "explanation": _explanation_for(topic_text),
        "knowledge_points": list(extract_knowledge_points(body, topic_text)),
        "line_explanations": explain_python_lines(body),
        "run": run.to_dict(),
    }


def rerun_python_code(source: str, language: str = "python") -> CodeRunResult:
    """Re-execute the saved source and return a fresh result."""
    if language != "python":
        return CodeRunResult(
            language=language, source=source or "", stdout="", stderr="",
            exit_code=0, timed_out=False, duration_ms=0, output_truncated=False,
            source_truncated=False, error_category="unsupported_language",
            error_message=f"暂不支持运行 {language} 代码", line_count=max(1, (source or "").count("\n")),
            started_at="", finished_at="",
        )
    return run_python(source or "")


def _strip_code_fence(text: str) -> str:
    cleaned = re.sub(r"```python\s*", "", text, flags=re.I)
    cleaned = re.sub(r"```\s*", "", cleaned)
    return cleaned.strip("\n")


async def _llm_revise(source: str, instruction: str, language: str) -> Optional[str]:
    if language != "python":
        return None
    try:
        llm = await get_llm_service()
    except Exception as exc:
        logger.warning("code revise: llm factory failed: %s", exc)
        return None
    prompt = (
        "你是「柿小吉」AI 学习助手。下面的 Python 代码是当前产物；"
        "请按用户的要求**只做修改**，并把改后的完整代码包在 ```python 和 ``` 之间返回。\n"
        "不要添加示例以外的解释；不允许任何 import；不要打开文件；不要联网。\n\n"
        "用户修改要求：" + instruction + "\n\n"
        "当前代码：\n```python\n" + source + "\n```"
    )
    try:
        text = extract_llm_text(await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=1500,
        ))
    except Exception as exc:
        logger.warning("code revise: llm call failed: %s", exc)
        return None
    match = re.search(r"```python\s*(.+?)```", text, re.S)
    if not match:
        match = re.search(r"```\s*(.+?)```", text, re.S)
    if not match:
        return None
    candidate = match.group(1).strip()
    if not candidate:
        return None
    from services.code_executor import _scan_for_forbidden
    try:
        _scan_for_forbidden(candidate)
    except Exception:
        return None
    return candidate


_RULE_REVISIONS = (
    (
        re.compile(r"改成\s*while(?:\s*循环)?|while\s*循环|换成\s*while|用\s*while"),
        "while 循环",
        (
            "i = 1\n"
            "while i < 6:\n"
            "    print('hello', i)\n"
            "    i += 1\n"
            "print('done')\n"
        ),
    ),
)


def _apply_rule_revision(source: str, instruction: str) -> Optional[str]:
    for pattern, _label, body in _RULE_REVISIONS:
        if pattern.search(instruction):
            return body
    return None


async def revise_python_code(source: str, instruction: str, language: str = "python") -> dict:
    """Modify a saved code artifact and re-run it.

    Returns ``{source, explanation, line_explanations, run}``.  We prefer
    deterministic rules (e.g. "改成 while") so common edits never depend
    on the LLM.  Anything else is forwarded to the LLM with strict
    sandbox-friendly constraints.
    """
    body = _apply_rule_revision(source or "", instruction or "")
    if body is None and language == "python":
        body = await _llm_revise(source or "", instruction, language)
    if body is None:
        # Re-emit the original so the caller can see the executor actually
        # ran their code (no fake success).
        body = source or "print('保持原样')\n"
    run = run_python(body)
    return {
        "source": run.source or body,
        "explanation": _explanation_for("for 循环") if "while" in (body or "") and "for" not in (body or "") else None,
        "knowledge_points": list(extract_knowledge_points(body, instruction or "")),
        "line_explanations": explain_python_lines(body),
        "run": run.to_dict(),
    }


def build_code_qa_context(source: str, run: dict) -> str:
    """Build a system-side context for the chat completion that will answer
    a question about the current code artifact.  The content is a
    deterministic summary of the saved source + its last run output.
    """
    lines = (source or "").splitlines()
    summary_lines = [
        "【当前会话的代码产物】",
        "language: python",
        f"line_count: {len(lines)}",
        "--- source ---",
        source or "",
        "--- last run ---",
        "exit_code: " + str((run or {}).get("exit_code", "")),
        "stdout: " + str((run or {}).get("stdout", ""))[:1500],
        "stderr: " + str((run or {}).get("stderr", ""))[:600],
    ]
    return "\n".join(summary_lines)
