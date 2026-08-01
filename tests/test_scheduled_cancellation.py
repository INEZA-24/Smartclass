"""Milestone 7 scheduled-booking cancellation verification."""

from unittest.mock import patch

import pytest
from flask_login import login_user
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from test_schedule_changes import database, login, scheduled  # noqa: F401

from app.extensions import db
from app.models import (
    AuditLog,
    Notification,
    NotificationType,
    RequestStatus,
    ScheduledBooking,
    SystemSettings,
    User,
    UserRole,
)
from app.scheduling import SchedulingError, cancel_booking


def cancellation_counts():
    return (
        db.session.scalar(
            db.select(db.func.count()).select_from(Notification).where(
                Notification.type == NotificationType.CANCELLED
            )
        ),
        db.session.scalar(
            db.select(db.func.count()).select_from(AuditLog).where(
                AuditLog.action == "BOOKING_CANCELLED"
            )
        ),
    )


@pytest.mark.parametrize("actor_key", ["scheduler", "admin"])
def test_cancellation_is_soft_atomic_and_idempotent(app, actor_key):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids[actor_key]))
        settings = db.session.get(SystemSettings, 1)
        queue_before = (
            settings.booking_queue_locked,
            settings.max_pending_requests,
            settings.reopen_threshold,
        )
        booking = db.session.get(ScheduledBooking, ids["booking"])
        slot = (booking.schedule_date, booking.prep, booking.room_id)
        db.session.rollback()

        assert cancel_booking(ids["booking"])[0]
        booking = db.session.get(ScheduledBooking, ids["booking"])
        assert booking is not None
        assert not booking.is_active
        assert booking.cancelled_at is not None
        assert booking.request.status == RequestStatus.CANCELLED
        assert booking.request.cancelled_at is not None
        settings = db.session.get(SystemSettings, 1)
        assert (
            settings.booking_queue_locked,
            settings.max_pending_requests,
            settings.reopen_threshold,
        ) == queue_before
        assert db.session.scalar(
            db.select(ScheduledBooking.id).where(
                ScheduledBooking.schedule_date == slot[0],
                ScheduledBooking.prep == slot[1],
                ScheduledBooking.room_id == slot[2],
                ScheduledBooking.is_active.is_(True),
            )
        ) is None
        assert cancellation_counts() == (1, 1)
        db.session.rollback()

        assert not cancel_booking(ids["booking"])[0]
        assert cancellation_counts() == (1, 1)


@pytest.mark.parametrize("name", ["teacher", "monitor"])
def test_requester_roles_cannot_cancel_booking(client, app, name):
    ids = scheduled(app)
    login(client, name)
    response = client.post(f"/scheduler/bookings/{ids['booking']}/cancel")
    assert response.status_code == 403


def test_anonymous_cancellation_redirects_to_login(client, app):
    ids = scheduled(app)
    response = client.post(f"/scheduler/bookings/{ids['booking']}/cancel")
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_forced_password_actor_cannot_cancel(client, app):
    ids = scheduled(app)
    with app.app_context():
        actor = db.session.get(User, ids["admin"])
        actor.must_change_password = True
        db.session.commit()
    login(client, "admin")
    response = client.post(f"/scheduler/bookings/{ids['booking']}/cancel")
    assert response.status_code == 302
    assert "/auth/change-password" in response.location


@pytest.mark.parametrize(
    ("column", "value"),
    [("is_active", False), ("role", UserRole.TEACHER)],
)
def test_cancellation_rejects_stale_actor(app, column, value):
    ids = scheduled(app)
    with app.test_request_context():
        actor = db.session.get(User, ids["admin"])
        login_user(actor)
        stale_value = getattr(actor, column)
        with db.engine.begin() as connection:
            connection.execute(
                update(User)
                .where(User.id == actor.id)
                .values(**{column: value})
                .execution_options(synchronize_session=False)
            )
        assert getattr(actor, column) == stale_value
        with pytest.raises(SchedulingError, match="not permitted"):
            cancel_booking(ids["booking"])
        assert not db.session().in_transaction()
        booking = db.session.get(ScheduledBooking, ids["booking"])
        assert booking.is_active
        assert booking.request.status == RequestStatus.SCHEDULED
        assert cancellation_counts() == (0, 0)

@pytest.mark.parametrize("failure_kind", ["notification", "audit", "commit"])
def test_cancellation_persistence_failure_rolls_back(app, failure_kind):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        settings = db.session.get(SystemSettings, 1)
        locked_before = settings.booking_queue_locked
        db.session.rollback()
        error = SQLAlchemyError("forced cancellation failure")
        original_add = db.session.add

        def failing_add(item):
            if failure_kind == "notification" and isinstance(item, Notification):
                raise error
            if failure_kind == "audit" and isinstance(item, AuditLog):
                raise error
            return original_add(item)

        context = (
            patch.object(db.session, "commit", side_effect=error)
            if failure_kind == "commit"
            else patch.object(db.session, "add", side_effect=failing_add)
        )
        with context, pytest.raises(SchedulingError, match="Unable to cancel"):
            cancel_booking(ids["booking"])
        assert not db.session().in_transaction()
        booking = db.session.get(ScheduledBooking, ids["booking"])
        assert booking.is_active
        assert booking.cancelled_at is None
        assert booking.request.status == RequestStatus.SCHEDULED
        assert booking.request.cancelled_at is None
        assert db.session.get(SystemSettings, 1).booking_queue_locked is locked_before
        assert cancellation_counts() == (0, 0)
