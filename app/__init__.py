"""Application factory for the Smart Class Management System."""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, redirect, render_template, request, url_for
from flask_login import current_user, logout_user

from app.authz import dashboard_url
from app.blueprints.admin import bp as admin_bp
from app.blueprints.auth import bp as auth_bp
from app.blueprints.notifications import bp as notifications_bp
from app.blueprints.public import bp as public_bp
from app.blueprints.reports import bp as reports_bp
from app.blueprints.requester import bp as requester_bp
from app.blueprints.scheduler import bp as scheduler_bp
from app.extensions import csrf, db, login_manager, migrate
from app.models import Notification, User
from app.seed import seed_command
from config import apply_runtime_environment, config_by_name, normalize_database_url


def create_app(config: str | dict[str, Any] | None = None) -> Flask:
    """Create and configure a Flask application instance."""
    app = Flask(__name__)

    if isinstance(config, dict):
        app.config.from_object(config_by_name["default"])
        app.config.from_mapping(config)
        database_url = app.config.get("SQLALCHEMY_DATABASE_URI")
        if database_url:
            app.config["SQLALCHEMY_DATABASE_URI"] = normalize_database_url(database_url)
    else:
        config_name = config or os.getenv("APP_ENV", "development")
        if config_name not in config_by_name:
            supported = ", ".join(sorted(config_by_name))
            raise ValueError(
                f"Unknown configuration {config_name!r}. Supported values: {supported}"
            )
        selected_config = config_by_name[config_name]
        app.config.from_object(selected_config)
        apply_runtime_environment(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    csrf.init_app(app)
    app.cli.add_command(seed_command)

    from app import models  # noqa: F401

    app.register_blueprint(public_bp)
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(scheduler_bp, url_prefix="/scheduler")
    app.register_blueprint(requester_bp, url_prefix="/requester")
    app.jinja_env.globals["dashboard_url"] = dashboard_url

    @app.context_processor
    def notification_navigation():
        if not current_user.is_authenticated or current_user.must_change_password:
            return {"notification_unread_count": 0}
        count = db.session.scalar(
            db.select(db.func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == current_user.id,
                Notification.is_read.is_(False),
            )
        )
        return {"notification_unread_count": count}

    @app.before_request
    def enforce_temporary_password_change():
        if current_user.is_authenticated:
            user = db.session.get(
                User, int(current_user.get_id()), populate_existing=True
            )
            if user is None or not user.is_active:
                logout_user()
                return redirect(url_for("auth.login"))
        allowed = {"auth.change_password", "auth.logout", "static"}
        if (
            current_user.is_authenticated
            and current_user.must_change_password
            and request.endpoint not in allowed
        ):
            return redirect(url_for("auth.change_password"))
        return None

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    return app
