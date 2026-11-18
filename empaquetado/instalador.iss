; Instalador de "Descargador de vídeos" (Inno Setup 6).
; Se compila desde empaquetado/construir.py, que pasa /DVersion=x.y.z

#ifndef Version
  #define Version "1.0.0"
#endif
#define Nombre "Descargador de vídeos"
#define Exe "Descargador de videos.exe"

[Setup]
AppId={{658D406A-9992-4504-A069-C97319D13F93}
AppName={#Nombre}
AppVersion={#Version}
AppVerName={#Nombre} {#Version}
AppPublisher={#Nombre}
VersionInfoVersion={#Version}
; Instalación solo para el usuario actual: no pide permisos de administrador.
PrivilegesRequired=lowest
DefaultDirName={autopf}\Descargador de videos
DisableProgramGroupPage=yes
DisableDirPage=auto
UninstallDisplayIcon={app}\{#Exe}
UninstallDisplayName={#Nombre}
SetupIconFile=icono.ico
; Términos de uso que hay que aceptar para poder instalar.
LicenseFile=dist\Descargador de videos\TERMINOS.txt
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
OutputDir=.
OutputBaseFilename=Instalar Descargador de videos

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[InstallDelete]
; Al actualizar, quitar los archivos de la versión anterior.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\Descargador de videos\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#Nombre}"; Filename: "{app}\{#Exe}"
Name: "{autodesktop}\{#Nombre}"; Filename: "{app}\{#Exe}"; Tasks: escritorio

[Run]
Filename: "{app}\{#Exe}"; Description: "Abrir {#Nombre} ahora"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Ajustes, caché y motor actualizado. Los vídeos descargados NO se borran.
Type: filesandordirs; Name: "{localappdata}\DescargadorVideos"
Type: filesandordirs; Name: "{app}"