@echo off
REM Run autommo from the project root using the local virtual environment.

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment not found at .venv\Scripts\activate.bat
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m src.main

if errorlevel 1 pause
