"""Unbound Flask extensions initialized by the application factory."""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()


@login_manager.user_loader
def load_user(user_id: str):
    """Load active users for Flask-Login sessions."""
    from app.models import User

    if not user_id.isdigit():
        return None
    user = db.session.get(User, int(user_id), populate_existing=True)
    return user if user is not None and user.is_active else None
