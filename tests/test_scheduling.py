"""Milestone 6 scheduling and availability-block tests."""

import re
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask_login import login_user
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AuditLog,
    BlockScope,
    BookingRequest,
    Notification,
    PrepPeriod,
    RequestStatus,
    Room,
    RoomBlock,
    ScheduledBooking,
    SchoolClass,
    SystemSettings,
    User,
    UserRole,
)
from app.scheduling import (
    SchedulingError,
    acquire_schedule_date_lock,
    advisory_lock_key,
    create_block,
    kigali_today,
    planning_window,
    remove_block,
    schedule_request,
    slot_states,
)

PASSWORD = "TemporaryPass123!"


@pytest.fixture(autouse=True)
def database(app):
    with app.app_context():
        db.create_all()
        db.session.add(SystemSettings(id=1))
        db.session.commit()
        yield
        db.session.remove()
        db.drop_all()


def user(username, role, school_class=None, active=True, forced=False):
    record = User(
        username=username,
        full_name=username.title(),
        password_hash="pending",
        role=role,
        school_class=school_class,
        is_active=active,
        must_change_password=forced,
    )
    record.set_password(PASSWORD)
    return record


def seed(app):
    with app.app_context():
        school_class = SchoolClass(name="S1 A")
        room = Room(name="Smart Class 1")
        teacher = user("teacher", UserRole.TEACHER)
        scheduler = user("scheduler", UserRole.SCHEDULER)
        admin = user("admin", UserRole.ADMIN)
        monitor = user("monitor", UserRole.MONITOR, school_class)
        request = BookingRequest(
            requester=teacher,
            school_class=school_class,
            subject="Mathematics",
            reason="Private lesson preparation",
        )
        db.session.add_all(
            [school_class, room, teacher, scheduler, admin, monitor, request]
        )
        db.session.commit()
        return {
            "class": school_class.id,
            "room": room.id,
            "teacher": teacher.id,
            "scheduler": scheduler.id,
            "admin": admin.id,
            "monitor": monitor.id,
            "request": request.id,
        }


def login(client, username):
    return client.post(
        "/auth/login", data={"username": username, "password": PASSWORD}
    )


def schedule_payload(room_id, target=None):
    return {
        "schedule_date": (target or planning_window()[0]).isoformat(),
        "prep": PrepPeriod.PREP_1.value,
        "room_id": room_id,
    }


def csrf_token(response):
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def assert_failed_schedule(app, request_id, booking_count=0):
    with app.app_context():
        request = db.session.get(BookingRequest, request_id)
        assert request.status == RequestStatus.PENDING
        count = db.session.scalar(db.select(db.func.count(ScheduledBooking.id)))
        assert count == booking_count
        assert db.session.scalar(db.select(Notification.id)) is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "REQUEST_SCHEDULED")
        ) is None


def test_kigali_window_is_three_calendar_days_and_rolls():
    monday = date(2026, 7, 27)
    assert planning_window(monday) == (
        monday,
        monday + timedelta(days=1),
        monday + timedelta(days=2),
    )
    before_midnight = datetime(2026, 7, 27, 21, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 27, 22, 0, tzinfo=UTC)
    assert kigali_today(before_midnight) == date(2026, 7, 27)
    assert kigali_today(after_midnight) == date(2026, 7, 28)
    saturday = date(2026, 8, 1)
    assert planning_window(saturday)[0] == saturday


def test_date_lock_key_is_stable():
    target = date(2026, 7, 29)
    assert advisory_lock_key(target) == advisory_lock_key(target)
    assert advisory_lock_key(target) != advisory_lock_key(target + timedelta(days=1))


@pytest.mark.parametrize("username", ["admin", "teacher", "monitor"])
def test_only_scheduler_can_schedule(client, app, username):
    ids = seed(app)
    login(client, username)
    response = client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(ids["room"]),
    )
    assert response.status_code == 403


