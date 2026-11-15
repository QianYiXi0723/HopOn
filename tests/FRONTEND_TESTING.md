# HopOn 前端测试策略（2026-11-14 定稿）

> 依据：Kent C. Dodds 测试奖杯（[Write tests. Not too many. Mostly integration.](https://kentcdodds.com/blog/write-tests-not-too-many-mostly-integration)）、
> [Playwright Best Practices](https://playwright.dev/docs/best-practices)、
> [Yegor Bugayenko 单元测试反模式](https://www.yegor256.com/2018/12/11/unit-testing-anti-patterns.html)。
> 触发背景：2026-11-14 一天内实锤四起「脚本全绿、实机全灭」——假测试的代价清单见文末。

## 分层（奖杯模型，HopOn 版）

```
        ┌─────────────┐
        │ 人工真机验收  │  用户目检（唯一可信的交互/动效验证）
        ├─────────────┤
        │ serve 烟测   │  rl_test_smoke_e2e.py：真实子进程 + 真实 HTTP
        │ (集成层★)    │  serve 内容判据 = 实机 bug 转化的回归测试
        ├─────────────┤
        │ 镜像门禁     │  rl_test_mirror_sync.py：根 ↔ engine 一致性
        ├─────────────┤
        │ 逻辑/桩测试   │  tests/rl_test_*.py 42 个（密度 0.08-0.34）
        └─────────────┘
   静态层              node --check / ast.parse（随改动随手跑）
```

## 纪律（每条都有实锤案例背书）

1. **实机 bug → 回归判据**：每修一个实机 bug，把它的判据加进
   `rl_test_smoke_e2e.py` 的 `CRITICAL_MARKERS`（serve 内容必须包含该子串）。
   今天已入库 8 条：dlfixCell 双轨、入口选择器放宽、rl-flash、首渲染必闪、
   hm-scroll、手动行标识、F5 记忆脚本尾调用、APK 公网 URL。
2. **双镜像必须过门禁**：任何 `ui/ core/ *.py` 改动后，`rl_test_mirror_sync.py`
   必须全绿再提交——engine 副本与根不一致 = 实机跑旧代码（NameError 实锤）。
3. **合成事件只验证纯逻辑，不声称验证交互**：`dispatchEvent` 不经过真实
   输入管线（hover/裁剪 hit-test/过渡帧全部测不到）。真实交互的最终确认 =
   人工真机（用户目检），自动化不越权宣称。
4. **修改 `ui/` 后必须刷新页面再验证**：ui 热重载按 (mtime_ns,size) 指纹，
   但浏览器可能持旧 HTML——验证前 hard reload（Ctrl+F5）。
5. **测试跑在根 ≠ 产品跑在 engine**：任何「脚本全绿」声明必须注明
   「镜像门禁 ✓ + 烟测 ✓」才算数。

## 假测试的代价清单（2026-11-14 实锤存档）

| 案例 | 假测试形态 | 真实后果 |
|---|---|---|
| netutil 漏 import NameError | 脚本 import 根文件，实机跑 engine 副本 | 全游戏假失败「查询失败」 |
| 长按四层连环失效 | 合成 click 不经真实输入管线（hover/裁剪/过渡全测不到） | 功能上线即不可用 |
| 页面跑旧 JS | 只测源文件，serve 链路（缓存/指纹）零覆盖 | 用户端行为与开发端不一致 |
| 启动链 TDZ 炸掉 | 第 44 行早调用 + 顶层 let 时序——启动加载从未运行 | 全页「读取中」 |
| 托盘 headline 撑宽菜单 | 只测字段存在，没测「渲染宽度」这类物理约束 | 菜单宽条 |

## 运行

```
python tests/run_all_tests.py          # 全量
python tests/run_all_tests.py --fast   # 跳过真实进程/网络型慢测试
python tests/rl_test_smoke_e2e.py      # 单跑集成烟测
python tests/rl_test_mirror_sync.py    # 单跑镜像门禁
```
