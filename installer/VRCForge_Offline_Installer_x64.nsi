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
!include LogicLib.nsh
!include nsDialogs.nsh
!include "MUI2.nsh"
Var ClearUserDataCheckbox
Var ClearUserData

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
UninstPage custom un.UserDataOptionsPage un.UserDataOptionsLeave
!insertmacro MUI_UNPAGE_INSTFILES

; ---------- Languages ----------
; Persist the chosen installer language so the uninstaller reuses it
; (MUI_UNGETLANGUAGE reads this value instead of asking again).
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "Software\VRCForge"
!define MUI_LANGDLL_REGISTRY_VALUENAME "InstallerLanguage"
; Unicode installer: offer every bundled language regardless of system codepage.
!define MUI_LANGDLL_ALLLANGUAGES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "TradChinese"
!insertmacro MUI_LANGUAGE "Japanese"
!insertmacro MUI_LANGUAGE "English"

LangString WelcomeTitle ${LANG_SIMPCHINESE} "欢迎安装 VRCForge ${VERSION}"
LangString WelcomeTitle ${LANG_TRADCHINESE} "歡迎安裝 VRCForge ${VERSION}"
LangString WelcomeTitle ${LANG_JAPANESE} "VRCForge ${VERSION} セットアップへようこそ"
LangString WelcomeTitle ${LANG_ENGLISH} "Welcome to VRCForge ${VERSION} Setup"
LangString WelcomeText ${LANG_SIMPCHINESE} "VRCForge 是面向 VRChat 创作者的本地 AI 工作台。$\r$\n$\r$\n安装向导将引导你完成安装。安装前建议关闭正在运行的 VRCForge。$\r$\n$\r$\n点击「下一步」继续。"
LangString WelcomeText ${LANG_TRADCHINESE} "VRCForge 是為 VRChat 創作者打造的本機 AI 工作台。$\r$\n$\r$\n安裝精靈將引導你完成安裝。安裝前建議先關閉正在執行的 VRCForge。$\r$\n$\r$\n點選「下一步」繼續。"
LangString WelcomeText ${LANG_JAPANESE} "VRCForge は VRChat クリエイター向けのローカル AI ワークベンチです。$\r$\n$\r$\nこのウィザードがインストール手順をご案内します。実行中の VRCForge は先に終了してください。$\r$\n$\r$\n「次へ」をクリックして続行してください。"
LangString WelcomeText ${LANG_ENGLISH} "VRCForge is a local AI workbench for VRChat creators.$\r$\n$\r$\nThis wizard will guide you through the installation. Please close any running VRCForge instance first.$\r$\n$\r$\nClick Next to continue."
LangString RunText ${LANG_SIMPCHINESE} "安装完成后启动 VRCForge"
LangString RunText ${LANG_TRADCHINESE} "安裝完成後啟動 VRCForge"
LangString RunText ${LANG_JAPANESE} "インストール完了後に VRCForge を起動する"
LangString RunText ${LANG_ENGLISH} "Launch VRCForge after install"
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
LangString UninstallKeptUserData ${LANG_SIMPCHINESE} "VRCForge 程序文件已移除。用户数据仍保留在 $LOCALAPPDATA\VRCForge\agentic-app。"
LangString UninstallKeptUserData ${LANG_TRADCHINESE} "VRCForge 程式檔案已移除。使用者資料仍保留在 $LOCALAPPDATA\VRCForge\agentic-app。"
LangString UninstallKeptUserData ${LANG_JAPANESE} "VRCForge のプログラムファイルを削除しました。ユーザーデータは $LOCALAPPDATA\VRCForge\agentic-app に保持されています。"
LangString UninstallKeptUserData ${LANG_ENGLISH} "VRCForge program files were removed. User data remains in $LOCALAPPDATA\VRCForge\agentic-app."
LangString UninstallClearedUserData ${LANG_SIMPCHINESE} "VRCForge 程序文件、用户数据和已知项目中的历史对话已移除。"
LangString UninstallClearedUserData ${LANG_TRADCHINESE} "VRCForge 程式檔案、使用者資料以及已知專案中的歷史對話已移除。"
LangString UninstallClearedUserData ${LANG_JAPANESE} "VRCForge のプログラムファイル、ユーザーデータ、既知プロジェクトのチャット履歴を削除しました。"
LangString UninstallClearedUserData ${LANG_ENGLISH} "VRCForge program files, user data, and known project chat history were removed."
LangString ClearingUserDataText ${LANG_SIMPCHINESE} "正在清除 VRCForge 用户数据和已知项目的历史对话..."
LangString ClearingUserDataText ${LANG_TRADCHINESE} "正在清除 VRCForge 使用者資料與已知專案的歷史對話..."
LangString ClearingUserDataText ${LANG_JAPANESE} "VRCForge のユーザーデータと既知プロジェクトのチャット履歴を削除しています..."
LangString ClearingUserDataText ${LANG_ENGLISH} "Clearing VRCForge user data and known project chat history..."

