"""Milestone 7 rescheduling conflict and transactional rollback verification."""

import sqlite3
from datetime import timedelta
from unittest.mock import patch

import pytest
from flask_login import login_user
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from test_schedule_changes import database, make_user, scheduled  # noqa: F401

from app.extensions import db
from app.models import (
    AuditLog,
    BlockScope,
    BookingRequest,
    Notification,
    NotificationType,
    PrepPeriod,
    RequestStatus,
    Room,
    RoomBlock,
    ScheduledBooking,
    SchoolClass,
    User,
    UserRole,
)
from app.scheduling import (
    SchedulingConflictError,
    SchedulingError,
    planning_window,
    reschedule_booking,
)


def snapshot_booking(booking_id):
    booking = db.session.get(ScheduledBooking, booking_id)
    return {
        "id": booking.id,
        "request_id": booking.request_id,
        "created_at": booking.created_at,
        "date": booking.schedule_date,
        "prep": booking.prep,
        "room_id": booking.room_id,
        "is_active": booking.is_active,
    }


def assert_failed_reschedule(
    ids,
    original,
    target,
    target_room,
    expected_status=RequestStatus.SCHEDULED,
):
    assert not db.session().in_transaction()
    booking = db.session.get(ScheduledBooking, ids["booking"])
    assert snapshot_booking(booking.id) == original
    assert booking.request.status == expected_status
    if original["is_active"]:
        assert db.session.scalar(
            db.select(ScheduledBooking.id).where(
                ScheduledBooking.id == booking.id,
                ScheduledBooking.schedule_date == original["date"],
                ScheduledBooking.prep == original["prep"],
                ScheduledBooking.room_id == original["room_id"],
                ScheduledBooking.is_active.is_(True),
            )
        ) == booking.id
    assert db.session.scalar(
        db.select(ScheduledBooking.id).where(
            ScheduledBooking.id == booking.id,
            ScheduledBooking.schedule_date == target,
            ScheduledBooking.prep == PrepPeriod.PREP_2,
            ScheduledBooking.room_id == target_room,
        )
    ) is None
    assert db.session.scalar(
        db.select(Notification.id).where(
            Notification.type == NotificationType.RESCHEDULED
        )
    ) is None
    assert db.session.scalar(
        db.select(AuditLog.id).where(AuditLog.action == "BOOKING_RESCHEDULED")
    ) is None


def add_competing_booking(ids, target, conflict_kind):
    original = db.session.get(ScheduledBooking, ids["booking"])
    other_class = SchoolClass(name=f"Other Class {conflict_kind}")
    other_teacher = make_user(f"other-{conflict_kind}", UserRole.TEACHER)
    db.session.add_all([other_class, other_teacher])
    db.session.flush()
    school_class = original.school_class if conflict_kind == "class" else other_class
    teacher = original.teacher if conflict_kind == "teacher" else other_teacher
    room_id = ids["room2"] if conflict_kind == "room" else ids["room1"]
    request = BookingRequest(
        requester=teacher,
        school_class=school_class,
        subject="Competing request",
        reason="Private competing reason",
    )
    db.session.add(request)
    db.session.flush()
    request.status = RequestStatus.SCHEDULED
    db.session.add(
        ScheduledBooking(
            request=request,
            schedule_date=target,
            prep=PrepPeriod.PREP_2,
            room_id=room_id,
            class_id=school_class.id,
            teacher_id=teacher.id,
            scheduled_by_id=ids["scheduler"],
        )
    )
    db.session.commit()


@pytest.mark.parametrize("conflict_kind", ["room", "class", "teacher"])
def test_each_rescheduling_identity_conflict_rolls_back(app, conflict_kind):
    ids = scheduled(app)
    target = planning_window()[1]
    with app.test_request_context():
        add_competing_booking(ids, target, conflict_kind)
        login_user(db.session.get(User, ids["admin"]))
        original = snapshot_booking(ids["booking"])
        db.session.rollback()
        with pytest.raises(SchedulingConflictError, match="unavailable"):
            reschedule_booking(
                ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
            )
        competing = db.session.scalar(
            db.select(ScheduledBooking.id).where(
                ScheduledBooking.id != ids["booking"],
                ScheduledBooking.schedule_date == target,
            )
        )
        db.session.rollback()
        assert competing is not None
        assert_failed_reschedule(ids, original, target, ids["room2"])


