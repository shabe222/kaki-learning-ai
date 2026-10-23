"""Compile a semantic scene into an ordered list of blackboard operations.

The model never emits a coordinate. It emits a *semantic* scene -- a title,
labelled items with a rough spatial role, relations between them, and the
occasional declarative figure. This module owns everything geometric: which
column an item lands in, how tall a card is, where an arrow leaves one box and
meets the next, and whether anything ended up off the board edge.

Invariants enforced here (each one is a failure the prototype actually hit):

* nothing is placed outside the board -- explicit coordinates are clamped and
  anchored elements flip sides before they overflow;
* side-length labels are measured from the drawn coordinates, never copied from
  the spec's positional order;
* rendered labels do not sit on top of one another;
* a schematic figure says so on the board.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping, Sequence

from services.blackboard import figures as F
from services.blackboard import geometry as G
from services.blackboard.model import BOARD, CHALK, Board, build_script, resolve_color, seed_for

LEFT_GROUPS = {"left", "before", "cause"}
RIGHT_GROUPS = {"right", "after", "effect"}

TITLE_SIZE = 46
LABEL_SIZE = 38
DESC_SIZE = 26
NOTE_SIZE = 22
TITLE_X = 80
TITLE_Y = 72
MARGIN_X = 90
MARGIN_BOTTOM = 60
COLUMN_GAP = 90
CARD_GAP = 34
LABEL_TO_DESC_GAP = 8
# 两块字之间必须留出的净空。estimate_text_size 与实际字体略有出入，贴着放的盒子
# 渲染出来就是压字，所以这里要的是一个真正的最小距离，而不是「重叠比例够小」。
TEXT_GAP = 10.0
MAX_DESC_LINES = 3
# 说明行数的下限目标与搜索上限是两件事。
#
# 3 行是「好看」的目标，不是「必须」的边界，以前却当成了硬上限：一栏卡片少、
# 说明又长时宽度被压窄，3 行装不下就只剩砍话一条路。2026-09-22 实测某块板
# 左栏 1 张卡只有 223px 宽，说明要 7 行，于是结论句「所以不会漏」根本没上板，
# 而同一高度带里中间那栏 4 张卡最多只要 3 行、有的是余量。
# 所以先按 3 行试，试不出来再往上放，而不是把句子截断。
#
# 上限本身也不能当边界用。原来取 8，而实测有一栏里一张卡的说明在 339px 宽下需要
# **11 行**；超过上限之后 `_truncates` 永远为真，整栏退回 3 行，把同栏两张本来只要
# 4 行和 6 行的卡一起砍了（2026-09-22 实测）。真正的边界不是行数，而是两条已经存在的
# 约束：字数预算（`DESC_CHAR_BUDGET`）和栏高（`total > available`）。所以这里只留一个
# 足够宽的天花板，免得搜索在病态输入上无界。
MAX_DESC_LINES_CEILING = 16
# 标签与标注注记的行数上限。原来都是 2 行，而实测有 4 个标签需要 3 行
# （「闭包 = 函数 + 捕获的绑定」这类，宽 236px、字号 38）——2 行装不下就砍，
# 渲染出来是「闭包 = inne / r + 捕获的 …」。3 行仍然紧凑，但一句话能写完。
MAX_LABEL_LINES = 3
MAX_NOTE_LINES = 3
# 说明字号。26 是设计值，后面两档只在「多写几行也写不完」时才动用：
# 它同时省宽度和高度，是唯一一根对两种短缺都有效的杠杆。
DESC_SIZES = (DESC_SIZE, 24, 22)
# 但这条放宽只给「卡片上的一句话」，不给「把一段正文塞进卡片」。
#
# 界限是量出来的，不是拍的：855 张真实卡片的说明里，p99 是 127 字、最大 238 字，
# 超过 160 的只有 5 张。所以 160 会把真实内容误判成正文——2026-09-22 实测那一栏
# 6 张卡里就有 1 张 238 字的练习卡，被误判后整栏跟着被砍。取 260：覆盖全部真实
# 内容，同时仍然挡住「一段正文冒充卡片」（单测里那张 300 字的重复文本）。
DESC_CHAR_BUDGET = 260
# Label sizes to try when a column runs out of height, largest first.
LABEL_SIZES = (LABEL_SIZE, 32, 28, 24)
FIGURE_WIDTH = 520
MAX_FIGURES_PER_ROW = 2
# Figures may take this share of the free height; the rest is for prose.
FIGURE_BAND_SHARE = 0.62
# Even a crowded board gives the figure this much, or it stops being a figure.
MIN_FIGURE_BAND = 300.0
# Below this a triangle is a doodle; the layout shrinks elsewhere instead.
MIN_FIGURE_SIZE = 260.0
# A figure's ink box (polygon + outward labels + caption) over its nominal size.
FIGURE_INK_RATIO = 1.55
# 内容在 1000 高写不下时依次尝试的高度。
#
# 上限必须留在横板范围内。原来一直排到 2200，于是"板子写不下就长高"变成了
# "板子长成一根竖条"：1600×2200 在对话区里按等比缩放，只会缩成中间一条窄板、
# 两边空出大片背景，比字小一点难看得多。实测有一块只有 6 个元素的板子也被顶到
# 2200，整块板子 55% 是空的。横板是这块板的形态，不是可以商量的参数。
#
# 2026-09-22 补 1400 一档。原来停在 1360 是必要的防线，但 1360 和 2200 之间原本
# 什么都没有，于是"再多写几行就能把话说完"这一类被判成治不了。1400 是横板契约
# 允许的上限（测试要求宽高比 ≥ 1.1，1600/1454 就是 1.1），再高就成竖条了。
# 代价函数是"问题最少优先、并列取更矮"，所以高度只在它真的买到完整性时才会被花掉。
BOARD_HEIGHT_STEPS = (1000.0, 1240.0, 1360.0, 1400.0)
ROOT_COLUMN_WIDTH = 980
# 按字数分栏宽时的下限：别把哪一栏压成一条缝，字挤成三两个字一行反而更差。
_WIDTH_FLOOR_SHARE = 0.18


def _is_wide(ch: str) -> bool:
    code = ord(ch)
    return 0x2E80 <= code <= 0x9FFF or 0x3000 <= code <= 0x303F or 0xFF00 <= code <= 0xFFEF


# 分句结束符。中文先看这几个，英文标点顺带认一下。
CLAUSE_ENDS = "，。；：、！？!?,;:."


def _clip_to_clause(text: str) -> str:
    """回退到最后一处分句边界；一处都没有就原样返回。

    在**保留下来的整段文字**里找，不只找最后一行：边界常常正好落在上一行的行尾，
    只看最后一行就等于没找。一处完整分句都留不下时（第一句本身就超预算），任何
    切法都是半句，那就保持原样、靠省略号说明。
    """
    boundary = max((index for index, ch in enumerate(text) if ch in CLAUSE_ENDS), default=-1)
    if boundary <= 0:
        return text
    return text[:boundary].rstrip(CLAUSE_ENDS)


def wrap_text(text: str, size: float, max_width: float, max_lines: int) -> str:
    """Wrap to the column width, CJK included, and stop at ``max_lines``."""
    raw = str(text or "")
    lines: list[str] = []
    for paragraph in raw.split("\n"):
        current = ""
        width = 0.0
        for ch in paragraph:
            step = size if _is_wide(ch) else size * 0.55
            if width + step > max_width and current:
                lines.append(current)
                current, width = "", 0.0
                if len(lines) >= max_lines:
                    break
            current += ch
            width += step
        if len(lines) >= max_lines:
            break
        if current:
            lines.append(current)
    if not lines:
        return ""
    kept = sum(len(line) for line in lines[:max_lines])
    kept = min(kept, len(raw.replace("\n", "")))
    if kept < len(raw.replace("\n", "")) and max_lines > 0:
        # A truncated card must not read as the whole sentence -- and it must not
        # stop in the middle of a clause either, which reads as if that were the
        # point. 读者会把「能不能忽略；能」当成句子的落点，所以宁可少留一点，
        # 也要停在分句边界上。
        clipped = _clip_to_clause("\n".join(lines[:max_lines]))
        kept_lines = [line for line in clipped.split("\n")]
        kept_lines[-1] = kept_lines[-1].rstrip("…") + "…"
        return "\n".join(kept_lines)
    return "\n".join(lines[:max_lines])


def _columns_for(items: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Group items into the columns they will be laid out in."""
    left = [item for item in items if str(item.get("group") or "") in LEFT_GROUPS]
    right = [item for item in items if str(item.get("group") or "") in RIGHT_GROUPS]
    middle = [item for item in items if item not in left and item not in right]
    if left and right:
        return [left, middle, right] if middle else [left, right]
    if left:
        return [left, middle] if middle else [left]
    if right:
        return [middle, right] if middle else [right]
    if len(items) < 3:
        return [list(items)]
    # Column-major: the eye goes down the first column and continues at the top
    # of the next, which is how a board is actually filled in.
    half = math.ceil(len(items) / 2)
    return [list(items[:half]), list(items[half:])]


