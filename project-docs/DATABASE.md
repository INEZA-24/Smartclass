# Database Specification

## Database engine

Use PostgreSQL with SQLAlchemy and Flask-Migrate/Alembic.

## Enumerations

```text
UserRole: ADMIN, SCHEDULER, TEACHER, MONITOR
RequestPriority: HIGH, NORMAL
RequestStatus: PENDING, SCHEDULED, REJECTED, CANCELLED
PrepPeriod: PREP_1, PREP_2
BlockScope: SLOT, ROOM_DAY, DAY
NotificationType: APPROVED, REJECTED, RESCHEDULED, CANCELLED, PASSWORD_RESET, SYSTEM
```

Use either PostgreSQL enums or validated strings consistently.

`SCHEDULER` has the user-facing label Patron/Matron. Only `SCHEDULER` may initially approve and schedule a Pending request. `ADMIN` and `SCHEDULER` may reschedule or cancel an existing Scheduled booking, but `ADMIN` must not initially schedule a Pending request.

Use `Africa/Kigali` for application date calculations, the public daily schedule, reminders, and planning-window calculations. Database timestamp columns remain timezone-aware.

Include the Python `tzdata` package in `requirements.txt` so `zoneinfo.ZoneInfo("Africa/Kigali")` also works in Windows local development. Add a test that loads this zone successfully.

## Tables

### users

| Column | Type | Rules |
|---|---|---|
| id | bigint | Primary key |
| username | varchar(80) | Unique, required, indexed |
| password_hash | varchar(255) | Required |
| full_name | varchar(150) | Required |
| role | enum/string | Required |
| class_id | bigint | Nullable FK; required for monitors |
| is_active | boolean | Default true |
| must_change_password | boolean | Default true for new/reset accounts |
| created_at | timestamptz | Required |
| updated_at | timestamptz | Required |

Rules:

- Only monitor accounts use `class_id`.
- Disabling a user must preserve historical records.

### school_classes

| Column | Type | Rules |
|---|---|---|
| id | bigint | Primary key |
| name | varchar(80) | Unique, required |
| is_active | boolean | Default true |
| created_at | timestamptz | Required |
| updated_at | timestamptz | Required |

Seed every class listed in `SRS.md`.

### rooms

| Column | Type | Rules |
|---|---|---|
| id | bigint | Primary key |
| name | varchar(80) | Unique, required |
| is_active | boolean | Default true |
| created_at | timestamptz | Required |
| updated_at | timestamptz | Required |

Seed Smart Class 1, 2, and 3.

### booking_requests

| Column | Type | Rules |
|---|---|---|
| id | bigint | Primary key |
| requester_id | bigint | Required FK to users |
| class_id | bigint | Required FK to school_classes |
| teacher_id | bigint | Required FK to users |
| subject | varchar(120) | Required |
| reason | text | Required and private |
| priority | enum/string | Required |
| status | enum/string | Required, indexed |
| rejection_reason | text | Nullable |
| created_at | timestamptz | Required, indexed |
| updated_at | timestamptz | Required |
| cancelled_at | timestamptz | Nullable |

Rules:

- Teacher request: requester equals teacher, priority High.
- Monitor request: requester role Monitor, class comes from monitor account, priority Normal.
- No date, prep, or room is stored here.

Recommended indexes:

- `(status, priority, created_at)`
- `requester_id`
- `class_id`
- `teacher_id`

### scheduled_bookings

| Column | Type | Rules |
|---|---|---|
| id | bigint | Primary key |
| request_id | bigint | Unique FK to booking_requests |
| schedule_date | date | Required, indexed |
| prep | enum/string | Required |
| room_id | bigint | Required FK to rooms |
| class_id | bigint | Required FK to school_classes |
| teacher_id | bigint | Required FK to users |
| scheduled_by_id | bigint | Required FK to users |
| is_active | boolean | Default true |
| created_at | timestamptz | Required |
| updated_at | timestamptz | Required |
| cancelled_at | timestamptz | Nullable |

Copy class and teacher from the request when scheduling so PostgreSQL can enforce direct uniqueness.

Required partial unique indexes for active bookings:

```sql
CREATE UNIQUE INDEX uq_active_room_slot
ON scheduled_bookings (schedule_date, prep, room_id)
WHERE is_active = true;

CREATE UNIQUE INDEX uq_active_class_slot
ON scheduled_bookings (schedule_date, prep, class_id)
WHERE is_active = true;

CREATE UNIQUE INDEX uq_active_teacher_slot
ON scheduled_bookings (schedule_date, prep, teacher_id)
WHERE is_active = true;
```

### room_blocks

| Column | Type | Rules |
|---|---|---|
| id | bigint | Primary key |
| block_date | date | Required, indexed |
| scope | enum/string | Required |
| room_id | bigint | Nullable FK to rooms |
| prep | enum/string | Nullable |
| reason | text | Nullable |
| created_by_id | bigint | Required FK to users |
| is_active | boolean | Default true |
| created_at | timestamptz | Required |
| removed_at | timestamptz | Nullable |
| removed_by_id | bigint | Nullable FK to users |

