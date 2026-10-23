"""Board state: element truth, anchor resolution, and the ops the frontend runs.

The layout is solved here, never in the browser. Two reasons the prototype
insisted on this: the frontend stays thin, and what the model reasons about is
the same board the learner sees -- no "client computed one layout, server
computed another" drift.

Element ids and chalk seeds are derived deterministically from the semantic
plan, so recompiling the same intent produces byte-identical elements. That is
what lets ``diff_ops`` tell "this box moved" apart from "this whole board is
new" and keep the eraser animation honest.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from services.blackboard import geometry as G

CHALK = {
    "white": "#f3f5f0",
    "amber": "#ffd98a",
    "blue": "#a8d3f5",
    "pink": "#f5a8b8",
    "green": "#b6e2a1",
}

BOARD = {"w": 1600, "h": 1000, "margin": 70}

POINT_TYPES = {"stroke", "line", "polyline", "polygon", "arrow"}

DEFAULT_WIDTH = 3.4


def resolve_color(value: Any, fallback: str | None = None) -> str:
    """Accept either a palette name or a literal CSS colour."""
    text = str(value or "").strip()
    if not text:
        return fallback or CHALK["white"]
    return CHALK.get(text.lower(), text)


def seed_for(text: str) -> int:
    """Stable chalk seed: same element id, same handwriting, every render."""
    digest = hashlib.blake2b(str(text).encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % 100000


class Board:
    """Ordered element store with server-side geometry."""

    def __init__(self, size: Mapping[str, float] | None = None) -> None:
        self.size = {"w": float((size or BOARD)["w"]), "h": float((size or BOARD)["h"])}
        self.elements: dict[str, dict[str, Any]] = {}

    # ---------- mutation ----------

    def add(self, element: Mapping[str, Any]) -> dict[str, Any]:
        el = dict(element)
        el.setdefault("id", f"bb-{len(self.elements) + 1}")
        el.setdefault("type", "text")
        el.setdefault("source", "ai")
        el.setdefault("color", CHALK["white"])
        el.setdefault("width", DEFAULT_WIDTH)
        el.setdefault("seed", seed_for(str(el["id"])))
        if el["type"] in POINT_TYPES and el.get("points"):
            el["points"] = [[float(x), float(y)] for x, y in el["points"]]
        self.elements[el["id"]] = el
        return el

    def get(self, element_id: str) -> dict[str, Any] | None:
        return self.elements.get(element_id)

    def remove(self, element_id: str) -> dict[str, Any] | None:
        return self.elements.pop(element_id, None)

    def clear(self) -> None:
        self.elements.clear()

    # ---------- geometry ----------

    def raw_bbox(self, el: Mapping[str, Any]) -> dict[str, float] | None:
        kind = el.get("type")
        if el.get("type") in POINT_TYPES and el.get("points"):
            return G.bbox_of_points(el["points"])
        if kind == "text":
            size = float(el.get("size") or 30)
            extent = G.estimate_text_size(str(el.get("text") or ""), size)
            return {"x": float(el.get("x") or 0), "y": float(el.get("y") or 0), "w": extent["w"], "h": extent["h"]}
        if kind == "circle":
            r = float(el.get("r") or 0)
            return {"x": el["cx"] - r, "y": el["cy"] - r, "w": r * 2, "h": r * 2}
        return None

    def anchor_delta(self, el: Mapping[str, Any]) -> tuple[float, float]:
        """Resolve an anchor into a translation, flipping sides before it leaves the board."""
        anchor = el.get("anchor")
        if not anchor:
            return 0.0, 0.0
        target = self.elements.get(str(anchor.get("to") or ""))
        if target is None:
            return 0.0, 0.0
        target_box = self.layout_one(target)["bbox"]
        if target_box is None:
            return 0.0, 0.0
        own = self.raw_bbox(el)
        if own is None:
            return 0.0, 0.0
        gap = float(anchor.get("gap") or 36)

        def place(side: str) -> tuple[float, float]:
            if side == "right":
                tx, ty = target_box["x"] + target_box["w"] + gap, target_box["y"]
            elif side == "left":
                tx, ty = target_box["x"] - gap - own["w"], target_box["y"]
            elif side == "below":
                tx, ty = target_box["x"], target_box["y"] + target_box["h"] + gap
            elif side == "above":
                tx, ty = target_box["x"], target_box["y"] - gap - own["h"]
            elif side == "center":
                tx = target_box["x"] + target_box["w"] / 2 - own["w"] / 2
                ty = target_box["y"] + target_box["h"] / 2 - own["h"] / 2
            else:
                tx, ty = own["x"], own["y"]
            return (
                tx - own["x"] + float(anchor.get("dx") or 0),
                ty - own["y"] + float(anchor.get("dy") or 0),
            )

        order = [str(anchor.get("side") or "below"), "below", "right", "left", "above", "center"]
        for side in dict.fromkeys(order):
            dx, dy = place(side)
            box = {"x": own["x"] + dx, "y": own["y"] + dy, "w": own["w"], "h": own["h"]}
            if G.is_inside_board(box, self.size):
                return dx, dy
        # Nothing fits on any side: clamp back inside rather than push the label
        # off the edge. This is the failure the prototype looped on six times.
        dx, dy = place(str(anchor.get("side") or "below"))
        box = {"x": own["x"] + dx, "y": own["y"] + dy, "w": own["w"], "h": own["h"]}
        adjust_x, adjust_y = G.clamp_box_inside(box, self.size)
        return dx + adjust_x, dy + adjust_y

    def layout_one(self, el: Mapping[str, Any]) -> dict[str, Any]:
        """Solve into render-ready geometry: coordinates already translated."""
        dx, dy = self.anchor_delta(el)
        kind = el.get("type")
        render: dict[str, Any] = {}
        bbox: dict[str, float] | None = None

        if kind in POINT_TYPES and el.get("points"):
            points = [[x + dx, y + dy] for x, y in el["points"]]
            render["points"] = points
            bbox = G.bbox_of_points(points)
        elif kind == "circle":
            r = float(el.get("r") or 0)
            render.update(cx=float(el["cx"]) + dx, cy=float(el["cy"]) + dy, r=r)
            bbox = {"x": render["cx"] - r, "y": render["cy"] - r, "w": r * 2, "h": r * 2}
        elif kind == "text":
            own = self.raw_bbox(el)
            size = float(el.get("size") or 30)
            render.update(
                x=own["x"] + dx,
                y=own["y"] + dy,
                text=str(el.get("text") or ""),
                size=size,
                align=el.get("align") or "left",
            )
            bbox = {"x": render["x"], "y": render["y"], "w": own["w"], "h": own["h"]}
        elif kind == "annotate":
            box = el.get("box")
            # 只有靠 targetId 找目标时才有「未外扩的原始盒」；显式给了 box 的那条
            # 路径（模型的 annotations）没有，锚点就只能是这个盒自己。
            target_box = None
            if box:
                tb = G.round_rect(box, 8)
            else:
                target = self.elements.get(str(el.get("targetId") or ""))
                target_box = self.layout_one(target)["bbox"] if target else None
                tb = G.round_rect(target_box, 12) if target_box else None
            if tb is not None:
                style = str(el.get("style") or "circle")
                render["style"] = style
                render["size"] = float(el.get("size") or 34)
                if style == "circle":
                    render.update(cx=tb["x"] + tb["w"] / 2, cy=tb["y"] + tb["h"] / 2, rx=tb["w"] / 2, ry=tb["h"] / 2)
                else:
                    # 下划线要落在标签与说明之间那道缝里，所以纵向锚点用**没有外扩**
                    # 的标签盒。以前两个间距叠在一起：round_rect 把标签盒上下各外扩
                    # 12px，再 +6，线就落到了标签下方 18px 处；而标签与说明只隔
                    # LABEL_TO_DESC_GAP(8px)，于是线横穿说明的第一行。
                    # 2026-09-22 实测：si-7 的线在 y=1073.1，说明从 y=1063.2 开始。
                    anchor = target_box if style == "underline" and target_box else tb
                    offset = (
                        anchor["y"] + anchor["h"] + 4
                        if style == "underline"
                        else tb["y"] + tb["h"] * 0.55
                    )
                    render["points"] = [[tb["x"], offset], [tb["x"] + tb["w"], offset]]
                bbox = tb
        return {"render": render, "bbox": bbox}

    def laid_out(self) -> list[dict[str, Any]]:
        out = []
        for el in self.elements.values():
            solved = self.layout_one(el)
            if solved["bbox"] is None:
                continue
            out.append(
                {
                    "id": el["id"],
                    "type": el["type"],
                    "source": el.get("source"),
                    "color": el.get("color"),
                    "width": el.get("width"),
                    "seed": el.get("seed"),
                    "label": el.get("label"),
                    "render": solved["render"],
                    "bbox": G.round_box(solved["bbox"]),
                }
            )
        return out

    def occupied_boxes(self) -> list[dict[str, float]]:
        boxes = []
        for el in self.elements.values():
            box = self.layout_one(el)["bbox"]
            if box is not None:
                boxes.append(box)
        return boxes

    def occupancy_grid(self, cols: int = 16, rows: int = 10) -> list[list[bool]]:
        cw = self.size["w"] / cols
        ch = self.size["h"] / rows
        grid = [[False] * cols for _ in range(rows)]
        for box in self.occupied_boxes():
            c0 = max(0, int(box["x"] // cw))
            c1 = min(cols - 1, int((box["x"] + box["w"] - 0.001) // cw))
            r0 = max(0, int(box["y"] // ch))
            r1 = min(rows - 1, int((box["y"] + box["h"] - 0.001) // ch))
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    grid[r][c] = True
        return grid

    def occupancy_map(self, cols: int = 16, rows: int = 10) -> str:
        """A text picture of the board: far cheaper than a screenshot."""
        grid = self.occupancy_grid(cols, rows)
        tens = "".join(str(i)[0] if i >= 10 else "0" for i in range(cols))
        ones = "".join(str(i % 10) for i in range(cols))
        cw = round(self.size["w"] / cols)
        ch = round(self.size["h"] / rows)
        body = "\n".join(
            f"y={round(r * self.size['h'] / rows):>4} " + "".join("#" if c else "·" for c in row)
            for r, row in enumerate(grid)
        )
        return f"板面占用图（每格 {cw}×{ch}，# 已占，· 空）：\n     {tens}\n     {ones}\n{body}"

    def space_report(self, cols: int = 20, rows: int = 12) -> dict[str, Any]:
        grid = self.occupancy_grid(cols, rows)
        free = sum(1 for row in grid for c in row if not c)
        rect = G.largest_free_rect(grid)
        cw = self.size["w"] / cols
        ch = self.size["h"] / rows
        return {
            "ratio": free / (cols * rows),
            "box": (
                {
                    "x": round(rect["x"] * cw),
                    "y": round(rect["y"] * ch),
                    "w": round(rect["w"] * cw),
                    "h": round(rect["h"] * ch),
                }
                if rect
                else None
            ),
        }

    def digest(self) -> str:
        """Compact board summary -- the model's main channel for "seeing" it."""
        items = self.laid_out()
        space = self.space_report()
        room = (
            f"空余 {round(space['ratio'] * 100)}%，最大一块连续空地约 "
            f"{space['box']['w']}×{space['box']['h']} 在 ({space['box']['x']}, {space['box']['y']})"
            if space["box"]
            else "板面几乎写满了"
        )
        if not items:
            return f"板面 {int(self.size['w'])}×{int(self.size['h'])}，空的，什么都没有。{room}"
        lines = []
        for item in items:
            box = item["bbox"]
            what = item["label"] or item["type"]
            if item["type"] == "text":
                text = str(item["render"].get("text") or "").replace("\n", "⏎")
                what = f"文字「{text[:34]}」"
            lines.append(
                f"- {item['id']} {what} @x{round(box['x'])}-{round(box['x'] + box['w'])}"
                f", y{round(box['y'])}-{round(box['y'] + box['h'])}"
            )
        return f"板面 {int(self.size['w'])}×{int(self.size['h'])}，已有 {len(items)} 个元素。{room}\n" + "\n".join(lines)


