"""High-priority, age-appropriate support for explicit child violence disclosures.

This narrow deterministic gate runs before tutoring/search. It does not infer
abuse from grades or family conflict; it activates only when a user identifies
as a minor and directly says a caregiver or family member hit them.
"""
from __future__ import annotations

import re


_MINOR = re.compile(r"小学|小学生|一年级|二年级|三年级|四年级|五年级|六年级|未成年")
_CAREGIVER_HIT = re.compile(
    r"(?:妈妈|妈|爸爸|爸|父母|家里人|监护人).{0,12}(?:打了我|打我|揍我|踢我|用东西打我)|"
    r"我.{0,24}被(?:妈妈|妈|爸爸|爸|父母|家里人|监护人).{0,12}(?:打|揍|踢)|"
    r"被(?:妈妈|妈|爸爸|爸|父母|家里人|监护人).{0,12}(?:打|揍|踢)"
)


def child_violence_safety_response(message: str) -> str | None:
    text = re.sub(r"\s+", "", str(message or ""))
    age = re.search(r"我(?:今年)?(\d{1,2})岁", text)
    is_minor = bool(_MINOR.search(text)) or bool(age and int(age.group(1)) < 18)
    if not is_minor or not _CAREGIVER_HIT.search(text):
        return None
    return (
        "[tide_center]\n"
        "先说最重要的：因为考试成绩被打不是你的错，你应该被安全地对待。"
        "如果你现在还可能被打、已经受伤，或者很害怕，先去有其他可靠大人的地方，"
        "告诉班主任、信任的老师、亲戚或邻居。如果有立即危险，请打 110；"
        "也可以打 12355 青少年服务台求助。不要一个人去和会打你的人争论。\n\n"
        "语文我也会陪你补，但安全要排在 58 分前面。等你处在安全的地方，"
        "我们再把试卷分成字词、阅读和作文三类，每天只做一个 15 分钟的小任务。\n\n"
        "你现在安全吗，身边有没有一位你信任的大人？\n"
        "[/tide_center]"
    )
