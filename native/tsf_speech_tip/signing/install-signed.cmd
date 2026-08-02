@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
set "X64_SOURCE=%~1"
set "X86_SOURCE=%~2"

if not defined X64_SOURCE set "X64_SOURCE=%PROJECT_DIR%\build-x64\Release\CapsWriterSpeechTip.dll"
if not defined X86_SOURCE set "X86_SOURCE=%PROJECT_DIR%\build-x86\Release\CapsWriterSpeechTip.dll"

set "X64_INSTALL=%PROJECT_DIR%\installed\x64\CapsWriterSpeechTip.dll"
set "X86_INSTALL=%PROJECT_DIR%\installed\x86\CapsWriterSpeechTip.dll"
set "X64_STAGING=%PROJECT_DIR%\installed\x64\CapsWriterSpeechTip.staging.dll"
set "X86_STAGING=%PROJECT_DIR%\installed\x86\CapsWriterSpeechTip.staging.dll"

if not exist "%X64_SOURCE%" (
    echo x64 source DLL not found: %X64_SOURCE%
    exit /b 1
)
if not exist "%X86_SOURCE%" (
    echo x86 source DLL not found: %X86_SOURCE%
    exit /b 1
)

if not exist "%PROJECT_DIR%\installed\x64" mkdir "%PROJECT_DIR%\installed\x64"
if errorlevel 1 exit /b 1
if not exist "%PROJECT_DIR%\installed\x86" mkdir "%PROJECT_DIR%\installed\x86"
if errorlevel 1 exit /b 1

copy /y "%X64_SOURCE%" "%X64_STAGING%"
if errorlevel 1 exit /b 1
copy /y "%X86_SOURCE%" "%X86_STAGING%"
if errorlevel 1 exit /b 1

call "%SCRIPT_DIR%sign.cmd" "%X64_STAGING%" "%X86_STAGING%"
if errorlevel 1 exit /b 1

copy /y "%X64_STAGING%" "%X64_INSTALL%"
if errorlevel 1 goto in_use
copy /y "%X86_STAGING%" "%X86_INSTALL%"
if errorlevel 1 goto in_use

echo Registering the signed x64 TIP for the current user...
"%SystemRoot%\System32\regsvr32.exe" /s "%X64_INSTALL%"
if errorlevel 1 exit /b 1

echo Registering the signed x86 TIP for the current user...
"%SystemRoot%\SysWOW64\regsvr32.exe" /s "%X86_INSTALL%"
if errorlevel 1 exit /b 1

echo Signed TSF DLLs installed and registered successfully.
exit /b 0

:in_use
echo The installed DLL is in use. Close applications that loaded the TIP,
echo or reboot, then run this command again. The registration was not changed.
exit /b 1
