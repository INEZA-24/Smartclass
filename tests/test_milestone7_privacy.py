"""Milestone 7 notification and audit privacy verification."""

import json

from flask_login import login_user
from test_schedule_changes import database, scheduled, seed  # noqa: F401

from app.extensions import db
from app.models import (
    AuditLog,
    BookingRequest,
    Notification,
    NotificationType,
    PrepPeriod,
    ScheduledBooking,
    User,
)
from app.scheduling import (
    cancel_booking,
    planning_window,
    reject_request,
    reschedule_booking,
)

PRIVATE_REASON = "PRIVATE-BOOKING-REASON-DO-NOT-DISCLOSE"
CSRF_VALUE = "csrf_token=secret-form-token"
FORM_PAYLOAD = {
    "csrf_token": "secret-form-token",
    "reason": PRIVATE_REASON,
}


def serialized_audits():
    records = db.session.scalars(db.select(AuditLog)).all()
    return "\n".join(json.dumps(record.details, sort_keys=True) for record in records)


def expected_notification(notification_type):
    records = db.session.scalars(
        db.select(Notification).where(Notification.type == notification_type)
    ).all()
    assert len(records) == 1
    return records[0]


def expected_audit(action):
    records = db.session.scalars(
        db.select(AuditLog).where(AuditLog.action == action)
    ).all()
    assert len(records) == 1
    return records[0]


def assert_private_data_absent(details):
    text = json.dumps(details, sort_keys=True)
    assert PRIVATE_REASON not in text
    assert CSRF_VALUE not in text
    assert "secret-form-token" not in text
    assert json.dumps(FORM_PAYLOAD, sort_keys=True) not in text


def test_rejection_notification_and_audit_privacy(app):
    ids = seed(app)
    rejection = "Insufficient preparation time"
    with app.test_request_context():
        request = db.session.get(BookingRequest, ids["request"])
        request.reason = PRIVATE_REASON
        db.session.commit()
        login_user(db.session.get(User, ids["scheduler"]))
        assert reject_request(ids["request"], rejection)
        notification = expected_notification(NotificationType.REJECTED)
        assert rejection in notification.message
        assert PRIVATE_REASON not in notification.message
        audit = expected_audit("REQUEST_REJECTED")
        assert_private_data_absent(audit.details)
        assert rejection not in json.dumps(audit.details, sort_keys=True)
        audit_text = serialized_audits()
        assert PRIVATE_REASON not in audit_text
        assert rejection not in audit_text
        assert CSRF_VALUE not in audit_text


def test_rescheduling_notification_and_audit_privacy(app):
    ids = scheduled(app)
    with app.test_request_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.request.reason = PRIVATE_REASON
        db.session.commit()
        login_user(db.session.get(User, ids["admin"]))
        reschedule_booking(
            ids["booking"], planning_window()[1], PrepPeriod.PREP_2, ids["room2"]
        )
        notification = expected_notification(NotificationType.RESCHEDULED)
        assert PRIVATE_REASON not in notification.message
        audit = expected_audit("BOOKING_RESCHEDULED")
        assert_private_data_absent(audit.details)
        audit_text = serialized_audits()
        assert PRIVATE_REASON not in audit_text
        assert CSRF_VALUE not in audit_text


def test_cancellation_notification_and_audit_privacy(app):
    ids = scheduled(app)
    with app.test_request_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.request.reason = PRIVATE_REASON
        db.session.commit()
        login_user(db.session.get(User, ids["admin"]))
        cancel_booking(ids["booking"])
        notification = expected_notification(NotificationType.CANCELLED)
        assert PRIVATE_REASON not in notification.message
        audit = expected_audit("BOOKING_CANCELLED")
        assert_private_data_absent(audit.details)
        audit_text = serialized_audits()
        assert PRIVATE_REASON not in audit_text
        assert CSRF_VALUE not in audit_text
