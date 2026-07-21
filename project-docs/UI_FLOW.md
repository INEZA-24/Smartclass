# User Interface and Workflow Specification

## Public pages

### Landing / daily schedule

Show:

- College Saint André identity
- Current date
- Today’s schedule grouped by prep
- Room
- Class
- Teacher
- Login button in the top-right

Hide:

- Booking reason
- Subject
- Priority
- Internal notes

Empty state:

> No Smart Class sessions are scheduled for today.

### Login

Fields:

- Username
- Password

Use generic invalid-credentials messages. Redirect by role after success. Force temporary-password change when required.

## Shared authenticated layout

- Top navigation
- Role label
- Notification indicator
- User menu
- Logout
- Responsive sidebar
- Flash messages

Status badges:

- Pending
- Scheduled
- Rejected
- Cancelled
- High Priority
- Normal Priority
- Available
- Booked
- Unavailable

## Teacher workflow

Dashboard cards:

- Pending requests
- Scheduled bookings
- Rejected requests
- Recent notifications

New request fields:

- Class
- Subject
- Private reason

Automatically set teacher, High priority, and Pending status. Do not show date, prep, or room.

Pending requests can be edited or cancelled.

## Monitor workflow

Dashboard cards match the teacher dashboard. The assigned class is displayed prominently.

New request fields:

- Responsible teacher
- Subject
- Private reason

Automatically set class from the monitor account, Normal priority, and Pending status. The monitor cannot change the assigned class.

## Patron/Matron workflow

### Dashboard

1. Queue status
   - Pending count out of 12
   - Open/locked state
   - Reopen threshold when locked
2. Priority queue
   - Teachers first
   - Monitors second
   - Oldest first within each group
3. Three-day overview
   - Booked, available, and unavailable slot totals
4. Reminder banner
   - Prepare tomorrow’s schedule
   - Pending request count
   - Tomorrow’s remaining capacity

### Request review

Show:

- Requester
- Role
- Priority
- Class
- Teacher
- Subject
- Private reason
- Submission time

Actions:

- Schedule
- Reject

### Schedule form

Select:

- Date inside rolling three-day window
- Prep 1 or Prep 2
- Active room

Show slot availability before submission. On conflict, do not save and show a precise error.

### Day schedule grid

| | Smart Class 1 | Smart Class 2 | Smart Class 3 |
|---|---|---|---|
| Prep 1 | state | state | state |
| Prep 2 | state | state | state |

Available slot actions:

- Assign request
- Block slot

Booked slot actions:

- View
- Reschedule
- Cancel

Unavailable slot actions:

- View reason
- Unblock

Day-level actions:

- Block one room for the day
- Block entire day

## Administrator workflow

Dashboard cards:

- Active users
- Pending requests
- Scheduled today
- Active rooms
- Queue state

Navigation:

- Users
- Classes
- Rooms
- Requests
- Schedule
- Reports
- Audit logs
- Settings

User actions:

- Create
- Edit
- Disable
- Reset password

Monitor role requires an assigned class.

Class and room records with history are archived/disabled, not hard-deleted.

## Notifications

Display:

- Title
- Message
- Time
- Read/unread state
- Related request link when relevant

Actions:

- Mark one read
- Mark all read

## Reports

Pages:

- Daily
- Weekly
- Booking history
- Room usage
- Class usage

Filters:

- Date range
- Room
- Class
- Status
- Requester role

Charts are optional and must not delay version 1.

## Error pages

Custom:

- 403
- 404
- 500

Never expose stack traces in production.
