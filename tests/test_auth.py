"""Authentication and authorization tests."""

import re

import pytest
from sqlalchemy.exc import OperationalError

from app.blueprints.auth import routes as auth_routes
from app.extensions import db
from app.models import SchoolClass, User, UserRole

PASSWORD = "TemporaryPass123!"
NEW_PASSWORD = "ReplacementPass456!"

ROLE_PATHS = {
    UserRole.ADMIN: "/admin/",
    UserRole.SCHEDULER: "/scheduler/",
    UserRole.TEACHER: "/requester/teacher",
    UserRole.MONITOR: "/requester/monitor",
}


@pytest.fixture(autouse=True)
def database(app):
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


def create_user(app, role=UserRole.TEACHER, **overrides):
    with app.app_context():
        school_class = None
        if role == UserRole.MONITOR:
            school_class = SchoolClass(name=f"Class-{overrides.get('username', role)}")
            db.session.add(school_class)
        user = User(
            username=overrides.get("username", role.value.lower()),
            full_name=overrides.get("full_name", f"{role.value} User"),
            password_hash="placeholder",
            role=role,
            school_class=school_class,
            is_active=overrides.get("is_active", True),
            must_change_password=overrides.get("must_change_password", False),
        )
        user.set_password(overrides.get("password", PASSWORD))
        db.session.add(user)
        db.session.commit()
        return user.id


def login(client, username="teacher", password=PASSWORD, **query):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        query_string=query,
    )


def test_password_hashing_and_verification(app):
    user_id = create_user(app)
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.password_hash != PASSWORD
        assert user.check_password(PASSWORD)
        assert not user.check_password("wrong-password")


def test_valid_login_succeeds(client, app):
    create_user(app)
    response = login(client)
    assert response.status_code == 302
    assert response.location.endswith("/requester/teacher")


def test_missing_user_still_verifies_dummy_hash(client, monkeypatch):
    calls = []
    original = auth_routes.check_password_hash

    def recording_check(password_hash, password):
        calls.append((password_hash, password))
        return original(password_hash, password)

    monkeypatch.setattr(auth_routes, "check_password_hash", recording_check)
    response = login(client, username="missing")

    assert calls == [(auth_routes.DUMMY_PASSWORD_HASH, PASSWORD)]
    assert b"Invalid username or password." in response.data


def test_active_valid_user_verifies_real_hash(client, app, monkeypatch):
    user_id = create_user(app)
    with app.app_context():
        real_hash = db.session.get(User, user_id).password_hash
    calls = []
    original = auth_routes.check_password_hash

    def recording_check(password_hash, password):
        calls.append((password_hash, password))
        return original(password_hash, password)

    monkeypatch.setattr(auth_routes, "check_password_hash", recording_check)
    response = login(client)

    assert calls == [(real_hash, PASSWORD)]
    assert response.location.endswith("/requester/teacher")


@pytest.mark.parametrize(
    ("username", "password"), [("missing", PASSWORD), ("teacher", "wrong")]
)
def test_invalid_login_uses_generic_error(client, app, username, password):
    create_user(app)
    response = login(client, username, password)
    assert b"Invalid username or password." in response.data


def test_disabled_user_cannot_login(client, app):
    create_user(app, is_active=False)
    response = login(client)
    assert b"Invalid username or password." in response.data


def test_authenticated_user_visiting_login_redirects(client, app):
    create_user(app)
    login(client)
    assert client.get("/auth/login").location.endswith("/requester/teacher")


@pytest.mark.parametrize(("role", "path"), ROLE_PATHS.items())
def test_each_role_redirects_to_own_dashboard(client, app, role, path):
    create_user(app, role=role)
    response = login(client, username=role.value.lower())
    assert response.location.endswith(path)


@pytest.mark.parametrize(("role", "own_path"), ROLE_PATHS.items())
def test_role_dashboards_reject_other_roles(client, app, role, own_path):
    create_user(app, role=role)
    login(client, username=role.value.lower())
    for path in ROLE_PATHS.values():
        response = client.get(path)
        assert response.status_code == (200 if path == own_path else 403)


