; ============================================================================
; HopOn 安装程序 —— Inno Setup 6 脚本
;
; 设计要点（2026-11-05）：
;
; 1. **程序与数据分离**。程序装 {localappdata}\Programs\HopOn（无需管理员），
;    用户数据（config.json / data\ / logs\）在 {localappdata}\HopOn。
;    卸载只删程序目录，**绝不碰数据目录** —— 这是数据外置改造的最终目的。
;
; 2. **卸载永不删用户数据**。不写 [UninstallDelete] 针对数据目录的条目；
;    反倒在卸载时提示数据保留位置（见 [Code] 的 CurUninstallStepChanged）。
;
; 3. **升级不覆盖配置**。程序的 config.json 只作为模板安装（若目标不存在），
;    已有配置一律保留。
;
; 4. **首启不写程序目录**。引擎已改成把数据写 %LOCALAPPDATA%\HopOn，
;    程序目录保持只读。
;
; 编译：
;   ISCC.exe /DAppVersion=2.4.0 installer\HopOn.iss
;   产物：installer\Output\HopOn_v2.4.0_Setup.exe
;
; 前置：先跑 _build_release.py 生成 release\HopOn_v<VERSION>\ 目录。
; ============================================================================

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName        "HopOn"
#define AppPublisher   "QianYiXi0723"
#define AppURL         "https://github.com/QianYiXi0723/HopOn"
#define AppExeName     "HopOn.exe"

; 程序目录：per-user 安装，不需要管理员权限
#define ProgramDir     "{localappdata}\Programs\HopOn"
; 用户数据目录（引擎写这里，卸载不删）
#define UserDataDir    "{localappdata}\HopOn"

; 发行产物目录（相对本脚本上层）：release\HopOn_v<版本>\
#define ReleaseDir "..\release\HopOn_v" + AppVersion

