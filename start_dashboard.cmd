@echo off
setlocal
set "REPO_ROOT=%~dp0"
if exist "%REPO_ROOT%backend\vrcforge_backend.exe" (
  set "VRCFORGE_APP_DIR=%REPO_ROOT:~0,-1%"
  set "VRCFORGE_USER_DATA_DIR=%LOCALAPPDATA%\VRCForge"
  set "VRCFORGE_CONFIG_DIR=%LOCALAPPDATA%\VRCForge\config"
  set "VRCFORGE_LOG_DIR=%LOCALAPPDATA%\VRCForge\logs"
  set "VRCFORGE_ARTIFACTS_DIR=%LOCALAPPDATA%\VRCForge\artifacts"
  set "VRCFORGE_DASHBOARD_DIR=%REPO_ROOT%dashboard"
  set "VRCFORGE_SETTINGS_PATH=%LOCALAPPDATA%\VRCForge\config\settings.json"
  set "UV_PYTHON_INSTALL_DIR=%LOCALAPPDATA%\VRCForge\tools\python"
  set "UV_TOOL_DIR=%LOCALAPPDATA%\VRCForge\tools\uv-tools"
  set "UV_CACHE_DIR=%LOCALAPPDATA%\VRCForge\tools\uv-cache"
  set "PATH=%REPO_ROOT%tools\uv;%PATH%"
  if not exist "%VRCFORGE_CONFIG_DIR%" mkdir "%VRCFORGE_CONFIG_DIR%"
  if not exist "%VRCFORGE_LOG_DIR%" mkdir "%VRCFORGE_LOG_DIR%"
  if not exist "%VRCFORGE_ARTIFACTS_DIR%" mkdir "%VRCFORGE_ARTIFACTS_DIR%"
  echo Starting packaged VRCForge backend...
  "%REPO_ROOT%backend\vrcforge_backend.exe" --host 127.0.0.1 --port 8757 %*
  exit /b %ERRORLEVEL%
)
echo Packaged VRCForge backend was not found at "%REPO_ROOT%backend\vrcforge_backend.exe".
echo Rebuild or reinstall VRCForge, then try again.
exit /b 1
endlocal
