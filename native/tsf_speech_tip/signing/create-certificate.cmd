@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "CERT_FILE=%SCRIPT_DIR%CapsWriterOfflineLocalCodeSigning.cer"
set "CERT_SUBJECT=CapsWriter Offline Local Code Signing"

certutil.exe -user -store My | findstr.exe /i /c:"%CERT_SUBJECT%" >nul
if not errorlevel 1 (
    echo Reusing the existing certificate in CurrentUser\My:
    certutil.exe -user -store My "%CERT_SUBJECT%"
    goto trust
)

echo Creating a 3072-bit, SHA-256, code-signing certificate...
certreq.exe -new -user "%SCRIPT_DIR%codesigning-certificate.inf" "%CERT_FILE%"
if errorlevel 1 exit /b 1
certutil.exe -user -store My | findstr.exe /i /c:"%CERT_SUBJECT%" >nul
if errorlevel 1 (
    echo Certificate creation did not install a private-key certificate in CurrentUser\My.
    exit /b 1
)

:trust
echo Exporting the current public certificate...
if exist "%CERT_FILE%" del /q "%CERT_FILE%"
certutil.exe -f -user -store My "%CERT_SUBJECT%" "%CERT_FILE%"
if not exist "%CERT_FILE%" (
    echo Could not export the public certificate automatically.
    echo Export it from certmgr.msc without the private key, then retry.
    exit /b 1
)
certutil.exe -dump "%CERT_FILE%" | findstr.exe /i /c:"%CERT_SUBJECT%" >nul
if errorlevel 1 exit /b 1

echo Installing the public certificate in LocalMachine\Root...
certutil.exe -f -addstore Root "%CERT_FILE%"
certutil.exe -store Root | findstr.exe /i /c:"%CERT_SUBJECT%" >nul
if errorlevel 1 (
    echo Trust installation failed. Run this script from an elevated terminal.
    exit /b 1
)

echo Installing the public certificate in LocalMachine\TrustedPublisher...
certutil.exe -f -addstore TrustedPublisher "%CERT_FILE%"
certutil.exe -store TrustedPublisher | findstr.exe /i /c:"%CERT_SUBJECT%" >nul
if errorlevel 1 (
    echo Trusted publisher installation failed.
    exit /b 1
)

echo Certificate creation and local-machine trust completed.
echo Keep the private key in CurrentUser\My. Do not commit a PFX backup.
exit /b 0
