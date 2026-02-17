@echo off
REM Run Cooldown Reader as administrator.
REM Right-click this file -> Run as administrator, or create a shortcut and set the shortcut to "Run as administrator".

cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
  call venv\Scripts\activate.bat
)

python -m src.main

if errorlevel 1 pause
