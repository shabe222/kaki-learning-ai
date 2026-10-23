"""Content quality gate for generated teaching artifacts.

`tide_artifact_review` answers one question: *may we assert this?* — it is a
safety reviewer about unsupported claims. It cannot tell an expert handout from
a well-formatted page of filler, which is why every artifact used to leave the
generator with a hardcoded 0.96 and no measurement behind it.

This module is the missing question: *does this actually teach the thing the
learner asked about, the way someone who knows the subject would?* It answers
in two layers:

* a deterministic pre-scan that needs no model and cannot be argued with —
  leftover placeholders ("概念1"), filler phrases, a repeated sentence, a quiz
  whose stem contains its own answer, and the topic itself never appearing;
* a teaching-quality reviewer that scores topic fidelity, expert depth,
  structure fit and specificity, and returns concrete constraints the
  generator can actually act on in a bounded revision round.

Both layers feed the same `{verdict, issues[], scores{}}` shape, so a caller can
run them through one bounded loop and record what was measured instead of what
was hoped for.
"""
from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Mapping, Sequence

KIND_HANDOUT = "handout"
KIND_PRESENTATION = "presentation"
KIND_QUESTIONS = "questions"
KIND_VIDEO_SCRIPT = "video_script"
# Markdown 讲义（workspace_lecture）。它没有块结构，段落就是它的最小单位。
KIND_LECTURE = "lecture"
KINDS = (KIND_HANDOUT, KIND_PRESENTATION, KIND_QUESTIONS, KIND_VIDEO_SCRIPT, KIND_LECTURE)

Issue = dict[str, str]

# 填充语：这些话谁都能说，说明这一句没有承载任何主题信息。
_FILLER = (
    "值得注意的是", "简单来说", "简单地说", "综上所述", "众所周知", "不难发现",
    "不言而喻", "总而言之", "总的来说", "显而易见", "众所周知的是", "如上所述",
)
# 占位符：模板没被真正填过。QuestionAgent 的兜底选项就是「概念1」这种。
_PLACEHOLDER = re.compile(
    r"(?:概念|要点|示例|例子|步骤|选项|主题|题目)\s*[一二三四五六七八九\d]\s*(?=[、。，,；;：:）)\]】]|$)"
    r"|(?:TODO|FIXME|XXX|lorem ipsum)"
    r"|(?:待补(?:充|全)|此处省略|略去不表)"
    r"|【\s*】|\{\{|\}\}|<主题>|<topic>|xxx+",
    re.I,
)
# 主题词里必须丢掉的疑问词和虚词：它们是提问方式，不是学科内容。
_TOPIC_STOPWORDS = {
    "怎么", "如何", "什么", "为什么", "是否", "哪些", "哪个", "怎样", "多少",
    "讲讲", "说说", "解释", "一下", "的话", "以及", "并且", "但是", "因为",
    "所以", "我们", "你们", "它们", "这个", "那个", "一个", "可以", "需要",
    "到底", "其实", "就是", "还是", "或者", "以及", "关于", "对于", "通过",
}
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_+#.\-]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
# 说话方式不是主题：把"用黑板讲讲"当成主题词，会让一份完全跑题的作品侥幸过关。
_INSTRUCTION_PREFIX = re.compile(
    r"^(?:帮我|请你|请|用黑板|换成黑板|换个切面|黑板|讲讲|讲一讲|讲下|讲|说说|说说看|说一下|聊聊|解释一下|解释|分析一下|分析|介绍一下|介绍|列一下|写一写|来一段)+"
)
# 跨这些字的二字碎片不是术语，只是切分留下的渣。
_FUNCTION_CHARS = set("的与和及了是在对为以而或之其也不很更最")


