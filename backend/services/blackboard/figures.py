"""Declarative figure drawing.

The model says *what* figure it wants ("a right triangle, right angle at C,
legs 3 and 4") and this module works out every coordinate: the shape itself,
where each vertex letter goes, where each side length is written, and the
right-angle / equal-side marks.

Two rules the prototype paid for twice:

1. **Side lengths are always measured from the coordinates**, never read off a
   hand-written positional mapping. A mapping breaks the moment vertex order
   changes -- which is exactly how the two legs got swapped once and how the
   SSS branch got shifted once.
2. **The right angle comes from construction, not from post-hoc alignment.**
   The right vertex sits at the origin with the two legs along the axes, so it
   is 90 degrees by definition.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from services.blackboard import geometry as G
from services.blackboard.character import CHARACTER_KINDS, CHARACTER_POSES, draw_character
from services.blackboard.model import CHALK, Board, resolve_color

FIGURE_KINDS = {"right_triangle", "triangle", "isosceles", "square", "circle"} | set(CHARACTER_KINDS)


class FigureError(ValueError):
    """The requested figure cannot be drawn truthfully."""


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_figure(spec: Any) -> tuple[str, ...]:
    """Checks that do not need geometry; used before the plan is accepted."""
    if not isinstance(spec, Mapping):
        return ("figure must be an object",)
    errors: list[str] = []
    kind = str(spec.get("kind") or "")
    if kind not in FIGURE_KINDS:
        return ("figure.kind must be one of: " + ", ".join(sorted(FIGURE_KINDS)),)
    if kind in CHARACTER_KINDS:
        # 人物没有「数值真值」要核：单位空间里的比例是写死的，姿态由 kind 决定。
        return ()
    if kind == "right_triangle":
        legs = spec.get("legs") or [3, 4]
        if not isinstance(legs, Sequence) or len(legs) != 2 or any((_number(v, 0) or 0) <= 0 for v in legs):
            errors.append("right_triangle needs two positive legs, e.g. [3,4]")
    elif kind in {"triangle", "isosceles"}:
        sides = spec.get("sides") or [3, 4, 5]
        if not isinstance(sides, Sequence) or len(sides) != 3 or any((_number(v, 0) or 0) <= 0 for v in sides):
            errors.append("triangle needs three positive sides, e.g. [3,4,5]")
        else:
            sa, sb, sc = (float(v) for v in sides)
            if sa + sb <= sc or sa + sc <= sb or sb + sc <= sa:
                errors.append(f"{sa:g}、{sb:g}、{sc:g} 这三条边组不成三角形（两边之和要大于第三边）")
            elif kind == "isosceles" and abs(sa - sb) > 1e-6 and abs(sb - sc) > 1e-6 and abs(sa - sc) > 1e-6:
                errors.append("isosceles needs two equal sides, e.g. [5,5,6]")
    elif kind == "square":
        if (_number(spec.get("side"), 4) or 0) <= 0:
            errors.append("square needs a positive side")
    elif kind == "circle":
        if (_number(spec.get("r"), 200) or 0) <= 0:
            errors.append("circle needs a positive radius")
    mode = spec.get("mode")
    if mode is not None and str(mode) not in {"exact", "schematic"}:
        errors.append("figure.mode must be exact or schematic")
    labels = spec.get("labels")
    if labels is not None and not isinstance(labels, Sequence):
        errors.append("figure.labels must be an array")
    return tuple(errors)


def _solve(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Unit-space vertices plus the length each edge label should read."""
    kind = str(spec["kind"])
    mode = "schematic" if str(spec.get("mode")) == "schematic" else "exact"
    labels = [str(v) for v in (spec.get("labels") or [])]
    points: list[list[float]] | None = None
    lengths: list[float] | None = None
    right_at = -1
    circle = None

    if kind == "circle":
        circle = {"r": _number(spec.get("r"), 200) or 200}
    elif kind == "square":
        a = _number(spec.get("side"), 4) or 4
        points = [[0.0, 0.0], [a, 0.0], [a, a], [0.0, a]]
        lengths = [a, a, a, a]
    elif kind == "right_triangle":
        legs = [_number(v, 0) or 0 for v in (spec.get("legs") or [3, 4])]
        p, q = float(legs[0]), float(legs[1])
        names = labels or ["A", "B", "C"]
        requested = str(spec.get("right_angle_at") or "C")
        right_at = names.index(requested) if requested in names else min(2, len(names) - 1)
        if right_at < 0 or right_at > 2:
            right_at = 2
        others = [i for i in range(3) if i != right_at]
        # Board y grows downward: legs point right and up, so the right angle
        # sits bottom-left, the way it is drawn in a textbook.
        points = [[0.0, 0.0] for _ in range(3)]
        points[right_at] = [0.0, 0.0]
        points[others[0]] = [p, 0.0]
        points[others[1]] = [0.0, -q]
        lengths = [
            math.hypot(points[(i + 1) % 3][0] - points[i][0], points[(i + 1) % 3][1] - points[i][1])
            for i in range(3)
        ]
    else:
        spec_sides = [float(_number(v, 0) or 0) for v in (spec.get("sides") or [3, 4, 5])]
        errors = validate_figure({**dict(spec), "sides": spec_sides})
        if errors:
            raise FigureError(errors[0])
        mean = sum(spec_sides) / 3
        # Schematic keeps the true labels but relaxes the proportions halfway to
        # equilateral: a very flat triangle is unreadable on a blackboard.
        sides = [mean + (v - mean) * 0.5 for v in spec_sides] if mode == "schematic" else list(spec_sides)
        a, b, c = sides  # a = BC (opposite A), b = CA (opposite B), c = AB (opposite C)
        points = [[0.0, 0.0] for _ in range(3)]
        points[1] = [0.0, 0.0]  # B
        points[2] = [a, 0.0]  # C
        x = (c * c + a * a - b * b) / (2 * a)
        y = math.sqrt(max(1e-6, c * c - x * x))
        points[0] = [x, -y]  # A above BC
        lengths = [
            spec_sides[i] if mode == "schematic"
            else math.hypot(points[(i + 1) % 3][0] - points[i][0], points[(i + 1) % 3][1] - points[i][1])
            for i in range(3)
        ]
    return {"kind": kind, "mode": mode, "points": points, "lengths": lengths, "right_at": right_at, "circle": circle}


