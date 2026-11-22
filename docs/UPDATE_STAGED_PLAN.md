# staged update 施工方案（完全模仿标杆项目）

日期：2026-11-22 · 状态：**待用户批准，批准前不动工**

## 0. 先修正一个说法

上一轮我说「我们的更新=杀掉自己→跑安装器」——**已过时**。11-16 起代码就不跑
Setup.exe 了（login_ui.py:4188-4193 有尸检注释：Inno 的 RestartManager 关不掉
Job Object 里的引擎，rc=5 回滚，故改用独立 HopOnUpdater.exe）。

真实差距比这更细，**apply 阶段已经是标杆式的，缺的是两处**：

| 缺口 | 现状（证据） | 标杆做法 |
|---|---|---|
| ① 解压发生在程序**死后** | 点「立即安装」→引擎自杀→Updater 等 PID 消失→**才**解压 zip→备份→覆盖→拉起（HopOnUpdater.cs Run() 131-347）。黑幕=死后再解压的空窗 | Velopack/Firefox/VS Code：下载校验完就**在运行期把新版本准备好**（staged），退出后只剩纯覆盖 |
| ② 壳不感知「更新退出」 | 引擎 os._exit(0) 后，壳的 Engine_Exited（MainForm.cs:577-608）把**旧引擎拉回来**，Updater 再 KillProcessesUnder 强杀全家——脏竞态，靠兜底而不是设计 | Firefox：重启时检测 staged 更新继续 apply；运行期绝不复活旧版本 |

十几个症状轮次（黑幕、失联、重连横幅、守护）治的都是这两个缺口的下游。

## 1. 标杆学习表（吸收什么、出处）

| 标杆 | 关键机制 | 我们吸收 |
|---|---|---|
| **Velopack**（Squirrel 后继） | 下载与 apply 解耦；Update.exe `apply --waitPid <PID>` 等主程序退出再应用；staged 先于 restart | ① prepare 前移；等退出协议（已有等价物 WaitPidGone，保留） |
| **Firefox**（官方源码文档 Update Implementation） | staged-update 目录运行期写好；**重启时继续应用中断的更新**；backup 目录失败回滚 | ① prepare 前移；③ 壳启动续 apply；backup 回滚（已有，保留） |
| **VS Code**（updateService.win32.ts） | 后台下载+staging；「Restart to Update」语义——安装永远是一次明确的用户动作 | ④ UI 语义：staged 态明示「安装将关闭应用、几秒回来」 |
| **Chrome**（Omaha） | 版本目录+指针翻转，运行中程序永不被碰 | 本方案原地覆盖已满足「运行中不碰」；版本目录制迁移成本大、不采用（见 §6） |

## 2. 目标态（用户旅程对比）

- 现状：点「立即安装」→ 应用消失 →（等退出+解压+备份+覆盖 ≈ 十几秒黑幕）→ 回来；
  期间壳复活旧引擎被强杀（竞态杂音进日志）。
- 目标：**检查更新下载完成的那一刻，新版本已解压就绪**（运行期无感知完成）→
  点「立即安装」→ 应用消失 →（纯覆盖，秒级）→ 自动回来；竞态杂音归零；
  任何一步失败自动回滚旧版（已有 Rollback，保留）。

## 3. 现有骨架盘点（改动面约 30%，70% 直接复用）

**不动**：core/update.py 下载链全段（双通道 .part 流式、_verify_sums、
download_state.json 落盘恢复、磁盘闸、防降级）；HopOnUpdater 的
备份/Rollback/CopyWithRetry/KillProcessesUnder/CheckRootSafety/Job 脱离/
退出码约定；zip 顶层单目录+ResolveStagingRoot；HOPON_INSTALL_DIR 钩子。

**改**：见 §4 批次。

## 4. 施工批次

### 批次 1 — prepare 前移（core/update.py）

- `_finish_job`（update.py:498-513）SHA 通过后**就地新增解压阶段**：
  `stage="staging"`（message「解压准备更新…」）→ 先清旧 staging（防版本
  残留错配）→ zipfile 解压到 `<app_root>\.update\staging` → 关键文件清单
  校验（HopOn.exe / engine\app.py / engine\daemon.py，与 Updater 同清单）
  → `stage="staged"` 落盘（新增字段 staged_at / staging 路径 / 文件计数）。
- zipfile 的 CRC 校验天然覆盖包完整性；整包 SHA 已在下载期验过——校验链
  = 整包 SHA（下载期）+ zip CRC（解压期）+ 关键文件存在（staging 期）+
  Updater 落盘校验（apply 期，3→5 文件，批次 5）。
