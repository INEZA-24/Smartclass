"""Requester blueprint placeholder for later milestones."""

from flask import Blueprint

bp = Blueprint("requester", __name__)

from app.blueprints.requester import routes  # noqa: E402, F401
