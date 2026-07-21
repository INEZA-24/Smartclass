# Development Plan

## Working method

For every milestone:

1. Start from a clean Git working tree.
2. Read `project-docs/`.
3. Ask Nemotron to implement one milestone only.
4. Run linting and tests.
5. Manually test the workflow.
6. Review `git diff`.
7. Upload changed files or a ZIP to ChatGPT for review.
8. Apply corrections.
9. Commit only after the review gate passes.

Recommended checks:

```bash
ruff check .
pytest
```

Before deployment:

```bash
bandit -r app
```

## Milestone 0 — Documentation lock

Deliverables:

- SRS
- Database specification
- ERD
- UI flow
- Prompt library
- Development plan
- Deployment checklist

Gate: no unresolved business-logic questions.

## Milestone 1 — Flask scaffold

Deliverables:

- Application factory
- Configuration
- Blueprints
- Base templates and Bootstrap
- SQLAlchemy, Flask-Migrate, Flask-Login, Flask-WTF
- Testing setup
- Gunicorn entry point
- Environment example

Gate:

- App starts
- Test route works
- No secrets committed
- Tests and lint pass

Suggested commit: `chore: scaffold Flask application`

## Milestone 2 — Database and seed data

Deliverables:

- All approved models
- Relationships and constraints
- Initial migration
- Partial unique indexes
- Idempotent seed command

Gate:

- Fresh database migrates
- Seed can run twice safely
- Constraint tests pass

Suggested commit: `feat: add core database schema`

## Milestone 3 — Authentication and authorization

Deliverables:

- Login/logout
- Password hashing
- Temporary-password flow
- Disabled-account protection
- Role authorization
- Basic dashboards

Gate:

- Cross-role access blocked
- CSRF works
- Authentication tests pass

Suggested commit: `feat: implement authentication and roles`

## Milestone 4 — Admin management

Deliverables:

- Users
- Classes
- Rooms
- Password reset
- Audit logs

Gate:

- Monitor validation works
- History protected
- Non-admin access blocked

Suggested commit: `feat: add admin management`

## Milestone 5 — Requests and queue

Deliverables:

- Teacher request form
- Monitor request form
- History
- Edit Pending
- Cancel Pending
- Priority ordering
- 12/9 queue lock

Gate:

- No date/prep/room fields for requesters
- Queue never exceeds 12
- Locked at 10 and 11
- Unlocks at 9
- Transaction tests pass

Suggested commit: `feat: add booking requests and queue control`

## Milestone 6 — Scheduler and blocks

Deliverables:

- Rolling three-day window
- Slot grid
- Schedule request
- Slot, room-day, and full-day blocking
- Conflict prevention
- Notifications
- Audit logs

Gate:

- All conflict tests pass
- Blocked slots cannot be scheduled
- Concurrent attempts cannot double-book

Suggested commit: `feat: add schedule builder and slot blocking`

## Milestone 7 — Rejection and schedule changes

Deliverables:

- Rejection
- Rescheduling
- Scheduled cancellation
- Queue-state recalculation
- Notifications and logs

Gate:

- Rescheduling transactional
- Old slot available
- New conflicts prevented
- History preserved

Suggested commit: `feat: add rejection and rescheduling workflows`

## Milestone 8 — Public schedule and notifications

Deliverables:

- Public current-day page
- Notification center
- Read/unread behavior
- Empty states

Gate:

- Private data never public
- Only current-date active schedules appear
- Public route needs no login

Suggested commit: `feat: publish daily schedule and notifications`

## Milestone 9 — Reports

Deliverables:

- Daily
- Weekly
- History
- Room usage
- Class usage
- Filters

Gate:

- Totals match test fixtures
- Access limited to Admin and Patron/Matron

Suggested commit: `feat: add booking reports`

## Milestone 10 — UI polish

Deliverables:

- Responsive layout
- Consistent Bootstrap components
- Validation feedback
- Confirmations
- Accessible labels
- Custom error pages

Gate:

- Desktop and mobile work
- Navigation complete
- No debug details exposed

Suggested commit: `style: polish responsive user interface`

## Milestone 11 — Deployment

Deliverables:

- Production config
- Render plan
- PostgreSQL connection
- Migration and seed procedure
- Health check

Gate:

- Gunicorn starts
- Migrations succeed
- Public page and login work
- Secrets use environment variables
- Smoke tests pass

Suggested commit: `chore: prepare Render deployment`

## Milestone 12 — Portfolio packaging

Deliverables:

- README
- Screenshots
- Architecture summary
- Demo instructions
- Known limitations
- Demo-video plan

Gate:

- Reviewer can understand and run project
- No private school data included