def _line_cap(item: Mapping[str, Any], desc_lines: int) -> int:
    """这张卡这次最多能写几行说明。

    「多写几行把话说完」是给**一句话**的；一段正文（把整道练习塞进一张卡）仍然
    按短处理。这个判断必须**按卡片**做，不能按栏：按栏做的话，同栏里一张超长的卡
    会把无辜的邻居一起按回 3 行——2026-09-22 实测就是这么发生的，6 张卡那一栏里
    4 张被一张 217 字的卡拖得砍掉了结论。
    """
    description = str(item.get("description") or "").strip()
    if len(description) > DESC_CHAR_BUDGET:
        return min(desc_lines, MAX_DESC_LINES)
    return desc_lines


def _card_text(
    item: Mapping[str, Any], width: float, desc_lines: int, label_size: float,
    desc_size: float = DESC_SIZE,
) -> tuple[str, str]:
    """Wrap a card's two lines to the column. Labels wrap too.

    A label is written larger, so a short label in a narrow column overruns it
    just as easily as a long description does -- "3² + 4² = 9 + 16" printed
    straight across the next column is the failure this prevents.
    """
    label = wrap_text(str(item.get("label") or "").strip(), label_size, width, MAX_LABEL_LINES)
    description = str(item.get("description") or "").strip()
    cap = _line_cap(item, desc_lines)
    desc = wrap_text(description, desc_size, width, cap) if (description and cap) else ""
    return label, desc


def _card_height(label: str, description: str, label_size: float, desc_size: float = DESC_SIZE) -> float:
    height = G.estimate_text_size(label, label_size)["h"] if label else 0.0
    if description:
        height += LABEL_TO_DESC_GAP + G.estimate_text_size(description, desc_size)["h"]
    return height


def _column_need(column: Sequence[Mapping[str, Any]], width: float) -> float:
    """Height a column needs at the *smallest* legible setting."""
    return sum(
        _card_height(
            *_card_text(item, width, 0, LABEL_SIZES[-1], DESC_SIZES[-1]),
            LABEL_SIZES[-1], DESC_SIZES[-1],
        )
        for item in column
    ) + CARD_GAP * max(0, len(column) - 1)


def _truncates(
    column: Sequence[Mapping[str, Any]], width: float, desc_lines: int,
    desc_size: float = DESC_SIZE,
) -> bool:
    """这份设置会不会砍掉**本该写完整**的说明。

    超长卡（一段正文冒充卡片）本来就要标短，它不算「被砍」，更不能让它成为整栏的
    永久阻塞：2026-09-22 实测，一栏 6 张卡里有 1 张 238 字的卡，把它算进判定之后
    `_truncates` 永远为真，第二遍搜索永远不返回，于是**全部 6 张**都退回 3 行，
    截断反而从 4 处涨到 7 处。
    """
    if desc_lines <= 0:
        return False
    for item in column:
        description = str(item.get("description") or "").strip()
        if not description or len(description) > DESC_CHAR_BUDGET:
            continue
        full = wrap_text(description, desc_size, width, 99)
        got = wrap_text(description, desc_size, width, desc_lines)
        if len(got.replace("\n", "").rstrip("…")) < len(full.replace("\n", "")):
            return True
    return False


def _column_total(
    column: Sequence[Mapping[str, Any]], width: float, desc_lines: int,
    label_size: float, desc_size: float,
) -> float:
    return sum(
        _card_height(*_card_text(item, width, desc_lines, label_size, desc_size), label_size, desc_size)
        for item in column
    ) + CARD_GAP * max(0, len(column) - 1)


