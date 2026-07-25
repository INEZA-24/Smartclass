"""Scheduler dashboard placeholder."""

from flask import render_template
from flask_login import current_user

from app.authz import ROLE_LABELS, role_required
from app.blueprints.scheduler import bp
from app.models import UserRole


@bp.get("/")
@role_required(UserRole.SCHEDULER)
def dashboard():
    return render_template(
        "dashboard.html",
        user=current_user,
        role_label=ROLE_LABELS[UserRole.SCHEDULER],
    )
