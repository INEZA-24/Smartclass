"""Transactional scheduling, blocking, and schedule-grid helpers."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask_login import current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.booking_queue import (
    QueueUnavailableError,
    add_queue_audit,
    lock_settings,
    pending_count,
)
from app.extensions import db
from app.models import (
    AuditLog,
    BlockScope,
    BookingRequest,
    Notification,
    NotificationType,
    PrepPeriod,
    RequestStatus,
    Room,
    RoomBlock,
    ScheduledBooking,
    SchoolClass,
    User,
    UserRole,
)

KIGALI = ZoneInfo("Africa/Kigali")
PREP_LABELS = {PrepPeriod.PREP_1: "Prep 1", PrepPeriod.PREP_2: "Prep 2"}


class SchedulingError(RuntimeError):
    """A safe scheduling or blocking failure."""


class SchedulingConflictError(SchedulingError):
    """A requested slot conflicts with current schedule state."""


def kigali_today(now=None):
    """Return the Kigali calendar date, with injectable time for tests."""
    instant = now or datetime.now(UTC)
    if isinstance(instant, date) and not isinstance(instant, datetime):
        return instant
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(KIGALI).date()


def planning_window(today=None):
    """Return current Kigali date plus the following two dates."""
    first = today or kigali_today()
    return tuple(first + timedelta(days=offset) for offset in range(3))


def require_planning_date(value, today=None):
    if value not in planning_window(today):
        raise SchedulingError("Select a date in the current three-day window.")
    return value


def advisory_lock_key(schedule_date):
    """Return a stable date-derived PostgreSQL advisory-lock key."""
    return schedule_date.toordinal()


def acquire_schedule_date_lock(schedule_date):
    """Serialize every schedule mutation affecting one date."""
    key = advisory_lock_key(schedule_date)
    if db.session.get_bind().dialect.name == "postgresql":
        db.session.execute(
            db.text("SELECT pg_advisory_xact_lock(:schedule_date_key)"),
            {"schedule_date_key": key},
        )
    return key


def lock_actor(allowed_roles):
    actor = db.session.scalar(
        db.select(User)
        .where(
            User.id == int(current_user.get_id()),
            User.is_active.is_(True),
            User.must_change_password.is_(False),
            User.role.in_(allowed_roles),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if actor is None:
        db.session.rollback()
        raise SchedulingError("Your account is not permitted to perform this action.")
    return actor


def active_blocks_for_date(schedule_date):
    return db.session.scalars(
        db.select(RoomBlock).where(
            RoomBlock.block_date == schedule_date,
            RoomBlock.is_active.is_(True),
        )
    ).all()


def block_covers(block, room_id, prep):
    if block.scope == BlockScope.DAY:
        return True
    if block.scope == BlockScope.ROOM_DAY:
        return block.room_id == room_id
    return block.room_id == room_id and block.prep == prep


def slot_states(schedule_date):
    """Resolve every active-room/prep cell to exactly one state."""
    require_planning_date(schedule_date)
    rooms = db.session.scalars(
        db.select(Room).where(Room.is_active.is_(True)).order_by(Room.name)
    ).all()
    bookings = db.session.scalars(
        db.select(ScheduledBooking).where(
            ScheduledBooking.schedule_date == schedule_date,
            ScheduledBooking.is_active.is_(True),
        )
    ).all()
    blocks = active_blocks_for_date(schedule_date)
    booked = {(item.room_id, item.prep): item for item in bookings}
    result = {}
    for prep in PrepPeriod:
        for room in rooms:
            booking = booked.get((room.id, prep))
            applicable = next(
                (item for item in blocks if block_covers(item, room.id, prep)), None
            )
            if booking:
                result[(room.id, prep)] = ("Booked", booking)
            elif applicable:
                result[(room.id, prep)] = ("Unavailable", applicable)
            else:
                result[(room.id, prep)] = ("Available", None)
    return rooms, result


def _locked_active(model, record_id):
    return db.session.scalar(
        db.select(model)
        .where(model.id == record_id, model.is_active.is_(True))
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _slot_conflict(schedule_date, prep, room_id, class_id, teacher_id):
    booking = db.session.scalar(
        db.select(ScheduledBooking.id).where(
            ScheduledBooking.schedule_date == schedule_date,
            ScheduledBooking.prep == prep,
            ScheduledBooking.is_active.is_(True),
            db.or_(
                ScheduledBooking.room_id == room_id,
                ScheduledBooking.class_id == class_id,
                ScheduledBooking.teacher_id == teacher_id,
            ),
        )
    )
    if booking is not None:
        return True
    return any(
        block_covers(block, room_id, prep)
        for block in active_blocks_for_date(schedule_date)
    )


def schedule_request(request_id, schedule_date, prep, room_id):
    """Atomically schedule one still-pending request."""
    try:
        acquire_schedule_date_lock(schedule_date)
        actor = lock_actor((UserRole.SCHEDULER,))
        settings = lock_settings()
        record = db.session.scalar(
            db.select(BookingRequest)
            .where(BookingRequest.id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if record is None or record.status != RequestStatus.PENDING:
            raise SchedulingConflictError("This request is no longer Pending.")
        require_planning_date(schedule_date)
        if prep not in tuple(PrepPeriod):
            raise SchedulingError("Select a valid prep period.")
        room = _locked_active(Room, room_id)
        school_class = _locked_active(SchoolClass, record.class_id)
        teacher = db.session.scalar(
            db.select(User)
            .where(
                User.id == record.teacher_id,
                User.role == UserRole.TEACHER,
                User.is_active.is_(True),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if room is None or school_class is None or teacher is None:
            raise SchedulingConflictError(
                "The selected room, class, or responsible Teacher is inactive."
            )
        if _slot_conflict(
            schedule_date, prep, room.id, school_class.id, teacher.id
        ):
            raise SchedulingConflictError(
                "That room, class, or responsible Teacher is unavailable for the slot."
            )
        booking = ScheduledBooking(
            request=record,
            schedule_date=schedule_date,
            prep=prep,
            room=room,
            school_class=school_class,
            teacher=teacher,
            scheduled_by=actor,
        )
        db.session.add(booking)
        db.session.flush()
        record.status = RequestStatus.SCHEDULED
        count = pending_count()
        reopened = (
            settings.booking_queue_locked and count <= settings.reopen_threshold
        )
        if reopened:
            settings.booking_queue_locked = False
        db.session.add(
            Notification(
                user_id=record.requester_id,
                type=NotificationType.APPROVED,
                title="Smart Class request scheduled",
                message=(
                    f"{schedule_date.isoformat()}, {PREP_LABELS[prep]}, "
                    f"{room.name}, {school_class.name}."
                ),
                booking_request_id=record.id,
            )
        )
        db.session.add(
            AuditLog(
                actor_id=actor.id,
                action="REQUEST_SCHEDULED",
                entity_type="ScheduledBooking",
                entity_id=booking.id,
                details={
                    "request_id": record.id,
                    "booking_id": booking.id,
                    "date": schedule_date.isoformat(),
                    "prep": prep.value,
                    "room_id": room.id,
                    "class_id": school_class.id,
                    "teacher_id": teacher.id,
                    "queue_count": count,
                },
            )
        )
        if reopened:
            add_queue_audit(actor.id, "QUEUE_REOPENED", count, False)
        db.session.commit()
    except QueueUnavailableError as exc:
        db.session.rollback()
        raise SchedulingError(
            "System settings are unavailable. Please contact an Administrator."
        ) from exc
    except IntegrityError as exc:
        db.session.rollback()
        raise SchedulingConflictError(
            "The requested slot was taken concurrently. Please choose another."
        ) from exc
    except (SQLAlchemyError, ValueError) as exc:
        db.session.rollback()
        raise SchedulingError(
            "Unable to schedule the request. Please try again."
        ) from exc
    except SchedulingError:
        db.session.rollback()
        raise
    return booking


def _covered_booking_exists(block_date, scope, room_id=None, prep=None):
    statement = db.select(ScheduledBooking.id).where(
        ScheduledBooking.schedule_date == block_date,
        ScheduledBooking.is_active.is_(True),
    )
    if scope == BlockScope.SLOT:
        statement = statement.where(
            ScheduledBooking.room_id == room_id, ScheduledBooking.prep == prep
        )
    elif scope == BlockScope.ROOM_DAY:
        statement = statement.where(ScheduledBooking.room_id == room_id)
    return db.session.scalar(statement.limit(1)) is not None


def create_block(block_date, scope, room_id=None, prep=None, reason=None):
    try:
        acquire_schedule_date_lock(block_date)
        actor = lock_actor((UserRole.ADMIN, UserRole.SCHEDULER))
        require_planning_date(block_date)
        room = None
        if scope == BlockScope.SLOT:
            if not room_id or prep not in tuple(PrepPeriod):
                raise SchedulingError(
                    "A slot block requires an active room and prep."
                )
            room = _locked_active(Room, room_id)
        elif scope == BlockScope.ROOM_DAY:
            if not room_id or prep is not None:
                raise SchedulingError(
                    "A room-day block requires only an active room."
                )
            room = _locked_active(Room, room_id)
        elif scope == BlockScope.DAY:
            if room_id is not None or prep is not None:
                raise SchedulingError(
                    "A full-day block cannot specify a room or prep."
                )
        else:
            raise SchedulingError("Select a valid block scope.")
        if scope != BlockScope.DAY and room is None:
            raise SchedulingError("Select an active room.")
        if _covered_booking_exists(block_date, scope, room_id, prep):
            raise SchedulingConflictError(
                "The block covers an existing booking. "
                "Reschedule or cancel it first."
            )
        block = RoomBlock(
            block_date=block_date,
            scope=scope,
            room=room,
            prep=prep,
            reason=(reason or "").strip() or None,
            created_by=actor,
        )
        db.session.add(block)
        db.session.flush()
        db.session.add(
            AuditLog(
                actor_id=actor.id,
                action=f"{scope.value}_BLOCK_CREATED",
                entity_type="RoomBlock",
                entity_id=block.id,
                details={
                    "block_id": block.id,
                    "date": block_date.isoformat(),
                    "block_scope": scope.value,
                    "room_id": room_id,
                    "prep": prep.value if prep else None,
                },
            )
        )
        db.session.commit()
    except SchedulingError:
        db.session.rollback()
        raise
    except (SQLAlchemyError, ValueError) as exc:
        db.session.rollback()
        raise SchedulingError("Unable to create the block. Please try again.") from exc
    return block


def remove_block(block_id):
    try:
        preliminary = db.session.get(RoomBlock, block_id)
        if preliminary is None:
            raise SchedulingError("Block not found.")
        block_date = preliminary.block_date
        acquire_schedule_date_lock(block_date)
        actor = lock_actor((UserRole.ADMIN, UserRole.SCHEDULER))
        block = db.session.scalar(
            db.select(RoomBlock)
            .where(RoomBlock.id == block_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if block is None:
            raise SchedulingError("Block not found.")
        block_date = block.block_date
        if not block.is_active:
            db.session.rollback()
            return False, block_date
        block.is_active = False
        block.removed_at = datetime.now(UTC)
        block.removed_by_id = actor.id
        db.session.add(
            AuditLog(
                actor_id=actor.id,
                action="BLOCK_REMOVED",
                entity_type="RoomBlock",
                entity_id=block.id,
                details={
                    "block_id": block.id,
                    "date": block_date.isoformat(),
                    "block_scope": block.scope.value,
                    "room_id": block.room_id,
                    "prep": block.prep.value if block.prep else None,
                },
            )
        )
        db.session.commit()
    except SchedulingError:
        db.session.rollback()
        raise
    except (SQLAlchemyError, ValueError) as exc:
        db.session.rollback()
        raise SchedulingError("Unable to remove the block. Please try again.") from exc
    return True, block_date
