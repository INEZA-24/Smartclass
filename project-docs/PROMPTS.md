# Controlled Nemotron Prompts

## Global instruction

Use at the beginning of every major task:

```text
You are implementing the Smart Class Management System.

Before changing code, read every file in project-docs/, especially SRS.md, DATABASE.md, and DEVELOPMENT_PLAN.md.

Rules:
1. Do not invent features or requirements.
2. Do not change the approved stack.
3. Make the smallest complete change required for the current milestone.
4. Preserve existing working behavior.
5. Use a Flask application factory and blueprints.
6. Use PostgreSQL, SQLAlchemy, Flask-Migrate, Flask-Login, Flask-WTF, and Bootstrap 5.
7. Use transactions for queue, scheduling, rescheduling, rejection, and cancellation.
8. Never hard-code secrets.
9. Add or update tests for every business rule.
10. At the end, report files changed, migrations, commands, tests, and risks.
Do not begin unrelated milestones.
```

## 1. Repository scaffold

```text
Implement only Milestone 1 from project-docs/DEVELOPMENT_PLAN.md.

Create the Flask scaffold with application factory, configuration, SQLAlchemy, Flask-Migrate, Flask-Login, Flask-WTF, Bootstrap templates, blueprints, pytest, Ruff, .env.example, .gitignore, requirements.txt, and a Gunicorn-compatible entry point.

Do not implement booking logic.
```

## 2. Database models

```text
Implement only the database milestone. Follow DATABASE.md exactly.

Create User, SchoolClass, Room, BookingRequest, ScheduledBooking, RoomBlock, Notification, AuditLog, and SystemSettings.

Add relationships, validation, indexes, partial unique indexes, initial migration, and an idempotent seed command.

Add tests for monitor class requirement, priority assignment, scheduling uniqueness, and block-scope validation.
```

## 3. Authentication

```text
Implement authentication and authorization only.

Add username/password login, secure hashing, logout, disabled-account rejection, temporary-password change, role authorization, CSRF protection, and minimal role dashboards.

Test cross-role access, disabled users, and temporary-password enforcement.
```

## 4. Admin management

```text
Implement Admin management only.

Add user, class, and room management; password reset; archive/disable behavior; role-sensitive validation; and audit logs.

Use Bootstrap forms and tables. Add permission and validation tests.
```

## 5. Request submission and queue

```text
Implement request submission and pending queue control only.

Teacher form: class, subject, private reason; teacher auto-set; priority HIGH.
Monitor form: teacher, subject, private reason; class auto-set; priority NORMAL.

Do not add date, prep, or room fields.

Implement exact 12/9 hysteresis with a PostgreSQL transaction and locked SystemSettings row.

Test maximum 12, locked at 11 and 10, unlock at 9, and edit/cancel only while Pending.
```

## 6. Scheduler and blocking

```text
Implement the Patron/Matron scheduler only.

Add rolling three-day window, priority ordering, six calculated slots per full day, Available/Booked/Unavailable states, single-slot blocks, room-day blocks, full-day blocks, unblocking, and scheduling.

Enforce room, class, teacher, and block conflicts in one transaction. Create notifications and audit logs. Test every conflict and block scope.
```

## 7. Rejection, rescheduling, cancellation

```text
Implement rejection, rescheduling, and cancellation only.

Patron/Matron rejects Pending requests with a reason. Requesters cancel Pending requests. Patron/Matron and Admin reschedule or cancel Scheduled bookings.

Apply all conflict checks again, recalculate queue state, and create notifications and audit logs. Add transactional tests.
```

## 8. Public schedule and notifications

```text
Implement the public current-day schedule and notification center only.

Public page shows prep, room, class, and teacher. Hide subject, reason, priority, and internal notes. Add an empty state.

Add notification list, unread count, mark one read, and mark all read. Test date filtering and private-data exposure.
```

## 9. Reports

```text
Implement version 1 reports only: daily, weekly, booking history, room usage, and class usage.

Add approved filters. Do not add PDF export. Keep queries efficient and tested.
```

## 10. Deployment audit

```text
Perform a deployment-readiness audit without adding product features.

Check Gunicorn, requirements, environment variables, DATABASE_URL handling, secure production config, migrations, seed command, static files, error handling, health route, Render configuration, secrets, tests, and linting.

Show the deployment plan before applying infrastructure changes.
```

## Review prompt

```text
Review the current uncommitted diff against project-docs/. Do not add features.

Find requirement violations, security weaknesses, missing authorization, transaction errors, race conditions, data-integrity problems, migration issues, missing tests, duplication, and unnecessary complexity.

Return findings by severity with file paths and exact fixes.
```
