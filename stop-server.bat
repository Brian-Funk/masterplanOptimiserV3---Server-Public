@echo off
REM Stop the V3 GC server stack
cd /d "%~dp0"
cd infra
docker compose down
