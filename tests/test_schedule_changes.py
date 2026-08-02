"""Milestone 7 rejection and schedule-change tests."""

import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask_login import login_user, logout_user
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AuditLog,
    BookingRequest,
    Notification,
    PrepPeriod,
    RequestStatus,
    Room,
    ScheduledBooking,
    SchoolClass,
    SystemSettings,
    User,
    UserRole,
)
from app.scheduling import (
    SchedulingConflictError,
    SchedulingError,
    _is_slot_uniqueness_error,
    _lock_booking_with_current_dates,
    cancel_booking,
    planning_window,
    reject_request,
    reschedule_booking,
    schedule_request,
)

PASSWORD = "TemporaryPass123!"


def csrf_token(response):
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def csrf_login(client, name):
    with client.session_transaction() as session:
        session.clear()
    page = client.get("/auth/login")
    return client.post(
        "/auth/login",
        data={"username": name, "password": PASSWORD,
              "csrf_token": csrf_token(page)},
    )


@pytest.fixture(autouse=True)
def database(app):
    with app.app_context():
        db.create_all()
        db.session.add(SystemSettings(id=1))
        db.session.commit()
        yield
        db.session.remove()
        db.drop_all()


def make_user(name, role):
    item = User(
        username=name,
        full_name=name.title(),
        password_hash="pending",
        role=role,
        is_active=True,
        must_change_password=False,
    )
    item.set_password(PASSWORD)
    return item


def seed(app):
    with app.app_context():
        school_class = SchoolClass(name="S1 A")
        room1, room2 = Room(name="Smart Class 1"), Room(name="Smart Class 2")
        teacher = make_user("teacher", UserRole.TEACHER)
        scheduler = make_user("scheduler", UserRole.SCHEDULER)
        admin = make_user("admin", UserRole.ADMIN)
        monitor = make_user("monitor", UserRole.MONITOR)
        monitor.school_class = school_class
        request = BookingRequest(
            requester=teacher,
            school_class=school_class,
            subject="Math",
            reason="Private",
        )
        db.session.add_all(
            [school_class, room1, room2, teacher, scheduler, admin, monitor, request]
        )
        db.session.commit()
        return {
            "request": request.id,
            "room1": room1.id,
            "room2": room2.id,
            "scheduler": scheduler.id,
            "admin": admin.id,
            "monitor": monitor.id,
            "class": school_class.id,
            "teacher": teacher.id,
        }


def login(client, name):
    return client.post("/auth/login", data={"username": name, "password": PASSWORD})


def test_rejection_stores_reason_notifies_audits_and_reopens_queue(app):
    ids = seed(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["scheduler"]))
        settings = db.session.get(SystemSettings, 1)
        settings.booking_queue_locked = True
        db.session.commit()
        assert reject_request(ids["request"], "  No room\nTry tomorrow  ")
        record = db.session.get(BookingRequest, ids["request"])
        assert record.status == RequestStatus.REJECTED
        assert record.rejection_reason == "No room\nTry tomorrow"
        assert not db.session.get(SystemSettings, 1).booking_queue_locked
        assert db.session.scalar(db.select(Notification.id)) is not None
        assert (
            db.session.scalar(
                db.select(AuditLog.id).where(AuditLog.action == "REQUEST_REJECTED")
            )
            is not None
        )


def scheduled(app):
    ids = seed(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["scheduler"]))
        schedule_request(
            ids["request"], planning_window()[0], PrepPeriod.PREP_1, ids["room1"]
        )
        ids["booking"] = db.session.scalar(db.select(ScheduledBooking.id))
        logout_user()
    return ids


