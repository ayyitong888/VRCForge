!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef PAYLOAD_DIR
  !error "PAYLOAD_DIR is required"
!endif
!ifndef OUTFILE
  !define OUTFILE "VRCForge_Offline_Installer_x64.exe"
!endif

Unicode true
!include "MUI2.nsh"

Name "VRCForge ${VERSION} x64"
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

LangString WelcomeTitle ${LANG_SIMPCHINESE} "欢迎安装 VRCForge ${VERSION}"
LangString WelcomeTitle ${LANG_ENGLISH} "Welcome to VRCForge ${VERSION} Setup"
LangString WelcomeText ${LANG_SIMPCHINESE} "VRCForge 是面向 VRChat 创作者的本地 AI 工作台。$\r$\n$\r$\n安装向导将引导你完成安装。安装前建议关闭正在运行的 VRCForge。$\r$\n$\r$\n点击「下一步」继续。"
LangString WelcomeText ${LANG_ENGLISH} "VRCForge is a local AI workbench for VRChat creators.$\r$\n$\r$\nThis wizard will guide you through the installation. Please close any running VRCForge instance first.$\r$\n$\r$\nClick Next to continue."
LangString RunText ${LANG_SIMPCHINESE} "安装完成后启动 VRCForge"
LangString RunText ${LANG_ENGLISH} "Launch VRCForge after install"

!macro StopVRCForgeProcesses
  nsExec::ExecToLog 'taskkill /F /IM VRCForge.exe /T'
  nsExec::ExecToLog 'taskkill /F /IM vrcforge_backend.exe /T'
  Sleep 800
!macroend

Section "Install"
  SetRegView 64
  ; Repair-friendly: stop the running tray app and backend before overwriting payload.
  !insertmacro StopVRCForgeProcesses
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\dashboard"
  RMDir /r "$INSTDIR\unity_plugin"
  RMDir /r "$INSTDIR\tools"
  RMDir /r "$INSTDIR\licenses"
  File /r /x "config" /x "logs" /x "artifacts" "${PAYLOAD_DIR}\*"

  CreateDirectory "$LOCALAPPDATA\VRCForge\config"
  CreateDirectory "$LOCALAPPDATA\VRCForge\logs"
  CreateDirectory "$LOCALAPPDATA\VRCForge\artifacts"
  CreateDirectory "$LOCALAPPDATA\VRCForge\backups"

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
  ; Repair = re-run this offline installer; it stops processes, clears stale payload dirs, and recopies.
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
  MessageBox MB_OK "VRCForge program files were removed. User data remains in $LOCALAPPDATA\VRCForge."
SectionEnd