def _flat(value: Any, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _sentence_list(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*|\n+", text) if part.strip()]


# ---------------------------------------------------------------- topic terms


def topic_terms(topic: str) -> list[str]:
    """The words an artifact about this topic has to actually contain.

    Latin terms and CJK words longer than one character, minus the question
    words ("为什么", "怎么") that describe the *ask* rather than the subject.
    Deliberately forgiving: this is a floor against a deck that is about
    nothing, not a vocabulary test.
    """
    text = _flat(topic, 400)
    terms: list[str] = []
    for match in _LATIN.findall(text):
        term = match.strip(".-_+#")
        if len(term) >= 2:
            terms.append(term)
    for run in _CJK_RUN.findall(text):
        for piece in _cjk_terms(run):
            terms.append(piece)
    unique: list[str] = []
    for term in terms:
        if term and term not in unique and term not in _TOPIC_STOPWORDS:
            unique.append(term)
    return unique[:12]


def _cjk_terms(run: str) -> list[str]:
    """Words inside one CJK run, with the question words cut out.

    「闭包为什么需要」不是一个词：把「为什么」当分隔符切开，留下「闭包」。
    同理，长串用二字词兜底，但丢掉「的合」「与分」这类跨虚词的碎片 ——
    它们既不是术语，出现与否也说明不了任何事。
    """
    pieces: list[str] = []
    for chunk in re.split("|".join(map(re.escape, sorted(_TOPIC_STOPWORDS, key=len, reverse=True))), run):
        chunk = _INSTRUCTION_PREFIX.sub("", chunk).strip("：:、，,的了")
        if not chunk or chunk in _TOPIC_STOPWORDS:
            continue
        if len(chunk) <= 8:
            pieces.append(chunk)
        for index in range(0, len(chunk) - 1, 2):
            piece = chunk[index:index + 2]
            if piece not in _TOPIC_STOPWORDS and not (_FUNCTION_CHARS & set(piece)):
                pieces.append(piece)
    return pieces


def _topic_coverage(text: str, terms: Sequence[str]) -> tuple[list[str], list[str]]:
    """Which topic words the artifact uses, and which it never mentions."""
    lowered = text.lower()
    present = [term for term in terms if term.lower() in lowered]
    missing = [term for term in terms if term.lower() not in lowered]
    return present, missing


# ------------------------------------------------------------------- readers


def artifact_lines(kind: str, candidate: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Visible text of an artifact as ``(location, text)`` pairs.

    One reader per kind keeps the checks below kind-agnostic: a handout block,
    a slide and a quiz item are all just "some text at some address".
    """
    lines: list[tuple[str, str]] = []

    def push(location: str, value: Any, limit: int = 4000) -> None:
        text = _flat(value, limit)
        if text:
            lines.append((location, text))

    if kind == KIND_HANDOUT:
        push("title", candidate.get("title"), 300)
        for index, block in enumerate(candidate.get("blocks") or []):
            if not isinstance(block, Mapping):
                continue
            data = block.get("data") if isinstance(block.get("data"), Mapping) else block
            push(f"block {index + 1} ({_flat(block.get('type'), 20)})", (data or {}).get("text"))
        return lines

    if kind == KIND_PRESENTATION:
        push("deck title", candidate.get("title"), 300)
        for index, slide in enumerate(candidate.get("slides") or []):
            if not isinstance(slide, Mapping):
                continue
            where = f"page {index + 1}"
            push(where, slide.get("title"), 300)
            push(where, slide.get("subtitle"), 300)
            push(where, slide.get("body"))
            push(where, slide.get("narration"))
            for item in slide.get("items") or []:
                push(where, item, 400)
        return lines

    if kind == KIND_QUESTIONS:
        push("quiz title", candidate.get("title") or candidate.get("goal"), 300)
        for index, question in enumerate(_question_items(candidate)):
            where = f"question {index + 1}"
            stem = _flat(question.get("question") or question.get("stem"), 800)
            push(where, stem)
            for option in question.get("options") or []:
                push(where, option, 300)
        return lines

    if kind == KIND_VIDEO_SCRIPT:
        push("title", candidate.get("title"), 300)
        push("script", candidate.get("script"))
        for point in candidate.get("key_points") or []:
            push("key point", point, 300)
        return lines

    if kind == KIND_LECTURE:
        markdown = str(candidate.get("content") or candidate.get("markdown") or "")
        push("title", candidate.get("title"), 300)
        for index, block in enumerate(re.split(r"\n\s*\n", markdown)):
            push(f"段 {index + 1}", block, 2000)
        return lines

    raise ValueError(f"unknown artifact kind: {kind}")


def _question_items(candidate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Quiz items may hang off `questions`, `items` or slides' `interaction`."""
    direct = candidate.get("questions")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, Mapping)]
    items = candidate.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, Mapping)]
    from_slides: list[Mapping[str, Any]] = []
    for slide in candidate.get("slides") or []:
        if isinstance(slide, Mapping) and isinstance(slide.get("interaction"), Mapping):
            from_slides.append(slide["interaction"])
    return from_slides


