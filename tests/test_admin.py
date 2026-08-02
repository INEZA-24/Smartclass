"""Milestone 4 Administrator management tests."""

import pytest
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import AuditLog, Room, SchoolClass, User, UserRole

PASSWORD = "TemporaryPass123!"
RESET_PASSWORD = "ReplacementPass456!"


@pytest.fixture(autouse=True)
def database(app):
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


def add_user(app, username, role, *, active=True, forced=False, school_class=None):
    with app.app_context():
        user = User(
            username=username,
            full_name=f"{username.title()} User",
            password_hash="pending",
            role=role,
            is_active=active,
            must_change_password=forced,
            school_class=school_class,
        )
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.commit()
        return user.id


def login(client, username):
    return client.post(
        "/auth/login", data={"username": username, "password": PASSWORD}
    )


@pytest.fixture()
def administrator(app, client):
    user_id = add_user(app, "admin", UserRole.ADMIN)
    login(client, "admin")
    return user_id


def user_data(role, **overrides):
    data = {
        "full_name": "Managed User",
        "username": f"new-{role.value.lower()}",
        "role": role.value,
        "class_id": 0,
        "temporary_password": PASSWORD,
        "confirm_password": PASSWORD,
        "is_active": "y",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "path", ["/admin/", "/admin/users", "/admin/classes", "/admin/rooms"]
)
def test_admin_pages_require_login(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert "/auth/login" in response.location


@pytest.mark.parametrize(
    "role", [UserRole.SCHEDULER, UserRole.TEACHER, UserRole.MONITOR]
)
def test_non_admin_roles_receive_403(client, app, role):
    school_class = None
    if role == UserRole.MONITOR:
        with app.app_context():
            school_class = SchoolClass(name="S1 A")
            db.session.add(school_class)
            db.session.commit()
            db.session.expunge(school_class)
    add_user(app, role.value.lower(), role, school_class=school_class)
    login(client, role.value.lower())
    assert client.get("/admin/users").status_code == 403


def test_forced_password_admin_cannot_bypass_change(client, app):
    add_user(app, "admin", UserRole.ADMIN, forced=True)
    login(client, "admin")
    assert client.get("/admin/users").location.endswith("/auth/change-password")


def test_admin_dashboard_and_management_pages_load(client, administrator):
    for path in ("/admin/", "/admin/users", "/admin/classes", "/admin/rooms"):
        assert client.get(path).status_code == 200


@pytest.mark.parametrize("role", list(UserRole))
def test_admin_can_create_each_supported_role(client, app, administrator, role):
    class_id = 0
    if role == UserRole.MONITOR:
        with app.app_context():
            school_class = SchoolClass(name="S2 A")
            db.session.add(school_class)
            db.session.commit()
            class_id = school_class.id
    response = client.post(
        "/admin/users/new", data=user_data(role, class_id=class_id)
    )
    assert response.status_code == 302
    with app.app_context():
        user = db.session.scalar(
            db.select(User).where(User.username == f"new-{role.value.lower()}")
        )
        assert user is not None
        assert user.must_change_password
        assert user.check_password(PASSWORD)
        assert user.password_hash != PASSWORD
        assert user.class_id == (class_id or None)


def test_username_is_normalized_unique_and_blank_rejected(
    client, app, administrator
):
    client.post(
        "/admin/users/new",
        data=user_data(UserRole.TEACHER, username="  Normalized  "),
    )
    duplicate = client.post(
        "/admin/users/new",
        data=user_data(UserRole.TEACHER, username="normalized"),
    )
    blank = client.post(
        "/admin/users/new", data=user_data(UserRole.TEACHER, username="   ")
    )
    assert b"already in use" in duplicate.data
    assert b"This field is required." in blank.data
    with app.app_context():
        assert db.session.scalar(
            db.select(db.func.count(User.id)).where(User.username == "normalized")
        ) == 1


def test_monitor_requires_active_class(client, app, administrator):
    with app.app_context():
        inactive = SchoolClass(name="Inactive", is_active=False)
        db.session.add(inactive)
        db.session.commit()
        inactive_id = inactive.id
    response = client.post(
        "/admin/users/new",
        data=user_data(UserRole.MONITOR, class_id=inactive_id),
    )
    assert b"active class is required" in response.data
    assert b"Inactive</option>" not in client.get("/admin/users/new").data
    with app.app_context():
        assert db.session.scalar(
            db.select(User).where(User.username == "new-monitor")
        ) is None


def test_changing_monitor_role_clears_class(client, app, administrator):
    with app.app_context():
        school_class = SchoolClass(name="S3 A")
        db.session.add(school_class)
        db.session.commit()
        db.session.expunge(school_class)
    user_id = add_user(
        app, "monitor", UserRole.MONITOR, school_class=school_class
    )
    response = client.post(
        f"/admin/users/{user_id}/edit",
        data={
            "full_name": "Former Monitor",
            "username": "monitor",
            "role": UserRole.TEACHER.value,
            "class_id": 0,
            "is_active": "y",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.role == UserRole.TEACHER
        assert user.class_id is None


def test_activation_actions_require_post_and_csrf(client, app, administrator):
    user_id = add_user(app, "teacher", UserRole.TEACHER)
    assert client.get(f"/admin/users/{user_id}/deactivate").status_code == 405
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post(f"/admin/users/{user_id}/deactivate").status_code == 400


def test_admin_cannot_deactivate_self_or_remove_own_role(
    client, app, administrator
):
    client.post(f"/admin/users/{administrator}/deactivate")
    response = client.post(
        f"/admin/users/{administrator}/edit",
        data={
            "full_name": "Admin User",
            "username": "admin",
            "role": UserRole.TEACHER.value,
            "class_id": 0,
            "is_active": "y",
        },
    )
    assert b"cannot remove your own Administrator role" in response.data
    with app.app_context():
        admin = db.session.get(User, administrator)
        assert admin.is_active
        assert admin.role == UserRole.ADMIN


def test_last_active_admin_remains_active(client, app, administrator):
    client.post(f"/admin/users/{administrator}/deactivate")
    with app.app_context():
        active_admins = db.session.scalar(
            db.select(db.func.count(User.id)).where(
                User.role == UserRole.ADMIN, User.is_active.is_(True)
            )
        )
        assert active_admins == 1


def test_temporary_password_reset_is_safe_and_audited(
    client, app, administrator
):
    user_id = add_user(app, "teacher", UserRole.TEACHER)
    with app.app_context():
        original_hash = db.session.get(User, user_id).password_hash
    app.config["WTF_CSRF_ENABLED"] = True
    page = client.get(f"/admin/users/{user_id}/temporary-password")
    confirmation = page.data.split(b"data-confirm-message=", 1)[1].split(b">", 1)[0]
    assert b"Reset this user" in confirmation
    assert RESET_PASSWORD.encode() not in confirmation
    assert b'method="post"' in page.data
    assert b'name="csrf_token"' in page.data
    with app.app_context():
        assert db.session.get(User, user_id).password_hash == original_hash
    app.config["WTF_CSRF_ENABLED"] = False
    response = client.post(
        f"/admin/users/{user_id}/temporary-password",
        data={
            "temporary_password": RESET_PASSWORD,
            "confirm_password": RESET_PASSWORD,
        },
    )
    assert response.status_code == 302
    assert client.get(
        f"/admin/users/{administrator}/temporary-password"
    ).status_code == 403
    with app.app_context():
        user = db.session.get(User, user_id)
        audit = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "USER_PASSWORD_RESET")
        )
        assert user.check_password(RESET_PASSWORD)
        assert user.must_change_password
        audit_text = str(audit.details).lower()
        assert "password" not in audit_text
        assert "hash" not in audit_text


@pytest.mark.parametrize(
    ("kind", "base", "model"),
    [("class", "/admin/classes", SchoolClass), ("room", "/admin/rooms", Room)],
)
def test_admin_can_create_edit_and_toggle_named_records(
    client, app, administrator, kind, base, model
):
    client.post(f"{base}/new", data={"name": "  New   Name  ", "is_active": "y"})
    with app.app_context():
        record = db.session.scalar(db.select(model).where(model.name == "New Name"))
        record_id = record.id
    client.post(
        f"{base}/{record_id}/edit",
        data={"name": "Edited Name", "is_active": "y"},
    )
    assert client.get(f"{base}/{record_id}/deactivate").status_code == 405
    client.post(f"{base}/{record_id}/deactivate")
    with app.app_context():
        assert not db.session.get(model, record_id).is_active
    client.post(f"{base}/{record_id}/activate")
    with app.app_context():
        assert db.session.get(model, record_id).is_active


@pytest.mark.parametrize(
    ("base", "model"),
    [("/admin/classes", SchoolClass), ("/admin/rooms", Room)],
)
def test_named_records_reject_duplicate_and_blank(
    client, app, administrator, base, model
):
    client.post(f"{base}/new", data={"name": "Existing", "is_active": "y"})
    duplicate = client.post(
        f"{base}/new", data={"name": " existing ", "is_active": "y"}
    )
    blank = client.post(f"{base}/new", data={"name": "   ", "is_active": "y"})
    assert b"already exists" in duplicate.data
    assert b"This field is required." in blank.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(model.id))) == 1