@pytest.mark.parametrize("scope", list(BlockScope))
def test_each_block_scope_rejects_rescheduling(app, scope):
    ids = scheduled(app)
    target = planning_window()[1]
    with app.test_request_context():
        kwargs = {
            "block_date": target,
            "scope": scope,
            "created_by_id": ids["scheduler"],
        }
        if scope != BlockScope.DAY:
            kwargs["room_id"] = ids["room2"]
        if scope == BlockScope.SLOT:
            kwargs["prep"] = PrepPeriod.PREP_2
        db.session.add(RoomBlock(**kwargs))
        db.session.commit()
        login_user(db.session.get(User, ids["admin"]))
        original = snapshot_booking(ids["booking"])
        db.session.rollback()
        with pytest.raises(SchedulingConflictError, match="unavailable"):
            reschedule_booking(
                ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
            )
        assert_failed_reschedule(ids, original, target, ids["room2"])


@pytest.mark.parametrize("dependency", ["room", "class", "teacher"])
def test_inactive_dependency_rejects_rescheduling(app, dependency):
    ids = scheduled(app)
    target = planning_window()[1]
    with app.test_request_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        record = {
            "room": db.session.get(Room, ids["room2"]),
            "class": booking.school_class,
            "teacher": booking.teacher,
        }[dependency]
        record.is_active = False
        db.session.commit()
        login_user(db.session.get(User, ids["admin"]))
        original = snapshot_booking(ids["booking"])
        db.session.rollback()
        with pytest.raises(SchedulingConflictError, match="inactive"):
            reschedule_booking(
                ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
            )
        assert_failed_reschedule(ids, original, target, ids["room2"])


@pytest.mark.parametrize("state", ["inactive_booking", "pending_request"])
def test_invalid_booking_state_rejects_rescheduling(app, state):
    ids = scheduled(app)
    target = planning_window()[1]
    with app.test_request_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        if state == "inactive_booking":
            booking.is_active = False
        else:
            booking.request.status = RequestStatus.PENDING
        db.session.commit()
        login_user(db.session.get(User, ids["admin"]))
        original = snapshot_booking(ids["booking"])
        db.session.rollback()
        with pytest.raises(SchedulingError, match="inconsistent"):
            reschedule_booking(
                ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
            )
        expected_status = (
            RequestStatus.PENDING
            if state == "pending_request"
            else RequestStatus.SCHEDULED
        )
        assert_failed_reschedule(
            ids, original, target, ids["room2"], expected_status
        )


def test_outside_planning_window_rejects_rescheduling(app):
    ids = scheduled(app)
    target = planning_window()[-1] + timedelta(days=1)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        original = snapshot_booking(ids["booking"])
        db.session.rollback()
        with pytest.raises(SchedulingError, match="three-day window"):
            reschedule_booking(
                ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
            )
        assert_failed_reschedule(ids, original, target, ids["room2"])


def test_missing_booking_rejects_rescheduling(app):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        with pytest.raises(SchedulingError, match="Booking not found"):
            reschedule_booking(
                999999, planning_window()[1], PrepPeriod.PREP_2, ids["room2"]
            )
        assert not db.session().in_transaction()
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "BOOKING_RESCHEDULED")
        ) is None


