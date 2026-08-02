"""Focused production-deployment readiness tests."""

import importlib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import flask_migrate
import pytest
import yaml
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from flask import request, session, url_for
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.datastructures import MultiDict

from app import create_app
from app.blueprints.admin.forms import UserCreateForm
from app.extensions import db
from app.models import Room, SchoolClass, SystemSettings, User, UserRole
from app.provisioning import (
    provision_admin_from_env_command,
    validate_initial_admin_values,
)
from app.seed import CLASS_NAMES, ROOM_NAMES, seed_command
from scripts import secret_scan, smoke_test

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SECRET = "correct-horse-battery-staple-production-key"
PRODUCTION_DATABASE = (
    "postgresql://user:password@ep-unit-test.eu-central-1.aws.neon.tech/scms"
    "?sslmode=require&channel_binding=require"
)


@pytest.fixture()
def tmp_path():
    """Provide a sandbox-writable temporary directory for POSIX script tests."""
    base = Path.cwd() / "instance" / "deployment-test-temp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"startup-{uuid.uuid4().hex}"
    path.mkdir(mode=0o755)
    yield path
    shutil.rmtree(path)


def production_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", PRODUCTION_DATABASE)
    return create_app("production")


@pytest.mark.parametrize("missing_name", ["SECRET_KEY", "DATABASE_URL"])
def test_production_requires_configuration(monkeypatch, missing_name):
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", PRODUCTION_DATABASE)
    monkeypatch.delenv(missing_name)
    with pytest.raises(RuntimeError, match=missing_name):
        create_app("production")


@pytest.mark.parametrize(
    "placeholder",
    [
        "replace-with-a-production-secret",
        "change-me",
        "development-secret-value",
        "testing-only-key",
        "secret",
    ],
)
def test_production_rejects_placeholder_secret(monkeypatch, placeholder):
    monkeypatch.setenv("SECRET_KEY", placeholder)
    monkeypatch.setenv("DATABASE_URL", PRODUCTION_DATABASE)
    with pytest.raises(RuntimeError, match="non-placeholder"):
        create_app("production")


@pytest.mark.parametrize(
    "database_url", ["sqlite:///production.db", "mysql://host/database"]
)
def test_production_rejects_non_postgresql_database(monkeypatch, database_url):
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    with pytest.raises(RuntimeError, match="must use PostgreSQL"):
        create_app("production")


def test_production_configuration_is_secure_and_preserves_url_query(monkeypatch):
    app = production_app(monkeypatch)
    assert app.config["DEBUG"] is False
    assert app.config["TESTING"] is False
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["REMEMBER_COOKIE_SECURE"] is True
    assert app.config["SQLALCHEMY_ENGINE_OPTIONS"]["pool_pre_ping"] is True
    assert app.config["SQLALCHEMY_DATABASE_URI"] == (
        "postgresql+psycopg://user:password@"
        "ep-unit-test.eu-central-1.aws.neon.tech/scms"
        "?sslmode=require&channel_binding=require"
    )


def test_development_configuration_remains_usable(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "local-only")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///local.db")
    app = create_app("development")
    assert app.debug is True
    assert app.config["TRUST_RENDER_PROXY"] is False
    assert app.config.get("SESSION_COOKIE_SECURE", False) is False


def test_production_trusts_one_proxy_hop_and_has_no_https_redirect(monkeypatch):
    app = production_app(monkeypatch)

    @app.get("/_deployment/request")
    def deployment_request_probe():
        session["probe"] = True
        return {
            "secure": request.is_secure,
            "external": url_for("public.health", _external=True),
        }

    client = app.test_client()
    forwarded = client.get(
        "/_deployment/request",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "service.test"},
    )
    assert forwarded.status_code == 200
    assert forwarded.get_json() == {
        "secure": True,
        "external": "https://service.test/health",
    }
    cookie = forwarded.headers["Set-Cookie"]
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie

    direct = client.get("/_deployment/request", base_url="http://direct.test")
    assert direct.status_code == 200
    assert direct.get_json()["secure"] is False
    assert not direct.headers.get("Location")


def test_health_is_exact_and_never_accesses_database(app, monkeypatch):
    def database_access_forbidden(*_args, **_kwargs):
        raise AssertionError("health endpoint attempted database access")

    monkeypatch.setattr(db.session, "execute", database_access_forbidden)
    monkeypatch.setattr(db.session, "scalar", database_access_forbidden)
    monkeypatch.setattr(db.session, "get", database_access_forbidden)
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert json.loads(response.data) == {"status": "ok"}
    assert response.data == b'{"status":"ok"}\n'
    assert response.content_type == "application/json"
    assert response.headers.getlist("Set-Cookie") == []


