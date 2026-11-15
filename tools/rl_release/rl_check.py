# -*- coding: utf-8 -*-
"""ui/ 三件套静态自检：标签配平 + CSS 括号 + ID 引用一致性（双向）+ 旧类残留 + 12 栅格 + 关键节点。

数据源 = 磁盘上的 ui/index.html + ui/style.css + ui/app.js（与 _assemble_page 拼页同源）。
不再 import 壳层模块取常量：旧 HTML_PAGE 常量移除后本脚本跟着常量名漂移整只死亡，
改按 ROOT 推导直接读盘，门禁的数据面从此只跟三件套文件走。
"""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]     # tools/rl_release/rl_check.py → 仓库根
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
css  = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")
js   = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")

errors, warns = [], []

# 1) 标签配平（忽略 void 标签）
from html.parser import HTMLParser
VOID = {"area","base","br","col","embed","hr","img","input","link","meta","source","track","wbr"}
class Chk(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
    def handle_starttag(self, tag, attrs):
        if tag not in VOID: self.stack.append((tag, self.getpos()))
    def handle_endtag(self, tag):
        if tag in VOID: return
        if not self.stack:
            errors.append(f"多余的闭合标签 </{tag}> @ {self.getpos()}"); return
        t, pos = self.stack.pop()
        if t != tag:
            errors.append(f"标签不匹配: <{t}>@{pos} 被 </{tag}>@{self.getpos()} 闭合")
Chk().feed(html)
# 未闭合
class Chk2(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
    def handle_starttag(self, tag, attrs):
        if tag not in VOID: self.stack.append((tag, self.getpos()))
    def handle_endtag(self, tag):
        if tag in VOID: return
        if self.stack and self.stack[-1][0] == tag: self.stack.pop()
        else: errors.append(f"异常闭合 </{tag}> @ {self.getpos()}")
c2 = Chk2(); c2.feed(html)
for t, pos in c2.stack:
    errors.append(f"未闭合标签 <{t}> @ {pos}")

# 2) CSS 花括号配平（css = ui/style.css 全文，不再从 <style> 块抠——拆分后该块已不存在）
if css.count("{") != css.count("}"):
    errors.append(f"CSS 花括号不配平: {{={css.count('{')} }}={css.count('}')}")

# 3) 正向：JS 引用的 getElementById 必须在 HTML 里存在（动态渲染的除外）
ids_in_html = set(re.findall(r'id="([\w-]+)"', html))
dynamic_prefix = ("h-", "live-", "act-", "cred-", "run-", "pv-", "apk-", "btn-apk",
                  "test-", "d-times", "console-")
for ref in set(re.findall(r"getElementById\('([\w-]+)'\)", js)):
    if ref in ids_in_html: continue
    if ref.startswith(dynamic_prefix): continue
    warns.append(f"JS 引用的 id 不在静态 HTML：{ref}")

# 3b) 反向（ERROR）：「必存在 id 白名单」——HTML 里必须有，且 JS 仍有引用，缺任一即拦发版。
#     抓的是更隐蔽的一侧：HTML 删了节点而 JS 还在引用 → getElementById 静默返 null，
#     渲染函数整个空转，控制台零报错、界面"看起来正常"但内容不对。
#     JS 引用判定三档（2026-11-16 对 v2.6.5 实况逐条核验）：
#       a) getElementById('id') 字面引用（单/双引号都认）
#       b) kpiSet('id', …) —— kpiSet 内部即 document.getElementById（app.js 已核）
#       c) getElementById('<前缀>' + …) 动态拼 id，白名单 id 以该前缀开头即算
#          （现役前缀 sum-/ch-state-/activity-tier-/tab-/cap-dot-/resin-/ch-dot-；
#            名单增删时须核对该 id 确为该前缀运行时的真实目标，防前缀误配假绿）
#     维护纪律：删卡退休一个 id = 显式改这份名单并留注释，不许悄悄漏掉。
MUST_EXIST_IDS = [
    "kpi-bar", "k-today", "k-today-sub", "k-resin", "k-resin-sub",
    "live-grid", "btn-live-refresh", "act-list", "btn-act-refresh",
    "log-console", "d-ruler", "d-next", "sum-monitor",
    # "kpi-toggle",   # 批次 2 新增此节点后启用（当前 HTML/JS 均未落地，先注释防误报）
    # "k-next"/"k-cred" 不入名单：批次 2 计划删除这两个节点，届时同样在此留痕声明退休
    # "set-item" 不入名单：它是 acc-* 设置行的 class（id 家族是 acc-*），不是 id；
    #   其存在性由第 6 项 'set-item' 检查继续覆盖
]
def _js_ref(iid):
    if re.search(r"getElementById\(['\"]%s['\"]\)" % iid, js): return "getElementById"
    if re.search(r"\bkpiSet\(['\"]%s['\"]" % iid, js): return "kpiSet"
    for m in re.finditer(r"getElementById\(['\"]([\w-]+?)['\"]\s*\+", js):
        if iid.startswith(m.group(1)): return f"动态前缀 {m.group(1)!r}"
    return None
for iid in MUST_EXIST_IDS:
    in_html, ref = iid in ids_in_html, _js_ref(iid)
    if in_html and ref: continue
    if not in_html:
        errors.append(f"必存在节点从 HTML 消失：#{iid}"
                      + (f"（JS 仍有 {ref} 引用）" if ref else "（JS 亦无引用）"))
    else:
        errors.append(f"必存在节点 #{iid} HTML 还在、但 JS 已无任何引用（合法退休请同步改 MUST_EXIST_IDS）")

# 4) 旧体系残留
for bad in ["col-stack", "acc-item", "acc-head", "acc-body", 'class="panel full"',
            '"cols full"', "acc-sep", "tbl-report"]:
    if bad in html:
        errors.append(f"旧类/旧结构残留：{bad!r}")

# 5) 12 列网格：tabpanel.on 必须是 12 列；存在 span 类
if "repeat(12,1fr)" not in css:
    errors.append("tabpanel.on 不是 12 列网格")
for cls in ["s3","s4","s5","s6","s7","s8","s12"]:
    if f".{cls}{{" not in css.replace(" ",""):
        warns.append(f"缺少 span 类 .{cls}")

# 6) 关键新组件存在
#    2026-11-16：三件套拆分后关键字符串可能落在 HTML 或 JS（如 api/logtail 端点现居 app.js，
#    后端路由在壳层——组件本身是活的，只是不在 index.html），故存在域 = html 或 js；
#    组件名单本身维持 2026-10 裁决原样，一字未动。
for need in ["kpi-bar", "log-console", "d-ruler", "set-item", "api/logtail"]:
    if need not in html and need not in js:
        errors.append(f"缺少一阶段组件：{need}")

print("=== ERRORS ===")
print("\n".join(errors) if errors else "(none)")
print("=== WARNINGS ===")
print("\n".join(sorted(set(warns))) if warns else "(none)")
print("=== RESULT:", "FAIL" if errors else "PASS", "===")
sys.exit(1 if errors else 0)
