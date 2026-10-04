@echo off
rem ===========================================================================
rem  run_lexicon.bat - IELTS vocabulary toolchain (Windows one-click runner)
rem
rem  Usage:
rem    run_lexicon.bat import "<path to word list>"   archive source text
rem    run_lexicon.bat fetch-ecdict                   download ECDICT (63 MB, once)
rem    run_lexicon.bat fetch-tatoeba                  download Tatoeba sentences (24 MB, once)
rem    run_lexicon.bat build                          rebuild wordbook.db
rem    run_lexicon.bat enrich                         ECDICT definitions/phonetics/word forms
rem    run_lexicon.bat corpus                         examples + preposition collocations (~45s)
rem    run_lexicon.bat rebuild                        build + enrich + corpus
rem    run_lexicon.bat stats                          wordbook overview
rem    run_lexicon.bat lookup depend                  definition + enrichment + examples + collocations
rem    run_lexicon.bat merge                          merge wordbook into the koolearn master list
rem    run_lexicon.bat layers                         rebuild layered files 1-5
rem    run_lexicon.bat qa                             invariant checks
rem    run_lexicon.bat pools                          list pool sizes
rem    run_lexicon.bat drill                          start the A/S/D recognition drill (opens browser)
rem    run_lexicon.bat all                            everything above, rerunnable
rem
rem  Dependency: the workspace venv Manager\.venv only (no third-party packages).
rem  Data locations come from tools\lexicon\lexicon.config.json (see lexicon\paths.py).
rem  External sources are cached under tools\lexicon\.cache\ (gitignored).
rem  NOTE: this file is intentionally ASCII-only so cmd.exe parses it under any
rem        code page. Chinese text is printed by Python, not by this script.
rem ===========================================================================
setlocal enabledelayedexpansion

set "WORKSPACE=D:\Repositories\Manager"
set "TOOLDIR=%WORKSPACE%\AtriumPyTools\tools\lexicon"
set "VENV_PYTHON=%WORKSPACE%\.venv\Scripts\python.exe"
set "LEXICON_CMD=%WORKSPACE%\.venv\Scripts\lexicon.exe"

if not exist "%VENV_PYTHON%" (
    echo [ERROR] venv interpreter not found: %VENV_PYTHON%
    echo         the workspace venv should be %WORKSPACE%\.venv
    exit /b 1
)

if not exist "%TOOLDIR%\lexicon\cli.py" (
    echo [ERROR] tool sources not found: %TOOLDIR%\lexicon\cli.py
    exit /b 1
)

rem Let python import the lexicon package.
rem ORDER MATTERS: tools\lexicon must come BEFORE tools.
set "PYTHONPATH=%TOOLDIR%;%WORKSPACE%\AtriumPyTools\tools;%PYTHONPATH%"
set "PYTHONUTF8=1"

rem For "drill", open the browser a couple of seconds later (server needs to be up).
rem Uses ping (not timeout) for the delay: timeout needs an interactive console and
rem fails when stdin is redirected.
rem The port is read from --port so a custom port opens the right tab; --help does
rem not start a server, so it must not open a tab either.
if /I "%~1"=="drill" (
    set "DRILL_PORT=8765"
    set "PREV="
    for %%A in (%*) do (
        if /I "!PREV!"=="--port" set "DRILL_PORT=%%A"
        set "PREV=%%A"
    )
    set "ALLARGS=%*"
    set "SKIP_BROWSER="
    if not "!ALLARGS:--help=!"=="!ALLARGS!" set "SKIP_BROWSER=1"
    if not "!ALLARGS:-h=!"=="!ALLARGS!" set "SKIP_BROWSER=1"
    if not defined SKIP_BROWSER (
        start "" /min cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:!DRILL_PORT!/"
    )
)

if exist "%LEXICON_CMD%" (
    "%LEXICON_CMD%" %*
    exit /b %ERRORLEVEL%
)

rem fall back to the module entry point when pip install was not run
"%VENV_PYTHON%" -X utf8 -m lexicon %*
exit /b %ERRORLEVEL%