def _fit_card_sizes(
    column: Sequence[Mapping[str, Any]], width: float, available: float
) -> tuple[int, float, float]:
    """Pick a (description lines, label size, description size) that fits the column.

    取舍顺序是这里唯一有内容的一行：**先要话是完整的，再要大字号**。

    以前只是按遍历顺序取第一个放得下的，于是「字号 38 但砍掉半句」永远排在
    「字号 30 但话说得完」前面——板书说明停在「能…」就是这么来的（2026-09-20
    实测：中间一栏 4 张卡、413px 宽，只分到 2 行，而说明要 3 到 5 行）。
    完全没有说明可放仍然比把说明砍一半更糟，所以那一档排在最后。
    """
    truncated_fallback: tuple[int, float, float] | None = None
    for label_size in LABEL_SIZES:
        for desc_lines in range(MAX_DESC_LINES, 0, -1):
            total = _column_total(column, width, desc_lines, label_size, DESC_SIZE)
            if total > available:
                continue
            if not _truncates(column, width, desc_lines, DESC_SIZE):
                return desc_lines, label_size, DESC_SIZE
            if truncated_fallback is None:
                truncated_fallback = (desc_lines, label_size, DESC_SIZE)
    # 3 行内写不完，就多写几行。宽度是分栏时定的，这里动不了，所以「把话写完」
    # 只剩纵向这一个方向可用；`total > available` 仍然守着高度，放不下就继续找。
    # 一张超长的卡自己按 3 行处理（见 `_line_cap`），但不再拖累同一栏的邻居。
    for label_size in LABEL_SIZES:
        for desc_lines in range(MAX_DESC_LINES_CEILING, MAX_DESC_LINES, -1):
            total = _column_total(column, width, desc_lines, label_size, DESC_SIZE)
            if total > available:
                continue
            if not _truncates(column, width, desc_lines, DESC_SIZE):
                return desc_lines, label_size, DESC_SIZE
    # 再写不完，就把说明字号调小一档。这是最后一根杠杆，也是唯一一根能同时缓解
    # 「宽度窄」和「高度不够」的：行数受栏高限制、宽度受张数限制，而字号两边都省。
    # 2026-09-22 实测：4 块 10 张卡的板在横板高度上限内只有降到 24 才能把话写完
    # （26 时 4 处截断），24 比设计值 26 只小一点，仍然好读。
    for desc_size in DESC_SIZES[1:]:
        for label_size in LABEL_SIZES:
            for desc_lines in range(MAX_DESC_LINES_CEILING, MAX_DESC_LINES, -1):
                total = _column_total(column, width, desc_lines, label_size, desc_size)
                if total > available:
                    continue
                if not _truncates(column, width, desc_lines, desc_size):
                    return desc_lines, label_size, desc_size
    if truncated_fallback is not None:
        return truncated_fallback
    # 放不下任何说明，才轮到只剩标签这一档。
    for label_size in LABEL_SIZES:
        total = _column_total(column, width, 0, label_size, DESC_SIZE)
        if total <= available:
            return 0, label_size, DESC_SIZE
    return 0, LABEL_SIZES[-1], DESC_SIZE


def _fill_columns(
    board: Board,
    columns: Sequence[Sequence[Mapping[str, Any]]],
    *,
    top: float,
    left: float = MARGIN_X,
    right: float | None = None,
    widths: Sequence[float] | None = None,
) -> dict[str, dict[str, float]]:
    """Write each column's cards and return every card's bounding box."""
    right_edge = board.size["w"] - MARGIN_X if right is None else right
    usable_width = right_edge - left - COLUMN_GAP * (len(columns) - 1)
    if widths is None:
        widths = [usable_width / max(1, len(columns))] * len(columns)
    block_width = sum(widths) + COLUMN_GAP * (len(columns) - 1)
    origin_x = left + max(0.0, (right_edge - left - block_width) / 2)
    available = board.size["h"] - top - MARGIN_BOTTOM
    boxes: dict[str, dict[str, float]] = {}

    for column_index, column in enumerate(columns):
        if not column:
            continue
        column_width = widths[column_index]
        column_x = origin_x + sum(widths[:column_index]) + column_index * COLUMN_GAP
        desc_lines, label_size, desc_size = _fit_card_sizes(column, column_width, available)
        y = top
        for item in column:
            item_id = str(item.get("id"))
            label, description = _card_text(item, column_width, desc_lines, label_size, desc_size)
            label_element = board.add(
                {
                    "id": f"bb-{item_id}-label",
                    "type": "text",
                    "text": label,
                    "size": label_size,
                    "color": CHALK["amber"] if bool(item.get("emphasis")) else CHALK["white"],
                    "x": column_x,
                    "y": y,
                    "label": f"要点：{label[:16]}",
                }
            )
            label_box = board.layout_one(label_element)["bbox"]
            y = label_box["y"] + label_box["h"]
            # The card box hugs the ink, not the column: arrows should meet the
            # text and a circle should ring it, not the empty space beside it.
            card_box: dict[str, float] = dict(label_box)
            if description:
                y += LABEL_TO_DESC_GAP
                desc_element = board.add(
                    {
                        "id": f"bb-{item_id}-desc",
                        "type": "text",
                        "text": description,
                        "size": desc_size,
                        "color": CHALK["blue"],
                        "x": column_x,
                        "y": y,
                        "label": f"说明：{label[:16]}",
                    }
                )
                card_box = G.bbox_union(card_box, board.layout_one(desc_element)["bbox"]) or card_box
                y += G.estimate_text_size(description, desc_size)["h"]
            if bool(item.get("underline")):
                board.add(
                    {
                        "id": f"bb-{item_id}-ul",
                        "type": "annotate",
                        "style": "underline",
                        "targetId": label_element["id"],
                        "color": CHALK["green"],
                        "width": 3,
                        "label": "重点下划线",
                    }
                )
            boxes[item_id] = card_box
            y += CARD_GAP
    return boxes


def _clip(centre: Sequence[float], direction: Sequence[float], box: Mapping[str, float], *, outward: bool) -> tuple[float, float]:
    """Where a ray from the box centre leaves (outward) or enters that box.

    两条轴对齐的快路径以前只取正方向那一侧，把 outward 丢了：于是目标端的入点
    取到的还是它自己的右侧边，一条水平连线就从卡片右边穿进卡片里。2026-09-20
    实测的 bb-rel-2 就是这样从 (509,394) 画到 (1475,394)，横穿中间一栏和右栏正文。
    """
    if abs(direction[0]) < 1e-9:
        step = (box["h"] / 2) * (1.0 if direction[1] > 0 else -1.0)
        return (centre[0], centre[1] + (step if outward else -step))
    if abs(direction[1]) < 1e-9:
        step = (box["w"] / 2) * (1.0 if direction[0] > 0 else -1.0)
        return (centre[0] + (step if outward else -step), centre[1])
    scale = min((box["w"] / 2) / abs(direction[0]), (box["h"] / 2) / abs(direction[1]))
    sign = 1.0 if outward else -1.0
    return (centre[0] + direction[0] * scale * sign, centre[1] + direction[1] * scale * sign)


