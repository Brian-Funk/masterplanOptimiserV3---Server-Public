#!/usr/bin/env bash
# Compatibility launcher for the guarded MP-OPT_SERVER production wizard.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT_DIR/manage.sh"
