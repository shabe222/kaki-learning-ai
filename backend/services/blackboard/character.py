"""黑板上的简笔人物。

三角形那种图形有「数值真值」——边长写多少就得画多长，所以它们在 figures.py 里，
由几何解出来。人物没有这种真值：它是一组笔画，只要比例顺眼、姿态读得出来就对。
所以这里只管一件事：把一具身体在单位空间里写清楚，交给别处缩放和摆放。

三条约定：

1. **单位空间**：x 向右、y 向下，0..1 的方框。调用方缩放，这里不关心画多大。
2. **只有线，没有面**。先在黑板上做出能看懂的轮廓；填色是后面的事，而且黑板的
   元素类型里本来也没有填充字段。
3. **一次给完一组笔画**。服务端算坐标、浏览器逐笔写出来，这是黑板本来就有的样子；
   所以这里返回的是一串有序笔画，不是一个「图片」。

姿态用 `pose` 选，`sitting_typing` 是「坐在键盘前打字」——参考图里那个姿态。
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

Pt = tuple[float, float]
Stroke = tuple[str, list[Pt]]  # (role, points)

CHARACTER_POSES = ("sitting_typing",)
CHARACTER_KINDS = tuple(f"character_{pose}" for pose in CHARACTER_POSES)


def _lerp(a: Pt, b: Pt, t: float) -> Pt:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _line(a: Pt, b: Pt) -> list[Pt]:
    return [a, b]


def _quad(p0: Pt, p1: Pt, p2: Pt, steps: int = 14) -> list[Pt]:
    """二次贝塞尔。够画头发、手臂和尾巴的弧度。"""
    out = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        out.append((
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        ))
    return out


def _cubic(p0: Pt, p1: Pt, p2: Pt, p3: Pt, steps: int = 18) -> list[Pt]:
    out = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        out.append((
            u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1],
        ))
    return out


def _arc(centre: Pt, rx: float, ry: float, start_deg: float, end_deg: float, steps: int = 20) -> list[Pt]:
    out = []
    for index in range(steps + 1):
        angle = math.radians(start_deg + (end_deg - start_deg) * index / steps)
        out.append((centre[0] + math.cos(angle) * rx, centre[1] + math.sin(angle) * ry))
    return out


def _closed(centre: Pt, rx: float, ry: float, steps: int = 24) -> list[Pt]:
    ring = _arc(centre, rx, ry, 0.0, 360.0, steps)
    ring.append(ring[0])
    return ring


def _sitting_typing() -> list[Stroke]:
    """坐在键盘前打字：大头、长直发、前倾的肩、两只手落在键盘上。"""
    head_c: Pt = (0.395, 0.255)
    head_rx, head_ry = 0.180, 0.196
    strokes: list[Stroke] = []

    def add(role: str, points: list[Pt]) -> None:
        strokes.append((role, points))

    # 尾巴：一条有宽度的带子，末端是分叉的尾鳍。两条边分开走，合成一片而不是一条线。
    add("tail", _cubic((0.238, 0.164), (0.190, 0.124), (0.146, 0.082), (0.110, 0.044))
        + _cubic((0.110, 0.044), (0.160, 0.096), (0.206, 0.140), (0.258, 0.186)))
    add("tail_fin", [
        (0.110, 0.044),
        (0.082, 0.038),
        (0.048, 0.032),
        (0.030, 0.030),
        (0.058, 0.014),
        (0.086, 0.004),
        (0.120, 0.006),
        (0.156, 0.018),
        (0.132, 0.034),
        (0.110, 0.044),
    ])

    # 头
    add("head", _closed(head_c, head_rx, head_ry))

    # 刘海：头发整体比头大一圈并往上抬，头顶那条缝才看得出来是「头发盖在头上」。
    # 两条弧几乎同心就成了扣帽子，第一版就是这个错。
    hair_c: Pt = (head_c[0], head_c[1] - 0.022)
    fringe: list[Pt] = _arc(hair_c, head_rx * 1.12, head_ry * 1.12, 150.0, 390.0, 34)
    fringe += _quad((0.578, 0.318), (0.500, 0.262), (0.446, 0.330), 10)
    fringe += _quad((0.446, 0.330), (0.395, 0.256), (0.344, 0.330), 10)
    fringe += _quad((0.344, 0.330), (0.290, 0.262), (0.212, 0.318), 10)
    add("bangs", fringe)

    # 两侧长发：贴着头的两侧垂到胸口就收，太长会变成两把垂刀。
    # 内缘必须留在脸的外侧（左 ≤0.245、右 ≥0.545），否则那两条线会从脸颊上穿过去。
    add("hair_side", _cubic((0.206, 0.330), (0.184, 0.456), (0.196, 0.556), (0.228, 0.626))
        + _cubic((0.228, 0.626), (0.250, 0.540), (0.250, 0.436), (0.243, 0.336)))
    add("hair_side", _cubic((0.584, 0.330), (0.606, 0.456), (0.594, 0.556), (0.562, 0.626))
        + _cubic((0.562, 0.626), (0.540, 0.540), (0.540, 0.436), (0.547, 0.336)))

    # 闭着的眼睛、小嘴：参考图里人物是闭眼在专心打字。
    add("face", _arc((0.336, 0.338), 0.034, 0.020, 20.0, 160.0, 12))
    add("face", _arc((0.462, 0.338), 0.034, 0.020, 20.0, 160.0, 12))
    add("face", _arc((0.398, 0.400), 0.020, 0.015, 20.0, 160.0, 10))

    # 领口与身体：肩膀窄、前倾。
    add("collar", _line((0.358, 0.428), (0.395, 0.476)))
    add("collar", _line((0.432, 0.428), (0.395, 0.476)))
    add("body", _cubic((0.315, 0.522), (0.318, 0.472), (0.344, 0.452), (0.395, 0.452)))
    add("body", _cubic((0.478, 0.522), (0.474, 0.472), (0.448, 0.452), (0.395, 0.452)))
    add("body", _cubic((0.315, 0.522), (0.302, 0.596), (0.312, 0.660), (0.344, 0.690)))
    add("body", _cubic((0.478, 0.522), (0.492, 0.596), (0.482, 0.660), (0.450, 0.690)))

    # 两条手臂伸向键盘，手落在键盘的上沿。近侧那条从肩的外缘出发，
    # 不要从身体轮廓里面长出来。
    add("arm", _cubic((0.318, 0.520), (0.330, 0.628), (0.420, 0.676), (0.492, 0.692)))
    add("arm", _cubic((0.470, 0.526), (0.524, 0.600), (0.628, 0.640), (0.700, 0.654)))
    add("hand", _closed((0.504, 0.698), 0.030, 0.019))
    add("hand", _closed((0.712, 0.660), 0.030, 0.019))

    # 坐姿：膝盖在身前一折，落在键盘左边，不和键盘打架。
    add("leg", _cubic((0.344, 0.690), (0.352, 0.744), (0.404, 0.752), (0.430, 0.706)))

    # 键盘：斜放的平行四边形，两条横向分行，一行短竖当键位。
    keys = [(0.500, 0.706), (0.930, 0.664), (0.972, 0.792), (0.542, 0.840)]
    add("keyboard", [keys[0], keys[1], keys[2], keys[3], keys[0]])
    for row in (0.32, 0.62):
        add("keys", _line(_lerp(keys[0], keys[3], row), _lerp(keys[1], keys[2], row)))
    for index in range(1, 6):
        top = _lerp(keys[0], keys[1], index / 6)
        bottom = _lerp(_lerp(keys[0], keys[3], 0.32), _lerp(keys[1], keys[2], 0.32), index / 6)
        add("keys", _line(top, bottom))

    return strokes


_BUILDERS = {"sitting_typing": _sitting_typing}

# 每一类笔画落板时的粗细与颜色角色。线条粗细是「哪一笔更重」的表达，
# 头发和脸比身体轻一点，键盘最轻。
_STROKE_STYLE: dict[str, tuple[float, str]] = {
    "head": (3.6, "white"),
    "bangs": (3.4, "white"),
    "hair_side": (3.4, "white"),
    "body": (3.4, "white"),
    "arm": (3.4, "white"),
    "leg": (3.2, "white"),
    "collar": (3.0, "amber"),
    "face": (3.0, "amber"),
    "hand": (3.0, "amber"),
    "tail": (3.2, "blue"),
    "tail_fin": (3.2, "blue"),
    "keyboard": (3.0, "green"),
    "keys": (2.2, "green"),
}


def build_character(pose: str) -> list[Stroke]:
    builder = _BUILDERS.get(str(pose or "sitting_typing"))
    if builder is None:
        raise ValueError(f"unknown character pose: {pose}")
    return builder()


def unit_bbox(strokes: Iterable[Stroke]) -> tuple[float, float, float, float]:
    xs = [p[0] for _role, pts in strokes for p in pts]
    ys = [p[1] for _role, pts in strokes for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def scaled_strokes(pose: str, box: Sequence[float]) -> list[Stroke]:
    """把单位空间里的笔画缩放进 box=[x, y, w, h]，保持比例并居中。"""
    strokes = build_character(pose)
    x0, y0, x1, y1 = unit_bbox(strokes)
    span_x = max(x1 - x0, 1e-6)
    span_y = max(y1 - y0, 1e-6)
    scale = min(box[2] / span_x, box[3] / span_y)
    offset_x = box[0] + (box[2] - span_x * scale) / 2 - x0 * scale
    offset_y = box[1] + (box[3] - span_y * scale) / 2 - y0 * scale
    return [
        (role, [(round(x * scale + offset_x, 1), round(y * scale + offset_y, 1)) for x, y in pts])
        for role, pts in strokes
    ]


def draw_character(
    board: Any,
    spec: Mapping[str, Any],
    *,
    origin: Sequence[float] | None = None,
    size: float | None = None,
    color: Any = None,
    label: str | None = None,
    id_prefix: str = "fig",
) -> dict[str, Any]:
    """把一个人物落进 board。

    返回的形状与 figures.draw_figure 一致（shape_id / ids / box / mode），
    这样 script._draw_figure_item 不需要为人 物分出一条支路。
    """
    # 延迟导入：model 也属于黑板内部，放模块顶部会让人和 figures 互相牵住。
    from services.blackboard import geometry as G
    from services.blackboard.model import CHALK, resolve_color

    pose = str(spec.get("pose") or "sitting_typing")
    nominal = float(spec.get("size") or size or 520)
    x = float(origin[0]) if origin is not None else 0.0
    y = float(origin[1]) if origin is not None else 0.0
    # 单位空间是 1 : 0.8，缩放时按比例居中，所以这里给一个正方形框。
    box = {"x": x, "y": y, "w": nominal, "h": nominal}
    dx, dy = G.clamp_box_inside(dict(box), board.size)
    box["x"], box["y"] = box["x"] + dx, box["y"] + dy

    placed = scaled_strokes(pose, [box["x"], box["y"], box["w"], box["h"]])
    # 调用方明确指定颜色时整幅用一个色；否则每一类笔画用自己的粉笔色，
    # 这样「头发、脸、尾巴、键盘」在单色板上也分得开。
    explicit = spec.get("color") or color
    caption = str(label or spec.get("label") or "人物")

    ids: list[str] = []
    for index, (role, points) in enumerate(placed):
        width, palette_role = _STROKE_STYLE.get(role, (3.4, "white"))
        element = board.add(
            {
                "id": f"{id_prefix}-p{index}",
                "type": "polyline",
                "points": [[px, py] for px, py in points],
                "color": resolve_color(explicit, CHALK["white"]) if explicit else CHALK[palette_role],
                "width": width,
                "label": f"{caption}：{role}",
            }
        )
        ids.append(element["id"])

    return {
        "shape_id": ids[0] if ids else "",
        "ids": {"shape": ids, "labels": [], "sides": [], "marks": []},
        "box": box,
        "mode": "exact",
    }
