@echo off
setlocal enabledelayedexpansion
REM ── First-time setup for local development ──
REM Generates .env if missing, installs frontend deps, builds, then starts.
cd /d "%~dp0"

if not exist ".env" (
    echo === Generating .env for local development ===
    for /f "usebackq" %%a in (`powershell -NoProfile -Command "$b = New-Object byte[] 48; [System.Security.Cryptography.RandomNumberGenerator]::Fill($b); [Convert]::ToBase64String($b)"`) do set "SECRET_KEY=%%a"
    > .env echo DATABASE_URL=postgresql://masterplan:masterplan@db:5432/masterplan
    >>.env echo SECRET_KEY=!SECRET_KEY!
    >>.env echo CORS_ORIGINS=["https://localhost"]
    >>.env echo WEBAUTHN_RP_ID=localhost
    >>.env echo WEBAUTHN_RP_NAME=GC Calendar
    >>.env echo WEBAUTHN_ORIGIN=https://localhost
    >>.env echo COOKIE_SECURE=true
    >>.env echo DOMAIN=localhost
    >>.env echo SESSION_TTL_HOURS=8
    >>.env echo SESSION_TTL_HOURS_ADMIN=1
    echo   .env created for localhost.
)

echo === Installing frontend dependencies ===
cd web
call npm install
if %ERRORLEVEL% neq 0 (
    echo [ERROR] npm install failed
    pause
    exit /b 1
)

echo === Building frontend ===
call npm run build
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Frontend build failed
    pause
    exit /b 1
)

echo === Starting Docker services ===
cd ..\infra
docker compose up --build

pause