def test_unauthenticated_scheduling_redirects(client, app):
    ids = seed(app)
    response = client.get(f"/scheduler/requests/{ids['request']}/schedule")
    assert "/auth/login" in response.location


def test_valid_scheduling_is_atomic_and_private_fields_are_not_disclosed(
    client, app
):
    ids = seed(app)
    login(client, "scheduler")
    response = client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(ids["room"]),
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        request = db.session.get(BookingRequest, ids["request"])
        booking = db.session.scalar(db.select(ScheduledBooking))
        notification = db.session.scalar(db.select(Notification))
        audit = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "REQUEST_SCHEDULED")
        )
        assert request.status == RequestStatus.SCHEDULED
        assert booking.class_id == request.class_id
        assert booking.teacher_id == request.teacher_id
        assert booking.room_id == ids["room"]
        assert booking.scheduled_by_id == ids["scheduler"]
        assert notification.user_id == request.requester_id
        assert "Private lesson preparation" not in notification.message
        assert "reason" not in audit.details


def test_slot_grid_resolves_booking_and_block_states(client, app):
    ids = seed(app)
    target = planning_window()[0]
    with app.app_context():
        room = db.session.get(Room, ids["room"])
        scheduler = db.session.get(User, ids["scheduler"])
        block = RoomBlock(
            block_date=target,
            scope=BlockScope.SLOT,
            room=room,
            prep=PrepPeriod.PREP_1,
            created_by=scheduler,
        )
        db.session.add(block)
        db.session.commit()
        _rooms, states = slot_states(target)
        assert states[(room.id, PrepPeriod.PREP_1)][0] == "Unavailable"
        assert states[(room.id, PrepPeriod.PREP_2)][0] == "Available"
        block.is_active = False
        db.session.commit()
        _rooms, states = slot_states(target)
        assert states[(room.id, PrepPeriod.PREP_1)][0] == "Available"


@pytest.mark.parametrize(
    ("scope", "unavailable"),
    [
        (
            BlockScope.ROOM_DAY,
            (PrepPeriod.PREP_1, PrepPeriod.PREP_2),
        ),
        (
            BlockScope.DAY,
            (PrepPeriod.PREP_1, PrepPeriod.PREP_2),
        ),
    ],
)
def test_broad_blocks_cover_both_preps(app, scope, unavailable):
    ids = seed(app)
    target = planning_window()[0]
    with app.app_context():
        block = RoomBlock(
            block_date=target,
            scope=scope,
            room_id=ids["room"] if scope == BlockScope.ROOM_DAY else None,
            created_by_id=ids["scheduler"],
        )
        db.session.add(block)
        db.session.commit()
        _rooms, states = slot_states(target)
        assert all(
            states[(ids["room"], prep)][0] == "Unavailable"
            for prep in unavailable
        )


@pytest.mark.parametrize("username", ["scheduler", "admin"])
@pytest.mark.parametrize(
    ("scope", "room_key", "prep"),
    [
        (BlockScope.SLOT, "room", PrepPeriod.PREP_1.value),
        (BlockScope.ROOM_DAY, "room", ""),
        (BlockScope.DAY, None, ""),
    ],
)
def test_scheduler_and_admin_create_each_block_scope(
    client, app, username, scope, room_key, prep
):
    ids = seed(app)
    login(client, username)
    data = {
        "scope": scope.value,
        "room_id": ids[room_key] if room_key else 0,
        "prep": prep,
        "reason": "  Maintenance\nkeep this line  ",
    }
    response = client.post(
        f"/scheduler/schedule/{planning_window()[0].isoformat()}/blocks", data=data
    )
    assert response.status_code == 302
    with app.app_context():
        block = db.session.scalar(db.select(RoomBlock))
        assert block.scope == scope
        assert block.reason == "Maintenance\nkeep this line"


@pytest.mark.parametrize("username", ["teacher", "monitor"])
def test_requesters_cannot_block(client, app, username):
    ids = seed(app)
    login(client, username)
    response = client.post(
        f"/scheduler/schedule/{planning_window()[0].isoformat()}/blocks",
        data={
            "scope": "SLOT",
            "room_id": ids["room"],
            "prep": "PREP_1",
        },
    )
    assert response.status_code == 403


