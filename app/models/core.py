"""Approved database models."""

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, Enum, Index, event, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, validates

from app.extensions import db
from app.models.enums import (
    BlockScope,
    NotificationType,
    PrepPeriod,
    RequestPriority,
    RequestStatus,
    UserRole,
)

PK = BigInteger().with_variant(db.Integer, "sqlite")


def now_utc() -> datetime:
    return datetime.now(UTC)


def enum_type(enum_class, name):
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        validate_strings=True,
        create_constraint=True,
    )


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )


class SchoolClass(TimestampMixin, db.Model):
    __tablename__ = "school_classes"
    id = db.Column(PK, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class Room(TimestampMixin, db.Model):
    __tablename__ = "rooms"
    id = db.Column(PK, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class User(TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(role = 'MONITOR' AND class_id IS NOT NULL) OR "
            "(role <> 'MONITOR' AND class_id IS NULL)",
            name="ck_user_monitor_class",
        ),
    )
    id = db.Column(PK, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    role = db.Column(enum_type(UserRole, "user_role"), nullable=False)
    class_id = db.Column(PK, db.ForeignKey("school_classes.id"), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=True)
    school_class = db.relationship("SchoolClass", backref="monitors")

    @validates("role", "class_id")
    def validate_class_assignment(self, key, value):
        role = value if key == "role" else self.role
        class_id = value if key == "class_id" else self.class_id
        if role and role != UserRole.MONITOR and class_id is not None:
            raise ValueError("Only monitor accounts may have an assigned class")
        return value


class BookingRequest(TimestampMixin, db.Model):
    __tablename__ = "booking_requests"
    __table_args__ = (
        Index("ix_booking_request_queue", "status", "priority", "created_at"),
    )
    id = db.Column(PK, primary_key=True)
    requester_id = db.Column(PK, db.ForeignKey("users.id"), nullable=False, index=True)
    class_id = db.Column(
        PK, db.ForeignKey("school_classes.id"), nullable=False, index=True
    )
    teacher_id = db.Column(PK, db.ForeignKey("users.id"), nullable=False, index=True)
    subject = db.Column(db.String(120), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    priority = db.Column(enum_type(RequestPriority, "request_priority"), nullable=False)
    status = db.Column(
        enum_type(RequestStatus, "request_status"),
        nullable=False,
        default=RequestStatus.PENDING,
        index=True,
    )
    rejection_reason = db.Column(db.Text)
    cancelled_at = db.Column(db.DateTime(timezone=True))
    requester = db.relationship("User", foreign_keys=[requester_id])
    teacher = db.relationship("User", foreign_keys=[teacher_id])
    school_class = db.relationship("SchoolClass")

    def apply_requester_rules(self):
        if self.requester is None:
            raise ValueError("A requester is required")
        if self.requester.role == UserRole.TEACHER:
            self.teacher = self.requester
            self.priority = RequestPriority.HIGH
        elif self.requester.role == UserRole.MONITOR:
            if self.requester.class_id is None and self.requester.school_class is None:
                raise ValueError("Monitor requester requires an assigned class")
            self.school_class = self.requester.school_class
            self.priority = RequestPriority.NORMAL
            if self.teacher is None or self.teacher.role != UserRole.TEACHER:
                raise ValueError("Monitor requests require a responsible teacher")
        else:
            raise ValueError("Only teachers and monitors may submit requests")


class ScheduledBooking(TimestampMixin, db.Model):
    __tablename__ = "scheduled_bookings"
    id = db.Column(PK, primary_key=True)
    request_id = db.Column(
        PK, db.ForeignKey("booking_requests.id"), unique=True, nullable=False
    )
    schedule_date = db.Column(db.Date, nullable=False, index=True)
    prep = db.Column(enum_type(PrepPeriod, "prep_period"), nullable=False)
    room_id = db.Column(PK, db.ForeignKey("rooms.id"), nullable=False)
    class_id = db.Column(PK, db.ForeignKey("school_classes.id"), nullable=False)
    teacher_id = db.Column(PK, db.ForeignKey("users.id"), nullable=False)
    scheduled_by_id = db.Column(PK, db.ForeignKey("users.id"), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    cancelled_at = db.Column(db.DateTime(timezone=True))
    request = db.relationship(
        "BookingRequest", backref=db.backref("scheduled_booking", uselist=False)
    )
    room = db.relationship("Room")
    school_class = db.relationship("SchoolClass")
    teacher = db.relationship("User", foreign_keys=[teacher_id])
    scheduled_by = db.relationship("User", foreign_keys=[scheduled_by_id])


for index_name, column in (
    ("uq_active_room_slot", "room_id"),
    ("uq_active_class_slot", "class_id"),
    ("uq_active_teacher_slot", "teacher_id"),
):
    Index(
        index_name,
        ScheduledBooking.schedule_date,
        ScheduledBooking.prep,
        getattr(ScheduledBooking, column),
        unique=True,
        postgresql_where=ScheduledBooking.is_active.is_(True),
        sqlite_where=ScheduledBooking.is_active.is_(True),
    )


class RoomBlock(db.Model):
    __tablename__ = "room_blocks"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'SLOT' AND room_id IS NOT NULL AND prep IS NOT NULL) OR "
            "(scope = 'ROOM_DAY' AND room_id IS NOT NULL AND prep IS NULL) OR "
            "(scope = 'DAY' AND room_id IS NULL AND prep IS NULL)",
            name="ck_room_block_scope",
        ),
    )
    id = db.Column(PK, primary_key=True)
    block_date = db.Column(db.Date, nullable=False, index=True)
    scope = db.Column(enum_type(BlockScope, "block_scope"), nullable=False)
    room_id = db.Column(PK, db.ForeignKey("rooms.id"))
    prep = db.Column(enum_type(PrepPeriod, "prep_period"))
    reason = db.Column(db.Text)
    created_by_id = db.Column(PK, db.ForeignKey("users.id"), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    removed_at = db.Column(db.DateTime(timezone=True))
    removed_by_id = db.Column(PK, db.ForeignKey("users.id"))
    room = db.relationship("Room")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    removed_by = db.relationship("User", foreign_keys=[removed_by_id])


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(PK, primary_key=True)
    user_id = db.Column(PK, db.ForeignKey("users.id"), nullable=False, index=True)
    type = db.Column(enum_type(NotificationType, "notification_type"), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    booking_request_id = db.Column(PK, db.ForeignKey("booking_requests.id"))
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=now_utc, index=True
    )
    read_at = db.Column(db.DateTime(timezone=True))
    user = db.relationship("User")
    booking_request = db.relationship("BookingRequest")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(PK, primary_key=True)
    actor_id = db.Column(PK, db.ForeignKey("users.id"))
    action = db.Column(db.String(100), nullable=False, index=True)
    entity_type = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(PK)
    details = db.Column(JSONB().with_variant(JSON, "sqlite"))
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=now_utc, index=True
    )
    actor = db.relationship("User")


class SystemSettings(db.Model):
    __tablename__ = "system_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_system_settings_single_row"),
        CheckConstraint(
            "max_pending_requests > 0", name="ck_system_settings_max_pending_positive"
        ),
        CheckConstraint(
            "reopen_threshold >= 0", name="ck_system_settings_reopen_nonnegative"
        ),
        CheckConstraint(
            "reopen_threshold < max_pending_requests",
            name="ck_system_settings_reopen_below_max",
        ),
        CheckConstraint(
            "planning_window_days > 0",
            name="ck_system_settings_window_positive",
        ),
    )
    id = db.Column(db.SmallInteger, primary_key=True, default=1)
    max_pending_requests = db.Column(db.Integer, nullable=False, default=12)
    reopen_threshold = db.Column(db.Integer, nullable=False, default=9)
    planning_window_days = db.Column(db.Integer, nullable=False, default=3)
    booking_queue_locked = db.Column(db.Boolean, nullable=False, default=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc
    )


