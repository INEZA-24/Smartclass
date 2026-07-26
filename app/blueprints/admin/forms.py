"""Forms for Administrator-managed records."""

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.validators import DataRequired, EqualTo, Length, Optional, ValidationError

from app.blueprints.auth.forms import MINIMUM_PASSWORD_LENGTH
from app.models import UserRole


def required_trimmed(form, field):
    """Reject values containing only whitespace."""
    if not field.data or not field.data.strip():
        raise ValidationError("This field is required.")


ROLE_CHOICES = [
    (UserRole.ADMIN.value, "Administrator"),
    (UserRole.SCHEDULER.value, "Patron/Matron"),
    (UserRole.TEACHER.value, "Teacher"),
    (UserRole.MONITOR.value, "Class Monitor"),
]


class UserBaseForm(FlaskForm):
    full_name = StringField(
        "Full name", validators=[DataRequired(), Length(max=150), required_trimmed]
    )
    username = StringField(
        "Username", validators=[DataRequired(), Length(max=80), required_trimmed]
    )
    role = SelectField("Role", choices=ROLE_CHOICES, validators=[DataRequired()])
    class_id = SelectField(
        "Assigned class",
        coerce=int,
        validators=[Optional()],
        choices=[],
        validate_choice=False,
    )
    is_active = BooleanField("Active", default=True)


class UserCreateForm(UserBaseForm):
    temporary_password = PasswordField(
        "Temporary password",
        validators=[DataRequired(), Length(min=MINIMUM_PASSWORD_LENGTH)],
    )
    confirm_password = PasswordField(
        "Confirm temporary password",
        validators=[DataRequired(), EqualTo("temporary_password")],
    )
    submit = SubmitField("Create user")


class UserEditForm(UserBaseForm):
    submit = SubmitField("Save user")


class TemporaryPasswordForm(FlaskForm):
    temporary_password = PasswordField(
        "Temporary password",
        validators=[DataRequired(), Length(min=MINIMUM_PASSWORD_LENGTH)],
    )
    confirm_password = PasswordField(
        "Confirm temporary password",
        validators=[DataRequired(), EqualTo("temporary_password")],
    )
    submit = SubmitField("Issue temporary password")


class NamedRecordEditForm(FlaskForm):
    name = StringField(
        "Name", validators=[DataRequired(), Length(max=80), required_trimmed]
    )
    submit = SubmitField("Save")


class NamedRecordCreateForm(NamedRecordEditForm):
    is_active = BooleanField("Active", default=True)


class ActionForm(FlaskForm):
    submit = SubmitField("Confirm")
