"""Environment-based application configuration."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Shared configuration defaults."""

    SECRET_KEY = None
    SQLALCHEMY_DATABASE_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    APP_TIMEZONE = "Africa/Kigali"


class DevelopmentConfig(Config):
    """Local development configuration."""

    DEBUG = True


class TestingConfig(Config):
    """Automated test configuration."""

    TESTING = True
    SECRET_KEY = "testing-only-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


config_by_name = {
    "default": DevelopmentConfig,
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def normalize_database_url(database_url: str) -> str:
    """Normalize supported PostgreSQL URLs for the psycopg driver."""
    psycopg_scheme = "postgresql+psycopg://"

    if database_url.startswith(psycopg_scheme):
        return database_url
    if database_url.startswith("postgres://"):
        return psycopg_scheme + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return psycopg_scheme + database_url.removeprefix("postgresql://")
    return database_url


def apply_runtime_environment(app) -> None:
    """Load and validate runtime-only configuration values."""
    if app.config.get("TESTING"):
        return

    secret_key = os.getenv("SECRET_KEY")
    database_url = os.getenv("DATABASE_URL")
    missing = []

    if not secret_key:
        missing.append("SECRET_KEY")
    if not database_url:
        missing.append("DATABASE_URL")
    if missing:
        required = ", ".join(missing)
        raise RuntimeError(f"Missing required configuration: {required}")

    app.config["SECRET_KEY"] = secret_key
    app.config["SQLALCHEMY_DATABASE_URI"] = normalize_database_url(database_url)
