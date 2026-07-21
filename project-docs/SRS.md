# Smart Class Management System (SCMS)

## Software Requirements Specification

**Version:** 1.0  
**Institution:** College Saint André  
**Application type:** Web-based booking and scheduling system  
**Stack:** Python, Flask, Jinja2, Bootstrap 5, SQLAlchemy, PostgreSQL, Flask-Migrate, Gunicorn, Render

## 1. Purpose

SCMS digitizes Smart Class requests, scheduling, conflict prevention, notifications, reports, and the public daily timetable.

Teachers and class monitors submit requests without selecting a date, prep, or room. The Patron/Matron assigns those values during approval.

## 2. Design principles

1. Keep the workflow simple.
2. Patron/Matron controls scheduling.
3. Teacher requests have high priority but still require approval.
4. Invalid schedules are blocked automatically.
5. Empty slots remain available unless manually blocked.
6. Sensitive configuration is stored only in environment variables.
7. Every milestone must remain testable and deployable.
8. Nemotron must not invent requirements.

## 3. Version 1 scope

Included:

- Username/password authentication
- Role-based access control
- Admin-created accounts
- Booking requests
- Teacher-priority queue
- Maximum pending queue of 12
- Queue reopening at 9 or fewer
- Rolling three-day schedule builder
- Conflict detection
- Slot, room-day, and full-day blocking
- Approval, rejection, cancellation, and rescheduling
- In-app notifications
- Public current-day schedule
- Daily, weekly, room-usage, class-usage, and history reports
- Audit logging
- PostgreSQL migrations
- Render deployment

Excluded:

- Attendance
- Marks and grading
- Email or SMS
- AI-generated schedules
- Calendar integration
- PDF exports
- Student accounts

## 4. Roles

### Administrator

- Create, edit, disable, and reset user accounts
- Assign roles
- Manage classes and rooms
- View and modify schedules
- Block/unblock slots
- View reports and audit logs

### Patron/Matron

- View pending requests
- See teacher requests first with a High Priority badge
- Schedule, reject, reschedule, and cancel
- Block/unblock slots, rooms, or entire days
- View reports and scheduling reminders

### Teacher

- Submit request
- Choose class
- Enter subject and private reason
- Edit/cancel while pending
- View history and notifications

Teacher requests are High Priority.

### Class Monitor

- Submit request for the class assigned to the account
- Select responsible teacher
- Enter subject and private reason
- Edit/cancel while pending
- View history and notifications

Monitor requests are Normal Priority.

### Public user

No login. Can see only today’s approved schedule:

- Class
- Smart Class room
- Prep
- Teacher

Private reasons are never public.

## 5. School classes

Initial seed data:

- S1 A, B, C, D
- S2 A, B, C, D
- S3 A, B, C, D
- S4 MSI A, B, C, D
- S4 MSII A, B, C, D
- S5 MSI A, B, C, D
- S5 MSII A, B, C, D
- S6 MPC, MPG, MCB, PCB, PCM

Classes with historical records must be archived/disabled rather than physically deleted.

## 6. Rooms and preps

Initial rooms:

- Smart Class 1
- Smart Class 2
- Smart Class 3

Preps:

- Prep 1
- Prep 2

A fully available day has six slots. A slot state is:

- Available
- Booked
- Unavailable

No booking and no block means Available.

## 7. Authentication

- Login uses username and password.
- Only Admin creates accounts.
- Monitor accounts require an assigned class.
- Passwords are securely hashed.
- Admin password reset creates a temporary password.
- User must change a temporary password after login.
- Disabled users cannot log in.

## 8. Booking requests

### Teacher form

- Class
- Subject
- Private reason

Teacher is automatically the logged-in user.

### Monitor form

- Responsible teacher
- Subject
- Private reason

Class is automatically the monitor’s assigned class.

Users never choose:

- Date
- Prep
- Room

Statuses:

- Pending
- Scheduled
- Rejected
- Cancelled

Only Pending requests may be edited/cancelled by requesters.

## 9. Pending queue

Ordering:

1. Teacher requests
2. Monitor requests
3. Oldest first within each priority group

Rules:

- Maximum pending requests: 12
- When the twelfth request is accepted, submissions lock
- Queue stays locked at 10, 11, or 12 pending
- Queue reopens only at 9 or fewer
- Scheduled, rejected, and cancelled requests do not count
- Capacity checks must run in a PostgreSQL transaction

