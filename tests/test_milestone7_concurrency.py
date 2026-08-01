"""Milestone 7 retry and shared advisory-lock verification."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from flask_login import login_user
from test_schedule_changes import database, scheduled  # noqa: F401

from app.extensions import db
from app.models import PrepPeriod, User
from app.scheduling import (
    SchedulingConflictError,
    _acquire_date_locks,
    _lock_booking_with_current_dates,
    acquire_schedule_date_lock,
    advisory_lock_key,
    cancel_booking,
    planning_window,
    reschedule_booking,
)


def test_date_lock_helper_sorts_and_deduplicates_dates():
    later = date(2026, 8, 3)
    earlier = date(2026, 8, 1)
    with patch("app.scheduling.acquire_schedule_date_lock") as lock:
        _acquire_date_locks(later, earlier, later, earlier)
    assert lock.call_args_list == [call(earlier), call(later)]


def test_stale_date_rolls_back_before_retrying_new_date(app):
    first, actual = planning_window()[:2]
    events = []
    stale_booking = SimpleNamespace(schedule_date=actual)
    current_booking = SimpleNamespace(schedule_date=actual)
    actor = SimpleNamespace(id=1)
    original_rollback = db.session.rollback

    def record_locks(*dates):
        events.append(("lock", dates))

    def record_rollback():
        events.append(("rollback",))
        original_rollback()

    with (
        app.app_context(),
        patch.object(
            db.session,
            "scalar",
            side_effect=[first, stale_booking, actual, current_booking],
        ),
        patch("app.scheduling._acquire_date_locks", side_effect=record_locks),
        patch("app.scheduling.lock_actor", return_value=actor),
        patch.object(db.session, "rollback", side_effect=record_rollback),
    ):
        result = _lock_booking_with_current_dates(1, actual)
    assert result == (actor, current_booking, actual)
    assert events == [
        ("lock", (first, actual)),
        ("rollback",),
        ("lock", (actual, actual)),
    ]


def test_retry_exhaustion_is_safe_and_ends_transaction(app):
    first, actual = planning_window()[:2]
    actor = SimpleNamespace(id=1)
    values = []
    for _attempt in range(3):
        values.extend([first, SimpleNamespace(schedule_date=actual)])
    with (
        app.app_context(),
        patch.object(db.session, "scalar", side_effect=values),
        patch("app.scheduling._acquire_date_locks"),
        patch("app.scheduling.lock_actor", return_value=actor),
        pytest.raises(SchedulingConflictError, match="changed concurrently"),
    ):
        _lock_booking_with_current_dates(1, actual)
    assert not db.session().in_transaction()


@pytest.mark.parametrize("operation", ["reschedule", "cancel"])
def test_schedule_changes_use_shared_date_lock_helper(app, operation):
    ids = scheduled(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        with patch(
            "app.scheduling._acquire_date_locks",
            wraps=_acquire_date_locks,
        ) as locks:
            if operation == "reschedule":
                reschedule_booking(
                    ids["booking"],
                    planning_window()[1],
                    PrepPeriod.PREP_2,
                    ids["room2"],
                )
            else:
                cancel_booking(ids["booking"])
        locks.assert_called()


def test_postgresql_advisory_lock_uses_expected_key(app):
    target = date(2026, 8, 1)
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    with (
        app.app_context(),
        patch.object(db.session, "get_bind", return_value=bind),
        patch.object(db.session, "execute") as execute,
    ):
        assert acquire_schedule_date_lock(target) == advisory_lock_key(target)
    statement, parameters = execute.call_args.args
    assert str(statement) == "SELECT pg_advisory_xact_lock(:schedule_date_key)"
    assert parameters == {"schedule_date_key": advisory_lock_key(target)}


def test_sqlite_advisory_lock_does_not_execute_postgresql_statement(app):
    with app.app_context(), patch.object(db.session, "execute") as execute:
        acquire_schedule_date_lock(date(2026, 8, 1))
    execute.assert_not_called()
