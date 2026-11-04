!ifndef VRCFORGE_DEEP_LINK_NSH
!define VRCFORGE_DEEP_LINK_NSH

!define VRCFORGE_DEEP_LINK_COMMAND "$\"$INSTDIR\VRCForge.exe$\" $\"%1$\""

Function VrcForgeRegisterDeepLink
  !ifndef VRCFORGE_SMOKE_BUILD
    SetRegView 64
    ClearErrors
    ReadRegStr $0 HKLM "Software\Classes\vrcforge\shell\open\command" ""
    IfErrors deep_link_register
    ; Leave a protocol owned by another installation unchanged.
    StrCmp $0 "${VRCFORGE_DEEP_LINK_COMMAND}" deep_link_register deep_link_done
    deep_link_register:
      ClearErrors
      WriteRegStr HKLM "Software\Classes\vrcforge" "" "URL:VRCForge"
      WriteRegStr HKLM "Software\Classes\vrcforge" "URL Protocol" ""
      WriteRegStr HKLM "Software\Classes\vrcforge\DefaultIcon" "" "$\"$INSTDIR\VRCForge.exe$\",0"
      WriteRegStr HKLM "Software\Classes\vrcforge\shell\open\command" "" "${VRCFORGE_DEEP_LINK_COMMAND}"
      IfErrors deep_link_failed
      System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
    deep_link_done:
  !endif
  ClearErrors
  Return
  !ifndef VRCFORGE_SMOKE_BUILD
    deep_link_failed:
      SetErrors
  !endif
FunctionEnd

Function un.VrcForgeRemoveDeepLink
  !ifndef VRCFORGE_SMOKE_BUILD
    SetRegView 64
    ClearErrors
    ReadRegStr $0 HKLM "Software\Classes\vrcforge\shell\open\command" ""
    IfErrors deep_link_uninstall_done
    StrCmp $0 "${VRCFORGE_DEEP_LINK_COMMAND}" 0 deep_link_uninstall_done
    DeleteRegKey HKLM "Software\Classes\vrcforge"
    System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
    deep_link_uninstall_done:
  !endif
FunctionEnd

!endif
