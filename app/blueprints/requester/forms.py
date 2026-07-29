"""Booking request forms with no server-controlled identity fields."""

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, ValidationError


def required_trimmed(_form, field):
    if not field.data or not field.data.strip():
        raise ValidationError("This field is required.")


class RequestContentForm(FlaskForm):
    subject = StringField(
        "Subject", validators=[DataRequired(), Length(max=120), required_trimmed]
    )
    reason = TextAreaField(
        "Private reason", validators=[DataRequired(), required_trimmed]
    )
    submit = SubmitField("Save request")


class TeacherRequestForm(RequestContentForm):
    class_id = SelectField("Class", coerce=int, choices=[], validate_choice=False)


class MonitorRequestForm(RequestContentForm):
    teacher_id = SelectField(
        "Responsible teacher", coerce=int, choices=[], validate_choice=False
    )


class CancelRequestForm(FlaskForm):
    submit = SubmitField("Cancel request")
