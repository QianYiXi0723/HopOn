# δ4 一键安装真机验证 · 执行单（待用户放行）

## 前置（已实测确认）
| 项 | 值 |
|---|---|
| 工作树 | `c87f9af`，干净 |
| `%LOCALAPPDATA%\Programs\HopOn` | **不存在** → 本次是「首次安装」 |
| 注册表 `HKCU\...\Uninstall\*8F3A2C41*` | **0 条** |
| 运行实例 | 2.6.7，PID 89176，`E:\Apps\ResinLoop\engine\app.py` |
| 用户数据基线 | md5 `8B10D1145E244655CF5A736056F3DE2F` / data 19 / logs 46 / dpapi 10 |

## 执行步骤（路径 3：直接 POST install，不碰版本号、不造虚假更新）

### S0 造下载态（用真实 2.6.7 包，不下载）
1. 复制 `dist\HopOn_v2.6.7_Setup.exe` → `%LOCALAPPDATA%\HopOn\data\downloads\`
2. 写 `download_state.json`：`{stage:done, sha_ok:true, version:2.6.7, file:<上面的包>}`
3. **临时**把 `core/__init__.py` 版本改为 `2.6.6`（让防降级放行）+ `_dev_sync_engine.py` + 重启实例
   → 验证完第 1 步立刻改回 `2.6.7` 并重新同步

### S1 首次安装（矩阵 1）
```bash
curl -X POST http://127.0.0.1:8765/api/update/install
```
预期链路：200 → 引擎 `_self_terminate`（停 daemon → 启桥接 → os._exit）
→ 桥接 sleep 2s → ISCC 装到 `%LOCALAPPDATA%\Programs\HopOn` → 校验 `HopOn.exe` → 拉起壳

判据（`python %TEMP%\rl_d4_check.py`）：
- [ ] `Programs\HopOn` 存在，且 `HopOn.exe` md5 **不等于**工作树那份
- [ ] `Programs` 下只有一个 hopon 目录（**无影子目录**）
- [ ] 注册表出现 `*8F3A2C41*_is1`，`DisplayVersion` = 2.6.7
- [ ] 进程路径指向 `...\Programs\HopOn\engine\python\pythonw.exe`，**不是** `E:\Apps\ResinLoop\engine\`
- [ ] `/api/env` version = 2.6.7
- [ ] `%LOCALAPPDATA%\HopOn\logs\install_bridge.log` 有「落盘校验通过」「拉起壳」
- [ ] 用户数据四项与基线一致

### S2 同版本覆盖安装（矩阵 2，用户建议补的关键项）
再次 POST install（此时当前版本已是 2.6.7，需移除防降级临时改动，或直接调桥接）
- [ ] 覆盖成功，`config.json` md5 **不变**
- [ ] 目标目录仍在原处，无影子目录
- [ ] `install.log` 无「先卸后装」回滚痕迹

### S3 中断恢复（矩阵 4）
安装中途 taskkill 桥接进程 → 重启工作树实例
- [ ] 软件仍可用（`/api/env` 正常、`/api/accounts` 8/8）
- [ ] 数据四项不变
- [ ] 半装状态不阻止后续重装

### S4 卸载（矩阵 5）
`unins000.exe /VERYSILENT /SUPPRESSMSGBOXES`
- [ ] 数据四项 = 基线（md5 / data 19 / logs / dpapi 10）
- [ ] 注册表项消失
- [ ] 无残留进程、8765 释放
- [ ] 程序目录残留仅运行期生成物（pyc/webview2 缓存），无 .exe

### S5 收尾
- [ ] 恢复 `core/__init__.py` = 2.6.7 + `_dev_sync_engine.py`
- [ ] 删除临时注册的 2.6.7 包与 download_state
- [ ] 重启工作树实例，`/api/env` = 2.6.7
- [ ] `git status` 干净

## 需要用户放行三件事
1. **选路径 3**（直接 POST，不碰版本号）—— 确认？
2. **现在方便装吗**（软件会关十几秒）？
3. **装完立刻卸载，还是留着用一天**？