def _corners(box: Mapping[str, float]) -> list[tuple[float, float]]:
    return [
        (box["x"], box["y"]),
        (box["x"] + box["w"], box["y"]),
        (box["x"] + box["w"], box["y"] + box["h"]),
        (box["x"], box["y"] + box["h"]),
    ]


def _text_boxes(board: Board) -> list[dict[str, float]]:
    boxes = []
    for element in board.elements.values():
        if element["type"] != "text":
            continue
        box = board.layout_one(element)["bbox"]
        if box is not None:
            boxes.append(box)
    return boxes


# Where along an arrow a label is willing to sit, and how far off the line.
_LABEL_T_POSITIONS = (0.5, 0.4, 0.6, 0.32, 0.68, 0.24, 0.76)
_LABEL_SIDES = (1.0, -1.0)


def _place_label(
    board: Board,
    start: Sequence[float],
    end: Sequence[float],
    text: str,
    *,
    avoid: Sequence[Mapping[str, float]],
) -> dict[str, float] | None:
    """Find somewhere along the arrow this word can be written without covering ink.

    Returns None when nothing is free. A relationship without its one-word
    label is a smaller loss than a word smeared across a card the learner is
    in the middle of reading.
    """
    width = G.estimate_text_size(text, NOTE_SIZE)["w"]
    height = NOTE_SIZE * 1.34
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    norm = math.hypot(dx, dy) or 1.0

    for t in _LABEL_T_POSITIONS:
        point = (start[0] + dx * t, start[1] + dy * t)
        for side in _LABEL_SIDES:
            # Offset perpendicular to the arrow so the word sits beside the
            # line rather than on top of it.
            offset = side * (height / 2 + 8)
            centre = (point[0] - dy / norm * offset, point[1] + dx / norm * offset)
            box = {"x": centre[0] - width / 2, "y": centre[1] - height / 2, "w": width, "h": height}
            if not G.is_inside_board(box, board.size):
                continue
            if not G.keeps_clearance(box, avoid, TEXT_GAP):
                continue
            return box
    return None