@pytest.mark.parametrize("user_id", ["1", "999999"])
def test_health_bypasses_valid_or_stale_login_session(app, monkeypatch, user_id):
    with app.app_context():
        db.create_all()
        if user_id == "1":
            user = User(
                username="health-user",
                full_name="Health User",
                role=UserRole.TEACHER,
                is_active=True,
                must_change_password=False,
            )
            user.set_password("health-test-password")
            db.session.add(user)
            db.session.commit()
            assert str(user.id) == user_id
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["_user_id"] = user_id
        flask_session["_fresh"] = True

    def forbidden(*_args, **_kwargs):
        raise AssertionError("health evaluated authentication or database state")

    monkeypatch.setattr(app.login_manager, "_user_callback", forbidden)
    monkeypatch.setattr(db.session, "get", forbidden)
    monkeypatch.setattr(db.session, "scalar", forbidden)
    monkeypatch.setattr(db.session, "scalars", forbidden)
    monkeypatch.setattr(db.session, "execute", forbidden)
    monkeypatch.setattr(db.session, "commit", forbidden)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.data == b'{"status":"ok"}\n'
    assert response.headers.getlist("Set-Cookie") == []
    with client.session_transaction() as flask_session:
        assert flask_session["_user_id"] == user_id


def test_health_bypass_does_not_recurse_into_application_error(app, monkeypatch):
    monkeypatch.setattr(
        app.login_manager,
        "_user_callback",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("loader unavailable")),
    )
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert b"Internal server error" not in response.data


def test_production_validation_is_independent_from_proxy_trust():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(
            {
                "IS_PRODUCTION": True,
                "TRUST_RENDER_PROXY": False,
                "SQLALCHEMY_DATABASE_URI": PRODUCTION_DATABASE,
            }
        )


def test_proxy_trust_does_not_enable_production_validation():
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "TRUST_RENDER_PROXY": True,
            "IS_PRODUCTION": False,
        }
    )
    assert app.config["TRUST_RENDER_PROXY"] is True
    assert app.config["IS_PRODUCTION"] is False


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://user:password@host.example/scms?sslmode=require",
        "postgres://user:password@host.example/scms?sslmode=require",
        "postgresql+psycopg://user:password@host.example/scms?sslmode=require",
        "postgresql://user:p%40ss%3Aword@host.example/scms?sslmode=require&x=1",
    ],
)
def test_complete_production_postgresql_urls_are_accepted(monkeypatch, database_url):
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    app = create_app("production")
    normalized = app.config["SQLALCHEMY_DATABASE_URI"]
    assert normalized.startswith("postgresql+psycopg://")
    assert database_url.split("?", 1)[-1] == normalized.split("?", 1)[-1]
    if "%40" in database_url:
        assert "p%40ss%3Aword" in normalized


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_production_accepts_each_secure_tls_mode(monkeypatch, sslmode):
    database_url = (
        "postgresql://user:password@ep-unit-test.eu-central-1.aws.neon.tech/scms"
        f"?sslmode={sslmode}&channel_binding=require&application_name=scms"
    )
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    app = create_app("production")
    normalized = app.config["SQLALCHEMY_DATABASE_URI"]
    assert normalized.endswith(
        f"sslmode={sslmode}&channel_binding=require&application_name=scms"
    )


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?sslmode=",
        "?sslmode=disable",
        "?sslmode=allow",
        "?sslmode=prefer",
        "?sslmode=require&sslmode=verify-full",
        "?sslmode=require&SSLMODE=verify-full",
    ],
)
def test_production_rejects_missing_unsafe_or_duplicate_tls_modes(
    monkeypatch, query
):
    database_url = f"postgresql://user:password@host.example/scms{query}"
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    with pytest.raises(RuntimeError, match="secure PostgreSQL TLS mode") as caught:
        create_app("production")
    assert database_url not in str(caught.value)
    assert "user:password" not in str(caught.value)


def test_production_tls_query_key_and_value_are_case_insensitive(monkeypatch):
    database_url = "postgresql://user:password@host.example/scms?SSLMODE=VERIFY-FULL"
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    app = create_app("production")
    assert app.config["SQLALCHEMY_DATABASE_URI"].endswith("SSLMODE=VERIFY-FULL")


@pytest.mark.parametrize(
    "hostname",
    [
        "ep-unit-test-pooler.eu-central-1.aws.neon.tech",
        "EP-UNIT-TEST-POOLER.EU-CENTRAL-1.AWS.NEON.TECH",
    ],
)
def test_production_rejects_neon_pooled_hostnames_safely(monkeypatch, hostname):
    database_url = (
        f"postgresql://neon_user:neon_password@{hostname}/scms"
        "?sslmode=require&channel_binding=require"
    )
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    with pytest.raises(RuntimeError, match="direct PostgreSQL connection") as caught:
        create_app("production")
    message = str(caught.value)
    assert database_url not in message
    assert hostname not in message
    assert "neon_user" not in message
    assert "neon_password" not in message
    assert "channel_binding" not in message


