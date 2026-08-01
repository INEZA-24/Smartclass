"""Administrator and Scheduler report routes."""

from datetime import timedelta

from flask import render_template
from sqlalchemy.orm import aliased

from app.authz import roles_required
from app.blueprints.reports import bp
from app.extensions import db
from app.models import (
    BookingRequest,
    PrepPeriod,
    RequestStatus,
    Room,
    ScheduledBooking,
    SchoolClass,
    User,
    UserRole,
)
from app.reports import (
    ORIGIN_LABELS,
    booking_rows,
    default_range,
    display_kigali,
    kigali_utc_bounds,
    parse_filters,
    usage_rows,
    week_bounds,
)
from app.scheduling import PREP_LABELS, kigali_today

REPORT_ROLES = (UserRole.ADMIN, UserRole.SCHEDULER)


def reference_choices(model):
    return db.session.scalars(db.select(model).order_by(model.name, model.id)).all()


def totals(rows):
    return {
        "total": len(rows),
        "prep_1": sum(row.prep == PrepPeriod.PREP_1 for row in rows),
        "prep_2": sum(row.prep == PrepPeriod.PREP_2 for row in rows),
    }


@bp.get("/")
@roles_required(*REPORT_ROLES)
def index():
    return render_template("reports/index.html")


@bp.get("/daily")
@roles_required(*REPORT_ROLES)
def daily():
    filters = parse_filters({"date"}, {"date": kigali_today()})
    selected = filters.values["date"] if filters.valid else None
    rows = booking_rows(selected, selected) if selected is not None else []
    return render_template(
        "reports/bookings.html",
        title="Daily Bookings",
        rows=rows,
        filters=filters,
        date_value=selected,
        summary=totals(rows),
        prep_labels=PREP_LABELS,
        origin_labels=ORIGIN_LABELS,
    )


@bp.get("/weekly")
@roles_required(*REPORT_ROLES)
def weekly():
    filters = parse_filters({"date"}, {"date": kigali_today()})
    anchor = filters.values["date"] if filters.valid else None
    if anchor is not None:
        start, end = week_bounds(anchor)
        rows = booking_rows(start, end)
        days = [(start + timedelta(days=offset)) for offset in range(7)]
    else:
        start = end = None
        rows = []
        days = []
    return render_template(
        "reports/bookings.html",
        title="Weekly Bookings",
        rows=rows,
        filters=filters,
        date_value=anchor,
        range_start=start,
        range_end=end,
        days=days,
        day_counts={day: sum(row.schedule_date == day for row in rows) for day in days},
        summary=totals(rows),
        prep_labels=PREP_LABELS,
        origin_labels=ORIGIN_LABELS,
    )


@bp.get("/history")
@roles_required(*REPORT_ROLES)
def history():
    filters = parse_filters(
        {"start_date", "end_date", "status", "room_id", "class_id", "origin"}
    )
    requester = aliased(User)
    teacher = aliased(User)
    statement = (
        db.select(
            BookingRequest.id,
            BookingRequest.created_at,
            BookingRequest.priority,
            BookingRequest.status,
            BookingRequest.subject,
            BookingRequest.cancelled_at.label("request_cancelled_at"),
            requester.full_name.label("requester_name"),
            teacher.full_name.label("teacher_name"),
            SchoolClass.name.label("class_name"),
            ScheduledBooking.schedule_date,
            ScheduledBooking.prep,
            ScheduledBooking.is_active.label("booking_active"),
            ScheduledBooking.cancelled_at.label("booking_cancelled_at"),
            Room.name.label("room_name"),
        )
        .join(requester, requester.id == BookingRequest.requester_id)
        .join(teacher, teacher.id == BookingRequest.teacher_id)
        .join(SchoolClass, SchoolClass.id == BookingRequest.class_id)
        .outerjoin(ScheduledBooking, ScheduledBooking.request_id == BookingRequest.id)
        .outerjoin(Room, Room.id == ScheduledBooking.room_id)
        .order_by(BookingRequest.created_at.desc(), BookingRequest.id.desc())
    )
    values = filters.values
    if values.get("start_date") and values.get("end_date"):
        start, end = kigali_utc_bounds(values["start_date"], values["end_date"])
        statement = statement.where(
            BookingRequest.created_at >= start, BookingRequest.created_at < end
        )
    elif values.get("start_date"):
        start, _end = kigali_utc_bounds(values["start_date"], values["start_date"])
        statement = statement.where(BookingRequest.created_at >= start)
    elif values.get("end_date"):
        _start, end = kigali_utc_bounds(values["end_date"], values["end_date"])
        statement = statement.where(BookingRequest.created_at < end)
    if values.get("status"):
        statement = statement.where(BookingRequest.status == values["status"])
    if values.get("room_id"):
        statement = statement.where(ScheduledBooking.room_id == values["room_id"])
    if values.get("class_id"):
        statement = statement.where(BookingRequest.class_id == values["class_id"])
    if values.get("origin"):
        statement = statement.where(BookingRequest.priority == values["origin"])
    rows = db.session.execute(statement).all() if filters.valid else []
    return render_template(
        "reports/history.html",
        rows=rows,
        filters=filters,
        rooms=reference_choices(Room),
        classes=reference_choices(SchoolClass),
        statuses=tuple(RequestStatus),
        origin_labels=ORIGIN_LABELS,
        prep_labels=PREP_LABELS,
        display_kigali=display_kigali,
    )


def usage_report(model, foreign_key, title, template_name):
    start_default, end_default = default_range()
    id_name = "room_id" if model is Room else "class_id"
    filters = parse_filters(
        {"start_date", "end_date", id_name},
        {"start_date": start_default, "end_date": end_default},
    )
    values = filters.values
    rows = (
        usage_rows(
            model,
            foreign_key,
            values["start_date"],
            values["end_date"],
            values.get(id_name),
        )
        if filters.valid
        else []
    )
    return render_template(
        "reports/usage.html",
        title=title,
        rows=rows,
        filters=filters,
        id_name=id_name,
        choices=reference_choices(model),
        grand_total=sum(row.total for row in rows),
        template_name=template_name,
    )


@bp.get("/rooms")
@roles_required(*REPORT_ROLES)
def rooms():
    return usage_report(Room, ScheduledBooking.room_id, "Room Usage", "rooms")


@bp.get("/classes")
@roles_required(*REPORT_ROLES)
def classes():
    return usage_report(
        SchoolClass, ScheduledBooking.class_id, "Class Usage", "classes"
    )
