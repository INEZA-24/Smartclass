"""Authentication forms."""

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length

from app.user_validation import MINIMUM_PASSWORD_LENGTH


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember me")
    submit = SubmitField("Login")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current temporary password", validators=[DataRequired()]
    )
    new_password = PasswordField(
        "New password",
        validators=[DataRequired(), Length(min=MINIMUM_PASSWORD_LENGTH)],
    )
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password")],
    )
    submit = SubmitField("Change password")
