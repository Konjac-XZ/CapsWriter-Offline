@echo off
setlocal EnableExtensions

set "PROJECT_DIR=%~dp0"
pushd "%PROJECT_DIR%\..\.."
if errorlevel 1 exit /b 1

uv run python "%PROJECT_DIR%manage_registration.py" install %*
set "RESULT=%errorlevel%"

popd
exit /b %RESULT%