- 解压失败 → stage=error + 清 staging（不比现状差）。
- download_state.json 恢复条件 `_load_download_state`（:558）从
  `stage=="done"` 扩为 `done|staged`；`done` 语义保留（包校验完成，
  Updater 自己解压的旧路径继续可用）——**协议向后兼容**。

### 批次 2 — pending 协议（引擎写、壳/Updater 读）

- 解压成功写 `<app_root>\.update\pending.json`：
  `{version, asset, pkg, staged_at, src_pid, files}`——「有更新待应用」的
  唯一事实源，壳与 Updater 都认它。
- install 端点（login_ui.py:4164-4263）：`staged` 态 spawn Updater 加
  `--staged` 参数（跳过解压直接 apply）；`done` 态（无 staging，如老版本
  落盘恢复）维持现状参数（Updater 自己解压）。`staging` 态（解压进行中）
  返回 409「正在准备，请稍候」。
- Updater（shell/HopOnUpdater.cs）：新增 `--staged` 开关——staging 完整
  （HopOn.exe 在场）直接进 BuildPlan；**不完整自动回退从 zip 解压**（永不
  比现状差）。apply 成功清 pending.json + staging + backup + zip（磁盘归还）。

### 批次 3 — 壳感知（shell/MainForm.cs，消竞态）

- `Engine_Exited`（:577）在 `_reallyExit` 检查后加：pending.json 存在 →
  Log「检测到更新待应用，不重启引擎（更新器接管）」→ return。**一刀消掉
  「壳复活旧引擎→Updater 强杀全家」竞态**。
- 壳启动（StartEngine 前）：pending.json 存在 → staging 完整则说明上次
  apply 中断（覆盖期崩溃），直接 spawn `HopOnUpdater --staged` 完成收尾
  （Firefox 启动续 apply 同款）；staging 不完整则清 pending 正常启动。

### 批次 4 — UI 语义（ui/app.js，对齐 VS Code）

- updDlPoll 渲染 `staged` 态：「已就绪——立即安装将关闭应用，几秒后自动回来」。
- staged 态点「立即安装」直接进安装（跳过下载轮询）。
- 清残留文案：弹窗「安装包（Setup.exe）」（app.js:847 附近）等统一 zip 语义。

### 批次 5 — 残留清理 + 落盘校验强化

- update.py:396 资产白名单去 `.exe`（zip-only）。
- Updater 落盘校验 3→5 文件（+engine\core\update.py、engine\login_ui.py）。
- 保留名单（HopOnUpdater.cs:40-49）显式注释安装根写入例外
  （logs\webview2 壳数据在安装根；BuildPlan 只覆盖 staging 内文件，
  天然安全——注释+结构钉固化，防未来改动踩坑）。

## 5. 测试矩阵

1. `tests/rl_test_update_staged.py`（新）：staged 态状态机、pending.json
   字段、恢复条件 done|staged、白名单 zip-only、解压失败清 staging。
2. 结构钉：Updater `--staged` 开关与回退解压路径；壳 Engine_Exited pending
   前置、启动续 apply。
3. 沙箱 E2E：装旧版→检查更新→下载→**断言引擎活着时 staging 已就绪**
   （prepare 前移生效的铁证）→点安装→版本到位→pending/staging/zip 清理
   干净→config/history 数据完好。
4. 断点恢复：staged 后杀全家→重启→壳续 apply 成功。
5. 竞态专项：引擎自杀后 2s 内壳未拉引擎（pending 在场）。

## 6. 风险与取舍

| 风险 | 对策 |
|---|---|
| zip+staging 双份 ≈100MB 磁盘 | 已有 300MB 空间闸；apply 成功清 zip+staging |
| 引擎解压期用户点安装 | install 端点 staging 态 409 |
| staging 跨版本残留 | 引擎解压前先清 staging |
| 旧版 Updater 不识 pending | 无害（自己解压），协议向后兼容 |
| Chrome 式版本目录+指针 | **不采用**：迁移要全用户重装一次、Inno/卸载/快捷方式全适配，收益（秒级回滚）对单机小工具不划算；原地覆盖+backup 回滚已达目标态 |
| 增量下载（delta） | 不采用：包仅 30-60MB、双通道已够快；delta 引入逐文件补丁失败模式，复杂度不成比例 |

## 7. 到达路径

本方案版本发布后，.13 及更早用户**最后手动装一次**（Setup.exe 双击，自带
taskkill 清场连退出都不用）→ 之后的每次更新都走 staged：下载完成即就绪、
安装=秒级覆盖+自动回滚。朋友机 .10 同路径。

## 8. 版本号

架构级改动，建议 **v2.7.0**——按红线由你定夺。
