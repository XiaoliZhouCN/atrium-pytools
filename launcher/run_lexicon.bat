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
rem    run_lexicon.bat export-json                    re-export ielts_layered.json (layers does it too)
rem    run_lexicon.bat qa                             invariant checks
rem    run_lexicon.bat pools                          list pool sizes
rem    run_lexicon.bat drill                          start the A/S/D recognition drill (opens browser)
rem    run_lexicon.bat all                            everything above, rerunnable
rem
rem  Exit codes: 0 ok / 1 launcher problem / 2 usage / 3 data problem / 4 qa failed.
rem
rem  Dependency: the workspace venv Manager\.venv only (no third-party packages).
rem  Data locations come from tools\lexicon\lexicon.config.json (see lexicon\paths.py).
rem  External sources are cached under tools\lexicon\.cache\ (gitignored).
rem
rem  DO NOT add "chcp" here. Changing the code page while cmd.exe is reading this
rem  file makes it lose track of its position in the file and it starts executing
rem  the remaining lines with their first characters chopped off ("set" -> "TS",
rem  "enrich" -> "ich"). Console encoding is handled inside Python instead, see
rem  lexicon/cli.py::_ensure_utf8_stdout.
rem
rem  This file must keep CRLF line endings; cmd.exe mis-parses LF-only batch files.
rem  It is intentionally ASCII-only so cmd.exe parses it under any code page.
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

rem NOTE: %ERRORLEVEL% would be expanded when this block is parsed, i.e. before
rem the command runs, so the real exit code must come from !ERRORLEVEL!.
set "RC=0"
if exist "%LEXICON_CMD%" (
    "%LEXICON_CMD%" %*
    set "RC=!ERRORLEVEL!"
) else (
    rem fall back to the module entry point when pip install was not run
    "%VENV_PYTHON%" -m lexicon %*
    set "RC=!ERRORLEVEL!"
)

exit /b !RC!
