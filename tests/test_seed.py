"""Seed command tests."""

from app.extensions import db
from app.models import Room, SchoolClass, SystemSettings
from app.seed import CLASS_NAMES, ROOM_NAMES


def test_seed_command_is_idempotent(app):
    runner = app.test_cli_runner()
    with app.app_context():
        db.create_all()
    assert runner.invoke(args=["seed"]).exit_code == 0
    assert runner.invoke(args=["seed"]).exit_code == 0
    with app.app_context():
        assert db.session.scalar(
            db.select(db.func.count()).select_from(SchoolClass)
        ) == len(CLASS_NAMES)
        assert db.session.scalar(db.select(db.func.count()).select_from(Room)) == len(
            ROOM_NAMES
        )
        assert (
            db.session.scalar(db.select(db.func.count()).select_from(SystemSettings))
            == 1
        )