def diff_ops(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Turn two compiled snapshots into an ordered op list.

    Unchanged elements emit nothing, so a follow-up that only moves one box
    leaves the rest of the handwriting alone instead of rewriting the board.
    A brand new scene starts with ``clear`` so stale chalk is actually erased.
    """
    ops: list[dict[str, Any]] = []
    if not previous:
        ops.append({"kind": "clear"})
    before = {el["id"]: el for el in (previous or {}).get("elements", [])}
    after = {el["id"]: el for el in current.get("elements", [])}
    for element_id, element in before.items():
        if element_id not in after:
            ops.append({"kind": "remove", "id": element_id})
    for element_id, element in after.items():
        if element_id not in before:
            ops.append({"kind": "add", "element": element})
        elif before[element_id] != element:
            ops.append({"kind": "update", "element": element})
    return ops


def build_script(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    pointer: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """The payload a message carries.

    ``ops`` are the incremental steps the browser animates. ``elements`` is the
    whole board after them, so a message stays readable on its own: when the
    panel mounts empty -- reopened history, a voice call starting mid-thread --
    it can seed the board without replaying every turn that came before.
    """
    ops = diff_ops(previous, current)
    if pointer:
        ops.append({"kind": "pointer", "bbox": dict(pointer)})
    return {"size": current.get("size"), "elements": list(current.get("elements") or []), "ops": ops}
