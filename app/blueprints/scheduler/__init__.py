"""Scheduler blueprint placeholder for later milestones."""

from flask import Blueprint

bp = Blueprint("scheduler", __name__)

from app.blueprints.scheduler import routes  # noqa: E402, F401