def test_class_with_active_monitor_cannot_be_deactivated(
    client, app, administrator
):
    with app.app_context():
        school_class = SchoolClass(name="Assigned")
        db.session.add(school_class)
        db.session.commit()
        class_id = school_class.id
        db.session.expunge(school_class)
    add_user(app, "monitor", UserRole.MONITOR, school_class=school_class)
    response = client.post(
        f"/admin/classes/{class_id}/deactivate", follow_redirects=True
    )
    assert b"cannot be deactivated" in response.data
    with app.app_context():
        assert db.session.get(SchoolClass, class_id).is_active


def test_monitor_activation_rejected_after_class_is_deactivated(
    client, app, administrator
):
    with app.app_context():
        school_class = SchoolClass(name="Later Inactive")
        db.session.add(school_class)
        db.session.commit()
        class_id = school_class.id
        db.session.expunge(school_class)
    monitor_id = add_user(
        app,
        "inactive-monitor",
        UserRole.MONITOR,
        active=False,
        school_class=school_class,
    )

    class_response = client.post(f"/admin/classes/{class_id}/deactivate")
    activation_response = client.post(
        f"/admin/users/{monitor_id}/activate", follow_redirects=True
    )

    assert class_response.status_code == 302
    assert b"cannot be activated without an active assigned class" in (
        activation_response.data
    )
    with app.app_context():
        monitor = db.session.get(User, monitor_id)
        assert not monitor.is_active
        assert not db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.action == "USER_ACTIVATED",
                AuditLog.entity_id == monitor_id,
            )
        ).all()


