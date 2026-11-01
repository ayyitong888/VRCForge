!ifndef VRCFORGE_INSTALL_FINALIZATION_NSH
!define VRCFORGE_INSTALL_FINALIZATION_NSH

Var InstallErrorDetail
Var InstallBackupPath
Var InstallFailureStage

LangString InstallConfigFailedText ${LANG_SIMPCHINESE} "程序文件已就位，但安装配置未完成：$InstallFailureStage。$\r$\n快捷方式或安装登记可能只完成了一部分。请重新运行安装器完成配置。"
LangString InstallConfigFailedText ${LANG_TRADCHINESE} "程式檔案已就位，但安裝設定未完成：$InstallFailureStage。$\r$\n捷徑或安裝登錄可能只完成一部分。請重新執行安裝程式完成設定。"
LangString InstallConfigFailedText ${LANG_JAPANESE} "プログラムの配置後、設定を完了できませんでした: $InstallFailureStage。$\r$\nショートカットや登録が一部だけ更新された可能性があります。インストーラーを再実行してください。"
LangString InstallConfigFailedText ${LANG_ENGLISH} "Program files are in place, but setup could not finish: $InstallFailureStage.$\r$\nShortcuts or registration may be partially updated. Run setup again to finish configuration."
LangString PriorInstallKeptText ${LANG_SIMPCHINESE} "旧版程序文件保留在：$InstallBackupPath。请勿删除该备份。"
LangString PriorInstallKeptText ${LANG_TRADCHINESE} "舊版程式檔案保留在：$InstallBackupPath。請勿刪除此備份。"
LangString PriorInstallKeptText ${LANG_JAPANESE} "以前のプログラムファイルは $InstallBackupPath に残っています。このバックアップを削除しないでください。"
LangString PriorInstallKeptText ${LANG_ENGLISH} "Prior program files remain at: $InstallBackupPath. Do not delete this backup."
LangString InstallDataStep ${LANG_SIMPCHINESE} "个人数据目录"
LangString InstallDataStep ${LANG_TRADCHINESE} "個人資料目錄"
LangString InstallDataStep ${LANG_JAPANESE} "個人データの保存先"
LangString InstallDataStep ${LANG_ENGLISH} "personal data folders"
LangString InstallShortcutStep ${LANG_SIMPCHINESE} "快捷方式"
LangString InstallShortcutStep ${LANG_TRADCHINESE} "捷徑"
LangString InstallShortcutStep ${LANG_JAPANESE} "ショートカット"
LangString InstallShortcutStep ${LANG_ENGLISH} "shortcuts"
LangString InstallRegistrationStep ${LANG_SIMPCHINESE} "安装登记或文件关联"
LangString InstallRegistrationStep ${LANG_TRADCHINESE} "安裝登錄或檔案關聯"
LangString InstallRegistrationStep ${LANG_JAPANESE} "アプリまたはファイルの登録"
LangString InstallRegistrationStep ${LANG_ENGLISH} "application or file registration"
LangString InstallUninstallerStep ${LANG_SIMPCHINESE} "卸载程序"
LangString InstallUninstallerStep ${LANG_TRADCHINESE} "解除安裝程式"
LangString InstallUninstallerStep ${LANG_JAPANESE} "アンインストーラー"
LangString InstallUninstallerStep ${LANG_ENGLISH} "uninstaller"
LangString InstallCompletionStep ${LANG_SIMPCHINESE} "完成安装"
LangString InstallCompletionStep ${LANG_TRADCHINESE} "完成安裝"
LangString InstallCompletionStep ${LANG_JAPANESE} "インストールの完了処理"
LangString InstallCompletionStep ${LANG_ENGLISH} "installation completion"

Function ReadActivationBackupPath
  StrCpy $InstallBackupPath ""
  ClearErrors
  FileOpen $0 "$HelperPayloadPath\activation-backup.txt" r
  ${If} $0 != ""
    FileReadUTF16LE $0 $InstallBackupPath
    FileClose $0
  ${EndIf}
  ClearErrors
FunctionEnd

!macro VrcForgeRequireInstallStep Description
  ${If} ${Errors}
    StrCpy $InstallFailureStage "${Description}"
    Goto install_configuration_failed
  ${EndIf}
!macroend

