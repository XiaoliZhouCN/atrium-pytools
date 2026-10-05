@echo off
rem Convenience entry inside the tool folder; delegates to the repo-level launcher.
rem Repo-level script: AtriumPyTools\launcher\run_lexicon.bat
call "%~dp0..\..\launcher\run_lexicon.bat" %*
exit /b %ERRORLEVEL%
