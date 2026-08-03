# Demo Video Plan

## Target length and preparation

Target duration: approximately 4 minutes 30 seconds, within a 3-to-5-minute portfolio video. Record in a controlled demonstration environment using fictional data and prepared role-specific sessions. Crop the browser and terminal so no credentials, cookies, tokens, environment values, database URLs, private school information, or unrelated personal data are visible.

## Storyboard

| Time | Section | Demonstration | Sensitive information to hide |
|---|---|---|---|
| 0:00-0:20 | Opening problem | Explain that shared Smart Class rooms need a clear request queue, controlled scheduling, and automatic conflict checks. | School-internal incidents, staff names, student data, and unapproved branding details. |
| 0:20-0:45 | Public schedule | Show today's public timetable or its empty state. Point out prep, room, class, and Teacher fields and responsive layout. | Private reasons, subjects, priorities, internal notes, and any real names. |
| 0:45-1:05 | Login and role separation | Show the login page, then briefly compare prepared role dashboards and user-facing labels. Mention server-side authorization. | All typed credentials, password-manager prompts, session cookies, CSRF tokens, and private URLs containing data. |
| 1:05-1:35 | Teacher or Monitor request | Submit one fictional request. Show that a Teacher selects a class, while a Monitor uses the assigned class and selects a responsible Teacher. | Passwords, real people, real booking reasons, and identifying class data. |
| 1:35-2:10 | Patron/Matron scheduling | Show Teacher-first queue order and schedule the Pending request within the current date plus the next two dates. | Other requesters' private reasons and any unapproved account details. |
| 2:10-2:35 | Conflict prevention | Attempt a safe duplicate slot or demonstrate a blocked slot and show the precise rejection. Explain date advisory locking and database unique indexes without exposing database configuration. | Database URLs, hostnames, credentials, SQL logs, stack traces, and private block reasons. |
| 2:35-2:55 | Notifications | Return to the dummy requester and show the relevant approval or change notification and read state. | Notifications belonging to other users and private request content. |
| 2:55-3:20 | Administrator management | Show user, class, and room management and note that the Administrator cannot initially approve Pending requests. | Temporary passwords, usernames, audit payloads with private data, and real identities. |
| 3:20-3:40 | Reports | Show one daily or weekly report and one usage report using fictional fixtures and filters. | Operational schedules, real names, private reasons, and export or browser history. |
| 3:40-4:10 | Architecture and deployment | Display the architecture summary diagrams: browser, Render/Gunicorn, Flask services, direct TLS, and Neon PostgreSQL. Mention migrations, health checks, and environment-based secrets. | Render or Neon dashboards, environment values, connection strings, project identifiers, and logs containing internal details. |
| 4:10-4:30 | Closing summary | Recap privacy-safe public visibility, role separation, queue control, transactional scheduling, notifications, reports, and documented limitations. | Any credentials or claims that the demonstration is approved for operational school data. |

## Recording checklist

- Use dummy accounts and fictional requests prepared specifically for the recording.
- Mask the password field and cut any footage that reveals typed credentials.
- Close developer tools, password managers, terminal windows, and cloud dashboards unless their sanitized content is essential.
- Disable notifications from the operating system and unrelated applications.
- Keep the address bar visible only when it contains a safe public or local URL with no query secrets.
- Review the final video frame by frame for names, credentials, tokens, private reasons, connection strings, and background information.
- Describe free-tier cold starts and operational-data approval as limitations rather than hiding them.