def test_production_accepts_direct_neon_and_ordinary_postgresql_hosts(monkeypatch):
    for hostname in (
        "ep-unit-test.eu-central-1.aws.neon.tech",
        "postgres.internal.example",
    ):
        monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
        monkeypatch.setenv(
            "DATABASE_URL",
            f"postgresql://user:password@{hostname}/scms?sslmode=require",
        )
        app = create_app("production")
        assert app.config["SQLALCHEMY_DATABASE_URI"].startswith(
            "postgresql+psycopg://"
        )


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://user:password@/scms",
        "postgresql://host.example/scms",
        "postgresql://user@host.example/scms",
        "postgresql://user:password@host.example/",
        "sqlite:///production.db",
        "mysql://user:password@host.example/scms",
        "postgresql://user:password@host.example:notaport/scms",
        "not a database URL",
    ],
)
def test_incomplete_or_malformed_production_urls_fail_without_disclosure(
    monkeypatch, database_url
):
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    with pytest.raises(RuntimeError) as caught:
        create_app("production")
    message = str(caught.value)
    assert database_url not in message
    assert "user:password@" not in message


def test_wsgi_import_has_only_application_construction(monkeypatch):
    def initialization_forbidden(*_args, **_kwargs):
        raise AssertionError("WSGI import ran deployment initialization")

    monkeypatch.setattr(flask_migrate, "upgrade", initialization_forbidden)
    monkeypatch.setattr(seed_command, "callback", initialization_forbidden)
    monkeypatch.setattr(
        provision_admin_from_env_command, "callback", initialization_forbidden
    )
    monkeypatch.setenv("SECRET_KEY", PRODUCTION_SECRET)
    monkeypatch.setenv("DATABASE_URL", PRODUCTION_DATABASE)
    sys.modules.pop("wsgi", None)
    module = importlib.import_module("wsgi")
    assert module.app.config["DEBUG"] is False
    assert not hasattr(module, "application")


def test_start_script_orders_fail_fast_startup():
    source = (ROOT / "scripts" / "render-start.sh").read_text(encoding="utf-8")
    migration = source.index("db upgrade")
    seed = source.index(" seed")
    bootstrap = source.index("provision-admin-from-env")
    unset_username = source.index("unset INITIAL_ADMIN_USERNAME")
    unset_full_name = source.index("unset INITIAL_ADMIN_FULL_NAME")
    unset_password = source.index("unset INITIAL_ADMIN_PASSWORD")
    gunicorn = source.index("exec gunicorn")
    assert migration < seed < bootstrap
    assert bootstrap < unset_username < unset_full_name < unset_password < gunicorn
    assert "set -eu" in source
    assert "set -x" not in source
    assert '${PORT:-10000}' in source
    assert '${GUNICORN_WORKERS:-1}' in source
    assert "DATABASE_URL is required" in source
    assert "echo" not in source
    assert "db migrate" not in source
    assert "db downgrade" not in source
    assert "db reset" not in source


