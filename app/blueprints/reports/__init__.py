"""Read-only reports blueprint."""

from flask import Blueprint

bp = Blueprint("reports", __name__)

from app.blueprints.reports import routes  # noqa: E402, F401
