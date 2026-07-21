# Entity-Relationship Diagram

```mermaid
erDiagram
    SCHOOL_CLASSES ||--o{ USERS : assigned_to_monitor
    USERS ||--o{ BOOKING_REQUESTS : submits
    USERS ||--o{ BOOKING_REQUESTS : responsible_teacher
    SCHOOL_CLASSES ||--o{ BOOKING_REQUESTS : requested_for
    BOOKING_REQUESTS ||--o| SCHEDULED_BOOKINGS : becomes
    ROOMS ||--o{ SCHEDULED_BOOKINGS : hosts
    SCHOOL_CLASSES ||--o{ SCHEDULED_BOOKINGS : scheduled_class
    USERS ||--o{ SCHEDULED_BOOKINGS : scheduled_teacher
    USERS ||--o{ SCHEDULED_BOOKINGS : scheduled_by
    ROOMS ||--o{ ROOM_BLOCKS : blocked_room
    USERS ||--o{ ROOM_BLOCKS : creates
    USERS ||--o{ ROOM_BLOCKS : removes
    USERS ||--o{ NOTIFICATIONS : receives
    BOOKING_REQUESTS ||--o{ NOTIFICATIONS : relates_to
    USERS ||--o{ AUDIT_LOGS : performs

    SCHOOL_CLASSES {
        bigint id PK
        varchar name UK
        boolean is_active
    }
    USERS {
        bigint id PK
        varchar username UK
        varchar password_hash
        varchar full_name
        varchar role
        bigint class_id FK
        boolean is_active
        boolean must_change_password
    }
    ROOMS {
        bigint id PK
        varchar name UK
        boolean is_active
    }
    BOOKING_REQUESTS {
        bigint id PK
        bigint requester_id FK
        bigint class_id FK
        bigint teacher_id FK
        varchar subject
        text reason
        varchar priority
        varchar status
        text rejection_reason
        timestamptz created_at
    }
    SCHEDULED_BOOKINGS {
        bigint id PK
        bigint request_id FK
        date schedule_date
        varchar prep
        bigint room_id FK
        bigint class_id FK
        bigint teacher_id FK
        bigint scheduled_by_id FK
        boolean is_active
    }
    ROOM_BLOCKS {
        bigint id PK
        date block_date
        varchar scope
        bigint room_id FK
        varchar prep
        text reason
        bigint created_by_id FK
        boolean is_active
    }
    NOTIFICATIONS {
        bigint id PK
        bigint user_id FK
        varchar type
        varchar title
        text message
        bigint booking_request_id FK
        boolean is_read
    }
    AUDIT_LOGS {
        bigint id PK
        bigint actor_id FK
        varchar action
        varchar entity_type
        bigint entity_id
        jsonb details
    }
    SYSTEM_SETTINGS {
        smallint id PK
        integer max_pending_requests
        integer reopen_threshold
        integer planning_window_days
        boolean booking_queue_locked
    }
```

## Role and scheduling constraints

- `USERS.role` is one of `ADMIN`, `SCHEDULER`, `TEACHER`, or `MONITOR`.
- `SCHEDULER` is displayed to users as Patron/Matron.
- Only `SCHEDULER` may initially approve and schedule a Pending request.
- `ADMIN` and `SCHEDULER` may reschedule or cancel an existing Scheduled booking.
- All application date calculations use `Africa/Kigali`; database timestamps remain timezone-aware.
- A room block must not overlap an active Scheduled booking within its slot, room-day, or day scope.
- Scheduled-booking and room-block mutations affecting a date share one deterministic PostgreSQL transaction-level advisory lock for that date. This cross-table lock is acquired before conflict checks; booking partial unique indexes remain the final booking-conflict defense.
