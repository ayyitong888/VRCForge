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
!include nsDialogs.nsh
!include "MUI2.nsh"
Var ClearUserDataCheckbox
Var ClearUserData

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

LangString WelcomeTitle ${LANG_SIMPCHINESE} "欢迎安装 VRCForge ${VERSION}（在线安装）"
LangString WelcomeTitle ${LANG_TRADCHINESE} "歡迎安裝 VRCForge ${VERSION}（線上安裝）"
LangString WelcomeTitle ${LANG_JAPANESE} "VRCForge ${VERSION} Web セットアップへようこそ"
LangString WelcomeTitle ${LANG_ENGLISH} "Welcome to VRCForge ${VERSION} Web Setup"
LangString WelcomeText ${LANG_SIMPCHINESE} "VRCForge 是面向 VRChat 创作者的本地 AI 工作台。$\r$\n$\r$\n本安装器体积较小，将在安装过程中联网下载完整组件，请保持网络畅通。$\r$\n$\r$\n点击「下一步」继续。"
LangString WelcomeText ${LANG_TRADCHINESE} "VRCForge 是為 VRChat 創作者打造的本機 AI 工作台。$\r$\n$\r$\n本安裝程式體積較小，將在安裝過程中連線下載完整元件，請保持網路暢通。$\r$\n$\r$\n點選「下一步」繼續。"
LangString WelcomeText ${LANG_JAPANESE} "VRCForge は VRChat クリエイター向けのローカル AI ワークベンチです。$\r$\n$\r$\nこれは小さな Web インストーラーです。インストール中に完全なコンポーネントをダウンロードするため、ネットワーク接続を維持してください。$\r$\n$\r$\n「次へ」をクリックして続行してください。"
LangString WelcomeText ${LANG_ENGLISH} "VRCForge is a local AI workbench for VRChat creators.$\r$\n$\r$\nThis is a small web installer: it downloads the full payload during installation, so please stay online.$\r$\n$\r$\nClick Next to continue."
LangString RunText ${LANG_SIMPCHINESE} "安装完成后启动 VRCForge"
LangString RunText ${LANG_TRADCHINESE} "安裝完成後啟動 VRCForge"
LangString RunText ${LANG_JAPANESE} "インストール完了後に VRCForge を起動する"
LangString RunText ${LANG_ENGLISH} "Launch VRCForge after install"
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
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$$ErrorActionPreference = ''SilentlyContinue''; $$root = Join-Path $$env:LOCALAPPDATA ''VRCForge\agentic-app''; $$projects = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase); function AddProject($$p) { if ($$p -and [System.IO.Path]::IsPathRooted([string]$$p)) { [void]$$projects.Add([string]$$p) } }; $$idx = Join-Path $$root ''chat-projects.json''; if (Test-Path -LiteralPath $$idx) { $$j = Get-Content -LiteralPath $$idx -Raw | ConvertFrom-Json; foreach ($$p in @($$j.projectPaths)) { AddProject $$p } }; $$prefs = Join-Path $$root ''custom-projects.json''; if (Test-Path -LiteralPath $$prefs) { $$j = Get-Content -LiteralPath $$prefs -Raw | ConvertFrom-Json; foreach ($$p in @($$j.customPaths + $$j.hiddenPaths)) { AddProject $$p } }; $$legacy = Join-Path $$root ''chat-transcripts.json''; if (Test-Path -LiteralPath $$legacy) { $$j = Get-Content -LiteralPath $$legacy -Raw | ConvertFrom-Json; foreach ($$c in @($$j.chats)) { AddProject $$c.projectPath } }; foreach ($$p in $$projects) { $$file = Join-Path $$p ''.vrcforge\chat-transcripts.json''; Remove-Item -LiteralPath $$file -Force; $$dir = Join-Path $$p ''.vrcforge''; if ((Test-Path -LiteralPath $$dir) -and -not (Get-ChildItem -LiteralPath $$dir -Force | Select-Object -First 1)) { Remove-Item -LiteralPath $$dir -Force } }; Remove-Item -LiteralPath $$root -Recurse -Force"'
  ${EndIf}
FunctionEnd

Section "Install"
  SetRegView 64
  DetailPrint "$(DownloadingText)"
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$$ProgressPreference = ''SilentlyContinue''; New-Item -ItemType Directory -Force -Path ''$TEMP\VRCForge'' | Out-Null; Invoke-WebRequest -Uri ''${DOWNLOAD_URL}'' -OutFile ''$TEMP\VRCForge\payload.zip''"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "$(DownloadFailedText)"
    Abort
  ${EndIf}

  DetailPrint "$(VerifyingText)"
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath ''$TEMP\VRCForge\payload.zip'').Hash.ToLowerInvariant(); if ($$actual -ne ''${PAYLOAD_SHA256}''.ToLowerInvariant()) { Write-Error \"Payload SHA256 mismatch. expected=${PAYLOAD_SHA256} actual=$$actual\"; exit 1 }"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "$(HashMismatchText)"
    Abort
  ${EndIf}

  !insertmacro StopVRCForgeProcesses
  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\dashboard"
  RMDir /r "$INSTDIR\unity_plugin"
  RMDir /r "$INSTDIR\tools"
  RMDir /r "$INSTDIR\licenses"
  CreateDirectory "$INSTDIR"

  DetailPrint "$(ExtractingText)"
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath ''$TEMP\VRCForge\payload.zip'' -DestinationPath ''$INSTDIR'' -Force; Remove-Item -LiteralPath ''$INSTDIR\config'',''$INSTDIR\logs'',''$INSTDIR\artifacts'' -Recurse -Force -ErrorAction SilentlyContinue"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "$(ExtractFailedText)"
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
  ; Remove the persisted installer language last; it was already read in un.onInit.
  DeleteRegValue HKCU "Software\VRCForge" "InstallerLanguage"
  DeleteRegKey /ifempty HKCU "Software\VRCForge"
  Call un.ClearUserDataIfRequested
  ${If} $ClearUserData == ${BST_CHECKED}
    MessageBox MB_OK "$(UninstallClearedUserData)"
  ${Else}
    MessageBox MB_OK "$(UninstallKeptUserData)"
  ${EndIf}
SectionEnd
