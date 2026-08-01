"""Transactional notification read-state services."""

from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import Notification


class NotificationNotFoundError(RuntimeError):
    """An owned notification was not found."""


class NotificationUpdateError(RuntimeError):
    """A notification read-state update failed safely."""


def _valid_identifier(value):
    return type(value) is int and value > 0


def mark_notification_read(notification_id, user_id):
    """Mark one owned notification read, preserving idempotent timestamps."""
    if not _valid_identifier(notification_id):
        db.session.rollback()
        raise NotificationNotFoundError
    if not _valid_identifier(user_id):
        db.session.rollback()
        raise NotificationUpdateError(
            "Unable to update notifications. Please try again."
        )
    try:
        record = db.session.scalar(
            db.select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if record is None:
            db.session.rollback()
            raise NotificationNotFoundError
        if record.is_read:
            db.session.rollback()
            return False
        record.is_read = True
        record.read_at = datetime.now(UTC)
        db.session.commit()
        return True
    except NotificationNotFoundError:
        raise
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise NotificationUpdateError(
            "Unable to update notifications. Please try again."
        ) from exc


def mark_all_notifications_read(user_id):
    """Atomically mark only one user's unread notifications as read."""
    if not _valid_identifier(user_id):
        db.session.rollback()
        raise NotificationUpdateError(
            "Unable to update notifications. Please try again."
        )
    try:
        read_at = datetime.now(UTC)
        result = db.session.execute(
            db.update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=read_at)
        )
        db.session.commit()
        return result.rowcount
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise NotificationUpdateError(
            "Unable to update notifications. Please try again."
        ) from exc
