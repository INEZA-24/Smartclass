"""Public endpoint tests."""

import pytest

from app.extensions import db


@pytest.fixture(autouse=True)
def database(app):
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


def test_home_page(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Smart Class Management System" in response.data
    assert b"bootstrap@5" in response.data


def test_health_route(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
