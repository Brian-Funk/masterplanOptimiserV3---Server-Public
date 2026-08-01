"""Generate lightweight Python API reference pages for MkDocs."""

import mkdocs_gen_files


PAGES = {
    "generated/python/index.md": """# Python API Reference

This section is generated during the MkDocs build from the server backend
source. It covers the FastAPI application, route modules, security helpers,
publishing pipeline, notification services, and snapshot utilities.
""",
    "generated/python/backend.md": """# Backend API

## Application

::: app.main

## API Router

::: app.api.v1.router

## Activation

::: app.api.v1.activation

## Admin

::: app.api.v1.admin

## Authentication

::: app.api.v1.auth

## Calendar

::: app.api.v1.calendar

## GDPR

::: app.api.v1.gdpr

## History

::: app.api.v1.history

## Notifications

::: app.api.v1.notifications

## Passkeys

::: app.api.v1.passkey

## Public Schedule Links

::: app.api.v1.public_schedule_links

## Publishing

::: app.api.v1.publish
""",
    "generated/python/core.md": """# Core Services

## Activation

::: app.core.activation

## Audit

::: app.core.audit

## Diff

::: app.core.diff

## Permissions

::: app.core.permissions

## Push

::: app.core.push

## Runtime Settings

::: app.core.runtime_settings

## Security

::: app.core.security

## Sessions

::: app.core.sessions

## Snapshots

::: app.core.snapshots
""",
}


for path, content in PAGES.items():
    with mkdocs_gen_files.open(path, "w") as file:
        file.write(content)
