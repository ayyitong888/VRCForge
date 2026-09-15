!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef DOWNLOAD_URL
  !error "DOWNLOAD_URL is required"
!endif
!ifndef PAYLOAD_SHA256
  !error "PAYLOAD_SHA256 is required"
!endif
!ifndef PAYLOAD_LENGTH
  !error "PAYLOAD_LENGTH is required"
!endif
!ifndef WEB_PAYLOAD_HELPER
  !error "WEB_PAYLOAD_HELPER is required"
!endif
!ifndef WEB_PAYLOAD_HELPER_SHA256
  !error "WEB_PAYLOAD_HELPER_SHA256 is required"
!endif

!define APP_USER_MODEL_ID "app.vrcforge.agentic"
!ifndef OUTFILE
  !define OUTFILE "VRCForge_Web_Installer_x64.exe"
!endif

!ifdef SMOKE_ID
  !error "SMOKE_ID cannot be supplied directly; use VRCFORGE_SMOKE_BUILD with the validated environment value."
!endif
!ifdef VRCFORGE_SMOKE_BUILD
  ; Keep the untrusted smoke token out of the compiler shell command. The
  ; validated environment value is expanded only after this command succeeds.
  !system '"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "ValidateNsisSmokeIdentity.ps1"' = 0
  !define SMOKE_ID "$%VRCFORGE_NSIS_SMOKE_ID%"
  !define INSTALL_LEAF "VRCForge-Smoke-${SMOKE_ID}"
  !define STATE_TAG "VRCForge-Smoke-${SMOKE_ID}"
  !define UNINSTALL_KEY "VRCForge-Smoke-${SMOKE_ID}"
  !define INSTALLER_LANGUAGE_KEY "Software\VRCForge\InstallerSmoke\${SMOKE_ID}"
  !define START_MENU_GROUP "VRCForge Smoke ${SMOKE_ID}"
  !define DESKTOP_SHORTCUT "VRCForge Smoke ${SMOKE_ID}.lnk"
  !define USER_DATA_RELATIVE "VRCForge\installer-smoke\${SMOKE_ID}"
!else
  !define INSTALL_LEAF "VRCForge"
  !define STATE_TAG "VRCForge"
  !define UNINSTALL_KEY "VRCForge"
  !define INSTALLER_LANGUAGE_KEY "Software\VRCForge"
  !define START_MENU_GROUP "VRCForge"
  !define DESKTOP_SHORTCUT "VRCForge.lnk"
  !define USER_DATA_RELATIVE "VRCForge\agentic-app"
!endif

Unicode true
!include LogicLib.nsh
!include nsDialogs.nsh
!include "MUI2.nsh"
Var ClearUserDataCheckbox
Var ClearUserData
Var PayloadStatePath
Var PayloadStageRoot
Var TrustedPowerShellPath
Var HelperStatePath
Var HelperPayloadPath
Var HelperSourcePath
Var UserDataRoot

Name "VRCForge ${VERSION} x64 Web Installer"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\${INSTALL_LEAF}"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
BrandingText "VRCForge ${VERSION}"

; ---------- Modern UI ----------
!define MUI_ICON "..\src-tauri\icons\icon.ico"
!define MUI_UNICON "..\src-tauri\icons\icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "$(WelcomeTitle)"
!define MUI_WELCOMEPAGE_TEXT "$(WelcomeText)"
; This installer is elevated. Do not launch the desktop app from the finish
; page because it would inherit administrator integrity and break normal
; desktop drag/drop and automation boundaries. The installed shortcuts launch
; VRCForge at the interactive user's normal integrity level.

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.UserDataOptionsPage un.UserDataOptionsLeave
!insertmacro MUI_UNPAGE_INSTFILES

; ---------- Languages ----------
; Persist the chosen installer language so the uninstaller reuses it
; (MUI_UNGETLANGUAGE reads this value instead of asking again).
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "${INSTALLER_LANGUAGE_KEY}"
!define MUI_LANGDLL_REGISTRY_VALUENAME "InstallerLanguage"
; Unicode installer: offer every bundled language regardless of system codepage.
!define MUI_LANGDLL_ALLLANGUAGES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "TradChinese"
!insertmacro MUI_LANGUAGE "Japanese"
!insertmacro MUI_LANGUAGE "English"

