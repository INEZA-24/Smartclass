"""Teacher and Monitor booking-request workflows."""

from datetime import UTC, datetime

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.authz import ROLE_LABELS, role_required
from app.blueprints.requester import bp
from app.blueprints.requester.forms import (
    CancelRequestForm,
    MonitorRequestForm,
    TeacherRequestForm,
)
from app.booking_queue import (
    QueueLockedError,
    QueueUnavailableError,
    add_queue_audit,
    add_request_audit,
    lock_settings,
    pending_count,
    queue_state,
)
from app.extensions import db
from app.models import (
    BookingRequest,
    RequestStatus,
    SchoolClass,
    User,
    UserRole,
)

GENERIC_ERROR = "Unable to save the request. Please try again."
QUEUE_LOCKED_MESSAGE = (
    "New requests are temporarily locked and reopen when the pending count "
    "reaches 9 or fewer."
)


def normalize_subject(value):
    return " ".join(value.strip().split())


def normalize_reason(value):
    return value.strip()


class RequesterInvalidError(RuntimeError):
    pass


def active_classes():
    return db.session.scalars(
        db.select(SchoolClass)
        .where(SchoolClass.is_active.is_(True))
        .order_by(SchoolClass.name)
    ).all()


def active_teachers():
    return db.session.scalars(
        db.select(User)
        .where(User.role == UserRole.TEACHER, User.is_active.is_(True))
        .order_by(User.full_name)
    ).all()


def configure_form(form, role):
    if role == UserRole.TEACHER:
        form.class_id.choices = [(item.id, item.name) for item in active_classes()]
    else:
        form.teacher_id.choices = [
            (item.id, item.full_name) for item in active_teachers()
        ]


def locked_active_class(class_id):
    return db.session.scalar(
        db.select(SchoolClass)
        .where(SchoolClass.id == class_id, SchoolClass.is_active.is_(True))
        .with_for_update()
    )


def locked_active_teacher(teacher_id):
    return db.session.scalar(
        db.select(User)
        .where(
            User.id == teacher_id,
            User.role == UserRole.TEACHER,
            User.is_active.is_(True),
        )
        .with_for_update()
    )