# -------------------------------------------------------------------- pre-scan


def _issue(category: str, severity: str, location: str, description: str, constraint: str,
           why: str = "") -> Issue:
    return {
        "category": category,
        "severity": severity,
        "location": location,
        "description": description,
        "why_it_matters": why or "这类问题会让产物看起来像套模板，而不是为这个主题写的内容。",
        "evidence_status": "OBSERVED",
        "recommended_constraint": constraint,
    }


def prescan(kind: str, candidate: Mapping[str, Any], topic: str = "") -> list[Issue]:
    """Deterministic defects. No model, no judgement call — these are facts."""
    lines = artifact_lines(kind, candidate)
    joined = "\n".join(text for _, text in lines)
    issues: list[Issue] = []

    flagged_placeholders: set[str] = set()
    for location, text in lines:
        for phrase in _FILLER:
            if phrase in text:
                issues.append(_issue(
                    "filler", "low", location,
                    f"出现填充语「{phrase}」。",
                    f"删掉「{phrase}」，把这一句改成对该主题成立的具体判断。",
                ))
                break
        placeholder = _PLACEHOLDER.search(text)
        # 同一个位置只报一次：四个「概念N」选项是同一个毛病，不是四个毛病。
        if placeholder and location not in flagged_placeholders:
            flagged_placeholders.add(location)
            issues.append(_issue(
                "placeholder", "blocking", location,
                f"还有没被真正填过的占位内容「{placeholder.group(0)}」。",
                "把占位内容换成这个主题里的真实名词、数值或判断；不要保留模板字样。",
                "占位符出现在学习者眼前，等于告诉他这份东西是模板填出来的。",
            ))

    counts: dict[str, str] = {}
    for location, text in lines:
        # 封面跟整份产物的标题一样是正常的，不算重复。
        if location in {"title", "deck title", "quiz title"}:
            continue
        for sentence in _sentence_list(text):
            if len(sentence) < 12:
                continue
            if sentence in counts:
                issues.append(_issue(
                    "repetition", "medium", location,
                    f"与 {counts[sentence]} 一字不差地重复了同一句：「{sentence[:40]}」。",
                    "删掉重复的那一句，换成针对当前小节的新内容。",
                ))
            else:
                counts[sentence] = location

    terms = topic_terms(topic)
    if terms:
        present, missing = _topic_coverage(joined, terms)
        # 门槛故意低：这不是词汇测验，它只拦"整份东西可以贴到任何主题上"。
        # 主题短（可取的词少于四个）时只要求命中一个，漏掉的那一半交给评审去说。
        required = 1 if len(terms) < 4 else 2
        if not present:
            issues.append(_issue(
                "topic_fidelity", "blocking", "artifact",
                f"整份产物没有出现主题里的任何一个词（{('、'.join(terms[:6]))}）。",
                f"重写内容，使它明确围绕「{_flat(topic, 60)}」展开；每一节都要出现该主题的真实术语。",
                "没有一个主题词出现，说明这份产物可以贴到任何主题上。",
            ))
        elif len(present) < required:
            issues.append(_issue(
                "topic_fidelity", "high", "artifact",
                f"主题词只出现了 {len(present)} 个（{('、'.join(present))}），缺少：{'、'.join(missing[:6])}。",
                f"补上这些主题词所代表的内容：{('、'.join(missing[:6]))}；不要用更宽泛的说法替代。",
            ))

    issues.extend(_kind_checks(kind, candidate, lines))
    return issues


