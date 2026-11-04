# -*- coding: utf-8 -*-
"""去边框化批量替换：所有 old 必须精确命中 1 次，否则报错不落盘"""
import io, sys

P = r"E:\Apps\HopOn\login_ui.py"
src = io.open(P, encoding="utf-8").read()
orig = src

R = []  # (old, new)

# ---------- 任务1+2：Token 重构（深色 BetterGI / 浅色 AUTO-MAS） ----------
R.append((
"""    --bg-base:#0d0d0d; --bg-sub:#151515; --bg-card:#1a1a1a; --bg-elevated:#222;
    --bg-raise:#202020; --bg-head:#191919;""",
"""    --bg-base:#1a1a1a; --bg-sub:#141414; --bg-card:#252526; --bg-elevated:#252526;
    --bg-raise:#2d2d2d; --bg-head:#2b2b2b; --bg-side:#161616; --bg-input:#111111;
    --btn-bg:#333333; --btn-hover:#3d3d3d; --nav-on:#333333; --nav-on-fg:#ffffff;
    --focus-ring:rgba(74,155,232,.45);
    --card-shadow:inset 0 1px 0 rgba(255,255,255,0.04), 0 2px 8px rgba(0,0,0,0.35);"""))

R.append((
"""    --bg-base:#f3f3f3; --bg-sub:#ebebee; --bg-card:#ffffff; --bg-elevated:#ffffff;
    --bg-raise:#ffffff; --bg-head:#f6f6f8;""",
"""    --bg-base:#f4f6f9; --bg-sub:#eef0f4; --bg-card:#ffffff; --bg-elevated:#ffffff;
    --bg-raise:#ffffff; --bg-head:#f7f8fa; --bg-side:#ffffff; --bg-input:#f5f5f5;
    --btn-bg:#eef0f4; --btn-hover:#e2e6ec; --nav-on:#e8f0fe; --nav-on-fg:var(--brand);
    --focus-ring:rgba(11,103,194,.30);
    --card-shadow:0 2px 8px rgba(0,0,0,0.04);"""))

# ---------- 侧边栏：与背景同色阶，去右边线，选中=色块无蓝线 ----------
R.append((
"""  .side{width:200px;flex:none;background:var(--bg);border-right:1px solid var(--line);
        display:flex;flex-direction:column;padding:10px 8px}""",
"""  .side{width:200px;flex:none;background:var(--bg-side);
        display:flex;flex-direction:column;padding:10px 8px}"""))

R.append((
"""  .nav button.on{background:var(--sel);font-weight:600}
  .nav button.on::before{content:"";position:absolute;left:0;top:6px;bottom:6px;width:3px;
        background:var(--brand);border-radius:0 2px 2px 0}""",
"""  .nav button.on{background:var(--nav-on);color:var(--nav-on-fg);font-weight:600}
  .nav button.on .nav-ico{color:var(--nav-on-fg)}"""))

R.append((
"""    .nav button.on::before{display:none}
""", ""))

R.append((
"""  .side-foot{margin-top:auto;padding:10px 8px 4px;border-top:1px solid var(--line);
        font-size:11px;color:var(--muted)}""",
"""  .side-foot{margin-top:auto;padding:10px 8px 4px;
        font-size:11px;color:var(--muted)}"""))

R.append((
"""    .side{width:100%;flex-direction:row;align-items:center;border-right:none;
          border-bottom:1px solid var(--line)}""",
"""    .side{width:100%;flex-direction:row;align-items:center}"""))

# ---------- 卡片：去边框，靠色阶 + 阴影浮起 ----------
R.append((
"""  .card{background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:8px;
        box-shadow:inset 0 1px 0 rgba(255,255,255,0.09), inset 0 -1px 0 rgba(0,0,0,0.3), 0 1px 2px rgba(0,0,0,0.6);
        transition:box-shadow 400ms ease-out, padding 400ms ease-out}""",
"""  .card{background:var(--bg-card);border:none;border-radius:10px;
        box-shadow:var(--card-shadow);
        transition:box-shadow 400ms ease-out, padding 400ms ease-out}"""))

