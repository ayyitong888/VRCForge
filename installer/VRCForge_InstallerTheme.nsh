!ifndef VRCFORGE_INSTALLER_THEME_NSH
!define VRCFORGE_INSTALLER_THEME_NSH

; Native MUI2 styling for the installer and uninstaller. Keep Windows controls
; and page flow intact; the theme is carried by sakura-white surfaces, rose-gray
; text, and restrained first-party bitmap artwork.
!define MUI_BGCOLOR "FFFCFD"
!define MUI_TEXTCOLOR "3B3035"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "VRCForge_InstallerHeader.bmp"
!define MUI_HEADERIMAGE_UNBITMAP "VRCForge_InstallerHeader.bmp"
!define MUI_HEADERIMAGE_BITMAP_STRETCH "FitControl"
!define MUI_WELCOMEFINISHPAGE_BITMAP "VRCForge_InstallerWelcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "VRCForge_InstallerWelcome.bmp"
!define MUI_WELCOMEFINISHPAGE_BITMAP_STRETCH "FitControl"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP_STRETCH "FitControl"
!define MUI_LICENSEPAGE_BGCOLOR "FFFCFD"
!define MUI_DIRECTORYPAGE_BGCOLOR "FFFCFD"
!define MUI_INSTFILESPAGE_COLORS "A81D5C FFFCFD"
!define MUI_INSTFILESPAGE_PROGRESSBAR "colored"
!define MUI_FINISHPAGE_TITLE "$(InstallFinishTitle)"
!define MUI_FINISHPAGE_TEXT "$(InstallFinishText)"
!define MUI_UNCONFIRMPAGE_TEXT_TOP "$(UninstallConfirmTop)"
!define MUI_CUSTOMFUNCTION_GUIINIT VrcForgeThemeGuiInit
!define MUI_CUSTOMFUNCTION_UNGUIINIT un.VrcForgeThemeGuiInit

!define VRCFORGE_THEME_TEXT "3B3035"
!define VRCFORGE_THEME_NOTE "A81D5C"
!define VRCFORGE_THEME_FIELD_BG "FFFEFF"

; MUI exposes the window and branding controls through its GUI-init callback.
; Keep its native buttons intact, but remove the default grey page/footer fill
; and hairline separators so they sit on the same near-white Sakura surface.
!macro VRCFORGE_APPLY_WINDOW_SURFACE
  SetCtlColors $HWNDPARENT "" "${MUI_BGCOLOR}"
  SetCtlColors $mui.Branding.Background "" "${MUI_BGCOLOR}"
  SetCtlColors $mui.Branding.Text "${MUI_TEXTCOLOR}" "${MUI_BGCOLOR}"
  ; MUI shows these controls again when entering/leaving its full-window pages.
  ; Collapse their decorative area once instead of rehiding on every page.
  System::Call 'user32::SetWindowPos(p $mui.Line.Standard, p 0, i 0, i 0, i 0, i 0, i 0x16)'
  System::Call 'user32::SetWindowPos(p $mui.Line.FullWindow, p 0, i 0, i 0, i 0, i 0, i 0x16)'
!macroend

Function VrcForgeThemeGuiInit
  !insertmacro VRCFORGE_APPLY_WINDOW_SURFACE
FunctionEnd

Function un.VrcForgeThemeGuiInit
  !insertmacro VRCFORGE_APPLY_WINDOW_SURFACE
FunctionEnd

!macro VRCFORGE_STYLE_BODY_LABEL CONTROL
  SetCtlColors ${CONTROL} "${VRCFORGE_THEME_TEXT}" transparent
!macroend

!macro VRCFORGE_STYLE_NOTE_LABEL CONTROL
  SetCtlColors ${CONTROL} "${VRCFORGE_THEME_NOTE}" transparent
!macroend

!macro VRCFORGE_STYLE_DIRECTORY_FIELD CONTROL
  SetCtlColors ${CONTROL} "${VRCFORGE_THEME_TEXT}" "${VRCFORGE_THEME_FIELD_BG}"
!macroend

!macro VRCFORGE_SET_FONTS
  SetFont /LANG=${LANG_SIMPCHINESE} "Segoe UI" 9
  SetFont /LANG=${LANG_TRADCHINESE} "Segoe UI" 9
  SetFont /LANG=${LANG_JAPANESE} "Segoe UI" 9
  SetFont /LANG=${LANG_ENGLISH} "Segoe UI" 9
!macroend

!endif