def find_posix_shell() -> str | None:
    candidates = (
        shutil.which("sh"),
        r"C:\Program Files\Git\bin\sh.exe",
        r"C:\Program Files\Git\usr\bin\sh.exe",
    )
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        probe = subprocess.run(
            [candidate, "-c", "exit 0"],
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    return None


def make_fake_start_commands(tmp_path: Path) -> tuple[Path, Path]:
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    flask = command_dir / "flask"
    flask.write_text(
        "#!/bin/sh\n"
        "user_state=${INITIAL_ADMIN_USERNAME+present}\n"
        "name_state=${INITIAL_ADMIN_FULL_NAME+present}\n"
        "password_state=${INITIAL_ADMIN_PASSWORD+present}\n"
        "printf 'flask:%s|user=%s|name=%s|password=%s\\n' \"$*\" "
        '"${user_state:-absent}" "${name_state:-absent}" '
        '"${password_state:-absent}" >> "$TRACE_FILE"\n'
        "case \"$*\" in *\"${FAIL_STEP:-never-match}\"*) exit 17;; esac\n",
        encoding="utf-8",
        newline="\n",
    )
    gunicorn = command_dir / "gunicorn"
    gunicorn.write_text(
        "#!/bin/sh\n"
        "if [ \"${INITIAL_ADMIN_USERNAME+set}\" = set ] || "
        "[ \"${INITIAL_ADMIN_FULL_NAME+set}\" = set ] || "
        "[ \"${INITIAL_ADMIN_PASSWORD+set}\" = set ]; then exit 31; fi\n"
        "secret_state=${SECRET_KEY+present}\n"
        "database_state=${DATABASE_URL+present}\n"
        "printf 'gunicorn:%s|secret=%s|database=%s|user=absent|name=absent|"
        "password=absent\\n' \"$*\" \"${secret_state:-absent}\" "
        '"${database_state:-absent}" >> "$TRACE_FILE"\n',
        encoding="utf-8",
        newline="\n",
    )
    flask.chmod(0o755)
    gunicorn.chmod(0o755)
    return command_dir, tmp_path / "trace.txt"


def run_fake_start(tmp_path, *, fail_step=None, overrides=None, omit=None):
    shell = find_posix_shell()
    if shell is None:
        pytest.skip("No POSIX shell is available for startup-script execution")
    command_dir, trace = make_fake_start_commands(tmp_path)
    environment = {
        "PATH": command_dir.as_posix(),
        "TRACE_FILE": trace.as_posix(),
        "SECRET_KEY": "controlled-startup-secret",
        "DATABASE_URL": "controlled-startup-database-url",
    }
    if fail_step:
        environment["FAIL_STEP"] = fail_step
    environment.update(overrides or {})
    environment.pop(omit, None)
    result = subprocess.run(
        [shell, (ROOT / "scripts" / "render-start.sh").as_posix()],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    lines = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result, lines


def test_start_script_executes_commands_in_order_with_defaults(tmp_path):
    result, lines = run_fake_start(tmp_path)
    assert result.returncode == 0
    assert [line.split(":", 1)[0] for line in lines] == [
        "flask",
        "flask",
        "flask",
        "gunicorn",
    ]
    assert "db upgrade" in lines[0]
    assert " seed|" in lines[1]
    assert " provision-admin-from-env|" in lines[2]
    assert "--bind 0.0.0.0:10000" in lines[3]
    assert "--workers 1" in lines[3]
    assert "--threads 2" in lines[3]
    assert "secret=present" in lines[3]
    assert "database=present" in lines[3]
    assert "user=absent|name=absent|password=absent" in lines[3]


def test_start_script_limits_bootstrap_environment_to_provisioning(tmp_path):
    sentinels = {
        "INITIAL_ADMIN_USERNAME": "controlled-bootstrap-user",
        "INITIAL_ADMIN_FULL_NAME": "controlled-bootstrap-name",
        "INITIAL_ADMIN_PASSWORD": "controlled-bootstrap-password",
    }
    result, lines = run_fake_start(tmp_path, overrides=sentinels)
    assert result.returncode == 0
    assert [line.split(":", 1)[0] for line in lines] == [
        "flask",
        "flask",
        "flask",
        "gunicorn",
    ]
    assert "db upgrade" in lines[0]
    assert " seed|" in lines[1]
    assert " provision-admin-from-env|" in lines[2]
    assert "user=present|name=present|password=present" in lines[2]
    assert "user=absent|name=absent|password=absent" in lines[3]
    assert "secret=present" in lines[3]
    assert "database=present" in lines[3]
    captured = result.stdout + result.stderr + "\n".join(lines)
    assert all(value not in captured for value in sentinels.values())


def test_start_script_bootstrap_failure_with_credentials_prevents_gunicorn(tmp_path):
    password_sentinel = "controlled-failing-bootstrap-password"
    result, lines = run_fake_start(
        tmp_path,
        fail_step="provision-admin-from-env",
        overrides={
            "INITIAL_ADMIN_USERNAME": "controlled-failing-user",
            "INITIAL_ADMIN_FULL_NAME": "controlled-failing-name",
            "INITIAL_ADMIN_PASSWORD": password_sentinel,
        },
    )
    assert result.returncode == 17
    assert len(lines) == 3
    assert "user=present|name=present|password=present" in lines[-1]
    assert not any(line.startswith("gunicorn:") for line in lines)
    assert password_sentinel not in result.stdout + result.stderr + "\n".join(lines)


def test_start_script_respects_safe_overrides(tmp_path):
    result, lines = run_fake_start(
        tmp_path,
        overrides={
            "PORT": "12345",
            "GUNICORN_WORKERS": "2",
            "GUNICORN_THREADS": "4",
            "GUNICORN_TIMEOUT": "90",
        },
    )
    assert result.returncode == 0
    gunicorn = lines[-1]
    assert "--bind 0.0.0.0:12345" in gunicorn
    assert "--workers 2" in gunicorn
    assert "--threads 4" in gunicorn
    assert "--timeout 90" in gunicorn


@pytest.mark.parametrize(
    "failure,expected_count",
    [("db upgrade", 1), (" seed", 2), ("provision-admin-from-env", 3)],
)
def test_start_script_stops_after_failed_initialization(
    tmp_path, failure, expected_count
):
    result, lines = run_fake_start(tmp_path, fail_step=failure)
    assert result.returncode == 17
    assert len(lines) == expected_count
    assert not any(line.startswith("gunicorn:") for line in lines)


@pytest.mark.parametrize("missing", ["SECRET_KEY", "DATABASE_URL"])
def test_start_script_rejects_missing_environment_without_commands_or_secrets(
    tmp_path, missing
):
    result, lines = run_fake_start(tmp_path, omit=missing)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert lines == []
    assert "controlled-startup-secret" not in output
    assert "controlled-startup-database-url" not in output


def test_runtime_pin_and_render_blueprint_are_safe():
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.14.2"
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    assert set(blueprint) == {"services"}
    assert len(blueprint["services"]) == 1
    service = blueprint["services"][0]
    assert service["type"] == "web"
    assert service["runtime"] == "python"
    assert service["region"] == "frankfurt"
    assert service["plan"] == "free"
    assert service["buildCommand"] == "pip install -r requirements.txt"
    assert service["startCommand"] == "sh scripts/render-start.sh"
    assert service["healthCheckPath"] == "/health"
    assert "preDeployCommand" not in service
    assert "previews" not in service

    environment = {item["key"]: item for item in service["envVars"]}
    assert environment["APP_ENV"] == {"key": "APP_ENV", "value": "production"}
    assert environment["SECRET_KEY"] == {
        "key": "SECRET_KEY",
        "generateValue": True,
    }
    for name in (
        "DATABASE_URL",
        "INITIAL_ADMIN_USERNAME",
        "INITIAL_ADMIN_FULL_NAME",
        "INITIAL_ADMIN_PASSWORD",
    ):
        assert environment[name] == {"key": name, "sync": False}
    assert environment["GUNICORN_WORKERS"]["value"] == "1"
    assert environment["GUNICORN_THREADS"]["value"] == "2"
    assert all(item.get("type", "web") == "web" for item in blueprint["services"])


def invoke_provision(app, monkeypatch, **values):
    for name in (
        "INITIAL_ADMIN_USERNAME",
        "INITIAL_ADMIN_FULL_NAME",
        "INITIAL_ADMIN_PASSWORD",
    ):
        if name in values:
            monkeypatch.setenv(name, values[name])
        else:
            monkeypatch.delenv(name, raising=False)
    return app.test_cli_runner().invoke(args=["provision-admin-from-env"])


@pytest.fixture()
def provision_database(app):
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


def valid_admin_values(**overrides):
    values = {
        "INITIAL_ADMIN_USERNAME": "initial-admin",
        "INITIAL_ADMIN_FULL_NAME": "Initial Administrator",
        "INITIAL_ADMIN_PASSWORD": "temporary-password-123",
    }
    values.update(overrides)
    return values


def test_admin_provisioning_skips_when_absent(app, provision_database, monkeypatch):
    result = invoke_provision(app, monkeypatch)
    assert result.exit_code == 0
    assert "not requested" in result.output


def test_admin_provisioning_rejects_partial_values(
    app, provision_database, monkeypatch
):
    result = invoke_provision(
        app, monkeypatch, INITIAL_ADMIN_USERNAME="initial-admin"
    )
    assert result.exit_code != 0
    assert "required together" in result.output


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"INITIAL_ADMIN_USERNAME": "   "}, "Unable to provision"),
        ({"INITIAL_ADMIN_FULL_NAME": "   "}, "Unable to provision"),
        ({"INITIAL_ADMIN_PASSWORD": "too-short"}, "Unable to provision"),
    ],
)
def test_admin_provisioning_validates_values(
    app, provision_database, monkeypatch, overrides, message
):
    result = invoke_provision(app, monkeypatch, **valid_admin_values(**overrides))
    assert result.exit_code != 0
    assert message in result.output


