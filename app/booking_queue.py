"""Transactional pending-queue helpers."""

from app.extensions import db
from app.models import (
    AuditLog,
    BookingRequest,
    RequestPriority,
    RequestStatus,
    SystemSettings,
    UserRole,
)


def request_origin_role(priority):
    """Return the immutable origin role represented by request priority."""
    if priority == RequestPriority.HIGH:
        return UserRole.TEACHER
    if priority == RequestPriority.NORMAL:
        return UserRole.MONITOR
    raise ValueError("Unsupported booking request priority")


class QueueUnavailableError(RuntimeError):
    pass


class QueueLockedError(RuntimeError):
    pass


def lock_settings():
    settings = db.session.scalar(
        db.select(SystemSettings)
        .where(SystemSettings.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if settings is None:
        raise QueueUnavailableError("System settings are unavailable")
    return settings


def pending_count():
    return db.session.scalar(
        db.select(db.func.count(BookingRequest.id)).where(
            BookingRequest.status == RequestStatus.PENDING
        )
    )


def queue_state():
    settings = db.session.get(SystemSettings, 1)
    return settings, pending_count()


def add_request_audit(actor_id, action, request_record, details=None):
    safe_details = {
        "request_id": request_record.id,
        "requester_role": request_origin_role(request_record.priority).value,
        "class_id": request_record.class_id,
        "teacher_id": request_record.teacher_id,
        "priority": request_record.priority.value,
    }
    if details:
        safe_details.update(details)
    db.session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type="BookingRequest",
            entity_id=request_record.id,
            details=safe_details,
        )
    )


def add_queue_audit(actor_id, action, count, locked):
    db.session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type="SystemSettings",
            entity_id=1,
            details={"queue_count": count, "queue_locked": locked},
        )
    )
