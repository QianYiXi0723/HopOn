# 二游扩展调研：崩坏3/战双/异环/渠道评估（2026-11-09）

> 结论先行：**崩坏3、战双帕弥什已接入**（fa51b1b）；**异环暂缓**（只读数据接口无实证）；
> 小黑盒/官网渠道评估后不采用。HopOn 现支持 7 款：原神/星铁/绝区零/崩坏3（米游社）+
> 明日方舟/终末地（森空岛）+ 鸣潮/战双（库街区，一张 token）。

## 已接入

### 战双帕弥什（库街区，gameId=2）
- 与鸣潮同一张 token；widget `gamer/widget/game2/refresh` 与鸣潮逐字段同构
  （血清 actionData/每日活跃 activeData/委托 dormData，cur/total/refreshTimeStamp）
- 活动：`api.kurobbs.com/wiki/core/homepage/getPage`（POST + `Wiki_type: 2`）→
  sideModules「热门活动」，`countDown.dateRange` 直出（game-schedule 同款端点）
- 绑定：库街区绑定卡一次覆盖两款；战双开关不自动打开，监控抽屉自行勾选

### 崩坏3（米游社，game_record）
- 端点 `game_record/app/bh3/api/note`（社区文档口径：current_stamina/
  max_stamina/stamina_recover_time，与原神 dailyNote 同名）
- ⚠️ **真机字段待联调**：崩3 的 game_record 在 `api-takumi.mihoyo.com`
  （无 -record 子域，实测 -record 404），且该域被 WAF 503 拦 Python 流量
  （与 UA/设备头无关）。用户有 lv80 崩3角色（角色列表已实测验通，
  uid 参数须用 cookie 的 `account_id` 而非 ltuid）；联调时抓真包核对
  note 响应字段 + 可能需要的 x-rpc 头，适配点收拢在
  `GAME_RECORD_BASES`/`ENDPOINTS`/`parse_bh3` 三处
- 绑定：米游社扫码勾选崩坏3；存量配置缺行由 write_mihoyo 自动补建

## 异环（NTE，塔吉多）：暂缓

- 塔吉多平台存在且签到生态成熟（NTE-Auto-Sign 34★ / taygedo-auto-attendance 27★）：
  登录（短信/refreshToken）+ 社区签到 + 游戏签到 + 云异环时长（user.laohu.com）
- **全生态零只读数据接口**：enumerated 全部端点只有
  `bbs-api.tajiduo.com`（usercenter login/refreshToken/getGameRoles +
  apihub/awapi sign 系列）与 `user.laohu.com`（云时长）——**无体力/日常查询**
- 结论：要做只能做「签到结果查询」级别（且签到是写操作，与只读定位冲突）。
  等塔吉多开放 game data 接口（如库街区 widget 那样）再评估

## 渠道评估

### 小黑盒：不采用
- 接口需 pkey/hkey 签名（非公开逆向），社区生态是**社区内容**（评分/短评/
  游戏库，xiaoheihe-cli），**无游戏体力查询生态**
- 2024 年小黑盒祈愿分析被曝要求用户输入米哈游账号密码——安全前科
- 与其走小黑盒逆向，不如库街区/米游社官方 token 路线（已在用，更稳更正当）

### 官网渠道：仅活动可用，体力/日常不存在
- 各官网公告 JSON 是活动数据的好来源（尘白禁区 cbjq-content.xoyocdn.com
  官方公告配置，game-cal 在用）——**只覆盖活动，无体力/日常**
- HopOn 的活动源已用官方一手数据（米游社 act_calendar/库洛 notice.json/
  wiki 侧栏），官网渠道无增量；留作某游戏活动源失效时的备胎

## 第二梯队现状备忘

| 游戏 | 社区平台 | 体力/日常只读 | 备注 |
|---|---|---|---|
| 尘白禁区 | 无（官网签到） | ❌ | 活动源可从官网公告接 |
| 重返未来:1999 | 无 | ❌ | 小程序生态 |
| 少前2追放 / 白荆回廊 / 碧蓝航线 | 无公开接口 | ❌ | 模拟器脚本生态，不同物种 |
| 蔚蓝档案 | 无 | ❌ | BAAH/BAAS 全是模拟器自动化；arona.icu 非官方聚合 |
| 未定事件簿 | 米游社（同族） | 待核 | 体力体系存疑，米游社 game_record 支持待验证 |
