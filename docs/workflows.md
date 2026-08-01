# Workflows

## Publish Schedule

1. The desktop app sends a publish payload to the server.
2. The server verifies the event publish secret.
3. Existing published people, tasks, edits, and related schedule state are
   replaced for that event.
4. A snapshot is stored so admins can inspect or restore previous states.
5. User links are refreshed by matching published person emails to server users.
6. Schedule changes are computed for notifications.

## Participant Calendar

Participants sign in, load their event calendar, and inspect task details. If
they have editing permission, they can create draft changes, edit their own
tasks where allowed, and submit batched updates.

## Shared Public Schedules

Root administrators and event issuers can create reusable links from the
`Public Links` administration tab. Every link has a required internal
description, expiry time, and one or more current Public Schedule views. The
description is never included in the public response.

The raw token is shown only when the link is created and the database stores
only its SHA-256 hash. The public page sends the token in an authorisation
header and receives only public Session Element programme fields. Masterplan
tasks, people, assignments, organiser notes, templates, and link-management
metadata are excluded. Active links can be edited without changing their URL;
expiry and invalidation are permanent.

## Admin Management

Root admins manage events and global users. Event admins manage users and
activation links within their event. Issuers can publish and manage specific
event workflows without receiving root-level access.

## Activation

Admins create activation links for users. A user opens the link, validates the
token, and completes passkey registration. Tokens are stored hashed and can be
expired, invalidated, or marked as used.

## Notifications

Users can subscribe to web push notifications. Announcements and schedule
changes are stored per event and delivered to active subscriptions where VAPID
configuration is available.

## History

Every publish can create a snapshot of the event schedule. Admins can list,
inspect, annotate, delete, and restore snapshots for their event.