[Setup]
AppId={{8F3A2C41-5B7D-4E96-9A1C-7D2F0B9E4A61}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={#ProgramDir}
DefaultGroupName={#AppName}
; per-user 安装：普通用户可装，不触发 UAC
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 64 位程序
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 压缩
Compression=lzma2/max
SolidCompression=yes
; 界面
WizardStyle=modern
DisableProgramGroupPage=yes
; 不允许改数据目录之外的东西
OutputDir=Output
OutputBaseFilename={#AppName}_v{#AppVersion}_Setup
; 图标
SetupIconFile=..\shell\app.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
; 关闭正在运行的实例，避免覆盖失败。
; 2026-11-19：Job Object 内的壳 RestartManager 关不掉（会弹「无法自动关闭」），
; 真正的清场在 [Code] PrepareToInstall 里 taskkill；这里 force 只作兜底。
CloseApplications=force
RestartApplications=no
; 2026-11-16：Job Object 内的进程 RestartManager 关不掉，
; 桥接已先精确清场；此处显式声明过滤清单（默认值即 *.exe,*.dll,*.chm）
CloseApplicationsFilter=*.exe,*.dll
; 版本信息
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} 安装程序

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked
Name: "startup"; Description: "开机自动启动"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; 壳可执行 + WebView2 三件套
Source: "{#ReleaseDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 独立更新器（2026-11-16）：自己不被替换，才能在主进程退出后换文件
Source: "{#ReleaseDir}\HopOnUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\Microsoft.Web.WebView2.Core.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\Microsoft.Web.WebView2.WinForms.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\WebView2Loader.dll"; DestDir: "{app}"; Flags: ignoreversion
; 引擎整目录
Source: "{#ReleaseDir}\engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs
; 说明文件
Source: "{#ReleaseDir}\小白使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; 配置模板：只在目标不存在时放入，**绝不覆盖已有配置**
Source: "{#ReleaseDir}\engine\config.json"; DestDir: "{#UserDataDir}"; \
    DestName: "config.json"; Flags: onlyifdoesntexist uninsneveruninstall skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startup

[Run]
; 装完可选立即运行。2026-11-20 去掉 skipifsilent：静默装完也拉起——此前
; 静默装完的重启全押在 HopOnUpdater 桥接上，桥接被安全软件拦就没人拉起，
; 用户面对满屏「无法连接本地服务」（朋友机实证）。单实例互斥保证与桥接
; 重复拉起只活一个；runasoriginaluser 防提权安装器把应用带成管理员身份。
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; \
    Flags: nowait postinstall runasoriginaluser

[UninstallRun]
; 卸载前先停掉引擎/守护：调用 exe 的退出参数（若壳支持），否则靠 taskkill
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExeName}"; \
    Flags: runhidden skipifdoesntexist; RunOnceId: "KillShell"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM pythonw.exe"; \
    Flags: runhidden skipifdoesntexist; RunOnceId: "KillEngine"

[UninstallDelete]
; ⚠ 只清理程序目录里的运行时垃圾，**绝不列数据目录**。
;   {localappdata}\HopOn（数据）在卸载后必须完整保留。
Type: filesandordirs; Name: "{app}\engine\__pycache__"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
// ---------------------------------------------------------------------------
// 卸载完成后提示数据保留位置 —— 让用户知道凭证和历史还在，可以放心重装
// ---------------------------------------------------------------------------
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataPath := ExpandConstant('{#UserDataDir}');
    if DirExists(DataPath) then
      MsgBox('程序已卸载。' + #13#10 + #13#10 +
             '你的账号配置、历史记录仍保留在：' + #13#10 + DataPath + #13#10 + #13#10 +
             '如需彻底删除，请手动删除该文件夹（这会同时删掉登录凭证）。',
             mbInformation, MB_OK);
  end;
end;

// ---------------------------------------------------------------------------
// 安装前检查：WebView2 运行时是否可用
// Win10 1803+ 通常自带；缺失时提示用户装 Evergreen Runtime
// ---------------------------------------------------------------------------
function WebView2Installed(): Boolean;
var
  Value: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Value);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not WebView2Installed() then
      MsgBox('未检测到 WebView2 运行时，控制台窗口可能无法显示。' + #13#10 + #13#10 +
             '请安装 Microsoft Edge WebView2 Runtime 后重试：' + #13#10 +
             'https://developer.microsoft.com/microsoft-edge/webview2/',
             mbError, MB_OK);
  end;
end;

// ---------------------------------------------------------------------------
// 安装前清场（2026-11-19）：手动双击 Setup.exe 时 RestartManager 关不掉
// Job Object 内的壳，会弹「无法自动关闭所有应用程序」。对标 UniGetUI /
// MSIME-Windows 的做法，在 PrepareToInstall 里循环 taskkill 具名进程。
// ---------------------------------------------------------------------------

// 循环杀某进程：taskkill 杀到时返回 0、进程不存在时返回 128（非 0 即停）。
// 刻意不加 /T——MSIME 事故里 /T 顺着进程树把安装程序自己也杀了。
procedure TaskKillWait(FileName: String);
var
  ResultCode: Integer;
  Attempts: Integer;
begin
  Attempts := 0;
  repeat
    if not Exec('taskkill.exe', '/f /im "' + FileName + '"', '', SW_HIDE,
                ewWaitUntilTerminated, ResultCode) then
      Break;
    if ResultCode <> 0 then
      Break;
    Sleep(500);
    Attempts := Attempts + 1;
  until Attempts >= 10;
end;

procedure KillRunningApps;
begin
  // 只杀壳。引擎/daemon 在壳的 Job Object 里，壳死会被 KILL_ON_JOB_CLOSE
  // 连带（6B 已实证：停壳后 8765 释放）；WebView2 是壳的子进程，跟着走。
  TaskKillWait('{#AppExeName}');
  Sleep(1000);  // 等 OS 释放文件句柄再复制
end;

// PrepareToInstall 在「Preparing to Install」页、文件复制之前运行，
// 早于 Inno 的 RestartManager 检测——先清场，就不用弹框问用户了。
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningApps;
  Result := '';  // 空串 = 继续安装
end;
