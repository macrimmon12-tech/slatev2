@echo off
rem Launches SLATE v2. Run from anywhere -- it cd's to its own location first.
rem First run creates a virtual environment and installs dependencies
rem (pygame-ce, lupa, simpleeval); every run after that is fast.
setlocal
cd /d "%~dp0"

if not exist .venv (
    echo First run: setting up a virtual environment and installing dependencies...
    python -m venv .venv
    if errorlevel 1 (
        echo Could not create a virtual environment. Is Python 3.11+ installed and on PATH?
        echo Get it from https://python.org/downloads/ ^(check "Add python.exe to PATH" during install^).
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    python -m pip install -e ".[dev]"
    if errorlevel 1 (
        echo Dependency install failed -- see the error above.
        pause
        exit /b 1
    )
) else (
    call .venv\Scripts\activate.bat
)

python -m engine.game_loop
if errorlevel 1 (
    echo.
    echo SLATE exited with an error -- see above.
    pause
)
