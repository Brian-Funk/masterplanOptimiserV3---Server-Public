FROM python:3.14-alpine

WORKDIR /app

# Container image builds install into an isolated root filesystem. Suppress
# pip's host-root warning and version notice, then validate the resolved set.
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Refresh Alpine security packages. The canonical activation QR renderer also
# requires a predictable local font.
RUN apk upgrade --no-cache \
    && apk add --no-cache font-dejavu openssh-client

# Install backend dependencies
COPY backend/requirements.txt /app/requirements.txt
COPY backend/requirements.lock.txt /app/requirements.lock.txt
RUN pip install --constraint requirements.lock.txt -r requirements.txt \
    && pip check \
    && python -m pip uninstall --yes pip

# Copy backend code
COPY backend/app /app/app
COPY deploy/evidence/evidence_manifest.py /app/evidence_manifest.py
COPY deploy/evidence /app/evidence
COPY web/public/logo_normal.png /app/app/assets/logo_normal.png

# The API does not need root privileges. A fixed numeric identity also keeps
# the runtime predictable when no matching account exists on the host.
RUN addgroup -S -g 10001 masterplan \
    && adduser -S -D -H -u 10001 -G masterplan masterplan \
    && chown -R 10001:10001 /app

# Static frontend is served by Caddy directly from web/out (mounted volume)

ENV API_HOST=0.0.0.0
ENV API_PORT=8000

EXPOSE 8000

# Only the adjacent Compose network proxy may supply forwarded headers.
USER 10001:10001
# Uvicorn's access log includes the raw peer address. The application emits a
# purpose-limited structured record instead, so the built-in logger is disabled.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "172.30.0.0/24", "--no-access-log"]
