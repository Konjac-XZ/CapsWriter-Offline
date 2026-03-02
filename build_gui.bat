@echo off
setlocal EnableDelayedExpansion
REM Build script for native start_client_gui launcher
REM This script compiles start_client_gui_launcher.cpp with MSVC

echo ========================================
echo CapsWriter-Offline GUI Launcher Build Script
echo ========================================
echo.

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "TMP_EXE=start_client_gui.new.exe"
set "TMP_RES=start_client_gui_launcher.res"

where cl >nul 2>nul
if errorlevel 1 (
    if exist "%VSWHERE%" (
        for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do (
            set "VSINSTALL=%%I"
        )
    )

    if defined VSINSTALL (
        if exist "!VSINSTALL!\Common7\Tools\VsDevCmd.bat" (
            call "!VSINSTALL!\Common7\Tools\VsDevCmd.bat" -host_arch=x64 -arch=x64 >nul
        )
    )

    where cl >nul 2>nul
    if errorlevel 1 (
        echo ERROR: MSVC compiler not found in PATH.
        echo Please install Visual Studio C++ tools or run from a Developer Command Prompt.
        pause
        exit /b 1
    )
)

echo [1/2] Compiling start_client_gui_launcher.cpp...
if exist "%TMP_RES%" del /f /q "%TMP_RES%" >nul 2>nul
if exist "%TMP_EXE%" del /f /q "%TMP_EXE%" >nul 2>nul
rc /nologo /fo "%TMP_RES%" start_client_gui_launcher.rc
if errorlevel 1 (
    echo ERROR: Failed to compile launcher icon resource.
    pause
    exit /b 1
)

cl /nologo /O2 /EHsc /std:c++17 /DUNICODE /D_UNICODE /DNOMINMAX /DWIN32_LEAN_AND_MEAN /MT start_client_gui_launcher.cpp "%TMP_RES%" /link /SUBSYSTEM:WINDOWS /OUT:%TMP_EXE%
if errorlevel 1 (
    echo ERROR: Failed to compile start_client_gui.exe
    pause
    exit /b 1
)

move /y "%TMP_EXE%" "start_client_gui.exe" >nul
if errorlevel 1 (
    echo ERROR: Could not replace start_client_gui.exe.
    echo Close any running start_client_gui.exe process and run build_gui.bat again.
    pause
    exit /b 1
)

echo.
echo [2/2] Build complete!
echo.
echo ========================================
echo Executable location: start_client_gui.exe
echo ========================================
echo.
echo start_client_gui.exe will launch:
echo   uv run python start_client_gui.py
echo from this directory with no console window.
echo.
pause
