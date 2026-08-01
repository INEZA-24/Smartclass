"""Authenticated notification-center routes."""

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.blueprints.notifications import bp
from app.blueprints.notifications.forms import NotificationActionForm
from app.extensions import db
from app.models import BookingRequest, Notification, ScheduledBooking, UserRole
from app.notifications import (
    NotificationNotFoundError,
    NotificationUpdateError,
    mark_all_notifications_read,
    mark_notification_read,
)


def related_request_url(notification):
    """Return only an existing destination authorized for this notification owner."""
    request_record = notification.booking_request
    if request_record is None:
        return None
    if request_record.requester_id == current_user.id:
        if current_user.role == UserRole.TEACHER:
            return url_for("requester.teacher_requests")
        if current_user.role == UserRole.MONITOR:
            return url_for("requester.monitor_requests")
    if current_user.role in {UserRole.ADMIN, UserRole.SCHEDULER}:
        booking = request_record.scheduled_booking
        if booking is not None:
            return url_for("scheduler.booking_detail", booking_id=booking.id)
    return None


@bp.get("/")
@login_required
def index():
    records = db.session.scalars(
        db.select(Notification)
        .where(Notification.user_id == current_user.id)
        .options(
            selectinload(Notification.booking_request)
            .load_only(BookingRequest.id, BookingRequest.requester_id)
            .selectinload(BookingRequest.scheduled_booking)
            .load_only(ScheduledBooking.id)
        )
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    ).all()
    links = {record.id: related_request_url(record) for record in records}
    unread_count = sum(not record.is_read for record in records)
    return render_template(
        "notifications/index.html",
        notifications=records,
        related_links=links,
        unread_count=unread_count,
        action_form=NotificationActionForm(),
    )


@bp.post("/<int:notification_id>/read")
@login_required
def mark_read(notification_id):
    if not NotificationActionForm().validate_on_submit():
        abort(400)
    try:
        changed = mark_notification_read(notification_id, current_user.id)
    except NotificationNotFoundError:
        abort(404)
    except NotificationUpdateError as exc:
        flash(str(exc), "danger")
    else:
        flash(
            "Notification marked as read."
            if changed
            else "Notification was already read.",
            "success" if changed else "info",
        )
    return redirect(url_for("notifications.index"))


@bp.post("/read-all")
@login_required
def mark_all_read():
    if not NotificationActionForm().validate_on_submit():
        abort(400)
    try:
        count = mark_all_notifications_read(current_user.id)
    except NotificationUpdateError as exc:
        flash(str(exc), "danger")
    else:
        flash(
            "All notifications marked as read."
            if count
            else "There were no unread notifications.",
            "success" if count else "info",
        )
    return redirect(url_for("notifications.index"))
