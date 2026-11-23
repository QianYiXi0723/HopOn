/* 托盘打卡后概览重拉绑定回归（2026-11-24 用户选方案A）。
 * 行为：页面 focus / 从隐藏变可见时重拉 loadDailies()，让托盘勾选的完成态
 * 同步到已打开的概览（后端同账本，缺的只是刷新触发）。
 * 方法：从 ui/app.js 的标记注释间抽取真实绑定代码块，在 mock window/document/
 * loadDailies 下执行，触发事件断言 loadDailies 被调用——针对真实生产代码。
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const APP = fs.readFileSync(path.join(ROOT, 'ui', 'app.js'), 'utf8');

let PASS = 0, FAIL = 0;
const FAILED = [];
function check(name, cond, detail) {
  if (cond) { PASS++; console.log('  PASS ' + name); }
  else { FAIL++; FAILED.push(name + (detail ? ' | ' + detail : '')); console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}

const B = '/* dl-refresh-bind-begin */';
const E = '/* dl-refresh-bind-end */';
const bs = APP.indexOf(B);
const es = APP.indexOf(E);
check('T1 app.js 存在重拉绑定块（begin/end 标记）', bs >= 0 && es > bs,
      `begin=${bs} end=${es}`);

if (bs >= 0 && es > bs) {
  const code = APP.slice(bs + B.length, es);
  const handlers = {};
  let calls = 0;
  const ctx = {
    window: { addEventListener: (evt, fn) => { handlers['w:' + evt] = fn; } },
    document: {
      hidden: true,
      addEventListener: (evt, fn) => { handlers['d:' + evt] = fn; },
    },
    loadDailies: () => { calls++; return Promise.resolve(); },
    console,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(code, ctx);

  check('T2 绑定了 window focus 与 document visibilitychange',
        typeof handlers['w:focus'] === 'function' && typeof handlers['d:visibilitychange'] === 'function',
        Object.keys(handlers).join(','));

  handlers['w:focus']();
  check('T3 focus 触发一次 loadDailies（calls=' + calls + '）', calls === 1, String(calls));

  ctx.document.hidden = true;
  handlers['d:visibilitychange']();
  check('T4 仍隐藏时 visibilitychange 不重拉（不浪费请求）', calls === 1, String(calls));

  ctx.document.hidden = false;
  handlers['d:visibilitychange']();
  check('T5 变可见时 visibilitychange 触发重拉（calls=' + calls + '）', calls === 2, String(calls));
}

console.log('');
console.log('='.repeat(50));
console.log('PASS ' + PASS + ' / FAIL ' + FAIL);
if (FAILED.length) { console.log('失败项：'); FAILED.forEach(f => console.log('  - ' + f)); }
process.exit(FAIL ? 1 : 0);
