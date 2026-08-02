"""Idempotent seed data command."""

import click
from flask.cli import with_appcontext
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import Room, SchoolClass, SystemSettings

CLASS_NAMES = [
    *(f"S{level} {section}" for level in range(1, 4) for section in "ABCD"),
    *(
        f"S{level} {track} {section}"
        for level in range(4, 6)
        for track in ("MSI", "MSII")
        for section in "ABCD"
    ),
    "S6 MPC",
    "S6 MPG",
    "S6 MCB",
    "S6 PCB",
    "S6 PCM",
]
ROOM_NAMES = ["Smart Class 1", "Smart Class 2", "Smart Class 3"]


@click.command("seed")
@with_appcontext
def seed_command():
    """Insert required reference data without creating duplicates."""
    try:
        for name in CLASS_NAMES:
            if db.session.scalar(db.select(SchoolClass).filter_by(name=name)) is None:
                db.session.add(SchoolClass(name=name))
        for name in ROOM_NAMES:
            if db.session.scalar(db.select(Room).filter_by(name=name)) is None:
                db.session.add(Room(name=name))
        if db.session.get(SystemSettings, 1) is None:
            db.session.add(SystemSettings(id=1))
        db.session.commit()
    except (SQLAlchemyError, ValueError) as error:
        db.session.rollback()
        raise click.ClickException("Unable to prepare seed data.") from error
    click.echo("Seed data is ready.")