def lock_requester(expected_role):
    requester = db.session.scalar(
        db.select(User)
        .where(
            User.id == int(current_user.get_id()),
            User.is_active.is_(True),
            User.role == expected_role,
            User.must_change_password.is_(False),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if requester is None:
        raise RequesterInvalidError
    return requester


def create_pending_request(form, role):
    settings = lock_settings()
    count = pending_count()
    if settings.booking_queue_locked:
        raise QueueLockedError
    if count >= settings.max_pending_requests:
        settings.booking_queue_locked = True
        db.session.commit()
        raise QueueLockedError

    if role == UserRole.TEACHER:
        school_class = locked_active_class(form.class_id.data)
        requester = lock_requester(UserRole.TEACHER)
        teacher = requester
        if school_class is None:
            form.class_id.errors.append("Select an active class.")
            db.session.rollback()
            return False
    else:
        selected_class_id = db.session.scalar(
            db.select(User.class_id).where(User.id == int(current_user.get_id()))
        )
        school_class = locked_active_class(selected_class_id)
        requester = lock_requester(UserRole.MONITOR)
        if (
            school_class is None
            or requester.class_id != selected_class_id
            or requester.class_id != school_class.id
        ):
            flash("Your assigned class is unavailable.", "danger")
            db.session.rollback()
            return False
        teacher = locked_active_teacher(form.teacher_id.data)
        if teacher is None:
            form.teacher_id.errors.append("Select an active Teacher.")
            db.session.rollback()
            return False

    record = BookingRequest(
        requester=requester,
        school_class=school_class,
        teacher=teacher,
        subject=normalize_subject(form.subject.data),
        reason=normalize_reason(form.reason.data),
        status=RequestStatus.PENDING,
    )
    db.session.add(record)
    db.session.flush()
    new_count = count + 1
    add_request_audit(requester.id, "REQUEST_CREATED", record)
    if new_count == settings.max_pending_requests:
        settings.booking_queue_locked = True
        add_queue_audit(requester.id, "QUEUE_LOCKED", new_count, True)
    db.session.commit()
    return True


def request_page(role):
    form = TeacherRequestForm() if role == UserRole.TEACHER else MonitorRequestForm()
    configure_form(form, role)
    settings, count = queue_state()
    if settings is None:
        flash(GENERIC_ERROR, "danger")
        return render_template(
            "requester/form.html", form=form, settings=None, pending_count=count
        )
    if form.validate_on_submit():
        try:
            if create_pending_request(form, role):
                flash("Request submitted.", "success")
                return redirect(url_for(f"requester.{role.value.lower()}_requests"))
        except QueueLockedError:
            db.session.rollback()
            flash(QUEUE_LOCKED_MESSAGE, "warning")
        except (
            QueueUnavailableError,
            RequesterInvalidError,
            SQLAlchemyError,
            ValueError,
        ):
            db.session.rollback()
            flash(GENERIC_ERROR, "danger")
    settings, count = queue_state()
    return render_template(
        "requester/form.html", form=form, settings=settings, pending_count=count
    )


@bp.get("/teacher")
@role_required(UserRole.TEACHER)
def teacher_dashboard():
    return render_template(
        "dashboard.html", user=current_user, role_label=ROLE_LABELS[UserRole.TEACHER]
    )


@bp.get("/monitor")
@role_required(UserRole.MONITOR)
def monitor_dashboard():
    return render_template(
        "dashboard.html", user=current_user, role_label=ROLE_LABELS[UserRole.MONITOR]
    )


@bp.route("/teacher/new", methods=["GET", "POST"])
@role_required(UserRole.TEACHER)
def teacher_new():
    return request_page(UserRole.TEACHER)


@bp.route("/monitor/new", methods=["GET", "POST"])
@role_required(UserRole.MONITOR)
def monitor_new():
    return request_page(UserRole.MONITOR)


def history_page():
    records = db.session.scalars(
        db.select(BookingRequest)
        .where(BookingRequest.requester_id == current_user.id)
        .order_by(BookingRequest.created_at.desc(), BookingRequest.id.desc())
    ).all()
    settings, count = queue_state()
    return render_template(
        "requester/history.html",
        requests=records,
        cancel_form=CancelRequestForm(),
        settings=settings,
        pending_count=count,
    )


@bp.get("/teacher/requests")
@role_required(UserRole.TEACHER)
def teacher_requests():
    return history_page()


@bp.get("/monitor/requests")
@role_required(UserRole.MONITOR)
def monitor_requests():
    return history_page()


def locked_request(request_id):
    record = db.session.scalar(
        db.select(BookingRequest)
        .where(BookingRequest.id == request_id)
        .with_for_update()
    )
    if record is None:
        abort(404)
    return record


@bp.route("/requests/<int:request_id>/edit", methods=["GET", "POST"])
@role_required(UserRole.TEACHER)
def teacher_edit(request_id):
    return edit_request(request_id, UserRole.TEACHER)


@bp.route("/monitor/requests/<int:request_id>/edit", methods=["GET", "POST"])
@role_required(UserRole.MONITOR)
def monitor_edit(request_id):
    return edit_request(request_id, UserRole.MONITOR)


def edit_request(request_id, role):
    record = locked_request(request_id)
    try:
        requester = lock_requester(role)
    except RequesterInvalidError:
        db.session.rollback()
        abort(403)
    if record.requester_id != requester.id:
        db.session.rollback()
        abort(404)
    if record.status != RequestStatus.PENDING:
        db.session.rollback()
        abort(409)
    form = (
        TeacherRequestForm(obj=record)
        if role == UserRole.TEACHER
        else MonitorRequestForm(obj=record)
    )
    configure_form(form, role)
    if not form.is_submitted():
        if role == UserRole.TEACHER:
            form.class_id.data = record.class_id
        else:
            form.teacher_id.data = record.teacher_id
    if form.validate_on_submit():
        try:
            if role == UserRole.TEACHER:
                school_class = locked_active_class(form.class_id.data)
                if school_class is None:
                    form.class_id.errors.append("Select an active class.")
                    db.session.rollback()
                    return render_template(
                        "requester/form.html", form=form, editing=True
                    )
                record.school_class = school_class
            else:
                if requester.class_id != record.class_id:
                    raise RequesterInvalidError
                teacher = locked_active_teacher(form.teacher_id.data)
                if teacher is None:
                    form.teacher_id.errors.append("Select an active Teacher.")
                    db.session.rollback()
                    return render_template(
                        "requester/form.html", form=form, editing=True
                    )
                record.teacher = teacher
            record.subject = normalize_subject(form.subject.data)
            record.reason = normalize_reason(form.reason.data)
            add_request_audit(requester.id, "REQUEST_EDITED", record)
            db.session.commit()
        except (RequesterInvalidError, SQLAlchemyError, ValueError):
            db.session.rollback()
            flash(GENERIC_ERROR, "danger")
        else:
            flash("Request updated.", "success")
            return redirect(url_for(f"requester.{role.value.lower()}_requests"))
    return render_template("requester/form.html", form=form, editing=True)


@bp.post("/requests/<int:request_id>/cancel")
@role_required(UserRole.TEACHER)
def teacher_cancel(request_id):
    return cancel_request(request_id, UserRole.TEACHER)


@bp.post("/monitor/requests/<int:request_id>/cancel")
@role_required(UserRole.MONITOR)
def monitor_cancel(request_id):
    return cancel_request(request_id, UserRole.MONITOR)


def cancel_request(request_id, role):
    form = CancelRequestForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        settings = lock_settings()
        record = db.session.scalar(
            db.select(BookingRequest)
            .where(BookingRequest.id == request_id)
            .with_for_update()
        )
        if record is None:
            abort(404)
        requester = lock_requester(role)
        if record.requester_id != requester.id:
            abort(404)
        if record.status != RequestStatus.PENDING:
            db.session.rollback()
            flash("Request is no longer pending.", "info")
            return redirect(url_for(f"requester.{role.value.lower()}_requests"))
        if role == UserRole.MONITOR and requester.class_id != record.class_id:
            raise RequesterInvalidError
        record.status = RequestStatus.CANCELLED
        record.cancelled_at = datetime.now(UTC)
        count = pending_count()
        add_request_audit(
            requester.id,
            "REQUEST_CANCELLED",
            record,
            {"status_transition": "PENDING->CANCELLED", "queue_count": count},
        )
        if settings.booking_queue_locked and count <= settings.reopen_threshold:
            settings.booking_queue_locked = False
            add_queue_audit(requester.id, "QUEUE_REOPENED", count, False)
        db.session.commit()
    except (QueueUnavailableError, RequesterInvalidError, SQLAlchemyError, ValueError):
        db.session.rollback()
        flash(GENERIC_ERROR, "danger")
    else:
        flash("Request cancelled.", "success")
    return redirect(url_for(f"requester.{role.value.lower()}_requests"))
