; Установщик «Плеер Plixus». Сборка: installer\build.ps1
; Тихая установка: PleerPlixus-Setup.exe /VERYSILENT /HOST=10.0.20.32 /RECDIR="D:\Записи" /TASKS="desktopicon,autostart"

#define AppName "Плеер Plixus"
#define AppExe "PleerPlixus.exe"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{8C1F3B6E-5D2A-4C57-9E1B-2A7F4D9C6B31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=RMS
DefaultDirName={localappdata}\Programs\PleerPlixus
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=PleerPlixus-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Ярлыки:"
Name: "autostart"; Description: "Запускать вместе с Windows — программа будет работать всегда, и метки микрофонов не потеряются"; GroupDescription: "Запуск:"

[Files]
Source: "dist\PleerPlixus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--no-browser"; Flags: runminimized; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Открыть плеер"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\config.json"
Type: files; Name: "{app}\config.tmp"

[Code]
var
  HostPage: TInputQueryWizardPage;
  DirPage: TInputDirWizardPage;
  CheckButton: TNewButton;
  CheckLabel: TNewStaticText;

function JsonValue(const Text, Key: String): String;
var
  P, Q: Integer;
  S: String;
begin
  Result := '';
  P := Pos('"' + Key + '"', Text);
  if P = 0 then Exit;
  S := Copy(Text, P + Length(Key) + 2, MaxInt);
  P := Pos('"', S);
  if P = 0 then Exit;
  S := Copy(S, P + 1, MaxInt);
  Q := Pos('"', S);
  if Q = 0 then Exit;
  Result := Copy(S, 1, Q - 1);
  StringChangeEx(Result, '\\', '\', True);
end;

function JsonEscape(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure CheckButtonClick(Sender: TObject);
var
  Host, Cmd: String;
  Code: Integer;
begin
  Host := Trim(HostPage.Values[0]);
  if Host = '' then
  begin
    CheckLabel.Caption := 'Укажите адрес Plixus.';
    Exit;
  end;
  CheckLabel.Caption := 'Проверяю связь с ' + Host + '…';
  WizardForm.Refresh;
  Cmd := '-NoProfile -NonInteractive -Command "$c = New-Object Net.Sockets.TcpClient; ' +
         '$r = $c.BeginConnect(''' + Host + ''', 6111, $null, $null); ' +
         'if ($r.AsyncWaitHandle.WaitOne(3000) -and $c.Connected) { exit 0 } else { exit 1 }"';
  if Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
    CheckLabel.Caption := 'Plixus ' + Host + ' отвечает.'
  else
    CheckLabel.Caption := 'Нет ответа от ' + Host + ' (порт 6111). Проверьте адрес и что компьютер в той же сети. ' +
                          'Продолжить можно — адрес потом меняется в плеере (шестерёнка).';
end;

procedure InitializeWizard;
var
  Old: AnsiString;
  OldHost, OldDir: String;
begin
  OldHost := '';
  OldDir := '';
  if LoadStringFromFile(ExpandConstant('{localappdata}\Programs\PleerPlixus\config.json'), Old) then
  begin
    OldHost := JsonValue(UTF8Decode(Old), 'host');
    OldDir := JsonValue(UTF8Decode(Old), 'rec_dir');
  end;
  if ExpandConstant('{param:HOST|}') <> '' then OldHost := ExpandConstant('{param:HOST|}');
  if ExpandConstant('{param:RECDIR|}') <> '' then OldDir := ExpandConstant('{param:RECDIR|}');
  if OldHost = '' then OldHost := '10.0.20.32';
  if OldDir = '' then OldDir := ExpandConstant('{userdocs}') + '\Записи Plixus';

  HostPage := CreateInputQueryPage(wpSelectDir, 'Подключение к Plixus',
    'Адрес центрального блока Televic Plixus в вашей сети.',
    'Обычно это 10.0.20.32. Компьютер должен быть в одной сети с Plixus.');
  HostPage.Add('Адрес Plixus:', False);
  HostPage.Values[0] := OldHost;

  CheckButton := TNewButton.Create(HostPage);
  CheckButton.Parent := HostPage.Surface;
  CheckButton.Caption := 'Проверить связь';
  CheckButton.Left := 0;
  CheckButton.Top := HostPage.Edits[0].Top + HostPage.Edits[0].Height + ScaleY(12);
  CheckButton.Width := ScaleX(150);
  CheckButton.Height := ScaleY(26);
  CheckButton.OnClick := @CheckButtonClick;

  CheckLabel := TNewStaticText.Create(HostPage);
  CheckLabel.Parent := HostPage.Surface;
  CheckLabel.Left := 0;
  CheckLabel.Top := CheckButton.Top + CheckButton.Height + ScaleY(10);
  CheckLabel.Width := HostPage.SurfaceWidth;
  CheckLabel.AutoSize := False;
  CheckLabel.WordWrap := True;
  CheckLabel.Height := ScaleY(60);
  CheckLabel.Caption := '';

  DirPage := CreateInputDirPage(HostPage.ID, 'Папка для записей',
    'Куда складывать скачанные записи с метками.',
    'Каждая запись — отдельная папка с каналами (Floor, 01 RUS…) и метками микрофонов. ' +
    'При удалении программы записи остаются.', False, '');
  DirPage.Add('');
  DirPage.Values[0] := OldDir;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = HostPage.ID) and (Trim(HostPage.Values[0]) = '') then
  begin
    MsgBox('Укажите адрес Plixus.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
begin
  if CurStep = ssPostInstall then
  begin
    SetArrayLength(Lines, 1);
    Lines[0] := '{"host": "' + JsonEscape(Trim(HostPage.Values[0])) + '", "rec_dir": "' +
                JsonEscape(Trim(DirPage.Values[0])) + '"}';
    ForceDirectories(Trim(DirPage.Values[0]));
    SaveStringsToUTF8File(ExpandConstant('{app}\config.json'), Lines, False);
  end;
end;
