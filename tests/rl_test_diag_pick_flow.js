/* 块B：点导出→弹原生文件夹选择→选完导出（2026-11-24）。
 * 从 ui/app.js 抽取 pickDiagFolderAndExport 源码，mock 下执行，断言：
 *   B1 带上次记住的目录作 initial_dir 调 pick-folder
 *   B2 选中目录后保存进 localStorage 并调 exportDiagnostics(btn, dir)
 *   B3 取消（ok=false）时不导出、不报错 toast
 *   B4 无历史目录时 initial_dir 为空
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
const fnSrc = extractFn(APP_JS, 'pickDiagFolderAndExport');
check('B0 抽取到 pickDiagFolderAndExport 源码', !!fnSrc, fnSrc ? fnSrc.length + ' chars' : 'null');

const KEY = 'rl-diag-last-dir';
function run(pickResult, storedDir) {
  const calls = { post: [], exports: [], toasts: [], set: [], get: [] };
  const btn = { disabled: false, dataset: {}, innerHTML: '', textContent: '' };
  const ctx = {
    DIAG_LAST_DIR_KEY: KEY,   // 生产环境是 app.js 模块级 const，抽取函数时需注入同款全局
    postJSON: (url, payload) => {
      calls.post.push({ url, payload });
      if (url === '/api/diag/pick-folder') return Promise.resolve(pickResult);
      return Promise.resolve({});
    },
    exportDiagnostics: (b, dir) => { calls.exports.push({ b, dir }); return Promise.resolve(); },
    toast: (msg, isErr) => { calls.toasts.push({ msg, isErr }); },
    localStorage: {
      getItem: (k) => { calls.get.push(k); return k === KEY ? (storedDir || null) : null; },
      setItem: (k, v) => { calls.set.push({ k, v }); },
    },
    console,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(fnSrc, ctx);
  return new Promise((resolve) => {
    ctx.pickDiagFolderAndExport(btn).then(() => setImmediate(() => resolve(calls)));
  });
}

(async () => {
  if (!fnSrc) { console.log('\nPASS 0 / FAIL ' + FAIL); process.exit(1); }
  const DIR = 'D:\\MyDiag';
  const r1 = await run({ ok: true, dir: DIR }, 'C:\\LastDir');
  check('B1 带上次目录作 initial_dir 调 pick-folder',
        r1.post.length === 1 && r1.post[0].url === '/api/diag/pick-folder' &&
        r1.post[0].payload.initial_dir === 'C:\\LastDir', JSON.stringify(r1.post));
  check('B2 选中后存 localStorage 并调 exportDiagnostics(btn, dir)',
        r1.exports.length === 1 && r1.exports[0].dir === DIR &&
        r1.set.length === 1 && r1.set[0].k === KEY && r1.set[0].v === DIR,
        JSON.stringify({ e: r1.exports, s: r1.set }));

  const r2 = await run({ ok: false, dir: '' }, null);
  check('B3 取消时不导出、不报错 toast',
        r2.exports.length === 0 && r2.toasts.length === 0 && r2.set.length === 0,
        JSON.stringify(r2));
  check('B4 无历史目录时 initial_dir 为空',
        r2.post[0].payload.initial_dir === '', JSON.stringify(r2.post));

  console.log('\nPASS ' + PASS + ' / FAIL ' + FAIL);
  process.exit(FAIL ? 1 : 0);
})();
