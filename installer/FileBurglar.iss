#ifndef PayloadDir
#error PayloadDir must point to the verified application payload.
#endif
#ifndef BuildVersion
#define BuildVersion "1.4.5"
#endif
#define SourceRoot AddBackslash(SourcePath) + ".."
[Setup]
AppId={{D32B72AB-933E-4E67-A590-991761AC704D}
AppName=File Burglar
AppVersion={#BuildVersion}
AppVerName=File Burglar {#BuildVersion}
AppPublisher=zerØ software
AppPublisherURL=https://github.com/snuffy420
AppSupportURL=https://snuffy420.github.io/FileBurglar/docs/contact.html
AppUpdatesURL=https://github.com/snuffy420/FileBurglar/releases
DefaultDirName={autopf}\File Burglar
DefaultGroupName=File Burglar
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern dark includetitlebar
WizardSizePercent=110
DisableWelcomePage=no
DisableDirPage=no
SetupIconFile={#SourceRoot}\assets\fileburglar.ico
UninstallDisplayIcon={app}\FileBurglar.exe
WizardImageFile={#SourceRoot}\assets\installer-side.bmp
WizardSmallImageFile={#SourceRoot}\assets\installer-small.bmp
WizardImageStretch=yes
LicenseFile={#SourceRoot}\LICENSE
OutputBaseFilename=FileBurglar-{#BuildVersion}-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
LZMANumBlockThreads=2
CloseApplications=yes
RestartApplications=no
Uninstallable=yes
UninstallDisplayName=File Burglar {#BuildVersion}
VersionInfoVersion={#BuildVersion}.0
VersionInfoCompany=zerØ software
VersionInfoDescription=File Burglar Windows x64 installer
VersionInfoProductName=File Burglar
VersionInfoProductVersion={#BuildVersion}.0
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "portable.txt,Data\*"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\File Burglar"; Filename: "{app}\FileBurglar.exe"; WorkingDir: "{app}"
Name: "{group}\Contact File Burglar"; Filename: "{app}\docs\contact.html"
Name: "{group}\Uninstall File Burglar"; Filename: "{uninstallexe}"
Name: "{autodesktop}\File Burglar"; Filename: "{app}\FileBurglar.exe"; Tasks: desktopicon; WorkingDir: "{app}"
[Run]
Filename: "{app}\FileBurglar.exe"; Description: "Launch File Burglar"; Flags: nowait postinstall skipifsilent unchecked
[UninstallRun]
Filename: "{app}\FileBurglar.exe"; Parameters: "--remove-autostart"; Flags: runhidden; RunOnceId: "FileBurglarRemoveOwnedStartup"
