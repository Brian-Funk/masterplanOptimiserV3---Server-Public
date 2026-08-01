@echo off
setlocal
python "%~dp0recovery_key_setup.py" generate %*
exit /b %ERRORLEVEL%