def test_block_over_booking_and_scheduling_over_block_are_rejected(client, app):
    ids = seed(app)
    target = planning_window()[0]
    login(client, "scheduler")
    client.post(
        f"/scheduler/schedule/{target.isoformat()}/blocks",
        data={
            "scope": "SLOT",
            "room_id": ids["room"],
            "prep": "PREP_1",
        },
    )
    response = client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(ids["room"], target),
        follow_redirects=True,
    )
    assert b"unavailable" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(ScheduledBooking)) is None
        persisted = db.session.get(BookingRequest, ids["request"])
        assert persisted.status == RequestStatus.PENDING
        assert (
            db.session.scalar(
                db.select(AuditLog).where(AuditLog.action == "REQUEST_SCHEDULED")
            )
            is None
        )


def test_unblocking_is_soft_and_idempotent(client, app):
    ids = seed(app)
    target = planning_window()[0]
    with app.app_context():
        block = RoomBlock(
            block_date=target,
            scope=BlockScope.DAY,
            created_by_id=ids["scheduler"],
        )
        db.session.add(block)
        db.session.commit()
        block_id = block.id
    login(client, "admin")
    assert client.post(f"/scheduler/blocks/{block_id}/remove").status_code == 302
    assert client.post(f"/scheduler/blocks/{block_id}/remove").status_code == 302
    with app.app_context():
        block = db.session.get(RoomBlock, block_id)
        audits = db.session.scalars(
            db.select(AuditLog).where(AuditLog.action == "BLOCK_REMOVED")
        ).all()
        assert not block.is_active
        assert block.removed_at is not None
        assert block.removed_by_id == ids["admin"]
        assert len(audits) == 1


def test_outside_window_is_rejected(client, app):
    ids = seed(app)
    login(client, "scheduler")
    fourth = planning_window()[0] + timedelta(days=3)
    response = client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(ids["room"], fourth),
        follow_redirects=True,
    )
    assert b"three-day window" in response.data
    with app.app_context():
        persisted = db.session.get(BookingRequest, ids["request"])
        assert persisted.status == RequestStatus.PENDING


@pytest.mark.parametrize(
    ("start_count", "expected_locked"), [(12, True), (11, True), (10, False)]
)
def test_scheduling_queue_hysteresis(client, app, start_count, expected_locked):
    ids = seed(app)
    with app.app_context():
        school_class = db.session.get(SchoolClass, ids["class"])
        teacher = db.session.get(User, ids["teacher"])
        for index in range(start_count - 1):
            db.session.add(
                BookingRequest(
                    requester=teacher,
                    school_class=school_class,
                    subject=f"Extra {index}",
                    reason="Private",
                )
            )
        settings = db.session.get(SystemSettings, 1)
        settings.booking_queue_locked = True
        db.session.commit()
    login(client, "scheduler")
    client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(ids["room"]),
    )
    with app.app_context():
        settings = db.session.get(SystemSettings, 1)
        assert settings.booking_queue_locked is expected_locked
        reopened = db.session.scalars(
            db.select(AuditLog).where(AuditLog.action == "QUEUE_REOPENED")
        ).all()
        assert bool(reopened) is (not expected_locked)


@pytest.mark.parametrize("failure_point", ["flush", "commit"])
def test_uniqueness_failure_rolls_back_complete_schedule(
    client, app, failure_point
):
    ids = seed(app)
    with app.app_context():
        settings = db.session.get(SystemSettings, 1)
        settings.booking_queue_locked = True
        db.session.commit()
    login(client, "scheduler")
    error = IntegrityError("unique conflict", {}, Exception("collision"))
    with patch.object(db.session, failure_point, side_effect=error):
        response = client.post(
            f"/scheduler/requests/{ids['request']}/schedule",
            data=schedule_payload(ids["room"]),
            follow_redirects=True,
        )
    assert b"The requested slot was taken concurrently" in response.data
    assert_failed_schedule(app, ids["request"])
    with app.app_context():
        assert db.session.get(SystemSettings, 1).booking_queue_locked