LangString WelcomeTitle ${LANG_SIMPCHINESE} "欢迎安装 VRCForge ${VERSION}（在线安装）"
LangString WelcomeTitle ${LANG_TRADCHINESE} "歡迎安裝 VRCForge ${VERSION}（線上安裝）"
LangString WelcomeTitle ${LANG_JAPANESE} "VRCForge ${VERSION} Web セットアップへようこそ"
LangString WelcomeTitle ${LANG_ENGLISH} "Welcome to VRCForge ${VERSION} Web Setup"
LangString WelcomeText ${LANG_SIMPCHINESE} "VRCForge 是面向 VRChat 创作者的本地 AI 工作台。$\r$\n$\r$\n本安装器将联网下载完整组件；替换程序文件前会正常退出已安装的 VRCForge，请保持网络畅通并先保存正在编辑的内容。$\r$\n$\r$\n点击「下一步」继续。"
LangString WelcomeText ${LANG_TRADCHINESE} "VRCForge 是為 VRChat 創作者打造的本機 AI 工作台。$\r$\n$\r$\n本安裝程式將連線下載完整元件；替換程式檔案前會正常結束已安裝的 VRCForge，請保持網路暢通並先儲存正在編輯的內容。$\r$\n$\r$\n點選「下一步」繼續。"
LangString WelcomeText ${LANG_JAPANESE} "VRCForge は VRChat クリエイター向けのローカル AI ワークベンチです。$\r$\n$\r$\n完全なコンポーネントをダウンロードし、プログラムファイルを置き換える前にインストール済みの VRCForge を正常終了します。接続を維持し、編集中の内容を先に保存してください。$\r$\n$\r$\n「次へ」をクリックして続行してください。"
LangString WelcomeText ${LANG_ENGLISH} "VRCForge is a local AI workbench for VRChat creators.$\r$\n$\r$\nSetup downloads the full payload and closes the installed VRCForge normally before replacing program files; stay online and save any work in progress first.$\r$\n$\r$\nClick Next to continue."
LangString UninstallShortcutName ${LANG_SIMPCHINESE} "卸载 VRCForge.lnk"
LangString UninstallShortcutName ${LANG_TRADCHINESE} "解除安裝 VRCForge.lnk"
LangString UninstallShortcutName ${LANG_JAPANESE} "VRCForge をアンインストール.lnk"
LangString UninstallShortcutName ${LANG_ENGLISH} "Uninstall VRCForge.lnk"
LangString ClearUserDataTitle ${LANG_SIMPCHINESE} "卸载数据"
LangString ClearUserDataTitle ${LANG_TRADCHINESE} "解除安裝資料"
LangString ClearUserDataTitle ${LANG_JAPANESE} "アンインストール データ"
LangString ClearUserDataTitle ${LANG_ENGLISH} "Uninstall Data"
LangString ClearUserDataText ${LANG_SIMPCHINESE} "默认仅卸载程序文件，并保留设置、对话、检查点和项目历史。"
LangString ClearUserDataText ${LANG_TRADCHINESE} "預設僅解除安裝程式檔案，並保留設定、對話、檢查點與專案歷史。"
LangString ClearUserDataText ${LANG_JAPANESE} "既定ではプログラムファイルのみを削除し、設定・チャット・チェックポイント・プロジェクト履歴は保持します。"
LangString ClearUserDataText ${LANG_ENGLISH} "By default, setup removes only program files and keeps settings, chats, checkpoints, and project history."
LangString ClearUserDataCheckboxText ${LANG_SIMPCHINESE} "清除用户数据和历史对话"
LangString ClearUserDataCheckboxText ${LANG_TRADCHINESE} "清除使用者資料與歷史對話"
LangString ClearUserDataCheckboxText ${LANG_JAPANESE} "ユーザーデータとチャット履歴を削除する"
LangString ClearUserDataCheckboxText ${LANG_ENGLISH} "Clear user data and chat history"
LangString UninstallKeptUserData ${LANG_SIMPCHINESE} "VRCForge 程序文件已移除。用户数据仍保留在 $LOCALAPPDATA\${USER_DATA_RELATIVE}。"
LangString UninstallKeptUserData ${LANG_TRADCHINESE} "VRCForge 程式檔案已移除。使用者資料仍保留在 $LOCALAPPDATA\${USER_DATA_RELATIVE}。"
LangString UninstallKeptUserData ${LANG_JAPANESE} "VRCForge のプログラムファイルを削除しました。ユーザーデータは $LOCALAPPDATA\${USER_DATA_RELATIVE} に保持されています。"
LangString UninstallKeptUserData ${LANG_ENGLISH} "VRCForge program files were removed. User data remains in $LOCALAPPDATA\${USER_DATA_RELATIVE}."
LangString UninstallClearedUserData ${LANG_SIMPCHINESE} "VRCForge 程序文件、用户数据和已知项目中的历史对话已移除。"
LangString UninstallClearedUserData ${LANG_TRADCHINESE} "VRCForge 程式檔案、使用者資料以及已知專案中的歷史對話已移除。"
LangString UninstallClearedUserData ${LANG_JAPANESE} "VRCForge のプログラムファイル、ユーザーデータ、既知プロジェクトのチャット履歴を削除しました。"
LangString UninstallClearedUserData ${LANG_ENGLISH} "VRCForge program files, user data, and known project chat history were removed."
LangString ClearingUserDataText ${LANG_SIMPCHINESE} "正在清除 VRCForge 用户数据和已知项目的历史对话..."
LangString ClearingUserDataText ${LANG_TRADCHINESE} "正在清除 VRCForge 使用者資料與已知專案的歷史對話..."
LangString ClearingUserDataText ${LANG_JAPANESE} "VRCForge のユーザーデータと既知プロジェクトのチャット履歴を削除しています..."
LangString ClearingUserDataText ${LANG_ENGLISH} "Clearing VRCForge user data and known project chat history..."
LangString CloseRunningAppText ${LANG_SIMPCHINESE} "VRCForge 仍在运行，或程序文件正在被占用。请保存工作并完全退出 VRCForge（包括系统托盘），然后点击「重试」继续安装。点击「取消」停止安装。"
LangString CloseRunningAppText ${LANG_TRADCHINESE} "VRCForge 仍在執行，或程式檔案正被佔用。請儲存工作並完全結束 VRCForge（包括系統匣），然後按「重試」繼續安裝。按「取消」停止安裝。"
LangString CloseRunningAppText ${LANG_JAPANESE} "VRCForge が実行中、またはプログラムファイルが使用中です。作業を保存して VRCForge を完全に終了し（タスクトレイを含む）、「再試行」でインストールを続けてください。「キャンセル」で中止します。"
LangString CloseRunningAppText ${LANG_ENGLISH} "VRCForge is still running, or its program files are in use. Save your work and fully exit VRCForge (including the system tray), then click Retry to continue this installation. Click Cancel to stop."
LangString ClosingRunningAppText ${LANG_SIMPCHINESE} "正在正常退出已安装的 VRCForge，以便安全更新..."
LangString ClosingRunningAppText ${LANG_TRADCHINESE} "正在正常結束已安裝的 VRCForge，以便安全更新..."
LangString ClosingRunningAppText ${LANG_JAPANESE} "安全に更新するため、インストール済みの VRCForge を正常終了しています..."
LangString ClosingRunningAppText ${LANG_ENGLISH} "Closing the installed VRCForge normally for a safe update..."
LangString ActivationFailedText ${LANG_SIMPCHINESE} "无法正常退出正在运行的 VRCForge，或无法安全激活新版本。原安装保持不变；请手动退出 VRCForge 后重试。"
LangString ActivationFailedText ${LANG_TRADCHINESE} "無法正常結束正在執行的 VRCForge，或無法安全啟用新版本。原安裝保持不變；請手動結束 VRCForge 後重試。"
LangString ActivationFailedText ${LANG_JAPANESE} "実行中の VRCForge を正常終了できないか、新しいバージョンを安全に有効化できませんでした。以前のインストールは保持されています。VRCForge を手動で終了して再試行してください。"
LangString ActivationFailedText ${LANG_ENGLISH} "The running VRCForge could not close normally, or the new version could not be activated safely. The prior installation was preserved; close VRCForge manually and retry."
LangString DownloadingText ${LANG_SIMPCHINESE} "正在下载 VRCForge Windows x64 组件..."
LangString DownloadingText ${LANG_TRADCHINESE} "正在下載 VRCForge Windows x64 元件..."
LangString DownloadingText ${LANG_JAPANESE} "VRCForge Windows x64 ペイロードをダウンロードしています..."
LangString DownloadingText ${LANG_ENGLISH} "Downloading VRCForge Windows x64 payload..."
LangString VerifyingText ${LANG_SIMPCHINESE} "正在校验组件 SHA256..."
LangString VerifyingText ${LANG_TRADCHINESE} "正在驗證元件 SHA256..."
LangString VerifyingText ${LANG_JAPANESE} "ペイロードの SHA256 を検証しています..."
LangString VerifyingText ${LANG_ENGLISH} "Verifying payload SHA256..."
LangString ExtractingText ${LANG_SIMPCHINESE} "正在解压组件..."
LangString ExtractingText ${LANG_TRADCHINESE} "正在解壓縮元件..."
LangString ExtractingText ${LANG_JAPANESE} "ペイロードを展開しています..."
LangString ExtractingText ${LANG_ENGLISH} "Extracting payload..."
LangString DownloadFailedText ${LANG_SIMPCHINESE} "下载 VRCForge 组件失败。错误码：$0"
LangString DownloadFailedText ${LANG_TRADCHINESE} "下載 VRCForge 元件失敗。錯誤碼：$0"
LangString DownloadFailedText ${LANG_JAPANESE} "VRCForge ペイロードのダウンロードに失敗しました。エラーコード: $0"
LangString DownloadFailedText ${LANG_ENGLISH} "Failed to download VRCForge payload. Error code: $0"
LangString HashMismatchText ${LANG_SIMPCHINESE} "下载的 VRCForge 组件未通过 SHA256 校验。错误码：$0"
LangString HashMismatchText ${LANG_TRADCHINESE} "下載的 VRCForge 元件未通過 SHA256 驗證。錯誤碼：$0"
LangString HashMismatchText ${LANG_JAPANESE} "ダウンロードした VRCForge ペイロードが SHA256 検証に失敗しました。エラーコード: $0"
LangString HashMismatchText ${LANG_ENGLISH} "Downloaded VRCForge payload failed SHA256 verification. Error code: $0"
LangString ExtractFailedText ${LANG_SIMPCHINESE} "解压 VRCForge 组件失败。错误码：$0"
LangString ExtractFailedText ${LANG_TRADCHINESE} "解壓縮 VRCForge 元件失敗。錯誤碼：$0"
LangString ExtractFailedText ${LANG_JAPANESE} "VRCForge ペイロードの展開に失敗しました。エラーコード: $0"
LangString ExtractFailedText ${LANG_ENGLISH} "Failed to extract VRCForge payload. Error code: $0"

