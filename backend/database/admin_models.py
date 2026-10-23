"""Administrative state only. No learner evidence or duplicate artifact store."""
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON, ForeignKey, UniqueConstraint, func
from database.models import Base


class AdminPreference(Base):
    __tablename__ = "admin_preferences"
    admin_id = Column(Integer, ForeignKey("admin_users.id"), primary_key=True)
    theme = Column(String(12), nullable=False, default="kaki")
    compact = Column(Boolean, nullable=False, default=False)
    auto_refresh = Column(Boolean, nullable=False, default=True)


class AdminAccountPurpose(Base):
    __tablename__ = "admin_account_purposes"
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    purpose = Column(String(20), nullable=False)
    reason = Column(String(500), nullable=False)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False)


class AdminIssue(Base):
    __tablename__ = "admin_issues"
    id = Column(Integer, primary_key=True)
    source_key = Column(String(140), unique=True, nullable=False, index=True)
    category = Column(String(40), nullable=False)
    severity = Column(String(20), nullable=False, default="warning")
    object_type = Column(String(20), nullable=False)
    object_id = Column(String(80), nullable=False)
    user_id = Column(Integer, nullable=True, index=True)
    state = Column(String(20), nullable=False, default="open", index=True)
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    owner_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    result = Column(String(1000), nullable=True)
    evidence = Column(String(500), nullable=True)


class AdminSampleGrant(Base):
    __tablename__ = "admin_sample_grants"
    __table_args__ = (UniqueConstraint("admin_id", "artifact_id"),)
    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False)
    artifact_id = Column(Integer, ForeignKey("generated_items.id"), nullable=False)
    granted_by = Column(Integer, ForeignKey("admin_users.id"), nullable=False)
    reason = Column(String(500), nullable=False)
    active = Column(Boolean, nullable=False, default=True)


class AdminAnnouncementRevision(Base):
    __tablename__ = "admin_announcement_revisions"
    id = Column(Integer, primary_key=True)
    announcement_id = Column(Integer, ForeignKey("announcements.id"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False)
    action = Column(String(30), nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AdminRetry(Base):
    __tablename__ = "admin_retries"
    id = Column(Integer, primary_key=True)
    request_key = Column(String(64), unique=True, nullable=False)
    source_artifact_id = Column(Integer, ForeignKey("generated_items.id"), nullable=False)
    target_artifact_id = Column(Integer, ForeignKey("generated_items.id"), nullable=False)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False)
    reason = Column(String(500), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AdminCapabilityProbe(Base):
    __tablename__ = "admin_capability_probes"
    capability = Column(String(40), primary_key=True)
    state = Column(String(20), nullable=False)
    checked_at = Column(DateTime, nullable=False)
    basis = Column(String(200), nullable=False)


class AdminRuntimeSetting(Base):
    __tablename__ = "admin_runtime_settings"
    key = Column(String(40), primary_key=True)
    value = Column(String(100), nullable=False)
    previous_value = Column(String(100), nullable=True)
    applied_at = Column(DateTime, nullable=True)
