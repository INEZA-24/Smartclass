"""Milestone 8 notification center and read-state tests."""

import re
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import event, update
from sqlalchemy.exc import SQLAlchemyError
from test_schedule_changes import (
    PASSWORD,
    login,
    scheduled,
    seed,
)

from app.extensions import db
from app.models import (
    AuditLog,
    BlockScope,
    BookingRequest,
    Notification,
    NotificationType,
    RoomBlock,
    SystemSettings,
    User,
    UserRole,
)
from app.models.core import UTCDateTime
from app.notifications import (
    NotificationNotFoundError,
    NotificationUpdateError,
    mark_all_notifications_read,
    mark_notification_read,
)


@pytest.fixture(autouse=True)
def notification_database(app):
    with app.app_context():
        db.create_all()
        db.session.add(SystemSettings(id=1))
        db.session.commit()
        yield
        db.session.remove()
        db.drop_all()


def add_notification(user_id, title, *, unread=True, created_at=None, request_id=None):
    record = Notification(
        user_id=user_id,
        type=NotificationType.SYSTEM,
        title=title,
        message=f"Message for {title}",
        booking_request_id=request_id,
        is_read=not unread,
        created_at=created_at or datetime.now(UTC),
        read_at=None if unread else datetime.now(UTC) - timedelta(hours=1),
    )
    db.session.add(record)
    db.session.commit()
    return record.id


def csrf_token(response):
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def csrf_login(client, username):
    page = client.get("/auth/login")
    return client.post(
        "/auth/login",
        data={
            "username": username,
            "password": PASSWORD,
            "csrf_token": csrf_token(page),
        },
    )


@pytest.mark.parametrize("role_name", ["admin", "scheduler", "teacher", "monitor"])
def test_every_authenticated_role_can_open_own_notifications(client, app, role_name):
    ids = seed(app)
    with app.app_context():
        add_notification(ids[role_name], f"{role_name} only")
    login(client, role_name)
    response = client.get("/notifications/")
    assert response.status_code == 200
    assert f"{role_name} only".encode() in response.data


def test_notification_center_requires_login(client, app):
    seed(app)
    response = client.get("/notifications/")
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_listing_and_unread_badge_are_owner_scoped_and_ordered(client, app):
    ids = seed(app)
    moment = datetime.now(UTC)
    with app.app_context():
        older = add_notification(ids["teacher"], "Older", created_at=moment)
        newer = add_notification(ids["teacher"], "Newer", created_at=moment)
        add_notification(ids["teacher"], "Already read", unread=False)
        add_notification(ids["monitor"], "Another user's private title")
        assert newer > older
    login(client, "teacher")
    response = client.get("/notifications/")
    assert response.data.index(b"Newer") < response.data.index(b"Older")
    assert b"Another user's private title" not in response.data
    assert b"2 unread" in response.data
    assert b">2</span>" in response.data
    assert b"Unread notification" in response.data


def test_empty_notification_center(client, app):
    seed(app)
    login(client, "admin")
    response = client.get("/notifications/")
    assert b"You have no notifications." in response.data


def test_related_request_link_only_for_owner(client, app):
    ids = seed(app)
    with app.app_context():
        add_notification(
            ids["teacher"], "Related", request_id=ids["request"]
        )
        add_notification(
            ids["monitor"], "Not yours", request_id=ids["request"]
        )
    login(client, "teacher")
    response = client.get("/notifications/")
    assert b"View related request" in response.data
    assert b"Not yours" not in response.data


def test_mark_one_service_is_owned_idempotent_and_transactional(app):
    ids = seed(app)
    with app.app_context():
        notification_id = add_notification(ids["teacher"], "Owned")
        assert mark_notification_read(notification_id, ids["teacher"])
        record = db.session.get(Notification, notification_id)
        first_read_at = record.read_at
        assert record.is_read and first_read_at is not None
        assert first_read_at.utcoffset() == timedelta(0)
        db.session.rollback()
        assert not mark_notification_read(notification_id, ids["teacher"])
        assert not db.session().in_transaction()
        assert db.session.get(Notification, notification_id).read_at == first_read_at
        with pytest.raises(NotificationNotFoundError):
            mark_notification_read(notification_id, ids["monitor"])
        assert not db.session().in_transaction()


def test_mark_one_commit_failure_rolls_back(app):
    ids = seed(app)
    with app.app_context():
        notification_id = add_notification(ids["teacher"], "Owned")
        with (
            patch.object(
                db.session, "commit", side_effect=SQLAlchemyError("forced")
            ),
            pytest.raises(NotificationUpdateError),
        ):
            mark_notification_read(notification_id, ids["teacher"])
        assert not db.session().in_transaction()
        record = db.session.get(Notification, notification_id)
        assert not record.is_read and record.read_at is None


