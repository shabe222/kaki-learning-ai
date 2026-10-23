"""Spatial facts for the blackboard, computed here instead of guessed by a model.

Ported from the prototype's ``server/geometry.mjs``. The rule the prototype
learned the hard way: whether a line leaves a figure, whether a label sits
inside a polygon, whether two boxes collide -- these are deterministic
questions. Asking a vision model costs a call and still gets the wrong answer.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

EPS = 1e-9

Pt = Sequence[float]
Rect = dict[str, float]


def bbox_of_points(points: Iterable[Pt]) -> Rect:
    x0 = y0 = math.inf
    x1 = y1 = -math.inf
    for x, y in points:
        x0 = min(x0, x)
        y0 = min(y0, y)
        x1 = max(x1, x)
        y1 = max(y1, y)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def estimate_text_size(text: str, size: float) -> Rect:
    """Text extent estimate: CJK counts as one em, Latin as 0.55 em."""
    lines = str(text).split("\n")
    width = 0.0
    for line in lines:
        line_width = 0.0
        for ch in line:
            code = ord(ch)
            wide = (
                0x2E80 <= code <= 0x9FFF
                or 0x3000 <= code <= 0x303F
                or 0xFF00 <= code <= 0xFFEF
            )
            line_width += size if wide else size * 0.55
        width = max(width, line_width)
    return {"x": 0.0, "y": 0.0, "w": width, "h": len(lines) * size * 1.34}


def round_rect(box: Rect, pad: float = 0) -> Rect:
    return {
        "x": box["x"] - pad,
        "y": box["y"] - pad,
        "w": box["w"] + pad * 2,
        "h": box["h"] + pad * 2,
    }


def bbox_union(a: Rect | None, b: Rect | None) -> Rect | None:
    if a is None:
        return b
    if b is None:
        return a
    x = min(a["x"], b["x"])
    y = min(a["y"], b["y"])
    return {
        "x": x,
        "y": y,
        "w": max(a["x"] + a["w"], b["x"] + b["w"]) - x,
        "h": max(a["y"] + a["h"], b["y"] + b["h"]) - y,
    }


def bbox_overlap(a: Rect, b: Rect) -> dict[str, float | bool]:
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if w <= 0 or h <= 0:
        return {"overlaps": False, "area": 0.0, "ratio": 0.0}
    area = w * h
    min_area = min(a["w"] * a["h"], b["w"] * b["h"]) or 1.0
    return {"overlaps": True, "area": area, "ratio": area / min_area}


def keeps_clearance(candidate: Rect, others: Iterable[Rect], gap: float) -> bool:
    """candidate 与每个 other 之间都至少留出 gap。

    排版里原来用「重叠比例 ≤ 0.08 就算通过」当判据，那个比值允许两个盒子贴在一起
    甚至小幅重叠。estimate_text_size 与实际字体略有出入，于是计算出来的 1.6px 净空
    渲染出来就是压字（2026-09-20 实测：中间栏正文第三行被关系标签整行压住）。
    所以把候选盒按 gap 外扩之后，任何接触都算不通过。
    """
    grown = round_rect(candidate, gap)
    return not any(bbox_overlap(grown, other)["ratio"] > 0 for other in others)


def segments_intersect(p1: Pt, p2: Pt, p3: Pt, p4: Pt) -> list[float] | None:
    """Intersection point, or None. Parallel/collinear counts as no crossing."""
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = p4[0] - p3[0], p4[1] - p3[1]
    den = d1x * d2y - d1y * d2x
    if abs(den) < EPS:
        return None
    t = ((p3[0] - p1[0]) * d2y - (p3[1] - p1[1]) * d2x) / den
    u = ((p3[0] - p1[0]) * d1y - (p3[1] - p1[1]) * d1x) / den
    if t < -EPS or t > 1 + EPS or u < -EPS or u > 1 + EPS:
        return None
    return [p1[0] + t * d1x, p1[1] + t * d1y]


def point_in_polygon(pt: Pt, poly: Sequence[Pt]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > pt[1]) != (yj > pt[1]) and pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def distance_point_to_segment(p: Pt, a: Pt, b: Pt) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    len2 = dx * dx + dy * dy
    if len2 < EPS:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def distance_to_polygon(pt: Pt, poly: Sequence[Pt]) -> float:
    best = math.inf
    for i in range(len(poly)):
        best = min(best, distance_point_to_segment(pt, poly[i], poly[(i + 1) % len(poly)]))
    return best


def segment_vs_polygon(a: Pt, b: Pt, poly: Sequence[Pt]) -> dict[str, object]:
    """How one segment relates to a polygon.

    An endpoint landing exactly on a vertex or an edge is the common case
    (drawing an altitude from a vertex to the base), so it is classified
    separately -- otherwise the answer reads "crosses the boundary three times,
    both ends outside", which is misleading.
    """
    tolerance = 0.75
    hits = []
    for i in range(len(poly)):
        point = segments_intersect(a, b, poly[i], poly[(i + 1) % len(poly)])
        if point:
            hits.append(point)
    a_on = distance_to_polygon(a, poly) <= tolerance
    b_on = distance_to_polygon(b, poly) <= tolerance
    a_in = (not a_on) and point_in_polygon(a, poly)
    b_in = (not b_on) and point_in_polygon(b, poly)
    mid_in = point_in_polygon([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], poly)
    return {
        "crossings": len(hits),
        "points": hits,
        "a_inside": a_in,
        "b_inside": b_in,
        "a_on_edge": a_on,
        "b_on_edge": b_on,
        "mid_inside": mid_in,
        "exits": (a_in or a_on) and not b_in and not b_on,
        "enters": (not a_in) and (not a_on) and (b_in or b_on),
    }


def angle_at(a: Pt, b: Pt, c: Pt) -> float | None:
    """Angle ABC in degrees, B being the vertex."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < EPS or n2 < EPS:
        return None
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def is_inside_board(box: Rect, board: Mapping[str, float]) -> bool:
    return (
        box["x"] >= 0
        and box["y"] >= 0
        and box["x"] + box["w"] <= board["w"]
        and box["y"] + box["h"] <= board["h"]
    )


