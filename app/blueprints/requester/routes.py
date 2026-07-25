"""Requester dashboard placeholders."""

from flask import render_template
from flask_login import current_user

from app.authz import ROLE_LABELS, role_required
from app.blueprints.requester import bp
from app.models import UserRole


@bp.get("/teacher")
@role_required(UserRole.TEACHER)
def teacher_dashboard():
    return render_template(
        "dashboard.html", user=current_user, role_label=ROLE_LABELS[UserRole.TEACHER]
    )


@bp.get("/monitor")
@role_required(UserRole.MONITOR)
def monitor_dashboard():
    return render_template(
        "dashboard.html", user=current_user, role_label=ROLE_LABELS[UserRole.MONITOR]
    )