Function .onInit
  ; Set only this installer's child-process environment; the change ends with this process.
  System::Call 'kernel32::SetEnvironmentVariable(t "PSModulePath", t "$WINDIR\System32\WindowsPowerShell\v1.0\Modules") i .r0'
  StrCmp $0 0 0 +2
    Abort
  ; Language dialog: preselects the OS UI language (or the previously
  ; persisted choice) and stores the result under HKCU\Software\VRCForge.
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Function un.onInit
  ; Set only this uninstaller's child-process environment; the change ends with this process.
  System::Call 'kernel32::SetEnvironmentVariable(t "PSModulePath", t "$WINDIR\System32\WindowsPowerShell\v1.0\Modules") i .r0'
  StrCmp $0 0 0 +2
    Abort
  ; Reuse the language chosen at install time instead of asking again.
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

!macro DefineProtectedHelperFunctions Prefix
Function ${Prefix}ValidateScopedInstallDir
  StrCpy $0 0
  StrCmp "$INSTDIR" "$PROGRAMFILES64\${INSTALL_LEAF}" 0 +2
    Return
  StrCpy $0 1
FunctionEnd
Function ${Prefix}CleanupProtectedHelper
  ${If} $HelperPayloadPath != ""
    System::Call 'kernel32::GetFileAttributes(t "$HelperPayloadPath") i .r9'
    IntOp $9 $9 & 0x400
    ${If} $9 == 0
      RMDir /r "$HelperPayloadPath"
    ${EndIf}
  ${EndIf}
  StrCpy $HelperPayloadPath ""