def find_free_space(
    occupied: Sequence[Rect],
    w: float,
    h: float,
    *,
    step: float = 40,
    pad: float = 26,
    within: Rect | None = None,
    near: Sequence[float] | None = None,
) -> dict[str, float] | None:
    """Scan for a w x h patch that collides with nothing. ``near`` biases it."""
    box = within if within is not None else {"x": 0, "y": 0, "w": 1600, "h": 1000}
    blockers = [round_rect(entry, pad) for entry in occupied]

    def fits(x: float, y: float) -> bool:
        cand = {"x": x, "y": y, "w": w, "h": h}
        if (
            x < box["x"] + 20
            or y < box["y"] + 20
            or x + w > box["x"] + box["w"] - 20
            or y + h > box["y"] + box["h"] - 20
        ):
            return False
        return not any(bbox_overlap(cand, b)["overlaps"] for b in blockers)

    cx0 = near[0] - w / 2 if near else box["x"] + box["w"] / 2 - w / 2
    cy0 = near[1] - h / 2 if near else box["y"] + box["h"] / 2 - h / 2
    best: dict[str, float] | None = None
    best_distance = math.inf
    y = box["y"]
    while y + h <= box["y"] + box["h"]:
        x = box["x"]
        while x + w <= box["x"] + box["w"]:
            if fits(x, y):
                distance = math.hypot(x - cx0, y - cy0)
                if distance < best_distance:
                    best_distance = distance
                    best = {"x": x, "y": y}
            x += step
        y += step
    return best


def largest_free_rect(grid: Sequence[Sequence[bool]]) -> dict[str, float] | None:
    """Biggest all-empty rectangle in an occupancy grid (greedy right/down)."""
    rows = len(grid)
    if rows == 0:
        return None
    cols = len(grid[0])
    best: dict[str, float] | None = None
    for r in range(rows):
        for c in range(cols):
            if grid[r][c]:
                continue
            w = 0
            while c + w < cols and not grid[r][c + w]:
                w += 1
                h = 1
                while r + h < rows and not any(grid[r + h][c : c + w]):
                    h += 1
                area = w * h
                if best is None or area > best["area"]:
                    best = {"x": float(c), "y": float(r), "w": float(w), "h": float(h), "area": float(area)}
    return best


def clamp_box_inside(box: Rect, board: Mapping[str, float], inset: float = 12) -> tuple[float, float]:
    """How far to nudge a box so it stays on the board.

    The ``max(inset, ...)`` guard matters when the box is wider than the board
    is: without it the clamp flips negative and pushes the element off the left
    edge instead of keeping it at the margin.
    """
    x = min(max(box["x"], inset), max(inset, board["w"] - inset - box["w"]))
    y = min(max(box["y"], inset), max(inset, board["h"] - inset - box["h"]))
    return x - box["x"], y - box["y"]


def round_box(box: Rect, digits: int = 1) -> Rect:
    factor = 10 ** digits
    return {
        "x": round(box["x"] * factor) / factor,
        "y": round(box["y"] * factor) / factor,
        "w": round(box["w"] * factor) / factor,
        "h": round(box["h"] * factor) / factor,
    }
