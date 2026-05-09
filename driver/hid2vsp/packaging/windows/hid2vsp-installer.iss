; hid2vsp — Windows installer (Inno Setup 6)
;
; Bundles:
;   - hid2vsp UMDF driver (hid2vsp.dll, .inf, .cat) + test-signing cert
;   - hid2vsp_setup.exe   helper that creates/removes Root\* devnodes
;   - hid2vsp_tray.exe    NotifyIcon applet showing pair_a/pair_b -> COMx
;
; What it does:
;   - Imports the test-sign cert into Root + TrustedPublisher.
;   - pnputil /add-driver  to register the package.
;   - hid2vsp_setup install  for both pair_a and pair_b -> creates the
;     two paired COM ports.
;   - Starts the tray applet, registers it for run-on-login (HKCU Run).
;
; Uninstall reverses all of the above (best-effort; certs are LEFT in
; the store — the user may have other test-signed drivers depending on
; them, and removing a Root cert prompts UAC again).
;
; Build (on Windows with Inno Setup 6 installed):
;   ISCC.exe packaging\windows\hid2vsp-installer.iss
; or via the wrapper:
;   C:\Temp\build_hid2vsp_installer.cmd

#define AppName       "hid2vsp"
#define AppVersion    "1.0.0"
#define AppPublisher  "Rosen Vladimirov"
#define AppURL        "https://github.com/rosenvladimirov/hid2serial"

[Setup]
AppId={{6F3B4D1C-2A8E-4F5D-9C1A-7E2B0F4FE9A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf64}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=hid2vsp-{#AppVersion}-setup
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\hid2vsp_tray.exe
UninstallDisplayName={#AppName} {#AppVersion}
SetupIconFile=icons\hid2serial.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Code]
// Copy Inno's runtime log into the install dir at the end so the tray's
// "Open install log" menu item can show it.
procedure CurStepChanged(CurStep: TSetupStep);
var src, dst: string;
begin
  if CurStep = ssDone then begin
    src := ExpandConstant('{log}');
    dst := ExpandConstant('{app}\install.log');
    if (src <> '') and FileExists(src) then
      FileCopy(src, dst, False);
  end;
end;

[Files]
; --- driver package ---
Source: "stage\driver\hid2vsp.dll";       DestDir: "{app}\driver"; Flags: ignoreversion
Source: "stage\driver\hid2vsp.inf";       DestDir: "{app}\driver"; Flags: ignoreversion
Source: "stage\driver\hid2vsp.cat";       DestDir: "{app}\driver"; Flags: ignoreversion
Source: "stage\driver\hid2vsp_test.cer";  DestDir: "{app}\driver"; Flags: ignoreversion
; --- binaries ---
Source: "stage\bin\hid2vsp_tray.exe";     DestDir: "{app}";        Flags: ignoreversion
Source: "stage\bin\hid2vsp_setup.exe";    DestDir: "{app}";        Flags: ignoreversion
; --- assets ---
Source: "icons\hid2serial.ico";           DestDir: "{app}";        DestName: "hid2vsp.ico"; Flags: ignoreversion
Source: "cleanup_driver.ps1";             DestDir: "{app}";        Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName} tray";  Filename: "{app}\hid2vsp_tray.exe"; IconFilename: "{app}\hid2vsp.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Registry]
; Run tray on login for the installing user.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#AppName}_tray"; \
    ValueData: """{app}\hid2vsp_tray.exe"""; Flags: uninsdeletevalue

[Run]
; 1. Import test-signing cert (Root + TrustedPublisher).
Filename: "certutil.exe"; \
    Parameters: "-addstore Root ""{app}\driver\hid2vsp_test.cer"""; \
    StatusMsg: "Importing test-signing certificate..."; \
    Flags: runhidden

Filename: "certutil.exe"; \
    Parameters: "-addstore TrustedPublisher ""{app}\driver\hid2vsp_test.cer"""; \
    StatusMsg: "Importing test-signing certificate..."; \
    Flags: runhidden

; 2. Register the driver package.
Filename: "pnputil.exe"; \
    Parameters: "/add-driver ""{app}\driver\hid2vsp.inf"" /install"; \
    StatusMsg: "Registering hid2vsp driver..."; \
    Flags: runhidden

; 3. Create the two root-enumerated devnodes (the pair).
Filename: "{app}\hid2vsp_setup.exe"; \
    Parameters: "install ""{app}\driver\hid2vsp.inf"" Root\hid2vsp_pair_a"; \
    StatusMsg: "Creating pair_a..."; \
    Flags: runhidden

Filename: "{app}\hid2vsp_setup.exe"; \
    Parameters: "install ""{app}\driver\hid2vsp.inf"" Root\hid2vsp_pair_b"; \
    StatusMsg: "Creating pair_b..."; \
    Flags: runhidden

; 4. Start tray now (also runs on next login via HKCU Run).
;    runasoriginaluser: drop back to the unelevated user IL — Shell_NotifyIcon
;    register from a High-IL process produces a translucent placeholder icon
;    and broken click routing because explorer.exe runs at Medium IL.
Filename: "{app}\hid2vsp_tray.exe"; \
    Description: "Start hid2vsp tray applet"; \
    Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
; 1. Stop tray (tray's mutex won't let installer-spawned copy exit cleanly,
;    so kill via taskkill before removing files).
Filename: "taskkill.exe"; Parameters: "/F /IM hid2vsp_tray.exe /T"; \
    Flags: runhidden; RunOnceId: "KillTray"

; 2. Remove devnodes (both halves, idempotent).
Filename: "{app}\hid2vsp_setup.exe"; Parameters: "remove Root\hid2vsp_pair_a"; \
    Flags: runhidden; RunOnceId: "RemovePairA"

Filename: "{app}\hid2vsp_setup.exe"; Parameters: "remove Root\hid2vsp_pair_b"; \
    Flags: runhidden; RunOnceId: "RemovePairB"

; 3. Delete driver package from the driver store. Requires looking up
;    the OEM-assigned name (oemNN.inf) — done via PowerShell since
;    pnputil /delete-driver wants that synthetic name, not our INF.
Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\cleanup_driver.ps1"""; \
    Flags: runhidden; RunOnceId: "DeleteDriver"