def test_missing_settings_service_failure_rolls_back_completely(app):
    ids = seed(app)
    target = planning_window()[0]
    with app.test_request_context():
        scheduler = db.session.get(User, ids["scheduler"])
        login_user(scheduler)
        db.session.delete(db.session.get(SystemSettings, 1))
        db.session.commit()
        with pytest.raises(
            SchedulingError,
            match="System settings are unavailable. Please contact an Administrator.",
        ):
            schedule_request(
                ids["request"], target, PrepPeriod.PREP_1, ids["room"]
            )
        assert not db.session().in_transaction()
        assert_failed_schedule(app, ids["request"])
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "QUEUE_REOPENED")
        ) is None


def test_missing_settings_route_returns_safe_non_500_response(client, app):
    ids = seed(app)
    with app.app_context():
        db.session.delete(db.session.get(SystemSettings, 1))
        db.session.commit()
    login(client, "scheduler")
    response = client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(ids["room"]),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"System settings are unavailable. Please contact an Administrator." in (
        response.data
    )
    assert_failed_schedule(app, ids["request"])
    with app.app_context():
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "QUEUE_REOPENED")
        ) is None


@pytest.mark.parametrize("failure_point", ["flush", "commit"])
@pytest.mark.parametrize(
    ("scope", "room_key", "prep"),
    [
        (BlockScope.SLOT, "room", PrepPeriod.PREP_1),
        (BlockScope.ROOM_DAY, "room", None),
        (BlockScope.DAY, None, None),
    ],
)
def test_block_flush_or_commit_failure_rolls_back_completely(
    app, failure_point, scope, room_key, prep
):
    ids = seed(app)
    target = planning_window()[0]
    error = IntegrityError("database failure", {}, Exception("failure"))
    with app.test_request_context():
        scheduler = db.session.get(User, ids["scheduler"])
        login_user(scheduler)
        with patch.object(db.session, failure_point, side_effect=error):
            with pytest.raises(
                SchedulingError,
                match="Unable to create the block. Please try again.",
            ):
                create_block(
                    target,
                    scope,
                    ids[room_key] if room_key else None,
                    prep,
                )
        assert not db.session().in_transaction()
        assert db.session.scalar(db.select(RoomBlock.id)) is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action.like("%BLOCK_CREATED"))
        ) is None


def test_repeated_unblock_explicitly_rolls_back_and_preserves_history(app):
    ids = seed(app)
    target = planning_window()[0]
    with app.test_request_context():
        actor = db.session.get(User, ids["admin"])
        login_user(actor)
        block = RoomBlock(
            block_date=target,
            scope=BlockScope.DAY,
            created_by_id=ids["scheduler"],
        )
        db.session.add(block)
        db.session.commit()
        block_id = block.id
        changed, _ = remove_block(block_id)
        assert changed
        persisted = db.session.get(RoomBlock, block_id)
        removed_at = persisted.removed_at
        removed_by_id = persisted.removed_by_id
        with patch.object(
            db.session, "rollback", wraps=db.session.rollback
        ) as rollback:
            changed, returned_date = remove_block(block_id)
            assert not changed
            assert returned_date == target
            rollback.assert_called_once_with()
        persisted = db.session.get(RoomBlock, block_id)
        assert persisted.removed_at == removed_at
        assert persisted.removed_by_id == removed_by_id
        assert len(
            db.session.scalars(
                db.select(AuditLog).where(AuditLog.action == "BLOCK_REMOVED")
            ).all()
        ) == 1


