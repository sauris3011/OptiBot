@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   OptiBot - one-click startup
echo ============================================================
echo.

REM --- Sanity checks -----------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found on PATH. Install Python 3.11+ and re-run.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm was not found on PATH. Install Node.js and re-run.
  pause
  exit /b 1
)

REM --- Backend config ------------------------------------------------------
if not exist backend\.env (
  echo [!] backend\.env not found - creating it from backend\.env.example
  copy /y backend\.env.example backend\.env >nul
  echo     Edit backend\.env with your LITELLM_BASE_URL / LITELLM_API_KEY ^(or set them
  echo     later from the gear icon in the UI^), then re-run this script if needed.
  echo.
)

REM --- Backend dependencies --------------------------------------------------
echo === Checking backend Python dependencies ===
python -c "import fastapi, litellm, httpx" >nul 2>nul
if errorlevel 1 (
  echo Installing backend dependencies from backend\requirements.txt ...
  python -m pip install -r backend\requirements.txt
  if errorlevel 1 (
    echo [ERROR] Backend dependency install failed. See output above.
    pause
    exit /b 1
  )
) else (
  echo Backend dependencies already installed.
)
echo.

REM --- Frontend dependencies -------------------------------------------------
echo === Checking frontend dependencies ===
if not exist frontend\node_modules (
  echo Installing frontend dependencies ^(npm install^) ...
  pushd frontend
  call npm install
  if errorlevel 1 (
    echo [ERROR] Frontend dependency install failed. See output above.
    popd
    pause
    exit /b 1
  )
  popd
) else (
  echo Frontend dependencies already installed.
)
echo.

REM --- Launch both services in their own windows ----------------------------
echo === Starting backend  (http://127.0.0.1:8000) ===
start "OptiBot Backend"  cmd /k "cd /d "%~dp0backend" && python -m uvicorn app.main:app --reload --port 8000"

echo === Starting frontend (http://localhost:3000) ===
start "OptiBot Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo.
echo ============================================================
echo   OptiBot is starting up in two separate windows:
echo     Frontend : http://localhost:3000
echo     Backend  : http://127.0.0.1:8000/api/health
echo.
echo   Close a window to stop that service.
echo   Set the LiteLLM key/model from the gear icon once the UI loads.
echo ============================================================
echo.

timeout /t 6 /nobreak >nul
start "" http://localhost:3000

endlocal
