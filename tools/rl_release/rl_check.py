# -*- coding: utf-8 -*-
"""login_ui.py HTML_PAGE 静态自检：结构配平 + ID 引用一致性 + 旧类残留。"""
import re, sys
sys.path.insert(0, r"E:\Apps\HopOn")
import login_ui

html = login_ui.HTML_PAGE
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
for t, pos in [(t, p) for t, p in []]: pass
if errors: pass
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

# 2) CSS 花括号配平
css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
if css.count("{") != css.count("}"):
    errors.append(f"CSS 花括号不配平: {{={css.count('{')} }}={css.count('}')}")

# 3) JS 引用的 getElementById 必须在 HTML 里存在（动态渲染的除外）
js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
ids_in_html = set(re.findall(r'id="([\w-]+)"', html))
dynamic_prefix = ("h-", "live-", "act-", "cred-", "run-", "pv-", "apk-", "btn-apk",
                  "test-", "d-times", "console-")
for ref in set(re.findall(r"getElementById\('([\w-]+)'\)", js)):
    if ref in ids_in_html: continue
    if ref.startswith(dynamic_prefix): continue
    warns.append(f"JS 引用的 id 不在静态 HTML：{ref}")

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
for need in ["kpi-bar", "log-console", "d-ruler", "set-item", "api/logtail"]:
    if need not in html:
        errors.append(f"缺少一阶段组件：{need}")

print("=== ERRORS ===")
print("\n".join(errors) if errors else "(none)")
print("=== WARNINGS ===")
print("\n".join(sorted(set(warns))) if warns else "(none)")
print("=== RESULT:", "FAIL" if errors else "PASS", "===")
