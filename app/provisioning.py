"""Private, environment-driven first-deployment provisioning commands."""

import os

import click
from flask.cli import with_appcontext
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import User, UserRole
from app.user_validation import (
    validate_full_name,
    validate_temporary_password,
    validate_username,
)

ADMIN_ENVIRONMENT_NAMES = (
    "INITIAL_ADMIN_USERNAME",
    "INITIAL_ADMIN_FULL_NAME",
    "INITIAL_ADMIN_PASSWORD",
)


def validate_initial_admin_values(supplied: dict[str, str]) -> tuple[str, str, str]:
    """Apply the shared account policy to bootstrap environment values."""
    return (
        validate_username(supplied["INITIAL_ADMIN_USERNAME"]),
        validate_full_name(supplied["INITIAL_ADMIN_FULL_NAME"]),
        validate_temporary_password(supplied["INITIAL_ADMIN_PASSWORD"]),
    )


@click.command("provision-admin-from-env")
@with_appcontext
def provision_admin_from_env_command() -> None:
    """Idempotently provision the one initial Administrator from environment."""
    supplied = {name: os.getenv(name) for name in ADMIN_ENVIRONMENT_NAMES}
    present = {name: bool(value) for name, value in supplied.items()}
    if not any(present.values()):
        click.echo("Initial Administrator provisioning was not requested.")
        return
    if not all(present.values()):
        raise click.ClickException(
            "All initial Administrator environment variables are required together."
        )

    try:
        username, full_name, password = validate_initial_admin_values(supplied)
        existing = db.session.scalar(db.select(User).where(User.username == username))
        if existing is not None:
            if existing.role != UserRole.ADMIN:
                raise click.ClickException(
                    "The requested username belongs to a non-Administrator account."
                )
            db.session.rollback()
            click.echo("The requested Administrator already exists; no changes made.")
            return

        another_admin = db.session.scalar(
            db.select(User.id).where(User.role == UserRole.ADMIN).limit(1)
        )
        if another_admin is not None:
            raise click.ClickException(
                "An Administrator already exists; initial provisioning was not applied."
            )

        administrator = User(
            username=username,
            full_name=full_name,
            role=UserRole.ADMIN,
            class_id=None,
            is_active=True,
            must_change_password=True,
        )
        administrator.set_password(password)
        db.session.add(administrator)
        db.session.commit()
    except click.ClickException:
        db.session.rollback()
        raise
    except (SQLAlchemyError, ValueError) as error:
        db.session.rollback()
        raise click.ClickException(
            "Unable to provision the initial Administrator."
        ) from error

    click.echo("Initial Administrator provisioned successfully.")
