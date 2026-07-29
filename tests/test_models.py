"""Database model rules."""

from datetime import date

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    BlockScope,
    BookingRequest,
    PrepPeriod,
    RequestPriority,
    RequestStatus,
    Room,
    RoomBlock,
    ScheduledBooking,
    SchoolClass,
    SystemSettings,
    User,
    UserRole,
)


@pytest.fixture(autouse=True)
def database(app):
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


def user(role, username, school_class=None):
    return User(
        username=username,
        password_hash="hash",
        full_name=username,
        role=role,
        school_class=school_class,
    )


def test_monitor_requires_class(app):
    with app.app_context(), pytest.raises(ValueError, match="assigned class"):
        db.session.add(user(UserRole.MONITOR, "monitor"))
        db.session.flush()


def test_non_monitor_cannot_retain_class(app):
    with app.app_context():
        school_class = SchoolClass(name="S1 A")
        teacher = user(UserRole.TEACHER, "teacher", school_class)
        db.session.add_all([school_class, teacher])
        with pytest.raises(ValueError, match="Non-monitor"):
            db.session.flush()


def test_teacher_request_rules(app):
    with app.app_context():
        school_class = SchoolClass(name="S1 A")
        teacher = user(UserRole.TEACHER, "teacher")
        request = BookingRequest(
            requester=teacher,
            school_class=school_class,
            subject="Math",
            reason="Lesson",
        )
        db.session.add_all([school_class, teacher, request])
        db.session.flush()
        assert request.teacher is teacher and request.priority == RequestPriority.HIGH


def test_monitor_request_rules(app):
    with app.app_context():
        school_class = SchoolClass(name="S1 A")
        monitor = user(UserRole.MONITOR, "monitor", school_class)
        teacher = user(UserRole.TEACHER, "teacher")
        request = BookingRequest(
            requester=monitor, teacher=teacher, subject="Math", reason="Lesson"
        )
        db.session.add_all([school_class, monitor, teacher, request])
        db.session.flush()
        assert (
            request.class_id == school_class.id
            and request.priority == RequestPriority.NORMAL
        )


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.SCHEDULER, UserRole.MONITOR])
def test_monitor_responsible_user_must_be_teacher(app, role):
    with app.app_context():
        school_class = SchoolClass(name="S1 A")
        monitor = user(UserRole.MONITOR, "monitor", school_class)
        responsible_user = user(
            role, "responsible", school_class if role == UserRole.MONITOR else None
        )
        request = BookingRequest(
            requester=monitor,
            teacher=responsible_user,
            subject="Math",
            reason="Lesson",
        )
        db.session.add_all([school_class, monitor, responsible_user, request])
        with pytest.raises(ValueError, match="responsible teacher"):
            db.session.flush()


def test_monitor_class_change_does_not_rewrite_historical_request(app):
    with app.app_context():
        original_class = SchoolClass(name="S1 A")
        new_class = SchoolClass(name="S1 B")
        monitor = user(UserRole.MONITOR, "monitor", original_class)
        teacher = user(UserRole.TEACHER, "teacher")
        request = BookingRequest(
            requester=monitor, teacher=teacher, subject="Math", reason="Lesson"
        )
        db.session.add_all([original_class, new_class, monitor, teacher, request])
        db.session.commit()
        original_class_id = request.class_id

        monitor.school_class = new_class
        request.status = RequestStatus.REJECTED
        db.session.commit()

        assert request.class_id == original_class_id


@pytest.mark.parametrize(
    "attribute", ["requester_id", "teacher_id", "priority"]
)
def test_persisted_request_identity_is_immutable(app, attribute):
    with app.app_context():
        class1, class2 = SchoolClass(name="C1"), SchoolClass(name="C2")
        teacher1, teacher2 = user(UserRole.TEACHER, "t1"), user(UserRole.TEACHER, "t2")
        request = BookingRequest(
            requester=teacher1, school_class=class1, subject="Math", reason="Lesson"
        )
        db.session.add_all([class1, class2, teacher1, teacher2, request])
        db.session.commit()
        replacements = {
            "requester_id": teacher2.id,
            "class_id": class2.id,
            "teacher_id": teacher2.id,
            "priority": RequestPriority.NORMAL,
        }
        setattr(request, attribute, replacements[attribute])
        with pytest.raises(ValueError, match="immutable"):
            db.session.flush()


