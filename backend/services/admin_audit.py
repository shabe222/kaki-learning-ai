"""管理端审计日志服务"""
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from database.models import AdminAuditLog


async def write_audit_log(
    db: AsyncSession,
    admin_id: int,
    action: str,
    target_type: str,
    target_id: Optional[str] = None,
    success: bool = True,
    summary: Optional[str] = None,
) -> AdminAuditLog:
    log = AdminAuditLog(
        admin_id=admin_id, action=action, target_type=target_type,
        target_id=target_id, success=success, summary=summary,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def list_audit_logs(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    admin_id: Optional[int] = None,
    action_filter: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
) -> tuple[List[dict], int]:
    from sqlalchemy import and_
    conditions = []
    if admin_id is not None:
        conditions.append(AdminAuditLog.admin_id == admin_id)
    if action_filter:
        conditions.append(AdminAuditLog.action == action_filter)
    if target_type:
        conditions.append(AdminAuditLog.target_type == target_type)
    if target_id is not None:
        conditions.append(AdminAuditLog.target_id == target_id)

    count_q = select(func.count(AdminAuditLog.id))
    if conditions:
        count_q = count_q.where(and_(*conditions))
    total_r = await db.execute(count_q)
    total = total_r.scalar() or 0

    q = select(AdminAuditLog).order_by(desc(AdminAuditLog.created_at))
    if conditions:
        q = q.where(and_(*conditions))
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()

    items = [
        {
            "id": r.id, "admin_id": r.admin_id, "action": r.action,
            "target_type": r.target_type, "target_id": r.target_id,
            "success": r.success, "summary": r.summary,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return items, total
