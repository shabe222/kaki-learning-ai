"""KAKI teaching-scene renderer and local evidence pipeline."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


def prepare_s2v_audio(source: Path, output_path: Path, max_sec: float = 19.0) -> Path:
    """Re-encode speech as a real .mp3 under the S2V duration cap."""
    ffmpeg = shutil.which("ffmpeg")
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1.0, min(float(max_sec), 19.0))
    if not ffmpeg:
        if source.suffix.lower() not in {".mp3", ".wav"}:
            raise RuntimeError("ffmpeg 未安装，无法转换数字人音频。")
        if source.resolve() != output_path:
            shutil.copyfile(source, output_path)
        return output_path
    command = [
        ffmpeg, "-y", "-nostdin", "-i", str(source), "-t", str(duration),
        "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "128k",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("数字人音频转码失败：" + (completed.stderr or "")[-400:])
    return output_path


async def synthesize_speech(text: str, output_path: Path, voice: str = "zh-CN-XiaoxiaoNeural") -> Path:
    import asyncio
    import edge_tts

    output_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    # Edge 在线语音偶发网络抖动，重试 3 次提升可用性
    for attempt in range(3):
        try:
            await edge_tts.Communicate(str(text), voice=voice).save(str(output_path))
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("语音服务未生成可用音频。")
            return output_path
        except Exception as exc:  # noqa: BLE001 - 统一重试
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"语音生成失败（重试 3 次）：{last_error}"[:300]) from last_error


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def official_teacher_reference() -> Path:
    """The canonical DARYA teacher likeness handed to the avatar provider."""
    return project_root() / "frontend" / "public" / "assets" / "avatar" / "darya" / "darya-video-teacher-v1.png"


def frame_palette(theme_key: str = "kaki") -> dict:
    """PIL-ready frame colors from the one visual-theme authority.

    视频帧与 PPT 导出、前端 deck 预览共用同一套主题令牌；主题只提供
    调色与材质，版面骨架仍由渲染器按内容节奏决定。
    """
    from services.visual_theme import normalize_theme, theme_rgb, theme_rgba

    key = normalize_theme(theme_key)
    return {
        "key": key,
        "background": theme_rgb(key, "background"),
        "surface_soft": theme_rgba(key, "surface", 246),
        "surface_card": theme_rgba(key, "surface", 244),
        "primary": theme_rgb(key, "primary"),
        "primary_tint": theme_rgba(key, "primary", 26),
        "primary_line": theme_rgba(key, "primary", 150),
        "primary_badge": theme_rgba(key, "primary", 232),
        "secondary": theme_rgb(key, "secondary"),
        "secondary_tint": theme_rgba(key, "secondary", 20),
        "accent": theme_rgb(key, "accent"),
        "text": theme_rgb(key, "text"),
        "muted": theme_rgb(key, "muted"),
        "line": theme_rgba(key, "line", 255),
        "card_outline": theme_rgba(key, "text", 42),
        "on_badge": theme_rgb(key, "background"),
    }


# 讲解卡的节拍外观：视频技能要求镜头节奏随钩子/过程/对照/误区/收束变化，
# 这里把每个节拍映射为可见差异（角标颜色、标题字号、正文起点高度）。
_BEAT_LOOKS: dict[str, dict] = {
    "hook": {"chip": "primary", "chip_text": "钩子", "title_size": 46, "body_top": 328},
    "process": {"chip": "secondary", "chip_text": "过程", "title_size": 40, "body_top": 316},
    "contrast": {"chip": "accent", "chip_text": "对照", "title_size": 40, "body_top": 316},
    "misconception": {"chip": "accent", "chip_text": "误区", "title_size": 40, "body_top": 316},
    "closing": {"chip": "primary", "chip_text": "收束", "title_size": 44, "body_top": 322},
}


def classify_script_beats(sentences: list, *, key_points_count: int = 0) -> list[str]:
    """给逐句讲稿一个可见的节拍标签：首句钩子、末句收束，中间默认过程。

    模型没有产出更细的节拍标注时，这就是节奏差异的下限：开头与结尾
    一定与中段不同，而不是整段视频同一张卡。
    """
    total = len(sentences)
    if total == 0:
        return []
    if total == 1:
        return ["hook"]
    beats: list[str] = []
    for index in range(total):
        if index == 0:
            beats.append("hook")
        elif index == total - 1:
            beats.append("closing")
        else:
            text = str(sentences[index] or "")
            if any(mark in text for mark in ("但", "却", "注意", "误区", "不是", "别")):
                beats.append("misconception")
            elif any(mark in text for mark in ("相比", "对比", "而", "差别", "不同")):
                beats.append("contrast")
            else:
                beats.append("process")
    return beats


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def render_scene_frame(
    output_path: Path, *, topic: str, key_points: Iterable[str], teacher_path: Path | None = None,
    include_teacher: bool = True, caption: str = "DARYA 讲解", theme: str = "kaki",
) -> Path:
    palette = frame_palette(theme)
    width, height = 1280, 720
    canvas = Image.new("RGB", (width, height), palette["background"])
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.ellipse((-190, 500, 620, 980), fill=palette["primary_tint"])
    draw.ellipse((760, -290, 1510, 310), fill=palette["secondary_tint"])
    draw.line((70, 84, 1210, 84), fill=palette["primary_line"], width=3)
    draw.rounded_rectangle((66, 118, 820, 654), radius=36, fill=palette["surface_card"],
                           outline=palette["card_outline"], width=2)
    draw.text((104, 150), "DARYA · 教学短讲", font=_font(24, bold=True), fill=palette["primary"])
    draw.text((104, 205), str(topic)[:28], font=_font(48, bold=True), fill=palette["text"])
    y = 304
    for index, point in enumerate(list(key_points)[:4], 1):
        draw.ellipse((104, y + 9, 126, y + 31), fill=palette["accent"])
        draw.text((145, y), str(point)[:34], font=_font(29), fill=palette["text"])
        y += 78
    draw.text((104, 601), str(caption or "DARYA 讲解")[:42], font=_font(18), fill=palette["muted"])

    if include_teacher:
        teacher = Image.open(teacher_path or official_teacher_reference()).convert("RGBA")
        teacher.thumbnail((342, 450), Image.Resampling.LANCZOS)
        teacher_layer = Image.new("RGBA", (350, 470), (*palette["background"], 0))
        x = (teacher_layer.width - teacher.width) // 2
        y = (teacher_layer.height - teacher.height) // 2
        teacher_layer.alpha_composite(teacher, (x, y))
        canvas.paste(teacher_layer, (875, 150), teacher_layer)
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle((904, 610, 1194, 658), radius=24, fill=palette["primary_badge"])
        draw.text((959, 621), "DARYA 讲解准备中", font=_font(19, bold=True), fill=palette["on_badge"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG")
    return output_path


def probe_video(path: Path) -> dict:
    import json

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe 未安装，无法验证生成视频。")
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError("ffprobe 无法解析生成视频。")
    data = json.loads(completed.stdout)
    types = {str(stream.get("codec_type") or "") for stream in data.get("streams", [])}
    if not {"video", "audio"}.issubset(types):
        raise RuntimeError("生成文件缺少视频流或音轨。")
    return data


def media_duration_sec(path: Path) -> float:
    return video_timing(path)[0]


def video_timing(path: Path) -> tuple[float, int, str]:
    import json

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe 未安装，无法读取媒体时长。")
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError("ffprobe 无法读取媒体时长。")
    data = json.loads(completed.stdout) or {}
    video = next(
        (stream for stream in data.get("streams") or [] if stream.get("codec_type") == "video"),
        {},
    )
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    frames = int(str(video.get("nb_frames") or "0") or 0)
    fps = str(video.get("r_frame_rate") or "16/1")
    if frames <= 0:
        try:
            numerator, denominator = fps.split("/", 1)
            frames = max(1, int(round(duration * float(numerator) / float(denominator or 1))))
        except (TypeError, ValueError, ZeroDivisionError):
            frames = max(1, int(round(duration * 16)))
    if duration <= 0:
        raise RuntimeError("媒体时长无效。")
    duration = max(0.4, min(duration, 600.0))
    frames = max(1, min(frames, 18000))
    return duration, frames, fps


def compose_teaching_video(
    *, avatar_video: Path, output_path: Path, topic: str, key_points: Iterable[str],
    timeout_sec: int = 90, theme: str = "kaki",
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg 未安装，无法合成教学视频。")
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration, frames, fps = video_timing(avatar_video)
    with tempfile.TemporaryDirectory(prefix="kaki-compose-") as temp_dir:
        scene = render_scene_frame(
            Path(temp_dir) / "scene.png", topic=topic, key_points=key_points,
            include_teacher=False, theme=theme,
        )
        command = [
            ffmpeg, "-y", "-nostdin", "-loop", "1", "-framerate", fps, "-i", str(scene),
            "-i", str(avatar_video),
            "-filter_complex",
            "[0:v]scale=850:720,crop=850:720:0:0[left];"
            "[1:v]scale=430:720:force_original_aspect_ratio=increase,crop=430:720[right];"
            "[left][right]hstack=inputs=2:shortest=1,format=yuv420p[v]",
            "-map", "[v]", "-map", "1:a?", "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k", "-t", f"{duration:.3f}",
            str(output_path),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=max(30, int(timeout_sec)),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffmpeg 教学场景合成超时。") from exc
        if completed.returncode != 0:
            raise RuntimeError("ffmpeg 教学场景合成失败：" + completed.stderr[-800:])
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("教学场景合成未产生视频文件。")
    probe_video(output_path)
    return output_path


def render_dry_run_video(
    output_path: Path, *, topic: str, key_points: Iterable[str], duration_sec: int = 3,
    audio_path: Path | None = None, caption: str = "DARYA 讲解", theme: str = "kaki",
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg 未安装，无法生成本地视频证据。")
    duration = max(1, min(int(duration_sec), 600))
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kaki-video-") as temp_dir:
        points = [str(point) for point in key_points if str(point).strip()] or [str(topic)]
        segment_duration = duration / max(1, len(points))
        timeline_lines: list[str] = []
        for index, point in enumerate(points):
            frame = render_scene_frame(
                Path(temp_dir) / f"scene-{index}.png", topic=topic,
                key_points=[point], caption=f"{caption} · 第 {index + 1} 步", theme=theme,
            )
            escaped = frame.as_posix().replace("'", "'\\''")
            timeline_lines.extend([f"file '{escaped}'", f"duration {segment_duration:.3f}"])
        timeline_lines.append(timeline_lines[-2])
        timeline = Path(temp_dir) / "timeline.txt"
        timeline.write_text("\n".join(timeline_lines) + "\n", encoding="utf-8")
        video_filter = "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p[v]"
        if audio_path and Path(audio_path).exists() and Path(audio_path).stat().st_size > 0:
            command = [
                ffmpeg, "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(timeline), "-i", str(audio_path),
                "-filter_complex", f"{video_filter};[1:a]apad=whole_dur={duration}[a]",
                "-map", "[v]", "-map", "[a]", "-t", str(duration),
                "-r", "30",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", str(output_path),
            ]
        else:
            command = [
                ffmpeg, "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(timeline),
                "-f", "lavfi", "-i", f"sine=frequency=523:sample_rate=48000:duration={duration}",
                "-filter_complex", video_filter,
                "-map", "[v]", "-map", "1:a", "-t", str(duration),
                "-r", "30",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-shortest", str(output_path),
            ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            raise RuntimeError("ffmpeg 合成失败：" + completed.stderr[-800:])
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("ffmpeg 未生成可用视频文件。")
    return output_path


def build_talking_teacher_pair(portrait_path: Path, workdir: Path, *, theme: str = "kaki") -> tuple[Path, Path] | None:
    """从一张老师肖像构造两帧说话素材：闭嘴原帧 + 下颌微开的变体帧。

    变体只对预测的嘴部椭圆区域做小幅下移重贴（半身居中构图的统计位置），
    不改变画面其余部分；两帧交替即形成可见的说话口部动作。肖像不可用时返回 None。
    """
    from PIL import Image, ImageFilter

    try:
        portrait = Image.open(portrait_path).convert("RGB")
    except Exception:
        return None
    width, height = portrait.size
    if width < 200 or height < 200:
        return None
    workdir.mkdir(parents=True, exist_ok=True)
    frame_a = workdir / "teacher-a.png"
    frame_b = workdir / "teacher-b.png"
    portrait.save(frame_a, format="PNG")

    # 嘴部椭圆：半身居中构图下约在画面高度 62%；开口幅度加大保证可见。
    center_x, center_y = width * 0.5, height * 0.62
    rx, ry = width * 0.15, height * 0.075
    shift = max(4, int(height * 0.022))
    box = (
        max(0, int(center_x - rx)), max(0, int(center_y - ry)),
        min(width, int(center_x + rx)), min(height, int(center_y + ry * 2.2)),
    )
    patch = portrait.crop(box)
    mask = Image.new("L", patch.size, 0)
    from PIL import ImageDraw as _ID

    _ID.Draw(mask).ellipse(
        (2, 2, patch.size[0] - 2, patch.size[1] - 2), fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(2, patch.size[1] // 6)))
    frame = portrait.copy()
    canvas_patch = Image.new("RGB", patch.size)
    canvas_patch.paste(patch, (0, shift))
    frame.paste(canvas_patch, box[:2], mask)
    # 头部整体微沉：变体帧额外携带整个画面的 6px 下移（上下留边用背景色补齐），
    # 让两帧交替时头部也有可见浮动，而不是只有嘴部微变。
    bob = max(4, int(height * 0.008))
    base = Image.new("RGB", (width, height), frame_palette(theme)["background"])
    base.paste(frame, (0, bob))
    frame = base
    frame.save(frame_b, format="PNG")
    return frame_a, frame_b


def split_script_sentences(script: str, *, max_len: int = 90) -> list:
    """按句读切分讲稿，合并过短片段，保证每段是一句可朗读的完整话。"""
    import re

    text = str(script or "").replace("\r", "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;])|\n+", text) if p and p.strip()]
    merged = []
    for part in parts:
        if merged and (len(merged[-1]) < 12 or len(part) < 6) and len(merged[-1]) + len(part) <= max_len:
            merged[-1] = merged[-1] + part
        elif len(part) > max_len:
            chunk = part
            while len(chunk) > max_len:
                cut = chunk.rfind("，", 0, max_len)
                cut = cut if cut > 20 else max_len
                merged.append(chunk[: cut + 1].rstrip("，"))
                chunk = chunk[cut + 1:]
            if chunk:
                merged.append(chunk)
        else:
            merged.append(part)
    return [m for m in merged if len(m) >= 2]


def _text_units(text: str) -> int:
    return sum(2 if ord(ch) >= 0x2E80 else 1 for ch in text)


def render_script_card(
    output_path: Path, *, topic: str, sentence: str, step: int, total_steps: int,
    progress: float, teacher_frame: Path | None = None, caption: str = "DARYA 讲解",
    theme: str = "kaki", beat: str = "",
) -> Path:
    """逐句讲解卡：步骤计数 + 当前句子 + 底部进度条，可叠老师两帧之一。

    节拍决定这张卡的可见差异（角标、标题字号、正文起点）；主题决定调色。
    """
    from PIL import ImageDraw

    palette = frame_palette(theme)
    look = _BEAT_LOOKS.get(str(beat or "").strip().lower()) or _BEAT_LOOKS["process"]

    width, height = 1280, 720
    canvas = Image.new("RGB", (width, height), palette["background"])
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.ellipse((-190, 500, 620, 980), fill=palette["primary_tint"])
    draw.ellipse((760, -290, 1510, 310), fill=palette["secondary_tint"])
    draw.line((70, 84, 1210, 84), fill=palette["primary_line"], width=3)
    draw.rounded_rectangle((66, 108, 860, 664), radius=36, fill=palette["surface_card"],
                           outline=palette["card_outline"], width=2)
    draw.text((104, 138), "DARYA · 教学短讲", font=_font(22, bold=True), fill=palette["primary"])
    draw.text((104, 182), str(topic)[:26], font=_font(look["title_size"], bold=True), fill=palette["text"])
    draw.text((104, 262), f"第 {step}/{total_steps} 句", font=_font(20), fill=palette["muted"])
    # 节拍角标：右上角小块，颜色随钩子/过程/对照/误区/收束变化。
    chip_text = str(look["chip_text"])
    chip_size = _font(18, bold=True)
    chip_w = int(chip_size.getbbox(chip_text)[2] - chip_size.getbbox(chip_text)[0]) + 36
    draw.rounded_rectangle((860 - 26 - chip_w, 130, 860 - 26, 172), radius=21,
                           fill=palette[look["chip"]])
    draw.text((860 - 26 - chip_w + 18, 139), chip_text, font=chip_size, fill=palette["on_badge"])

    y = look["body_top"]
    line = ""
    for ch in str(sentence):
        line += ch
        if _text_units(line) >= 24:
            draw.text((104, y), line, font=_font(30), fill=palette["text"])
            y += 52
            line = ""
    if line:
        draw.text((104, y), line, font=_font(30), fill=palette["text"])

    draw.text((104, 596), str(caption)[:42], font=_font(18), fill=palette["muted"])
    bar_x0, bar_y, bar_x1 = 104, 636, 822
    draw.rounded_rectangle((bar_x0, bar_y, bar_x1, bar_y + 12), radius=6, fill=palette["card_outline"])
    ratio = max(0.0, min(1.0, float(progress)))
    if ratio > 0:
        draw.rounded_rectangle(
            (bar_x0, bar_y, bar_x0 + int((bar_x1 - bar_x0) * ratio), bar_y + 12),
            radius=6, fill=palette["accent"],
        )

    if teacher_frame:
        teacher = Image.open(teacher_frame).convert("RGBA")
        teacher.thumbnail((360, 470), Image.Resampling.LANCZOS)
        layer = Image.new("RGBA", (380, 500), (*palette["background"], 0))
        layer.alpha_composite(teacher, ((layer.width - teacher.width) // 2, (layer.height - teacher.height) // 2))
        canvas.paste(layer, (880, 140), layer)
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle((914, 596, 1186, 648), radius=22, fill=palette["primary_badge"])
        draw.text((950, 608), "DARYA 老师正在讲解", font=_font(19, bold=True), fill=palette["on_badge"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG")
    return output_path


def render_scripted_lesson_video(
    output_path: Path, *, topic: str, sentences: list, segment_durations: list,
    segment_audios: list, teacher_pair, caption: str = "DARYA 讲解", flap_seconds: float = 0.36,
    theme: str = "kaki", beats: list | None = None, single_audio: Path | None = None,
) -> Path:
    """逐句卡片 + 老师两帧 concat 交替的确定性渲染。

    每句一张讲解卡（闭嘴/微开两变体按节奏交替），卡与旁白逐句对齐，内容随讲解
    持续变化；节拍标签让开头/对照/收束卡在视觉上可辨，交替在 concat 时间轴层面
    完成，不依赖 overlay enable。逐句 TTS 不可用而整段 TTS 成功时传 single_audio，
    卡片按估算时长推进、整段音轨直混一次，不做逐句拼接。
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg 未安装，无法生成本地视频。")
    if not sentences or len(sentences) != len(segment_durations):
        raise RuntimeError("讲稿分段与时长不一致。")
    if single_audio is None and len(segment_audios) != len(sentences):
        raise RuntimeError("讲稿分段与逐句音频数量不一致。")
    beat_labels = list(beats or [])
    total = sum(float(d) for d in segment_durations)
    duration = max(1.0, min(total, 600.0))
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kaki-lesson-") as temp_dir:
        temp = Path(temp_dir)
        timeline_lines = []
        audio_lines = []
        elapsed = 0.0
        half = max(0.09, flap_seconds / 2)
        for index, (sentence, seg_duration) in enumerate(zip(sentences, segment_durations)):
            seg_duration = float(seg_duration)
            beat = str(beat_labels[index]) if index < len(beat_labels) else ""
            card_a = render_script_card(
                temp / f"card-{index}-a.png", topic=topic, sentence=sentence,
                step=index + 1, total_steps=len(sentences),
                progress=(elapsed + seg_duration / 2) / duration,
                teacher_frame=teacher_pair[0] if teacher_pair else None, caption=caption,
                theme=theme, beat=beat,
            )
            card_b = None
            if teacher_pair:
                card_b = render_script_card(
                    temp / f"card-{index}-b.png", topic=topic, sentence=sentence,
                    step=index + 1, total_steps=len(sentences),
                    progress=(elapsed + seg_duration / 2) / duration,
                    teacher_frame=teacher_pair[1], caption=caption,
                    theme=theme, beat=beat,
                )
            remaining = seg_duration
            show_b = False
            while remaining > 0:
                piece = min(half, remaining)
                frame = card_b if (card_b is not None and show_b) else card_a
                escaped = frame.as_posix().replace("'", "'\\''")
                timeline_lines.extend([f"file '{escaped}'", f"duration {piece:.3f}"])
                remaining -= piece
                show_b = not show_b
            elapsed += seg_duration
            if single_audio is None:
                escaped_audio = Path(segment_audios[index]).as_posix().replace("'", "'\\''")
                audio_lines.append(f"file '{escaped_audio}'")
        timeline_lines.append(timeline_lines[-2])
        (temp / "timeline.txt").write_text("\n".join(timeline_lines) + "\n", encoding="utf-8")
        if single_audio is not None:
            (temp / "audio.txt").write_text(
                f"file '{Path(single_audio).resolve().as_posix().replace(chr(39), chr(39) + chr(92) + chr(39))}'\n",
                encoding="utf-8",
            )
        else:
            (temp / "audio.txt").write_text("\n".join(audio_lines) + "\n", encoding="utf-8")

        video_filter = (
            "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p[v]"
        )
        command = [
            ffmpeg, "-y", "-nostdin",
            "-f", "concat", "-safe", "0", "-i", str(temp / "timeline.txt"),
            "-f", "concat", "-safe", "0", "-i", str(temp / "audio.txt"),
            "-filter_complex", video_filter,
            "-map", "[v]", "-map", "1:a", "-t", f"{duration:.3f}", "-r", "24",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", str(output_path),
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(60, int(duration * 4)),
        )
        if completed.returncode != 0:
            raise RuntimeError("ffmpeg 逐句讲解合成失败：" + completed.stderr[-800:])
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("逐句讲解视频未生成文件。")
    probe_video(output_path)
    return output_path


def _concat_cards_with_audio(
    output_path: Path, *, cards: list, durations: list, audios: list,
    single_audio: Path | None = None,
) -> Path:
    """纯文字逐句卡与音频的确定性合成（无老师形象时的完整路径）。

    single_audio 提供时整段音轨直混一次（逐句 TTS 不可用的兜底），audios 被忽略。
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg 未安装。")
    if single_audio is None and len(audios) != len(cards):
        raise RuntimeError("卡片与逐句音频数量不一致。")
    total = min(600.0, max(1.0, sum(float(d) for d in durations)))
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kaki-cards-") as temp_dir:
        temp = Path(temp_dir)
        timeline_lines = []
        for card, seg_duration in zip(cards, durations):
            escaped = card.as_posix().replace("'", "'\''")
            timeline_lines.extend([f"file '{escaped}'", f"duration {float(seg_duration):.3f}"])
        timeline_lines.append(timeline_lines[-2])
        (temp / "timeline.txt").write_text("\n".join(timeline_lines) + "\n", encoding="utf-8")
        if single_audio is not None:
            audio_lines = [
                f"file '{Path(single_audio).resolve().as_posix().replace(chr(39), chr(39) + chr(92) + chr(39))}'",
            ]
        else:
            audio_lines = [f"file '{Path(a).as_posix().replace(chr(39), chr(39) + chr(92) + chr(39))}'" for a in audios]
        (temp / "audio.txt").write_text("\n".join(audio_lines) + "\n", encoding="utf-8")
        video_filter = (
            "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p[v]"
        )
        command = [
            ffmpeg, "-y", "-nostdin",
            "-f", "concat", "-safe", "0", "-i", str(temp / "timeline.txt"),
            "-f", "concat", "-safe", "0", "-i", str(temp / "audio.txt"),
            "-filter_complex", video_filter,
            "-map", "[v]", "-map", "1:a", "-t", f"{total:.3f}", "-r", "24",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", str(output_path),
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(60, int(total * 4)),
        )
        if completed.returncode != 0:
            raise RuntimeError("ffmpeg 卡片合成失败：" + completed.stderr[-800:])
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("卡片视频未生成文件。")
    probe_video(output_path)
    return output_path