def test_csrf_protects_block_unblock_and_scheduling(client, app):
    ids = seed(app)
    app.config["WTF_CSRF_ENABLED"] = True
    login_page = client.get("/auth/login")
    login_response = client.post(
        "/auth/login",
        data={
            "username": "scheduler",
            "password": PASSWORD,
            "csrf_token": csrf_token(login_page),
        },
    )
    assert login_response.status_code == 302
    target = planning_window()[0]
    block_url = f"/scheduler/schedule/{target.isoformat()}/blocks"
    block_data = {
        "scope": "SLOT",
        "room_id": ids["room"],
        "prep": "PREP_2",
    }
    assert client.post(block_url, data=block_data).status_code == 400
    schedule_page = client.get(f"/scheduler/schedule/{target.isoformat()}")
    block_data["csrf_token"] = csrf_token(schedule_page)
    assert client.post(block_url, data=block_data).status_code == 302
    with app.app_context():
        block_id = db.session.scalar(db.select(RoomBlock.id))
    assert client.post(f"/scheduler/blocks/{block_id}/remove").status_code == 400
    schedule_page = client.get(f"/scheduler/schedule/{target.isoformat()}")
    assert client.post(
        f"/scheduler/blocks/{block_id}/remove",
        data={"csrf_token": csrf_token(schedule_page)},
    ).status_code == 302
    request_url = f"/scheduler/requests/{ids['request']}/schedule"
    missing_token = client.post(request_url, data=schedule_payload(ids["room"]))
    assert missing_token.status_code == 400
    request_page = client.get(request_url)
    payload = schedule_payload(ids["room"])
    payload["csrf_token"] = csrf_token(request_page)
    assert client.post(request_url, data=payload).status_code == 302


def make_conflicting_schedule(
    app, ids, *, room_id=None, class_id=None, teacher_id=None
):
    with app.app_context():
        alternate_class = SchoolClass(name="S2 A")
        alternate_room = Room(name="Smart Class 2")
        alternate_teacher = user("teacher-two", UserRole.TEACHER)
        db.session.add_all([alternate_class, alternate_room, alternate_teacher])
        db.session.flush()
        owner = db.session.get(User, ids["teacher"])
        conflict_request = BookingRequest(
            requester=owner,
            school_class=db.session.get(SchoolClass, class_id or alternate_class.id),
            subject="Existing",
            reason="Private",
        )
        db.session.add(conflict_request)
        db.session.flush()
        conflict_request.status = RequestStatus.SCHEDULED
        booking = ScheduledBooking(
            request=conflict_request,
            schedule_date=planning_window()[0],
            prep=PrepPeriod.PREP_1,
            room_id=room_id or alternate_room.id,
            class_id=class_id or alternate_class.id,
            teacher_id=teacher_id or alternate_teacher.id,
            scheduled_by_id=ids["scheduler"],
        )
        db.session.add(booking)
        db.session.commit()
        return alternate_room.id, alternate_class.id, alternate_teacher.id


@pytest.mark.parametrize("conflict_kind", ["room", "class", "teacher"])
def test_each_booking_conflict_rejects_without_side_effects(
    client, app, conflict_kind
):
    ids = seed(app)
    kwargs = {
        "room_id": ids["room"] if conflict_kind == "room" else None,
        "class_id": ids["class"] if conflict_kind == "class" else None,
        "teacher_id": ids["teacher"] if conflict_kind == "teacher" else None,
    }
    alternate_room, _class, _teacher = make_conflicting_schedule(app, ids, **kwargs)
    login(client, "scheduler")
    room_id = alternate_room if conflict_kind != "room" else ids["room"]
    client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(room_id),
    )
    assert_failed_schedule(app, ids["request"], booking_count=1)


@pytest.mark.parametrize("scope", list(BlockScope))
def test_each_block_scope_prevents_scheduling(client, app, scope):
    ids = seed(app)
    target = planning_window()[0]
    with app.app_context():
        db.session.add(
            RoomBlock(
                block_date=target,
                scope=scope,
                room_id=ids["room"] if scope != BlockScope.DAY else None,
                prep=PrepPeriod.PREP_1 if scope == BlockScope.SLOT else None,
                created_by_id=ids["scheduler"],
            )
        )
        db.session.commit()
    login(client, "scheduler")
    client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(ids["room"]),
    )
    assert_failed_schedule(app, ids["request"])