def draw_figure(
    board: Board,
    spec: Mapping[str, Any],
    *,
    origin: Sequence[float] | None = None,
    size: float | None = None,
    color: Any = None,
    label: str | None = None,
    id_prefix: str = "fig",
) -> dict[str, Any]:
    """Append a fully annotated figure to ``board``; return the ids it created."""
    errors = validate_figure(spec)
    if errors:
        raise FigureError(errors[0])
    if str(spec["kind"]) in CHARACTER_KINDS:
        return draw_character(
            board,
            spec,
            origin=origin,
            size=size,
            color=color,
            label=label,
            id_prefix=id_prefix,
        )
    solved = _solve(spec)
    width = float(_number(spec.get("size"), None) or size or 520)
    requested = str(spec.get("label") or label or "")
    show_lengths = spec.get("show_lengths") is not False
    stroke = resolve_color(spec.get("color") or color, CHALK["white"])
    ids: dict[str, list[str]] = {"labels": [], "sides": [], "marks": []}

    if solved["circle"] is not None:
        r = float(solved["circle"]["r"])
        box = {"x": 0.0, "y": 0.0, "w": r * 2, "h": r * 2}
        if origin is not None:
            box["x"], box["y"] = float(origin[0]), float(origin[1])
        box["x"], box["y"] = _clamp(box, board)
        element = board.add(
            {
                "id": f"{id_prefix}-circle",
                "type": "circle",
                "cx": box["x"] + r,
                "cy": box["y"] + r,
                "r": r,
                "color": stroke,
                "width": 3.4,
                "label": requested or "圆",
            }
        )
        return {"shape_id": element["id"], "ids": ids, "box": box, "mode": solved["mode"]}

    points = solved["points"]
    raw = G.bbox_of_points(points)
    scale = width / max(raw["w"], raw["h"], 1.0)
    placed = [[round((x - raw["x"]) * scale), round((y - raw["y"]) * scale)] for x, y in points]
    box = {
        "x": float(origin[0]) if origin is not None else 0.0,
        "y": float(origin[1]) if origin is not None else 0.0,
        "w": max(p[0] for p in placed),
        "h": max(p[1] for p in placed),
    }
    box["x"], box["y"] = _clamp(box, board)
    placed = [[x + box["x"], y + box["y"]] for x, y in placed]

    default_label = {"square": "正方形", "right_triangle": "三角形", "triangle": "三角形", "isosceles": "等腰三角形"}
    shape = board.add(
        {
            "id": f"{id_prefix}-shape",
            "type": "polygon",
            "points": placed,
            "color": stroke,
            "width": 3.4,
            "label": requested or default_label.get(solved["kind"], "图形"),
        }
    )
    ids["shape"] = [shape["id"]]

    vertex_size = max(24, round(width / 16))
    side_size = max(22, round(width / 18))
    names = [str(v) for v in (spec.get("labels") or [])] or ["A", "B", "C", "D"][: len(placed)]
    while len(names) < len(placed):
        names.append(chr(ord("A") + len(names)))

    centroid_x = sum(p[0] for p in placed) / len(placed)
    centroid_y = sum(p[1] for p in placed) / len(placed)

    def outward(point: Sequence[float]) -> tuple[float, float]:
        dx, dy = point[0] - centroid_x, point[1] - centroid_y
        norm = math.hypot(dx, dy) or 1.0
        return dx / norm, dy / norm

    # Vertex letters go away from the centroid so they never sit on the shape.
    for index, (vx, vy) in enumerate(placed):
        ux, uy = outward([vx, vy])
        text_width = G.estimate_text_size(names[index], vertex_size)["w"]
        vertex = board.add(
            {
                "id": f"{id_prefix}-v{index}",
                "type": "text",
                "text": names[index],
                "size": vertex_size,
                "color": stroke,
                "x": vx + ux * vertex_size * 1.35 - text_width / 2,
                "y": vy + uy * vertex_size * 1.35 - vertex_size / 2,
                "label": f"顶点{names[index]}",
            }
        )
        ids["labels"].append(vertex["id"])

    if show_lengths and solved["lengths"]:
        others = board.occupied_boxes()
        for index in range(len(placed)):
            point_a = placed[index]
            point_b = placed[(index + 1) % len(placed)]
            length = solved["lengths"][index]
            if not length:
                continue
            mid_x = (point_a[0] + point_b[0]) / 2
            mid_y = (point_a[1] + point_b[1]) / 2
            ux, uy = outward([mid_x, mid_y])
            text = f"{names[index]}{names[(index + 1) % len(names)]}={round(length, 2):g}"
            text_width = G.estimate_text_size(text, side_size)["w"]

            def candidate(distance: float) -> dict[str, float]:
                return {
                    "x": mid_x + ux * distance - text_width / 2,
                    "y": mid_y + uy * distance - side_size / 2,
                    "w": text_width,
                    "h": side_size * 1.34,
                }

            # What matters is that the label's *centre* is outside the figure.
            # A bounding-box test is useless here: the hypotenuse's label sits
            # inside the triangle's bbox while being outside the triangle.
            spot = None
            for distance in (side_size * 1.6, side_size * 2.6, side_size * 3.8):
                box_candidate = candidate(distance)
                centre = [box_candidate["x"] + box_candidate["w"] / 2, box_candidate["y"] + box_candidate["h"] / 2]
                if not G.is_inside_board(box_candidate, board.size):
                    continue
                if G.point_in_polygon(centre, placed):
                    continue
                if any(G.bbox_overlap(box_candidate, other)["ratio"] > 0.25 for other in others):
                    continue
                spot = box_candidate
                break
            chosen = spot or candidate(side_size * 2.6)
            side = board.add(
                {
                    "id": f"{id_prefix}-s{index}",
                    "type": "text",
                    "text": text,
                    "size": side_size,
                    "color": CHALK["blue"],
                    "x": chosen["x"],
                    "y": chosen["y"],
                    "label": "边长",
                }
            )
            ids["sides"].append(side["id"])

    if solved["right_at"] >= 0:
        vertex = placed[solved["right_at"]]
        others = [i for i in range(3) if i != solved["right_at"]]

        def unit(index: int) -> tuple[float, float]:
            dx = placed[index][0] - vertex[0]
            dy = placed[index][1] - vertex[1]
            norm = math.hypot(dx, dy) or 1.0
            return dx / norm, dy / norm

        leg = max(22.0, side_size * 1.1)
        a1 = [vertex[0] + unit(others[0])[0] * leg, vertex[1] + unit(others[0])[1] * leg]
        a3 = [vertex[0] + unit(others[1])[0] * leg, vertex[1] + unit(others[1])[1] * leg]
        a2 = [a1[0] + unit(others[1])[0] * leg, a1[1] + unit(others[1])[1] * leg]
        mark = board.add(
            {
                "id": f"{id_prefix}-right",
                "type": "polyline",
                "points": [a1, a2, a3],
                "color": CHALK["amber"],
                "width": 3,
                "label": "直角标记",
            }
        )
        ids["marks"].append(mark["id"])

    if solved["kind"] == "isosceles" and solved["lengths"]:
        for index in range(len(placed)):
            following = (index + 1) % len(placed)
            if abs(solved["lengths"][index] - solved["lengths"][following]) > 1e-6:
                continue
            point_a, point_b = placed[index], placed[following]
            mid_x = (point_a[0] + point_b[0]) / 2
            mid_y = (point_a[1] + point_b[1]) / 2
            dx, dy = point_b[0] - point_a[0], point_b[1] - point_a[1]
            norm = math.hypot(dx, dy) or 1.0
            px, py = -dy / norm, dx / norm
            tick = 14
            mark = board.add(
                {
                    "id": f"{id_prefix}-eq{index}",
                    "type": "line",
                    "points": [[mid_x - px * tick, mid_y - py * tick], [mid_x + px * tick, mid_y + py * tick]],
                    "color": CHALK["green"],
                    "width": 3,
                    "label": "等边标记",
                }
            )
            ids["marks"].append(mark["id"])

    if solved["mode"] == "schematic":
        bottoms = []
        for element_id in [*ids["shape"], *ids["labels"], *ids["sides"], *ids["marks"]]:
            solved_element = board.layout_one(board.get(element_id))
            if solved_element["bbox"] is not None:
                bottoms.append(solved_element["bbox"]["y"] + solved_element["bbox"]["h"])
        bottom = max(bottoms) if bottoms else box["y"] + box["h"]
        note_size = max(20, round(side_size * 0.85))
        note = board.add(
            {
                "id": f"{id_prefix}-note",
                "type": "text",
                "text": "（示意图，不按比例）",
                "size": note_size,
                "color": CHALK["pink"],
                "x": box["x"],
                "y": min(bottom + 12, board.size["h"] - 12 - note_size * 1.34),
                "label": "示意说明",
            }
        )
        ids["sides"].append(note["id"])

    return {"shape_id": shape["id"], "ids": ids, "box": box, "mode": solved["mode"]}


def _clamp(box: Mapping[str, float], board: Board) -> tuple[float, float]:
    dx, dy = G.clamp_box_inside(dict(box), board.size)
    return box["x"] + dx, box["y"] + dy