def test_mark_all_updates_only_owned_unread_with_shared_timestamp(app):
    ids = seed(app)
    with app.app_context():
        first = add_notification(ids["teacher"], "First")
        second = add_notification(ids["teacher"], "Second")
        already = add_notification(ids["teacher"], "Read", unread=False)
        other = add_notification(ids["monitor"], "Other")
        old_read_at = db.session.get(Notification, already).read_at
        db.session.rollback()
        assert mark_all_notifications_read(ids["teacher"]) == 2
        first_record = db.session.get(Notification, first)
        second_record = db.session.get(Notification, second)
        assert first_record.read_at == second_record.read_at
        assert first_record.read_at.utcoffset() == timedelta(0)
        assert db.session.get(Notification, already).read_at == old_read_at
        assert not db.session.get(Notification, other).is_read
        db.session.rollback()
        assert mark_all_notifications_read(ids["teacher"]) == 0


def test_mark_all_commit_failure_rolls_back_every_record(app):
    ids = seed(app)
    with app.app_context():
        first = add_notification(ids["teacher"], "First")
        second = add_notification(ids["teacher"], "Second")
        with (
            patch.object(
                db.session, "commit", side_effect=SQLAlchemyError("forced")
            ),
            pytest.raises(NotificationUpdateError),
        ):
            mark_all_notifications_read(ids["teacher"])
        assert not db.session().in_transaction()
        assert not db.session.get(Notification, first).is_read
        assert not db.session.get(Notification, second).is_read


def test_mark_routes_are_post_only_csrf_protected_and_owner_scoped(client, app):
    ids = seed(app)
    with app.app_context():
        notification_id = add_notification(ids["teacher"], "Owned")
        other_id = add_notification(ids["monitor"], "Other")
    app.config["WTF_CSRF_ENABLED"] = True
    assert csrf_login(client, "teacher").status_code == 302
    one_url = f"/notifications/{notification_id}/read"
    assert client.get(one_url).status_code == 405
    assert client.post(one_url).status_code == 400
    page = client.get("/notifications/")
    token = csrf_token(page)
    assert client.post(one_url, data={"csrf_token": token}).status_code == 302
    assert client.post(
        f"/notifications/{other_id}/read", data={"csrf_token": token}
    ).status_code == 404
    assert client.get("/notifications/read-all").status_code == 405