@pytest.mark.parametrize("inactive_entity", ["room", "class", "teacher"])
def test_inactive_schedule_dependency_rejects_cleanly(
    client, app, inactive_entity
):
    ids = seed(app)
    with app.app_context():
        model = {"room": Room, "class": SchoolClass, "teacher": User}[inactive_entity]
        db.session.get(model, ids[inactive_entity]).is_active = False
        db.session.commit()
    login(client, "scheduler")
    client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(ids["room"]),
    )
    assert_failed_schedule(app, ids["request"])


def test_non_pending_request_rejected_without_success_side_effects(client, app):
    ids = seed(app)
    with app.app_context():
        db.session.get(BookingRequest, ids["request"]).status = RequestStatus.CANCELLED
        db.session.commit()
    login(client, "scheduler")
    assert (
        client.get(f"/scheduler/requests/{ids['request']}/schedule").status_code
        == 409
    )
    with app.app_context():
        assert db.session.scalar(db.select(ScheduledBooking.id)) is None
        assert db.session.scalar(db.select(Notification.id)) is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "REQUEST_SCHEDULED")
        ) is None


@pytest.mark.parametrize("prep", list(PrepPeriod))
def test_room_day_block_rejected_when_either_prep_booked(client, app, prep):
    ids = seed(app)
    make_conflicting_schedule(app, ids, room_id=ids["room"])
    with app.app_context():
        booking = db.session.scalar(db.select(ScheduledBooking))
        booking.prep = prep
        db.session.commit()
    login(client, "scheduler")
    client.post(
        f"/scheduler/schedule/{planning_window()[0].isoformat()}/blocks",
        data={"scope": "ROOM_DAY", "room_id": ids["room"], "prep": ""},
    )
    with app.app_context():
        assert db.session.scalar(db.select(RoomBlock.id)) is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action.like("%BLOCK_CREATED"))
        ) is None


def test_day_block_rejected_when_any_slot_booked(client, app):
    ids = seed(app)
    make_conflicting_schedule(app, ids)
    login(client, "scheduler")
    client.post(
        f"/scheduler/schedule/{planning_window()[0].isoformat()}/blocks",
        data={"scope": "DAY", "room_id": 0, "prep": ""},
    )
    with app.app_context():
        assert db.session.scalar(db.select(RoomBlock.id)) is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action.like("%BLOCK_CREATED"))
        ) is None


def test_inactive_block_does_not_prevent_scheduling(client, app):
    ids = seed(app)
    with app.app_context():
        db.session.add(
            RoomBlock(
                block_date=planning_window()[0],
                scope=BlockScope.DAY,
                created_by_id=ids["scheduler"],
                is_active=False,
            )
        )
        db.session.commit()
    login(client, "scheduler")
    client.post(
        f"/scheduler/requests/{ids['request']}/schedule",
        data=schedule_payload(ids["room"]),
    )
    with app.app_context():
        persisted = db.session.get(BookingRequest, ids["request"])
        assert persisted.status == RequestStatus.SCHEDULED


@pytest.mark.parametrize(
    ("operation", "actor_name", "actor_key"),
    [
        ("schedule", "scheduler", "scheduler"),
        ("block", "scheduler", "scheduler"),
        ("block", "admin", "admin"),
        ("unblock", "scheduler", "scheduler"),
        ("unblock", "admin", "admin"),
    ],
)
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("is_active", False),
        ("role", UserRole.TEACHER),
        ("must_change_password", True),
    ],
)
def test_stale_actor_is_revalidated_and_rejected(
    app, operation, actor_name, actor_key, column, value
):
    ids = seed(app)
    target = planning_window()[0]
    with app.test_request_context():
        actor = db.session.get(User, ids[actor_key])
        login_user(actor)
        block_id = None
        if operation == "unblock":
            block = RoomBlock(
                block_date=target,
                scope=BlockScope.DAY,
                created_by_id=ids[actor_key],
            )
            db.session.add(block)
            db.session.commit()
            block_id = block.id
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
            if operation == "schedule":
                schedule_request(
                    ids["request"], target, PrepPeriod.PREP_1, ids["room"]
                )
            elif operation == "block":
                create_block(target, BlockScope.DAY)
            else:
                remove_block(block_id)
        assert not db.session().in_transaction()
        assert db.session.scalar(
            db.select(AuditLog.id).where(
                AuditLog.action.in_(
                    ["REQUEST_SCHEDULED", "DAY_BLOCK_CREATED", "BLOCK_REMOVED"]
                )
            )
        ) is None


