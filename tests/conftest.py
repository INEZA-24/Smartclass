"""Shared pytest fixtures."""

import pytest

from app import create_app


@pytest.fixture()
def app():
    """Create an isolated application for each test."""
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
        }
    )


@pytest.fixture()
def client(app):
    """Create a Flask test client."""
    return app.test_client()
