;
; hid2serial — Windows installer (NSIS, modern UI)
;
; Builds a self-contained setup.exe that:
;   - Bundles Python 3.12 embeddable + every required wheel
;   - Installs to %PROGRAMFILES%\hid2serial
;   - Creates %PROGRAMDATA%\hid2serial\config.yaml from example
;   - Registers the `hid2serial` Windows service (auto-start on boot)
;   - Adds the tray applet to current user's Startup folder
;   - Detects com0com presence; warns if missing and links to download
;   - Provides a clean uninstaller
;
; Build from Linux with:
;   ./packaging/windows/build-installer.sh
;
; The build script downloads Python embeddable + all wheels into
; build/win/, then invokes makensis to produce dist/hid2serial-<version>-setup.exe.

!define APP_NAME      "hid2serial"
; APP_VERSION is normally passed via -D from the build script
; (`makensis -DAPP_VERSION=...`); fall back to a static string if
; running makensis directly.
!ifndef APP_VERSION
    !define APP_VERSION "dev"
!endif
!define APP_PUBLISHER "Rosen Vladimirov"
!define APP_URL       "https://github.com/rosenvladimirov/hid2serial"
!define SVC_NAME      "hid2serial"

Name        "${APP_NAME} ${APP_VERSION}"
OutFile     "..\..\dist\${APP_NAME}-${APP_VERSION}-setup.exe"
InstallDir  "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "Software\${APP_NAME}" "InstallDir"

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

RequestExecutionLevel admin
SetCompressor /SOLID lzma

; ─── Modern UI pages ────────────────────────────────────────

!define MUI_ABORTWARNING
!define MUI_ICON   "icons\hid2serial.ico"
!define MUI_UNICON "icons\hid2serial.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

; ─── Main install section ───────────────────────────────────

Section "hid2serial daemon + tray (required)" SEC_MAIN
    SectionIn RO  ; required
    SetOutPath "$INSTDIR"

    ; Python embeddable runtime + already-pip-installed wheels
    File /r "..\..\build\win\python\*.*"

    ; hid2serial source (importable from the embedded site-packages)
    File /r "..\..\build\win\hid2serial\*.*"

    ; Default config — only writes if missing.
    ; NSIS doesn't have $PROGRAMDATA, so we read it from the env.
    ReadEnvStr $0 "ProgramData"
    StrCmp $0 "" use_fallback have_progdata
    use_fallback:
        StrCpy $0 "C:\ProgramData"
    have_progdata:
    SetOutPath "$0\${APP_NAME}"
    SetOverwrite off
    File /oname=config.yaml "..\..\config.example.yaml"
    SetOverwrite on
    File "..\..\config.example.yaml"

    ; Service registration via the bundled Python
    DetailPrint "Registering Windows service..."
    nsExec::ExecToLog '"$INSTDIR\python.exe" -m pywin32_postinstall -install'
    nsExec::ExecToLog '"$INSTDIR\python.exe" -m hid2serial.win_service --startup auto install'

    ; Tray autostart for the user who ran the installer
    SetShellVarContext current
    CreateShortCut "$SMSTARTUP\hid2serial-tray.lnk" \
        "$INSTDIR\pythonw.exe" "-m hid2serial tray" \
        "$INSTDIR\icons\hid2serial.ico" 0 SW_SHOWNORMAL "" \
        "Toggle hid2serial redirect"
    SetShellVarContext all

    ; Start menu entries
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME} Tray.lnk" \
        "$INSTDIR\pythonw.exe" "-m hid2serial tray" \
        "$INSTDIR\icons\hid2serial.ico"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME} Doctor.lnk" \
        "$INSTDIR\python.exe" "-m hid2serial doctor"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\Edit config.lnk" \
        "notepad.exe" "$0\${APP_NAME}\config.yaml"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk" \
        "$INSTDIR\uninstall.exe"

    ; Registry — Add/Remove Programs entry
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "DisplayName" "${APP_NAME} ${APP_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "URLInfoAbout" "${APP_URL}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "UninstallString" '"$INSTDIR\uninstall.exe"'
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "DisplayIcon" "$INSTDIR\icons\hid2serial.ico"
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "NoModify" 1
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "NoRepair" 1

    WriteRegStr HKLM "Software\${APP_NAME}" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "Software\${APP_NAME}" "Version" "${APP_VERSION}"

    WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

; ─── com0com presence check (informational) ────────────────

Section "Detect com0com" SEC_COM0COM
    SectionIn RO
    IfFileExists "$PROGRAMFILES\com0com\setupc.exe" com0com_ok com0com_missing
    com0com_missing:
        DetailPrint "WARNING: com0com is not installed."
        MessageBox MB_OK|MB_ICONEXCLAMATION \
            "com0com is required for hid2serial to expose a virtual COM port.$\r$\n$\r$\nDownload it from https://sourceforge.net/projects/com0com/ and create a port pair (e.g. CNCA0 / COM21) before starting the service.$\r$\n$\r$\nAfter installing com0com, edit %PROGRAMDATA%\${APP_NAME}\config.yaml and set output.windows.com_pair to your pair."
        Goto com0com_done
    com0com_ok:
        DetailPrint "com0com detected — OK."
    com0com_done:
SectionEnd

; ─── Uninstaller ────────────────────────────────────────────

Section "Uninstall"
    DetailPrint "Stopping and removing the Windows service..."
    nsExec::ExecToLog 'sc stop ${SVC_NAME}'
    nsExec::ExecToLog '"$INSTDIR\python.exe" -m hid2serial.win_service remove'

    SetShellVarContext current
    Delete "$SMSTARTUP\hid2serial-tray.lnk"
    SetShellVarContext all

    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME} Tray.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME} Doctor.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\Edit config.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk"
    RMDir  "$SMPROGRAMS\${APP_NAME}"

    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
    DeleteRegKey HKLM "Software\${APP_NAME}"

    ; Leave %PROGRAMDATA%\hid2serial\config.yaml in place — admin's data,
    ; not ours to delete. They can drop it manually if they want a clean
    ; uninstall.
    RMDir /r "$INSTDIR"
SectionEnd