def test_postgresql_advisory_lock_executes_expected_statement(app):
    target = date(2026, 8, 1)
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    with app.app_context(), patch.object(
        db.session, "get_bind", return_value=bind
    ), patch.object(db.session, "execute") as execute:
        key = acquire_schedule_date_lock(target)
        execute.assert_called_once()
        statement, parameters = execute.call_args.args
        assert str(statement) == (
            "SELECT pg_advisory_xact_lock(:schedule_date_key)"
        )
        assert parameters == {"schedule_date_key": advisory_lock_key(target)}
        assert key == advisory_lock_key(target)


def test_sqlite_advisory_lock_fallback_does_not_execute_sql(app):
    with app.app_context(), patch.object(db.session, "execute") as execute:
        acquire_schedule_date_lock(date(2026, 8, 1))
        execute.assert_not_called()


def test_all_schedule_mutations_use_shared_date_lock(client, app):
    ids = seed(app)
    target = planning_window()[0]
    login(client, "scheduler")
    with patch(
        "app.scheduling.acquire_schedule_date_lock",
        wraps=acquire_schedule_date_lock,
    ) as lock:
        client.post(
            f"/scheduler/requests/{ids['request']}/schedule",
            data=schedule_payload(ids["room"]),
        )
        lock.assert_called_with(target)


@pytest.mark.parametrize("scope", list(BlockScope))
def test_each_block_scope_uses_shared_date_lock(client, app, scope):
    ids = seed(app)
    target = planning_window()[0]
    login(client, "admin")
    data = {
        "scope": scope.value,
        "room_id": ids["room"] if scope != BlockScope.DAY else 0,
        "prep": PrepPeriod.PREP_1.value if scope == BlockScope.SLOT else "",
    }
    with patch(
        "app.scheduling.acquire_schedule_date_lock",
        wraps=acquire_schedule_date_lock,
    ) as lock:
        client.post(f"/scheduler/schedule/{target.isoformat()}/blocks", data=data)
        lock.assert_called_once_with(target)


def test_unblock_uses_shared_date_lock(client, app):
    ids = seed(app)
    target = planning_window()[0]
    with app.app_context():
        block = RoomBlock(
            block_date=target,
            scope=BlockScope.DAY,
            created_by_id=ids["scheduler"],
        )
        db.session.add(block)
        db.session.commit()
        block_id = block.id
    login(client, "admin")
    with patch(
        "app.scheduling.acquire_schedule_date_lock",
        wraps=acquire_schedule_date_lock,
    ) as lock:
        client.post(f"/scheduler/blocks/{block_id}/remove")
        lock.assert_called_once_with(target)


