"""Public routes."""

from flask import Response, render_template

from app.blueprints.public import bp
from app.extensions import db
from app.models import PrepPeriod, Room, ScheduledBooking, SchoolClass, User
from app.scheduling import PREP_LABELS, kigali_today


@bp.get("/")
def home() -> str:
    """Render today's privacy-limited public schedule in Kigali time."""
    today = kigali_today()
    rows = db.session.execute(
        db.select(
            ScheduledBooking.prep.label("prep"),
            Room.name.label("room_name"),
            SchoolClass.name.label("class_name"),
            User.full_name.label("teacher_name"),
        )
        .join(Room, Room.id == ScheduledBooking.room_id)
        .join(SchoolClass, SchoolClass.id == ScheduledBooking.class_id)
        .join(User, User.id == ScheduledBooking.teacher_id)
        .where(
            ScheduledBooking.schedule_date == today,
            ScheduledBooking.is_active.is_(True),
        )
        .order_by(
            ScheduledBooking.prep,
            Room.name,
            SchoolClass.name,
            ScheduledBooking.id,
        )
    ).all()
    schedule = {
        prep: [row for row in rows if row.prep == prep] for prep in PrepPeriod
    }
    return render_template(
        "public/home.html",
        schedule_date=today,
        schedule=schedule,
        prep_labels=PREP_LABELS,
    )


@bp.get("/health")
def health():
    """Return a lightweight service health response."""
    return Response('{"status":"ok"}\n', status=200, mimetype="application/json")