FunctionEnd
Function ${Prefix}PrepareProtectedHelper
  System::Call 'kernel32::GetTempFileName(t "$PROGRAMFILES64", t "vfg", i 0, t .r8) i .r9'
  ${If} $9 == 0
    Abort
  ${EndIf}
  Delete "$8"
  CreateDirectory "$8"
  System::Call 'kernel32::GetFileAttributes(t "$8") i .r9'
  IntOp $7 $9 & 0x10
  IntOp $9 $9 & 0x400
  ${If} $7 == 0
    Abort
  ${EndIf}
  ${If} $9 != 0
    Abort
  ${EndIf}
  nsExec::ExecToLog '"$SYSDIR\icacls.exe" "$8" /setowner "*S-1-5-32-544" /Q'
  Pop $9
  ${If} $9 != 0
    Abort
  ${EndIf}
  nsExec::ExecToLog '"$SYSDIR\icacls.exe" "$8" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /Q'
  Pop $9
  ${If} $9 != 0
    Abort
  ${EndIf}
  CopyFiles /SILENT "$HelperStatePath" "$8\VRCForge_WebPayload.ps1"
  StrCpy $HelperPayloadPath "$8"
  nsExec::ExecToLog '"$TrustedPowerShellPath" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$$a=(Get-FileHash -Algorithm SHA256 -LiteralPath $\'$HelperPayloadPath\VRCForge_WebPayload.ps1$\').Hash;if($$a -ieq $\'${WEB_PAYLOAD_HELPER_SHA256}$\'){exit 0};exit 1"'
  Pop $0
  ${If} $0 != 0
    Call ${Prefix}CleanupProtectedHelper
  ${EndIf}
