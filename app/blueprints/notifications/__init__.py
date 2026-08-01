"""Authenticated notification-center blueprint."""

from flask import Blueprint

bp = Blueprint("notifications", __name__)

from app.blueprints.notifications import routes  # noqa: E402, F401