def test_missing_request_rejects_rescheduling(app):
    ids = scheduled(app)
    target = planning_window()[1]
    with app.test_request_context():
        original = snapshot_booking(ids["booking"])
        db.session.remove()
        with db.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.execute(
                delete(BookingRequest).where(BookingRequest.id == ids["request"])
            )
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        login_user(db.session.get(User, ids["admin"]))
        with pytest.raises(SchedulingError, match="request is unavailable"):
            reschedule_booking(
                ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
            )
        assert not db.session().in_transaction()
        booking = db.session.get(ScheduledBooking, ids["booking"])
        assert snapshot_booking(booking.id) == original
        assert db.session.scalar(
            db.select(Notification.id).where(
                Notification.type == NotificationType.RESCHEDULED
            )
        ) is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "BOOKING_RESCHEDULED")
        ) is None


@pytest.mark.parametrize("field", ["class_id", "teacher_id"])
def test_booking_request_identity_mismatch_rejects_rescheduling(app, field):
    ids = scheduled(app)
    target = planning_window()[1]
    with app.test_request_context():
        if field == "class_id":
            replacement = SchoolClass(name="Request mismatch class")
            db.session.add(replacement)
            db.session.commit()
            replacement_id = replacement.id
        else:
            replacement_id = ids["admin"]
        db.session.execute(
            update(BookingRequest)
            .where(BookingRequest.id == ids["request"])
            .values(**{field: replacement_id})
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
        login_user(db.session.get(User, ids["admin"]))
        original = snapshot_booking(ids["booking"])
        db.session.rollback()
        with pytest.raises(SchedulingError, match="inconsistent"):
            reschedule_booking(
                ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
            )
        assert_failed_reschedule(ids, original, target, ids["room2"])


@pytest.mark.parametrize(
    "failure_kind",
    [
        "flush",
        "uq_active_room_slot",
        "uq_active_class_slot",
        "uq_active_teacher_slot",
        "unrelated_integrity",
        "notification",
        "audit",
        "commit",
    ],
)
def test_rescheduling_persistence_failures_roll_back(app, failure_kind):
    ids = scheduled(app)
    target = planning_window()[1]
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        original = snapshot_booking(ids["booking"])
        db.session.rollback()
        ordinary_error = SQLAlchemyError("forced failure")
        constraint = (
            failure_kind
            if failure_kind.startswith("uq_active_")
            else "other_constraint"
        )
        integrity_error = IntegrityError(
            "redacted", {}, sqlite3.IntegrityError(f"constraint {constraint}")
        )
        original_add = db.session.add

        def failing_add(item):
            if failure_kind == "notification" and isinstance(item, Notification):
                raise ordinary_error
            if failure_kind == "audit" and isinstance(item, AuditLog):
                raise ordinary_error
            return original_add(item)

        if failure_kind == "flush":
            context = patch.object(db.session, "flush", side_effect=ordinary_error)
        elif (
            failure_kind.startswith("uq_active_")
            or failure_kind == "unrelated_integrity"
        ):
            context = patch.object(db.session, "flush", side_effect=integrity_error)
        elif failure_kind in {"notification", "audit"}:
            context = patch.object(db.session, "add", side_effect=failing_add)
        else:
            context = patch.object(db.session, "commit", side_effect=ordinary_error)
        expected = (
            SchedulingConflictError
            if failure_kind.startswith("uq_active_")
            else SchedulingError
        )
        message = (
            "taken concurrently"
            if failure_kind.startswith("uq_active_")
            else "Unable to reschedule"
        )
        with context, pytest.raises(expected, match=message):
            reschedule_booking(
                ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
            )
        assert_failed_reschedule(ids, original, target, ids["room2"])


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("is_active", False),
        ("role", UserRole.TEACHER),
        ("must_change_password", True),
    ],
)
def test_rescheduling_rejects_stale_actor(app, column, value):
    ids = scheduled(app)
    target = planning_window()[1]
    with app.test_request_context():
        actor = db.session.get(User, ids["admin"])
        login_user(actor)
        original = snapshot_booking(ids["booking"])
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
            reschedule_booking(
                ids["booking"], target, PrepPeriod.PREP_2, ids["room2"]
            )
        assert_failed_reschedule(ids, original, target, ids["room2"])