!macro VrcForgeFinishInstallation
  ; The old program directory remains intact in its backup until every
  ; required setup operation has reported success. Do not claim that restoring
  ; files alone would undo partially written shell/registry configuration.
  Call ReadActivationBackupPath
  StrCpy $InstallErrorDetail ""
  StrCpy $R0 ""
  !ifndef VRCFORGE_SMOKE_BUILD
    ReadRegStr $R0 HKCU "Software\VRCForge" "DataDir"
  !endif
  ${If} $R0 != ""
    ${If} ${FileExists} "$R0\*.*"
      StrCpy $DataDir $R0
      Goto data_dir_resolved
    ${EndIf}
  ${EndIf}
  ${If} $DataDir == ""
    Call ComputeDefaultDataDir
  ${EndIf}
  ClearErrors
  CreateDirectory "$DataDir\config"
  CreateDirectory "$DataDir\logs"
  CreateDirectory "$DataDir\artifacts"
  CreateDirectory "$DataDir\backups"
  !insertmacro VrcForgeRequireInstallStep "$(InstallDataStep)"
data_dir_resolved:
  ClearErrors
  !ifndef VRCFORGE_SMOKE_BUILD
    WriteRegStr HKCU "Software\VRCForge" "DataDir" "$DataDir"
  !endif
  !insertmacro VrcForgeRequireInstallStep "$(InstallRegistrationStep)"

  ClearErrors
  CreateDirectory "$SMPROGRAMS\${START_MENU_GROUP}"
  SetOutPath "$INSTDIR"
  CreateShortCut "$DESKTOP\${DESKTOP_SHORTCUT}" "$INSTDIR\VRCForge.exe" "" "$INSTDIR\VRCForge.ico" 0
  CreateShortCut "$SMPROGRAMS\${START_MENU_GROUP}\VRCForge.lnk" "$INSTDIR\VRCForge.exe" "" "$INSTDIR\VRCForge.ico" 0
  CreateShortCut "$SMPROGRAMS\${START_MENU_GROUP}\$(UninstallShortcutName)" "$INSTDIR\Uninstall.exe"
  !insertmacro VrcForgeRequireInstallStep "$(InstallShortcutStep)"

  ClearErrors
  !ifndef VRCFORGE_SMOKE_BUILD
    WriteRegStr HKCU "Software\Classes\AppUserModelId\${APP_USER_MODEL_ID}" "DisplayName" "VRCForge"
    WriteRegStr HKCU "Software\Classes\AppUserModelId\${APP_USER_MODEL_ID}" "IconBackgroundColor" "0"
    WriteRegStr HKCU "Software\Classes\AppUserModelId\${APP_USER_MODEL_ID}" "IconUri" "$INSTDIR\VRCForge.png"
  !endif
  !insertmacro VrcForgeRequireInstallStep "$(InstallRegistrationStep)"

  ClearErrors
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  !insertmacro VrcForgeRequireInstallStep "$(InstallUninstallerStep)"
  !ifndef VRCFORGE_SMOKE_BUILD
    Call VrcForgeRegisterVskAssociation
  !endif
  !insertmacro VrcForgeRequireInstallStep "$(InstallRegistrationStep)"
  ClearErrors
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "DisplayName" "VRCForge"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "Publisher" "VRCForge"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\VRCForge.exe"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "NoRepair" 1
  !insertmacro VrcForgeRequireInstallStep "$(InstallRegistrationStep)"

  nsExec::ExecToStack '"$TrustedPowerShellPath" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "$HelperPayloadPath\VRCForge_WebPayload.ps1" -Action Complete -Version "${VERSION}" -ProgramFilesRoot "$PROGRAMFILES64" -DestinationRoot "$INSTDIR" -ExpectedInstallLeaf "${INSTALL_LEAF}" -StateTag "${STATE_TAG}"'
  Pop $0
  Pop $InstallErrorDetail
  DetailPrint "$InstallErrorDetail"
  ${If} $0 != 0
    StrCpy $InstallFailureStage "$(InstallCompletionStep)"
    Goto install_configuration_failed
  ${EndIf}
  Call CleanupProtectedHelper
  Goto install_configuration_done

install_configuration_failed:
  SetDetailsView show
  DetailPrint "$(InstallConfigFailedText)"
  ${If} $InstallBackupPath != ""
    DetailPrint "$(PriorInstallKeptText)"
    MessageBox MB_ICONSTOP "$(InstallConfigFailedText)$\r$\n$\r$\n$(PriorInstallKeptText)$\r$\n$InstallErrorDetail" /SD IDOK
  ${Else}
    MessageBox MB_ICONSTOP "$(InstallConfigFailedText)$\r$\n$InstallErrorDetail" /SD IDOK
  ${EndIf}
  Call CleanupProtectedHelper
  SetErrorLevel 2
  Abort
install_configuration_done:
!macroend

!endif
