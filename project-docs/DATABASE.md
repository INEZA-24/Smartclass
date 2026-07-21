# Database Specification

## Database engine

Use PostgreSQL with SQLAlchemy and Flask-Migrate/Alembic.

## Enumerations

```text
UserRole: ADMIN, MATRON, TEACHER, MONITOR
RequestPriority: HIGH, NORMAL
RequestStatus: PENDING, SCHEDULED, REJECTED, CANCELLED
PrepPeriod: PREP_1, PREP_2
BlockScope: SLOT, ROOM_DAY, DAY
NotificationType: APPROVED, REJECTED, RESCHEDULED, CANCELLED, PASSWORD_RESET, SYSTEM
```

Use either PostgreSQL enums or validated strings consistently.

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
2. Validate planning-window date.
3. Confirm class, teacher, and room are active.
4. Check active blocks.
5. Insert ScheduledBooking.
6. Let unique indexes prevent race-condition conflicts.
7. Change request to Scheduled.
8. Create notification and audit log.
9. Update queue lock state.
10. Commit or roll back everything.

## Rescheduling transaction

1. Lock scheduled booking.
2. Validate target date, prep, room, and blocks.
3. Update schedule.
4. Let unique indexes verify availability.
5. Create notification and audit log.
6. Commit.

## Data retention

Use disable/archive or soft deletion for users, classes, rooms, schedules, and blocks. Historical requests and logs remain queryable.

## Migration policy

- Every schema change requires a migration.
- Do not recreate production tables.
- Review migrations before deployment.
- Seed operations must be idempotent.