@event.listens_for(Session, "before_flush")
def validate_models(_session, _context, _instances):
    for obj in _session.new | _session.dirty:
        if isinstance(obj, User):
            has_class = obj.class_id is not None or obj.school_class is not None
            if obj.role == UserRole.MONITOR and not has_class:
                raise ValueError("Monitor accounts require an assigned class")
            if obj.role != UserRole.MONITOR and has_class:
                raise ValueError("Non-monitor accounts cannot retain an assigned class")
        elif isinstance(obj, BookingRequest):
            if obj in _session.new:
                obj.apply_requester_rules()
            else:
                state = inspect(obj)
                immutable = (
                    "requester_id",
                    "class_id",
                    "teacher_id",
                    "priority",
                    "requester",
                    "school_class",
                    "teacher",
                )
                if any(state.attrs[name].history.has_changes() for name in immutable):
                    raise ValueError("Persisted request identity fields are immutable")
        elif isinstance(obj, RoomBlock):
            has_room = obj.room_id is not None or obj.room is not None
            if obj.scope == BlockScope.SLOT and (not has_room or obj.prep is None):
                raise ValueError("SLOT blocks require room and prep")
            if obj.scope == BlockScope.ROOM_DAY and (
                not has_room or obj.prep is not None
            ):
                raise ValueError("ROOM_DAY blocks require room and no prep")
            if obj.scope == BlockScope.DAY and (has_room or obj.prep is not None):
                raise ValueError("DAY blocks require no room or prep")
