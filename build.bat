@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   GPUUnlockInspector - one-click build
echo ============================================================
echo.

rem ---------- 1) 找 Python ----------
set "PYEXE="
for %%P in (python.exe) do if exist "%%~$PATH:P" set "PYEXE=%%~$PATH:P"
if not defined PYEXE (
  for %%C in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
  ) do if exist %%C set "PYEXE=%%~C"
)
if not defined PYEXE (
  echo [ERR] python.exe not found. Install Python 3.10+ and retry.
  pause & exit /b 1
)
echo [1/4] Python: %PYEXE%
"%PYEXE%" -c "import tkinter" 2>nul
if errorlevel 1 (
  echo [ERR] this Python has no tkinter. Use the official python.org build.
  pause & exit /b 1
)

rem ---------- 2) 构建依赖 ----------
echo [2/4] Installing build deps into _build_deps ...
if not exist "_build_deps" (
  "%PYEXE%" -m pip install --disable-pip-version-check -q --target "_build_deps" pyinstaller pillow
  if errorlevel 1 (
    echo [ERR] pip install failed.
    pause & exit /b 1
  )
)
set "PYTHONPATH=%CD%\_build_deps"
"%PYEXE%" -c "import PyInstaller; print('   PyInstaller', PyInstaller.__version__)"

rem ---------- 3) 打包 ----------
echo [3/4] Building single-file exe ...
"%PYEXE%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build GPUUnlockInspector.spec
if errorlevel 1 (
  echo [ERR] PyInstaller failed.
  pause & exit /b 1
)

rem ---------- 4) 分发物 ----------
echo [4/4] Collecting payload ...
if not exist "dist\GPUUnlockInspector.exe" (
  echo [ERR] dist\GPUUnlockInspector.exe missing.
  pause & exit /b 1
)
copy /y "assets\gpu_db.json" "dist\gpu_db.json" >nul
copy /y "README.md" "dist\README.md" >nul 2>nul
if exist "tools\ssdiag-x86_64-windows.exe" (
  if not exist "dist\tools" mkdir "dist\tools"
  copy /y "tools\ssdiag-x86_64-windows.exe" "dist\tools\" >nul
)

echo.
echo ============================================================
echo   DONE  -^>  dist\GPUUnlockInspector.exe
echo ============================================================
dir /b "dist"
echo.
pause
endlocal
