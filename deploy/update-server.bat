@echo off
setlocal enabledelayedexpansion
title MP-OPT Server  -  Update
color 0B

echo ============================================================
echo   MP-OPT Server  -  UPDATE
echo ============================================================
echo.
echo   This will:
echo     1. SSH into your VPS
echo     2. Pull the latest code from GitHub
echo     3. Rebuild ^& restart the containers
echo.

rem ════════════════════════════════════════════════════════════
rem  CONFIGURATION  -  loaded from deploy\.env
rem ════════════════════════════════════════════════════════════
if not exist "%~dp0.env" (
    echo   ERROR: deploy\.env not found!
    echo   Copy deploy\.env.example to deploy\.env and fill in your values.
    echo.
    pause
    exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
    set "%%A=%%B"
)

rem ════════════════════════════════════════════════════════════
rem  Validate
rem ════════════════════════════════════════════════════════════

if "!VPS_HOST!"=="YOUR_VPS_IP" (
    echo   ERROR: Set VPS_HOST in deploy\.env first!
    echo.
    pause
    exit /b 1
)

echo   Server:  !VPS_USER!@!VPS_HOST!
echo   App dir: !APP_DIR!
echo   Domain:  !DOMAIN!
echo.
set /p "CONFIRM=   Continue? [Y/n]: "
if /i "!CONFIRM!"=="n" (
    echo   Cancelled.
    pause
    exit /b 0
)

rem ════════════════════════════════════════════════════════════
rem  SSH and update
rem ════════════════════════════════════════════════════════════
echo.
echo [1/1] Connecting to !VPS_USER!@!VPS_HOST! ...
echo.

ssh -p !VPS_PORT! !VPS_USER!@!VPS_HOST! "set -e && umask 077 && cd '!APP_DIR!' && bash deploy/deploy.sh"

if errorlevel 1 (
    echo.
    echo   ERROR: SSH command failed. Check output above.
    echo.
    echo   Common fixes:
    echo     - If git pull reports local changes, inspect them on the VPS before retrying.
    echo     - To check container logs:
    echo       ssh !VPS_USER!@!VPS_HOST! "cd !APP_DIR! ^&^& docker compose -f infra/docker-compose.yml logs backend"
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   UPDATE COMPLETE
echo ============================================================
echo.
echo   Site: https://!DOMAIN!
echo.
pause
exit /b 0
