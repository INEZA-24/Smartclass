"""Milestone 7 rejection persistence, rollback, and actor verification."""

from unittest.mock import patch

import pytest
from flask_login import login_user
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from test_schedule_changes import database, login, seed  # noqa: F401

from app.extensions import db
from app.models import (
    AuditLog,
    BookingRequest,
    Notification,
    RequestStatus,
    SystemSettings,
    User,
    UserRole,
)
from app.scheduling import SchedulingError, reject_request


def add_pending_requests(ids, total):
    teacher = db.session.get(User, ids["teacher"])
    school_class = db.session.get(BookingRequest, ids["request"]).school_class
    for number in range(1, total):
        db.session.add(
            BookingRequest(
                requester=teacher,
                school_class=school_class,
                subject=f"Subject {number}",
                reason=f"Private reason {number}",
            )
        )
    db.session.commit()


@pytest.mark.parametrize(
    ("start_count", "initial_locked", "expected_locked", "reopened"),
    [
        (12, True, True, False),
        (11, True, True, False),
        (10, True, False, True),
        (9, False, False, False),
    ],
)
def test_rejection_queue_hysteresis_is_atomic(
    app, start_count, initial_locked, expected_locked, reopened
):
    ids = seed(app)
    with app.test_request_context():
        add_pending_requests(ids, start_count)
        settings = db.session.get(SystemSettings, 1)
        settings.booking_queue_locked = initial_locked
        db.session.commit()
        login_user(db.session.get(User, ids["scheduler"]))

        assert reject_request(ids["request"], "No availability")

        record = db.session.get(BookingRequest, ids["request"])
        assert record.status == RequestStatus.REJECTED
        assert record.rejection_reason == "No availability"
        assert db.session.get(SystemSettings, 1).booking_queue_locked is expected_locked
        assert db.session.scalar(
            db.select(db.func.count()).select_from(BookingRequest).where(
                BookingRequest.status == RequestStatus.PENDING
            )
        ) == start_count - 1
        assert db.session.scalar(
            db.select(db.func.count()).select_from(Notification)
        ) == 1
        assert db.session.scalar(
            db.select(db.func.count()).select_from(AuditLog).where(
                AuditLog.action == "REQUEST_REJECTED"
            )
        ) == 1
        assert db.session.scalar(
            db.select(db.func.count()).select_from(AuditLog).where(
                AuditLog.action == "QUEUE_REOPENED"
            )
        ) == int(reopened)


def assert_rejection_rolled_back(request_id, expected_locked):
    assert not db.session().in_transaction()
    record = db.session.get(BookingRequest, request_id)
    assert record.status == RequestStatus.PENDING
    assert record.rejection_reason is None
    assert db.session.get(SystemSettings, 1).booking_queue_locked is expected_locked
    assert db.session.scalar(db.select(Notification.id)) is None
    assert db.session.scalar(
        db.select(AuditLog.id).where(
            AuditLog.action.in_(["REQUEST_REJECTED", "QUEUE_REOPENED"])
        )
    ) is None


@pytest.mark.parametrize(
    "failure_kind",
    [
        "flush",
        "pending_count",
        "notification",
        "request_audit",
        "queue_audit",
        "commit",
    ],
)
def test_rejection_failure_rolls_back_all_side_effects(app, failure_kind):
    ids = seed(app)
    with app.test_request_context():
        add_pending_requests(ids, 10)
        settings = db.session.get(SystemSettings, 1)
        settings.booking_queue_locked = True
        db.session.commit()
        login_user(db.session.get(User, ids["scheduler"]))
        error = SQLAlchemyError("forced persistence failure")
        original_add = db.session.add

        def failing_add(item):
            if failure_kind == "notification" and isinstance(item, Notification):
                raise error
            if isinstance(item, AuditLog):
                if (
                    failure_kind == "request_audit"
                    and item.action == "REQUEST_REJECTED"
                ):
                    raise error
                if failure_kind == "queue_audit" and item.action == "QUEUE_REOPENED":
                    raise error
            return original_add(item)

        patches = []
        if failure_kind == "flush":
            patches.append(patch.object(db.session, "flush", side_effect=error))
        elif failure_kind == "pending_count":
            patches.append(patch("app.scheduling.pending_count", side_effect=error))
        elif failure_kind in {"notification", "request_audit", "queue_audit"}:
            patches.append(patch.object(db.session, "add", side_effect=failing_add))
        else:
            patches.append(patch.object(db.session, "commit", side_effect=error))

        with patches[0], pytest.raises(SchedulingError, match="Unable to reject"):
            reject_request(ids["request"], "No availability")
        assert_rejection_rolled_back(ids["request"], True)


def test_rejection_missing_settings_rolls_back_safely(app):
    ids = seed(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["scheduler"]))
        db.session.delete(db.session.get(SystemSettings, 1))
        db.session.commit()
        with pytest.raises(SchedulingError, match="System settings are unavailable"):
            reject_request(ids["request"], "No availability")
        assert not db.session().in_transaction()
        record = db.session.get(BookingRequest, ids["request"])
        assert record.status == RequestStatus.PENDING
        assert record.rejection_reason is None
        assert db.session.scalar(db.select(Notification.id)) is None
        assert db.session.scalar(db.select(AuditLog.id)) is None


@pytest.mark.parametrize(
    ("column", "value"),
    [("is_active", False), ("role", UserRole.TEACHER)],
)
def test_rejection_refreshes_and_rejects_stale_scheduler(app, column, value):
    ids = seed(app)
    with app.test_request_context():
        actor = db.session.get(User, ids["scheduler"])
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
            reject_request(ids["request"], "No availability")
        assert not db.session().in_transaction()
        record = db.session.get(BookingRequest, ids["request"])
        assert record.status == RequestStatus.PENDING
        assert db.session.scalar(db.select(Notification.id)) is None
        assert db.session.scalar(db.select(AuditLog.id)) is None


def test_anonymous_rejection_redirects_to_login(client, app):
    ids = seed(app)
    response = client.get(f"/scheduler/requests/{ids['request']}/reject")
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_forced_password_scheduler_cannot_reject(client, app):
    ids = seed(app)
    with app.app_context():
        actor = db.session.get(User, ids["scheduler"])
        actor.must_change_password = True
        db.session.commit()
    login(client, "scheduler")
    response = client.get(f"/scheduler/requests/{ids['request']}/reject")
    assert response.status_code == 302
    assert "/auth/change-password" in response.location