def _kind_checks(kind: str, candidate: Mapping[str, Any], lines: Sequence[tuple[str, str]]) -> list[Issue]:
    issues: list[Issue] = []

    if kind == KIND_HANDOUT:
        kinds = [str(block.get("type") or block.get("kind") or "").upper()
                 for block in (candidate.get("blocks") or []) if isinstance(block, Mapping)]
        if kinds and "EXAMPLE" not in kinds:
            issues.append(_issue(
                "expert_depth", "high", "artifact",
                "整份讲义没有任何 EXAMPLE —— 只有说法，没有可验算或可运行的东西。",
                "至少补一个 EXAMPLE：可运行的最小代码，或能一步一步算完的具体数值演算。",
                "没有例子的讲义是最容易由任何模型套出来的东西，也最难让人真的学会。",
            ))
        if kinds and "CALLOUT" not in kinds:
            issues.append(_issue(
                "expert_depth", "medium", "artifact",
                "整份讲义没有 CALLOUT —— 常见的错法没有被点出来。",
                "补一个 CALLOUT，写清「现象 → 为什么它会误导 → 正确的区分标准」。",
            ))
        if kinds and not (4 <= len(kinds) <= 6):
            issues.append(_issue(
                "structure_fit", "low", "artifact",
                f"内容块数量是 {len(kinds)}，方法论要求 4-6 块。",
                "把内容重新组织成 4-6 块，多余的分节合并，缺的补上。",
            ))

    if kind == KIND_PRESENTATION:
        slides = [s for s in (candidate.get("slides") or []) if isinstance(s, Mapping)]
        titles: dict[str, int] = {}
        for index, slide in enumerate(slides):
            where = f"page {index + 1}"
            kind_name = _flat(slide.get("type"), 20) or "content"
            title = _flat(slide.get("title"), 200)
            # 封面重复整份标题是应该的；只统计内容页之间撞标题。
            if title and kind_name != "title":
                titles[title] = titles.get(title, 0) + 1
            if kind_name != "title" and not _flat(slide.get("narration"), 100):
                issues.append(_issue(
                    "structure_fit", "high", where,
                    "这一页没有 narration，讲述人在这一页无话可讲。",
                    "为这一页补上自然的口播讲解，内容与页面一致。",
                ))
            items = [i for i in (slide.get("items") or []) if _flat(i, 200)]
            if len(items) > 5:
                issues.append(_issue(
                    "structure_fit", "medium", where,
                    f"这一页堆了 {len(items)} 条并列要点。",
                    "一页只留一个核心想法，把其余要点移到它的下一页或 narration 里。",
                ))
            if kind_name == "content" and not _flat(slide.get("title"), 200):
                issues.append(_issue(
                    "structure_fit", "medium", where,
                    "内容页没有标题，这一页讲什么看不出来。",
                    "给这一页写一个判断句标题。",
                ))
        for title, count in titles.items():
            if count > 1 and title:
                issues.append(_issue(
                    "repetition", "medium", "artifact",
                    f"有 {count} 页用了同一个标题「{title[:30]}」。",
                    "把重复的页合并或改成不同的判断句标题。",
                ))

    if kind == KIND_QUESTIONS:
        items = _question_items(candidate)
        if not items:
            issues.append(_issue(
                "artifact_integrity", "blocking", "artifact",
                "没有解析出任何题目。",
                "重新生成题目，确保每道题都有题干与选项。",
            ))
        for index, question in enumerate(items):
            where = f"question {index + 1}"
            stem = _flat(question.get("question") or question.get("stem"), 800)
            options = [_flat(option, 300) for option in (question.get("options") or []) if _flat(option, 300)]
            if len(options) < 3:
                issues.append(_issue(
                    "artifact_integrity", "high", where,
                    f"只有 {len(options)} 个选项。",
                    "给出 4 个互不相同的选项，且只有一个正确答案。",
                ))
            if len({option for option in options}) != len(options):
                issues.append(_issue(
                    "ambiguity", "high", where,
                    "选项之间有完全一样的项。",
                    "让每个选项互不相同；重复项会让正确答案不唯一。",
                ))
            correct = question.get("correct_index", question.get("answer"))
            if isinstance(correct, int) and options and not 0 <= correct < len(options):
                issues.append(_issue(
                    "artifact_integrity", "blocking", where,
                    f"正确答案下标 {correct} 不在选项范围内。",
                    "把正确答案下标改到选项范围内。",
                ))
            if isinstance(correct, int) and 0 <= correct < len(options):
                answer_text = options[correct]
                # 三字以上的正确项出现在题干里：短术语（「摩擦力」）本来就要在题干里
                # 出现才问得成问题，所以只记一条 medium，交给评审判断是不是真漏了答案；
                # 四个字以上的整句答案出现在题干里则是明确的泄露。
                if len(answer_text) >= 3 and answer_text in stem:
                    issues.append(_issue(
                        "answer_leakage", "high" if len(answer_text) >= 4 else "medium", where,
                        f"题干里出现了正确选项的文字「{answer_text}」。",
                        "改写题干：可以提到这个概念，但不要连同正确选项的完整说法一起给出。",
                        "题干自带答案，这道题就不再测量任何东西。",
                    ))
            if options and max(len(option) for option in options) >= 2 * max(1, min(len(option) for option in options)) + 8:
                issues.append(_issue(
                    "ambiguity", "medium", where,
                    "正确选项或某个选项的长度明显长于其余选项。",
                    "让各选项长度接近，正确项不要靠长度暴露自己。",
                ))

    if kind == KIND_LECTURE:
        markdown = str(candidate.get("content") or candidate.get("markdown") or "")
        headings = [line.strip() for line in markdown.splitlines() if re.match(r"^#{1,4}\s+\S", line)]
        if markdown.strip() and not headings:
            issues.append(_issue(
                "structure_fit", "high", "artifact",
                "整篇讲义没有任何小标题，学习者无法定位到某一节。",
                "按方法论给出小标题：核心内容、常见误区（至少两条）、总结与思考题。",
            ))
        # 误区那一节写了标题却只列一条，等于没写：方法论要求至少两条。
        # 条目要一直收到下一个标题为止 —— 标题和列表之间常常隔着一个空行，
        # 只看标题所在的那一段会把一份合格的讲义误判成没写误区。
        blocks = re.split(r"\n\s*\n", markdown)
        for index, block in enumerate(blocks):
            if not re.search(r"^#{1,4}.*(误区|易错|常见错误)", block, flags=re.M):
                continue
            entries = [line for line in block.splitlines() if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)]
            for following in blocks[index + 1:]:
                if re.search(r"^#{1,4}\s+\S", following, flags=re.M):
                    break
                entries.extend(
                    line for line in following.splitlines()
                    if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)
                )
            if len(entries) < 2:
                issues.append(_issue(
                    "expert_depth", "medium", f"段 {index + 1}",
                    f"误区一节只写了 {len(entries)} 条。",
                    "每条误区写成「现象 → 为什么它会误导 → 正确的判断标准」，至少两条。",
                ))

    if kind == KIND_VIDEO_SCRIPT:
        script = _flat(candidate.get("script"), 4000)
        points = [p for p in (candidate.get("key_points") or []) if _flat(p, 200)]
        # 讲稿是"宁短不长"的：时长约束是上限，不是下限。所以短只记一条提示，
        # 真正该拦的是跑题和套话，那两件事由预扫和评审分别负责。
        if len(script) < 40:
            issues.append(_issue(
                "specificity", "medium", "script",
                f"讲稿只有 {len(script)} 个字，能承载的信息很少。",
                "在不超出时长上限的前提下，把结论背后的依据补上。",
            ))
        if len(points) < 2:
            issues.append(_issue(
                "artifact_integrity", "high", "script",
                f"只有 {len(points)} 条要点。",
                "给出 2-6 条简短要点。",
            ))
        first = _sentence_list(script)[0] if _sentence_list(script) else ""
        if first and re.match(r"^(同学你好|大家好|你好)[，,]", first) and "？" not in first and "?" not in first:
            issues.append(_issue(
                "structure_fit", "medium", "script",
                "开头是自我介绍，不是钩子。",
                "把第一句改成问题或反差，直接落在主题上；自我介绍不是钩子。",
            ))

    return issues


