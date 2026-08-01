@echo off
REM Rebuild and restart the V3 GC server stack
cd /d "%~dp0"

REM Rebuild frontend
cd web
call npm run build
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Frontend build failed
    pause
    exit /b 1
)

cd ..\infra
docker compose down
docker compose build --no-cache
docker compose up -d
