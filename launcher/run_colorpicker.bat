@echo off
setlocal

set "VENV_PYTHON=D:\Repositories\Manager\AtriumSteward\.venv\Scripts\python.exe"
set "COLORPICKER_CMD=D:\Repositories\Manager\AtriumSteward\.venv\Scripts\colorpicker.exe"
set "PYTHONPATH=D:\Repositories\Manager\AtriumPyTools\tools;%PYTHONPATH%"

if not exist "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment python not found: %VENV_PYTHON%
    exit /b 1
)

if exist "%COLORPICKER_CMD%" (
    if "%~1"=="" (
        "%COLORPICKER_CMD%" --x 100 --y 200
    ) else (
        "%COLORPICKER_CMD%" %*
    )
    exit /b %ERRORLEVEL%
)

if "%~1"=="" (
    "%VENV_PYTHON%" -m colorpicker.cli --x 100 --y 200
) else (
    "%VENV_PYTHON%" -m colorpicker.cli %*
)

exit /b %ERRORLEVEL%
