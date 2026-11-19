/* updDlReset 按钮状态复位回归（2026-11-19）。
 * 背景：更新弹窗点「下载并安装更新」后按钮变「准备下载…」+disabled，
 * 若 POST 失败走 updDlReset()，但旧实现只切三处 hidden，漏恢复文字与
 * disabled → 按钮永久卡在「准备下载…」，用户以为卡死。
 * 本测试直接从 ui/app.js 抽取 updDlReset 源码在 mock DOM 下执行，
 * 断言复位后按钮文字恢复初始值、disabled 解除——针对真实生产代码，非 mock。
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const APP_JS = fs.readFileSync(path.join(ROOT, 'ui', 'app.js'), 'utf8');
const INDEX_HTML = fs.readFileSync(path.join(ROOT, 'ui', 'index.html'), 'utf8');

let PASS = 0, FAIL = 0;
const FAILED = [];
function check(name, cond, detail) {
  if (cond) { PASS++; console.log('  PASS ' + name); }
  else { FAIL++; FAILED.push(name + (detail ? ' | ' + detail : '')); console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}

// 从 index.html 读按钮初始文字（唯一真源）
const m = INDEX_HTML.match(/id="upd-dl-btn"[^>]*>([^<]*)</);
const INITIAL = m ? m[1].trim() : null;
check('T0 拿到按钮初始文字', !!INITIAL, String(INITIAL));

// 抽取 updDlReset 函数源码（按大括号配平，避免正则截断）
function extractFunction(src, name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) return null;
  const brace = src.indexOf('{', start);
  let depth = 0, i = brace;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { i++; break; } }
  }
  return src.slice(start, i);
}
const fnSrc = extractFunction(APP_JS, 'updDlReset');
check('T1 抽取到 updDlReset 源码', !!fnSrc && fnSrc.length > 40, fnSrc ? fnSrc.length + ' chars' : 'null');

if (fnSrc && INITIAL) {
  // 构造 mock DOM：三个元素都有 hidden；按钮额外有 disabled/textContent
  function el() { return { hidden: false, disabled: false, textContent: '' }; }
  const els = { 'upd-dl-btn': el(), 'upd-dl-prog': el(), 'upd-dl-done': el() };
  const document = { getElementById: (id) => els[id] || null };
  let cleared = false;
  const ctx = {
    document,
    clearTimeout: () => { cleared = true; },
    _updDlTimer: null,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(fnSrc, ctx);

  // 模拟 updDlStart 已把按钮改成「准备下载…」并 disabled
  els['upd-dl-btn'].textContent = '准备下载…';
  els['upd-dl-btn'].disabled = true;
  els['upd-dl-prog'].hidden = false;

  // 调用复位（失败分支所走的路径）
  ctx.updDlReset();

  check('T2 复位后按钮文字恢复初始值（「' + INITIAL + '」）',
        els['upd-dl-btn'].textContent === INITIAL, els['upd-dl-btn'].textContent);
  check('T3 复位后按钮 disabled 解除',
        els['upd-dl-btn'].disabled === false, String(els['upd-dl-btn'].disabled));
  check('T4 复位后按钮可见', els['upd-dl-btn'].hidden === false, String(els['upd-dl-btn'].hidden));
  check('T5 复位后进度条隐藏', els['upd-dl-prog'].hidden === true, String(els['upd-dl-prog'].hidden));
  check('T6 复位后完成态隐藏', els['upd-dl-done'].hidden === true, String(els['upd-dl-done'].hidden));
}

console.log('');
console.log('='.repeat(50));
console.log('PASS ' + PASS + ' / FAIL ' + FAIL);
if (FAILED.length) { console.log('失败项：'); FAILED.forEach(f => console.log('  - ' + f)); }
process.exit(FAIL ? 1 : 0);