def test_unauthenticated_protected_route_redirects_to_login(client):
    response = client.get("/admin/")
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_safe_internal_next_redirect_works(client, app):
    create_user(app)
    assert login(client, next="/health").location.endswith("/health")


def test_safe_internal_next_with_query_string_works(client, app):
    create_user(app)
    response = login(client, next="/health?source=login")
    assert response.location.endswith("/health?source=login")


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/",
        "//evil.example/",
        r"/\evil",
        "/%ZZ",
        "/%2F%2Fevil.example",
        "/%5C%5Cevil.example",
        "/%0d%0aLocation%3Ahttps%3A%2F%2Fevil.example",
        "/%252F%252Fevil.example",
        "/%255C%255Cevil.example",
        "/%250d%250aLocation%253Aevil",
    ],
)
def test_unsafe_next_redirect_is_rejected(client, app, target):
    create_user(app)
    response = login(client, next=target)
    assert response.location.endswith("/requester/teacher")


def test_logout_requires_post(client, app):
    create_user(app)
    login(client)
    assert client.get("/auth/logout").status_code == 405
    assert client.post("/auth/logout").location.endswith("/")


def test_logout_is_csrf_protected(client, app):
    create_user(app)
    login(client)
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/auth/logout").status_code == 400


def test_logout_succeeds_with_valid_csrf_token(client, app):
    create_user(app)
    login(client)
    app.config["WTF_CSRF_ENABLED"] = True
    dashboard = client.get("/requester/teacher")
    match = re.search(rb'name="csrf_token" value="([^"]+)"', dashboard.data)
    assert match is not None

    response = client.post(
        "/auth/logout", data={"csrf_token": match.group(1).decode("utf-8")}
    )

    assert response.status_code == 302
    assert response.location.endswith("/")
    assert client.get("/requester/teacher").status_code == 302


def test_temporary_password_user_is_forced_to_change(client, app):
    create_user(app, must_change_password=True)
    response = login(client)
    assert response.location.endswith("/auth/change-password")
    assert client.get("/requester/teacher").location.endswith("/auth/change-password")


def test_incorrect_current_password_is_rejected(client, app):
    create_user(app, must_change_password=True)
    login(client)
    response = client.post(
        "/auth/change-password",
        data={
            "current_password": "incorrect",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert b"Current password is incorrect." in response.data


def test_password_confirmation_must_match(client, app):
    create_user(app, must_change_password=True)
    login(client)
    response = client.post(
        "/auth/change-password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": "DifferentPass789!",
        },
    )
    assert b"Field must be equal to new_password." in response.data


def test_new_password_cannot_equal_temporary_password(client, app):
    create_user(app, must_change_password=True)
    login(client)
    response = client.post(
        "/auth/change-password",
        data={
            "current_password": PASSWORD,
            "new_password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )
    assert b"New password must be different." in response.data


def test_successful_password_change_clears_flag_and_redirects(client, app):
    user_id = create_user(app, must_change_password=True)
    login(client)
    response = client.post(
        "/auth/change-password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert response.location.endswith("/requester/teacher")
    with app.app_context():
        user = db.session.get(User, user_id)
        assert not user.must_change_password
        assert user.check_password(NEW_PASSWORD)


def test_password_change_commit_failure_rolls_back(client, app, monkeypatch):
    user_id = create_user(app, must_change_password=True)
    login(client)

    def failed_commit():
        raise OperationalError("UPDATE users", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(db.session, "commit", failed_commit)
    response = client.post(
        "/auth/change-password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )

    assert b"Unable to change password. Please try again." in response.data
    with app.app_context():
        user = db.session.get(User, user_id, populate_existing=True)
        assert user.must_change_password
        assert user.check_password(PASSWORD)
        assert not user.check_password(NEW_PASSWORD)


def test_disabled_user_does_not_remain_authenticated(client, app):
    user_id = create_user(app)
    login(client)
    with app.app_context():
        user = db.session.get(User, user_id)
        user.is_active = False
        db.session.commit()
    response = client.get("/requester/teacher")
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_403_page_renders(client, app):
    create_user(app)
    login(client)
    response = client.get("/admin/")
    assert response.status_code == 403
    assert b"Access denied" in response.data
