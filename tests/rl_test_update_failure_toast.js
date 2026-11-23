/* 更新失败结果→前端提示 端到端闭环回归（update-parity Task4，2026-11-24）。
 * 背景：ec1ba42 让前端消费后端 /api/update/result，但 result.ok=false 分支
 * （app.js:836）此前是死代码——没有任何代码写失败 result。update-parity
 * Task1 让 Updater 失败写 result.ok=false，本测试钉住闭环的另一半：
 * 启动确认 IIFE 拿到失败结果时，真的调用 toast(失败文案, true)。
 *
 * 方法：从 ui/app.js 抽取启动确认 IIFE 源码，在 mock fetch/toast 下执行，
 * 断言失败 result 触发 toast 且文案非成功——针对真实生产代码，非源码字符串钉。
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const APP_JS = fs.readFileSync(path.join(ROOT, 'ui', 'app.js'), 'utf8');

let PASS = 0, FAIL = 0;
const FAILED = [];
function check(name, cond, detail) {
  if (cond) { PASS++; console.log('  PASS ' + name); }
  else { FAIL++; FAILED.push(name + (detail ? ' | ' + detail : '')); console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}

// 抽取启动确认 IIFE：从「更新结果确认」注释后的 (function(){ ... fetch('/api/update/result') ... })();
function extractResultIIFE(src) {
  const marker = "fetch('/api/update/result')";
  const fetchPos = src.indexOf(marker);
  if (fetchPos < 0) return null;
  const start = src.lastIndexOf('(function(){', fetchPos);
  if (start < 0) return null;
  // 从 start 按括号配平找到 IIFE 结束 })();
  let depth = 0, i = start, ended = false;
  for (; i < src.length; i++) {
    if (src[i] === '(') depth++;
    else if (src[i] === ')') { depth--; if (depth === 0) { ended = true; break; } }
  }
  if (!ended) return null;
  return src.slice(start, i + 1); // 含最后的 )
}
const iife = extractResultIIFE(APP_JS);
check('T1 抽取到启动确认 IIFE 源码', !!iife && iife.includes('/api/update/result'),
      iife ? iife.length + ' chars' : 'null');

// mock fetch：返回可控的 result payload；mock toast：记录调用
function runIIFE(payload) {
  const toasts = [];
  const ctx = {
    fetch: (url) => {
      if (url !== '/api/update/result') throw new Error('unexpected url ' + url);
      return Promise.resolve({ json: () => Promise.resolve(payload) });
    },
    toast: (msg, isErr) => { toasts.push({ msg, isErr }); },
    document: { getElementById: () => null },
    console,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  // 抽取停在函数表达式的闭合 ')'（结尾是 '})'），需补 '();' 才是真正的 IIFE 调用
  vm.runInContext(iife + '();', ctx);
  // fetch→json→then 是两层 Promise，setImmediate 后 microtask 应已排空
  return new Promise((resolve) => setImmediate(() => resolve(toasts)));
}

(async () => {
  // 失败 result → 必须弹失败 toast（不是静默、不是成功提示）
  const failToasts = await runIIFE({
    ok: true, present: true, current: '2.7.0.5',
    result: { ok: false, error: '校验失败，已回滚', version: '2.7.0.6', ts: 'now' },
  });
  check('T2 失败 result 触发一次 toast（不再静默）', failToasts.length === 1,
        JSON.stringify(failToasts));
  check('T3 失败 toast 是错误态（isErr=true）', failToasts.length === 1 && failToasts[0].isErr === true,
        JSON.stringify(failToasts));
  check('T4 失败 toast 文案不含「已成功更新」（不误导）',
        failToasts.length === 1 && !failToasts[0].msg.includes('已成功更新'),
        failToasts[0] ? failToasts[0].msg : '');

  // 无 result（present=false）→ 静默，不弹任何 toast（绝大多数启动）
  const noneToasts = await runIIFE({ ok: true, present: false, result: null, current: '2.7.0.6' });
  check('T5 present=false 静默返回（不误报）', noneToasts.length === 0,
        JSON.stringify(noneToasts));

  // 成功 result → 成功 toast（回归保护，确认没被失败改动破坏）
  const okToasts = await runIIFE({
    ok: true, present: true, current: '2.7.0.6',
    result: { ok: true, version: '2.7.0.6', ts: 'now' },
  });
  check('T6 成功 result 仍弹成功 toast（回归）',
        okToasts.length === 1 && okToasts[0].msg.includes('已成功更新'),
        JSON.stringify(okToasts));

  console.log('');
  console.log('='.repeat(50));
  console.log('PASS ' + PASS + ' / FAIL ' + FAIL);
  if (FAILED.length) { console.log('失败项：'); FAILED.forEach(f => console.log('  - ' + f)); }
  process.exit(FAIL ? 1 : 0);
})();
