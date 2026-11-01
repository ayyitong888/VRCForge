!ifndef VRCFORGE_INSTALLER_THEME_NSH
!define VRCFORGE_INSTALLER_THEME_NSH

; Native MUI2 styling for the installer and uninstaller. Keep Windows controls
; and page flow intact; the theme is carried by sakura-white surfaces, rose-gray
; text, and restrained first-party bitmap artwork.
!define MUI_BGCOLOR "FDF7F8"
!define MUI_TEXTCOLOR "3B3035"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "VRCForge_InstallerHeader.bmp"
!define MUI_HEADERIMAGE_UNBITMAP "VRCForge_InstallerHeader.bmp"
!define MUI_HEADERIMAGE_BITMAP_STRETCH "AspectFitHeight"
!define MUI_WELCOMEFINISHPAGE_BITMAP "VRCForge_InstallerWelcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "VRCForge_InstallerWelcome.bmp"
!define MUI_LICENSEPAGE_BGCOLOR "FFFBFC"
!define MUI_DIRECTORYPAGE_BGCOLOR "FFFBFC"
!define MUI_INSTFILESPAGE_COLORS "A81D5C FDF7F8"
!define MUI_INSTFILESPAGE_PROGRESSBAR "colored"
!define MUI_FINISHPAGE_TITLE "$(InstallFinishTitle)"
!define MUI_FINISHPAGE_TEXT "$(InstallFinishText)"
!define MUI_UNCONFIRMPAGE_TEXT_TOP "$(UninstallConfirmTop)"

!define VRCFORGE_THEME_TEXT "3B3035"
!define VRCFORGE_THEME_NOTE "A81D5C"
!define VRCFORGE_THEME_FIELD_BG "FFFEFF"

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