R.append((
"""  .card-head{display:flex;align-items:center;gap:8px;min-height:50px;
              padding:12px 22px;border-bottom:1px solid var(--line)}""",
"""  .card-head{display:flex;align-items:center;gap:8px;min-height:50px;
              padding:12px 22px;background:var(--bg-head);
              border-radius:10px 10px 0 0}"""))

R.append((
"""  .card-tip{padding:8px 16px;font-size:11.5px;color:var(--muted);line-height:1.6;
             border-bottom:1px solid var(--line);background:var(--subtle)}""",
"""  .card-tip{padding:8px 16px;font-size:11.5px;color:var(--muted);line-height:1.6;
             background:var(--subtle)}"""))

R.append((
"""  .v-sep{border-top:1px solid var(--line);margin:10px 0 8px}""",
"""  .v-sep{margin:12px 0 6px}"""))

# ---------- 表格：去行线，靠斑马纹 + 留白 ----------
R.append((
"""  .tbl th,.tbl td{padding:9px 12px;border-bottom:1px solid var(--line);
                  text-align:left;white-space:nowrap;vertical-align:middle;line-height:1.5}""",
"""  .tbl th,.tbl td{padding:10px 12px;
                  text-align:left;white-space:nowrap;vertical-align:middle;line-height:1.5}"""))

R.append((
"""  .tbl tbody tr:last-child td{border-bottom:none}
""", ""))

# ---------- 按钮：无边框，色阶底 ----------
R.append((
"""  .btn{display:inline-flex;align-items:center;justify-content:center;height:40px;
       padding:0 18px;font-size:13.5px;line-height:1;border-radius:4px;
       border:1px solid var(--line);background:var(--surface);
       color:var(--fg);cursor:pointer;white-space:nowrap;font-weight:500}
  .btn:hover{background:var(--subtle)}
  .btn.primary{background:var(--brand);border-color:var(--brand);color:#fff}""",
"""  .btn{display:inline-flex;align-items:center;justify-content:center;height:40px;
       padding:0 18px;font-size:13.5px;line-height:1;border-radius:6px;
       border:none;background:var(--btn-bg);
       color:var(--fg);cursor:pointer;white-space:nowrap;font-weight:500}
  .btn:hover{background:var(--btn-hover)}
  .btn.primary{background:var(--brand);color:#fff}"""))

R.append((
"""  .mini{padding:3px 10px;font-size:11.5px;border-radius:4px;border:1px solid var(--line);
        background:var(--surface);color:var(--fg);cursor:pointer;white-space:nowrap}
  .mini:hover{background:var(--subtle)}
  .mini:disabled{background:var(--line);color:var(--muted);border-color:transparent;
        opacity:1;cursor:not-allowed}""",
"""  .mini{padding:3px 10px;font-size:11.5px;border-radius:4px;border:none;
        background:var(--btn-bg);color:var(--fg);cursor:pointer;white-space:nowrap}
  .mini:hover{background:var(--btn-hover)}
  .mini:disabled{background:var(--line);color:var(--muted);
        opacity:1;cursor:not-allowed}"""))

# ---------- 徽章：边框 → 色底 ----------
R.append((
"""  .badge{font-size:11px;padding:1px 8px;border-radius:3px;border:1px solid var(--line);
         color:var(--muted);white-space:nowrap}
  .badge.ok{color:var(--ok);border-color:var(--ok)}
  .badge.warn{color:var(--warn);border-color:var(--warn)}
  .badge.err{color:var(--err);border-color:var(--err)}""",
"""  .badge{font-size:11px;padding:1px 8px;border-radius:3px;background:var(--subtle);
         color:var(--muted);white-space:nowrap}
  .badge.ok{color:var(--ok);background:var(--ok-bg)}
  .badge.warn{color:var(--warn);background:rgba(224,160,64,.15)}
  .badge.err{color:var(--err);background:var(--err-bg)}"""))

# ---------- Tooltip：去描边 ----------
R.append((
"""       padding:7px 11px;border-radius:4px;border:1px solid rgba(255,255,255,.08);""",
"""       padding:7px 11px;border-radius:4px;"""))

