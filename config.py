"""Environment-based application configuration."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

load_dotenv()


class Config:
    """Shared configuration defaults."""

    SECRET_KEY = None
    SQLALCHEMY_DATABASE_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    APP_TIMEZONE = "Africa/Kigali"
    IS_PRODUCTION = False
    TRUST_RENDER_PROXY = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}


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
    TESTING = False
    IS_PRODUCTION = True
    TRUST_RENDER_PROXY = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    PREFERRED_URL_SCHEME = "https"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 5,
        "max_overflow": 2,
    }


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
    """Load named runtime configuration values from the environment."""
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


def validate_production_configuration(app) -> None:
    """Fail safely when production configuration is incomplete or unsafe."""
    if not app.config.get("IS_PRODUCTION"):
        return

    secret_key = app.config.get("SECRET_KEY")
    database_url = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not secret_key:
        raise RuntimeError("Missing required configuration: SECRET_KEY")
    if not database_url:
        raise RuntimeError("Missing required configuration: DATABASE_URL")

    normalized_secret = secret_key.strip().lower()
    placeholder_markers = (
        "replace-with",
        "change-me",
        "changeme",
        "development",
        "testing-only",
        "temporary-test",
    )
    obvious_placeholders = {
        "default",
        "dev",
        "password",
        "secret",
        "secret-key",
    }
    if (
        not secret_key.strip()
        or normalized_secret in obvious_placeholders
        or any(marker in normalized_secret for marker in placeholder_markers)
    ):
        raise RuntimeError("SECRET_KEY must be a non-placeholder production value")

    normalized_url = normalize_database_url(str(database_url))
    try:
        parsed_url = make_url(normalized_url)
        _ = parsed_url.port
    except (ArgumentError, TypeError, ValueError) as error:
        raise RuntimeError("Production DATABASE_URL is malformed") from error

    if parsed_url.drivername != "postgresql+psycopg":
        raise RuntimeError("Production DATABASE_URL must use PostgreSQL with psycopg")
    required_parts = {
        "hostname": parsed_url.host,
        "username": parsed_url.username,
        "password": parsed_url.password,
        "database name": parsed_url.database,
    }
    missing_parts = [name for name, value in required_parts.items() if not value]
    if missing_parts:
        raise RuntimeError(
            "Production DATABASE_URL is missing required connection information: "
            + ", ".join(missing_parts)
        )

    hostname = parsed_url.host.lower().rstrip(".")
    hostname_labels = hostname.split(".")
    if (
        hostname.endswith(".neon.tech")
        and hostname_labels[0].endswith("-pooler")
    ):
        raise RuntimeError(
            "The initial deployment requires a direct PostgreSQL connection; "
            "disable connection pooling."
        )

    sslmode_values = []
    for key, raw_value in parsed_url.query.items():
        if key.lower() != "sslmode":
            continue
        if isinstance(raw_value, tuple):
            sslmode_values.extend(raw_value)
        else:
            sslmode_values.append(raw_value)
    secure_sslmodes = {"require", "verify-ca", "verify-full"}
    if len(sslmode_values) != 1 or sslmode_values[0].lower() not in secure_sslmodes:
        raise RuntimeError(
            "Production DATABASE_URL must specify one secure PostgreSQL TLS mode."
        )

    app.config["SQLALCHEMY_DATABASE_URI"] = normalized_url
