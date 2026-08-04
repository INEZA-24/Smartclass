"""Milestone 10 structural, navigation, confirmation, and error-page tests."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import flash, redirect
from test_auth import PASSWORD, create_user, login

from app import create_app
from app.extensions import db
from app.models import Room, SchoolClass, User, UserRole


@pytest.fixture(autouse=True)
def database(app):
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


def test_public_layout_is_responsive_and_contains_no_private_navigation(client):
    response = client.get("/")
    assert response.status_code == 200
    for markup in (
        b'name="viewport"',
        b'href="#main-content"',
        b'id="main-content"',
        b'class="public-secondary-links"',
        b'class="public-button public-button-primary public-login"',
        b'href="/auth/login"',
    ):
        assert markup in response.data
    for private_link in (b">Users<", b">Reports<", b">Notifications<", b">Logout<"):
        assert private_link not in response.data
    assert "College Saint André" in response.get_data(as_text=True)


def test_anonymous_public_header_shows_only_login_action(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/auth/login">Login</a>' in html
    assert ">Dashboard</a>" not in html
    assert ">Logout</button>" not in html


@pytest.mark.parametrize(
    ("role", "dashboard_path"),
    [
        (UserRole.ADMIN, "/admin/"),
        (UserRole.SCHEDULER, "/scheduler/"),
        (UserRole.TEACHER, "/requester/teacher"),
        (UserRole.MONITOR, "/requester/monitor"),
    ],
)
def test_authenticated_public_header_has_role_dashboard_and_secure_logout(
    client, app, role, dashboard_path
):
    create_user(app, role=role)
    login(client, username=role.value.lower())
    html = client.get("/").get_data(as_text=True)
    assert f'href="{dashboard_path}">Dashboard</a>' in html
    assert 'method="post" action="/auth/logout"' in html
    assert 'name="csrf_token"' in html
    assert ">Logout</button>" in html
    assert 'href="/auth/login">Login</a>' not in html


@pytest.mark.parametrize(
    ("role", "expected", "forbidden"),
    [
        (
            UserRole.ADMIN,
            ("Users", "Classes", "Rooms", "Schedule", "Reports", "Notifications"),
            ("New request", "Pending requests"),
        ),
        (
            UserRole.SCHEDULER,
            ("Pending requests", "Schedule", "Reports", "Notifications"),
            ("Users", "Classes", "Rooms", "New request"),
        ),
        (
            UserRole.TEACHER,
            ("New request", "Request history", "Notifications"),
            ("Users", "Classes", "Rooms", "Schedule", "Reports"),
        ),
        (
            UserRole.MONITOR,
            ("New request", "Request history", "Notifications"),
            ("Users", "Classes", "Rooms", "Schedule", "Reports"),
        ),
    ],
)
def test_authenticated_navigation_matches_role(client, app, role, expected, forbidden):
    create_user(app, role=role)
    login(client, username=role.value.lower())
    response = client.get(
        {
            UserRole.ADMIN: "/admin/",
            UserRole.SCHEDULER: "/scheduler/",
            UserRole.TEACHER: "/requester/teacher",
            UserRole.MONITOR: "/requester/monitor",
        }[role]
    )
    html = response.get_data(as_text=True)
    for label in expected:
        assert f">{label}<" in html or f">\n                    {label}" in html
    for label in forbidden:
        assert f">{label}<" not in html
    assert "Logout" in html
    assert "csrf_token" in html
    assert {
        UserRole.ADMIN: "Administrator",
        UserRole.SCHEDULER: "Patron/Matron",
        UserRole.TEACHER: "Teacher",
        UserRole.MONITOR: "Class Monitor",
    }[role] in html


def test_login_fields_have_labels_required_text_and_autocomplete(client):
    html = client.get("/auth/login").get_data(as_text=True)
    assert '<label class="form-label" for="username">' in html
    assert '<label class="form-label" for="password">' in html
    assert html.count("(required)") >= 2
    assert 'autocomplete="username"' in html
    assert 'autocomplete="current-password"' in html


def test_invalid_login_fields_have_accessible_invalid_state(client):
    response = client.post("/auth/login", data={"username": "", "password": ""})
    assert response.status_code == 200
    assert response.data.count(b'aria-invalid="true"') >= 2
    assert response.data.count(b"invalid-feedback") >= 2
    assert b'aria-describedby="username-errors"' in response.data
    assert b'id="username-errors"' in response.data


def test_user_status_confirmation_is_safe_identified_post_and_csrf(client, app):
    create_user(app, role=UserRole.ADMIN)
    managed_id = create_user(
        app,
        role=UserRole.TEACHER,
        username="quoted-user",
        full_name='Quoted "<User>" O\'Neil',
    )
    login(client, username="admin")
    response = client.get("/admin/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "data-confirm-message=" in html
    assert "Deactivate user Quoted" in html
    assert "&lt;User&gt;" in html
    assert "csrf_token" in html
    assert "onsubmit=" not in html
    assert client.get(f"/admin/users/{managed_id}/deactivate").status_code == 405


def test_stylesheet_contains_focus_mobile_and_reduced_motion_rules(client):
    response = client.get("/static/app.css")
    assert response.status_code == 200
    for rule in (b":focus-visible", b"@media (max-width", b"prefers-reduced-motion"):
        assert rule in response.data


def test_public_stylesheet_contains_accessibility_and_responsive_rules(client):
    response = client.get("/static/public-home.css")
    assert response.status_code == 200
    for rule in (b":focus-visible", b"@media (max-width", b"prefers-reduced-motion"):
        assert rule in response.data


def test_confirmation_script_is_deferred_generic_and_contains_no_private_logic():
    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    script = Path("app/static/confirm.js").read_text(encoding="utf-8")
    assert 'defer src="{{ url_for(\'static\', filename=\'confirm.js\') }}"' in base
    assert 'form[data-confirm-message]' in script
    assert "window.confirm" in script
    assert "event.preventDefault()" in script
    for forbidden in (
        "booking_id",
        "request_id",
        "password",
        "reason",
        "csrf",
        "secret",
        "schedule",
    ):
        assert forbidden not in script.lower()
    for template in Path("app/templates").rglob("*.html"):
        assert "onsubmit=" not in template.read_text(encoding="utf-8")


@pytest.mark.parametrize("path,status,text", [("/missing", 404, "Page not found")])
def test_public_error_page_is_safe(client, path, status, text):
    response = client.get(path)
    assert response.status_code == status
    assert text.encode() in response.data
    assert b"Traceback" not in response.data
    assert b"SELECT " not in response.data
    assert b"C:\\Users" not in response.data
    assert b"Return to a safe page" in response.data


def test_403_page_is_safe_and_role_appropriate(client, app):
    create_user(app, role=UserRole.TEACHER)
    login(client)
    response = client.get("/admin/")
    assert response.status_code == 403
    assert b"not authorized" in response.data
    assert b"Traceback" not in response.data
    assert b'href="/requester/teacher"' in response.data


@pytest.mark.parametrize("authenticated", [False, True])
def test_500_page_hides_exception_and_secret_values(authenticated):
    app = create_app(
        {
            "TESTING": True,
            "PROPAGATE_EXCEPTIONS": False,
            "SECRET_KEY": "SECRET-KEY-SENTINEL",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
        }
    )

    @app.get("/_test/forced-error")
    def forced_error():
        raise RuntimeError("FORCED-PRIVATE-TRACE SQL SELECT password_hash")

    with app.app_context():
        db.create_all()
    client = app.test_client()
    if authenticated:
        create_user(app, role=UserRole.ADMIN)
        login(client, username="admin", password=PASSWORD)
    response = client.get("/_test/forced-error")
    assert response.status_code == 500
    html = response.get_data(as_text=True)
    assert "Something went wrong" in html
    for sentinel in (
        "FORCED-PRIVATE-TRACE",
        "RuntimeError",
        "Traceback",
        "SELECT password_hash",
        "SECRET-KEY-SENTINEL",
        "sqlite:///:memory:",
    ):
        assert sentinel not in html
    for private_navigation in ("Users", "Reports", "Notifications", "Logout"):
        assert f">{private_navigation}<" not in html


def build_error_test_app():
    app = create_app(
        {
            "TESTING": True,
            "PROPAGATE_EXCEPTIONS": False,
            "SECRET_KEY": "error-test-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
        }
    )
    with app.app_context():
        db.create_all()
    return app


def test_500_remains_safe_when_rollback_fails():
    app = build_error_test_app()

    @app.get("/_test/rollback-error")
    def rollback_error():
        raise RuntimeError("ORIGINAL-FAILURE-SENTINEL")

    with patch.object(db.session, "rollback", side_effect=RuntimeError("ROLLBACK")):
        response = app.test_client().get("/_test/rollback-error")
    assert response.status_code == 500
    assert b"Something went wrong" in response.data
    assert b"ROLLBACK" not in response.data
    assert b"ORIGINAL-FAILURE-SENTINEL" not in response.data


def test_unread_count_failure_uses_database_independent_500():
    app = build_error_test_app()
    create_user(app, role=UserRole.ADMIN)
    client = app.test_client()
    login(client, username="admin")
    with patch.object(db.session, "scalar", side_effect=RuntimeError("UNREAD-FAIL")):
        response = client.get("/admin/")
    assert response.status_code == 500
    assert b"Something went wrong" in response.data
    assert b"UNREAD-FAIL" not in response.data
    assert b">Notifications<" not in response.data


def test_authenticated_user_load_failure_does_not_recurse():
    app = build_error_test_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "999"
        session["_fresh"] = True
    with patch.object(db.session, "get", side_effect=RuntimeError("USER-LOAD-FAIL")):
        response = client.get("/")
    assert response.status_code == 500
    assert b"Something went wrong" in response.data
    assert b"USER-LOAD-FAIL" not in response.data
    assert b">Logout<" not in response.data


def test_milestone10_does_not_enable_debug():
    app = create_app(
        {
            "TESTING": True,
            "DEBUG": False,
            "SECRET_KEY": "debug-test-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
    assert app.debug is False


@pytest.mark.parametrize(
    ("role", "new_path", "history_path", "other_new_path"),
    [
        (
            UserRole.TEACHER,
            "/requester/teacher/new",
            "/requester/teacher/requests",
            "/requester/monitor/new",
        ),
        (
            UserRole.MONITOR,
            "/requester/monitor/new",
            "/requester/monitor/requests",
            "/requester/teacher/new",
        ),
    ],
)
def test_requester_dashboard_has_only_role_specific_actions(
    client, app, role, new_path, history_path, other_new_path
):
    create_user(app, role=role)
    login(client, username=role.value.lower())
    response = client.get(
        "/requester/teacher" if role == UserRole.TEACHER else "/requester/monitor"
    )
    assert response.status_code == 200
    assert f'href="{new_path}"'.encode() in response.data
    assert f'href="{history_path}"'.encode() in response.data
    assert f'href="{other_new_path}"'.encode() not in response.data


def test_patron_matron_and_password_state_wording(client, app):
    create_user(app, role=UserRole.SCHEDULER)
    login(client, username="scheduler")
    dashboard = client.get("/scheduler/")
    assert b"Patron/Matron Dashboard" in dashboard.data
    assert b"Scheduler Dashboard" not in dashboard.data

    client.post("/auth/logout")
    create_user(app, role=UserRole.ADMIN)
    create_user(app, role=UserRole.TEACHER, username="password-user")
    with app.app_context():
        user = db.session.scalar(
            db.select(User).where(User.username == "password-user")
        )
        user.must_change_password = True
        db.session.commit()
    login(client, username="admin")
    users_page = client.get("/admin/users")
    assert b"Password change required" in users_page.data
    assert b">SCHEDULER<" not in users_page.data


def test_class_and_room_context_drives_empty_captions_and_safe_confirmations(
    client, app
):
    create_user(app, role=UserRole.ADMIN)
    login(client, username="admin")
    assert "Managed class records" in client.get("/admin/classes").get_data(
        as_text=True
    )
    assert "Managed room records" in client.get("/admin/rooms").get_data(as_text=True)
    with app.app_context():
        db.session.add_all(
            [
                SchoolClass(name='Class "<One>"'),
                SchoolClass(name="Inactive class", is_active=False),
                Room(name="Room O'Neil"),
                Room(name="Inactive room", is_active=False),
            ]
        )
        db.session.commit()
    classes = client.get("/admin/classes").get_data(as_text=True)
    rooms = client.get("/admin/rooms").get_data(as_text=True)
    assert "Managed class records" in classes
    assert "Managed room records" in rooms
    assert "Deactivate class Class &#34;&lt;One&gt;&#34;" in classes
    assert "Activate class Inactive class" in classes
    assert "Deactivate room Room O&#39;Neil" in rooms
    assert "Activate room Inactive room" in rooms


@pytest.mark.parametrize(
    ("category", "bootstrap_class"),
    [
        ("success", "alert-success"),
        ("info", "alert-info"),
        ("warning", "alert-warning"),
        ("error", "alert-danger"),
        ("unexpected", "alert-info"),
    ],
)
def test_flash_categories_are_accessible_and_escaped(category, bootstrap_class):
    app = build_error_test_app()

    @app.get("/_test/flash")
    def flash_test_message():
        flash("<script>FLASH-SENTINEL</script>", category)
        return redirect("/")

    response = app.test_client().get("/_test/flash", follow_redirects=True)
    html = response.get_data(as_text=True)
    assert bootstrap_class in html
    assert 'role="alert"' in html
    assert 'aria-label="Dismiss message"' in html
    assert "&lt;script&gt;FLASH-SENTINEL&lt;/script&gt;" in html
    assert "<script>FLASH-SENTINEL</script>" not in html


def test_critical_rendered_page_has_no_duplicate_ids(client):
    html = client.get("/auth/login").get_data(as_text=True)
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
