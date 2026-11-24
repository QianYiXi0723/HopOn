/* 块A：诊断包导出后显示真实路径结果行（2026-11-24）。
 * 根因：app.js 旧代码写死「诊断包已导出到桌面」，丢弃后端已返回的 res.path，
 * 桌面不存在回退到 diagnostics 目录时文案还是错的。
 * 本测试从 ui/app.js 抽取 exportDiagnostics 源码，在 mock 下执行，断言：
 *   1. 成功后把真实 res.path 写进只读结果行（不再写死「桌面」）
 *   2. toast 只说已导出·N 个文件，不含路径
 *   3. 空文件仍追加提示
 * TDD：先跑看 RED，再改实现。
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const APP_JS = fs.readFileSync(path.join(ROOT, 'ui', 'app.js'), 'utf8');

let PASS = 0, FAIL = 0;
function check(name, cond, detail) {
  if (cond) { PASS++; console.log('  PASS ' + name); }
  else { FAIL++; console.log('  FAIL ' + name + (detail ? ' | ' + detail : '')); }
}

// 抽取 exportDiagnostics 函数源码（async function ... 到配平右括号）
function extractFn(src, name) {
  const marker = 'async function ' + name + '(';
  const start = src.indexOf(marker);
  if (start < 0) return null;
  let depth = 0, i = src.indexOf('{', start), ended = false;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { ended = true; break; } }
  }
  return ended ? src.slice(start, i + 1) : null;
}
const fnSrc = extractFn(APP_JS, 'exportDiagnostics');
check('A0 抽取到 exportDiagnostics 源码', !!fnSrc, fnSrc ? fnSrc.length + ' chars' : 'null');

function run(payload) {
  const toasts = [];
  const els = {};
  function mkEl(id) {
    return els[id] || (els[id] = { id, value: '', textContent: '', hidden: true, style: {} });
  }
  const ctx = {
    fetch: (url) => {
      if (url !== '/api/diag_export') throw new Error('unexpected url ' + url);
      return Promise.resolve({ json: () => Promise.resolve(payload) });
    },
    toast: (msg, isErr) => { toasts.push({ msg, isErr }); },
    document: { getElementById: (id) => mkEl(id) },
    console,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(fnSrc, ctx);
  const btn = { disabled: false, dataset: { label: '导出诊断包' }, innerHTML: '', textContent: '' };
  return new Promise((resolve) => {
    ctx.exportDiagnostics(btn).then(() => setImmediate(() => resolve({ toasts, els })));
  });
}

(async () => {
  const REAL = 'C:\\Users\\me\\AppData\\Local\\HopOn\\diagnostics\\HopOn_Diagnostics_20261124_153012.zip';
  const r = await run({ ok: true, path: REAL, files: ['MANIFEST.txt'], empty: [], message: '导出成功' });

  check('A1 结果行只读框填入真实完整路径',
        r.els['diag-result-path'] && r.els['diag-result-path'].value === REAL,
        r.els['diag-result-path'] && r.els['diag-result-path'].value);
  check('A2 结果行从隐藏变为可见',
        r.els['diag-result-row'] && r.els['diag-result-row'].hidden === false,
        r.els['diag-result-row'] && r.els['diag-result-row'].hidden);
  check('A3 成功 toast 说已导出·N个文件、不含真实路径、不写死桌面',
        r.toasts.length === 1 && /已导出/.test(r.toasts[0].msg) &&
        r.toasts[0].msg.indexOf(REAL) < 0 && r.toasts[0].msg.indexOf('桌面') < 0,
        JSON.stringify(r.toasts));

  const r2 = await run({ ok: true, path: REAL, files: [], empty: ['a.log'], message: '' });
  check('A4 有空文件时追加一条提示', r2.toasts.length === 2, JSON.stringify(r2.toasts));

  console.log('\nPASS ' + PASS + ' / FAIL ' + FAIL);
  process.exit(FAIL ? 1 : 0);
})();