def test_role_conversion_to_monitor_rejects_inactive_class(
    client, app, administrator
):
    user_id = add_user(app, "teacher-to-monitor", UserRole.TEACHER)
    with app.app_context():
        inactive = SchoolClass(name="Conversion Inactive", is_active=False)
        db.session.add(inactive)
        db.session.commit()
        class_id = inactive.id

    response = client.post(
        f"/admin/users/{user_id}/edit",
        data={
            "full_name": "Teacher To Monitor",
            "username": "teacher-to-monitor",
            "role": UserRole.MONITOR.value,
            "class_id": class_id,
            "is_active": "y",
        },
    )

    assert b"active class is required" in response.data
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.role == UserRole.TEACHER
        assert user.class_id is None


def test_active_admin_can_edit_an_already_inactive_admin(
    client, app, administrator
):
    inactive_admin_id = add_user(
        app, "inactive-admin", UserRole.ADMIN, active=False
    )

    response = client.post(
        f"/admin/users/{inactive_admin_id}/edit",
        data={
            "full_name": "Former Administrator",
            "username": "inactive-admin",
            "role": UserRole.TEACHER.value,
            "class_id": 0,
        },
    )

    assert response.status_code == 302
    with app.app_context():
        active_admin = db.session.get(User, administrator)
        managed_user = db.session.get(User, inactive_admin_id)
        audit = db.session.scalar(
            db.select(AuditLog).where(
                AuditLog.action == "USER_EDITED",
                AuditLog.entity_id == inactive_admin_id,
            )
        )
        assert active_admin.is_active
        assert active_admin.role == UserRole.ADMIN
        assert not managed_user.is_active
        assert managed_user.role == UserRole.TEACHER
        assert managed_user.full_name == "Former Administrator"
        assert audit is not None


def test_class_edit_preserves_active_status_and_creates_no_status_audit(
    client, app, administrator
):
    with app.app_context():
        school_class = SchoolClass(name="Assigned Active Class")
        db.session.add(school_class)
        db.session.commit()
        class_id = school_class.id
        db.session.expunge(school_class)
    add_user(app, "assigned-monitor", UserRole.MONITOR, school_class=school_class)

    first = client.post(
        f"/admin/classes/{class_id}/edit", data={"name": "Renamed Class"}
    )
    crafted = client.post(
        f"/admin/classes/{class_id}/edit",
        data={"name": "Final Class Name", "is_active": ""},
    )

    assert first.status_code == 302
    assert crafted.status_code == 302
    with app.app_context():
        record = db.session.get(SchoolClass, class_id)
        status_audits = db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.entity_id == class_id,
                AuditLog.action.in_(["CLASS_ACTIVATED", "CLASS_DEACTIVATED"]),
            )
        ).all()
        assert record.name == "Final Class Name"
        assert record.is_active
        assert status_audits == []


