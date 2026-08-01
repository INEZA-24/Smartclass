"""Milestone 5 request and pending-queue tests."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import (
    AuditLog,
    BookingRequest,
    RequestPriority,
    RequestStatus,
    SchoolClass,
    SystemSettings,
    User,
    UserRole,
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


def make_user(app, username, role, school_class=None, forced=False):
    class_id = school_class.id if school_class is not None else None
    with app.app_context():
        assigned_class = db.session.get(SchoolClass, class_id) if class_id else None
        user = User(
            username=username,
            full_name=username.title(),
            password_hash="pending",
            role=role,
            school_class=assigned_class,
            is_active=True,
            must_change_password=forced,
        )
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.commit()
        return user.id


def make_class(app, name="S1 A", active=True):
    with app.app_context():
        item = SchoolClass(name=name, is_active=active)
        db.session.add(item)
        db.session.commit()
        _ = item.id
        db.session.expunge(item)
        return item


def login(client, username):
    return client.post("/auth/login", data={"username": username, "password": PASSWORD})


def teacher_payload(class_id, **extra):
    data = {"class_id": class_id, "subject": " Mathematics ", "reason": " Practice "}
    data.update(extra)
    return data


def monitor_payload(teacher_id, **extra):
    data = {"teacher_id": teacher_id, "subject": " Science ", "reason": " Lab work "}
    data.update(extra)
    return data


@pytest.mark.parametrize(
    "path",
    [
        "/requester/teacher/new",
        "/requester/monitor/new",
        "/requester/teacher/requests",
        "/scheduler/pending",
    ],
)
def test_request_pages_require_login(client, path):
    assert "/auth/login" in client.get(path).location


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.SCHEDULER])
def test_non_requester_roles_cannot_use_request_forms(client, app, role):
    make_user(app, role.value.lower(), role)
    login(client, role.value.lower())
    assert client.get("/requester/teacher/new").status_code == 403
    assert client.get("/requester/monitor/new").status_code == 403


def test_teacher_and_monitor_forms_are_role_specific(client, app):
    school_class = make_class(app)
    make_user(app, "teacher", UserRole.TEACHER)
    make_user(app, "monitor", UserRole.MONITOR, school_class)
    login(client, "teacher")
    assert client.get("/requester/monitor/new").status_code == 403
    client.post("/auth/logout")
    login(client, "monitor")
    assert client.get("/requester/teacher/new").status_code == 403


def test_forced_requester_cannot_bypass_password_change(client, app):
    make_user(app, "teacher", UserRole.TEACHER, forced=True)
    login(client, "teacher")
    response = client.get("/requester/teacher/new")
    assert response.location.endswith("/auth/change-password")


def test_teacher_creates_server_controlled_request(client, app):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    other_id = make_user(app, "other", UserRole.TEACHER)
    login(client, "teacher")
    response = client.post(
        "/requester/teacher/new",
        data=teacher_payload(
            school_class.id,
            requester_id=other_id,
            teacher_id=other_id,
            priority="NORMAL",
            status="CANCELLED",
            date="2026-01-01",
            prep="PREP_1",
            room_id=99,
        ),
    )
    assert response.status_code == 302
    with app.app_context():
        record = db.session.scalar(db.select(BookingRequest))
        assert record.requester_id == teacher_id == record.teacher_id
        assert record.class_id == school_class.id
        assert record.priority == RequestPriority.HIGH
        assert record.status == RequestStatus.PENDING
        assert record.subject == "Mathematics"
        assert record.reason == "Practice"


def test_monitor_creates_server_controlled_request(client, app):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    monitor_id = make_user(app, "monitor", UserRole.MONITOR, school_class)
    login(client, "monitor")
    response = client.post(
        "/requester/monitor/new",
        data=monitor_payload(
            teacher_id,
            requester_id=teacher_id,
            class_id=999,
            priority="HIGH",
            status="CANCELLED",
            room_id=2,
        ),
    )
    assert response.status_code == 302
    with app.app_context():
        record = db.session.scalar(db.select(BookingRequest))
        assert record.requester_id == monitor_id
        assert record.class_id == school_class.id
        assert record.teacher_id == teacher_id
        assert record.priority == RequestPriority.NORMAL
        assert record.status == RequestStatus.PENDING


@pytest.mark.parametrize("role", [UserRole.TEACHER, UserRole.MONITOR])
def test_blank_subject_and_reason_are_rejected(client, app, role):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    if role == UserRole.MONITOR:
        make_user(app, "monitor", role, school_class)
        login(client, "monitor")
        path = "/requester/monitor/new"
        data = monitor_payload(teacher_id, subject="   ", reason=" ")
    else:
        login(client, "teacher")
        path = "/requester/teacher/new"
        data = teacher_payload(school_class.id, subject="   ", reason=" ")
    response = client.post(path, data=data)
    assert b"This field is required." in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(BookingRequest.id))) == 0


def test_only_active_choices_are_displayed(client, app):
    active = make_class(app, "Active Class")
    make_class(app, "Inactive Class", False)
    make_user(app, "active-teacher", UserRole.TEACHER)
    inactive_teacher = make_user(app, "inactive-teacher", UserRole.TEACHER)
    monitor_id = make_user(app, "monitor", UserRole.MONITOR, active)
    with app.app_context():
        db.session.get(User, inactive_teacher).is_active = False
        db.session.commit()
    login(client, "active-teacher")
    page = client.get("/requester/teacher/new")
    assert b"Active Class" in page.data and b"Inactive Class" not in page.data
    client.post("/auth/logout")
    login(client, "monitor")
    page = client.get("/requester/monitor/new")
    assert b"Active-Teacher" in page.data and b"Inactive-Teacher" not in page.data
    assert monitor_id


def test_queue_accepts_twelve_locks_and_rejects_thirteenth(client, app):
    school_class = make_class(app)
    make_user(app, "teacher", UserRole.TEACHER)
    login(client, "teacher")
    for number in range(12):
        response = client.post(
            "/requester/teacher/new",
            data=teacher_payload(school_class.id, subject=f"Subject {number}"),
        )
        assert response.status_code == 302
    rejected = client.post(
        "/requester/teacher/new", data=teacher_payload(school_class.id)
    )
    assert b"temporarily locked" in rejected.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(BookingRequest.id))) == 12
        assert db.session.get(SystemSettings, 1).booking_queue_locked
        assert (
            db.session.scalar(
                db.select(db.func.count(AuditLog.id)).where(
                    AuditLog.action == "QUEUE_LOCKED"
                )
            )
            == 1
        )


def add_pending_requests(app, requester_id, class_id, count):
    with app.app_context():
        requester = db.session.get(User, requester_id)
        school_class = db.session.get(SchoolClass, class_id)
        for number in range(count):
            db.session.add(
                BookingRequest(
                    requester=requester,
                    school_class=school_class,
                    teacher=requester,
                    subject=f"Subject {number}",
                    reason="Private",
                    status=RequestStatus.PENDING,
                )
            )
        db.session.commit()
        return db.session.scalars(
            db.select(BookingRequest.id).order_by(BookingRequest.id)
        ).all()


def test_cancellation_preserves_lock_at_11_and_10_then_reopens_at_9(client, app):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    request_ids = add_pending_requests(app, teacher_id, school_class.id, 12)
    with app.app_context():
        db.session.get(SystemSettings, 1).booking_queue_locked = True
        db.session.commit()
    login(client, "teacher")
    for index, expected_count in enumerate((11, 10, 9)):
        client.post(f"/requester/requests/{request_ids[index]}/cancel")
        with app.app_context():
            settings = db.session.get(SystemSettings, 1)
            assert settings.booking_queue_locked is (expected_count > 9)
    with app.app_context():
        assert (
            db.session.scalar(
                db.select(db.func.count(AuditLog.id)).where(
                    AuditLog.action == "QUEUE_REOPENED"
                )
            )
            == 1
        )


def test_missing_settings_fails_safely(client, app):
    school_class = make_class(app)
    make_user(app, "teacher", UserRole.TEACHER)
    with app.app_context():
        db.session.delete(db.session.get(SystemSettings, 1))
        db.session.commit()
    login(client, "teacher")
    response = client.post(
        "/requester/teacher/new", data=teacher_payload(school_class.id)
    )
    assert b"Unable to save" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(BookingRequest)) is None


def test_history_is_private_and_newest_first(client, app):
    school_class = make_class(app)
    first_id = make_user(app, "first", UserRole.TEACHER)
    make_user(app, "second", UserRole.TEACHER)
    ids = add_pending_requests(app, first_id, school_class.id, 2)
    with app.app_context():
        first = db.session.get(BookingRequest, ids[0])
        second = db.session.get(BookingRequest, ids[1])
        first.subject = "Older"
        second.subject = "Newer"
        db.session.commit()
    login(client, "second")
    page = client.get("/requester/teacher/requests")
    assert b"Older" not in page.data and b"<td>Private</td>" not in page.data
    assert client.get(f"/requester/requests/{ids[0]}/edit").status_code == 404
    assert client.post(f"/requester/requests/{ids[0]}/cancel").status_code == 404
    client.post("/auth/logout")
    login(client, "first")
    page = client.get("/requester/teacher/requests")
    assert page.data.index(b"Newer") < page.data.index(b"Older")


def test_teacher_pending_model_edit_allows_class_not_teacher(app):
    first = make_class(app, "First")
    second = make_class(app, "Second")
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    other_id = make_user(app, "other", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, first.id, 1)[0]
    with app.app_context():
        record = db.session.get(BookingRequest, request_id)
        record.school_class = db.session.get(SchoolClass, second.id)
        db.session.commit()
        assert record.class_id == second.id
        record.teacher = db.session.get(User, other_id)
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        assert db.session.get(BookingRequest, request_id).teacher_id == teacher_id


def test_monitor_pending_model_edit_allows_teacher_not_class(app):
    first = make_class(app, "First")
    second = make_class(app, "Second")
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    other_id = make_user(app, "other", UserRole.TEACHER)
    monitor_id = make_user(app, "monitor", UserRole.MONITOR, first)
    with app.app_context():
        record = BookingRequest(
            requester=db.session.get(User, monitor_id),
            school_class=db.session.get(SchoolClass, first.id),
            teacher=db.session.get(User, teacher_id),
            subject="Subject",
            reason="Reason",
        )
        db.session.add(record)
        db.session.commit()
        request_id = record.id
        record.teacher = db.session.get(User, other_id)
        db.session.commit()
        assert record.teacher_id == other_id
        record.school_class = db.session.get(SchoolClass, second.id)
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        assert db.session.get(BookingRequest, request_id).class_id == first.id


def test_non_pending_identity_and_core_identity_are_immutable(app):
    first = make_class(app, "First")
    second = make_class(app, "Second")
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    other_id = make_user(app, "other", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, first.id, 1)[0]
    with app.app_context():
        record = db.session.get(BookingRequest, request_id)
        record.status = RequestStatus.CANCELLED
        db.session.commit()
        record.school_class = db.session.get(SchoolClass, second.id)
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        record = db.session.get(BookingRequest, request_id)
        record.requester_id = other_id
        record.priority = RequestPriority.NORMAL
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        persisted = db.session.get(BookingRequest, request_id)
        assert persisted.requester_id == teacher_id
        assert persisted.priority == RequestPriority.HIGH


def test_route_edits_only_role_appropriate_fields(client, app):
    first = make_class(app, "First")
    second = make_class(app, "Second")
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    other_id = make_user(app, "other", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, first.id, 1)[0]
    login(client, "teacher")
    response = client.post(
        f"/requester/requests/{request_id}/edit",
        data=teacher_payload(
            second.id,
            teacher_id=other_id,
            requester_id=other_id,
            priority="NORMAL",
            status="CANCELLED",
        ),
    )
    assert response.status_code == 302
    with app.app_context():
        record = db.session.get(BookingRequest, request_id)
        assert record.class_id == second.id
        assert record.teacher_id == teacher_id
        assert record.requester_id == teacher_id
        assert record.priority == RequestPriority.HIGH
        assert record.status == RequestStatus.PENDING


def test_non_pending_edit_and_repeated_cancel_have_no_false_audit(client, app):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, school_class.id, 1)[0]
    login(client, "teacher")
    client.post(f"/requester/requests/{request_id}/cancel")
    assert client.get(f"/requester/requests/{request_id}/edit").status_code == 409
    client.post(f"/requester/requests/{request_id}/cancel")
    with app.app_context():
        assert (
            db.session.scalar(
                db.select(db.func.count(AuditLog.id)).where(
                    AuditLog.action == "REQUEST_CANCELLED"
                )
            )
            == 1
        )
        assert db.session.get(BookingRequest, request_id) is not None


def test_scheduler_queue_is_read_only_and_priority_ordered(client, app):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    monitor_id = make_user(app, "monitor", UserRole.MONITOR, school_class)
    scheduler_id = make_user(app, "scheduler", UserRole.SCHEDULER)
    with app.app_context():
        teacher = db.session.get(User, teacher_id)
        monitor = db.session.get(User, monitor_id)
        school = db.session.get(SchoolClass, school_class.id)
        base = datetime.now(UTC)
        records = [
            BookingRequest(
                requester=monitor,
                school_class=school,
                teacher=teacher,
                subject="Monitor Old",
                reason="Monitor private",
                created_at=base,
            ),
            BookingRequest(
                requester=teacher,
                school_class=school,
                teacher=teacher,
                subject="Teacher New",
                reason="Teacher private",
                created_at=base + timedelta(seconds=2),
            ),
            BookingRequest(
                requester=teacher,
                school_class=school,
                teacher=teacher,
                subject="Teacher Old",
                reason="Teacher old private",
                created_at=base + timedelta(seconds=1),
            ),
        ]
        db.session.add_all(records)
        db.session.commit()
    login(client, "scheduler")
    page = client.get("/scheduler/pending")
    assert page.status_code == 200
    assert page.data.index(b"Teacher Old") < page.data.index(b"Teacher New")
    assert page.data.index(b"Teacher New") < page.data.index(b"Monitor Old")
    assert b"Teacher private" in page.data
    assert b">Schedule<" in page.data and b">Reject<" not in page.data
    assert scheduler_id


def test_creation_commit_failure_rolls_back_request_and_lock(client, app, monkeypatch):
    school_class = make_class(app)
    make_user(app, "teacher", UserRole.TEACHER)
    with app.app_context():
        teacher = db.session.scalar(db.select(User).where(User.username == "teacher"))
        add_pending_requests(app, teacher.id, school_class.id, 11)
    login(client, "teacher")

    def failed_commit():
        raise OperationalError("COMMIT", {}, RuntimeError("unavailable"))

    monkeypatch.setattr(db.session, "commit", failed_commit)
    response = client.post(
        "/requester/teacher/new", data=teacher_payload(school_class.id)
    )
    assert b"Unable to save" in response.data
    with app.app_context():
        assert pending_count_for_test() == 11
        assert not db.session.get(SystemSettings, 1).booking_queue_locked


def pending_count_for_test():
    return db.session.scalar(
        db.select(db.func.count(BookingRequest.id)).where(
            BookingRequest.status == RequestStatus.PENDING
        )
    )


@pytest.mark.parametrize(
    ("username", "role", "mutation"),
    [
        ("stale-teacher", UserRole.TEACHER, "inactive"),
        ("role-teacher", UserRole.TEACHER, "role"),
        ("stale-monitor", UserRole.MONITOR, "inactive"),
    ],
)
def test_stale_requester_state_rejects_submission(
    client, app, username, role, mutation
):
    school_class = make_class(app, f"Class {username}")
    teacher_id = make_user(app, f"responsible-{username}", UserRole.TEACHER)
    make_user(
        app,
        username,
        role,
        school_class if role == UserRole.MONITOR else None,
    )
    login(client, username)
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == username))
        if mutation == "inactive":
            user.is_active = False
        else:
            user.role = UserRole.SCHEDULER
        db.session.commit()
    path = (
        "/requester/teacher/new"
        if role == UserRole.TEACHER
        else "/requester/monitor/new"
    )
    data = (
        teacher_payload(school_class.id)
        if role == UserRole.TEACHER
        else monitor_payload(teacher_id)
    )
    response = client.post(path, data=data)
    assert response.status_code in {302, 403}
    with app.app_context():
        assert db.session.scalar(db.select(BookingRequest)) is None
        assert (
            db.session.scalar(
                db.select(AuditLog).where(AuditLog.action == "REQUEST_CREATED")
            )
            is None
        )


def test_monitor_class_change_during_submission_is_rejected(client, app, monkeypatch):
    first = make_class(app, "Original Monitor Class")
    second = make_class(app, "Replacement Monitor Class")
    teacher_id = make_user(app, "responsible", UserRole.TEACHER)
    monitor_id = make_user(app, "monitor", UserRole.MONITOR, first)
    login(client, "monitor")
    from app.blueprints.requester import routes as request_routes

    original_lock = request_routes.locked_active_class

    def class_lock_then_reassign(class_id):
        locked = original_lock(class_id)
        monitor = db.session.get(User, monitor_id)
        monitor.school_class = db.session.get(SchoolClass, second.id)
        db.session.flush()
        return locked

    monkeypatch.setattr(request_routes, "locked_active_class", class_lock_then_reassign)
    response = client.post("/requester/monitor/new", data=monitor_payload(teacher_id))
    assert b"assigned class is unavailable" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(BookingRequest)) is None
        assert db.session.get(User, monitor_id).class_id == first.id


def test_invalid_server_selection_rolls_back(client, app, monkeypatch):
    school_class = make_class(app)
    make_user(app, "teacher", UserRole.TEACHER)
    login(client, "teacher")
    calls = []
    original = db.session.rollback

    def recording_rollback():
        calls.append(True)
        return original()

    monkeypatch.setattr(db.session, "rollback", recording_rollback)
    response = client.post("/requester/teacher/new", data=teacher_payload(999999))
    assert b"Select an active class" in response.data
    assert calls
    with app.app_context():
        assert db.session.scalar(db.select(BookingRequest)) is None
    assert school_class.id


def test_multiline_reason_preserved_on_creation_and_edit(client, app):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    login(client, "teacher")
    original_reason = "  First line\n  indented second line\nThird line  "
    client.post(
        "/requester/teacher/new",
        data=teacher_payload(school_class.id, reason=original_reason),
    )
    with app.app_context():
        record = db.session.scalar(db.select(BookingRequest))
        request_id = record.id
        assert record.reason == "First line\n  indented second line\nThird line"
    edited_reason = "  Updated line\n\nFinal line  "
    client.post(
        f"/requester/requests/{request_id}/edit",
        data=teacher_payload(school_class.id, reason=edited_reason),
    )
    with app.app_context():
        assert db.session.get(BookingRequest, request_id).reason == (
            "Updated line\n\nFinal line"
        )
        assert db.session.get(BookingRequest, request_id).requester_id == teacher_id


def test_cancellation_csrf_failure_and_success(client, app):
    import re

    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    request_ids = add_pending_requests(app, teacher_id, school_class.id, 2)
    login(client, "teacher")
    app.config["WTF_CSRF_ENABLED"] = True
    assert (
        client.post(f"/requester/requests/{request_ids[0]}/cancel").status_code == 400
    )
    history = client.get("/requester/teacher/requests")
    token = (
        re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', history.data)
        .group(1)
        .decode()
    )
    response = client.post(
        f"/requester/requests/{request_ids[1]}/cancel",
        data={"csrf_token": token},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(BookingRequest, request_ids[0]).status == (
            RequestStatus.PENDING
        )
        assert db.session.get(BookingRequest, request_ids[1]).status == (
            RequestStatus.CANCELLED
        )


def test_failed_edit_rolls_back_all_fields_and_audit(client, app, monkeypatch):
    first = make_class(app, "Original")
    second = make_class(app, "Replacement")
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, first.id, 1)[0]
    login(client, "teacher")

    def failed_commit():
        raise OperationalError("COMMIT", {}, RuntimeError("unavailable"))

    monkeypatch.setattr(db.session, "commit", failed_commit)
    response = client.post(
        f"/requester/requests/{request_id}/edit",
        data=teacher_payload(second.id, subject="Changed", reason="Changed"),
    )
    assert b"Unable to save" in response.data
    with app.app_context():
        record = db.session.get(BookingRequest, request_id)
        assert record.class_id == first.id
        assert record.subject == "Subject 0"
        assert record.reason == "Private"
        assert (
            db.session.scalar(
                db.select(AuditLog).where(AuditLog.action == "REQUEST_EDITED")
            )
            is None
        )


def test_failed_cancellation_rolls_back_everything(client, app, monkeypatch):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, school_class.id, 1)[0]
    with app.app_context():
        settings = db.session.get(SystemSettings, 1)
        settings.booking_queue_locked = True
        settings.reopen_threshold = 1
        db.session.commit()
    login(client, "teacher")

    def failed_commit():
        raise OperationalError("COMMIT", {}, RuntimeError("unavailable"))

    monkeypatch.setattr(db.session, "commit", failed_commit)
    response = client.post(
        f"/requester/requests/{request_id}/cancel", follow_redirects=True
    )
    assert b"Unable to save" in response.data
    with app.app_context():
        record = db.session.get(BookingRequest, request_id)
        assert record.status == RequestStatus.PENDING
        assert record.cancelled_at is None
        assert db.session.get(SystemSettings, 1).booking_queue_locked
        assert (
            db.session.scalar(
                db.select(AuditLog).where(
                    AuditLog.action.in_(["REQUEST_CANCELLED", "QUEUE_REOPENED"])
                )
            )
            is None
        )


def test_teacher_and_monitor_share_global_capacity(client, app):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    make_user(app, "monitor", UserRole.MONITOR, school_class)
    login(client, "teacher")
    for number in range(11):
        assert (
            client.post(
                "/requester/teacher/new",
                data=teacher_payload(school_class.id, subject=f"T{number}"),
            ).status_code
            == 302
        )
    client.post("/auth/logout")
    login(client, "monitor")
    assert (
        client.post(
            "/requester/monitor/new", data=monitor_payload(teacher_id)
        ).status_code
        == 302
    )
    rejected = client.post("/requester/monitor/new", data=monitor_payload(teacher_id))
    assert b"temporarily locked" in rejected.data
    with app.app_context():
        assert pending_count_for_test() == 12


def test_all_request_audits_exclude_private_reason(client, app):
    school_class = make_class(app)
    teacher_id = make_user(app, "teacher", UserRole.TEACHER)
    login(client, "teacher")
    client.post(
        "/requester/teacher/new",
        data=teacher_payload(school_class.id, reason="TOP SECRET REASON"),
    )
    with app.app_context():
        request_id = db.session.scalar(db.select(BookingRequest.id))
    client.post(
        f"/requester/requests/{request_id}/edit",
        data=teacher_payload(school_class.id, reason="NEW SECRET REASON"),
    )
    client.post(f"/requester/requests/{request_id}/cancel")
    with app.app_context():
        audits = db.session.scalars(db.select(AuditLog)).all()
        serialized = " ".join(str(item.details) for item in audits).lower()
        assert "secret reason" not in serialized
        assert "reason" not in serialized
        assert "csrf" not in serialized
        assert "subject" not in serialized
        assert teacher_id


@pytest.mark.parametrize("operation", ["edit", "cancel"])
def test_requester_deactivation_rejects_management_operation(client, app, operation):
    school_class = make_class(app, f"Deactivation {operation}")
    teacher_id = make_user(app, f"teacher-{operation}", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, school_class.id, 1)[0]
    login(client, f"teacher-{operation}")
    with app.app_context():
        db.session.get(User, teacher_id).is_active = False
        db.session.commit()
    if operation == "edit":
        response = client.post(
            f"/requester/requests/{request_id}/edit",
            data=teacher_payload(school_class.id, subject="Unauthorized"),
        )
    else:
        response = client.post(f"/requester/requests/{request_id}/cancel")
    assert response.status_code == 302
    assert "/auth/login" in response.location
    with app.app_context():
        record = db.session.get(BookingRequest, request_id)
        assert record.status == RequestStatus.PENDING
        assert record.subject == "Subject 0"
        assert (
            db.session.scalar(
                db.select(AuditLog).where(
                    AuditLog.action.in_(["REQUEST_EDITED", "REQUEST_CANCELLED"])
                )
            )
            is None
        )


def test_monitor_submission_rejects_inactive_assigned_class(client, app):
    school_class = make_class(app, "Inactive Assigned")
    teacher_id = make_user(app, "responsible-inactive", UserRole.TEACHER)
    make_user(app, "inactive-class-monitor", UserRole.MONITOR, school_class)
    with app.app_context():
        db.session.get(SchoolClass, school_class.id).is_active = False
        db.session.commit()
    login(client, "inactive-class-monitor")
    response = client.post("/requester/monitor/new", data=monitor_payload(teacher_id))
    assert b"assigned class is unavailable" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(BookingRequest)) is None
        assert (
            db.session.scalar(
                db.select(AuditLog).where(AuditLog.action == "REQUEST_CREATED")
            )
            is None
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("is_active", False),
        ("role", UserRole.SCHEDULER),
        ("must_change_password", True),
    ],
)
def test_lock_requester_rejects_stale_identity_map_state(app, column, value):
    from flask_login import login_user

    from app.blueprints.requester.forms import TeacherRequestForm
    from app.blueprints.requester.routes import (
        RequesterInvalidError,
        create_pending_request,
    )

    school_class = make_class(app, f"Stale {column}")
    teacher_id = make_user(app, f"stale-{column}", UserRole.TEACHER)
    with app.test_request_context("/requester/teacher/new", method="POST"):
        stale_user = db.session.get(User, teacher_id)
        login_user(stale_user)
        assert stale_user.is_active
        assert stale_user.role == UserRole.TEACHER
        assert not stale_user.must_change_password
        db.session.connection().execute(
            update(User).where(User.id == teacher_id).values({column: value})
        )
        form = TeacherRequestForm(
            data={
                "class_id": school_class.id,
                "subject": "Stale state",
                "reason": "Private",
            }
        )
        with pytest.raises(RequesterInvalidError):
            create_pending_request(form, UserRole.TEACHER)
        db.session.rollback()
        assert db.session.scalar(db.select(BookingRequest)) is None
        assert (
            db.session.scalar(
                db.select(AuditLog).where(AuditLog.action == "REQUEST_CREATED")
            )
            is None
        )


def test_monitor_creation_rejects_class_change_after_candidate_lookup(app, monkeypatch):
    from flask_login import login_user

    from app.blueprints.requester import routes as request_routes
    from app.blueprints.requester.forms import MonitorRequestForm

    first = make_class(app, "Stale Monitor Original")
    second = make_class(app, "Stale Monitor Replacement")
    teacher_id = make_user(app, "stale-responsible", UserRole.TEACHER)
    monitor_id = make_user(app, "stale-monitor-class", UserRole.MONITOR, first)
    with app.test_request_context("/requester/monitor/new", method="POST"):
        stale_monitor = db.session.get(User, monitor_id)
        login_user(stale_monitor)
        assert stale_monitor.class_id == first.id
        original_lock = request_routes.locked_active_class
        original_requester_lock = request_routes.lock_requester
        refreshed_class_ids = []

        def capture_refreshed_requester(role):
            requester = original_requester_lock(role)
            refreshed_class_ids.append(requester.class_id)
            return requester

        def change_assignment_after_candidate(class_id):
            locked_class = original_lock(class_id)
            db.session.connection().execute(
                update(User).where(User.id == monitor_id).values(class_id=second.id)
            )
            assert stale_monitor.class_id == first.id
            return locked_class

        monkeypatch.setattr(
            request_routes,
            "locked_active_class",
            change_assignment_after_candidate,
        )
        monkeypatch.setattr(
            request_routes, "lock_requester", capture_refreshed_requester
        )
        form = MonitorRequestForm(
            data={
                "teacher_id": teacher_id,
                "subject": "Stale monitor",
                "reason": "Private",
            }
        )
        assert not request_routes.create_pending_request(form, UserRole.MONITOR)
        assert refreshed_class_ids == [second.id]
        assert stale_monitor.class_id == first.id
        db.session.rollback()
        assert db.session.scalar(db.select(BookingRequest)) is None
        assert (
            db.session.scalar(
                db.select(AuditLog).where(AuditLog.action == "REQUEST_CREATED")
            )
            is None
        )


@pytest.mark.parametrize("origin", ["teacher", "monitor"])
def test_non_pending_to_pending_with_identity_change_is_rejected(app, origin):
    first = make_class(app, f"{origin} Original")
    second = make_class(app, f"{origin} Replacement")
    teacher_id = make_user(app, f"{origin}-teacher", UserRole.TEACHER)
    other_teacher_id = make_user(app, f"{origin}-other", UserRole.TEACHER)
    if origin == "teacher":
        requester_id = teacher_id
    else:
        requester_id = make_user(app, f"{origin}-monitor", UserRole.MONITOR, first)
    with app.app_context():
        record = BookingRequest(
            requester=db.session.get(User, requester_id),
            school_class=db.session.get(SchoolClass, first.id),
            teacher=db.session.get(User, teacher_id),
            subject="Original",
            reason="Private",
        )
        db.session.add(record)
        db.session.commit()
        record.status = RequestStatus.CANCELLED
        db.session.commit()
        request_id = record.id
        replacement_class = db.session.get(SchoolClass, second.id)
        replacement_teacher = db.session.get(User, other_teacher_id)
        record.status = RequestStatus.PENDING
        if origin == "teacher":
            record.school_class = replacement_class
        else:
            record.teacher = replacement_teacher
        with pytest.raises(ValueError, match="immutable"):
            db.session.commit()
        db.session.rollback()
        persisted = db.session.get(BookingRequest, request_id)
        assert persisted.status == RequestStatus.CANCELLED
        assert persisted.class_id == first.id
        assert persisted.teacher_id == teacher_id


def test_pending_status_and_identity_change_same_flush_is_rejected(app):
    first = make_class(app, "Pending Original")
    second = make_class(app, "Pending Replacement")
    teacher_id = make_user(app, "pending-teacher", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, first.id, 1)[0]
    with app.app_context():
        record = db.session.get(BookingRequest, request_id)
        record.status = RequestStatus.CANCELLED
        record.school_class = db.session.get(SchoolClass, second.id)
        with pytest.raises(ValueError, match="immutable"):
            db.session.commit()
        db.session.rollback()
        persisted = db.session.get(BookingRequest, request_id)
        assert persisted.status == RequestStatus.PENDING
        assert persisted.class_id == first.id


def test_request_origin_helper_rejects_unsupported_priority():
    from app.booking_queue import request_origin_role

    with pytest.raises(ValueError, match="Unsupported"):
        request_origin_role("UNSUPPORTED")


def test_audit_origin_uses_immutable_priority_after_role_change(app):
    from app.booking_queue import add_request_audit

    school_class = make_class(app, "Audit Origin Class")
    teacher_id = make_user(app, "origin-teacher", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, school_class.id, 1)[0]
    with app.app_context():
        requester = db.session.get(User, teacher_id)
        requester.role = UserRole.SCHEDULER
        db.session.commit()
        record = db.session.get(BookingRequest, request_id)
        add_request_audit(requester.id, "ORIGIN_AUDIT_TEST", record)
        db.session.commit()
        audit = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "ORIGIN_AUDIT_TEST")
        )
        assert audit.details["requester_role"] == UserRole.TEACHER.value


def test_scheduler_displays_immutable_origin_after_account_role_changes(
    client, app
):
    school_class = make_class(app, "Origin Display Class")
    teacher_id = make_user(app, "display-teacher", UserRole.TEACHER)
    responsible_id = make_user(app, "display-responsible", UserRole.TEACHER)
    monitor_id = make_user(
        app, "display-monitor", UserRole.MONITOR, school_class
    )
    make_user(app, "display-scheduler", UserRole.SCHEDULER)
    with app.app_context():
        school = db.session.get(SchoolClass, school_class.id)
        teacher_request = BookingRequest(
            requester=db.session.get(User, teacher_id),
            school_class=school,
            teacher=db.session.get(User, teacher_id),
            subject="Teacher Origin Subject",
            reason="Private",
        )
        monitor_request = BookingRequest(
            requester=db.session.get(User, monitor_id),
            school_class=school,
            teacher=db.session.get(User, responsible_id),
            subject="Monitor Origin Subject",
            reason="Private",
        )
        db.session.add_all([monitor_request, teacher_request])
        db.session.commit()
        teacher = db.session.get(User, teacher_id)
        monitor = db.session.get(User, monitor_id)
        teacher.role = UserRole.SCHEDULER
        monitor.class_id = None
        monitor.school_class = None
        monitor.role = UserRole.SCHEDULER
        db.session.commit()
    login(client, "display-scheduler")
    page = client.get("/scheduler/pending")
    assert page.status_code == 200
    teacher_position = page.data.index(b"Teacher Origin Subject")
    monitor_position = page.data.index(b"Monitor Origin Subject")
    assert teacher_position < monitor_position
    teacher_row = page.data[teacher_position - 500 : teacher_position + 500]
    monitor_row = page.data[monitor_position - 500 : monitor_position + 500]
    assert b"Teacher" in teacher_row
    assert b"Class Monitor" in monitor_row

def test_lock_settings_refreshes_all_stale_queue_values(app):
    from app.booking_queue import lock_settings

    with app.app_context():
        stale_settings = db.session.get(SystemSettings, 1)
        assert not stale_settings.booking_queue_locked
        assert stale_settings.max_pending_requests == 12
        assert stale_settings.reopen_threshold == 9
        db.session.connection().execute(
            update(SystemSettings)
            .where(SystemSettings.id == 1)
            .values(
                booking_queue_locked=True,
                max_pending_requests=20,
                reopen_threshold=5,
            )
        )
        assert not stale_settings.booking_queue_locked
        refreshed = lock_settings()
        assert refreshed is stale_settings
        assert refreshed.booking_queue_locked
        assert refreshed.max_pending_requests == 20
        assert refreshed.reopen_threshold == 5
        db.session.rollback()


def test_stale_cached_settings_cannot_bypass_persistent_queue_lock(app):
    from flask_login import login_user

    from app.blueprints.requester.forms import TeacherRequestForm
    from app.blueprints.requester.routes import create_pending_request
    from app.booking_queue import QueueLockedError

    school_class = make_class(app, "Stale Settings Class")
    teacher_id = make_user(app, "stale-settings-teacher", UserRole.TEACHER)
    add_pending_requests(app, teacher_id, school_class.id, 10)
    with app.test_request_context("/requester/teacher/new", method="POST"):
        teacher = db.session.get(User, teacher_id)
        login_user(teacher)
        stale_settings = db.session.get(SystemSettings, 1)
        assert not stale_settings.booking_queue_locked
        db.session.connection().execute(
            update(SystemSettings)
            .where(SystemSettings.id == 1)
            .values(booking_queue_locked=True)
        )
        assert not stale_settings.booking_queue_locked
        form = TeacherRequestForm(
            data={
                "class_id": school_class.id,
                "subject": "Must be rejected",
                "reason": "Private",
            }
        )
        with pytest.raises(QueueLockedError):
            create_pending_request(form, UserRole.TEACHER)
        assert stale_settings.booking_queue_locked
        assert pending_count_for_test() == 10
        assert db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "REQUEST_CREATED")
        ) is None
        db.session.rollback()


def test_missing_settings_hides_new_request_form(client, app):
    school_class = make_class(app, "Missing Settings Form")
    make_user(app, "missing-settings-teacher", UserRole.TEACHER)
    with app.app_context():
        db.session.delete(db.session.get(SystemSettings, 1))
        db.session.commit()
    login(client, "missing-settings-teacher")
    page = client.get("/requester/teacher/new")
    assert page.status_code == 200
    assert b"Unable to save the request" in page.data
    assert b'<form method="post" class="card card-body">' not in page.data
    assert school_class.id


def test_locked_queue_hides_creation_but_rejects_crafted_post(client, app):
    school_class = make_class(app, "Locked Form Class")
    make_user(app, "locked-form-teacher", UserRole.TEACHER)
    with app.app_context():
        db.session.get(SystemSettings, 1).booking_queue_locked = True
        db.session.commit()
    login(client, "locked-form-teacher")
    page = client.get("/requester/teacher/new")
    assert b"Temporarily locked" in page.data
    assert b'<form method="post" class="card card-body">' not in page.data
    crafted = client.post(
        "/requester/teacher/new", data=teacher_payload(school_class.id)
    )
    assert b"temporarily locked" in crafted.data
    with app.app_context():
        assert db.session.scalar(db.select(BookingRequest)) is None
        assert db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "REQUEST_CREATED")
        ) is None


def test_edit_form_remains_available_when_queue_locked(client, app):
    school_class = make_class(app, "Locked Edit Class")
    teacher_id = make_user(app, "locked-edit-teacher", UserRole.TEACHER)
    request_id = add_pending_requests(app, teacher_id, school_class.id, 1)[0]
    with app.app_context():
        db.session.get(SystemSettings, 1).booking_queue_locked = True
        db.session.commit()
    login(client, "locked-edit-teacher")
    page = client.get(f"/requester/requests/{request_id}/edit")
    assert page.status_code == 200
    assert b"<form" in page.data
    assert b"Edit booking request" in page.data
