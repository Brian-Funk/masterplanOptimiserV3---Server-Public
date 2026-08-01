# Architecture

## Components

The server has three main parts:

- **FastAPI backend** in `backend/app`, responsible for authentication,
  published schedule storage, admin workflows, notifications, audit logs, and
  GDPR operations.
- **Next/React frontend** in `web/src`, responsible for the participant calendar,
  login, activation, admin screens, and installable PWA behaviour.
- **Deployment assets** in `infra` and `deploy`, responsible for container
  configuration, reverse-proxy setup, and production updates.

## Data Flow

The desktop app publishes a schedule with an event-level secret. The server
authenticates that publish request, replaces the published event data, stores a
snapshot, computes participant-facing changes, and exposes the schedule through
calendar endpoints.

Participants authenticate with passkeys or activation links. Their calendar view
is scoped to their event and role. Admin and issuer permissions are enforced in
backend dependencies and middleware, not only in the frontend.

## Backend Layers

- `api/v1` contains route modules for auth, passkeys, calendar data, publishing,
  history, notifications, GDPR, and administration.
- `core` contains reusable services for sessions, permissions, audit logging,
  runtime settings, push notifications, snapshots, schedule diffs, and activation
  links.
- `models` contains SQLAlchemy persistence models.
- `db` owns the database engine and session dependency.

## Frontend Layers

- Route pages under `web/src/app` are user-facing screens.
- Context providers manage authentication and theme state.
- Components provide calendar display, task details, admin workflows, PWA
  prompts, notification controls, and shared UI elements.
- `lib` contains API, environment, colour, brand, and re-authentication helpers.
