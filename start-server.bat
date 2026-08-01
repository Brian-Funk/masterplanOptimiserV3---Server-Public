@echo off
REM Start the V3 GC server stack locally
cd /d "%~dp0"

REM Build frontend (skip npm install  -  run bootstrap.bat first time)
cd web
call npm run build
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Frontend build failed
    pause
    exit /b 1
)

cd ..\infra
docker compose up --build
