; Instalador da Dock (Inno Setup 6). Gerado pelo GitHub Actions ou pelo build.ps1.
; Instala so para o usuario atual (nao pede senha de administrador) e liga a dock ao iniciar o Windows.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B5C1D0E2-3A4F-4C77-9E21-6D0F5A1E7C11}
AppName=Dock
AppVersion={#AppVersion}
AppPublisher=Dock
DefaultDirName={localappdata}\Programs\Dock
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=Dock-Setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\Dock.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ShowLanguageDialog=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "iniciar"; Description: "Abrir a Dock quando ligar o Windows"

[Files]
Source: "..\dist\Dock\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\Dock"; Filename: "{app}\Dock.exe"
; mesmo nome que a opcao "Abrir a dock ao ligar o Windows" das Preferencias usa (Dock.lnk)
Name: "{userstartup}\Dock"; Filename: "{app}\Dock.exe"; WorkingDir: "{app}"; Tasks: iniciar

[Run]
; roda tambem nas atualizacoes silenciosas: e assim que a dock volta sozinha depois de atualizar
Filename: "{app}\Dock.exe"; Description: "Abrir a Dock agora"; Flags: nowait postinstall

[UninstallRun]
; devolve a barra de tarefas do Windows ao normal antes de apagar os arquivos
Filename: "{app}\Dock.exe"; Parameters: "--restaurar"; Flags: runhidden; RunOnceId: "RestaurarBarra"

[Code]
procedure FecharDockEmExecucao();
var
  Codigo: Integer;
begin
  { devolve a barra de tarefas e fecha a dock antiga para poder trocar os arquivos }
  if FileExists(ExpandConstant('{app}\Dock.exe')) then
    Exec(ExpandConstant('{app}\Dock.exe'), '--restaurar', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  Exec('taskkill.exe', '/F /IM Dock.exe /T', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
  Sleep(600);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  FecharDockEmExecucao();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  FecharDockEmExecucao();
  Result := True;
end;