def test_scheduled_cancellation_and_rejection_confirmations_are_safe(client, app):
    ids = scheduled(app)
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.school_class.name = 'Class "<Quoted>" O\'Neil é'
        booking.request.reason = "PRIVATE-CONFIRMATION-REASON"
        db.session.commit()
    login(client, "admin")
    app.config["WTF_CSRF_ENABLED"] = True
    detail = client.get(f"/scheduler/bookings/{ids['booking']}")
    confirmation = detail.data.split(b"data-confirm-message=", 1)[1].split(
        b">", 1
    )[0]
    assert b"Cancel the scheduled booking" in confirmation
    assert b"&lt;Quoted&gt;" in confirmation
    assert b"PRIVATE-CONFIRMATION-REASON" not in confirmation
    assert b'method="post"' in detail.data
    assert b'name="csrf_token"' in detail.data
    day = client.get(f"/scheduler/schedule/{planning_window()[0].isoformat()}")
    assert b"Cancel the scheduled booking" in day.data
    assert b"PRIVATE-CONFIRMATION-REASON" not in day.data.split(
        b"data-confirm-message=", 1
    )[1].split(b">", 1)[0]
    assert client.get(f"/scheduler/bookings/{ids['booking']}/cancel").status_code == 405

    app.config["WTF_CSRF_ENABLED"] = False
    client.post("/auth/logout")
    with app.app_context():
        pending = BookingRequest(
            requester=db.session.get(User, ids["teacher"]),
            school_class=db.session.get(SchoolClass, ids["class"]),
            subject='Reject "<Quoted>"',
            reason="PRIVATE-REJECTION-CONFIRMATION",
        )
        db.session.add(pending)
        db.session.commit()
        pending_id = pending.id
    login(client, "scheduler")
    app.config["WTF_CSRF_ENABLED"] = True
    rejection = client.get(f"/scheduler/requests/{pending_id}/reject")
    reject_confirmation = rejection.data.split(b"data-confirm-message=", 1)[1].split(
        b">", 1
    )[0]
    assert b"Reject this pending request" in reject_confirmation
    assert b"Private" not in reject_confirmation
    assert b'method="post"' in rejection.data
    assert b'name="csrf_token"' in rejection.data


@pytest.mark.parametrize("actor_key", ["scheduler", "admin"])
def test_reschedule_preserves_booking_and_request(app, actor_key):
    ids = scheduled(app)
    target = planning_window()[1]
    with app.test_request_context():
        login_user(db.session.get(User, ids[actor_key]))
        booking = db.session.get(ScheduledBooking, ids["booking"])
        created_at = booking.created_at
        assert reschedule_booking(booking.id, target, PrepPeriod.PREP_2, ids["room2"])[
            0
        ]
        booking = db.session.get(ScheduledBooking, ids["booking"])
        assert booking.schedule_date == target and booking.prep == PrepPeriod.PREP_2
        assert booking.created_at == created_at
        assert booking.request.status == RequestStatus.SCHEDULED
        assert (
            db.session.scalar(
                db.select(AuditLog.id).where(AuditLog.action == "BOOKING_RESCHEDULED")
            )
            is not None
        )


@pytest.mark.parametrize("actor_key", ["scheduler", "admin"])
def test_cancel_is_soft_and_idempotent(app, actor_key):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids[actor_key]))
        assert cancel_booking(ids["booking"])[0]
        assert not cancel_booking(ids["booking"])[0]
        booking = db.session.get(ScheduledBooking, ids["booking"])
        assert not booking.is_active and booking.cancelled_at is not None
        assert booking.request.status == RequestStatus.CANCELLED
        audits = db.session.scalars(
            db.select(AuditLog).where(AuditLog.action == "BOOKING_CANCELLED")
        ).all()
        assert len(audits) == 1


@pytest.mark.parametrize("name", ["admin", "teacher", "monitor"])
def test_only_scheduler_can_reject(client, app, name):
    ids = seed(app)
    login(client, name)
    assert client.get(f"/scheduler/requests/{ids['request']}/reject").status_code == 403


@pytest.mark.parametrize("name", ["teacher", "monitor"])
@pytest.mark.parametrize("action", ["detail", "reschedule", "cancel"])
def test_requesters_cannot_manage_scheduled_bookings(client, app, name, action):
    ids = scheduled(app)
    login(client, name)
    suffix = "" if action == "detail" else f"/{action}"
    path = f"/scheduler/bookings/{ids['booking']}{suffix}"
    response = client.post(path) if action == "cancel" else client.get(path)
    assert response.status_code == 403


@pytest.mark.parametrize("action", ["detail", "reschedule"])
def test_booking_pages_require_login(client, app, action):
    ids = scheduled(app)
    suffix = "" if action == "detail" else "/reschedule"
    response = client.get(f"/scheduler/bookings/{ids['booking']}{suffix}")
    assert "/auth/login" in response.location


