# 库街区（鸣潮）接入调研 2026-11-09

> 分支 `feat/wuwa-kurobbs`。情报源：[TomyJan/Kuro-API-Collection](https://github.com/TomyJan/Kuro-API-Collection)
> （82★，2025-10 还在更新）、[Wuthery/kuro.py](https://github.com/Wuthery/kuro.py)（Python 异步封装）、
> Yunzai-Kuro-Plugin 等。端点均为社区抓包实证，非猜测。

## 结论

**可行，且比预估便宜。** 库街区没有 DS 签名、没有设备指纹校验、只读查询无风控报告——
比米游社简单一个量级。一个接口拿全体力+日常+周常，工作量按 **2~3 天**估。

## 核心端点：小组件数据（一次拿全）

`POST https://api.kurobbs.com/gamer/widget/game3/refresh`
（`/getData` 同参数同结构，refresh 返回更准；`gameId=3` 固定为鸣潮，战双=2）

**请求体**（form-urlencoded，均可不传，不带 roleId/serverId 返回默认角色）：
`roleId`、`serverId`（国服固定 `76402e5b20be2c39f095a152090afddc`）、`type=2`、`sizeType=1`

**响应**（`code=200` 成功，`code=220` token 失效，`success=false` 且 220 = 签到类风控）：

| 字段 | 内容 | 对 HopOn 的意义 |
|---|---|---|
| `energyData` | 结晶波片 `cur/total`（146/240）+ `refreshTimeStamp` 回满时间戳 | 体力三态 + ETA 预测，同米游社 recovery_time 链路 |
| `storeEnergyData` | 结晶单质 `cur/total`（345/480） | **溢出存储**＝星铁「备用开拓力」同款，道具破上限三态显示有先例 |
| `livenessData` | 每日活跃 `cur/total`（100/100） | **日常完成度**＝鸣潮没有委托清单，活跃度即官方每日度量 |
| `weeklyData` | 战歌重奏 `0/3`（周刷新） | 周常 TaskItem |
| `hasSignIn` | 库街区月签状态 bool + `signInUrl` | 只读展示（不代签） |
| `towerData` / `slashTowerData` / `weeklyRougeData` | 深塔 / 冥歌海墟 / 千道门扉 cur/total | 扩展 TaskItem（v2） |
| `roleId` / `roleName` / `serverName` | 角色 + 区服 | 账号标识 |

⚠️ 库洛服务器时间戳偶发不准——相对时间一律按 `data.serverTime` 推算，勿信本机时钟直减。

## 鉴权：比米游社简单一个量级

**请求头**（APP 端风格）：`token`（JWT）+ `devCode`（**必须但不校验**，随便填）+
`source: android` + `distinct_id`（任意 UUID）+ `User-Agent: okhttp/3.11.0` +
`version/versionCode/model/lang/countryCode`（静态值即可）。

- 无签名算法、无私有参数、无设备指纹验证
- 只读查询无风控报告；风控只出现在**签到类 POST**（HopOn 不做）
- 响应 `code 220` = token 失效 → 接 `cred_health` 凭证告警链

## 凭证获取：三条路

1. **token 粘贴**（v1 推荐）：用户从库街区 Web 端登录后取 token（社区标准做法，
   各签到工具均如此）。绑定卡做粘贴框 + `mineV2` 校验回显昵称。
2. **短信验证码登录**：`POST /user/sdkLogin`（mobile+code，`/user/getSmsCode` 发码）。
   ⚠️ **APP 端单点登录**——拿新 token 会踢掉用户手机 App 的登录态！做的话必须明示。
   返回带 `refreshToken` 但 refresh 端点社区未确认。
3. Web 端 H5 登录（`sdkLoginForH5`）：web token 与 app token 域可能隔离，
   widget 接口是否收 web token **需实测**（第一优先验证项）。

token 实证寿命：数月级（文档示例 2023-07 的 token 2025 年仍在用），过期接 cred_health。

## 对 HopOn 架构的落点（全部已有先例）

- `core/kuro_api.py`（新，~300 行）：静态头 + widget/refresh + role/list + mineV2
- `core/querier.py`：`KuroQuerier(BaseQuerier)` + `parse_wuwa`；`register_querier("kuro","wuwa",...)`
- 日常判定：活跃度 100/100 → TaskItem「每日活跃」；04:00 日界与鸣潮日常刷新一致（须实测确认）
- `core/config.py`：GAME_NAME/RESOURCE_THRESHOLD（结晶波片 240）/RESOURCE_UNIT/阈值字段
- `core/history.py`：GAME_ORDER 加 `wuwa`
- 绑定：账号页新增「库街区」卡（token 粘贴 + 校验），DPAPI 加密复用 `secure_store`
- UI 七处机械活清单：`ui/app.js` 游戏列表/阈值输入/图标/游戏色 + `index.html` 绑定卡与阈值框
- 活动日历：库街区活动组 API 已过期；kuro.py 有公告组件可后续接——v1 先标「暂不支持」（同终末地路径）

## 风险与未决项

| 项 | 状态 | 对策 |
|---|---|---|
| web token 能否调 widget 接口 | **需实测** | 不行就走 APP 抓包 token 指引 / 短信登录（带踢下线提示） |
| 日常 04:00 刷新是否与 game_moment 一致 | 需实测 | 不一致则 wuwa 单独配日界小时 |
| token 过期无自动刷新 | 社区同款限制 | cred_health 告警 + 重新粘贴 |
| 结晶单质 total=480 的三态语义 | 复用星铁备用文案 | 「+ 结晶单质 N」措辞注意 `_parse_progress` 后缀兼容（v2.4.7 教训） |