@pytest.mark.parametrize(
    "scope,room,prep",
    [
        (BlockScope.SLOT, False, PrepPeriod.PREP_1),
        (BlockScope.ROOM_DAY, True, PrepPeriod.PREP_1),
        (BlockScope.DAY, True, None),
    ],
)
def test_invalid_room_block_scopes(app, scope, room, prep):
    with app.app_context():
        creator = user(UserRole.SCHEDULER, "scheduler")
        actual_room = Room(name="Room") if room else None
        block = RoomBlock(
            block_date=date.today(),
            scope=scope,
            room=actual_room,
            prep=prep,
            created_by=creator,
        )
        db.session.add_all([creator, block])
        with pytest.raises(ValueError):
            db.session.flush()


def test_system_settings_defaults(app):
    with app.app_context():
        settings = SystemSettings()
        db.session.add(settings)
        db.session.flush()
        assert (
            settings.max_pending_requests,
            settings.reopen_threshold,
            settings.planning_window_days,
            settings.booking_queue_locked,
        ) == (12, 9, 3, False)


@pytest.mark.parametrize(
    "values",
    [
        {"id": 2},
        {"max_pending_requests": 0},
        {"reopen_threshold": -1},
        {"max_pending_requests": 9, "reopen_threshold": 9},
        {"planning_window_days": 0},
    ],
)
def test_invalid_system_settings_are_rejected(app, values):
    with app.app_context():
        db.session.add(SystemSettings(**values))
        with pytest.raises(IntegrityError):
            db.session.commit()


def test_raw_invalid_enum_value_is_rejected(app):
    with app.app_context(), pytest.raises(IntegrityError):
        db.session.execute(
            text(
                "INSERT INTO users "
                "(username, password_hash, full_name, role, class_id, is_active, "
                "must_change_password, created_at, updated_at) VALUES "
                "('raw', 'hash', 'Raw', 'INVALID', NULL, 1, 1, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        )


def test_all_enum_columns_have_database_checks(app):
    expected = {
        "users": {"user_role"},
        "booking_requests": {"request_priority", "request_status"},
        "room_blocks": {"block_scope", "prep_period"},
        "notifications": {"notification_type"},
        "scheduled_bookings": {"prep_period"},
    }
    with app.app_context():
        database_inspector = inspect(db.engine)
        for table, names in expected.items():
            actual = {
                item["name"] for item in database_inspector.get_check_constraints(table)
            }
            assert names <= actual


def make_booking(suffix, room, school_class, teacher, scheduler, active=True):
    request = BookingRequest(
        requester=teacher,
        school_class=school_class,
        subject=f"Subject {suffix}",
        reason="Reason",
    )
    booking = ScheduledBooking(
        request=request,
        schedule_date=date(2026, 7, 22),
        prep=PrepPeriod.PREP_1,
        room=room,
        school_class=school_class,
        teacher=teacher,
        scheduled_by=scheduler,
        is_active=active,
    )
    return request, booking


@pytest.mark.parametrize("conflict", ["room", "class", "teacher"])
def test_active_schedule_uniqueness(app, conflict):
    with app.app_context():
        room1, room2 = Room(name="R1"), Room(name="R2")
        class1, class2 = SchoolClass(name="C1"), SchoolClass(name="C2")
        teacher1, teacher2 = user(UserRole.TEACHER, "t1"), user(UserRole.TEACHER, "t2")
        scheduler = user(UserRole.SCHEDULER, "s")
        first = make_booking("1", room1, class1, teacher1, scheduler)
        db.session.add_all(
            [room1, room2, class1, class2, teacher1, teacher2, scheduler, *first]
        )
        db.session.commit()
        second = make_booking(
            "2",
            room1 if conflict == "room" else room2,
            class1 if conflict == "class" else class2,
            teacher1 if conflict == "teacher" else teacher2,
            scheduler,
        )
        db.session.add_all(second)
        with pytest.raises(IntegrityError):
            db.session.commit()


def test_inactive_schedule_allows_replacement(app):
    with app.app_context():
        room, school_class = Room(name="R1"), SchoolClass(name="C1")
        teacher, scheduler = user(UserRole.TEACHER, "t"), user(UserRole.SCHEDULER, "s")
        old = make_booking("old", room, school_class, teacher, scheduler, False)
        db.session.add_all([room, school_class, teacher, scheduler, *old])
        db.session.commit()
        replacement = make_booking("new", room, school_class, teacher, scheduler)
        db.session.add_all(replacement)
        db.session.commit()
        assert replacement[1].id is not None
