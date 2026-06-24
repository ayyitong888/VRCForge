!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef DOWNLOAD_URL
  !error "DOWNLOAD_URL is required"
!endif
!ifndef PAYLOAD_SHA256
  !error "PAYLOAD_SHA256 is required"
!endif
!ifndef OUTFILE
  !define OUTFILE "VRCForge_Web_Installer_x64.exe"
!endif

Unicode true
!include LogicLib.nsh
!include "MUI2.nsh"

Name "VRCForge ${VERSION} x64 Web Installer"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\VRCForge"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
BrandingText "VRCForge ${VERSION}"

; ---------- Modern UI ----------
!define MUI_ICON "..\src-tauri\icons\icon.ico"
!define MUI_UNICON "..\src-tauri\icons\icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "$(WelcomeTitle)"
!define MUI_WELCOMEPAGE_TEXT "$(WelcomeText)"
!define MUI_FINISHPAGE_RUN "$INSTDIR\VRCForge.exe"
!define MUI_FINISHPAGE_RUN_TEXT "$(RunText)"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

LangString WelcomeTitle ${LANG_SIMPCHINESE} "欢迎安装 VRCForge ${VERSION}（在线安装）"
LangString WelcomeTitle ${LANG_ENGLISH} "Welcome to VRCForge ${VERSION} Web Setup"
LangString WelcomeText ${LANG_SIMPCHINESE} "VRCForge 是面向 VRChat 创作者的本地 AI 工作台。$\r$\n$\r$\n本安装器体积较小，将在安装过程中联网下载完整组件，请保持网络畅通。$\r$\n$\r$\n点击「下一步」继续。"
LangString WelcomeText ${LANG_ENGLISH} "VRCForge is a local AI workbench for VRChat creators.$\r$\n$\r$\nThis is a small web installer: it downloads the full payload during installation, so please stay online.$\r$\n$\r$\nClick Next to continue."
LangString RunText ${LANG_SIMPCHINESE} "安装完成后启动 VRCForge"
LangString RunText ${LANG_ENGLISH} "Launch VRCForge after install"

!macro StopVRCForgeProcesses
  nsExec::ExecToLog 'taskkill /F /IM VRCForge.exe /T'
  nsExec::ExecToLog 'taskkill /F /IM vrcforge_backend.exe /T'
  Sleep 800
!macroend

Section "Install"
  SetRegView 64
  DetailPrint "Downloading VRCForge Windows x64 payload..."
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$$ProgressPreference = ''SilentlyContinue''; New-Item -ItemType Directory -Force -Path ''$TEMP\VRCForge'' | Out-Null; Invoke-WebRequest -Uri ''${DOWNLOAD_URL}'' -OutFile ''$TEMP\VRCForge\payload.zip''"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Failed to download VRCForge payload. Error code: $0"
    Abort
  ${EndIf}

  DetailPrint "Verifying payload SHA256..."
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath ''$TEMP\VRCForge\payload.zip'').Hash.ToLowerInvariant(); if ($$actual -ne ''${PAYLOAD_SHA256}''.ToLowerInvariant()) { Write-Error \"Payload SHA256 mismatch. expected=${PAYLOAD_SHA256} actual=$$actual\"; exit 1 }"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Downloaded VRCForge payload failed SHA256 verification. Error code: $0"
    Abort
  ${EndIf}

  !insertmacro StopVRCForgeProcesses
  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\dashboard"
  RMDir /r "$INSTDIR\unity_plugin"
  RMDir /r "$INSTDIR\tools"
  RMDir /r "$INSTDIR\licenses"
  CreateDirectory "$INSTDIR"

  DetailPrint "Extracting payload..."
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath ''$TEMP\VRCForge\payload.zip'' -DestinationPath ''$INSTDIR'' -Force; Remove-Item -LiteralPath ''$INSTDIR\config'',''$INSTDIR\logs'',''$INSTDIR\artifacts'' -Recurse -Force -ErrorAction SilentlyContinue"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Failed to extract VRCForge payload. Error code: $0"
    Abort
  ${EndIf}

  CreateDirectory "$LOCALAPPDATA\VRCForge\agentic-app\config"
  CreateDirectory "$LOCALAPPDATA\VRCForge\agentic-app\logs"
  CreateDirectory "$LOCALAPPDATA\VRCForge\agentic-app\artifacts"
  CreateDirectory "$LOCALAPPDATA\VRCForge\agentic-app\backups"

  CreateDirectory "$SMPROGRAMS\VRCForge"
  CreateShortCut "$DESKTOP\VRCForge.lnk" "$INSTDIR\VRCForge.exe"
  CreateShortCut "$SMPROGRAMS\VRCForge\VRCForge.lnk" "$INSTDIR\VRCForge.exe"
  CreateShortCut "$SMPROGRAMS\VRCForge\Uninstall VRCForge.lnk" "$INSTDIR\Uninstall.exe"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge" "DisplayName" "VRCForge"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge" "Publisher" "VRCForge"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge" "DisplayIcon" "$INSTDIR\VRCForge.exe"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetRegView 64
  !insertmacro StopVRCForgeProcesses
  Delete "$DESKTOP\VRCForge.lnk"
  Delete "$SMPROGRAMS\VRCForge\VRCForge.lnk"
  Delete "$SMPROGRAMS\VRCForge\Uninstall VRCForge.lnk"
  RMDir "$SMPROGRAMS\VRCForge"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge"
  MessageBox MB_OK "VRCForge program files were removed. User data remains in $LOCALAPPDATA\VRCForge\agentic-app."
SectionEnd
