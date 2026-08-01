"""Shared validation and query helpers for read-only reports."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import request
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    BookingRequest,
    PrepPeriod,
    RequestPriority,
    RequestStatus,
    Room,
    ScheduledBooking,
    SchoolClass,
    User,
)
from app.scheduling import kigali_today

KIGALI = ZoneInfo("Africa/Kigali")
MIN_SAFE_REPORT_DATE = date(1, 1, 8)
MAX_SAFE_REPORT_DATE = date(9999, 12, 24)
ORIGIN_LABELS = {
    RequestPriority.HIGH: "Teacher",
    RequestPriority.NORMAL: "Class Monitor",
}


@dataclass
class ReportFilters:
    values: dict = field(default_factory=dict)
    raw_values: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self):
        return not self.errors


def _one_arg(name, allowed, filters):
    if name not in allowed:
        return None
    values = request.args.getlist(name)
    if len(values) > 1:
        filters.errors.append(f"Provide only one {name.replace('_', ' ')} value.")
        return None
    return values[0] if values else None


def parse_date_value(value, label, filters, default=None):
    if value in (None, ""):
        return default
    if (
        type(value) is not str
        or len(value) != 10
        or value[4] != "-"
        or value[7] != "-"
        or not (value[:4] + value[5:7] + value[8:]).isascii()
        or not (value[:4] + value[5:7] + value[8:]).isdigit()
    ):
        filters.errors.append(f"{label} must use YYYY-MM-DD.")
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        filters.errors.append(f"{label} must use YYYY-MM-DD.")
        return None
    if parsed.isoformat() != value:
        filters.errors.append(f"{label} must use YYYY-MM-DD.")
        return None
    if not MIN_SAFE_REPORT_DATE <= parsed <= MAX_SAFE_REPORT_DATE:
        filters.errors.append(f"{label} is outside the supported report range.")
        return None
    return parsed


def parse_positive_id(value, label, model, filters):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        filters.errors.append(f"Select a valid {label}.")
        return None
    identifier = int(value)
    if identifier <= 0 or db.session.get(model, identifier) is None:
        filters.errors.append(f"Select a valid {label}.")
        return None
    return identifier


def parse_filters(allowed, defaults=None):
    defaults = defaults or {}
    filters = ReportFilters()
    unexpected = sorted(set(request.args) - set(allowed))
    if unexpected:
        filters.errors.append("Remove unsupported report filters.")
    raw = {name: _one_arg(name, allowed, filters) for name in allowed}
    filters.raw_values.update(raw)
    for name in ("date", "start_date", "end_date"):
        if name in allowed:
            filters.values[name] = parse_date_value(
                raw[name], name.replace("_", " ").title(), filters, defaults.get(name)
            )
    if "room_id" in allowed:
        filters.values["room_id"] = parse_positive_id(
            raw["room_id"], "room", Room, filters
        )
    if "class_id" in allowed:
        filters.values["class_id"] = parse_positive_id(
            raw["class_id"], "class", SchoolClass, filters
        )
    if "status" in allowed:
        try:
            filters.values["status"] = (
                RequestStatus(raw["status"]) if raw["status"] else None
            )
        except ValueError:
            filters.errors.append("Select a valid request status.")
            filters.values["status"] = None
    if "origin" in allowed:
        origins = {"TEACHER": RequestPriority.HIGH, "MONITOR": RequestPriority.NORMAL}
        filters.values["origin"] = origins.get(raw["origin"])
        if raw["origin"] and raw["origin"] not in origins:
            filters.errors.append("Select Teacher or Class Monitor as the origin.")
    start = filters.values.get("start_date")
    end = filters.values.get("end_date")
    if start and end and start > end:
        filters.errors.append("Start date must not be later than end date.")
    return filters


def week_bounds(anchor):
    if (
        type(anchor) is not date
        or not MIN_SAFE_REPORT_DATE <= anchor <= MAX_SAFE_REPORT_DATE
    ):
        raise ValueError("Date is outside the supported report range.")
    try:
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    except OverflowError as exc:
        raise ValueError("Date is outside the supported report range.") from exc


def kigali_utc_bounds(start_date, end_date):
    if (
        type(start_date) is not date
        or type(end_date) is not date
        or not MIN_SAFE_REPORT_DATE <= start_date <= MAX_SAFE_REPORT_DATE
        or not MIN_SAFE_REPORT_DATE <= end_date <= MAX_SAFE_REPORT_DATE
    ):
        raise ValueError("Date range is outside the supported report range.")
    try:
        start = datetime.combine(start_date, time.min, tzinfo=KIGALI).astimezone(UTC)
        end = datetime.combine(
            end_date + timedelta(days=1), time.min, tzinfo=KIGALI
        ).astimezone(UTC)
        return start, end
    except (OverflowError, ValueError) as exc:
        raise ValueError("Date range is outside the supported report range.") from exc


def display_kigali(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(KIGALI)


def booking_rows(start_date, end_date):
    teacher = aliased(User)
    prep_order = db.case(
        (ScheduledBooking.prep == PrepPeriod.PREP_1, 1),
        (ScheduledBooking.prep == PrepPeriod.PREP_2, 2),
        else_=3,
    )
    return db.session.execute(
        db.select(
            ScheduledBooking.id,
            ScheduledBooking.request_id,
            ScheduledBooking.schedule_date,
            ScheduledBooking.prep,
            Room.name.label("room_name"),
            SchoolClass.name.label("class_name"),
            teacher.full_name.label("teacher_name"),
            BookingRequest.priority,
            BookingRequest.subject,
        )
        .join(BookingRequest, BookingRequest.id == ScheduledBooking.request_id)
        .join(Room, Room.id == ScheduledBooking.room_id)
        .join(SchoolClass, SchoolClass.id == ScheduledBooking.class_id)
        .join(teacher, teacher.id == ScheduledBooking.teacher_id)
        .where(
            ScheduledBooking.is_active.is_(True),
            ScheduledBooking.schedule_date >= start_date,
            ScheduledBooking.schedule_date <= end_date,
        )
        .order_by(
            ScheduledBooking.schedule_date,
            prep_order,
            Room.name,
            SchoolClass.name,
            ScheduledBooking.id,
        )
    ).all()


def usage_rows(model, foreign_key, start_date, end_date, selected_id=None):
    prep_1 = db.func.sum(
        db.case((ScheduledBooking.prep == PrepPeriod.PREP_1, 1), else_=0)
    )
    prep_2 = db.func.sum(
        db.case((ScheduledBooking.prep == PrepPeriod.PREP_2, 1), else_=0)
    )
    total = db.func.count(ScheduledBooking.id)
    statement = (
        db.select(
            model.id,
            model.name,
            model.is_active,
            total.label("total"),
            prep_1.label("prep_1"),
            prep_2.label("prep_2"),
        )
        .join(ScheduledBooking, foreign_key == model.id)
        .where(
            ScheduledBooking.is_active.is_(True),
            ScheduledBooking.schedule_date >= start_date,
            ScheduledBooking.schedule_date <= end_date,
        )
        .group_by(model.id, model.name, model.is_active)
        .order_by(total.desc(), model.name, model.id)
    )
    if selected_id is not None:
        statement = statement.where(model.id == selected_id)
    return db.session.execute(statement).all()


def default_range():
    return week_bounds(kigali_today())