FunctionEnd
Function ${Prefix}ValidateInstallBoundary
  StrCpy $HelperStatePath "$HelperSourcePath"
  Call ${Prefix}PrepareProtectedHelper
  ${If} $0 == 0
    nsExec::ExecToLog '"$TrustedPowerShellPath" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "$HelperPayloadPath\VRCForge_WebPayload.ps1" -Action ValidateDestination -Version "${VERSION}" -ProgramFilesRoot "$PROGRAMFILES64" -DestinationRoot "$INSTDIR" -ExpectedInstallLeaf "${INSTALL_LEAF}" -StateTag "${STATE_TAG}"'
    Pop $0
    Push $0
    Call ${Prefix}CleanupProtectedHelper
    Pop $0
  ${EndIf}
FunctionEnd
!macroend
!insertmacro DefineProtectedHelperFunctions ""
!insertmacro DefineProtectedHelperFunctions "un."

Function un.UserDataOptionsPage
  IfSilent 0 +2
    Abort
  !insertmacro MUI_HEADER_TEXT "$(ClearUserDataTitle)" "$(ClearUserDataText)"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 32u "$(ClearUserDataText)"
  Pop $1
  ${NSD_CreateCheckbox} 0 44u 100% 12u "$(ClearUserDataCheckboxText)"
  Pop $ClearUserDataCheckbox
  ${NSD_SetState} $ClearUserDataCheckbox ${BST_UNCHECKED}
  nsDialogs::Show
FunctionEnd

Function un.UserDataOptionsLeave
  ${NSD_GetState} $ClearUserDataCheckbox $ClearUserData
FunctionEnd

Function un.ClearUserDataIfRequested
  ${If} $ClearUserData == ${BST_CHECKED}
    DetailPrint "$(ClearingUserDataText)"
    ${If} ${FileExists} "$INSTDIR\backend\vrcforge_backend.exe"
      nsExec::ExecToLog '"$INSTDIR\backend\vrcforge_backend.exe" --cleanup-user-data --cleanup-user-data-root "$UserDataRoot"'
      Pop $0
      ${If} $0 != 0
        RMDir /r "$UserDataRoot"
      ${EndIf}
    ${Else}
      RMDir /r "$UserDataRoot"
    ${EndIf}
  ${EndIf}
FunctionEnd

