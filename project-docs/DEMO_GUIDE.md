# Demonstration Guide

## Public demonstration

Open the [live application](https://smart-class-management-system.onrender.com/) in a private browser window. The public landing page is safe to demonstrate without credentials and should show the current `Africa/Kigali` date, approved sessions grouped by prep, room, class, and Teacher, or an empty-state message when no session is scheduled.

A public reviewer can inspect:

- The current-day schedule and its privacy-limited fields
- The responsive public navigation
- The login page without submitting credentials
- The custom 404 page by visiting a nonexistent path
- The credential-free `/health` response

Public visitors cannot inspect authenticated dashboards, pending requests, private booking reasons, notifications, reports, or administration pages.

## Private role demonstration

Private workflows should be demonstrated only in a controlled local or explicitly authorized demonstration database. Create distinct dummy accounts through the Administrator interface for these internal roles:

- `ADMIN` (Administrator)
- `SCHEDULER` (Patron/Matron)
- `TEACHER` (Teacher)
- `MONITOR` (Class Monitor, assigned to an active dummy class)

Do not publish the usernames or passwords. Store no credentials in screenshots, recordings, documentation, source control, browser autofill, terminal history, or shared notes. If temporary passwords are used, demonstrate the forced-change flow without revealing either password.

## Using locally created dummy accounts

1. Prepare a local development database, apply migrations, and run the idempotent seed command as described in the [README](../README.md).
2. Create or provision an Administrator using the project's approved local workflow. Do not hard-code an account.
3. Through the Administrator interface, create active dummy Teacher, Patron/Matron, and Class Monitor accounts. Assign the Monitor to an active seeded class.
4. Use invented, non-identifying display names and fictional booking content.
5. Open separate private browser sessions for each role to avoid showing session cookies or mixing authorization states.
6. Remove or discard demonstration data after the review when retention is unnecessary.

## Safe sample scenario

1. Sign in as a dummy Teacher and submit a Pending request for an active class using a fictional subject and a neutral private reason.
2. Sign in as a dummy Class Monitor and submit a second request for the assigned class, selecting a dummy Teacher.
3. Sign in as the Patron/Matron and show that the Teacher request appears before the Monitor request while order remains oldest-first within each priority.
4. Schedule the Teacher request in an available date, prep, and room within the rolling three-day window.
5. Attempt a conflicting assignment to show that the same room, class, or Teacher cannot occupy the same date and prep twice. Do not save misleading data merely for the demonstration.
6. Return to the requester account and show the in-app approval notification.
7. Show the approved booking on the public schedule only if its date is today; confirm the private reason and subject are absent.
8. Use the Administrator account to demonstrate management pages and reports with dummy records only. Do not use the Administrator to perform initial scheduling.

## Privacy warning

Never use real student names, staff names, school-internal details, booking reasons, operational timetables, credentials, tokens, or production database information in a portfolio demonstration. The public deployment is a demonstration service and is not automatically authorized for operational school data. Follow the backup, retention, authorization, and recovery requirements in the [deployment runbook](DEPLOYMENT.md) before any institutional use.
