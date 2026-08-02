@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
set "CERT_SUBJECT=CapsWriter Offline Local Code Signing"
set "SIGNTOOL="

for /f "delims=" %%I in ('where.exe signtool.exe 2^>nul') do if not defined SIGNTOOL set "SIGNTOOL=%%I"
if not defined SIGNTOOL (
    for /f "delims=" %%I in ('where.exe /r "%ProgramFiles(x86)%\Windows Kits\10\bin" signtool.exe 2^>nul ^| findstr.exe /i "\\x64\\signtool.exe"') do if not defined SIGNTOOL set "SIGNTOOL=%%I"
)
if not defined SIGNTOOL (
    echo SignTool was not found. Install a Windows 10 or 11 SDK.
    exit /b 1
)

certutil.exe -user -store My | findstr.exe /i /c:"%CERT_SUBJECT%" >nul
if errorlevel 1 (
    echo The signing certificate is missing from CurrentUser\My.
    echo Run "%SCRIPT_DIR%create-certificate.cmd" from an elevated terminal first.
    exit /b 1
)

if "%~1"=="" (
    call :sign_one "%PROJECT_DIR%\build-x64\Release\CapsWriterSpeechTip.dll"
    if errorlevel 1 exit /b 1
    call :sign_one "%PROJECT_DIR%\build-x86\Release\CapsWriterSpeechTip.dll"
    exit /b %errorlevel%
)

:sign_args
if "%~1"=="" exit /b 0
call :sign_one "%~1"
if errorlevel 1 exit /b 1
shift
goto sign_args

:sign_one
if not exist "%~1" (
    echo DLL not found: %~1
    exit /b 1
)
echo Signing %~1
"%SIGNTOOL%" sign /fd SHA256 /s My /n "%CERT_SUBJECT%" /d "CapsWriter Offline TSF Speech TIP" "%~1"
if errorlevel 1 exit /b 1
"%SIGNTOOL%" verify /pa /v "%~1"
exit /b %errorlevel%
