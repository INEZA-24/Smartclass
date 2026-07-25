"""Login, logout, and temporary-password routes."""

import re
from urllib.parse import unquote, urlsplit

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from app.authz import redirect_to_dashboard
from app.blueprints.auth import bp
from app.blueprints.auth.forms import ChangePasswordForm, LoginForm
from app.extensions import db
from app.models import User

INVALID_LOGIN_MESSAGE = "Invalid username or password."
PASSWORD_CHANGE_ERROR = "Unable to change password. Please try again."
DUMMY_PASSWORD_HASH = generate_password_hash("not-a-real-user-password")
MAX_REDIRECT_DECODE_PASSES = 3


def safe_internal_target(target: str | None) -> bool:
    """Return whether a redirect target is a well-formed internal path."""
    if not target:
        return False

    decoded = target
    for _decode_pass in range(MAX_REDIRECT_DECODE_PASSES):
        if re.search(r"%(?![0-9A-Fa-f]{2})", decoded):
            return False
        try:
            next_value = unquote(decoded, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return False
        if "\\" in next_value or any(ord(character) < 32 for character in next_value):
            return False
        if next_value == decoded:
            break
        decoded = next_value
    else:
        return False

    if not decoded.startswith("/") or decoded.startswith("//"):
        return False
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        return False
    return not parsed.scheme and not parsed.netloc


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if current_user.must_change_password:
            return redirect(url_for("auth.change_password"))
        return redirect_to_dashboard()

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(db.select(User).filter_by(username=form.username.data))
        eligible_user = user if user is not None and user.is_active else None
        password_hash = (
            eligible_user.password_hash
            if eligible_user is not None
            else DUMMY_PASSWORD_HASH
        )
        password_valid = check_password_hash(password_hash, form.password.data)
        if eligible_user is None or not password_valid:
            flash(INVALID_LOGIN_MESSAGE, "danger")
        else:
            login_user(eligible_user, remember=form.remember.data)
            if eligible_user.must_change_password:
                return redirect(url_for("auth.change_password"))
            target = request.args.get("next")
            if safe_internal_target(target):
                return redirect(target)
            return redirect_to_dashboard(eligible_user)
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("public.home"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if not current_user.must_change_password:
        return redirect_to_dashboard()
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Current password is incorrect.")
        elif current_user.check_password(form.new_password.data):
            form.new_password.errors.append("New password must be different.")
        else:
            current_user.set_password(form.new_password.data)
            current_user.must_change_password = False
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                flash(PASSWORD_CHANGE_ERROR, "danger")
            else:
                return redirect_to_dashboard()
    return render_template("auth/change_password.html", form=form)