# ------------------------------------------------------------------- reviewer

_TEACHING_RUBRIC = (
    "topic_fidelity", "expert_depth", "structure_fit", "specificity",
)


def _score(value: Any) -> int:
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError):
        return 0


def _extract_json(raw: Any) -> dict[str, Any]:
    text = str(raw or "").replace("\ufeff", "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("content reviewer returned no JSON object")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("content reviewer returned invalid JSON")
    return value


def normalize_teaching_review(value: Mapping[str, Any], *, round_number: int,
                              prescan_issues: Sequence[Issue]) -> dict[str, Any]:
    issues: list[Issue] = [dict(issue) for issue in prescan_issues]
    for raw in value.get("issues") or []:
        if not isinstance(raw, Mapping):
            continue
        description = _flat(raw.get("description"), 500)
        if not description:
            continue
        severity = _flat(raw.get("severity"), 20).lower()
        if severity not in {"low", "medium", "high", "blocking"}:
            severity = "medium"
        issues.append(_issue(
            _flat(raw.get("category"), 80) or "expert_depth",
            severity,
            _flat(raw.get("location"), 100) or "artifact",
            description,
            _flat(raw.get("recommended_constraint"), 600) or "按这一条意见重写对应位置的内容。",
            _flat(raw.get("why_it_matters"), 500),
        ))

    scores = {key: _score((value.get("scores") or {}).get(key, value.get(key))) for key in _TEACHING_RUBRIC}
    judged = any(score > 0 for score in scores.values())
    stated = _flat(value.get("verdict"), 20).lower()

    # 判定只由两件事决定：评审明确说"不能保留"，或者出现了高严重度问题。
    # 高严重度问题不代表只能推翻 —— 它是可改的，所以给一轮改写机会而不是直接毙掉。
    # 评审没给分数时（例如只跑了确定性预扫），只看问题，不凭空判分。
    if stated == "block":
        verdict = "block"
    elif any(issue["severity"] in {"high", "blocking"} for issue in issues):
        verdict = "revise"
    elif not judged:
        verdict = "pass"
    elif all(score >= 4 for score in scores.values()):
        verdict = "pass"
    else:
        verdict = "revise"

    # 没有模型评审时不许留一份看起来像"评了 0 分还通过"的记录：分数就是没量过。
    if not judged:
        scores = {}
    if verdict == "pass" and not judged:
        default_summary = "已按确定性检查复核通过（主题落点、占位符、重复、结构完整）。"
    elif verdict == "pass":
        default_summary = "这一版达到了可以交给学习者的水准。"
    else:
        default_summary = "这一版还需要按下面的约束收紧。"
    return {
        "verdict": verdict,
        "round": round_number,
        "summary": _flat(value.get("summary"), 600) or default_summary,
        "issues": issues[:24],
        "claims": [],
        "scores": scores,
        "measured": judged,
    }


async def review_teaching_quality(*, kind: str, topic: str, candidate: Mapping[str, Any],
                                  learning_goal: str = "", round_number: int = 1,
                                  prescan_issues: Sequence[Issue] = ()) -> dict[str, Any]:
    """Independent teaching-quality review. Reviewer sees content, never generator reasoning."""
    from services.llm_service import get_llm_service

    payload = {
        "artifact_kind": kind,
        "requested_topic": _flat(topic, 200),
        "learning_goal": _flat(learning_goal, 300),
        "candidate": candidate,
    }
    prompt = f"""你是柿子智学的教学内容评审。你不负责写内容，只判断这份产物够不够格交给学习者。
不要输出思维过程，只输出严格 JSON。

评判标准（按学科专家备课的水准，不是按"看起来完整"）：
1. topic_fidelity：每一节是不是真的在讲被要求的主题？**把主题换成另一个不相干的主题，有多少内容依然成立？** 成立得越多，分数越低。术语用错了、用泛了，扣到底。
2. expert_depth：有没有真正知道这个主题的人才会写进去的东西 —— 起作用的机制、成立的条件与边界、会出错的地方、一个能自己算完或跑通的例子。只有正确但空泛的表述，最多 2 分。
3. structure_fit：这个结构是为这个主题选的，还是任何主题都能套的模板？顺序是否由主题本身的逻辑决定？
4. specificity：用具体名词、数值、条件说话，还是用"相关概念""重要因素"这类可以贴到任何地方的词？

严格性要求：5 分表示"专家亲手编的、可以直接拿去上课"；4 分表示"可以交，只有小瑕疵"；3 分表示"及格但普通"；2 分表示"空泛或跑题"；0-1 分表示"必须重做"。**不要给面子分。**

**长度不等于深度，这一条最容易判错。** 本产品的作者方法论本身要求短：PPT 一页只讲一个想法、正文不超过 5 条要点；短视频讲稿明确"宁短不长"；讲义 4-6 块。所以：
- 一个 45 秒的讲稿、一份三页的 PPT、一段五块的讲义，只要每一句都落在**这个主题的机制、成立条件、边界或具体数值**上，expert_depth 就可以是 5 分；
- 反过来，长而空的东西 expert_depth 仍然是 1-2 分；
- 扣分要扣在"说了什么"，不是"说了多少"。判 specificity 时看有没有具体名词、数值、条件，而不是看字数。
- 只有当内容短到**无法承载它声称要讲的东西**时，才因为短而扣分，并在 issue 里写清缺的是哪一块。

普通对话能力不足以判断学科细节时，只判断可判断的维度，宁可只挑得出 3 个真问题，也不要凑数。

产物类型：{kind}
被要求的主题：{_flat(topic, 200) or '（未提供）'}
学习目标：{_flat(learning_goal, 300) or '（未提供）'}

候选产物：
{json.dumps(payload["candidate"], ensure_ascii=False)[:28000]}

机器预扫已发现的问题（不要重复列举，可以补充它们漏掉的）：
{json.dumps(list(prescan_issues), ensure_ascii=False)[:4000]}

输出：
{{
  "verdict":"pass|revise|block",
  "summary":"面向学习者的一句话结论，说清这一版够不够格",
  "issues":[{{"category":"topic_fidelity|expert_depth|structure_fit|specificity|clarity|artifact_integrity","severity":"low|medium|high|blocking","location":"具体位置，如 page 3 / block 2","description":"具体问题，引用产物里的原话","why_it_matters":"为什么这会妨碍学习","recommended_constraint":"交给生成器的明确改写要求"}}],
  "topic_fidelity":0,
  "expert_depth":0,
  "structure_fit":0,
  "specificity":0
}}

规则：
- 每个 issue 必须引用产物里的原话，否则不要写；
- **只报会改变学习者结论的问题。** 一处表述读起来别扭、但在结果里不可观测，就不是缺陷；
  把它写成 high / blocking 会把整份产物退回重写，代价由学习者承担。
  实测过一例（2026-09-22）：练习答案是 `[5,5,5,5]` 正序删除，答案写「下标 1 对应的是第二个 5」，
  评审判成「把第二个 5 和原第三个 5 写混了，会误导学生」——但四个元素取值相同，
  元素身份在结果里不可观测，这句话不可能误导任何人。而这条误判让这份讲义被退回，
  重写出来的那一版反而多了三个新问题。
  要挑就挑可验证的：下标算错、结论与过程矛盾、该有的步骤缺失、答案本身不对；
- recommended_constraint 必须是可以直接执行的重写要求，不要写"提升质量"这种话；
- 分数 0 到 5；
- 只输出 JSON。"""
    llm = await get_llm_service()
    raw = await llm.generate(prompt, max_tokens=2400, response_format="json")
    return normalize_teaching_review(
        _extract_json(raw), round_number=round_number, prescan_issues=prescan_issues,
    )


# ----------------------------------------------------------------- bounded loop

Reviser = Callable[..., Awaitable[Mapping[str, Any]]]


def _constraints(review: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for issue in review.get("issues") or []:
        if not isinstance(issue, Mapping):
            continue
        value = _flat(issue.get("recommended_constraint"), 600)
        if value and value not in result:
            result.append(value)
    return result[:12]


async def run_content_quality_gate(
    *, kind: str, candidate: Mapping[str, Any], topic: str = "", learning_goal: str = "",
    revise: Reviser | None = None, reviewer: Callable[..., Awaitable[dict[str, Any]]] = review_teaching_quality,
    max_rounds: int = 2,
) -> dict[str, Any]:
    """Pre-scan, review, and let the generator fix it — at most `max_rounds` times.

    The pre-scan runs every round and always contributes: a placeholder is a
    fact about the text, and no reviewer opinion may soften it.
    """
    maximum = max(1, min(int(max_rounds), 3))
    current = dict(candidate)
    rounds: list[dict[str, Any]] = []
    revised_rounds = 0

    for round_number in range(1, maximum + 1):
        scanned = prescan(kind, current, topic)
        review = await reviewer(
            kind=kind, topic=topic, candidate=current,
            learning_goal=learning_goal, round_number=round_number, prescan_issues=scanned,
        )
        rounds.append(review)
        if review.get("verdict") == "pass":
            break
        constraints = _constraints(review)
        if not constraints or revise is None or round_number >= maximum:
            break
        try:
            rewritten = await revise(
                existing_candidate=dict(current), constraints=constraints,
                prior_review=review, round_number=round_number,
            )
        except Exception:  # noqa: BLE001 - 改写失败不能把已经生成的产物一起带走
            break
        if not rewritten or json.dumps(dict(rewritten), ensure_ascii=False, sort_keys=True) == \
                json.dumps(current, ensure_ascii=False, sort_keys=True):
            break
        current = dict(rewritten)
        revised_rounds += 1

    final = rounds[-1] if rounds else {"verdict": "block", "issues": [], "scores": {}, "summary": "质量审阅未执行。"}
    return {
        "status": "passed" if final.get("verdict") == "pass" else "blocked",
        "candidate": current,
        "reviews": rounds,
        "verdict": final.get("verdict", "block"),
        "scores": final.get("scores") or {},
        "issues": final.get("issues") or [],
        "summary": final.get("summary") or "",
        "revision_rounds": revised_rounds,
        "max_rounds": maximum,
    }


def quality_receipt(result: Mapping[str, Any]) -> dict[str, Any]:
    """What actually got measured, for the artifact's own review record."""
    reviews = list(result.get("reviews") or [])
    final = reviews[-1] if reviews else {}
    scores = {key: int(value) for key, value in (final.get("scores") or {}).items()}
    measured = bool(final.get("measured")) and any(value > 0 for value in scores.values())
    return {
        "gate": "content_quality",
        "verdict": str(final.get("verdict") or "block"),
        "rounds": len(reviews),
        "revision_rounds": int(result.get("revision_rounds") or 0),
        # measured=False 时这里必须是空的：一份没有量过的产物不该带一组分数出场。
        "measured": measured,
        "scores": scores if measured else {},
        "blocking": [
            {
                "location": issue.get("location"),
                "description": issue.get("description"),
                # 评审按要求为每个问题写了一条「明确改写要求」，而回执以前把它丢掉了——
                # 于是修复路径永远只能告诉模型**哪里错**，从来没法告诉它**该写什么**。
                # 2026-09-22 实测的后果：第三版把同一个缺陷一字不差地又写了一遍，
                # 分数与阻断项和上一版完全相同，重试等于空转。
                "recommended_constraint": _flat(issue.get("recommended_constraint"), 300),
                "why_it_matters": _flat(issue.get("why_it_matters"), 200),
            }
            for issue in (final.get("issues") or [])
            if str(issue.get("severity")) in {"high", "blocking"}
        ][:6],
        "summary": _flat(final.get("summary"), 400),
    }
