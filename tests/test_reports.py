"""Milestone 9 report authorization, correctness, filtering, and privacy tests."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from flask_login import login_user
from sqlalchemy import update
from test_schedule_changes import (  # noqa: F401
    PASSWORD,
    database,
    login,
    scheduled,
    seed,
)

from app.extensions import db
from app.models import (
    AuditLog,
    BookingRequest,
    Notification,
    NotificationType,
    PrepPeriod,
    RequestStatus,
    ScheduledBooking,
    SchoolClass,
    User,
    UserRole,
)
from app.reports import (
    ReportFilters,
    kigali_utc_bounds,
    parse_date_value,
    week_bounds,
)
from app.scheduling import planning_window, reschedule_booking

REPORT_PATHS = (
    "/reports/",
    "/reports/daily",
    "/reports/weekly",
    "/reports/history",
    "/reports/rooms",
    "/reports/classes",
)


@pytest.mark.parametrize("role_name", ["admin", "scheduler"])
@pytest.mark.parametrize("path", REPORT_PATHS)
def test_authorized_roles_can_access_every_report(client, app, role_name, path):
    seed(app)
    login(client, role_name)
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("role_name", ["teacher", "monitor"])
@pytest.mark.parametrize("path", REPORT_PATHS)
def test_requester_roles_are_forbidden_from_reports(client, app, role_name, path):
    seed(app)
    login(client, role_name)
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("path", REPORT_PATHS)
def test_anonymous_users_are_redirected_from_reports(client, app, path):
    seed(app)
    response = client.get(path)
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_requester_navigation_does_not_show_reports(client, app):
    seed(app)
    for name in ("teacher", "monitor"):
        login(client, name)
        assert b'href="/reports/"' not in client.get(f"/requester/{name}").data
        client.post("/auth/logout")


@pytest.mark.parametrize("role_name", ["admin", "scheduler"])
def test_authorized_navigation_shows_reports(client, app, role_name):
    seed(app)
    login(client, role_name)
    assert b'href="/reports/"' in client.get("/reports/").data


def test_anonymous_navigation_does_not_show_reports(client, app):
    seed(app)
    assert b'href="/reports/"' not in client.get("/").data


@pytest.mark.parametrize(
    "column,value", [("is_active", False), ("role", UserRole.TEACHER)]
)
def test_stale_report_actor_loses_access(client, app, column, value):
    ids = seed(app)
    login(client, "admin")
    with app.app_context(), db.engine.begin() as connection:
        connection.execute(
            update(User).where(User.id == ids["admin"]).values(**{column: value})
        )
    response = client.get("/reports/daily")
    assert response.status_code in {302, 403}


@pytest.mark.parametrize("role_name", ["admin", "scheduler"])
def test_forced_password_user_is_redirected(client, app, role_name):
    ids = seed(app)
    with app.app_context():
        user = db.session.get(User, ids[role_name])
        user.must_change_password = True
        db.session.commit()
    login(client, role_name)
    assert "/auth/change-password" in client.get("/reports/").location


@pytest.mark.parametrize(
    ("role_name", "changed_role"),
    [("admin", UserRole.TEACHER), ("scheduler", UserRole.MONITOR)],
)
def test_report_access_rechecks_stale_role(client, app, role_name, changed_role):
    ids = seed(app)
    login(client, role_name)
    changed_values = {"role": changed_role}
    if changed_role == UserRole.MONITOR:
        changed_values["class_id"] = ids["class"]
    with app.app_context(), db.engine.begin() as connection:
        connection.execute(
            update(User)
            .where(User.id == ids[role_name])
            .values(**changed_values)
        )
    assert client.get("/reports/history").status_code == 403


@pytest.mark.parametrize("role_name", ["admin", "scheduler"])
def test_report_access_rechecks_stale_inactive_account(client, app, role_name):
    ids = seed(app)
    login(client, role_name)
    with app.app_context(), db.engine.begin() as connection:
        connection.execute(
            update(User).where(User.id == ids[role_name]).values(is_active=False)
        )
    assert client.get("/reports/history").status_code == 302


def test_daily_report_counts_active_selected_date_and_hides_private_data(client, app):
    ids = scheduled(app)
    selected = planning_window()[0]
    with app.app_context():
        request = db.session.get(BookingRequest, ids["request"])
        request.reason = "PRIVATE-DAILY-REASON"
        request.rejection_reason = "PRIVATE-DAILY-REJECTION"
        db.session.commit()
    login(client, "admin")
    response = client.get(f"/reports/daily?date={selected.isoformat()}")
    assert response.status_code == 200
    assert b"Smart Class 1" in response.data
    assert b"S1 A" in response.data
    assert b"Math" in response.data
    assert b"Total sessions</strong><span>1" in response.data
    assert b"PRIVATE-DAILY-REASON" not in response.data
    assert b"PRIVATE-DAILY-REJECTION" not in response.data


@pytest.mark.parametrize("offset", [-1, 1])
def test_daily_report_excludes_other_dates(client, app, offset):
    ids = scheduled(app)
    selected = planning_window()[0]
    with app.app_context():
        db.session.get(ScheduledBooking, ids["booking"]).schedule_date = (
            selected + timedelta(days=offset)
        )
        db.session.commit()
    login(client, "scheduler")
    response = client.get(f"/reports/daily?date={selected.isoformat()}")
    assert b"No scheduled sessions match this report." in response.data


def test_daily_report_excludes_cancelled_booking(client, app):
    ids = scheduled(app)
    selected = planning_window()[0]
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.is_active = False
        booking.cancelled_at = datetime.now(UTC)
        booking.request.status = RequestStatus.CANCELLED
        db.session.commit()
    login(client, "admin")
    response = client.get(f"/reports/daily?date={selected.isoformat()}")
    assert b"No scheduled sessions match this report." in response.data


def test_week_bounds_are_monday_through_sunday():
    assert week_bounds(date(2026, 8, 5)) == (date(2026, 8, 3), date(2026, 8, 9))


@pytest.mark.parametrize(
    "value",
    [
        "20260801",
        "2026-W31-6",
        "2026-8-1",
        "2026-08-01T00:00:00",
        " 2026-08-01",
        "2026-08-01 ",
        "２０２６-０８-０１",
        20260801,
    ],
)
def test_date_parser_rejects_values_not_in_exact_ascii_format(value):
    filters = ReportFilters()
    assert parse_date_value(value, "Date", filters) is None
    assert filters.errors == ["Date must use YYYY-MM-DD."]


def test_date_parser_accepts_exact_supported_date_and_intentional_blank():
    filters = ReportFilters()
    default = date(2026, 8, 2)
    assert parse_date_value("2026-08-01", "Date", filters) == date(2026, 8, 1)
    assert parse_date_value("", "Date", filters, default) == default
    assert not filters.errors


@pytest.mark.parametrize("value", ["0001-01-01", "9999-12-31"])
def test_date_parser_rejects_unsafe_python_boundaries(value):
    filters = ReportFilters()
    assert parse_date_value(value, "Date", filters) is None
    assert filters.errors == ["Date is outside the supported report range."]


@pytest.mark.parametrize("value", [None, "2026-08-01", datetime(2026, 8, 1)])
def test_week_bounds_rejects_non_date_values(value):
    with pytest.raises(ValueError, match="outside the supported report range"):
        week_bounds(value)


@pytest.mark.parametrize(
    "start,end",
    [
        (None, date(2026, 8, 1)),
        ("2026-08-01", date(2026, 8, 1)),
        (datetime(2026, 8, 1), date(2026, 8, 1)),
        (date.min, date.min),
        (date.max, date.max),
    ],
)
def test_kigali_bounds_rejects_unsafe_types_and_extremes(start, end):
    with pytest.raises(ValueError, match="outside the supported report range"):
        kigali_utc_bounds(start, end)


@pytest.mark.parametrize("path", ["daily", "weekly"])
@pytest.mark.parametrize(
    "value", ["20260801", "2026-W31-6", "0001-01-01", "9999-12-31"]
)
def test_invalid_report_date_never_falls_back_to_today(client, app, path, value):
    seed(app)
    login(client, "admin")
    with patch(
        "app.blueprints.reports.routes.booking_rows",
        side_effect=AssertionError("invalid dates must not run the report query"),
    ):
        response = client.get(f"/reports/{path}?date={value}")
    assert response.status_code == 200
    assert b"Correct the filters above to run this report." in response.data
    assert b"Total sessions" not in response.data
    assert b"2026-08-01" not in response.data


@pytest.mark.parametrize("path", ["rooms", "classes"])
def test_invalid_usage_date_does_not_claim_default_range(client, app, path):
    seed(app)
    login(client, "scheduler")
    response = client.get(
        f"/reports/{path}?start_date=20260801&end_date=2026-08-02"
    )
    assert response.status_code == 200
    assert b"Correct the filters above to run this report." in response.data
    assert b"Inclusive range:" not in response.data


@pytest.mark.parametrize(
    ("path", "query", "forbidden"),
    [
        ("daily", "date=invalid", (b"Total sessions", b"No scheduled sessions")),
        ("weekly", "date=invalid", (b"Total sessions", b"No scheduled sessions")),
        (
            "history",
            "status=INVALID",
            (b"Total requests:", b"No booking requests match"),
        ),
        (
            "rooms",
            "start_date=invalid",
            (b"Grand total:", b"No usage was recorded"),
        ),
        (
            "classes",
            "end_date=invalid",
            (b"Grand total:", b"No usage was recorded"),
        ),
    ],
)
def test_invalid_filters_render_feedback_without_report_results(
    client, app, path, query, forbidden
):
    seed(app)
    login(client, "admin")
    response = client.get(f"/reports/{path}?{query}")
    assert response.status_code == 200
    assert b"Correct the filters above to run this report." in response.data
    for text in forbidden:
        assert text not in response.data


def test_history_displays_request_and_booking_cancellation_times_separately(
    client, app
):
    ids = scheduled(app)
    with app.app_context():
        request_record = db.session.get(BookingRequest, ids["request"])
        booking = db.session.get(ScheduledBooking, ids["booking"])
        request_record.status = RequestStatus.CANCELLED
        request_record.cancelled_at = datetime(2026, 8, 1, 8, 15, tzinfo=UTC)
        booking.is_active = False
        booking.cancelled_at = datetime(2026, 8, 1, 9, 45, tzinfo=UTC)
        db.session.commit()
    login(client, "scheduler")
    response = client.get("/reports/history")
    assert response.status_code == 200
    assert b"Request cancelled" in response.data
    assert b"Booking cancelled" in response.data
    assert b"2026-08-01 10:15" in response.data
    assert b"2026-08-01 11:45" in response.data


def test_weekly_report_includes_boundary_days_and_zero_day_summary(client, app):
    ids = scheduled(app)
    monday = date(2026, 8, 3)
    with app.app_context():
        db.session.get(ScheduledBooking, ids["booking"]).schedule_date = monday
        db.session.commit()
    login(client, "scheduler")
    response = client.get("/reports/weekly?date=2026-08-05")
    assert b"2026-08-03" in response.data
    assert b"2026-08-09" in response.data
    assert b"2026-08-04: 0" in response.data
    assert b"Total sessions</strong><span>1" in response.data


def add_status_requests(ids):
    teacher = db.session.get(User, ids["teacher"])
    school_class = db.session.get(SchoolClass, ids["class"])
    for status in (
        RequestStatus.PENDING,
        RequestStatus.REJECTED,
        RequestStatus.CANCELLED,
    ):
        record = BookingRequest(
            requester=teacher,
            school_class=school_class,
            subject=f"{status.value} subject",
            reason=f"PRIVATE {status.value}",
        )
        db.session.add(record)
        db.session.flush()
        record.status = status
        if status == RequestStatus.CANCELLED:
            record.cancelled_at = datetime.now(UTC)
    db.session.commit()


def test_history_includes_every_status_and_filters_with_and_semantics(client, app):
    ids = scheduled(app)
    with app.app_context():
        add_status_requests(ids)
    login(client, "admin")
    response = client.get("/reports/history")
    for status in RequestStatus:
        assert status.value.title().encode() in response.data
    filtered = client.get(
        f"/reports/history?status=SCHEDULED&class_id={ids['class']}"
        f"&room_id={ids['room1']}&origin=TEACHER"
    )
    assert b"SCHEDULED subject" not in filtered.data
    assert b"Math" in filtered.data
    assert b"PRIVATE" not in filtered.data


def test_history_origin_uses_immutable_priority_after_role_change(client, app):
    ids = seed(app)
    with app.app_context():
        db.session.execute(
            update(User).where(User.id == ids["teacher"]).values(role=UserRole.ADMIN)
        )
        db.session.commit()
    login(client, "scheduler")
    response = client.get("/reports/history?origin=TEACHER")
    assert b"Teacher" in response.data
    assert b"Math" in response.data


def test_kigali_history_boundaries_convert_to_utc():
    start, end = kigali_utc_bounds(date(2026, 8, 1), date(2026, 8, 1))
    assert start == datetime(2026, 7, 31, 22, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 1, 22, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("path", "label"), [("rooms", "Smart Class 1"), ("classes", "S1 A")]
)
def test_usage_reports_count_active_once_and_show_subtotals(client, app, path, label):
    scheduled(app)
    target = planning_window()[0]
    login(client, "admin")
    response = client.get(
        f"/reports/{path}?start_date={target}&end_date={target}"
    )
    assert label.encode() in response.data
    assert b"Grand total:</strong> 1" in response.data
    assert b">1</td><td>1</td><td>0</td>" in response.data


@pytest.mark.parametrize("path", ["rooms", "classes"])
def test_usage_excludes_cancelled_and_outside_range(client, app, path):
    ids = scheduled(app)
    target = planning_window()[0]
    with app.app_context():
        db.session.get(ScheduledBooking, ids["booking"]).is_active = False
        db.session.commit()
    login(client, "scheduler")
    response = client.get(f"/reports/{path}?start_date={target}&end_date={target}")
    assert b"No usage was recorded for this range." in response.data


@pytest.mark.parametrize(
    "query",
    [
        "date=invalid",
        "date=2026-01-01&date=2026-01-02",
        "unexpected=value",
        "date=<script>alert(1)</script>",
    ],
)
def test_malformed_daily_filters_are_safe(client, app, query):
    seed(app)
    login(client, "admin")
    response = client.get(f"/reports/daily?{query}")
    assert response.status_code == 200
    assert b"alert(1)" not in response.data
    assert b"Traceback" not in response.data


@pytest.mark.parametrize(
    "path,id_name", [("rooms", "room_id"), ("classes", "class_id")]
)
@pytest.mark.parametrize("bad_id", ["0", "-1", "999999", "true", "<script>"])
def test_invalid_usage_identifiers_and_ranges_are_safe(
    client, app, path, id_name, bad_id
):
    seed(app)
    login(client, "admin")
    response = client.get(
        f"/reports/{path}?start_date=2026-08-02&end_date=2026-08-01"
        f"&{id_name}={bad_id}"
    )
    assert response.status_code == 200
    assert b"Start date must not be later than end date." in response.data
    assert b"Select a valid" in response.data


def test_report_values_are_escaped_and_unrelated_private_records_absent(client, app):
    ids = scheduled(app)
    target = planning_window()[0]
    with app.app_context():
        request = db.session.get(BookingRequest, ids["request"])
        request.subject = "<script>SAFE-ESCAPED-SUBJECT</script>"
        request.reason = "PRIVATE-REPORT-REASON"
        request.rejection_reason = "PRIVATE-REJECTION"
        db.session.add(
            Notification(
                user_id=ids["teacher"],
                type=NotificationType.SYSTEM,
                title="PRIVATE-NOTIFICATION-TITLE",
                message="PRIVATE-NOTIFICATION-MESSAGE",
            )
        )
        db.session.add(
            AuditLog(
                actor_id=ids["admin"],
                action="REPORT_PRIVATE",
                entity_type="Test",
                details={"secret": "PRIVATE-AUDIT"},
            )
        )
        db.session.commit()
    login(client, "admin")
    response = client.get(f"/reports/daily?date={target}")
    assert b"&lt;script&gt;SAFE-ESCAPED-SUBJECT&lt;/script&gt;" in response.data
    assert b"<script>SAFE-ESCAPED-SUBJECT</script>" not in response.data
    for value in (
        b"PRIVATE-REPORT-REASON",
        b"PRIVATE-REJECTION",
        b"PRIVATE-NOTIFICATION-TITLE",
        b"PRIVATE-NOTIFICATION-MESSAGE",
        b"PRIVATE-AUDIT",
    ):
        assert value not in response.data


def test_disabled_historical_relationships_render_without_reactivation(client, app):
    ids = scheduled(app)
    target = planning_window()[0]
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        booking.room.is_active = False
        booking.school_class.is_active = False
        booking.teacher.is_active = False
        booking.request.requester.is_active = False
        db.session.commit()
    login(client, "admin")
    history = client.get("/reports/history")
    rooms = client.get(f"/reports/rooms?start_date={target}&end_date={target}")
    classes = client.get(f"/reports/classes?start_date={target}&end_date={target}")
    for response in (history, rooms, classes):
        assert response.status_code == 200
    assert b"Smart Class 1" in rooms.data and b"Inactive" in rooms.data
    assert b"S1 A" in classes.data and b"Inactive" in classes.data
    with app.app_context():
        booking = db.session.get(ScheduledBooking, ids["booking"])
        assert not booking.room.is_active
        assert not booking.school_class.is_active


def test_rescheduled_booking_counts_only_current_slot_and_preserves_history(app):
    ids = scheduled(app)
    old_date, new_date = planning_window()[:2]
    with app.test_request_context():
        login_user(db.session.get(User, ids["admin"]))
        assert reschedule_booking(
            ids["booking"], new_date, PrepPeriod.PREP_2, ids["room2"]
        )[0]
        old_room_rows = db.session.execute(
            db.select(ScheduledBooking.id).where(
                ScheduledBooking.schedule_date == old_date,
                ScheduledBooking.room_id == ids["room1"],
                ScheduledBooking.is_active.is_(True),
            )
        ).all()
        new_rows = db.session.execute(
            db.select(ScheduledBooking.id).where(
                ScheduledBooking.schedule_date == new_date,
                ScheduledBooking.room_id == ids["room2"],
                ScheduledBooking.is_active.is_(True),
            )
        ).all()
        assert old_room_rows == []
        assert len(new_rows) == 1
        assert db.session.scalar(
            db.select(db.func.count()).select_from(BookingRequest).where(
                BookingRequest.id == ids["request"]
            )
        ) == 1


def test_history_kigali_calendar_boundaries(client, app):
    ids = seed(app)
    with app.app_context():
        teacher = db.session.get(User, ids["teacher"])
        school_class = db.session.get(SchoolClass, ids["class"])
        before_midnight = BookingRequest(
            requester=teacher,
            school_class=school_class,
            subject="KIGALI-DAY-ONE",
            reason="Private",
        )
        after_midnight = BookingRequest(
            requester=teacher,
            school_class=school_class,
            subject="KIGALI-DAY-TWO",
            reason="Private",
        )
        db.session.add_all([before_midnight, after_midnight])
        db.session.flush()
        before_midnight.created_at = datetime(2026, 8, 1, 21, 59, tzinfo=UTC)
        after_midnight.created_at = datetime(2026, 8, 1, 22, 0, tzinfo=UTC)
        db.session.commit()
    login(client, "scheduler")
    response = client.get(
        "/reports/history?start_date=2026-08-01&end_date=2026-08-01"
    )
    assert b"KIGALI-DAY-ONE" in response.data
    assert b"KIGALI-DAY-TWO" not in response.data


def test_report_routes_do_not_modify_audit_history(client, app):
    scheduled(app)
    with app.app_context():
        before = db.session.scalar(db.select(db.func.count()).select_from(AuditLog))
    login(client, "admin")
    for path in REPORT_PATHS:
        assert client.get(path).status_code == 200
    with app.app_context():
        after = db.session.scalar(db.select(db.func.count()).select_from(AuditLog))
    assert after == before


@pytest.mark.parametrize(
    ("path", "valid_query", "invalid_query", "control_id"),
    [
        ("daily", "date=2026-08-01", "date=invalid", "date"),
        ("weekly", "date=2026-08-05", "date=invalid", "date"),
        ("history", "status=PENDING", "status=INVALID", "status"),
        (
            "rooms",
            "start_date=2026-08-01&end_date=2026-08-02",
            "start_date=invalid&end_date=2026-08-02",
            "start_date",
        ),
        (
            "classes",
            "start_date=2026-08-01&end_date=2026-08-02",
            "start_date=invalid&end_date=2026-08-02",
            "start_date",
        ),
    ],
)
def test_report_filter_accessibility_states(
    client, app, path, valid_query, invalid_query, control_id
):
    seed(app)
    login(client, "admin")
    valid = client.get(f"/reports/{path}?{valid_query}")
    assert valid.status_code == 200
    assert b'id="report-filter-errors"' not in valid.data

    invalid = client.get(f"/reports/{path}?{invalid_query}")
    assert invalid.status_code == 200
    assert b'id="report-filter-errors"' in invalid.data
    assert (
        f'id="{control_id}"'.encode() in invalid.data
        and b'aria-invalid="true"' in invalid.data
        and b'aria-describedby="report-filter-errors"' in invalid.data
    )
    assert b"Correct the filters above to run this report." in invalid.data
    report_form = invalid.data.split(b'<form method="get"', 1)[1].split(
        b"</form>", 1
    )[0]
    assert b"csrf_token" not in report_form