# ---------- 监控选择卡：边框 → 色阶 ----------
R.append((
"""  .sel-card{position:relative;display:flex;align-items:center;gap:12px;
    padding:12px 14px;border:1px solid var(--line);border-radius:8px;
    background:var(--bg-raise);cursor:pointer;user-select:none;
    opacity:.6;filter:grayscale(.5);
    transition:opacity .2s ease,filter .2s ease,border-color .2s ease}
  .sel-card:hover{border-color:var(--border-strong)}
  .sel-card.on{opacity:1;filter:none;border-color:var(--border-strong)}""",
"""  .sel-card{position:relative;display:flex;align-items:center;gap:12px;
    padding:12px 14px;border:none;border-radius:8px;
    background:var(--bg-input);cursor:pointer;user-select:none;
    opacity:.6;filter:grayscale(.5);
    transition:opacity .2s ease,filter .2s ease,background .2s ease}
  .sel-card:hover{background:var(--btn-hover)}
  .sel-card.on{opacity:1;filter:none;background:var(--sel)}"""))

# ---------- 扫码胶囊：边框 → 色阶 ----------
R.append((
"""    padding:10px 20px;border-radius:999px;border:1px solid var(--line);
    background:var(--surface);color:var(--fg);cursor:pointer;font-family:inherit;
    transition:transform .15s ease,box-shadow .15s ease,border-color .15s ease,
               background .15s ease}
  .scan-cap:hover{border-color:var(--brand);background:var(--subtle);
    transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.25)}""",
"""    padding:10px 20px;border-radius:999px;border:none;
    background:var(--btn-bg);color:var(--fg);cursor:pointer;font-family:inherit;
    transition:transform .15s ease,box-shadow .15s ease,background .15s ease}
  .scan-cap:hover{background:var(--btn-hover);
    transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.25)}"""))

# ---------- 凭证健康列表：去行线 ----------
R.append((
"""  .cred-row{display:flex;align-items:center;gap:10px;padding:9px 0;
    border-bottom:1px solid var(--line);min-height:44px}""",
"""  .cred-row{display:flex;align-items:center;gap:10px;padding:9px 0;
    min-height:44px}"""))

# ---------- 输入框：无边框，色底 + 聚焦光环 ----------
R.append((
"""  .field input{width:100%;height:42px;padding:0 16px;border-radius:4px;
               border:1px solid var(--input-line);background:var(--surface);color:var(--fg);
               font-size:14px;font-family:ui-monospace,Consolas,"Courier New",monospace}
  .field textarea{width:100%;padding:10px 14px;border-radius:4px;
               border:1px solid var(--input-line);background:var(--surface);color:var(--fg);
               font:12.5px/1.55 ui-monospace,Consolas,"Courier New",monospace;
                  resize:vertical;min-height:112px}
  .field input:focus,.field textarea:focus{outline:none;border-color:var(--brand)}""",
"""  .field input{width:100%;height:42px;padding:0 16px;border-radius:6px;
               border:none;background:var(--bg-input);color:var(--fg);
               font-size:14px;font-family:ui-monospace,Consolas,"Courier New",monospace}
  .field textarea{width:100%;padding:10px 14px;border-radius:6px;
               border:none;background:var(--bg-input);color:var(--fg);
               font:12.5px/1.55 ui-monospace,Consolas,"Courier New",monospace;
                  resize:vertical;min-height:112px}
  .field input:focus,.field textarea:focus{outline:none;
               box-shadow:0 0 0 2px var(--focus-ring)}"""))

R.append((
"""  .hint code{background:var(--subtle);padding:0 4px;border-radius:2px;font-size:11px;
             border:1px solid var(--line)}""",
"""  .hint code{background:var(--subtle);padding:0 4px;border-radius:2px;font-size:11px}"""))

R.append((
"""  input[type=time],input[type=number]{width:100%;height:42px;padding:0 14px;
                   border-radius:4px;border:1px solid var(--input-line);
                   background:var(--surface);color:var(--fg);
                   font-size:14px;font-family:ui-monospace,Consolas,monospace}
  input[type=time]:focus,input[type=number]:focus{outline:none;border-color:var(--brand)}""",
"""  input[type=time],input[type=number]{width:100%;height:42px;padding:0 14px;
                   border-radius:6px;border:none;
                   background:var(--bg-input);color:var(--fg);
                   font-size:14px;font-family:ui-monospace,Consolas,monospace}
  input[type=time]:focus,input[type=number]:focus{outline:none;
                   box-shadow:0 0 0 2px var(--focus-ring)}"""))