Section "Install"
  SetRegView 64
  ; A silent /D override is accepted only after the protected helper restricts
  ; it to the VRCForge production or isolated smoke leaf under Program Files.
  InitPluginsDir
  Call ValidateScopedInstallDir
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "The requested install directory does not match this installer identity." /SD IDOK
    Abort
  ${EndIf}
  SetOutPath "$PLUGINSDIR"
  File /oname=VRCForge_WebPayload.ps1 "${WEB_PAYLOAD_HELPER}"
  StrCpy $TrustedPowerShellPath "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  ${IfNot} ${FileExists} "$TrustedPowerShellPath"
    StrCpy $TrustedPowerShellPath "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"
  ${EndIf}
  StrCpy $HelperSourcePath "$PLUGINSDIR\VRCForge_WebPayload.ps1"
  Call ValidateInstallBoundary
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "The protected Program Files installation boundary could not be verified." /SD IDOK
    Abort
  ${EndIf}
  ; Retry stays in this installer. No payload work occurs while the app is busy.
  install_retry:
  StrCpy $HelperSourcePath "$PLUGINSDIR\VRCForge_WebPayload.ps1"
  StrCpy $HelperStatePath "$HelperSourcePath"
  Call PrepareProtectedHelper
  ${If} $0 == 0
    nsExec::ExecToLog '"$TrustedPowerShellPath" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "$HelperPayloadPath\VRCForge_WebPayload.ps1" -Action CheckNotRunning -Version "${VERSION}" -ProgramFilesRoot "$PROGRAMFILES64" -DestinationRoot "$INSTDIR" -ExpectedInstallLeaf "${INSTALL_LEAF}" -StateTag "${STATE_TAG}"'
    Pop $0
    Push $0
    Call CleanupProtectedHelper
    Pop $0
  ${EndIf}
  ${If} $0 != 0
    MessageBox MB_ICONEXCLAMATION|MB_RETRYCANCEL "$(CloseRunningAppText)" /SD IDCANCEL IDRETRY install_retry
    SetErrorLevel 2
    Abort
  ${EndIf}
  StrCpy $PayloadStatePath "$PLUGINSDIR\payload-stage.txt"
  DetailPrint "$(DownloadingText)"
  StrCpy $HelperSourcePath "$PLUGINSDIR\VRCForge_WebPayload.ps1"
  StrCpy $HelperStatePath "$HelperSourcePath"
  Call PrepareProtectedHelper
  ${If} $0 == 0
    nsExec::ExecToLog '"$TrustedPowerShellPath" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "$HelperPayloadPath\VRCForge_WebPayload.ps1" -Action Prepare -Version "${VERSION}" -ProgramFilesRoot "$PROGRAMFILES64" -PayloadUrl "${DOWNLOAD_URL}" -ExpectedSha256 "${PAYLOAD_SHA256}" -ExpectedLength "${PAYLOAD_LENGTH}" -StatePath "$PayloadStatePath" -ExpectedInstallLeaf "${INSTALL_LEAF}" -StateTag "${STATE_TAG}"'
    Pop $0
    Push $0
    Call CleanupProtectedHelper
    Pop $0
  ${EndIf}
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "$(DownloadFailedText)" /SD IDOK
    Abort
  ${EndIf}
  FileOpen $0 "$PayloadStatePath" r
  ${If} $0 == ""
    MessageBox MB_ICONSTOP "$(DownloadFailedText)" /SD IDOK
    Abort
  ${EndIf}
  FileRead $0 $PayloadStageRoot
  FileClose $0
  ; The opaque descriptor is consumed. Prepare requires a fresh state file on
  ; retry; Extract owns and cleans up the separate verified download directory.
  Delete "$PayloadStatePath"
  ${If} $PayloadStageRoot == ""
    MessageBox MB_ICONSTOP "$(HashMismatchText)" /SD IDOK
    Abort
  ${EndIf}

  DetailPrint "$(VerifyingText)"
  DetailPrint "$(ExtractingText)"
  StrCpy $HelperSourcePath "$PLUGINSDIR\VRCForge_WebPayload.ps1"
  StrCpy $HelperStatePath "$HelperSourcePath"
  DetailPrint "$(ClosingRunningAppText)"
  Call PrepareProtectedHelper
  ${If} $0 == 0
    nsExec::ExecToLog '"$TrustedPowerShellPath" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "$HelperPayloadPath\VRCForge_WebPayload.ps1" -Action Extract -Version "${VERSION}" -ProgramFilesRoot "$PROGRAMFILES64" -PayloadUrl "${DOWNLOAD_URL}" -ExpectedSha256 "${PAYLOAD_SHA256}" -ExpectedLength "${PAYLOAD_LENGTH}" -StageRoot "$PayloadStageRoot" -DestinationRoot "$INSTDIR" -ExpectedInstallLeaf "${INSTALL_LEAF}" -StateTag "${STATE_TAG}"'
    Pop $0
    Push $0
    Call CleanupProtectedHelper
    Pop $0
  ${EndIf}
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_RETRYCANCEL "$(ActivationFailedText)" /SD IDCANCEL IDRETRY install_retry
    SetErrorLevel 2
    Abort
  ${EndIf}
  StrCpy $UserDataRoot "$LOCALAPPDATA\${USER_DATA_RELATIVE}"
  CreateDirectory "$UserDataRoot\config"
  CreateDirectory "$UserDataRoot\logs"
  CreateDirectory "$UserDataRoot\artifacts"
  CreateDirectory "$UserDataRoot\backups"

  CreateDirectory "$SMPROGRAMS\${START_MENU_GROUP}"
  SetOutPath "$INSTDIR"
  CreateShortCut "$DESKTOP\${DESKTOP_SHORTCUT}" "$INSTDIR\VRCForge.exe" "" "$INSTDIR\VRCForge.ico" 0
  CreateShortCut "$SMPROGRAMS\${START_MENU_GROUP}\VRCForge.lnk" "$INSTDIR\VRCForge.exe" "" "$INSTDIR\VRCForge.ico" 0
  CreateShortCut "$SMPROGRAMS\${START_MENU_GROUP}\$(UninstallShortcutName)" "$INSTDIR\Uninstall.exe"

  !ifndef VRCFORGE_SMOKE_BUILD
    WriteRegStr HKCU "Software\Classes\AppUserModelId\${APP_USER_MODEL_ID}" "DisplayName" "VRCForge"
    WriteRegStr HKCU "Software\Classes\AppUserModelId\${APP_USER_MODEL_ID}" "IconBackgroundColor" "0"
    WriteRegStr HKCU "Software\Classes\AppUserModelId\${APP_USER_MODEL_ID}" "IconUri" "$INSTDIR\VRCForge.png"
  !endif

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "DisplayName" "VRCForge"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "Publisher" "VRCForge"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\VRCForge.exe"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetRegView 64
  Call un.ValidateScopedInstallDir
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "The uninstall directory does not match this installer identity." /SD IDOK
    Abort
  ${EndIf}
  StrCpy $UserDataRoot "$LOCALAPPDATA\${USER_DATA_RELATIVE}"
  StrCpy $TrustedPowerShellPath "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  ${IfNot} ${FileExists} "$TrustedPowerShellPath"
    StrCpy $TrustedPowerShellPath "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"
  ${EndIf}
  StrCpy $HelperSourcePath "$INSTDIR\installer\VRCForge_WebPayload.ps1"
  Call un.ValidateInstallBoundary
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "The protected Program Files uninstall boundary could not be verified." /SD IDOK
    Abort
  ${EndIf}
  Delete "$DESKTOP\${DESKTOP_SHORTCUT}"
  Delete "$SMPROGRAMS\${START_MENU_GROUP}\VRCForge.lnk"
  Delete "$SMPROGRAMS\${START_MENU_GROUP}\Uninstall VRCForge.lnk"
  Delete "$SMPROGRAMS\${START_MENU_GROUP}\卸载 VRCForge.lnk"
  Delete "$SMPROGRAMS\${START_MENU_GROUP}\解除安裝 VRCForge.lnk"
  Delete "$SMPROGRAMS\${START_MENU_GROUP}\VRCForge をアンインストール.lnk"
  RMDir "$SMPROGRAMS\${START_MENU_GROUP}"
  Call un.ClearUserDataIfRequested
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_KEY}"
  !ifndef VRCFORGE_SMOKE_BUILD
    DeleteRegKey HKCU "Software\Classes\AppUserModelId\${APP_USER_MODEL_ID}"
  !endif
  ; Remove the persisted installer language last; it was already read in un.onInit.
  DeleteRegValue HKCU "${INSTALLER_LANGUAGE_KEY}" "InstallerLanguage"
  DeleteRegKey /ifempty HKCU "${INSTALLER_LANGUAGE_KEY}"
  ${If} $ClearUserData == ${BST_CHECKED}
    MessageBox MB_OK "$(UninstallClearedUserData)" /SD IDOK
  ${Else}
    MessageBox MB_OK "$(UninstallKeptUserData)" /SD IDOK
  ${EndIf}
SectionEnd
