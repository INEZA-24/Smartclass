# Known Limitations

## Version 1 functional scope

The Smart Class Management System deliberately does not provide:

- Attendance management
- Marks, grading, or academic assessment
- Student accounts
- Email notifications
- SMS notifications
- AI-generated schedules
- External calendar integration
- PDF exports

Notifications are available only inside the application. Scheduling remains a controlled human decision: the Patron/Matron reviews requests and chooses a date, prep, and room. Weekends and holidays are not automatically excluded; authorized staff must create a full-day block when Smart Classes are unavailable.

## Demonstration hosting

The demonstration application has been deployed to Render and uses Neon PostgreSQL. The public application, health endpoint, login page, protected-route behavior, custom 404 page, and debugger-exposure checks passed the non-mutating deployment smoke test.

This successful deployment does not prove backup restoration, disaster recovery, institutional authorization, production capacity, long-term availability, or suitability for real operational school data. Free services may sleep when idle, so the first request after inactivity can experience a cold start. Free-tier compute, connection, storage, availability, interactive-shell, and support limits may also affect responsiveness and operations.

## Data authorization

The demonstration deployment is not automatically approved for real school, student, staff, timetable, or booking data. Operational use requires explicit institutional authorization and a review of access control, data ownership, applicable policy, incident response, and the acceptable handling of private booking reasons.

Portfolio screenshots, recordings, and sample records must use fictional, non-identifying data. Public pages must continue to omit the subject, private reason, priority, and internal notes.

## Backup, retention, and recovery

Before operational use, the institution must define and approve:

- Which database records and logs are retained and for how long
- Who owns backups and who may restore them
- The available Neon backup, branch, export, snapshot, or restore-point capability and its retention period
- Tested restoration procedures and responsible personnel
- Recovery-time and recovery-point objectives
- Incident response and service-continuity procedures
- A safe application rollback process compatible with the current database schema

Free tiers may not provide the backup retention, availability, capacity, or recovery guarantees required for operational school use. Application rollback must not automatically downgrade migrations or delete and recreate production tables.

## Related documentation

- [Software requirements](SRS.md)
- [Deployment runbook](DEPLOYMENT.md)
- [Demonstration guide](DEMO_GUIDE.md)