@pytest.mark.parametrize(
    "username,full_name,password,expected",
    [
        ("  New.Admin  ", "  New   Administrator ", "strong-password-123", True),
        ("", "New Administrator", "strong-password-123", False),
        ("new\nadmin", "New Administrator", "strong-password-123", False),
        ("x" * 81, "New Administrator", "strong-password-123", False),
        ("new-admin", "", "strong-password-123", False),
        ("new-admin", "New\tAdministrator", "strong-password-123", False),
        ("new-admin", "x" * 151, "strong-password-123", False),
        ("new-admin", "New Administrator", "short", False),
        ("new-admin", "New Administrator", "strong\npassword-123", False),
    ],
)
def test_admin_form_and_bootstrap_share_validation_policy(
    app, username, full_name, password, expected
):
    with app.test_request_context("/", method="POST"):
        form = UserCreateForm(
            formdata=MultiDict(
                {
                    "username": username,
                    "full_name": full_name,
                    "role": UserRole.ADMIN.value,
                    "class_id": "0",
                    "is_active": "y",
                    "temporary_password": password,
                    "confirm_password": password,
                }
            )
        )
        form_valid = form.validate()

    supplied = {
        "INITIAL_ADMIN_USERNAME": username,
        "INITIAL_ADMIN_FULL_NAME": full_name,
        "INITIAL_ADMIN_PASSWORD": password,
    }
    try:
        normalized = validate_initial_admin_values(supplied)
    except ValueError:
        bootstrap_valid = False
    else:
        bootstrap_valid = True
        if expected:
            assert normalized[:2] == ("new.admin", "New Administrator")
    assert form_valid is expected
    assert bootstrap_valid is expected