Function .onInit
  ; Language dialog: preselects the OS UI language (or the previously
  ; persisted choice) and stores the result under HKCU\Software\VRCForge.
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Function un.onInit
  ; Reuse the language chosen at install time instead of asking again.
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

!macro StopVRCForgeProcesses
  nsExec::ExecToLog 'taskkill /F /IM VRCForge.exe /T'
  nsExec::ExecToLog 'taskkill /F /IM vrcforge_backend.exe /T'
  Sleep 800
!macroend

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
      nsExec::ExecToLog '"$INSTDIR\backend\vrcforge_backend.exe" --cleanup-user-data --cleanup-user-data-root "$LOCALAPPDATA\VRCForge\agentic-app"'
      Pop $0
      ${If} $0 != 0
        RMDir /r "$LOCALAPPDATA\VRCForge\agentic-app"
      ${EndIf}
    ${Else}
      RMDir /r "$LOCALAPPDATA\VRCForge\agentic-app"
    ${EndIf}
  ${EndIf}
FunctionEnd

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

  CreateDirectory "$LOCALAPPDATA\VRCForge\agentic-app\config"
  CreateDirectory "$LOCALAPPDATA\VRCForge\agentic-app\logs"
  CreateDirectory "$LOCALAPPDATA\VRCForge\agentic-app\artifacts"
  CreateDirectory "$LOCALAPPDATA\VRCForge\agentic-app\backups"

  CreateDirectory "$SMPROGRAMS\VRCForge"
  CreateShortCut "$DESKTOP\VRCForge.lnk" "$INSTDIR\VRCForge.exe"
  CreateShortCut "$SMPROGRAMS\VRCForge\VRCForge.lnk" "$INSTDIR\VRCForge.exe"
  CreateShortCut "$SMPROGRAMS\VRCForge\$(UninstallShortcutName)" "$INSTDIR\Uninstall.exe"

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
  Delete "$SMPROGRAMS\VRCForge\卸载 VRCForge.lnk"
  Delete "$SMPROGRAMS\VRCForge\解除安裝 VRCForge.lnk"
  Delete "$SMPROGRAMS\VRCForge\VRCForge をアンインストール.lnk"
  RMDir "$SMPROGRAMS\VRCForge"
  Call un.ClearUserDataIfRequested
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\VRCForge"
  ; Remove the persisted installer language last; it was already read in un.onInit.
  DeleteRegValue HKCU "Software\VRCForge" "InstallerLanguage"
  DeleteRegKey /ifempty HKCU "Software\VRCForge"
  ${If} $ClearUserData == ${BST_CHECKED}
    MessageBox MB_OK "$(UninstallClearedUserData)"
  ${Else}
    MessageBox MB_OK "$(UninstallKeptUserData)"
  ${EndIf}
SectionEnd
