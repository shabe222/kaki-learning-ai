"""How long someone has actually spent here, and how to say it back to them.

The product had no record of a learner's time at all: messages carry no
timestamp, the minutes column on `learning_progress` is never written, and
`answer_records.time_spent_sec` only covers answers to assessments. A clock that
invented its number from message gaps would be worse than no clock -- one
message after a four-hour break is not four hours of study.

So presence is measured directly. The browser reports a heartbeat while the tab
is visible and the person is at the keyboard; this module accumulates it per day
and answers the questions a learner actually asks: how long today, is this week
more than last week, what is the longest run I have kept.

The summary is shaped for a page rather than a dashboard: one number that
matters, a short honest sentence about it, and the raw days behind it. The
frontend does the designing; nothing here decides how it looks.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DaryaStudyTime

# A heartbeat is a claim about time already spent. One report may not claim more
# than this, so a clock that was left running, or a tab that slept, cannot
# silently turn into hours.
MAX_REPORT_SECONDS = 180
# Below this, a day did not happen. Thirty seconds is a page load, not studying.
MIN_COUNTED_SECONDS = 60


def _today() -> date:
    return datetime.now().date()


async def record(
    db: AsyncSession, *, user_id: int, seconds: int, day: date | None = None
) -> int:
    """Add reported seconds to a day. Returns the day's new total."""
    amount = max(0, min(int(seconds or 0), MAX_REPORT_SECONDS))
    if amount == 0:
        return await seconds_on(db, user_id=user_id, day=day or _today())
    target = day or _today()
    row = (
        await db.execute(
            select(DaryaStudyTime).where(
                DaryaStudyTime.user_id == user_id, DaryaStudyTime.day == target
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = DaryaStudyTime(user_id=user_id, day=target, seconds=amount)
        db.add(row)
    else:
        row.seconds = int(row.seconds or 0) + amount
    await db.flush()
    return int(row.seconds or 0)


async def seconds_on(db: AsyncSession, *, user_id: int, day: date) -> int:
    value = (
        await db.execute(
            select(DaryaStudyTime.seconds).where(
                DaryaStudyTime.user_id == user_id, DaryaStudyTime.day == day
            )
        )
    ).scalar_one_or_none()
    return int(value or 0)


@dataclass(frozen=True)
class StudyDay:
    day: date
    seconds: int

    @property
    def counted(self) -> bool:
        return self.seconds >= MIN_COUNTED_SECONDS


def _streaks(days: Sequence[StudyDay]) -> tuple[int, int]:
    """(current run ending today or yesterday, longest run ever)."""
    counted = [item.day for item in days if item.counted]
    if not counted:
        return 0, 0
    longest = run = 1
    for previous, current in zip(counted, counted[1:]):
        run = run + 1 if (current - previous).days == 1 else 1
        longest = max(longest, run)
    today = _today()
    current_run = 0
    if counted[-1] in (today, today - timedelta(days=1)):
        current_run = 1
        index = len(counted) - 1
        while index > 0 and (counted[index] - counted[index - 1]).days == 1:
            current_run += 1
            index -= 1
    return current_run, longest


def _describe(*, today_seconds: int, week_seconds: int, last_week_seconds: int,
              active_days: int, total_days: int, longest: int) -> str:
    """One sentence a person would actually want to read about themselves.

    Written to be true at every scale: a first day gets a welcome, not a
    productivity verdict, and nobody is told they are behind.
    """
    if total_days == 0:
        return "今天是第一天。接下来发生什么，都会从这里开始长出来。"
    if today_seconds < MIN_COUNTED_SECONDS:
        if active_days >= 3:
            return f"今天还没开始。你这个月已经有 {active_days} 天坐下来了——不差这一天，来了就接着算。"
        return "今天还没开始。慢慢来，这里不会催你。"
    if total_days <= 2:
        return "刚开始的记录还很短，但它已经不再是一条平线了。"
    if last_week_seconds and week_seconds > last_week_seconds * 1.2:
        return "这一周比上一周长。不是突然变厉害，是你来了更多次。"
    if last_week_seconds and week_seconds < last_week_seconds * 0.8:
        return "这一周比上一周短。没有关系，时间不是用来追平的。"
    if longest >= 5:
        return f"你最长连续来过 {longest} 天。这种节奏比某一天特别久更难得。"
    return "节奏是平的。平稳的一段，通常也是最走得下去的一段。"


def _minutes(seconds: int) -> int:
    return int(round(int(seconds or 0) / 60))


async def summary(
    db: AsyncSession, *, user_id: int, days: int = 30
) -> dict[str, Any]:
    """Everything the poster page needs, in one call."""
    window = max(7, min(int(days or 30), 400))
    today = _today()
    start = today - timedelta(days=window - 1)
    rows = (
        await db.execute(
            select(DaryaStudyTime)
            .where(DaryaStudyTime.user_id == user_id, DaryaStudyTime.day >= start)
            .order_by(DaryaStudyTime.day)
        )
    ).scalars().all()
    by_day = {row.day: int(row.seconds or 0) for row in rows}
    series = [StudyDay(day=start + timedelta(days=offset), seconds=by_day.get(start + timedelta(days=offset), 0))
              for offset in range(window)]

    today_seconds = by_day.get(today, 0)
    week_start = today - timedelta(days=6)
    week_seconds = sum(by_day.get(week_start + timedelta(days=offset), 0) for offset in range(7))
    last_week_seconds = sum(
        by_day.get(week_start - timedelta(days=7) + timedelta(days=offset), 0) for offset in range(7)
    )
    month_start = today - timedelta(days=29)
    month_seconds = sum(
        by_day.get(month_start + timedelta(days=offset), 0) for offset in range(30)
    )

    counted = [item for item in series if item.counted]
    current_run, longest = _streaks(series)
    best = max(series, key=lambda item: item.seconds) if series else None

    return {
        "contract_version": "darya_study_time_v1",
        "today_seconds": today_seconds,
        "today_minutes": _minutes(today_seconds),
        "week_seconds": week_seconds,
        "week_minutes": _minutes(week_seconds),
        "last_week_seconds": last_week_seconds,
        "month_seconds": month_seconds,
        "month_minutes": _minutes(month_seconds),
        "month_hours": round(month_seconds / 3600, 1),
        "active_days": len(counted),
        "total_days": len([item for item in series if item.seconds > 0]),
        "window_days": window,
        "current_streak": current_run,
        "longest_streak": longest,
        "best_day": (
            {"day": best.day.isoformat(), "seconds": best.seconds, "minutes": _minutes(best.seconds)}
            if best and best.seconds else None
        ),
        # The seven days a person actually pictures when they think "this week".
        "week": [
            {
                "day": item.day.isoformat(),
                "weekday": item.day.weekday(),
                "seconds": item.seconds,
                "minutes": _minutes(item.seconds),
            }
            for item in series[-7:]
        ],
        "series": [
            {"day": item.day.isoformat(), "seconds": item.seconds, "minutes": _minutes(item.seconds)}
            for item in series
        ],
        "sentence": _describe(
            today_seconds=today_seconds,
            week_seconds=week_seconds,
            last_week_seconds=last_week_seconds,
            active_days=len(counted),
            total_days=len([item for item in series if item.seconds > 0]),
            longest=longest,
        ),
        "counts_only_presence": True,
    }