@pytest.mark.parametrize(
    "username,full_name,expected_normalized",
    [
        (f"  {'u' * 80}  ", "Valid Name", ("u" * 80, "Valid Name")),
        (f"  {'u' * 81}  ", "Valid Name", None),
        ("user", "First     Last", ("user", "First Last")),
        ("user", f"A{' ' * 200}B", ("user", "A B")),
        ("user", "n" * 151, None),
        ("umuyobozi", "Joséphine Uwase", ("umuyobozi", "Joséphine Uwase")),
        ("user\rname", "Valid Name", None),
        ("user", "Valid\nName", None),
        ("", "Valid Name", None),
        ("   ", "   ", None),
    ],
)
def test_shared_account_validation_normalizes_before_length_checks(
    app, username, full_name, expected_normalized
):
    password = "strong-password-123"
    with app.test_request_context("/", method="POST"):
        form = UserCreateForm(
            formdata=MultiDict(
                {
                    "username": username,
                    "full_name": full_name,
                    "role": UserRole.ADMIN.value,
                    "class_id": "0",
                    "temporary_password": password,
                    "confirm_password": password,
                }
            )
        )
        form_valid = form.validate()
    try:
        normalized = validate_initial_admin_values(
            {
                "INITIAL_ADMIN_USERNAME": username,
                "INITIAL_ADMIN_FULL_NAME": full_name,
                "INITIAL_ADMIN_PASSWORD": password,
            }
        )[:2]
    except ValueError:
        normalized = None
    assert form_valid is (expected_normalized is not None)
    assert normalized == expected_normalized


def test_admin_ui_and_bootstrap_store_identical_normalized_values(app, monkeypatch):
    username = "  Stored.Admin  "
    full_name = "Stored     Administrator"
    password = "strong-password-123"
    with app.app_context():
        db.create_all()
        actor = User(
            username="root-admin",
            full_name="Root Administrator",
            role=UserRole.ADMIN,
            is_active=True,
            must_change_password=False,
        )
        actor.set_password(password)
        db.session.add(actor)
        db.session.commit()
    client = app.test_client()
    assert client.post(
        "/auth/login", data={"username": "root-admin", "password": password}
    ).status_code == 302
    response = client.post(
        "/admin/users/new",
        data={
            "username": username,
            "full_name": full_name,
            "role": UserRole.ADMIN.value,
            "class_id": "0",
            "is_active": "y",
            "temporary_password": password,
            "confirm_password": password,
        },
    )
    assert response.status_code == 302
    with app.app_context():
        ui_user = db.session.scalar(
            db.select(User).where(User.username == "stored.admin")
        )
        ui_values = (ui_user.username, ui_user.full_name)

    bootstrap_app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "bootstrap-test",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
        }
    )
    for name, value in {
        "INITIAL_ADMIN_USERNAME": username,
        "INITIAL_ADMIN_FULL_NAME": full_name,
        "INITIAL_ADMIN_PASSWORD": password,
    }.items():
        monkeypatch.setenv(name, value)
    with bootstrap_app.app_context():
        db.create_all()
    result = bootstrap_app.test_cli_runner().invoke(
        args=["provision-admin-from-env"]
    )
    assert result.exit_code == 0
    with bootstrap_app.app_context():
        bootstrap_user = db.session.scalar(db.select(User))
        bootstrap_values = (bootstrap_user.username, bootstrap_user.full_name)
    assert ui_values == bootstrap_values == ("stored.admin", "Stored Administrator")


def test_admin_provisioning_is_hashed_and_idempotent(
    app, provision_database, monkeypatch
):
    values = valid_admin_values()
    first = invoke_provision(app, monkeypatch, **values)
    second = invoke_provision(app, monkeypatch, **values)
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert values["INITIAL_ADMIN_PASSWORD"] not in first.output + second.output
    with app.app_context():
        users = db.session.scalars(db.select(User)).all()
        assert len(users) == 1
        administrator = users[0]
        assert administrator.role == UserRole.ADMIN
        assert administrator.is_active is True
        assert administrator.must_change_password is True
        assert administrator.class_id is None
        assert administrator.password_hash != values["INITIAL_ADMIN_PASSWORD"]
        assert administrator.check_password(values["INITIAL_ADMIN_PASSWORD"])


def test_admin_provisioning_rejects_existing_non_admin(
    app, provision_database, monkeypatch
):
    with app.app_context():
        user = User(
            username="initial-admin",
            full_name="Teacher",
            role=UserRole.TEACHER,
            is_active=True,
            must_change_password=False,
        )
        user.set_password("existing-password-123")
        db.session.add(user)
        db.session.commit()
    result = invoke_provision(app, monkeypatch, **valid_admin_values())
    assert result.exit_code != 0
    assert "non-Administrator" in result.output


def test_admin_provisioning_rejects_different_existing_admin(
    app, provision_database, monkeypatch
):
    with app.app_context():
        user = User(
            username="other-admin",
            full_name="Other Administrator",
            role=UserRole.ADMIN,
            is_active=True,
            must_change_password=False,
        )
        user.set_password("existing-password-123")
        db.session.add(user)
        db.session.commit()
    result = invoke_provision(app, monkeypatch, **valid_admin_values())
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_admin_provisioning_commit_failure_rolls_back(
    app, provision_database, monkeypatch
):
    with app.app_context():
        monkeypatch.setattr(
            db.session, "commit", lambda: (_ for _ in ()).throw(SQLAlchemyError())
        )
        result = invoke_provision(app, monkeypatch, **valid_admin_values())
        assert result.exit_code != 0
        assert "Unable to provision" in result.output
        assert "temporary-password-123" not in result.output
        assert db.session().in_transaction() is False
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 0
        db.session.rollback()
        assert db.session().in_transaction() is False


