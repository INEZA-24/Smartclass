"""Scheduler dashboard, pending queue, scheduling, and availability blocks."""

from datetime import date

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.authz import ROLE_LABELS, role_required, roles_required
from app.blueprints.scheduler import bp
from app.blueprints.scheduler.forms import (
    ActionForm,
    BlockForm,
    RejectionForm,
    ScheduleRequestForm,
)
from app.booking_queue import queue_state, request_origin_role
from app.extensions import db
from app.models import (
    BlockScope,
    BookingRequest,
    PrepPeriod,
    RequestPriority,
    RequestStatus,
    Room,
    ScheduledBooking,
    UserRole,
)
from app.scheduling import (
    PREP_LABELS,
    SchedulingError,
    cancel_booking,
    create_block,
    planning_window,
    reject_request,
    remove_block,
    require_planning_date,
    reschedule_booking,
    schedule_request,
    slot_states,
)

GENERIC_ERROR = "Unable to save the schedule change. Please try again."


def _parse_date(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        abort(404)
    try:
        return require_planning_date(parsed)
    except SchedulingError:
        abort(404)


def _active_room_choices():
    return [
        (room.id, room.name)
        for room in db.session.scalars(
            db.select(Room).where(Room.is_active.is_(True)).order_by(Room.name)
        ).all()
    ]


def _prep_choices(blank=False):
    choices = [(item.value, PREP_LABELS[item]) for item in PrepPeriod]
    return ([("", "Not applicable")] if blank else []) + choices


def _booking_can_be_modified(booking):
    request_record = booking.request
    if (
        not booking.is_active
        or request_record is None
        or request_record.status != RequestStatus.SCHEDULED
        or booking.class_id != request_record.class_id
        or booking.teacher_id != request_record.teacher_id
    ):
        return False
    return (
        db.session.scalar(
            db.select(ScheduledBooking.id).where(
                ScheduledBooking.request_id == booking.request_id,
                ScheduledBooking.id != booking.id,
            )
        )
        is None
    )


@bp.get("/")
@role_required(UserRole.SCHEDULER)
def dashboard():
    settings, count = queue_state()
    days = []
    for item in planning_window():
        rooms, states = slot_states(item)
        counts = {"Available": 0, "Booked": 0, "Unavailable": 0}
        for state, _record in states.values():
            counts[state] += 1
        days.append({"date": item, "counts": counts, "capacity": len(rooms) * 2})
    return render_template(
        "scheduler/dashboard.html",
        user=current_user,
        role_label=ROLE_LABELS[UserRole.SCHEDULER],
        settings=settings,
        pending_count=count,
        days=days,
    )


@bp.get("/pending")
@role_required(UserRole.SCHEDULER)
def pending_queue():
    priority_order = db.case(
        (BookingRequest.priority == RequestPriority.HIGH, 0), else_=1
    )
    records = db.session.scalars(
        db.select(BookingRequest)
        .where(BookingRequest.status == RequestStatus.PENDING)
        .order_by(priority_order, BookingRequest.created_at, BookingRequest.id)
    ).all()
    settings, count = queue_state()
    return render_template(
        "scheduler/pending.html",
        requests=records,
        origin_labels={
            record.id: ROLE_LABELS[request_origin_role(record.priority)]
            for record in records
        },
        settings=settings,
        pending_count=count,
    )


@bp.get("/schedule")
@roles_required(UserRole.ADMIN, UserRole.SCHEDULER)
def schedule_index():
    return redirect(url_for("scheduler.day_schedule", date_value=planning_window()[0]))


@bp.get("/schedule/<date_value>")
@roles_required(UserRole.ADMIN, UserRole.SCHEDULER)
def day_schedule(date_value):
    selected_date = _parse_date(date_value)
    rooms, states = slot_states(selected_date)
    form = BlockForm()
    form.scope.choices = [
        (item.value, item.value.replace("_", " ").title()) for item in BlockScope
    ]
    form.room_id.choices = [(0, "Not applicable"), *_active_room_choices()]
    form.prep.choices = _prep_choices(blank=True)
    return render_template(
        "scheduler/day.html",
        selected_date=selected_date,
        dates=planning_window(),
        rooms=rooms,
        preps=tuple(PrepPeriod),
        prep_labels=PREP_LABELS,
        states=states,
        block_form=form,
        action_form=ActionForm(),
        can_modify_booking={
            record.id: _booking_can_be_modified(record)
            for state, record in states.values()
            if state == "Booked"
        },
    )


@bp.route("/requests/<int:request_id>/schedule", methods=["GET", "POST"])
@role_required(UserRole.SCHEDULER)
def schedule_pending_request(request_id):
    record = db.get_or_404(BookingRequest, request_id)
    if record.status != RequestStatus.PENDING:
        abort(409)
    form = ScheduleRequestForm()
    form.prep.choices = _prep_choices()
    form.room_id.choices = _active_room_choices()
    if not form.is_submitted():
        form.schedule_date.data = planning_window()[0]
    if form.validate_on_submit():
        try:
            schedule_request(
                record.id,
                form.schedule_date.data,
                PrepPeriod(form.prep.data),
                form.room_id.data,
            )
        except (SchedulingError, SQLAlchemyError, ValueError) as exc:
            db.session.rollback()
            message = str(exc) if isinstance(exc, SchedulingError) else GENERIC_ERROR
            flash(message, "danger")
        else:
            flash("Request scheduled.", "success")
            return redirect(
                url_for(
                    "scheduler.day_schedule",
                    date_value=form.schedule_date.data.isoformat(),
                )
            )
    return render_template(
        "scheduler/schedule_form.html",
        form=form,
        request_record=record,
        origin_label=ROLE_LABELS[request_origin_role(record.priority)],
    )


@bp.post("/schedule/<date_value>/blocks")
@roles_required(UserRole.ADMIN, UserRole.SCHEDULER)
def add_block(date_value):
    selected_date = _parse_date(date_value)
    form = BlockForm()
    form.scope.choices = [(item.value, item.value) for item in BlockScope]
    form.room_id.choices = [(0, "Not applicable"), *_active_room_choices()]
    form.prep.choices = _prep_choices(blank=True)
    if not form.validate_on_submit():
        flash("Correct the block form and try again.", "danger")
        return redirect(
            url_for("scheduler.day_schedule", date_value=selected_date.isoformat())
        )
    try:
        scope = BlockScope(form.scope.data)
        room_id = form.room_id.data or None
        prep = PrepPeriod(form.prep.data) if form.prep.data else None
        create_block(selected_date, scope, room_id, prep, form.reason.data)
    except (SchedulingError, SQLAlchemyError, ValueError) as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc, SchedulingError) else GENERIC_ERROR
        flash(message, "danger")
    else:
        flash("Availability block created.", "success")
    return redirect(
        url_for("scheduler.day_schedule", date_value=selected_date.isoformat())
    )


