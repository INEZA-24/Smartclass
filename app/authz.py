"""Authentication and role authorization helpers."""

from collections.abc import Callable
from functools import wraps

from flask import abort, redirect, url_for
from flask_login import current_user, login_required

from app.models import UserRole

ROLE_LABELS = {
    UserRole.ADMIN: "Administrator",
    UserRole.SCHEDULER: "Patron/Matron",
    UserRole.TEACHER: "Teacher",
    UserRole.MONITOR: "Class Monitor",
}

ROLE_DASHBOARDS = {
    UserRole.ADMIN: "admin.dashboard",
    UserRole.SCHEDULER: "scheduler.dashboard",
    UserRole.TEACHER: "requester.teacher_dashboard",
    UserRole.MONITOR: "requester.monitor_dashboard",
}


def dashboard_url(user=None) -> str:
    """Return the correct dashboard URL for a user."""
    active_user = user or current_user
    return url_for(ROLE_DASHBOARDS[active_user.role])


def redirect_to_dashboard(user=None):
    """Redirect a user to their role dashboard."""
    return redirect(dashboard_url(user))


def role_required(role: UserRole) -> Callable:
    """Require authentication and one exact internal role."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
