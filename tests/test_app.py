"""Application scaffold tests."""

import pytest

from app import create_app


def test_application_factory_creates_app(app):
    assert app.name == "app"
    assert app.config["TESTING"] is True


def test_required_blueprints_are_registered(app):
    assert {"public", "auth", "admin", "scheduler", "requester"}.issubset(
        app.blueprints
    )


def test_named_testing_configuration():
    app = create_app("testing")

    assert app.config["TESTING"] is True
    assert app.config["SECRET_KEY"] == "testing-only-key"
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"


def test_unknown_configuration_name_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "developmnt")

    with pytest.raises(ValueError, match="Unknown configuration 'developmnt'"):
        create_app()


def test_missing_secret_key_is_rejected(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/scms")

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app("development")


def test_missing_database_url_is_rejected(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "temporary-test-value")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app("production")


@pytest.mark.parametrize(
    ("provider_url", "expected_url"),
    [
        (
            "postgres://user:password@host/database?sslmode=require",
            "postgresql+psycopg://user:password@host/database?sslmode=require",
        ),
        (
            "postgresql://user:password@host/database?sslmode=require",
            "postgresql+psycopg://user:password@host/database?sslmode=require",
        ),
        (
            "postgresql+psycopg://user:password@host/database?sslmode=require",
            "postgresql+psycopg://user:password@host/database?sslmode=require",
        ),
    ],
)
def test_postgresql_url_normalization(monkeypatch, provider_url, expected_url):
    monkeypatch.setenv("SECRET_KEY", "temporary-test-value")
    monkeypatch.setenv("DATABASE_URL", provider_url)

    app = create_app("development")

    assert app.config["SQLALCHEMY_DATABASE_URI"] == expected_url
