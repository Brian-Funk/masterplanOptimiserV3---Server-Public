@echo off
title MP-OPT_SERVER - SSH Management Required
color 0E

echo ============================================================
echo   MP-OPT_SERVER production management runs on the VPS
echo ============================================================
echo.
echo   Connect to the VPS through SSH, then run:
echo.
echo       cd /opt/masterplan
echo       ./manage.sh
echo.
echo   On configured servers, you can also run:
echo.
echo       mp-opt
echo.
echo   This Windows compatibility file does not create or modify
echo   production configuration or secret files.
echo.
pause