## 10. Three-day schedule builder

Patron/Matron sees a rolling three-day window and may assign a Pending request to any available slot by selecting:

- Date
- Prep
- Room

The system shows:

- Available slots
- Booked slots
- Unavailable slots
- Remaining daily capacity
- Priority queue

The system does not automatically reserve all three days. Unused slots remain available.

## 11. Blocking

Only Patron/Matron or Admin can block:

1. One exact slot: date + prep + room
2. One room for a full day
3. The entire day

Blocks may have an internal reason and must be audited. They can be removed by authorized users.

## 12. Scheduling workflow

1. Confirm request is still Pending.
2. Confirm date is inside planning window.
3. Confirm class, teacher, and room are active.
4. Confirm slot is not blocked.
5. Check all conflicts.
6. Save schedule in one transaction.
7. Change request to Scheduled.
8. Recalculate queue lock.
9. Notify requester.
10. Write audit log.

## 13. Conflict rules

Reject schedules causing:

- Same room, date, and prep twice
- Same class, date, and prep twice
- Same teacher, date, and prep twice
- Booking in a blocked slot
- Booking with a disabled room, class, or teacher

Application checks must be backed by PostgreSQL constraints where possible.

## 14. Rejection

Patron/Matron may reject a Pending request with a reason. The request leaves the queue, the requester is notified, queue state is recalculated, and an audit log is created.

## 15. Rescheduling

Only Patron/Matron or Admin can reschedule. They choose a new date, prep, and room. All conflict rules run again. The user receives a Booking Changed notification.

## 16. Cancellation

- Requester may cancel a Pending request.
- Patron/Matron or Admin may cancel a Scheduled booking.
- Historical data remains for reports.

## 17. Public schedule

The public route automatically filters by the current date and shows:

- Prep
- Room
- Class
- Teacher

It hides reason, subject, priority, and internal notes. An empty-state message appears when nothing is scheduled.

## 18. Notifications

Users receive:

- Request approved
- Request rejected
- Booking changed
- Booking cancelled

Patron/Matron dashboard shows a reminder to prepare tomorrow’s schedule using pending count, tomorrow’s capacity, and tomorrow’s existing schedule.

## 19. Reports

- Daily bookings
- Weekly bookings
- Booking history
- Room usage
- Class usage

Filters may include date range, status, room, class, and requester role.

## 20. Audit logs

Audit important actions including user changes, password resets, submissions, edits, cancellations, rejection, scheduling, rescheduling, blocking, and unblocking.

Each log records actor, action, entity, timestamp, and structured details.

## 21. UI

- Bootstrap 5
- Responsive desktop/mobile layout
- Clear role navigation
- Status badges
- High Priority badge
- Accessible labels and validation
- Confirmation dialogs
- Empty states
- Minimal animation
- Custom 403, 404, and 500 pages

## 22. Security

- Secure password hashing
- Flask-Login or equivalent
- CSRF protection
- Server-side authorization
- SQLAlchemy parameterized queries
- Jinja auto-escaping
- Input validation
- Secure production cookies
- Environment variables for secrets
- Generic login errors

## 23. Reliability

- Use migrations for all schema changes.
- Use transactions for queue, scheduling, rescheduling, rejection, and cancellation.
- Preserve historical records.
- Prefer archive/disable over hard deletion.

## 24. Deployment

Production uses:

- Flask application factory
- Gunicorn
- PostgreSQL
- Flask-Migrate
- Render
- OpenCode Render integration

Required environment variables:

- DATABASE_URL
- SECRET_KEY

The Flask development server must not be used in production.

## 25. Acceptance criteria

Version 1 is accepted when:

1. Admin manages accounts, classes, and rooms.
2. Teachers and monitors submit valid requests.
3. Users cannot select date, prep, or room.
4. Teacher requests appear first.
5. Pending count never exceeds 12.
6. Queue remains locked until count reaches 9 or fewer.
7. Patron/Matron schedules inside a rolling three-day window.
8. Slots correctly show Available, Booked, or Unavailable.
9. Blocking and unblocking work.
10. All conflicts are prevented.
11. Rescheduling is safe and transactional.
12. Notifications work.
13. Public page shows only today’s schedule.
14. Reports are correct.
15. Unauthorized access is blocked.
16. App runs on PostgreSQL with Gunicorn and Render.
