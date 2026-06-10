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
Name "VRCForge ${VERSION} x64"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\VRCForge"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

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
