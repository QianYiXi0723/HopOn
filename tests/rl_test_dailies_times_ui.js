/* 自定义提醒多时刻 UI（2026-11-25）：收集/增删时刻。TDD 先 RED。 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const ROOT = path.resolve(__dirname, '..');
const APP = fs.readFileSync(path.join(ROOT, 'ui', 'app.js'), 'utf8');
let PASS = 0, FAIL = 0;
function check(n, c, d) { if (c) { PASS++; console.log('  PASS ' + n); } else { FAIL++; console.log('  FAIL ' + n + (d ? ' | ' + d : '')); } }
function extractFn(src, name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) return null;
  let depth = 0, i = src.indexOf('{', start), ended = false;
  for (; i < src.length; i++) { if (src[i] === '{') depth++; else if (src[i] === '}') { depth--; if (!depth) { ended = true; break; } } }
  return ended ? src.slice(start, i + 1) : null;
}
const collectSrc = extractFn(APP, 'collectDailiesTimes');
check('U0 collectDailiesTimes 存在', !!collectSrc);

// mock document：#df-times 下三个 .df-time-input
function makeCtx(values) {
  const inputs = values.map(v => ({ value: v, className: 'df-time-input' }));
  return {
    document: {
      querySelectorAll: (sel) => sel === '.df-time-input' ? inputs : [],
    },
  };
}
if (collectSrc) {
  const c1 = makeCtx(['08:00', '12:00', '18:00']);
  vm.createContext(c1); vm.runInContext(collectSrc, c1);
  check('U1 收集多个时刻', JSON.stringify(c1.collectDailiesTimes()) === JSON.stringify(['08:00', '12:00', '18:00']), JSON.stringify(c1.collectDailiesTimes()));
  const c2 = makeCtx(['', '12:00', 'bad']);
  vm.createContext(c2); vm.runInContext(collectSrc, c2);
  check('U2 过滤空与非法', JSON.stringify(c2.collectDailiesTimes()) === JSON.stringify(['12:00']), JSON.stringify(c2.collectDailiesTimes()));
}
console.log('\nPASS ' + PASS + ' / FAIL ' + FAIL);
process.exit(FAIL ? 1 : 0);