def test_mark_all_route_with_csrf_updates_badge(client, app):
    ids = seed(app)
    with app.app_context():
        add_notification(ids["teacher"], "One")
        add_notification(ids["teacher"], "Two")
    app.config["WTF_CSRF_ENABLED"] = True
    csrf_login(client, "teacher")
    page = client.get("/notifications/")
    assert b"2 unread" in page.data
    assert client.post("/notifications/read-all").status_code == 400
    response = client.post(
        "/notifications/read-all",
        data={"csrf_token": csrf_token(page)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"0 unread" in response.data


def test_forced_password_user_is_restricted_from_notifications(client, app):
    ids = seed(app)
    with app.app_context():
        user = db.session.get(User, ids["teacher"])
        user.must_change_password = True
        db.session.commit()
    login(client, "teacher")
    response = client.get("/notifications/")
    assert response.status_code == 302
    assert "/auth/change-password" in response.location


@pytest.mark.parametrize("notification_id", [None, True, False, 0, -1, "1", 1.0])
def test_mark_one_rejects_malformed_notification_identifier(app, notification_id):
    ids = seed(app)
    with app.app_context():
        with pytest.raises(NotificationNotFoundError):
            mark_notification_read(notification_id, ids["teacher"])
        assert not db.session().in_transaction()


@pytest.mark.parametrize("user_id", [None, True, False, 0, -1, "1", 1.0])
def test_mark_one_rejects_malformed_user_identifier(app, user_id):
    ids = seed(app)
    with app.app_context():
        notification_id = add_notification(ids["teacher"], "Owned")
        with pytest.raises(NotificationUpdateError, match="Unable to update"):
            mark_notification_read(notification_id, user_id)
        assert not db.session().in_transaction()
        assert not db.session.get(Notification, notification_id).is_read


@pytest.mark.parametrize("user_id", [None, True, False, 0, -1, "1", 1.0])
def test_mark_all_rejects_malformed_user_identifier(app, user_id):
    ids = seed(app)
    with app.app_context():
        notification_id = add_notification(ids["teacher"], "Owned")
        with pytest.raises(NotificationUpdateError, match="Unable to update"):
            mark_all_notifications_read(user_id)
        assert not db.session().in_transaction()
        assert not db.session.get(Notification, notification_id).is_read


@pytest.mark.parametrize("endpoint", ["center", "one", "all"])
def test_independently_deactivated_user_cannot_access_or_mutate(
    client, app, endpoint
):
    ids = seed(app)
    with app.app_context():
        notification_id = add_notification(ids["teacher"], "Owned")
    login(client, "teacher")
    with app.app_context(), db.engine.begin() as connection:
        connection.execute(
            update(User).where(User.id == ids["teacher"]).values(is_active=False)
        )
    if endpoint == "center":
        response = client.get("/notifications/")
    elif endpoint == "one":
        response = client.post(f"/notifications/{notification_id}/read")
    else:
        response = client.post("/notifications/read-all")
    assert response.status_code == 302
    assert "/auth/login" in response.location
    with app.app_context():
        record = db.session.get(Notification, notification_id)
        assert not record.is_read and record.read_at is None


def test_role_change_does_not_expose_another_users_notifications(client, app):
    ids = seed(app)
    with app.app_context():
        add_notification(ids["teacher"], "Teacher owned")
        add_notification(ids["admin"], "Administrator private")
    login(client, "teacher")
    with app.app_context(), db.engine.begin() as connection:
        connection.execute(
            update(User).where(User.id == ids["teacher"]).values(role=UserRole.ADMIN)
        )
    response = client.get("/notifications/")
    assert b"Teacher owned" in response.data
    assert b"Administrator private" not in response.data


def test_anonymous_rendering_skips_unread_count_query(client, app):
    seed(app)
    with patch.object(db.session, "scalar", wraps=db.session.scalar) as scalar:
        home = client.get("/")
        login_page = client.get("/auth/login")
    assert scalar.call_count == 0
    for response in (home, login_page):
        assert b"notification_unread_count" not in response.data
        assert b"private notification" not in response.data


def test_authenticated_rendering_runs_one_owner_scoped_unread_query(client, app):
    ids = seed(app)
    with app.app_context():
        add_notification(ids["teacher"], "Owned")
    login(client, "teacher")
    with patch.object(db.session, "scalar", wraps=db.session.scalar) as scalar:
        response = client.get("/requester/teacher")
    assert response.status_code == 200
    assert scalar.call_count == 1


def test_notification_listing_has_bounded_relationship_queries(client, app):
    ids = scheduled(app)
    with app.app_context():
        for number in range(5):
            add_notification(
                ids["teacher"],
                f"Related {number}",
                request_id=ids["request"],
            )
    login(client, "teacher")
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            response = client.get("/notifications/")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert len(statements) <= 6


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (datetime(2026, 1, 2, 3, 4), datetime(2026, 1, 2, 3, 4, tzinfo=UTC)),
        (
            datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
            datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        ),
        (
            datetime(2026, 1, 2, 5, 4, tzinfo=timezone(timedelta(hours=2))),
            datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        ),
    ],
)
def test_utc_datetime_normalizes_bind_and_result_values(value, expected):
    column_type = UTCDateTime()
    assert column_type.process_bind_param(value, None) == expected
    result = column_type.process_result_value(value, None)
    assert result == expected
    if result is not None:
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)


@pytest.mark.parametrize("notification_id", ["not-an-id", "-1", "0"])
def test_malformed_mark_one_route_is_safe_404(client, app, notification_id):
    ids = seed(app)
    with app.app_context():
        owned_id = add_notification(ids["teacher"], "Owned")
    login(client, "teacher")
    response = client.post(f"/notifications/{notification_id}/read")
    assert response.status_code == 404
    with app.app_context():
        assert not db.session.get(Notification, owned_id).is_read


@pytest.mark.parametrize("operation", ["one", "all"])
def test_route_persistence_failure_shows_only_safe_message(
    client, app, operation
):
    ids = seed(app)
    with app.app_context():
        notification_id = add_notification(ids["teacher"], "Owned")
    login(client, "teacher")
    error = NotificationUpdateError(
        "Unable to update notifications. Please try again."
    )
    target = (
        "app.blueprints.notifications.routes.mark_notification_read"
        if operation == "one"
        else "app.blueprints.notifications.routes.mark_all_notifications_read"
    )
    url = (
        f"/notifications/{notification_id}/read"
        if operation == "one"
        else "/notifications/read-all"
    )
    with patch(target, side_effect=error):
        response = client.post(url, follow_redirects=True)
    assert b"Unable to update notifications. Please try again." in response.data
    assert b"forced persistence failure" not in response.data


