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

from app.models import UserRole
from app.user_validation import (
    validate_full_name,
    validate_temporary_password,
    validate_username,
)


def required_trimmed(form, field):
    """Reject values containing only whitespace."""
    if not field.data or not field.data.strip():
        raise ValidationError("This field is required.")


def account_username(_form, field):
    try:
        validate_username(field.data)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def account_full_name(_form, field):
    try:
        validate_full_name(field.data)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def account_temporary_password(_form, field):
    try:
        validate_temporary_password(field.data)
    except ValueError as error:
        raise ValidationError(str(error)) from error


ROLE_CHOICES = [
    (UserRole.ADMIN.value, "Administrator"),
    (UserRole.SCHEDULER.value, "Patron/Matron"),
    (UserRole.TEACHER.value, "Teacher"),
    (UserRole.MONITOR.value, "Class Monitor"),
]


class UserBaseForm(FlaskForm):
    full_name = StringField(
        "Full name",
        validators=[
            DataRequired(),
            account_full_name,
        ],
    )
    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            account_username,
        ],
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
        validators=[DataRequired(), account_temporary_password],
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
        validators=[DataRequired(), account_temporary_password],
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
