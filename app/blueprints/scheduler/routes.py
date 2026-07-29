"""Scheduler dashboard and read-only pending queue."""

from flask import render_template
from flask_login import current_user

from app.authz import ROLE_LABELS, role_required
from app.blueprints.scheduler import bp
from app.booking_queue import queue_state, request_origin_role
from app.extensions import db
from app.models import BookingRequest, RequestPriority, RequestStatus, UserRole


@bp.get("/")
@role_required(UserRole.SCHEDULER)
def dashboard():
    return render_template(
        "dashboard.html",
        user=current_user,
        role_label=ROLE_LABELS[UserRole.SCHEDULER],
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