def test_seed_preserves_disabled_records_and_queue_state(app):
    with app.app_context():
        db.create_all()
        school_class = SchoolClass(name=CLASS_NAMES[0], is_active=False)
        room = Room(name=ROOM_NAMES[0], is_active=False)
        settings = SystemSettings(id=1, booking_queue_locked=True)
        db.session.add_all([school_class, room, settings])
        db.session.commit()
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed"]).exit_code == 0
    assert runner.invoke(args=["seed"]).exit_code == 0
    with app.app_context():
        assert db.session.scalar(
            db.select(SchoolClass.is_active).where(SchoolClass.name == CLASS_NAMES[0])
        ) is False
        assert db.session.scalar(
            db.select(Room.is_active).where(Room.name == ROOM_NAMES[0])
        ) is False
        assert db.session.get(SystemSettings, 1).booking_queue_locked is True
        assert db.session.scalar(
            db.select(db.func.count()).select_from(SchoolClass)
        ) == len(CLASS_NAMES)
        assert db.session.scalar(db.select(db.func.count()).select_from(Room)) == len(
            ROOM_NAMES
        )


def test_seed_failure_rolls_back_safely(app, monkeypatch):
    with app.app_context():
        db.create_all()
        monkeypatch.setattr(
            db.session, "commit", lambda: (_ for _ in ()).throw(SQLAlchemyError())
        )
        result = app.test_cli_runner().invoke(args=["seed"])
        assert result.exit_code != 0
        assert "Unable to prepare seed data" in result.output
        assert db.session.scalar(
            db.select(db.func.count()).select_from(SchoolClass)
        ) == 0
        db.session.rollback()
        assert db.session().in_transaction() is False


def test_smoke_checks_success_without_mutation(monkeypatch):
    calls = []

    def fake_fetch(_base_url, path, *, follow_redirects=True):
        calls.append((path, follow_redirects))
        if path == "/health":
            return 200, b'{"status":"ok"}', {}
        if path == "/":
            return 200, b"Smart Class Management System", {}
        if path == "/auth/login":
            return 200, b"Login securely", {}
        if path == "/admin/":
            return 302, b"", {"Location": "/auth/login?next=/admin/"}
        if path == "/deployment-smoke-missing":
            return 404, b"Error 404 - Page not found", {}
        raise AssertionError("unexpected smoke path")

    monkeypatch.setattr(smoke_test, "fetch", fake_fetch)
    smoke_test.run_checks("https://service.example")
    expected_paths = {
        "/health",
        "/",
        "/auth/login",
        "/admin/",
        "/deployment-smoke-missing",
    }
    assert all(path in expected_paths for path, _ in calls)


def test_smoke_checks_reject_credentials_and_report_safe_failure(monkeypatch, capsys):
    assert smoke_test.main(["--base-url", "https://user:pass@service.example"]) == 1
    assert "pass@" not in capsys.readouterr().err

    monkeypatch.setattr(
        smoke_test,
        "fetch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
    )
    assert smoke_test.main(["--base-url", "https://service.example"]) == 1
    assert "network request could not be completed" in capsys.readouterr().err

    monkeypatch.setattr(
        smoke_test,
        "fetch",
        lambda *_args, **_kwargs: (200, None, None),
    )
    assert smoke_test.main(["--base-url", "https://service.example"]) == 1
    assert "malformed HTTP response" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value",
    [
        "http://service.example",
        "https://user:password@service.example",
        "https://service.example/prefix",
        "https://service.example?query=1",
        "https://service.example#fragment",
        "https:///missing-host",
        "https://service.example:invalid",
        "ftp://service.example",
    ],
)
def test_smoke_base_url_rejects_non_origin_values(value):
    with pytest.raises(ValueError):
        smoke_test.normalize_base_url(value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://service.example", "https://service.example"),
        ("https://SERVICE.example/", "https://service.example"),
        ("https://service.example:8443/", "https://service.example:8443"),
    ],
)
def test_smoke_base_url_normalizes_https_origins(value, expected):
    assert smoke_test.normalize_base_url(value) == expected


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/auth/login",
        "//evil.example/auth/login",
        "/",
        "/unrelated",
        "auth/login",
    ],
)
def test_smoke_rejects_unsafe_or_unrelated_protected_redirects(
    monkeypatch, location
):
    def fake_fetch(_base_url, path, *, follow_redirects=True):
        if path == "/health":
            return 200, b'{"status":"ok"}', {}
        if path == "/":
            return 200, smoke_test.PUBLIC_MARKER, {}
        if path == "/auth/login":
            return 200, smoke_test.LOGIN_MARKER, {}
        if path == "/admin/":
            return 302, b"", {"Location": location}
        raise AssertionError("smoke check should stop at protected redirect")

    monkeypatch.setattr(smoke_test, "fetch", fake_fetch)
    with pytest.raises(RuntimeError, match="unsafe redirect"):
        smoke_test.run_checks("https://service.example")