@pytest.mark.parametrize(
    "failure_kind", ["non_pending", "invalid_date", "inactive_room", "conflict"]
)
def test_direct_schedule_domain_failures_end_transaction(app, failure_kind):
    ids = seed(app)
    target = planning_window()[0]
    with app.test_request_context():
        scheduler = db.session.get(User, ids["scheduler"])
        login_user(scheduler)
        expected_status = RequestStatus.PENDING
        if failure_kind == "non_pending":
            request = db.session.get(BookingRequest, ids["request"])
            request.status = RequestStatus.CANCELLED
            db.session.commit()
            expected_status = RequestStatus.CANCELLED
        elif failure_kind == "invalid_date":
            target += timedelta(days=3)
        elif failure_kind == "inactive_room":
            db.session.get(Room, ids["room"]).is_active = False
            db.session.commit()
        else:
            db.session.add(
                RoomBlock(
                    block_date=target,
                    scope=BlockScope.SLOT,
                    room_id=ids["room"],
                    prep=PrepPeriod.PREP_1,
                    created_by_id=ids["scheduler"],
                )
            )
            db.session.commit()
        with pytest.raises(SchedulingError):
            schedule_request(
                ids["request"], target, PrepPeriod.PREP_1, ids["room"]
            )
        assert not db.session().in_transaction()
        persisted = db.session.get(BookingRequest, ids["request"])
        assert persisted.status == expected_status
        assert db.session.scalar(db.select(ScheduledBooking.id)) is None
        assert db.session.scalar(db.select(Notification.id)) is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(
                AuditLog.action.in_(["REQUEST_SCHEDULED", "QUEUE_REOPENED"])
            )
        ) is None


@pytest.mark.parametrize(
    "failure_kind", ["invalid_fields", "inactive_room", "booking", "model"]
)
def test_direct_block_failures_end_transaction(app, failure_kind):
    ids = seed(app)
    target = planning_window()[0]
    with app.test_request_context():
        scheduler = db.session.get(User, ids["scheduler"])
        login_user(scheduler)
        scope = BlockScope.SLOT
        room_id = ids["room"]
        prep = PrepPeriod.PREP_1
        if failure_kind == "invalid_fields":
            prep = None
        elif failure_kind == "inactive_room":
            db.session.get(Room, ids["room"]).is_active = False
            db.session.commit()
        elif failure_kind == "booking":
            make_conflicting_schedule(app, ids, room_id=ids["room"])
        error_context = (
            patch.object(db.session, "flush", side_effect=ValueError("invalid"))
            if failure_kind == "model"
            else nullcontext()
        )
        with error_context, pytest.raises(SchedulingError):
            create_block(target, scope, room_id, prep)
        assert not db.session().in_transaction()
        assert db.session.scalar(db.select(RoomBlock.id)) is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action.like("%BLOCK_CREATED"))
        ) is None


def make_day_block(app, ids):
    with app.app_context():
        block = RoomBlock(
            block_date=planning_window()[0],
            scope=BlockScope.DAY,
            created_by_id=ids["scheduler"],
        )
        db.session.add(block)
        db.session.commit()
        return block.id


def test_direct_missing_unblock_ends_transaction(app):
    ids = seed(app)
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        with pytest.raises(SchedulingError, match="Block not found"):
            remove_block(999999)
        assert not db.session().in_transaction()
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "BLOCK_REMOVED")
        ) is None


def test_direct_successful_unblock_returns_without_new_transaction(app):
    ids = seed(app)
    block_id = make_day_block(app, ids)
    target = planning_window()[0]
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        changed, returned_date = remove_block(block_id)
        assert changed
        assert returned_date == target
        assert not db.session().in_transaction()
        persisted = db.session.get(RoomBlock, block_id)
        assert not persisted.is_active
        assert persisted.removed_at is not None
        assert persisted.removed_by_id == ids["admin"]


def test_direct_unblock_commit_failure_rolls_back_and_ends_transaction(app):
    ids = seed(app)
    block_id = make_day_block(app, ids)
    error = IntegrityError("commit failure", {}, Exception("failure"))
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        with patch.object(db.session, "commit", side_effect=error):
            with pytest.raises(
                SchedulingError,
                match="Unable to remove the block. Please try again.",
            ):
                remove_block(block_id)
        assert not db.session().in_transaction()
        persisted = db.session.get(RoomBlock, block_id)
        assert persisted.is_active
        assert persisted.removed_at is None
        assert persisted.removed_by_id is None
        assert db.session.scalar(
            db.select(AuditLog.id).where(AuditLog.action == "BLOCK_REMOVED")
        ) is None
