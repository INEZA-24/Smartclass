"""Milestone 8 public current-day schedule tests."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from test_schedule_changes import database, make_user, scheduled  # noqa: F401

from app.extensions import db
from app.models import (
    AuditLog,
    BookingRequest,
    Notification,
    NotificationType,
    PrepPeriod,
    RequestStatus,
    RoomBlock,
    ScheduledBooking,
    SchoolClass,
    UserRole,
)

FIXED_DATE = date(2026, 4, 15)


@pytest.fixture(autouse=True)
def fixed_kigali_today():
    with patch(
        "app.blueprints.public.routes.kigali_today", return_value=FIXED_DATE
    ):
        yield


def scheduled_on_fixed_date(app):
    ids = scheduled(app)
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.schedule_date = FIXED_DATE
        db.session.commit()
    return ids


def test_public_schedule_requires_no_login_and_has_empty_state(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"College Saint Andre" in response.data
    assert b"No Smart Class sessions are scheduled for today." in response.data
    assert b"Login" in response.data
    assert b"Notifications" not in response.data


@pytest.mark.parametrize("offset", [-1, 1])
def test_public_schedule_excludes_other_dates(client, app, offset):
    ids = scheduled_on_fixed_date(app)
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.schedule_date = FIXED_DATE + timedelta(days=offset)
        db.session.commit()
    response = client.get("/")
    assert b"S1 A" not in response.data
    assert b"No Smart Class sessions are scheduled for today." in response.data


@pytest.mark.parametrize("active", [False])
def test_public_schedule_excludes_inactive_booking(client, app, active):
    ids = scheduled_on_fixed_date(app)
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.is_active = active
        db.session.commit()
    response = client.get("/")
    assert b"S1 A" not in response.data


def test_public_schedule_shows_only_approved_public_fields(client, app):
    ids = scheduled_on_fixed_date(app)
    private_values = [
        "PRIVATE-BOOKING-REASON",
        "SECRET-SUBJECT",
        "SECRET-REJECTION",
        "SECRET-BLOCK-REASON",
        "SECRET-AUDIT-DETAIL",
        "SECRET-NOTIFICATION-TITLE",
        "SECRET-NOTIFICATION-CONTENT",
        "PRIVATE-REQUESTER-USERNAME",
    ]
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.request.reason = private_values[0]
        booking.request.subject = private_values[1]
        booking.request.rejection_reason = private_values[2]
        booking.room.name = "PUBLIC-ROOM-NAME"
        booking.school_class.name = "PUBLIC-CLASS-NAME"
        booking.teacher.full_name = "PUBLIC-TEACHER-FULL-NAME"
        booking.request.requester.username = private_values[7]
        db.session.add(
            RoomBlock(
                block_date=FIXED_DATE,
                scope="DAY",
                reason=private_values[3],
                created_by_id=ids["scheduler"],
            )
        )
        db.session.add(
            AuditLog(
                actor_id=ids["admin"],
                action="PRIVATE_TEST",
                entity_type="Test",
                details={"secret": private_values[4]},
            )
        )
        db.session.add(
            Notification(
                user_id=ids["teacher"],
                type=NotificationType.SYSTEM,
                title=private_values[5],
                message=private_values[6],
            )
        )
        db.session.commit()
    response = client.get("/")
    assert response.status_code == 200
    assert b"Prep 1" in response.data
    assert b"PUBLIC-ROOM-NAME" in response.data
    assert b"PUBLIC-CLASS-NAME" in response.data
    assert b"PUBLIC-TEACHER-FULL-NAME" in response.data
    assert b"HIGH" not in response.data
    for value in private_values:
        assert value.encode() not in response.data
    assert b"type=\"hidden\"" not in response.data
    # The only script is the shared Bootstrap asset; schedule data is never serialized.
    assert b"<script" in response.data


def test_public_schedule_excludes_soft_cancelled_booking(client, app):
    ids = scheduled_on_fixed_date(app)
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.is_active = False
        booking.cancelled_at = datetime.now(UTC)
        db.session.commit()
    response = client.get("/")
    assert b"S1 A" not in response.data


def test_public_date_is_injectable_and_uses_kigali_helper(client, app):
    scheduled_on_fixed_date(app)
    with patch(
        "app.blueprints.public.routes.kigali_today", return_value=FIXED_DATE
    ) as helper:
        response = client.get("/")
    helper.assert_called_once_with()
    assert FIXED_DATE.isoformat().encode() in response.data


def test_disabled_historical_dependencies_remain_safe(client, app):
    ids = scheduled_on_fixed_date(app)
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.room.is_active = False
        booking.school_class.is_active = False
        booking.teacher.is_active = False
        booking.teacher.username = "INACTIVE-ACCOUNT-USERNAME"
        booking.request.reason = "DISABLED-DEPENDENCY-PRIVATE-REASON"
        db.session.commit()
    response = client.get("/")
    assert response.status_code == 200
    assert b"Smart Class 1" in response.data
    assert b"S1 A" in response.data
    assert b"INACTIVE-ACCOUNT-USERNAME" not in response.data
    assert b"DISABLED-DEPENDENCY-PRIVATE-REASON" not in response.data


def test_public_schedule_orders_prep_then_room_deterministically(client, app):
    ids = scheduled_on_fixed_date(app)
    with app.app_context():
        existing = db.session.get(ScheduledBooking, ids["booking"])
        existing.prep = PrepPeriod.PREP_2
        existing.room_id = ids["room2"]
        for number, room_id in [(1, ids["room2"]), (2, ids["room1"])]:
            school_class = SchoolClass(name=f"Ordering Class {number}")
            teacher = make_user(f"ordering-teacher-{number}", UserRole.TEACHER)
            db.session.add_all([school_class, teacher])
            db.session.flush()
            request = BookingRequest(
                requester=teacher,
                school_class=school_class,
                subject="Ordering",
                reason="Private ordering reason",
            )
            db.session.add(request)
            db.session.flush()
            request.status = RequestStatus.SCHEDULED
            db.session.add(
                ScheduledBooking(
                    request=request,
                    schedule_date=FIXED_DATE,
                    prep=PrepPeriod.PREP_1,
                    room_id=room_id,
                    class_id=school_class.id,
                    teacher_id=teacher.id,
                    scheduled_by_id=ids["scheduler"],
                )
            )
        db.session.commit()
    response = client.get("/")
    assert response.data.index(b"Prep 1") < response.data.index(b"Prep 2")
    prep_one = response.data[
        response.data.index(b"Prep 1") : response.data.index(b"Prep 2")
    ]
    assert prep_one.index(b"Smart Class 1") < prep_one.index(b"Smart Class 2")
