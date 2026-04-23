@echo off
setlocal EnableDelayedExpansion
title Build Tool

set APP_NAME=PostgreSQL_Bulk_Architect_Pro
set ENTRY=main.py

echo.
echo ============================================
echo  PostgreSQL Bulk Architect Pro - EXE Build
echo ============================================
echo.

if not exist %ENTRY% (
    echo [ERROR] main.py not found. Run from project root.
    pause
    exit /b 1
)

echo [1/5] Checking Python...
python --version
if errorlevel 1 ( echo [ERROR] Python not found & pause & exit /b 1 )

echo [2/5] Checking PyInstaller...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)
echo [OK] PyInstaller ready.

echo [3/5] Installing dependencies...
pip install psycopg2-binary python-dotenv requests

echo [4/5] Cleaning old build...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist %APP_NAME%.spec del /q %APP_NAME%.spec

echo [5/5] Building EXE...
echo.

python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name %APP_NAME% ^
    --add-data ".env;." ^
    --add-data "src;src" ^
    --hidden-import psycopg2 ^
    --hidden-import psycopg2.extras ^
    --hidden-import psycopg2._psycopg ^
    --hidden-import tkinter ^
    --hidden-import tkinter.ttk ^
    --hidden-import tkinter.messagebox ^
    --hidden-import dotenv ^
    --hidden-import requests ^
    --collect-submodules psycopg2 ^
    --exclude-module matplotlib ^
    --exclude-module numpy ^
    --exclude-module pandas ^
    --noconfirm ^
    --clean ^
    %ENTRY%

if errorlevel 1 (
    echo.
    echo [FAILED] Build failed. See errors above.
    pause
    exit /b 1
)

copy /y .env dist\.env >nul
echo [OK] .env copied to dist\

echo.
echo ============================================
echo  BUILD SUCCESS
echo  Output: dist\%APP_NAME%.exe
echo ============================================
echo.
echo NOTE: Edit dist\.env with your DB credentials before distribution.
echo.

choice /c YN /m "Open dist folder now?"
if errorlevel 1 if not errorlevel 2 explorer dist

echo.
pause
endlocal