# ---------- 提醒时间 chips：边框 → 色底 ----------
R.append((
"""  .chip{display:inline-flex;align-items:center;gap:5px;background:var(--subtle);
        border:1px solid var(--line);border-radius:3px;padding:2px 3px 2px 9px;
        font-size:12.5px;font-family:ui-monospace,Consolas,monospace}""",
"""  .chip{display:inline-flex;align-items:center;gap:5px;background:var(--subtle);
        border-radius:3px;padding:2px 3px 2px 9px;
        font-size:12.5px;font-family:ui-monospace,Consolas,monospace}"""))

R.append((
"""  .chip.empty{color:var(--muted);border-style:dashed;padding:2px 9px;font-family:inherit}""",
"""  .chip.empty{color:var(--muted);background:transparent;padding:2px 9px;font-family:inherit}"""))

# ---------- Win11 设置行：去分隔线，靠 hover/留白 ----------
R.append((
"""  .set-card > .card-body{padding:0}
  .set-item{border-bottom:1px solid var(--line)}
  .set-item:last-child{border-bottom:none}""",
"""  .set-card > .card-body{padding:0}"""))

R.append((
"""  .set-body{padding:12px 22px 16px 272px;border-top:1px dashed var(--line)}""",
"""  .set-body{padding:12px 22px 16px 272px}"""))

# ---------- KPI：去边框 ----------
R.append((
"""  .kpi{background:var(--bg-elevated);border:1px solid var(--border-subtle);border-radius:8px;
       padding:12px 18px;min-width:0}""",
"""  .kpi{background:var(--bg-elevated);border:none;border-radius:8px;
       padding:12px 18px;min-width:0}"""))

# ---------- 日志控制台：去描边 ----------
R.append((
"""  .console{flex-shrink:0;background:var(--surface);
           border:1px solid var(--line);border-bottom:none;border-left:none;border-right:none;
           border-radius:0;overflow:hidden}""",
"""  .console{flex-shrink:0;background:var(--surface);
           border-radius:0;overflow:hidden}"""))

R.append((
"""  .console-body{border-top:1px solid var(--line);background:var(--bg-sub);""",
"""  .console-body{background:var(--bg-sub);"""))

# ---------- 巡检动态列表：去虚线 ----------
R.append((
"""  .log-row{display:flex;align-items:center;gap:10px;padding:9px 0;
           border-bottom:1px dashed var(--line);font-size:12.5px;flex-wrap:wrap}
  .log-row:last-child{border-bottom:none}""",
"""  .log-row{display:flex;align-items:center;gap:10px;padding:9px 0;
           font-size:12.5px;flex-wrap:wrap}"""))

# ---------- 手机预览模型：去描边 ----------
R.append((
"""  .phone-mock{background:#1b1e23;border:1px solid var(--line);border-radius:10px;
              padding:14px}""",
"""  .phone-mock{background:#1b1e23;border-radius:10px;
              padding:14px}"""))

# ---------- 守护进程 kv：去虚线 ----------
R.append((
"""  .kv-grid > div{display:flex;justify-content:space-between;gap:10px;
    padding:6px 0;border-bottom:1px dashed var(--line)}
  .kv-grid > div:nth-last-child(-n+2){border-bottom:none}""",
"""  .kv-grid > div{display:flex;justify-content:space-between;gap:10px;
    padding:6px 0}"""))

# ---------- 通知/警告条：边框 → 色底 ----------
R.append((
"""  .notice{margin-bottom:12px;padding:8px 12px;border:1px solid var(--warn);border-radius:4px;
          font-size:12.5px;line-height:1.7;background:rgba(217,119,6,.07)}""",
"""  .notice{margin-bottom:12px;padding:8px 12px;border-radius:6px;
          font-size:12.5px;line-height:1.7;background:rgba(217,119,6,.07)}"""))

