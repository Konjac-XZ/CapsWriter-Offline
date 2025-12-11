@echo off
REM Build script for CapsWriter-Offline GUI executables
REM This script builds the GUI executables using PyInstaller

echo ========================================
echo CapsWriter-Offline GUI Build Script
echo ========================================
echo.

REM Check if uv is available
where uv >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: uv is not installed or not in PATH
    echo Please install uv first: pip install uv
    pause
    exit /b 1
)

echo [1/4] Installing PyInstaller...
uv pip install pyinstaller
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install PyInstaller
    pause
    exit /b 1
)

echo.
echo [2/4] Building start_client_gui.exe...
uv run pyinstaller start_client_gui.spec --clean
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to build start_client_gui.exe
    pause
    exit /b 1
)

echo.
echo [3/4] Copying executable to repository root...
if exist "dist\start_client_gui.exe" (
    copy /Y "dist\start_client_gui.exe" "start_client_gui.exe"
    echo Successfully copied start_client_gui.exe to repository root
) else (
    echo ERROR: dist\start_client_gui.exe not found
    pause
    exit /b 1
)

echo.
echo [4/4] Build complete!
echo.
echo ========================================
echo Executable location: start_client_gui.exe
echo ========================================
echo.
echo You can now run start_client_gui.exe without opening a terminal window.
echo The executable is portable - you can copy the entire repository to another
echo computer and it will work as long as the .venv directory is present.
echo.
echo To rebuild, simply run this script again.
echo.
pause