def test_room_edit_preserves_active_status_and_creates_no_status_audit(
    client, app, administrator
):
    with app.app_context():
        room = Room(name="Active Room")
        db.session.add(room)
        db.session.commit()
        room_id = room.id

    response = client.post(
        f"/admin/rooms/{room_id}/edit",
        data={"name": "Renamed Active Room", "is_active": ""},
    )

    assert response.status_code == 302
    with app.app_context():
        record = db.session.get(Room, room_id)
        status_audits = db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.entity_id == room_id,
                AuditLog.action.in_(["ROOM_ACTIVATED", "ROOM_DEACTIVATED"]),
            )
        ).all()
        assert record.name == "Renamed Active Room"
        assert record.is_active
        assert status_audits == []


@pytest.mark.parametrize(
    ("original_status", "submitted_status", "expected_action"),
    [
        (False, True, "USER_ACTIVATED"),
        (True, False, "USER_DEACTIVATED"),
        (True, True, None),
    ],
)
def test_user_edit_audits_status_only_when_changed(
    client,
    app,
    administrator,
    original_status,
    submitted_status,
    expected_action,
):
    user_id = add_user(
        app, f"status-{original_status}-{submitted_status}", UserRole.TEACHER,
        active=original_status,
    )
    data = {
        "full_name": "Status Managed User",
        "username": f"status-{original_status}-{submitted_status}",
        "role": UserRole.TEACHER.value,
        "class_id": 0,
    }
    if submitted_status:
        data["is_active"] = "y"

    response = client.post(f"/admin/users/{user_id}/edit", data=data)

    assert response.status_code == 302
    with app.app_context():
        actions = db.session.scalars(
            db.select(AuditLog.action).where(AuditLog.entity_id == user_id)
        ).all()
        assert "USER_EDITED" in actions
        status_actions = [
            action
            for action in actions
            if action in {"USER_ACTIVATED", "USER_DEACTIVATED"}
        ]
        assert status_actions == ([expected_action] if expected_action else [])
        assert db.session.get(User, user_id).is_active is submitted_status


@pytest.mark.parametrize("requested_status", [True, False])
@pytest.mark.parametrize("entity_type", ["user", "class", "room"])
def test_dedicated_status_routes_are_idempotent(
    client, app, administrator, entity_type, requested_status
):
    with app.app_context():
        if entity_type == "user":
            record = User(
                username=f"idempotent-{requested_status}",
                full_name="Idempotent User",
                password_hash="pending",
                role=UserRole.TEACHER,
                is_active=requested_status,
                must_change_password=False,
            )
            record.set_password(PASSWORD)
            action_prefix = "USER"
            base = "/admin/users"
        elif entity_type == "class":
            record = SchoolClass(
                name=f"Idempotent Class {requested_status}",
                is_active=requested_status,
            )
            action_prefix = "CLASS"
            base = "/admin/classes"
        else:
            record = Room(
                name=f"Idempotent Room {requested_status}",
                is_active=requested_status,
            )
            action_prefix = "ROOM"
            base = "/admin/rooms"
        db.session.add(record)
        db.session.commit()
        record_id = record.id

    operation = "activate" if requested_status else "deactivate"
    response = client.post(
        f"{base}/{record_id}/{operation}", follow_redirects=True
    )

    assert response.status_code == 200
    assert b"already has the requested status" in response.data
    with app.app_context():
        expected_action = (
            f"{action_prefix}_"
            f"{'ACTIVATED' if requested_status else 'DEACTIVATED'}"
        )
        false_audits = db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.entity_id == record_id,
                AuditLog.action == expected_action,
            )
        ).all()
        assert false_audits == []

def test_failed_commit_rolls_back_record_and_audit(
    client, app, administrator, monkeypatch
):
    def failed_commit():
        raise OperationalError("INSERT", {}, RuntimeError("unavailable"))

    monkeypatch.setattr(db.session, "commit", failed_commit)
    response = client.post(
        "/admin/rooms/new",
        data={"name": "Rolled Back Room", "is_active": "y"},
    )
    assert b"Unable to save the change" in response.data
    with app.app_context():
        assert db.session.scalar(
            db.select(Room).where(Room.name == "Rolled Back Room")
        ) is None
        assert db.session.scalar(
            db.select(AuditLog).where(AuditLog.entity_type == "Room")
        ) is None


def test_successful_actions_create_safe_audit_records(
    client, app, administrator
):
    client.post(
        "/admin/users/new",
        data=user_data(
            UserRole.TEACHER,
            temporary_password=RESET_PASSWORD,
            confirm_password=RESET_PASSWORD,
        ),
    )
    with app.app_context():
        audit = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "USER_CREATED")
        )
        assert audit.actor_id == administrator
        assert audit.entity_id is not None
        serialized = str(audit.details).lower()
        assert RESET_PASSWORD.lower() not in serialized
        assert "password" not in serialized
        assert "hash" not in serialized
