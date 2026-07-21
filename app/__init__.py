"""Application factory for the Smart Class Management System."""

from __future__ import annotations

import os
from typing import Any

from flask import Flask

from app.blueprints.admin import bp as admin_bp
from app.blueprints.auth import bp as auth_bp
from app.blueprints.public import bp as public_bp
from app.blueprints.requester import bp as requester_bp
from app.blueprints.scheduler import bp as scheduler_bp
from app.extensions import csrf, db, login_manager, migrate
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
    csrf.init_app(app)

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(scheduler_bp, url_prefix="/scheduler")
    app.register_blueprint(requester_bp, url_prefix="/requester")

    return app