def _connection(
    board: Board,
    source: Mapping[str, float],
    target: Mapping[str, float],
    *,
    element_id: str,
    label: str = "",
    kind: str = "arrow",
    color: str | None = None,
    width: float = 3,
    avoid: Sequence[Mapping[str, float]] = (),
) -> None:
    """An arrow from one card's edge to another's, clipped at both borders.

    两个在不相邻列上的卡片之间，直线通常要从中间那一栏身上压过去——画出来就是
    一条划过别人正文的线（2026-09-20 实测）。同 _place_label 的取舍：关系写成
    一个词，比把线画在字上损失小。所以直线被挡时只留标签，不留线。
    """
    source_centre = (source["x"] + source["w"] / 2, source["y"] + source["h"] / 2)
    target_centre = (target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)
    dx = target_centre[0] - source_centre[0]
    dy = target_centre[1] - source_centre[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return
    norm = math.hypot(dx, dy)
    start = _clip(source_centre, (dx, dy), source, outward=True)
    end = _clip(target_centre, (dx, dy), target, outward=False)
    gap = 12
    start = (start[0] + dx / norm * gap, start[1] + dy / norm * gap)
    end = (end[0] - dx / norm * gap, end[1] - dy / norm * gap)
    blocked = any(
        G.segment_vs_polygon(start, end, _corners(box))["crossings"] >= 2 for box in avoid
    )
    if blocked:
        if not label:
            return
    else:
        board.add(
            {
                "id": element_id,
                "type": "line" if kind == "line" else "arrow",
                "points": [[start[0], start[1]], [end[0], end[1]]],
                "color": resolve_color(color, CHALK["green"]),
                "width": width,
                "label": label or None,
            }
        )
        if not label:
            return
    spot = _place_label(board, start, end, label, avoid=_text_boxes(board))
    if spot is None:
        return
    board.add(
        {
            "id": f"{element_id}-label",
            "type": "text",
            "text": label,
            "size": NOTE_SIZE,
            "color": CHALK["green"],
            "x": spot["x"],
            "y": spot["y"],
            "label": "关系",
        }
    )


def _draw_figure_item(
    board: Board,
    item: Mapping[str, Any],
    *,
    top: float,
    size: float,
    origin_x: float | None = None,
    max_width: float | None = None,
) -> dict[str, float] | None:
    """Draw one declarative figure plus its caption; None if the spec is invalid."""
    item_id = str(item.get("id"))
    spec = dict(item["figure"])
    spec.setdefault("label", item.get("label"))
    # Nudged right of the column so outward-placed vertex letters still have
    # room before the board edge or the next figure.
    left = MARGIN_X + 40 if origin_x is None else origin_x + 40
    try:
        drawn = F.draw_figure(
            board,
            spec,
            origin=(left, top),
            size=size,
            color=spec.get("color") or CHALK["white"],
            label=str(item.get("label") or ""),
            id_prefix=f"bb-{item_id}-fig",
        )
    except F.FigureError:
        return None
    # Everything the figure put on the board, not just the polygon: vertex
    # letters, side lengths and the right-angle mark all live outside the
    # polygon's box, and the caption used to be written straight onto them.
    card = dict(drawn["box"])
    for group in ("shape", "labels", "sides", "marks"):
        for element_id in drawn["ids"].get(group) or []:
            element = board.get(element_id)
            if element is None:
                continue
            card = G.bbox_union(card, board.layout_one(element)["bbox"]) or card

    caption = str(item.get("description") or "").strip()
    if caption:
        caption_width = max(200.0, min(max_width or 720.0, card["w"]))
        caption_box = {
            "x": card["x"],
            "y": card["y"] + card["h"] + 26,
            "w": caption_width,
            "h": DESC_SIZE * 1.34 * 3,
        }
        captioned = wrap_text(caption, DESC_SIZE, caption_width, 3)
        board.add(
            {
                "id": f"bb-{item_id}-desc",
                "type": "text",
                "text": captioned,
                "size": DESC_SIZE,
                "color": CHALK["blue"],
                "x": caption_box["x"],
                "y": caption_box["y"],
                "label": f"说明：{str(item.get('label') or '图形')[:16]}",
            }
        )
        card = G.bbox_union(card, caption_box) or card
    return card


def _figure_size(board: Board, *, top: float, columns: int, budget: float) -> float:
    """How large a figure may be drawn.

    A figure's ink box -- polygon, outward vertex letters, side lengths and the
    caption -- is roughly 1.3x the nominal size. Two figures side by side have
    to share the width as well as the height, and whatever is left after them
    is what the prose gets. Sizing them independently is how the second figure
    ended up drawn on top of the first and the text pushed off the bottom edge.
    """
    slot = (board.size["w"] - MARGIN_X * 2 - COLUMN_GAP * (columns - 1)) / columns
    by_height = max(MIN_FIGURE_SIZE, budget / FIGURE_INK_RATIO)
    by_width = max(MIN_FIGURE_SIZE, slot - 150.0)
    return min(FIGURE_WIDTH, by_height, by_width)


def _column_widths(columns: Sequence[Sequence[Mapping[str, Any]]], usable: float) -> list[float]:
    """把横向空间按各栏要写的**卡片张数**分下去。

    等宽看着公平，其实是最不公平的那种：一栏 4 张卡、另一栏 2 张，两栏拿一样宽，
    4 张卡那栏就只剩 2 行说明可写。2026-09-20 实测的那块板就是这样——中间栏
    需要 15 行说明、只给得起 8 行，于是说明停在「能…」。

    **权重只看卡片张数，不看字数。** 按字数分更贴合需求，但那样一来改一句描述就
    会改动每一栏的宽度、把整块板重排一遍，而黑板的契约是「追问只改该改的元素，
    不重排其他部分」。张数是结构性的：改措辞它不动，加减一张卡它才动。

    比例的直觉：每栏的行数约等于「卡片张数 ÷ 列宽」再乘上各卡的平均长度，让张数多
    的栏拿到更多宽度，各栏的行数才会靠近。再给一个下限，别把哪一栏压成一条缝。
    """
    count = len(columns)
    if count <= 1:
        return [usable]
    floor = usable * _WIDTH_FLOOR_SHARE
    weights = [max(1.0, float(len(column))) for column in columns]
    total = sum(weights)
    widths = [usable * weight / total for weight in weights]
    # 触到下限的栏先钉住，剩下的空间按原比例重新分给其余各栏。
    for _ in range(count):
        pinned = [index for index, width in enumerate(widths) if width < floor]
        if not pinned:
            break
        for index in pinned:
            widths[index] = floor
        remaining = usable - floor * len(pinned)
        loose = [index for index in range(count) if index not in pinned]
        if not loose or remaining <= 0:
            break
        loose_total = sum(weights[index] for index in loose)
        for index in loose:
            widths[index] = remaining * weights[index] / loose_total
    return widths


def _text_bands(
    board: Board, text_items: Sequence[Mapping[str, Any]], left: float, right: float
) -> list[tuple[list[list[Mapping[str, Any]]], list[float]]]:
    """The column groups the prose will actually be written in, with widths.

    Roots get a band of their own above the rest, so this has to be the single
    source of truth for both laying the prose out and reserving room for it --
    reserving from a different grouping is how the words ran off the bottom.
    """
    if not text_items:
        return []
    roots = [item for item in text_items if str(item.get("group") or "") == "root"]
    rest = [item for item in text_items if item not in roots]
    bands: list[tuple[list[list[Mapping[str, Any]]], list[float]]] = []

    def widths_for(columns: Sequence[Sequence[Mapping[str, Any]]], max_width: float | None = None) -> list[float]:
        usable = right - left - COLUMN_GAP * (len(columns) - 1)
        widths = _column_widths(columns, max(1.0, usable))
        if max_width is not None:
            widths = [min(width, max_width) for width in widths]
        return widths

    if roots:
        columns = [roots]
        bands.append((columns, widths_for(columns, ROOT_COLUMN_WIDTH)))
        if rest:
            columns = _columns_for(rest)
            bands.append((columns, widths_for(columns)))
    else:
        columns = _columns_for(text_items)
        bands.append((columns, widths_for(columns)))
    return bands


def _prose_reserve(
    board: Board, text_items: Sequence[Mapping[str, Any]], *, left: float = MARGIN_X, right: float | None = None
) -> float:
    """Height the words need, at the smallest legible setting, before figures.

    A board of figures with nowhere to write the point is not an explanation,
    so figures are sized from what is left over rather than the other way round.
    """
    bands = _text_bands(board, text_items, left, board.size["w"] - MARGIN_X if right is None else right)
    total = 0.0
    for index, (columns, widths) in enumerate(bands):
        if index:
            total += 56
        total += max(
            (_column_need(column, widths[position]) for position, column in enumerate(columns)),
            default=0.0,
        )
    return total + 40 if bands else 0.0


def _place_figures(
    board: Board,
    figure_items: Sequence[Mapping[str, Any]],
    boxes: dict[str, dict[str, float]],
    *,
    top: float,
    reserve: float = 0.0,
) -> float:
    """Draw figures in rows of at most two; returns the y the prose starts at."""
    if not figure_items:
        return top
    rows: list[list[Mapping[str, Any]]] = []
    for index in range(0, len(figure_items), MAX_FIGURES_PER_ROW):
        rows.append(list(figure_items[index : index + MAX_FIGURES_PER_ROW]))
    free = board.size["h"] - top - MARGIN_BOTTOM
    # The words come first; figures take what is left, up to a share of the
    # board, and never less than MIN_FIGURE_BAND.
    band_budget = max(min(MIN_FIGURE_BAND, free), min(free * FIGURE_BAND_SHARE, free - reserve))
    row_budget = max(120.0, band_budget / len(rows))
    cursor = top
    for row in rows:
        size = _figure_size(board, top=cursor, columns=MAX_FIGURES_PER_ROW, budget=row_budget)
        columns = len(row)
        slot = (board.size["w"] - MARGIN_X * 2 - COLUMN_GAP * (columns - 1)) / columns
        row_bottom = cursor
        for index, item in enumerate(row):
            origin_x = MARGIN_X + index * (slot + COLUMN_GAP)
            card = _draw_figure_item(board, item, top=cursor, size=size, origin_x=origin_x, max_width=slot)
            if not card:
                continue
            boxes[str(item.get("id"))] = card
            row_bottom = max(row_bottom, card["y"] + card["h"])
        cursor = row_bottom + 56
    return cursor


def _place_text(
    board: Board,
    text_items: Sequence[Mapping[str, Any]],
    boxes: dict[str, dict[str, float]],
    *,
    top: float,
    left: float = MARGIN_X,
    right: float | None = None,
) -> None:
    """Lay the prose half of the board: hierarchy roots first, then columns."""
    if not text_items:
        return
    cursor = top
    right_edge = board.size["w"] - MARGIN_X if right is None else right
    for index, (columns, widths) in enumerate(_text_bands(board, text_items, left, right_edge)):
        if index:
            cursor += 56
        placed = _fill_columns(
            board, columns, top=cursor, left=left, right=right_edge, widths=widths
        )
        boxes.update(placed)
        cursor = max((box["y"] + box["h"] for box in placed.values()), default=cursor)


def compile_intent(
    intent: Mapping[str, Any] | None,
    *,
    board_size: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Compile a semantic scene into a render-ready board snapshot.

    When the scene cannot be written legibly inside the nominal board, the board
    gets a little taller rather than the writing running off the bottom edge --
    up to a limit that still reads as a blackboard rather than a column.

    The choice is "fewest problems, then shortest". Falling through to the
    tallest step was wrong: a violation that height cannot fix (an element past
    the right edge, a card truncated by column width) stays a violation at every
    height, so that loop grew the board to the maximum and left a portrait slab
    with more than half of it empty. Height is a remedy for vertical overflow
    and for nothing else, so it is only spent when it actually buys something.
    """
    if board_size is not None:
        return _compile_at(intent, {"w": float(board_size["w"]), "h": float(board_size["h"])})
    best: dict[str, Any] | None = None
    best_cost: int | None = None
    for height in BOARD_HEIGHT_STEPS:
        snapshot = _compile_at(intent, {"w": float(BOARD["w"]), "h": height})
        if not snapshot["elements"]:
            return snapshot
        cost = len(outside_board(snapshot)) + len(truncated_text(snapshot))
        if cost == 0:
            return snapshot
        # 严格小于：并列时保留更矮的那块，不为不存在的收益长高。
        if best_cost is None or cost < best_cost:
            best_cost, best = cost, snapshot
    return best if best is not None else _compile_at(intent, {"w": float(BOARD["w"]), "h": BOARD_HEIGHT_STEPS[0]})


def _compile_at(intent: Mapping[str, Any] | None, size: Mapping[str, float]) -> dict[str, Any]:
    board = Board({"w": float(size["w"]), "h": float(size["h"])})
    if not intent:
        return {"size": {"w": board.size["w"], "h": board.size["h"]}, "elements": []}

    items = [item for item in (intent.get("items") or []) if isinstance(item, Mapping)]
    title = str(intent.get("title") or "").strip()
    top = TITLE_Y
    if title:
        title_element = board.add(
            {
                "id": "bb-title",
                "type": "text",
                "text": title,
                "size": TITLE_SIZE,
                "color": CHALK["amber"],
                "x": TITLE_X,
                "y": TITLE_Y,
                "label": "标题",
            }
        )
        top = TITLE_Y + board.layout_one(title_element)["bbox"]["h"] + 46

    boxes: dict[str, dict[str, float]] = {}
    figure_items = [item for item in items if isinstance(item.get("figure"), Mapping)]
    text_items = [item for item in items if not isinstance(item.get("figure"), Mapping)]

    # A figure pushes its vertex and length labels outward, so it needs a little
    # more headroom than a text card does before the title above it.
    if figure_items and title:
        top += 34

    placed_side_by_side = False
    if len(figure_items) == 1 and text_items:
        # One figure plus explanation is the common teaching shape: the figure
        # is drawn on the left and the words are written beside it, the way a
        # person fills a board, rather than stacking two full-width bands.
        item = figure_items[0]
        card = _draw_figure_item(
            board,
            item,
            top=top,
            size=_figure_size(board, top=top, columns=2, budget=board.size["h"] - top - MARGIN_BOTTOM),
        )
        if card:
            boxes[str(item.get("id"))] = card
            _place_text(
                board,
                text_items,
                boxes,
                top=top,
                left=card["x"] + card["w"] + 110,
                right=board.size["w"] - MARGIN_X,
            )
            placed_side_by_side = True
        else:
            figure_items = []
    if not placed_side_by_side:
        top = _place_figures(
            board, figure_items, boxes, top=top, reserve=_prose_reserve(board, text_items)
        )
        _place_text(board, text_items, boxes, top=top)

    for index, relation in enumerate(intent.get("relations") or []):
        if not isinstance(relation, Mapping):
            continue
        source = boxes.get(str(relation.get("from") or ""))
        target = boxes.get(str(relation.get("to") or ""))
        if not source or not target:
            continue
        kind = "line" if str(relation.get("type") or "arrow") == "line" else "arrow"
        _connection(
            board,
            source,
            target,
            element_id=f"bb-rel-{index}",
            label=str(relation.get("label") or "").strip(),
            kind=kind,
            color=CHALK["pink"] if relation.get("emphasis") else None,
            width=5 if relation.get("emphasis") else 3,
            # 除了这两张卡自己，别的卡都不许被这条线穿过。
            avoid=[
                box
                for key, box in boxes.items()
                if key not in {str(relation.get("from") or ""), str(relation.get("to") or "")}
            ],
        )

    for index, annotation in enumerate(intent.get("annotations") or []):
        if not isinstance(annotation, Mapping):
            continue
        box = boxes.get(str(annotation.get("targetId") or ""))
        if not box:
            continue
        style = str(annotation.get("type") or "circle")
        if style not in {"circle", "underline", "highlight"}:
            style = "highlight" if style == "callout" else "circle"
        board.add(
            {
                "id": f"bb-ann-{index}",
                "type": "annotate",
                "style": style,
                "box": dict(box),
                "color": CHALK["pink"],
                "width": 3.8,
                "size": DESC_SIZE,
                "label": "标注",
            }
        )
        text = str(annotation.get("text") or "").strip()
        if text:
            wrapped = wrap_text(text, NOTE_SIZE, min(560.0, max(160.0, box["w"])), MAX_NOTE_LINES)
            height = G.estimate_text_size(wrapped, NOTE_SIZE)["h"]
            width = max(G.estimate_text_size(line, NOTE_SIZE)["w"] for line in wrapped.split("\n"))
            existing = _text_boxes(board)
            # Below the marked item reads best; beside it is the fallback; and if
            # even that is taken, the mark alone still carries the meaning.
            #
            # 余量比 TEXT_GAP 多 1px。以前这里写的是字面的 10，而检查要求 ≥ TEXT_GAP(10)
            # ——刀刃上，被 0.1px 的浮点漂移判成违规：2026-09-22 全量实测 60 对
            # 「卡片文字 ↔ 标注注记」的间隙是 9.9 或 10.0，视觉上完全正常，却全部
            # 记成净空不足。要的是「确实留出间距」，不是一个刚好的数字。
            note_gap = TEXT_GAP + 1
            for candidate in (
                {"x": box["x"], "y": box["y"] + box["h"] + note_gap, "w": width, "h": height},
                {"x": box["x"] + box["w"] + note_gap + 12, "y": box["y"], "w": width, "h": height},
                {"x": box["x"], "y": box["y"] - note_gap - height, "w": width, "h": height},
            ):
                if not G.is_inside_board(candidate, board.size):
                    continue
                if not G.keeps_clearance(candidate, existing, TEXT_GAP):
                    continue
                board.add(
                    {
                        "id": f"bb-ann-{index}-note",
                        "type": "text",
                        "text": wrapped,
                        "size": NOTE_SIZE,
                        "color": CHALK["pink"],
                        "x": candidate["x"],
                        "y": candidate["y"],
                        "label": "注",
                    }
                )
                break

    return {"size": {"w": board.size["w"], "h": board.size["h"]}, "elements": board.laid_out()}


def compile_script(
    intent: Mapping[str, Any] | None,
    *,
    previous_intent: Mapping[str, Any] | None = None,
    new_scene: bool = False,
    board_size: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Full pipeline: semantic scene in, ordered ``BlackboardOp[]`` out."""
    current = compile_intent(intent, board_size=board_size)
    previous = None if new_scene else (
        compile_intent(previous_intent, board_size=board_size) if previous_intent else None
    )
    if previous is not None and previous["elements"] == current["elements"]:
        # Nothing changed: emit no steps rather than replay a batch of no-op
        # animations over handwriting that is already correct. The element
        # snapshot still rides along so the message stays self-describing.
        return {"size": current["size"], "elements": current["elements"], "ops": [], "steps": []}
    script = build_script(previous, current)
    script["steps"] = narration_steps(script["ops"], intent)
    return script


def _flat(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _spoken(label: Any, detail: Any) -> str:
    """One spoken line out of a label and its explanation."""
    label = _flat(label).rstrip("。.；;")
    detail = _flat(detail).rstrip("。.；;")
    if label and detail:
        return f"{label}：{detail}。"
    if detail:
        return f"{detail}。"
    if label:
        return f"{label}。"
    return ""


def _scene_owners(intent: Mapping[str, Any]) -> list[tuple[str, str]]:
    """element id prefix -> the scene object that element belongs to."""
    owners: list[tuple[str, str]] = [("bb-title", "title")]
    for item in intent.get("items") or []:
        if isinstance(item, Mapping):
            item_id = _flat(item.get("id"))
            if item_id:
                owners.append((f"bb-{item_id}", f"item:{item_id}"))
    for index, relation in enumerate(intent.get("relations") or []):
        if isinstance(relation, Mapping):
            owners.append((f"bb-rel-{index}", f"rel:{index}"))
    for index, annotation in enumerate(intent.get("annotations") or []):
        if isinstance(annotation, Mapping):
            owners.append((f"bb-ann-{index}", f"ann:{index}"))
    # Longest prefix first. The trailing "-" rule below is what actually keeps
    # 「bb-si-1」 from claiming 「bb-si-10-label」's strokes.
    return sorted(owners, key=lambda pair: len(pair[0]), reverse=True)


def _step_sentence(
    key: str,
    intent: Mapping[str, Any],
    items: Mapping[str, Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
    annotations: Sequence[Mapping[str, Any]],
) -> str:
    """What a teacher says while the strokes of this step go up."""
    if key == "title":
        title = _flat(intent.get("title")).rstrip("。.")
        return f"先写标题——{title}。" if title else ""
    if key.startswith("item:"):
        item = items.get(key[len("item:"):])
        return _spoken(item.get("label"), item.get("description")) if item else ""
    if key.startswith("rel:"):
        relation = relations[int(key[len("rel:"):])]
        source = items.get(_flat(relation.get("from")))
        target = items.get(_flat(relation.get("to")))
        label = _flat(relation.get("label")).rstrip("。.")
        source_label = _flat(source.get("label")).rstrip("。.") if source else ""
        target_label = _flat(target.get("label")).rstrip("。.") if target else ""
        if source_label and target_label:
            return (
                f"{source_label} 到 {target_label}：{label}。" if label
                else f"{source_label} 连到 {target_label}。"
            )
        return f"{label}。" if label else ""
    if key.startswith("ann:"):
        annotation = annotations[int(key[len("ann:"):])]
        text = _flat(annotation.get("text")).rstrip("。.")
        if text:
            return f"这里标一下——{text}。"
        target = items.get(_flat(annotation.get("targetId")))
        target_label = _flat(target.get("label")).rstrip("。.") if target else ""
        return f"在「{target_label}」上标一下。" if target_label else "这里标一下。"
    return ""


def narration_steps(
    ops: Sequence[Mapping[str, Any]],
    intent: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Group the diff into ``[{narration, ops}]``: one spoken sentence per beat.

    The board used to go up in one silent batch. A person filling a board does
    not do that -- they write the title, say what it is, write the first point,
    say what that is. So each step pairs the sentence with exactly the strokes
    that sentence is about, and the browser alternates "say" and "draw" instead
    of dumping the whole board and then explaining it.

    The grouping is by *ownership*, not by counting ops: the title is a step,
    an item is a step together with its figure and its caption, one arrow is a
    step. A pair of strokes that belong to the same object never gets split
    into two sentences.
    """
    scene = dict(intent or {})
    owners = _scene_owners(scene)
    items = {
        _flat(item.get("id")): item
        for item in (scene.get("items") or [])
        if isinstance(item, Mapping)
    }
    relations = [r for r in (scene.get("relations") or []) if isinstance(r, Mapping)]
    annotations = [a for a in (scene.get("annotations") or []) if isinstance(a, Mapping)]

    def owner_of(element_id: str) -> str:
        for prefix, key in owners:
            if element_id == prefix or element_id.startswith(prefix + "-"):
                return key
        return f"other:{element_id}"

    steps: list[dict[str, Any]] = []
    pending_erase: list[dict[str, Any]] = []

    for op in ops:
        kind = str(op.get("kind") or "")
        if not kind:
            continue
        if kind in {"clear", "remove"}:
            # 擦除没有自己的句子：板子擦干净是下一句话的起手式，不是一句话。
            # 板子上已有内容时它跟在上一句之后；板子还空着时它并进下一句。
            if steps:
                steps[-1]["ops"].append(dict(op))
            else:
                pending_erase.append(dict(op))
            continue
        if kind == "pointer":
            key, narration = "pointer", ""
        else:
            element = op.get("element")
            element_id = _flat(element.get("id")) if isinstance(element, Mapping) else ""
            key = owner_of(element_id) if element_id else f"other:{len(steps)}"
            narration = _step_sentence(key, scene, items, relations, annotations)
        here = pending_erase + [dict(op)]
        pending_erase = []
        if steps and steps[-1]["_key"] == key:
            steps[-1]["ops"].extend(here)
        else:
            steps.append({"narration": narration, "ops": here, "_key": key})

    if pending_erase:
        if steps:
            steps[-1]["ops"].extend(pending_erase)
        else:
            steps.append({"narration": "", "ops": pending_erase, "_key": "transition"})

    for step in steps:
        step.pop("_key", None)
    return steps


def digest_of(snapshot: Mapping[str, Any]) -> str:
    """Text view of a compiled board, for prompts and for tests."""
    lines = [
        f"板面 {int(snapshot['size']['w'])}×{int(snapshot['size']['h'])}，共 {len(snapshot['elements'])} 个元素"
    ]
    for element in snapshot["elements"]:
        box = element["bbox"]
        lines.append(
            f"- {element['id']} {element.get('label') or element['type']} "
            f"@x{round(box['x'])}-{round(box['x'] + box['w'])}, y{round(box['y'])}-{round(box['y'] + box['h'])}"
        )
    return "\n".join(lines)


def truncated_text(snapshot: Mapping[str, Any]) -> list[str]:
    """Written text that had to be cut short to fit.

    A card whose explanation ends in an ellipsis loses the part that explains
    *why*, which is the part a learner came for. Measured on a real board:
    four of twenty-five blocks read "因为查找顺序是本…" -- the reason, gone.
    The board may grow; it may not swallow the end of a sentence.
    """
    return [
        element["id"]
        for element in snapshot["elements"]
        if element["type"] == "text" and str(element.get("render", {}).get("text") or "").endswith("…")
    ]


def outside_board(snapshot: Mapping[str, Any], tolerance: float = 1.0) -> list[str]:
    """Ids of elements whose solved box leaves the board."""
    width = snapshot["size"]["w"]
    height = snapshot["size"]["h"]
    offenders: list[str] = []
    for element in snapshot["elements"]:
        box = element["bbox"]
        if (
            box["x"] < -tolerance
            or box["y"] < -tolerance
            or box["x"] + box["w"] > width + tolerance
            or box["y"] + box["h"] > height + tolerance
        ):
            offenders.append(element["id"])
    return offenders


def overlapping_pairs(snapshot: Mapping[str, Any], *, ratio: float = 0.30) -> list[tuple[str, str]]:
    """Pairs of written things that sit on top of each other.

    Only vertex letters are exempt: they are placed outward from a figure and
    are allowed to graze the shape they belong to. Everything else -- cards,
    relation words, annotations, side lengths -- has to stay legible.
    """
    texts = [
        element
        for element in snapshot["elements"]
        if element["type"] == "text"
        and not str(element.get("label") or "").startswith("顶点")
    ]
    pairs = []
    for index, first in enumerate(texts):
        for second in texts[index + 1 :]:
            if G.bbox_overlap(first["bbox"], second["bbox"])["ratio"] > ratio:
                pairs.append((first["id"], second["id"]))
    return pairs


SIDE_LABEL_PATTERN = re.compile(r"^(?P<prefix>.*)-fig-s(?P<edge>\d+)$")


def measure_label_consistency(snapshot: Mapping[str, Any], *, tolerance: float = 0.02) -> list[str]:
    """Check every drawn side label against the distance actually drawn.

    The drawn figure is scaled to fit the board, so the label text cannot be
    compared to pixels directly. What must hold is that **one scale explains
    every edge**: drawn_i / claimed_i is the same for all i. A label written
    from a hand-written positional mapping breaks that immediately -- which is
    exactly how the prototype shipped two swapped legs and a shifted SSS edge
    while every nominal number in the request was correct.
    """
    problems: list[str] = []
    polygons = {
        element["id"][: -len("-fig-shape")]: element
        for element in snapshot["elements"]
        if element["type"] == "polygon" and element["id"].endswith("-fig-shape")
    }
    if not polygons:
        return problems

    for prefix, polygon in polygons.items():
        points = polygon["render"]["points"]
        labels: dict[int, dict[str, Any]] = {}
        for element in snapshot["elements"]:
            matched = SIDE_LABEL_PATTERN.match(str(element["id"]))
            if matched and matched.group("prefix") == prefix:
                labels[int(matched.group("edge"))] = element
        if not labels:
            continue
        if len(labels) != len(points):
            problems.append(f"{prefix} 边长标注 {len(labels)} 个，但图形有 {len(points)} 条边")

        if any(element["id"] == f"{prefix}-fig-note" for element in snapshot["elements"]):
            # Schematic drawing: proportions are deliberately relaxed and the
            # board says so, so only the label's completeness is checkable here.
            continue

        scales: list[float] = []
        for edge, element in sorted(labels.items()):
            text = str(element["render"].get("text") or "")
            _pair, _, claimed_text = text.partition("=")
            try:
                claimed = float(claimed_text)
            except ValueError:
                problems.append(f"{element['id']} 的标注不是可读的数值：{text}")
                continue
            if claimed <= 0:
                problems.append(f"{element['id']} 的标注不是正数：{text}")
                continue
            start = points[edge % len(points)]
            end = points[(edge + 1) % len(points)]
            scales.append(math.dist(start, end) / claimed)
        if len(scales) < 2:
            continue
        spread = (max(scales) - min(scales)) / max(scales)
        if spread > tolerance:
            detail = ", ".join(
                f"{str(labels[edge]['render'].get('text'))}→{round(scale, 1)}"
                for edge, scale in zip(sorted(labels), scales)
            )
            problems.append(f"{prefix} 的边长标注与实测长度不成同一比例（{detail}）")
    return problems


def seed_consistency(snapshot: Mapping[str, Any]) -> bool:
    """Every element must carry the deterministic seed of its own id."""
    return all(element.get("seed") == seed_for(str(element.get("id"))) for element in snapshot["elements"])


def iter_elements(script: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for op in script.get("ops") or []:
        element = op.get("element")
        if isinstance(element, Mapping):
            yield element
