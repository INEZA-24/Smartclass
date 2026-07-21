# Smart Class Management System - Project Documentation

This folder is the source of truth for the Smart Class Management System.

## Documents

- `SRS.md` - software requirements specification
- `DATABASE.md` - PostgreSQL data model and transaction rules
- `ERD.md` - Mermaid entity-relationship diagram
- `UI_FLOW.md` - pages and user workflows
- `PROMPTS.md` - controlled prompts for Nemotron 3 Ultra
- `DEVELOPMENT_PLAN.md` - milestones and review gates
- `DEPLOYMENT.md` - Flask/PostgreSQL/Render deployment checklist

## Core rule

Nemotron must not invent features absent from these documents. Requirement changes should be recorded here before code is modified.

## Finalized conventions

- Authenticated internal roles are `ADMIN`, `SCHEDULER`, `TEACHER`, and `MONITOR`; display `SCHEDULER` as Patron/Matron.
- Only `SCHEDULER` initially approves and schedules Pending requests. `ADMIN` and `SCHEDULER` may reschedule or cancel existing Scheduled bookings.
- Application dates use `Africa/Kigali`. The planning window is the current date plus the next two calendar dates.
- Python dependencies include `tzdata` for reliable `ZoneInfo("Africa/Kigali")` loading on Windows.
- Booking and block mutations use a shared deterministic PostgreSQL transaction-level advisory lock for every affected date before conflict checks.
- Markdown files are stored as UTF-8 and prefer plain apostrophes and punctuation for reliable display.
