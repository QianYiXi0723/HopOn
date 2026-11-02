# ResinLoop → HopOn 改名迁移清单

> 状态：方案已定（2026-10-31），**执行等朋友测试期结束后**再做——现在刚清完技术债，不开大手术。
> 决策：总名 **HopOn**（「该上号了」= 产品的灵魂动作）；**SupplyDrop** 留作推送栏目彩蛋名（如周报栏目「本周空投」），不作总名。
> 本清单是全库 grep 实证。**v2 复查补入首批遗漏 6 文件与 resinloop.local 域名项；v3 复查（无大小写/目录盲区全量扫）补入 tools/rl_release 发布工具族与 promo 宣传物料族，并勘误本机注册表现状**；行号会漂、落点文件名是准的。

## L1 用户可见层（改名时必改，纯文案零风险）

| 文件 | 位置 | 内容 |
|---|---|---|
| login_ui.py | :855 | 测试推送标题 `ResinLoop · 测试推送` |
| daemon.py | :520 | 周报标题 `📊 ResinLoop 周报` |
| daemon.py | :808 | 托盘名 `ResinLoop · 日常巡查`（pystray Icon name="resinloop" 一并改） |
| login_ui.py | :2689/:2721 | CLI 描述 / 启动横幅 |
| app.py | :44 / :242 | pywebview 窗口标题 `ResinLoop · 日常巡查` + CLI 描述（v2 补） |
| shell/engine_app.py | :3/:5/:7（v3 补） | 引擎入口模板 docstring「ResinLoop 引擎入口…由 C# 壳（shell/ResinLoop.exe）拉起」 |
| core/__init__.py | :15 | docstring 引用 csproj 文件名（v2 补） |
| tests/rl_test_api.py | :98 | T1.1 断言「页面 200 且含 ResinLoop」——改名必改断言（v2 补） |
| _build_release.py | ZIP_NAME | `ResinLoop_v{VERSION}_Windows_x64` → `HopOn_v…`（OUT 目录名跟随） |
| core/tools_server.py | :64 | `ResinLoopTools/1.0` → `HopOnTools/1.0` |
| ui/index.html | title/品牌区/欢迎语/推送预览 app 名 | `ResinLoop` 字样 ×4 |
| README.md | 8 处 | 标题/正文/**小写仓库 URL**（:29，v2 补）/下载链接 |

## L2 系统身份层（改名即升级兼容问题，见 §迁移）

| 项 | 现值 | 影响 |
|---|---|---|
| shell/ResinLoop.csproj（**文件名本身**也要改） | AssemblyName/exe 名 `ResinLoop.exe`（4 处） | 产物名变 HopOn.exe → autostart 探测路径（core/autostart.py:77 找 `ResinLoop.exe`）、dev.bat（10 处）、_dev_sync_engine.py（4 处）全要跟 |
| **shell/MainForm.cs:36 `UiHost = "resinloop.local"`（v2 补，最关键）** | 壳的 WebView2 拦截器挂在 `https://resinloop.local/*` 假域名上应答页面与全部 API | **功能性改名**：连 :181/:382 两处注释与拦截逻辑一起动；前端已核实无域名硬编码（全部相对路径），只需改壳侧；改完必须过 build.ps1 首启门禁实测 |
| shell/build.ps1（v2 补，7 处） | 产物名 `ResinLoop.exe`、**首启门禁窗口标题 `-match "ResinLoop"`**、门禁目录 | **发版闸门**：exe 改名后标题匹配不同步 = 门禁永远失败、发版直接卡死 |
| shell/app.manifest :3（v2 补） | `<assemblyIdentity name="ResinLoop.app" />` | Windows 程序集身份 |
| core/autostart.py :43 | 注册表 HKCU Run 键名 `VALUE_NAME = "ResinLoop"` | **老机器升级时旧键残留 = 双自启**，见 §迁移 |
| shell/Program.cs :18-19 | 单实例互斥锁 `ResinLoop.Shell.Mutex` | 升级瞬间新旧 exe 并存时互斥失效（先退旧再装新即可，风险低） |
| .gitignore :99（v2 补） | `/ResinLoop.exe` 忽略规则 | 增加 `/HopOn.exe` 一行 |
| **tools/rl_release/ 发布工具族（v3 补，~10 文件，v2 完全漏掉）** | `gh_release_v2.py`/`gh_release_v21.py` 硬编码 `OWNER, REPO = "QianYiXi0723", "ResinLoop"` + zip 路径 + tag/正文文案；`build_shell.py`（dotnet publish shell/ResinLoop.csproj）、`repack_shell.py`（ILRepack 产出根目录 ResinLoop.exe）、`smoke/verify_release.py` 校验链；`rl_ring_fixture/` 历史审计工件 | **发版流水线**：仓库名/exe 名改后不同步 = 上传指错仓库、构建产出错名。gh_release 脚本与 §L2 的 csproj/产物名联动改 |
| **promo/ 宣传物料（v3 补，在途 WIP）** | `promo.html`（标题/「GitHub 搜索 ResinLoop」CTA）、`render.py` 渲染管线、成片 `ResinLoop_promo_45s_1080p.mp4` 文件名、`promo/demo/` 完整应用演示快照 | 改名后宣传物料需重渲染/重命名；demo 快照随源码同步。在途未跟踪，执行改名前先与 promo 会话对齐 |
| GitHub 仓库名 QianYiXi0723/ResinLoop | 仓库名 + Release 资产链接 | GitHub rename 后旧 URL 自动重定向，README 下载链接仍建议更新；**严禁删旧 Release**（v2.0.0 红线沿用） |

## L3 内部标识（建议**不动**，改了纯亏）

- logger 前缀 `dailycheck.*`（纯内部，改了要动所有 grep 习惯）
- localStorage 键 `rl-theme` / `rl-cache-v1` / `rl-welcome-dismissed`（改名会丢用户主题与缓存；`rl-` 前缀不含项目名、无冲突）
- %TEMP% 工具临时目录 `rl_*` / `rl-*` 前缀
- 引擎目录 `engine/`（不含项目名）

## §迁移（朋友/已装机升级路径）

1. L1+L2 全部改完后，`autostart.py` 的 `build_command()` 增加**旧键清理**：写 `HopOn` 键的同时 `reg delete` 旧 `ResinLoop` 键（幂等，不存在则跳过）——这是唯一必须写代码的兼容动作；
   **v3 现场勘误**：本机 HKCU Run 实测**没有** ResinLoop 自启键（只有百度网盘/Apifox），迁移负担主要在**挂了自启的朋友机器**上，本机零负担；
2. 升级说明（小白使用说明.txt）加一行：老用户解压新包到**同一目录**覆盖，先托盘退出旧版；
3. exe 文件名变更（ResinLoop.exe → HopOn.exe）：旧机器上旧 exe 文件仍在——说明里注明「旧的 ResinLoop.exe 可删除」，autostart 探测两个名字都认（HopOn 优先，回退 ResinLoop，过渡一个版本后移除回退）。

## 执行顺序（到时候照抄）

1. L1 文案层一次 commit（低风险）；
2. L2 身份层一次 commit：csproj 改名（含文件名）+ **UiHost 域名（MainForm.cs）** + **build.ps1 产物名与门禁标题匹配（v2 补：改漏 = 发版闸门卡死）** + **tools/rl_release 发布工具族（v3 补：gh_release 的 OWNER/REPO 与产物路径）** + app.manifest + autostart 迁移代码 + dev.bat/_dev_sync/_build_release 跟随；
3. `shell/build.ps1` 本地构建 + 首启门禁**实测通过**（改名后第一道证明）；
4. GitHub 仓库 rename（网页操作，旧链接自动重定向）+ README 链接更新 + `tools/rl_release/gh_release_*.py` 的 OWNER/REPO 同步核对；
5. 打 `HopOn_v{VERSION}_Windows_x64.zip`，走一遍朋友机器「覆盖升级」实测（重点验自启与旧键清理）；
6. promo/ 宣传物料重渲染（与 promo 会话对齐在途工作）；
7. docs/observe-device-identity.md 等旧名引用顺手改。

## 验收清单

- [ ] 全库 `grep -ri resinloop`（排除 engine/__pycache__/.bak）仅剩：GitHub 历史 URL、迁移兼容代码里的旧键名
- [ ] 新装机器：三关测试（装得上/登得进/收得到）全过
- [ ] 老机器覆盖升级：旧自启键被清理、无双 daemon、配置与历史完好
- [ ] 托盘/窗口/推送/周报/zip 名全部显示 HopOn
