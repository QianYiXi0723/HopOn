// 用真实 /api/history + /api/schedule 数据跑二阶段渲染函数（Node 冒烟）
const fs = require("fs");
const src = fs.readFileSync("D:/WorkBuddy/rl_page.js", "utf8");

// 按函数名抓完整定义（花括号配平）
function grab(name) {
  const i = src.indexOf("function " + name + "(");
  if (i < 0) throw new Error("未找到函数 " + name);
  let d = 0, j = src.indexOf("{", i);
  for (let k = j; k < src.length; k++) {
    if (src[k] === "{") d++;
    else if (src[k] === "}") { d--; if (d === 0) return src.slice(i, k + 1); }
  }
  throw new Error("函数 " + name + " 花括号不配平");
}
function grabConst(name) {
  const m = src.match(new RegExp("const " + name + " = [\\s\\S]*?;[\\r\\n]+"));
  if (!m) throw new Error("未找到常量 " + name);
  return m[0];
}

const code = [
  grabConst("GAME_DOT"), grabConst("GAME_ICON"),
  'const ICON_STAMP = "test";',
  'var window = { __liveMap: { genshin: {name:"原粹树脂",current:160,maximum:200,percent:80} } };',
  'var captured = {};',
  'var document = { getElementById: (id) => (captured[id] = captured[id] || { innerHTML: "" }) };',
  grab("escHtml"), grab("gameBaseName"), grab("gameDotColor"), grab("shortStamp"),
  grab("gameIconHtml"), grab("todayCellFor"), grab("gameCardHtml"),
  grab("renderHeatmap"), grab("buildLiveMap"), grab("renderHistory"),
  'var renderHistoryEmpty = (m) => { captured["h-table"].innerHTML = m; };',
].join("\n");

eval(code);

(async () => {
  const get = async (u) => (await fetch(u)).json();
  const base = "http://127.0.0.1:8793";
  const hist = await get(base + "/api/history?days=7");
  const live = await get(base + "/api/live");

  // renderHistory：迷你卡 + 热力图
  renderHistory(hist);
  const cards = captured["h-stats"].innerHTML;
  const heat = captured["h-table"].innerHTML;
  if (!cards.includes('class="gcards"')) throw new Error("迷你卡未渲染");
  if (!cards.includes("gcard-streak")) throw new Error("迷你卡缺连续天数");
  if (!cards.includes("sbar")) throw new Error("迷你卡缺体力条");
  if (!heat.includes('class="heatmap"')) throw new Error("热力图未渲染");
  if (!heat.includes("hm-legend")) throw new Error("热力图缺图例");
  const cardCount = (cards.match(/class="gcard"/g) || []).length;
  console.log("迷你卡数量:", cardCount, "| 热力图行数:", (heat.match(/hm-row/g) || []).length);

  // buildLiveMap + 体力映射
  const map = buildLiveMap(live);
  console.log("liveMap games:", Object.keys(map).join(",") || "(空)");

  // KPI：下次巡查
  const sched = await get(base + "/api/schedule");
  const mkpi = String(sched.next_run).match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/);
  console.log("next_run:", sched.next_run, "| 解析:", mkpi ? "OK" : "FAIL");

  // logtail 最近推送行
  const tail = await get(base + "/api/logtail?n=300");
  const pushRows = (tail.lines || []).filter((l) => /推送/.test(l));
  console.log("推送记录行数:", pushRows.length);
  console.log("SMOKE_PASS");
})().catch((e) => { console.error("SMOKE_FAIL:", e.message); process.exit(1); });