@pytest.mark.parametrize(
    "body",
    [
        b"ordinary server 404",
        b"Error 404 Traceback (most recent call last)",
        b"Error 404 Notifications",
        b"Error 404 sqlalchemy database failure",
        b"Error 404 C:\\private\\application.py",
    ],
)
def test_smoke_requires_safe_custom_404(monkeypatch, body):
    def fake_fetch(_base_url, path, *, follow_redirects=True):
        responses = {
            "/health": (200, b'{"status":"ok"}', {}),
            "/": (200, smoke_test.PUBLIC_MARKER, {}),
            "/auth/login": (200, smoke_test.LOGIN_MARKER, {}),
            "/admin/": (302, b"", {"Location": "/auth/login?next=/admin/"}),
            "/deployment-smoke-missing": (404, body, {}),
        }
        return responses[path]

    monkeypatch.setattr(smoke_test, "fetch", fake_fetch)
    with pytest.raises(RuntimeError):
        smoke_test.run_checks("https://service.example")


def test_environment_file_is_ignored_and_untracked():
    ignored = subprocess.run(
        ["git", "check-ignore", ".env"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ignored.returncode == 0
    assert tracked.returncode != 0
    assert subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env.example"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    ).returncode == 0


@pytest.mark.parametrize(
    "path,category",
    [
        (".env", "committed environment file"),
        (".env.local", "committed environment file"),
        (".env.production", "committed environment file"),
        ("nested/.env.staging", "committed environment file"),
        (".flaskenv", "committed Flask environment file"),
        (".env.example", None),
        ("settings.env", None),
        ("nested/ordinary.txt", None),
    ],
)
def test_secret_environment_filename_classification(path, category):
    assert secret_scan.secret_environment_category(path) == category


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".env.production",
        "nested/.env.staging",
        ".flaskenv",
    ],
)
def test_local_environment_variants_are_ignored(path):
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", path],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


def test_alembic_tree_has_one_unchanged_head():
    configuration = AlembicConfig(str(ROOT / "migrations" / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(configuration)
    assert scripts.get_heads() == ["453be70581b9"]
    revisions = list(scripts.walk_revisions())
    assert [revision.revision for revision in revisions] == ["453be70581b9"]
    assert list((ROOT / "migrations" / "versions").glob("*.py")) == [
        ROOT / "migrations" / "versions" / "453be70581b9_initial_database_schema.py"
    ]


def test_production_static_assets_are_database_independent(monkeypatch):
    app = production_app(monkeypatch)

    @app.get("/_deployment/static-url")
    def static_url_probe():
        return url_for("static", filename="app.css", _external=True)

    def database_access_forbidden(*_args, **_kwargs):
        raise AssertionError("static file serving attempted database access")

    monkeypatch.setattr(db.session, "get", database_access_forbidden)
    monkeypatch.setattr(db.session, "scalar", database_access_forbidden)
    monkeypatch.setattr(db.session, "execute", database_access_forbidden)
    assets = {
        "/static/app.css": b"--scms-primary",
        "/static/confirm.js": b"data-confirm",
    }
    before = {
        path: (ROOT / "app" / "static" / Path(path).name).stat().st_mtime_ns
        for path in assets
    }
    client = app.test_client()
    for path, marker in assets.items():
        response = client.get(path)
        assert response.status_code == 200
        assert marker in response.data
    generated = client.get(
        "/_deployment/static-url",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "app.test"},
    )
    assert generated.text == "https://app.test/static/app.css"
    missing = client.get("/static/deployment-missing.css")
    assert missing.status_code == 404
    lowered = missing.data.lower()
    assert b"c:\\" not in lowered
    assert b"/home/" not in lowered
    after = {
        path: (ROOT / "app" / "static" / Path(path).name).stat().st_mtime_ns
        for path in assets
    }
    assert after == before


def requirement_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "-r")):
            names.add(stripped.split("[", 1)[0].split("=", 1)[0].lower())
    return names


def test_production_and_development_requirements_are_separated():
    production = requirement_names(ROOT / "requirements.txt")
    development = requirement_names(ROOT / "requirements-dev.txt")
    assert {
        "alembic",
        "flask",
        "flask-login",
        "flask-migrate",
        "flask-sqlalchemy",
        "flask-wtf",
        "gunicorn",
        "psycopg",
        "python-dotenv",
        "sqlalchemy",
        "tzdata",
    } <= production
    assert {"pytest", "ruff", "bandit"}.isdisjoint(production)
    assert {"pytest", "ruff", "bandit"} <= development
    assert "-r requirements.txt" in (ROOT / "requirements-dev.txt").read_text(
        encoding="utf-8"
    )


def test_tracked_files_contain_no_focused_production_secrets():
    assert secret_scan.scan_tracked_files(ROOT) == []
    render_source = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "postgresql://" not in render_source
    assert "postgres://" not in render_source
