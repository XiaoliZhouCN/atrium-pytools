@echo off
rem ===========================================================================
rem  run_tarotdraw.bat - draw tarot cards (Windows one-click runner)
rem
rem  Usage:
rem    run_tarotdraw.bat                       draw 1 card
rem    run_tarotdraw.bat 3                     draw 3 cards (past/present/future)
rem    run_tarotdraw.bat --spread cross        Celtic Cross (10 cards)
rem    run_tarotdraw.bat 3 --seed 42           reproducible draw
rem    run_tarotdraw.bat 1 --html draw.html    also export a single-file HTML
rem    run_tarotdraw.bat --list-spreads        list spreads
rem    run_tarotdraw.bat --list-decks          list meaning sources
rem
rem  Dependency: the workspace venv Manager\.venv only (no third-party packages).
rem  NOTE: this file is intentionally ASCII-only, so cmd.exe parses it under any
rem        code page. Chinese text is printed by Python, not by this script.
rem ===========================================================================
setlocal

set "WORKSPACE=D:\Repositories\Manager"
set "TOOLDIR=%WORKSPACE%\AtriumPyTools\tools\tarotdraw"
set "VENV_PYTHON=%WORKSPACE%\.venv\Scripts\python.exe"
set "TAROTDRAW_CMD=%WORKSPACE%\.venv\Scripts\tarotdraw.exe"

if not exist "%VENV_PYTHON%" (
    echo [ERROR] venv interpreter not found: %VENV_PYTHON%
    echo         the workspace venv should be %WORKSPACE%\.venv
    exit /b 1
)

if not exist "%TOOLDIR%\tarotdraw\cli.py" (
    echo [ERROR] tool sources not found: %TOOLDIR%\tarotdraw\cli.py
    exit /b 1
)

rem Let python import the tarotdraw package.
rem ORDER MATTERS: tools\tarotdraw must come BEFORE tools, otherwise the
rem namespace package tools\tarotdraw\ (which has no __init__.py) shadows the
rem real package tools\tarotdraw\tarotdraw\ and "-m tarotdraw" fails.
set "PYTHONPATH=%TOOLDIR%;%WORKSPACE%\AtriumPyTools\tools;%PYTHONPATH%"
set "PYTHONUTF8=1"

if exist "%TAROTDRAW_CMD%" (
    "%TAROTDRAW_CMD%" %*
    exit /b %ERRORLEVEL%
)

rem fall back to the module entry point when pip install was not run
"%VENV_PYTHON%" -X utf8 -m tarotdraw %*
exit /b %ERRORLEVEL%
