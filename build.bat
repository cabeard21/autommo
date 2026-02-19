@echo off
REM Build Cooldown Reader with PyInstaller (icon: cocktus.ico).
REM Run from project root: build.bat

cd /d "%~dp0"

if not exist "cocktus.ico" (
    echo WARNING: cocktus.ico not found in project root. Place it for exe icon.
)

if exist "venv\Scripts\activate.bat" (
    echo Activating venv...
    call venv\Scripts\activate.bat
)

pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo Building Cooldown Reader (output in dist\CooldownReader\)...
pyinstaller --noconfirm CooldownReader.spec

if errorlevel 1 exit /b 1
echo Done. Run: dist\CooldownReader\CooldownReader.exe
