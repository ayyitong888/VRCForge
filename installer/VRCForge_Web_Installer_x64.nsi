!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef DOWNLOAD_URL
  !error "DOWNLOAD_URL is required"
!endif
!ifndef OUTFILE
  !define OUTFILE "VRCForge_Web_Installer_x64.exe"
!endif

Unicode true
!include LogicLib.nsh
Name "VRCForge ${VERSION} x64 Web Installer"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\VRCForge"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

Section "Install"
  SetRegView 64
  DetailPrint "Downloading VRCForge Windows x64 payload..."
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$$ProgressPreference = ''SilentlyContinue''; New-Item -ItemType Directory -Force -Path ''$TEMP\VRCForge'' | Out-Null; Invoke-WebRequest -Uri ''${DOWNLOAD_URL}'' -OutFile ''$TEMP\VRCForge\payload.zip''"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Failed to download VRCForge payload. Error code: $0"
    Abort
  ${EndIf}

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
SectionEnd

Section "Uninstall"
  SetRegView 64
  Delete "$DESKTOP\VRCForge.lnk"
  Delete "$SMPROGRAMS\VRCForge\VRCForge.lnk"
  Delete "$SMPROGRAMS\VRCForge\Uninstall VRCForge.lnk"
  RMDir "$SMPROGRAMS\VRCForge"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge"
  MessageBox MB_OK "VRCForge program files were removed. User data remains in $LOCALAPPDATA\VRCForge."
SectionEnd