@bp.post("/blocks/<int:block_id>/remove")
@roles_required(UserRole.ADMIN, UserRole.SCHEDULER)
def unblock(block_id):
    form = ActionForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        changed, block_date = remove_block(block_id)
    except (SchedulingError, SQLAlchemyError, ValueError) as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc, SchedulingError) else GENERIC_ERROR
        flash(message, "danger")
        return redirect(url_for("scheduler.schedule_index"))
    flash(
        "Block removed." if changed else "Block was already removed.",
        "success" if changed else "info",
    )
    return redirect(
        url_for("scheduler.day_schedule", date_value=block_date.isoformat())
    )


@bp.route("/requests/<int:request_id>/reject", methods=["GET", "POST"])
@role_required(UserRole.SCHEDULER)
def reject_pending_request(request_id):
    record = db.get_or_404(BookingRequest, request_id)
    if record.status != RequestStatus.PENDING:
        flash("Request was already processed.", "info")
        return redirect(url_for("scheduler.pending_queue"))
    form = RejectionForm()
    if form.validate_on_submit():
        try:
            changed = reject_request(record.id, form.reason.data)
        except SchedulingError as exc:
            flash(str(exc), "danger")
        else:
            flash(
                "Request rejected." if changed else "Request was already processed.",
                "success" if changed else "info",
            )
            return redirect(url_for("scheduler.pending_queue"))
    return render_template(
        "scheduler/reject_form.html",
        form=form,
        request_record=record,
        origin_label=ROLE_LABELS[request_origin_role(record.priority)],
    )


@bp.get("/bookings/<int:booking_id>")
@roles_required(UserRole.ADMIN, UserRole.SCHEDULER)
def booking_detail(booking_id):
    booking = db.get_or_404(ScheduledBooking, booking_id)
    return render_template(
        "scheduler/booking_detail.html",
        booking=booking,
        can_modify_booking=_booking_can_be_modified(booking),
        action_form=ActionForm(),
    )


@bp.route("/bookings/<int:booking_id>/reschedule", methods=["GET", "POST"])
@roles_required(UserRole.ADMIN, UserRole.SCHEDULER)
def reschedule(booking_id):
    booking = db.get_or_404(ScheduledBooking, booking_id)
    if not _booking_can_be_modified(booking):
        flash("This booking cannot be rescheduled in its current state.", "info")
        return redirect(
            url_for("scheduler.booking_detail", booking_id=booking.id)
        )
    form = ScheduleRequestForm()
    form.submit.label.text = "Reschedule booking"
    form.prep.choices = _prep_choices()
    form.room_id.choices = _active_room_choices()
    if not form.is_submitted():
        form.schedule_date.data = booking.schedule_date
        form.prep.data = booking.prep.value
        form.room_id.data = booking.room_id
    if form.validate_on_submit():
        try:
            changed, target = reschedule_booking(
                booking.id,
                form.schedule_date.data,
                PrepPeriod(form.prep.data),
                form.room_id.data,
            )
        except SchedulingError as exc:
            flash(str(exc), "danger")
        else:
            flash(
                "Booking rescheduled." if changed else "Schedule is unchanged.",
                "success" if changed else "info",
            )
            return redirect(
                url_for("scheduler.day_schedule", date_value=target.isoformat())
            )
    return render_template("scheduler/reschedule_form.html", form=form, booking=booking)


@bp.post("/bookings/<int:booking_id>/cancel")
@roles_required(UserRole.ADMIN, UserRole.SCHEDULER)
def cancel_scheduled(booking_id):
    if not ActionForm().validate_on_submit():
        abort(400)
    try:
        changed, _schedule_date = cancel_booking(booking_id)
    except SchedulingError as exc:
        flash(str(exc), "danger")
        if db.session.get(ScheduledBooking, booking_id) is not None:
            return redirect(
                url_for("scheduler.booking_detail", booking_id=booking_id)
            )
        return redirect(url_for("scheduler.schedule_index"))
    flash(
        "Booking cancelled." if changed else "Booking was already cancelled.",
        "success" if changed else "info",
    )
    return redirect(url_for("scheduler.booking_detail", booking_id=booking_id))
