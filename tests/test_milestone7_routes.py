"""Focused Milestone 7 route-state and database-diagnostic regressions."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from test_schedule_changes import database, login, scheduled  # noqa: F401

from app.extensions import db
from app.models import BookingRequest, RequestStatus, ScheduledBooking, SchoolClass
from app.scheduling import SchedulingError, _is_slot_uniqueness_error, planning_window


@pytest.mark.parametrize("historical", [False, True])
def test_successful_cancellation_redirects_to_booking_detail(client, app, historical):
    ids = scheduled(app)
    if historical:
        with app.app_context():
            booking = db.session.get(ScheduledBooking, ids["booking"])
            booking.schedule_date = planning_window()[0] - timedelta(days=20)
            db.session.commit()
    login(client, "admin")
    response = client.post(f"/scheduler/bookings/{ids['booking']}/cancel")
    assert response.status_code == 302
    assert response.location.endswith(f"/scheduler/bookings/{ids['booking']}")


def test_repeated_historical_cancellation_redirects_to_booking_detail(client, app):
    ids = scheduled(app)
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.schedule_date = planning_window()[0] - timedelta(days=20)
        db.session.commit()
    login(client, "scheduler")
    url = f"/scheduler/bookings/{ids['booking']}/cancel"
    assert client.post(url).status_code == 302
    response = client.post(url)
    assert response.status_code == 302
    assert response.location.endswith(f"/scheduler/bookings/{ids['booking']}")


def test_failed_cancellation_redirects_to_existing_booking(client, app):
    ids = scheduled(app)
    login(client, "admin")
    with patch(
        "app.blueprints.scheduler.routes.cancel_booking",
        side_effect=SchedulingError("Unable to cancel the booking. Please try again."),
    ):
        response = client.post(f"/scheduler/bookings/{ids['booking']}/cancel")
    assert response.status_code == 302
    assert response.location.endswith(f"/scheduler/bookings/{ids['booking']}")


def test_missing_cancellation_redirects_to_schedule_index(client, app):
    scheduled(app)
    login(client, "admin")
    response = client.post("/scheduler/bookings/999999/cancel")
    assert response.status_code == 302
    assert response.location.endswith("/scheduler/schedule")


@pytest.mark.parametrize(
    "status", [RequestStatus.PENDING, RequestStatus.REJECTED, RequestStatus.CANCELLED]
)
def test_inconsistent_request_status_hides_booking_actions(client, app, status):
    ids = scheduled(app)
    with app.app_context():
        record = db.session.get(BookingRequest, ids["request"])
        record.status = status
        db.session.commit()
    login(client, "admin")
    response = client.get(f"/scheduler/bookings/{ids['booking']}")
    assert response.status_code == 200
    assert b">Reschedule<" not in response.data
    assert b">Cancel<" not in response.data


@pytest.mark.parametrize("field", ["class_id", "teacher_id"])
def test_identity_mismatch_hides_booking_actions(client, app, field):
    ids = scheduled(app)
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        if field == "class_id":
            replacement = SchoolClass(name="Mismatch Class")
            db.session.add(replacement)
            db.session.flush()
            booking.class_id = replacement.id
        else:
            booking.teacher_id = ids["admin"]
        db.session.commit()
    login(client, "scheduler")
    response = client.get(f"/scheduler/bookings/{ids['booking']}")
    assert response.status_code == 200
    assert b">Reschedule<" not in response.data
    assert b">Cancel<" not in response.data


@pytest.mark.parametrize("actor", ["scheduler", "admin"])
def test_valid_booking_detail_shows_actions(client, app, actor):
    ids = scheduled(app)
    login(client, actor)
    response = client.get(f"/scheduler/bookings/{ids['booking']}")
    assert b">Reschedule<" in response.data
    assert b">Cancel<" in response.data


def test_cancellation_get_is_not_allowed(client, app):
    ids = scheduled(app)
    login(client, "admin")
    response = client.get(f"/scheduler/bookings/{ids['booking']}/cancel")
    assert response.status_code == 405


@pytest.mark.parametrize(
    "constraint_name",
    [
        "uq_active_room_slot",
        "uq_active_class_slot",
        "uq_active_teacher_slot",
        '"uq_active_room_slot"',
        '"uq_active_class_slot"',
        '"uq_active_teacher_slot"',
    ],
)
def test_postgresql_diagnostic_slot_constraints_are_recognized(constraint_name):
    original = SimpleNamespace(
        diag=SimpleNamespace(constraint_name=constraint_name),
    )
    assert _is_slot_uniqueness_error(IntegrityError("redacted", {}, original))


@pytest.mark.parametrize(
    "constraint_name",
    ["notifications_pkey", "audit_logs_pkey", "unrelated_unique_constraint"],
)
def test_unrelated_postgresql_diagnostic_constraints_are_not_slot_conflicts(
    constraint_name,
):
    original = SimpleNamespace(
        diag=SimpleNamespace(constraint_name=constraint_name),
    )
    assert not _is_slot_uniqueness_error(IntegrityError("redacted", {}, original))


@pytest.mark.parametrize(
    "identity", ["room_id", "class_id", "teacher_id"]
)
def test_all_sqlite_slot_signatures_are_recognized(identity):
    detail = (
        "UNIQUE constraint failed: scheduled_bookings.schedule_date, "
        f"scheduled_bookings.prep, scheduled_bookings.{identity}"
    )
    assert _is_slot_uniqueness_error(IntegrityError("redacted", {}, Exception(detail)))


@pytest.mark.parametrize(
    "constraint_name",
    ["uq_active_room_slot", "uq_active_class_slot", "uq_active_teacher_slot"],
)
def test_quoted_postgresql_constraint_text_fallback_is_recognized(constraint_name):
    detail = f'duplicate key value violates unique constraint "{constraint_name}"'
    assert _is_slot_uniqueness_error(IntegrityError("redacted", {}, Exception(detail)))