def test_rejection_and_scheduling_share_lock_order():
    source = open("app/scheduling.py", encoding="utf-8").read()
    reject = source[
        source.index("def reject_request") : source.index("def _acquire_date_locks")
    ]
    schedule = source[
        source.index("def schedule_request") : source.index(
            "def _covered_booking_exists"
        )
    ]
    for function in (reject, schedule):
        assert function.index("lock_actor") < function.index("lock_settings")
        assert function.index("lock_settings") < function.index("BookingRequest")


def test_stale_preliminary_date_rolls_back_and_restarts(app):
    first, second = planning_window()[:2]
    booking = SimpleNamespace(schedule_date=second)
    actor = SimpleNamespace(id=1)
    with (
        app.app_context(),
        patch.object(
            db.session, "scalar", side_effect=[first, booking, second, booking]
        ),
        patch("app.scheduling._acquire_date_locks") as locks,
        patch("app.scheduling.lock_actor", return_value=actor),
        patch.object(db.session, "rollback", wraps=db.session.rollback) as rollback,
    ):
        result = _lock_booking_with_current_dates(1, second)
        assert result == (actor, booking, second)
        assert locks.call_args_list[0].args == (first, second)
        assert locks.call_args_list[1].args == (second, second)
        rollback.assert_called_once_with()


def test_rejection_requires_and_accepts_genuine_csrf(client, app):
    ids = seed(app)
    app.config["WTF_CSRF_ENABLED"] = True
    assert csrf_login(client, "scheduler").status_code == 302
    url = f"/scheduler/requests/{ids['request']}/reject"
    assert client.post(url, data={"reason": "Unavailable"}).status_code == 400
    page = client.get(url)
    response = client.post(
        url,
        data={"reason": "Unavailable", "csrf_token": csrf_token(page)},
    )
    assert response.status_code == 302


def test_rescheduling_requires_and_accepts_genuine_csrf(client, app):
    ids = scheduled(app)
    app.config["WTF_CSRF_ENABLED"] = True
    assert csrf_login(client, "admin").status_code == 302
    url = f"/scheduler/bookings/{ids['booking']}/reschedule"
    payload = {"schedule_date": planning_window()[1].isoformat(),
               "prep": "PREP_2", "room_id": ids["room2"]}
    assert client.post(url, data=payload).status_code == 400
    payload["csrf_token"] = csrf_token(client.get(url))
    assert client.post(url, data=payload).status_code == 302


def test_cancellation_requires_and_accepts_genuine_csrf(client, app):
    ids = scheduled(app)
    app.config["WTF_CSRF_ENABLED"] = True
    assert csrf_login(client, "admin").status_code == 302
    url = f"/scheduler/bookings/{ids['booking']}/cancel"
    assert client.post(url).status_code == 400
    detail = client.get(f"/scheduler/bookings/{ids['booking']}")
    assert client.post(url, data={"csrf_token": csrf_token(detail)}).status_code == 302


@pytest.mark.parametrize("dependency", ["room", "class", "teacher"])
def test_same_slot_noop_still_validates_active_dependencies(app, dependency):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        model, record_id = {
            "room": (Room, ids["room1"]),
            "class": (SchoolClass, ids["class"]),
            "teacher": (User, ids["teacher"]),
        }[dependency]
        db.session.get(model, record_id).is_active = False
        db.session.commit()
        with pytest.raises(SchedulingError, match="inactive"):
            reschedule_booking(
                ids["booking"], planning_window()[0],
                PrepPeriod.PREP_1, ids["room1"]
            )
        assert not db.session().in_transaction()
        assert db.session.scalar(db.select(AuditLog.id).where(
            AuditLog.action == "BOOKING_RESCHEDULED")) is None


