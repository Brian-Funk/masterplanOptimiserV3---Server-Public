# Masterplan Optimiser Server

The Server repo hosts the collaborative web calendar for Masterplan Optimiser.
It receives published schedules from the desktop app, stores them securely, and
serves event-specific calendar views to participants.

The server is intentionally separate from the desktop application. The desktop
app remains the planning and optimisation tool, while this service handles
published schedules, passkey authentication, calendar access, announcements,
push notifications, audit trails, and participant data requests.

## Documentation Structure

- **Setup** covers local development prerequisites and first-run commands.
- **Architecture** explains the FastAPI backend, Next frontend, database models,
  and publish flow.
- **Workflows** describes normal operating flows for admins, issuers, and users.
- **Deployment** covers production configuration and update scripts.
- **Security** documents the main security controls and operational checks.
- **API Reference** is generated from Python docstrings and TypeScript JSDoc.

## Source Of Truth

Manual pages explain decisions and workflows. Generated pages document exported
Python and TypeScript APIs from source comments, so docstrings and JSDoc must be
kept current when behaviour changes.