Scope rules:

- SLOT: room and prep required
- ROOM_DAY: room required, prep null
- DAY: room and prep null

A new block must be rejected if any active Scheduled booking falls within its scope:

- SLOT conflicts with the same date, room, and prep.
- ROOM_DAY conflicts with any active booking for that date and room.
- DAY conflicts with any active booking on that date.

The authorized user must reschedule or cancel affected bookings first. Creating a block never cancels or overwrites a booking.

### notifications

| Column | Type | Rules |
|---|---|---|
| id | bigint | Primary key |
| user_id | bigint | Required FK, indexed |
| type | enum/string | Required |
| title | varchar(150) | Required |
| message | text | Required |
| booking_request_id | bigint | Nullable FK |
| is_read | boolean | Default false |
| created_at | timestamptz | Required, indexed |
| read_at | timestamptz | Nullable |

### audit_logs

| Column | Type | Rules |
|---|---|---|
| id | bigint | Primary key |
| actor_id | bigint | Nullable FK to users |
| action | varchar(100) | Required, indexed |
| entity_type | varchar(80) | Required |
| entity_id | bigint | Nullable |
| details | jsonb | Nullable |
| created_at | timestamptz | Required, indexed |

Audit logs are append-only through normal routes.

### system_settings

Use one row.

| Column | Type | Rules |
|---|---|---|
| id | smallint | Primary key |
| max_pending_requests | integer | Default 12 |
| reopen_threshold | integer | Default 9 |
| planning_window_days | integer | Default 3 |
| booking_queue_locked | boolean | Default false |
| updated_at | timestamptz | Required |

The persistent lock is necessary because a pending count of 10 or 11 does not reveal whether the queue previously reached 12.

## Queue transaction

Submission:

1. Begin transaction.
2. Lock SystemSettings row with `SELECT FOR UPDATE`.
3. Count Pending requests.
4. Reject if queue is locked or count is already 12.
5. Insert request.
6. Lock queue if result reaches 12.
7. Commit.

Scheduling, rejection, or pending cancellation:

1. Begin transaction.
2. Lock request row.
3. Change status.
4. Recount Pending requests.
5. If locked and count is 9 or fewer, unlock.
6. Commit.

## Scheduling transaction

1. Lock Pending request.
2. Confirm the actor has role `SCHEDULER`; `ADMIN` cannot initially schedule a Pending request.
3. Validate that the date is the current `Africa/Kigali` date or one of the next two calendar dates.
4. Allow same-day scheduling without a time cutoff.
5. Acquire the shared PostgreSQL transaction-level advisory lock for the target schedule date.
6. Confirm class, teacher, and room are active.
7. Check bookings, active blocks, and all conflicts.
8. Insert ScheduledBooking.
9. Let partial unique indexes prevent final room, class, and teacher booking conflicts.
10. Change request to Scheduled.
11. Create notification and audit log.
12. Update queue lock state.
13. Commit or roll back everything.

## Rescheduling transaction

1. Lock scheduled booking.
2. Confirm the actor has role `ADMIN` or `SCHEDULER`.
3. Validate the target date against the current `Africa/Kigali` date plus the next two calendar dates; same-day rescheduling has no time cutoff.
4. Acquire the shared advisory locks for the old and target schedule dates in chronological order, acquiring only one lock when the date is unchanged.
5. Validate prep and room, then check bookings, blocks, and all conflicts.
6. Update schedule.
7. Let partial unique indexes verify final room, class, and teacher availability.
8. Create notification and audit log.
9. Commit.

## Date advisory-lock protocol

Scheduling, rescheduling, cancellation of a Scheduled booking, block creation, and block removal must acquire the same PostgreSQL transaction-level advisory lock keyed deterministically by every affected schedule date. Use one shared date-to-lock-key function for all five operations. The key must depend only on the date, not on room, prep, operation type, table, or process-local hashing.

Acquire date locks before reading or checking Scheduled bookings, room blocks, or conflicts. If an operation affects more than one date, acquire locks in chronological order to prevent deadlocks. Transaction-level locks must be released automatically on commit or rollback.

This protocol is required because `scheduled_bookings` and `room_blocks` are separate tables; booking partial unique indexes cannot prevent a concurrent booking and block from both passing their checks. Keep the existing active room, class, and teacher partial unique indexes as the final defense against booking-to-booking conflicts.

Cancellation of a Scheduled booking must lock its schedule date before checking or changing date availability. Block creation and block removal must lock `block_date` before checking bookings, blocks, or conflicts. Add transactional concurrency tests that start scheduling and blocking attempts simultaneously on the same date and prove that conflicting operations cannot both commit.

## Data retention

Use disable/archive or soft deletion for users, classes, rooms, schedules, and blocks. Historical requests and logs remain queryable.

## Migration policy

- Every schema change requires a migration.
- Do not recreate production tables.
- Review migrations before deployment.
- Seed operations must be idempotent.