@pytest.mark.parametrize("field", ["class_id", "teacher_id"])
def test_booking_request_identity_mismatch_fails_safely(app, field):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        if field == "class_id":
            replacement = SchoolClass(name="S2 A")
        else:
            replacement = make_user("other-teacher", UserRole.TEACHER)
        db.session.add(replacement)
        db.session.commit()
        db.session.execute(
            update(BookingRequest)
            .where(BookingRequest.id == ids["request"])
            .values(**{field: replacement.id})
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
        with pytest.raises(SchedulingError, match="records are inconsistent"):
            cancel_booking(ids["booking"])
        assert not db.session().in_transaction()
        booking = db.session.get(ScheduledBooking, ids["booking"])
        assert booking.is_active and booking.cancelled_at is None


def test_malformed_direct_inputs_are_safe(app):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        with pytest.raises(SchedulingError):
            reschedule_booking(ids["booking"], [], "INVALID", object())
        assert not db.session().in_transaction()
        with pytest.raises(SchedulingError):
            cancel_booking(object())
        assert not db.session().in_transaction()


def test_non_string_rejection_reason_fails_safely(app):
    ids = seed(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["scheduler"]))
        with pytest.raises(SchedulingError, match="valid rejection reason"):
            reject_request(ids["request"], object())
        assert not db.session().in_transaction()
        request = db.session.get(BookingRequest, ids["request"])
        assert request.status == RequestStatus.PENDING
        assert request.rejection_reason is None


STATUS_MATRIX = (
    (True, RequestStatus.SCHEDULED, "active"),
    (False, RequestStatus.CANCELLED, "cancelled"),
    (False, RequestStatus.PENDING, "inconsistent"),
    (False, RequestStatus.REJECTED, "inconsistent"),
    (False, RequestStatus.SCHEDULED, "inconsistent"),
    (True, RequestStatus.CANCELLED, "inconsistent"),
    (True, RequestStatus.PENDING, "inconsistent"),
    (True, RequestStatus.REJECTED, "inconsistent"),
)


@pytest.mark.parametrize(("active", "status", "expected"), STATUS_MATRIX)
def test_reschedule_booking_request_state_matrix(app, active, status, expected):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.is_active = active
        booking.request.status = status
        db.session.commit()
        if expected == "active":
            assert not reschedule_booking(
                booking.id, booking.schedule_date, booking.prep, booking.room_id
            )[0]
        elif expected == "cancelled":
            with pytest.raises(SchedulingError, match="no longer active"):
                reschedule_booking(
                    booking.id, booking.schedule_date, booking.prep, booking.room_id
                )
        else:
            with pytest.raises(SchedulingError, match="records are inconsistent"):
                reschedule_booking(
                    booking.id, booking.schedule_date, booking.prep, booking.room_id
                )
        assert not db.session().in_transaction()


@pytest.mark.parametrize(("active", "status", "expected"), STATUS_MATRIX)
def test_cancel_booking_request_state_matrix(app, active, status, expected):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.is_active = active
        booking.request.status = status
        db.session.commit()
        if expected == "active":
            assert cancel_booking(booking.id)[0]
        elif expected == "cancelled":
            assert not cancel_booking(booking.id)[0]
        else:
            with pytest.raises(SchedulingError, match="records are inconsistent"):
                cancel_booking(booking.id)
        assert not db.session().in_transaction()


@pytest.mark.parametrize("bad_id", [None, True, 0, -1, "1", 1.0, object()])
def test_rejection_rejects_malformed_identifiers(app, bad_id):
    ids = seed(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["scheduler"]))
        with pytest.raises(SchedulingError, match="valid request"):
            reject_request(bad_id, "Reason")
        assert not db.session().in_transaction()


@pytest.mark.parametrize("reason", ["", "   ", "x" * 2001])
def test_rejection_enforces_reason_constraints(app, reason):
    ids = seed(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["scheduler"]))
        with pytest.raises(SchedulingError):
            reject_request(ids["request"], reason)
        assert not db.session().in_transaction()
        persisted = db.session.get(BookingRequest, ids["request"])
        assert persisted.status == RequestStatus.PENDING


@pytest.mark.parametrize("bad_id", [None, True, 0, -1, "1", 1.0, object()])
def test_cancellation_rejects_malformed_identifiers(app, bad_id):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        with pytest.raises(SchedulingError, match="valid booking"):
            cancel_booking(bad_id)
        assert not db.session().in_transaction()


