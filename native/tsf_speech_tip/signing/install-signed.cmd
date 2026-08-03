@echo off
setlocal EnableExtensions

set "PROJECT_DIR=%~dp0.."
pushd "%PROJECT_DIR%\..\.."
if errorlevel 1 exit /b 1

echo Delegating to the versioned side-by-side registration workflow...
uv run python "%PROJECT_DIR%\manage_registration.py" install --skip-build %*
set "RESULT=%errorlevel%"

popd
exit /b %RESULT%
