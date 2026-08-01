"""Scheduler and schedule-block forms."""

from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class ScheduleRequestForm(FlaskForm):
    schedule_date = DateField("Schedule date", validators=[DataRequired()])
    prep = SelectField("Prep", validators=[DataRequired()])
    room_id = SelectField("Room", coerce=int, validators=[DataRequired()])
    submit = SubmitField("Schedule request")


class BlockForm(FlaskForm):
    scope = SelectField("Block scope", validators=[DataRequired()])
    room_id = SelectField("Room", coerce=int, validators=[Optional()])
    prep = SelectField("Prep", validators=[Optional()])
    reason = TextAreaField("Internal reason", validators=[Optional(), Length(max=2000)])
    submit = SubmitField("Create block")


class ActionForm(FlaskForm):
    submit = SubmitField("Confirm")