@pytest.mark.parametrize(
    "status", [RequestStatus.REJECTED, RequestStatus.SCHEDULED, RequestStatus.CANCELLED]
)
@pytest.mark.parametrize("replacement", ["", "   ", object(), "x" * 2001])
def test_processed_rejection_is_noop_before_reason_validation(
    app, status, replacement
):
    ids = seed(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["scheduler"]))
        record = db.session.get(BookingRequest, ids["request"])
        record.status = status
        record.rejection_reason = "Original decision"
        db.session.commit()
        with patch("app.scheduling.pending_count") as count:
            assert not reject_request(record.id, replacement)
        assert not db.session().in_transaction()
        count.assert_not_called()
        record = db.session.get(BookingRequest, record.id)
        assert record.status == status
        assert record.rejection_reason == "Original decision"
        assert db.session.scalar(db.select(Notification.id)) is None
        assert db.session.scalar(db.select(AuditLog.id)) is None


@pytest.mark.parametrize(
    "status",
    [RequestStatus.REJECTED, RequestStatus.SCHEDULED, RequestStatus.CANCELLED],
)
def test_processed_request_does_not_render_rejection_form(client, app, status):
    ids = seed(app)
    with app.app_context():
        record = db.session.get(BookingRequest, ids["request"])
        record.status = status
        db.session.commit()
    login(client, "scheduler")
    response = client.get(f"/scheduler/requests/{ids['request']}/reject")
    assert response.status_code == 302
    assert response.location.endswith("/scheduler/pending")


@pytest.mark.parametrize(
    ("active", "status"),
    [
        (False, RequestStatus.CANCELLED),
        (True, RequestStatus.PENDING),
        (False, RequestStatus.SCHEDULED),
    ],
)
def test_invalid_booking_state_does_not_render_reschedule_form(
    client, app, active, status
):
    ids = scheduled(app)
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.is_active = active
        booking.request.status = status
        db.session.commit()
    login(client, "admin")
    response = client.get(f"/scheduler/bookings/{ids['booking']}/reschedule")
    assert response.status_code == 302
    assert response.location.endswith(f"/scheduler/bookings/{ids['booking']}")


@pytest.mark.parametrize(
    ("detail", "recognized"),
    [
        ("duplicate key violates constraint uq_active_room_slot", True),
        (
            "UNIQUE constraint failed: scheduled_bookings.schedule_date, "
            "scheduled_bookings.prep, scheduled_bookings.class_id",
            True,
        ),
        ("FOREIGN KEY constraint failed", False),
        ("duplicate key violates constraint notifications_pkey", False),
    ],
)
def test_slot_integrity_error_classification(detail, recognized):
    exc = IntegrityError("redacted", {}, sqlite3.IntegrityError(detail))
    assert _is_slot_uniqueness_error(exc) is recognized


@pytest.mark.parametrize("failure_point", ["flush", "commit"])
@pytest.mark.parametrize("slot_conflict", [True, False])
def test_reschedule_integrity_failures_are_classified_and_rolled_back(
    app, failure_point, slot_conflict
):
    ids = scheduled(app)
    target = planning_window()[1]
    detail = (
        "duplicate key violates constraint uq_active_teacher_slot"
        if slot_conflict
        else "FOREIGN KEY constraint failed"
    )
    error = IntegrityError("redacted", {}, sqlite3.IntegrityError(detail))
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        booking = db.session.get(ScheduledBooking, ids["booking"])
        old_slot = (booking.schedule_date, booking.prep, booking.room_id)
        db.session.rollback()
        exception = SchedulingConflictError if slot_conflict else SchedulingError
        message = "taken concurrently" if slot_conflict else "Unable to reschedule"
        with patch.object(db.session, failure_point, side_effect=error):
            with pytest.raises(exception, match=message):
                reschedule_booking(
                    ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
                )
        assert not db.session().in_transaction()
        booking = db.session.get(ScheduledBooking, ids["booking"])
        assert (booking.schedule_date, booking.prep, booking.room_id) == old_slot
        assert booking.request.status == RequestStatus.SCHEDULED
        assert db.session.scalar(
            db.select(Notification.id).where(
                Notification.type == "RESCHEDULED"
            )
        ) is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "BOOKING_RESCHEDULED")
        ) is None
