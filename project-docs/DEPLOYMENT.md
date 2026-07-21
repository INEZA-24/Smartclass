# Deployment Checklist

## Target architecture

```text
Browser
  -> Render web service
  -> Gunicorn
  -> Flask
  -> Hosted PostgreSQL
```

Development can run locally while using the hosted PostgreSQL database.

Git is recommended locally for rollback. GitHub is optional for terminal-assisted deployment and useful later for portfolio presentation.

## Required files

- `requirements.txt`
- `.env.example`
- `.gitignore`
- Gunicorn-compatible entry point
- Flask-Migrate configuration
- Optional `render.yaml`
- Health route
- Idempotent seed command
- README deployment section

`requirements.txt` must include `tzdata` so `zoneinfo.ZoneInfo("Africa/Kigali")` works during Windows local development as well as deployment.

## Environment variables

Required:

```text
DATABASE_URL
SECRET_KEY
```

Recommended:

```text
APP_ENV=production
SESSION_COOKIE_SECURE=true
```

The application timezone is fixed to `Africa/Kigali`. Use it for all application date calculations, public daily schedules, reminders, and planning-window calculations. Keep PostgreSQL timestamps timezone-aware.

Never commit `.env`, passwords, secret keys, Render credentials, or OpenCode credentials.

## PostgreSQL URL

Normalize the provider URL only when required by the installed SQLAlchemy/PostgreSQL driver. Never print the complete URL in logs.

## Production server

Use Gunicorn, not Flask's development server.

A common application-factory start command is:

```text
gunicorn "app:create_app()"
```

Verify the exact command against the final project structure.

## Safe deployment sequence

1. Provision PostgreSQL.
2. Configure environment variables.
3. Install dependencies.
4. Run migrations.
5. Run the idempotent seed command.
6. Start Gunicorn.
7. Check health route.
8. Test login and public schedule.

Never fix migration problems by deleting production data.

## Terminal-assisted Render workflow

1. Ask OpenCode to inspect the repository.
2. Ask it to show the proposed deployment plan.
3. Verify service name, commands, environment-variable names, and database target.
4. Apply only after the plan is correct.
5. Inspect build and runtime logs.
6. Run smoke tests.

OpenCode must not expose or commit secrets.

## Smoke tests

- Public page loads
- Current date is correct
- `ZoneInfo("Africa/Kigali")` loads successfully
- Login works
- Disabled user cannot log in
- Teacher can submit
- Monitor can submit
- Queue count updates
- Patron/Matron (`SCHEDULER`) can initially approve and schedule Pending requests
- Administrator cannot initially approve or schedule Pending requests
- Administrator and Patron/Matron can reschedule or cancel existing Scheduled bookings
- Conflicts are blocked
- Blocks fail when an active Scheduled booking exists in their scope
- Simultaneous scheduling and blocking attempts on the same date cannot both commit when they conflict
- Current-date and three-day-window behavior uses Africa/Kigali
- Today's booking appears publicly
- Notifications appear
- Admin pages are protected
- Error pages work

## Recovery preparation

Before major migrations:

- Export or snapshot database where supported
- Commit code locally
- Keep migrations under version control
- Record the deployed commit

## Save as deployment evidence

- Deployment plan
- Build logs
- Migration logs
- Smoke-test results
- Environment-variable names without values
- Deployed URL
- Known limitations