R.append((
"""  .notice.info{border-color:var(--line);background:var(--subtle)}""",
"""  .notice.info{background:var(--subtle)}"""))

# ---------- 扫码区：二维码白底加阴影替边框；kv 去顶线 ----------
R.append((
"""  .qrbox{display:inline-flex;padding:10px;background:#fff;border:1px solid var(--line);
         border-radius:4px;min-height:224px;min-width:224px;align-items:center;
         justify-content:center}""",
"""  .qrbox{display:inline-flex;padding:10px;background:#fff;
         border-radius:8px;min-height:224px;min-width:224px;align-items:center;
         justify-content:center;box-shadow:0 2px 10px rgba(0,0,0,.2)}"""))

R.append((
"""  .kv{margin-top:12px;font-size:12.5px;color:var(--muted);text-align:left;
      border-top:1px solid var(--line);padding-top:10px}""",
"""  .kv{margin-top:12px;font-size:12.5px;color:var(--muted);text-align:left;
      padding-top:10px}"""))

# ---------- 扫码浮层 modal：去边框（阴影足够） ----------
R.append((
"""  .modal{background:var(--bg-card);border:1px solid var(--border-strong);border-radius:10px;""",
"""  .modal{background:var(--bg-card);border:none;border-radius:10px;"""))

R.append((
"""  .modal-x{width:26px;height:26px;border-radius:5px;border:1px solid var(--line);
    background:var(--surface);color:var(--muted);cursor:pointer;font-size:12px;line-height:1}
  .modal-x:hover{background:var(--subtle);color:var(--fg)}""",
"""  .modal-x{width:26px;height:26px;border-radius:5px;border:none;
    background:var(--btn-bg);color:var(--muted);cursor:pointer;font-size:12px;line-height:1}
  .modal-x:hover{background:var(--btn-hover);color:var(--fg)}"""))

R.append((
"""  #test-result{margin-top:4px;padding:8px 12px;border-top:1px solid var(--line);font-size:12.5px}""",
"""  #test-result{margin-top:4px;padding:8px 12px;font-size:12.5px}"""))

# ---------- 设备伪装折叠：展开头去底线改色阶；数字圆珠去描边 ----------
R.append((
"""  #dev-card[open] summary{border-bottom:1px solid var(--line)}""",
"""  #dev-card[open] summary{background:var(--bg-head)}"""))

R.append((
"""  .dstep b{position:absolute;left:-30px;top:0;width:18px;height:18px;border-radius:50%;
    background:var(--subtle);border:1px solid var(--line);color:var(--muted);""",
"""  .dstep b{position:absolute;left:-30px;top:0;width:18px;height:18px;border-radius:50%;
    background:var(--btn-bg);color:var(--muted);"""))

R.append((
"""  .apk-qr{display:inline-block;padding:6px;background:#fff;border:1px solid var(--line);
          border-radius:4px}""",
"""  .apk-qr{display:inline-block;padding:6px;background:#fff;
          border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.15)}"""))

R.append((
"""  .dev-text{width:100%;min-height:132px;padding:10px 14px;border-radius:4px;
    border:1px solid var(--input-line);background:var(--surface);color:var(--fg);
    font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:12px;
    line-height:1.6;resize:vertical;
    box-shadow:inset 0 2px 6px rgba(0,0,0,.18)}
  .dev-text:focus{outline:none;border-color:var(--brand)}""",
"""  .dev-text{width:100%;min-height:132px;padding:10px 14px;border-radius:6px;
    border:none;background:var(--bg-input);color:var(--fg);
    font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:12px;
    line-height:1.6;resize:vertical;
    box-shadow:inset 0 2px 6px rgba(0,0,0,.18)}
  .dev-text:focus{outline:none;
    box-shadow:inset 0 2px 6px rgba(0,0,0,.18),0 0 0 2px var(--focus-ring)}"""))

# ---------- 活动卡：去描边，纯色阶 ----------
R.append((
"""  /* 卡片底色比页面卡面再提亮一档 + 更明显的描边，避免"陷进黑泥" */
  .acard{display:flex;flex-direction:column;gap:8px;
         min-height:120px;justify-content:space-between;
         padding:14px 16px;font-size:12.5px;
         background:var(--bg-raise);border-color:var(--border-strong)}""",
"""  /* 卡片底色比页面卡面再提亮一档，靠色阶分层，不用描边 */
  .acard{display:flex;flex-direction:column;gap:8px;
         min-height:120px;justify-content:space-between;
         padding:14px 16px;font-size:12.5px;
         background:var(--bg-raise)}"""))

