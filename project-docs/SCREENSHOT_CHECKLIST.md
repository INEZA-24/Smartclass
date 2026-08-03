# Screenshot Checklist

Approved screenshots will be added during Milestone 12B under `docs/screenshots/`. Do not create or publish a file until the screen has been populated with fictional data and reviewed for credentials, private reasons, tokens, identifiers, and background information.

| Recommended filename | Page or feature | Role required | What must be visible | What must be hidden | Suggested caption |
|---|---|---|---|---|---|
| `docs/screenshots/public-daily-schedule.png` | Public daily schedule | Public visitor | Current date, prep grouping, room, class, Teacher, or approved empty state | Subjects, private reasons, priority, internal notes, real identities, and private navigation | "The public view shows only today's approved Smart Class timetable." |
| `docs/screenshots/login-page.png` | Login page | Public visitor | Username and password labels, submit control, and public navigation | Typed credentials, password-manager suggestions, browser autofill, and error text containing entered data | "The secure login entry point for authorized staff roles." |
| `docs/screenshots/admin-dashboard.png` | Administrator dashboard | Administrator | Summary cards, Administrator label, and permitted navigation | Real names, usernames, notification contents, audit details, and credentials | "The Administrator dashboard summarizes managed resources and queue state." |
| `docs/screenshots/user-management.png` | User management | Administrator | Fictional account list, roles, active state, and available management actions | Real accounts, usernames used elsewhere, temporary passwords, reset values, and personal data | "Administrators manage role-based staff accounts without exposing passwords." |
| `docs/screenshots/teacher-request-form.png` | Teacher request form | Teacher | Class, subject, and private-reason fields; no date, prep, or room selector | A real private reason, real staff or student names, credentials, and unrelated notifications | "Teachers submit high-priority requests while scheduling details remain controlled by the Patron/Matron." |
| `docs/screenshots/monitor-request-form.png` | Monitor request form | Class Monitor | Assigned dummy class, responsible-Teacher selector, subject, and private-reason fields | Real class assignment, real people, real private reason, credentials, and any editable class control | "Class Monitors request for their assigned class and select a responsible Teacher." |
| `docs/screenshots/pending-queue.png` | Pending request queue | Patron/Matron | Pending count, open or locked state, Teacher-before-Monitor ordering, priority badges, and fictional entries | Private reasons unless essential and fictional, credentials, CSRF data, and real requester names | "The pending queue prioritizes Teacher requests and enforces the 12/9 lock rule." |
| `docs/screenshots/three-day-schedule-builder.png` | Rolling three-day schedule builder | Patron/Matron | Three `Africa/Kigali` dates, room and prep grid, and Available, Booked, and Unavailable states | Private reasons, internal block notes, real names, hidden form data, and production identifiers | "The rolling three-day builder presents conflict-checked room and prep availability." |
| `docs/screenshots/notifications.png` | Notification center | Teacher or Class Monitor | Fictional notification title, safe message, time, and read/unread treatment | Private booking reason, other users' messages, personal data, credentials, and internal IDs | "In-app notifications keep requesters informed of approved schedule changes." |
| `docs/screenshots/reports.png` | Reports | Administrator or Patron/Matron | Date filters, report type, fictional rows or totals, and privacy-safe usage data | Private reasons, real operational schedules, real identities, database details, and browser exports | "Authorized staff can review booking history and room or class usage." |
| `docs/screenshots/responsive-mobile-layout.png` | Responsive mobile layout | Public visitor or one dummy authenticated role | Narrow viewport, usable navigation, readable cards or table treatment, and clear controls | Device notifications, carrier details, personal browser tabs, credentials, and real data | "The interface remains usable on a privacy-safe mobile-sized viewport." |
| `docs/screenshots/custom-404-page.png` | Custom 404 page | Public visitor | Branded error heading, safe explanation, and public recovery link | Stack traces, framework details, filesystem paths, private navigation, environment names, and database details | "The custom 404 page provides a safe recovery path without exposing internals." |

## Approval checks

Before adding any screenshot:

1. Use only fictional, non-identifying records.
2. Inspect the complete image, including address bar, tabs, taskbar, notifications, and background windows.
3. Confirm no password, username, session value, CSRF token, secret key, database URL, environment value, private reason, or production credential is present.
4. Confirm the screen matches the documented role and does not imply public access to a private dashboard.
5. Use the recommended filename and add the image to the README only after the file exists and has been approved.