def test_notification_interface_read_states_and_csrf_forms(client, app):
    ids = seed(app)
    with app.app_context():
        unread_id = add_notification(ids["teacher"], "Unread item")
        read_id = add_notification(ids["teacher"], "Read item", unread=False)
    app.config["WTF_CSRF_ENABLED"] = True
    csrf_login(client, "teacher")
    response = client.get("/notifications/")
    assert b"Unread notification: Unread item" in response.data
    assert b"Read notification: Read item" in response.data
    assert f"/notifications/{unread_id}/read".encode() in response.data
    assert f"/notifications/{read_id}/read".encode() not in response.data
    assert re.search(
        rb'action="/notifications/read-all"[^>]*>.*?name="csrf_token"',
        response.data,
        re.DOTALL,
    )
    assert re.search(
        rf'action="/notifications/{unread_id}/read"[^>]*>.*?name="csrf_token"'.encode(),
        response.data,
        re.DOTALL,
    )
    assert b"Mark all as read" in response.data


@pytest.mark.parametrize(
    ("role_name", "expected_path"),
    [
        ("teacher", "/requester/teacher/requests"),
        ("monitor", "/requester/monitor/requests"),
        ("scheduler", "/scheduler/bookings/"),
        ("admin", "/scheduler/bookings/"),
    ],
)
def test_related_links_follow_existing_role_authorization(
    client, app, role_name, expected_path
):
    ids = scheduled(app) if role_name in {"scheduler", "admin"} else seed(app)
    with app.app_context():
        if role_name == "monitor":
            monitor = db.session.get(User, ids["monitor"])
            request = BookingRequest(
                requester=monitor,
                teacher=db.session.get(User, ids["teacher"]),
                school_class=monitor.school_class,
                subject="Monitor subject",
                reason="Monitor private reason",
            )
            db.session.add(request)
            db.session.commit()
            request_id = request.id
        else:
            request_id = ids["request"]
        add_notification(
            ids[role_name],
            f"{role_name} related",
            request_id=request_id,
        )
    login(client, role_name)
    response = client.get("/notifications/")
    assert expected_path.encode() in response.data


def test_unsafe_or_missing_related_request_has_no_link(client, app):
    ids = seed(app)
    with app.app_context():
        add_notification(ids["teacher"], "Missing relation")
        add_notification(
            ids["monitor"],
            "Other request relation",
            request_id=ids["request"],
        )
    login(client, "monitor")
    response = client.get("/notifications/?user_id=teacher")
    assert b"Other request relation" in response.data
    assert b"View related request" not in response.data
    assert b"Missing relation" not in response.data


def test_notification_center_does_not_dereference_private_records(client, app):
    ids = seed(app)
    sentinels = {
        "reason": "PRIVATE-REQUEST-REASON",
        "subject": "PRIVATE-SUBJECT",
        "rejection": "INTENTIONAL-REJECTION-MESSAGE",
        "block": "PRIVATE-BLOCK-REASON",
        "audit": "PRIVATE-AUDIT-DETAIL",
        "csrf": "csrf_token=PRIVATE-CSRF",
        "payload": '{"subject":"COMPLETE-FORM-PAYLOAD"}',
    }
    with app.app_context():
        request = db.session.get(BookingRequest, ids["request"])
        request.reason = sentinels["reason"]
        request.subject = sentinels["subject"]
        request.rejection_reason = sentinels["rejection"]
        db.session.add(
            RoomBlock(
                block_date=datetime.now(UTC).date(),
                scope=BlockScope.DAY,
                reason=sentinels["block"],
                created_by_id=ids["scheduler"],
            )
        )
        db.session.add(
            AuditLog(
                actor_id=ids["admin"],
                action="PRIVATE_M8_TEST",
                entity_type="Test",
                details={
                    "audit": sentinels["audit"],
                    "csrf": sentinels["csrf"],
                    "payload": sentinels["payload"],
                },
            )
        )
        db.session.commit()
        add_notification(
            ids["teacher"],
            "Approved public notification title",
            request_id=request.id,
        )
        rejected = Notification(
            user_id=ids["teacher"],
            type=NotificationType.REJECTED,
            title="Rejected notification",
            message=f"Request rejected: {sentinels['rejection']}",
            booking_request_id=request.id,
        )
        other = Notification(
            user_id=ids["monitor"],
            type=NotificationType.SYSTEM,
            title="OTHER-USER-TITLE",
            message="OTHER-USER-MESSAGE",
        )
        db.session.add_all([rejected, other])
        db.session.commit()
    login(client, "teacher")
    response = client.get("/notifications/")
    assert b"Approved public notification title" in response.data
    assert sentinels["rejection"].encode() in response.data
    for key in ("reason", "subject", "block", "audit", "csrf", "payload"):
        assert sentinels[key].encode() not in response.data
    assert b"OTHER-USER-TITLE" not in response.data
    assert b"OTHER-USER-MESSAGE" not in response.data