R.append((
"""    background:var(--bg-head);margin:-14px -16px 0;padding:10px 16px 8px;
    border-bottom:1px solid var(--border-subtle);border-radius:7px 7px 0 0}""",
"""    background:var(--bg-head);margin:-14px -16px 0;padding:10px 16px 8px;
    border-radius:10px 10px 0 0}"""))

R.append((
"""  .act-drawer{margin-top:16px;border:1px solid var(--border-subtle);border-radius:8px;""",
"""  .act-drawer{margin-top:16px;border:none;border-radius:8px;"""))

R.append((
"""  .au-chip{display:inline-block;font-size:11.5px;color:var(--muted);background:var(--subtle);
    border:1px solid var(--line);border-radius:3px;padding:1px 8px;margin:0 6px 4px 0}""",
"""  .au-chip{display:inline-block;font-size:11.5px;color:var(--muted);background:var(--subtle);
    border-radius:3px;padding:1px 8px;margin:0 6px 4px 0}"""))

# ---------- 游戏迷你卡：占位图与底部去线 ----------
R.append((
"""  .gcard-ico.ph{display:grid;place-items:center;background:var(--subtle);
    border:1px solid var(--line);color:var(--muted);font-size:16px}""",
"""  .gcard-ico.ph{display:grid;place-items:center;background:var(--subtle);
    color:var(--muted);font-size:16px}"""))

R.append((
"""  .gcard-foot{font-size:11.5px;color:var(--muted);border-top:1px dashed var(--line);
              padding-top:8px;margin-top:auto}""",
"""  .gcard-foot{font-size:11.5px;color:var(--muted);
              padding-top:8px;margin-top:auto}"""))

# ---------- 热力图：无数据格去边框；等待态改斜纹 ----------
R.append((
"""  .hm-cell.na{background:var(--subtle);border:1px solid var(--line);color:var(--muted)}
  .hm-cell.na.wait{border-style:dashed}""",
"""  .hm-cell.na{background:var(--subtle);color:var(--muted)}
  .hm-cell.na.wait{background:repeating-linear-gradient(135deg,
    var(--subtle) 0 4px, var(--hover) 4px 8px)}"""))

# ---------- 通知渠道列表：边框 → 色阶 ----------
R.append((
"""  .ch-item{display:flex;align-items:center;gap:10px;padding:11px 12px;
    border:1px solid var(--line);border-radius:6px;cursor:pointer;user-select:none;min-width:0}
  .ch-item:hover{background:var(--hover)}
  .ch-item.on{border-color:var(--brand);background:var(--sel)}""",
"""  .ch-item{display:flex;align-items:center;gap:10px;padding:11px 12px;
    border:none;border-radius:6px;cursor:pointer;user-select:none;min-width:0;
    background:var(--subtle)}
  .ch-item:hover{background:var(--hover)}
  .ch-item.on{background:var(--sel)}"""))

# ---------- 推送记录列表：去虚线 ----------
R.append((
"""  .recent div{padding:6px 0;border-bottom:1px dashed var(--line);
    font-family:ui-monospace,Consolas,monospace;word-break:break-all}
  .recent div:last-child{border-bottom:none}""",
"""  .recent div{padding:6px 0;
    font-family:ui-monospace,Consolas,monospace;word-break:break-all}"""))

# ---------- 执行 ----------
miss = []
for i, (old, new) in enumerate(R):
    n = src.count(old)
    if n != 1:
        miss.append((i, n, old.splitlines()[0][:70]))
        continue
    src = src.replace(old, new)

if miss:
    for i, n, head in miss:
        print("BAD  #%d count=%d  %s" % (i, n, head))
    sys.exit(1)

io.open(P, "w", encoding="utf-8", newline="").write(src)
print("OK: %d replacements applied (%d -> %d bytes)" % (len(R), len(orig), len(src)))
