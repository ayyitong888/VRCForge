!ifndef VRCFORGE_VSK_ASSOCIATION_NSH
!define VRCFORGE_VSK_ASSOCIATION_NSH

!define VRCFORGE_VSK_PROGID "VRCForge.SkillPackage"
!define VRCFORGE_VSK_OPEN_COMMAND "$\"$INSTDIR\VRCForge.exe$\" $\"%1$\""

Function VrcForgeRegisterVskAssociation
  !ifndef VRCFORGE_SMOKE_BUILD
    SetRegView 64

    ; Do not replace a ProgID command owned by another VRCForge installation.
    ClearErrors
    ReadRegStr $0 HKLM "Software\Classes\${VRCFORGE_VSK_PROGID}\shell\open\command" ""
    IfErrors vsk_register_command
    StrCmp $0 "${VRCFORGE_VSK_OPEN_COMMAND}" vsk_register_command vsk_register_done

    vsk_register_command:
      ClearErrors
      WriteRegStr HKLM "Software\Classes\${VRCFORGE_VSK_PROGID}" "" "VRCForge Skill Package"
      WriteRegStr HKLM "Software\Classes\${VRCFORGE_VSK_PROGID}\DefaultIcon" "" "$\"$INSTDIR\VRCForge.exe$\",0"
      WriteRegStr HKLM "Software\Classes\${VRCFORGE_VSK_PROGID}\shell\open\command" "" "${VRCFORGE_VSK_OPEN_COMMAND}"
      WriteRegStr HKLM "Software\Classes\.vsk\OpenWithProgids" "${VRCFORGE_VSK_PROGID}" ""
      IfErrors vsk_register_failed

      ; Set a default only when none exists or this ProgID is already selected.
      ClearErrors
      ReadRegStr $0 HKLM "Software\Classes\.vsk" ""
      IfErrors vsk_register_default
      StrCmp $0 "" vsk_register_default
      StrCmp $0 "${VRCFORGE_VSK_PROGID}" vsk_register_default vsk_register_notify
    vsk_register_default:
      ClearErrors
      WriteRegStr HKLM "Software\Classes\.vsk" "" "${VRCFORGE_VSK_PROGID}"
      IfErrors vsk_register_failed
    vsk_register_notify:
      System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
    vsk_register_done:
  !endif
  ClearErrors
  Return
  !ifndef VRCFORGE_SMOKE_BUILD
    vsk_register_failed:
      SetErrors
  !endif
FunctionEnd

Function un.VrcForgeRemoveVskAssociation
  !ifndef VRCFORGE_SMOKE_BUILD
    SetRegView 64
    ClearErrors
    ReadRegStr $0 HKLM "Software\Classes\${VRCFORGE_VSK_PROGID}\shell\open\command" ""
    IfErrors vsk_uninstall_done
    StrCmp $0 "${VRCFORGE_VSK_OPEN_COMMAND}" 0 vsk_uninstall_done

    DeleteRegValue HKLM "Software\Classes\.vsk\OpenWithProgids" "${VRCFORGE_VSK_PROGID}"
    DeleteRegKey HKLM "Software\Classes\${VRCFORGE_VSK_PROGID}"
    ; Keep .vsk's default value; Windows ignores it when its ProgID is unregistered.
    System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
    vsk_uninstall_done:
  !endif
FunctionEnd

!endif
