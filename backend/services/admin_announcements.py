"""管理端公告服务"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc

from database.models import Announcement, UserAnnouncementRead
from database.admin_models import AdminAnnouncementRevision


def record_revision(db, ann, admin_id, action):
    db.add(AdminAnnouncementRevision(announcement_id=ann.id, admin_id=admin_id,
        action=action, snapshot=_announcement_to_dict(ann)))


async def list_announcements(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    status_filter: Optional[str] = None,
) -> tuple[List[Announcement], int]:
    conditions = []
    if status_filter:
        conditions.append(Announcement.status == status_filter)

    count_q = select(func.count(Announcement.id))
    if conditions:
        count_q = count_q.where(and_(*conditions))
    total_r = await db.execute(count_q)
    total = total_r.scalar() or 0

    q = select(Announcement).order_by(desc(Announcement.created_at))
    if conditions:
        q = q.where(and_(*conditions))
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()

    return list(rows), total


async def get_announcement(db: AsyncSession, announcement_id: int) -> Optional[Announcement]:
    r = await db.execute(select(Announcement).where(Announcement.id == announcement_id))
    return r.scalar_one_or_none()


async def create_announcement(
    db: AsyncSession, admin_id: int, title: str, content: str,
    level: str = "info", expires_at: Optional[datetime] = None,
) -> Announcement:
    ann = Announcement(
        title=title, content=content, level=level,
        status="draft", target_scope="all_users",
        created_by=admin_id, expires_at=expires_at,
    )
    db.add(ann)
    await db.flush()
    record_revision(db, ann, admin_id, "create")
    await db.commit()
    await db.refresh(ann)
    return ann


async def update_announcement(
    db: AsyncSession, announcement_id: int, admin_id: int = None, **kwargs,
) -> Optional[Announcement]:
    ann = await get_announcement(db, announcement_id)
    if not ann:
        return None
    for k, v in kwargs.items():
        if hasattr(ann, k):
            setattr(ann, k, v)
    record_revision(db, ann, admin_id or ann.created_by, "revise" if ann.status == "published" else "edit")
    await db.commit()
    await db.refresh(ann)
    return ann


async def publish_announcement(db: AsyncSession, announcement_id: int, admin_id: int = None) -> Optional[Announcement]:
    ann = await get_announcement(db, announcement_id)
    if not ann:
        return None
    if ann.status == "published":
        return ann
    ann.status = "published"
    ann.published_at = datetime.utcnow()
    record_revision(db, ann, admin_id or ann.created_by, "publish")
    await db.commit()
    await db.refresh(ann)
    return ann


async def revoke_announcement(db: AsyncSession, announcement_id: int, admin_id: int = None) -> Optional[Announcement]:
    ann = await get_announcement(db, announcement_id)
    if not ann:
        return None
    ann.status = "revoked"
    record_revision(db, ann, admin_id or ann.created_by, "revoke")
    await db.commit()
    await db.refresh(ann)
    return ann


async def get_active_announcements_for_user(db: AsyncSession, user_id: int) -> List[dict]:
    now = datetime.utcnow()
    rows = (await db.execute(
        select(Announcement).where(
            and_(
                Announcement.status == "published",
                Announcement.target_scope == "all_users",
                func.coalesce(Announcement.expires_at, datetime(2099, 1, 1)) > now,
            )
        ).order_by(desc(Announcement.published_at))
    )).scalars().all()

    ann_ids = [a.id for a in rows]
    read_map = {}
    if ann_ids:
        read_rows = (await db.execute(
            select(UserAnnouncementRead).where(
                and_(
                    UserAnnouncementRead.user_id == user_id,
                    UserAnnouncementRead.announcement_id.in_(ann_ids),
                )
            )
        )).scalars().all()
        read_map = {r.announcement_id: r.is_read for r in read_rows}

    return [
        {
            "id": a.id, "title": a.title, "content": a.content,
            "level": a.level,
            "published_at": a.published_at.isoformat() if a.published_at else None,
            "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            "is_read": read_map.get(a.id, False),
        }
        for a in rows
    ]


async def mark_announcement_read(db: AsyncSession, user_id: int, announcement_id: int) -> bool:
    ann = await get_announcement(db, announcement_id)
    if not ann:
        return False

    r = await db.execute(
        select(UserAnnouncementRead).where(
            and_(
                UserAnnouncementRead.user_id == user_id,
                UserAnnouncementRead.announcement_id == announcement_id,
            )
        )
    )
    existing = r.scalar_one_or_none()
    if existing:
        existing.is_read = True
        existing.read_at = datetime.utcnow()
    else:
        db.add(UserAnnouncementRead(
            user_id=user_id, announcement_id=announcement_id,
            is_read=True, read_at=datetime.utcnow(),
        ))
    await db.commit()
    return True


def _announcement_to_dict(a: Announcement) -> dict:
    return {
        "id": a.id, "title": a.title, "content": a.content,
        "level": a.level, "status": a.status,
        "target_scope": a.target_scope,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "created_by": a.created_by,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }
