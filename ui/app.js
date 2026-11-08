
/* =========================== Tab 切换 =========================== */
const PANEL_NAMES = ['history', 'query', 'account', 'schedule'];
let schedTimer = null;

let activeTab = 'history';   // 当前页签（数据轮询的闸门：只轮询可见页）

function switchTab(name){
  activeTab = name;
  PANEL_NAMES.forEach(function(k){
    const panel = document.getElementById('tab-' + k);
    if (panel) {
      panel.classList.toggle('on', k === name);
    }
    const btn = document.querySelector('.nav button[data-tab="' + k + '"]');
    if (btn) btn.classList.toggle('on', k === name);
  });

  if (name === 'query') loadQuery();
  if (name === 'schedule') { loadSchedule(); startSchedPoll(); loadThresholds(); loadAutostart(); loadAlwaysNotify(); loadQuiet(); loadRunLog(); } else { stopSchedPoll(); }
  /* 账号与通知已合并为一页：通知的三路加载并入 account 分支 */
  if (name === 'account') { loadDevice(); loadAccounts(); refreshNotice(); loadPush(); loadPushPreview(); loadPushRecent(); }
  if (name === 'history') { loadHistory(); loadLive(false); }
}

function startSchedPoll(){
  stopSchedPoll();
  schedTimer = setInterval(loadSchedule, 3000);
}
function stopSchedPoll(){
  if (schedTimer) { clearInterval(schedTimer); schedTimer = null; }
}

document.querySelectorAll('.nav button[data-tab]').forEach(function(btn){
  btn.addEventListener('click', function(){ switchTab(this.dataset.tab); });
});

document.addEventListener('click', function(e){
  /* 点击高亮：点卡片/折叠框加 .active（蓝描边），点其它卡片时切换。
     常驻只是「点过」的痕迹，光标离开就由下面的 pointerout 撤掉（见下方注释）。
     各 .card 均适用（dev-card 入口卡也是 .card）。
     【死命令：同一事件只绑一次】原独立监听已并入本处理函数 */
  const card = e.target.closest('.card');
  document.querySelectorAll('.card.active').forEach(function(c){
    if (c !== card) c.classList.remove('active');
  });
  if (card) card.classList.add('active');

  const link = e.target.closest('[data-goto]');
  if (!link) return;
  e.preventDefault();
  switchTab(link.dataset.goto);
});

/* 点击高亮跟随光标：光标离开卡片范围立即恢复原样（原来会常驻到点下一张卡，
   看着像「选中」了却没有任何后续动作）。mouseleave 不冒泡，因此用 pointerout
   配 relatedTarget 判断是否真的离开——在卡片内部子元素之间移动不撤高亮。 */
document.addEventListener('pointerout', function(e){
  const card = e.target.closest('.card');
  if (!card || !card.classList.contains('active')) return;
  const to = e.relatedTarget;
  if (to && card.contains(to)) return;      // 还在同一张卡里，只是换了子元素
  card.classList.remove('active');
});

/* =========================== 通用提示 =========================== */
function toast(text, isErr){
  const el = document.getElementById('toast');
  el.textContent = text;
  el.className = 'show' + (isErr ? ' err' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(function(){ el.className = ''; }, 2800);
}

/* 页内通知轮询：daemon 的 toast 渠道把推送同步投到 data/toasts.json，
   这里 30s 取走未读（消费型队列，服务端取走即标已读）并逐条弹出。
   与页内操作提示共用同一个气泡位，队列 3s 一条防止互相顶掉。 */
const _pushToastQueue = [];
let _pushToastBusy = false;
function _drainPushToasts(){
  if (_pushToastBusy || !_pushToastQueue.length) return;
  _pushToastBusy = true;
  const item = _pushToastQueue.shift();
  toast(item.title + '\n' + item.body);
  setTimeout(function(){ _pushToastBusy = false; _drainPushToasts(); }, 3000);
}
function loadToasts(){
  fetch('/api/toasts').then(function(r){ return r.json(); }).then(function(d){
    if (!d.ok || !(d.items || []).length) return;
    _pushToastQueue.push(...(d.items || []));
    _drainPushToasts();
  }).catch(function(){ /* 静默：页内通知不是关键路径 */ });
}
loadToasts();
setInterval(loadToasts, 30000);

async function postJSON(url, payload){
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {})
  });
  return r.json();
}

/* =========================== 秒开缓存（localStorage） ===========================
   机制：每次成功拉到数据就落一份到 localStorage；页面一打开先用缓存把 KPI /
   报表 / 实时面板画出来（绝不让用户看 0/0 白屏），真实请求随后在后台静默刷新。
   key 带版本号，结构变更时旧缓存整体失效。 */
const CACHE_V = 'rl-cache-v1';

function cacheGet(key){
  try {
    const raw = localStorage.getItem(CACHE_V + ':' + key);
    return raw ? JSON.parse(raw) : null;
  } catch (e) { return null; }
}

function cacheSet(key, val){
  try { localStorage.setItem(CACHE_V + ':' + key, JSON.stringify(val)); } catch (e) {}
}

/* KPI 静默刷新态计数：history 与 live 两路并发，最后一个到手才摘掉转圈 */
let kpiBusyCount = 0;
function kpiBusy(on){
  kpiBusyCount = Math.max(0, kpiBusyCount + (on ? 1 : -1));
  ['k-today', 'k-resin', 'k-cred'].forEach(function(id){
    const el = document.getElementById(id);
    if (el) el.classList.toggle('refreshing', kpiBusyCount > 0);
  });
}

/* 秒开：用上次缓存先把界面画出来。缓存结构异常就当没有，走正常加载。 */
function renderCachedData(){
  const ch = cacheGet('history');
  if (ch && ch.has_data) {
    try {
      renderHistory(ch);
      renderKpiToday(ch);
    } catch (e) { console.warn('缓存报表渲染失败，走正常加载', e); }
  }
  const cl = cacheGet('live');
  if (cl && cl.ok !== false && (cl.games || []).length) {
    try { renderLive(cl); } catch (e) { console.warn('缓存实时面板渲染失败', e); }
  }
}

function escHtml(v){
  return String(v == null ? '' : v).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

/* =========================== 账号绑定 =========================== */
let sid = null, timer = null;

/* 选择卡视觉与 input 状态严格同步：.on = 开启 */
function syncSelCard(box){
  const card = box.closest('.sel-card');
  if (card) card.classList.toggle('on', box.checked);
}

function toggleAll(group, checked){
  document.querySelectorAll('input[name="' + group + '"]').forEach(function(i){
    i.checked = checked;
    syncSelCard(i);
  });
  updateLoginButtons();
  pushEnabled(group,
    [...document.querySelectorAll('input[name="' + group + '"]')].map(function(i){ return i.value; }),
    checked);
}

/* 标题行右侧的「全选」次级按钮：未全开 → 全开；已全开 → 取消全选 */
function toggleAllCards(){
  const boxes = [...document.querySelectorAll('.sel-card input')];
  const target = !boxes.every(function(b){ return b.checked; });
  ['m', 's', 'k'].forEach(function(g){
    const gb = [...document.querySelectorAll('input[name="' + g + '"]')];
    if (gb.some(function(b){ return b.checked !== target; })) toggleAll(g, target);
  });
}

function pushEnabled(group, games, enabled){
  postJSON('/api/accounts/enabled', { games: games, enabled: enabled })
    .then(function(d){
      if (!d || !d.ok) { toast((d && d.message) || '设置失败', true); loadAccounts(); return; }
      toast(d.message || (enabled ? '已开启提醒' : '已关闭提醒'));
    })
    .catch(function(){ toast('无法连接本地服务', true); });
}

/* 「全选」按钮文案随状态变化 + 游戏监控抽屉的折叠摘要
   （2026-11-06：游戏监控收成抽屉后，收起时必须能看到「几款在监控」，
   否则要展开才知道自己在盯哪些游戏） */
function updateLoginButtons(){
  const boxes = [...document.querySelectorAll('.sel-card input')];
  const allOn = boxes.length > 0 && boxes.every(function(b){ return b.checked; });
  const btn = document.getElementById('btn-selall');
  if (btn) btn.textContent = allOn ? '取消全选' : '全选';
  const onCount = boxes.filter(function(b){ return b.checked; }).length;
  setSum('monitor', onCount + '/' + boxes.length + ' 参与',
         boxes.length ? ('已开启 ' + onCount + ' / 共 ' + boxes.length + ' 款游戏参与每日巡检') : '');
}

/* 从后端拉取真实的巡检开关与凭证状态，覆盖页面默认值 */
function loadAccounts(){
  fetch('/api/accounts').then(function(r){ return r.json(); }).then(function(d){
    if (!d || !d.ok) { credLoadFailed(); return; }
    /* 存一份映射，扫码选择弹窗按它显示游戏名与凭证状态 */
    window.__accounts = {};
    (d.items || []).forEach(function(it){ window.__accounts[it.id] = it; });
    (d.items || []).forEach(function(it){
      const box = document.querySelector(
        'input[name="m"][value="' + it.id + '"],input[name="s"][value="' + it.id + '"],input[name="k"][value="' + it.id + '"]');
      if (!box) return;
      box.checked = !!it.enabled;
      syncSelCard(box);
    });
    /* 存量配置里还没有账号行的游戏（如后加的鸣潮）：勾选框不跟 HTML 默认的
       checked 走——没有账号行就不算"参与巡检"，诚实显示未勾；绑定后
       write_kuro 建行，下次 loadAccounts 自动勾上 */
    [...document.querySelectorAll('.sel-card input')].forEach(function(box){
      if (!window.__accounts[box.value]) { box.checked = false; syncSelCard(box); }
    });
    renderCredHealth(d);
    renderKpiCred(d);
    renderCapsuleDots(d);
    updateLoginButtons();
  }).catch(credLoadFailed);
}

/* 读取失败要有真反馈：凭证健康/KPI 停在「读取中…」是假加载 */
function credLoadFailed(){
  renderCredHealth({ ok: false, items: [] });
  kpiSet('k-cred-sub', '读取失败');
}

/* 胶囊黄点：该平台任一游戏未绑定凭证 → 点亮 */
function renderCapsuleDots(d){
  ['m', 's', 'k'].forEach(function(g){
    const dot = document.getElementById('cap-dot-' + g);
    if (!dot) return;
    const missing = (d.items || []).some(function(it){
      const box = document.querySelector('input[name="' + g + '"][value="' + it.id + '"]');
      return box && !it.has_credential;
    });
    dot.classList.toggle('on', missing);
  });
}

/* 凭证健康：紧凑列表。绿点 = 已绑定（含从未更新）· 黄点 = 超过 7 天未更新 · 红点 = 未绑定
   色彩纪律：健康一律绿点灰字，只有「过期/未绑定」才给警示色 */
function relTime(text){
  // "2026-10-27 22:00:33" -> "3天前"
  const m = String(text || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return String(text || '');
  const t = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  const diff = Date.now() - t.getTime();
  if (diff < 0) return m[2] + '-' + m[3];
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return min + '分钟前';
  const h = Math.floor(min / 60);
  if (h < 24) return h + '小时前';
  const d = Math.floor(h / 24);
  if (d < 30) return d + '天前';
  return m[2] + '-' + m[3];
}

function credAgeDays(text){
  const m = String(text || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return null;
  const t = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime();
  return (Date.now() - t) / 86400000;
}

function renderCredHealth(d){
  const box = document.getElementById('cred-health');
  const badge = document.getElementById('cred-health-badge');
  if (!box) return;
  if (d && d.ok === false) {
    box.innerHTML = '<div class="emptybox">读取账号状态失败，请点「刷新」或稍后重试。</div>';
    if (badge) badge.textContent = '-';
    return;
  }
  /* 列与徽章同口径 = 仅启用账号：与「今日完成 0/3」同一分母（KPI tip 的文档契约），
     未勾选游戏的凭证数据保留在配置里，重新勾选即原样恢复 */
  const items = ((d && d.items) || []).filter(function(i){ return i.enabled; });
  if (!items.length) {
    box.innerHTML = '<div class="emptybox">没有启用任何游戏账号。</div>';
    if (badge) badge.textContent = '-';
    return;
  }
  const bound = items.filter(function(i){ return i.has_credential; }).length;
  if (badge) {
    badge.textContent = bound + '/' + items.length + ' 已绑定';
    badge.className = 'badge' + (bound === items.length ? '' : ' warn');
  }
  box.innerHTML = '<div class="cred-list">' +
    items.map(function(it){
      const age = it.has_credential ? credAgeDays(it.credential_updated_at) : null;
      let st, stText, timeTxt;
      if (!it.has_credential) {
        st = 'err'; stText = '未绑定'; timeTxt = '从未更新';
      } else if (age == null) {
        st = 'ok'; stText = '已绑定'; timeTxt = '从未更新';
      } else if (age > 7) {
        st = 'warn'; stText = '超过 7 天未更新'; timeTxt = relTime(it.credential_updated_at);
      } else {
        st = 'ok'; stText = '已绑定'; timeTxt = relTime(it.credential_updated_at);
      }
      return '<div class="cred-row' + (st === 'err' ? ' bad' : '') +
             (st === 'warn' ? ' stale' : '') + '" data-gid="' + escHtml(it.id) +
             '" title="点击扫码更新该游戏凭证">' +
        selIconHtml(it.id, it.name || it.id) +
        '<div class="cred-info"><b>' + escHtml(it.name || it.id) + '</b>' +
        '<small>' + stText +
          (it.nickname ? ' · ' + escHtml(it.nickname) : '') + '</small></div>' +
        '<span class="cred-dot ' + st + '"></span>' +
        '<span class="cred-time">' + escHtml(timeTxt) + '</span>' +
        '</div>';
    }).join('') + '</div>';
}

/* 凭证行点击直达扫码（Discord 连接页范式：状态与动作同卡）：
   只预选该游戏，不改监控开关、不沿用「已绑定即预勾」默认 */
document.getElementById('cred-health').addEventListener('click', function(e){
  const row = e.target.closest('[data-gid]');
  if (!row) return;
  const box = document.querySelector('.sel-grid input[value="' + row.dataset.gid + '"]');
  if (box) openPickModal(box.name, [row.dataset.gid]);
});

/* 选择卡/凭证行的游戏图标：五款游戏都有官方图（终末地自 v1.1.8 起用 App Store 图） */
function selIconHtml(gid, name){
  const base = GAME_ICON[gid];
  if (!base) {
    return '<span class="sel-ico ph">' + escHtml(String(name || gid || '?').slice(0, 1)) + '</span>';
  }
  const v = ICON_STAMP.indexOf('__') === 0 ? '' : '?v=' + ICON_STAMP;
  return '<img class="sel-ico" alt="" src="' + base + '.png' + v +
         '" srcset="' + base + '@2x.png' + v + ' 2x">';
}

/* 把静态占位图替换成官方图标（GAME_ICON 定义后调用一次） */
function fillSelIcons(){
  document.querySelectorAll('.sel-card').forEach(function(card){
    const ph = card.querySelector('.sel-ico.ph');
    if (!ph) return;
    const input = card.querySelector('input');
    if (!input) return;
    const tmp = document.createElement('div');
    tmp.innerHTML = selIconHtml(input.value, ph.textContent);
    const node = tmp.firstChild;
    if (node) ph.replaceWith(node);
  });
}

document.querySelectorAll('.sel-card input').forEach(function(box){
  syncSelCard(box);
  box.addEventListener('change', function(){
    syncSelCard(box);
    updateLoginButtons();
    pushEnabled(box.name, [box.value], box.checked);
  });
});

function badgeFor(state){
  return {
    creating:['准备中',''], waiting:['等待扫码','warn'], scanned:['已扫码','warn'],
    writing:['写入中',''], done:['完成','ok'], error:['失败','err']
  }[state] || [state,''];
}

function setBusy(busy){
  ['cap-m', 'cap-s'].forEach(function(id){
    const b = document.getElementById(id);
    if (b) b.disabled = busy;
  });
}

function render(d){
  const stage = document.getElementById('stage');
  stage.hidden = false;
  const pair = badgeFor(d.state);
  const badge = document.getElementById('badge');
  badge.textContent = pair[0];
  badge.className = 'badge ' + pair[1];
  document.getElementById('msg').textContent = d.message || '';

  const box = document.getElementById('qrbox');
  if (d.qr) {
    if (!box.querySelector('img')) box.innerHTML = '<img alt="登录二维码">';
    box.querySelector('img').src = 'data:image/png;base64,' + d.qr;
  } else if (d.state === 'creating' || d.state === 'writing') {
    box.innerHTML = '<span class="ph"><span class="spinner"></span>请稍候…</span>';
  }

  const kv = document.getElementById('detail');
  const items = [];
  if (d.detail) {
    if (d.detail.ltuid) items.push('米游社 ID：<code>' + d.detail.ltuid + '</code>');
    if (d.detail.games && d.detail.games.length) items.push('已更新：' + d.detail.games.join('、'));
    if (d.detail.note) items.push(d.detail.note);
  }
  if (d.results && d.results.length && (d.results.length > 1 || d.state === 'error')) {
    const rows = d.results.map(function(r){
      /* skipped = 正常跳过（账号下没这款游戏）：灰色 –，别画成红 ✗ 吓人 */
      if (r.skipped) {
        return '<div><span class="dim">–</span>' + r.label +
               (r.note ? ' — ' + r.note : '') + '</div>';
      }
      return '<div><span class="' + (r.ok ? 'i-ok' : 'i-no') + '">' +
             (r.ok ? '✓' : '✗') + '</span>' + r.label +
             (r.note ? ' — ' + r.note : '') + '</div>';
    }).join('');
    items.push('<div class="res">' + rows + '</div>');
  }
  kv.innerHTML = items.join('<br>');
  kv.hidden = items.length === 0;
}

async function start(provider, targets){
  reset();
  setBusy(true);
  document.getElementById('modal-title').textContent =
    provider === 'mihoyo' ? '米游社扫码登录' : '森空岛扫码登录';
  const url = '/api/start?provider=' + encodeURIComponent(provider)
            + '&targets=' + encodeURIComponent(targets.join(','));
  try {
    const r = await fetch(url);
    const d = await r.json();
    if (!d.ok) { showError(d.message); setBusy(false); return; }
    sid = d.sid;
    poll(pollEpoch);
  } catch (e) {
    showError('无法连接本地服务：' + e);
    setBusy(false);
  }
}

/* ---------- 两步式扫码：第一步先选游戏，第二步才生成二维码 ---------- */
const PROVIDER_OF = { m: 'mihoyo', s: 'skland' };
const PROVIDER_LABEL = { m: '米游社', s: '森空岛' };
let pickGroup = null;

/* 点胶囊 → 打开选择弹窗（默认勾选已绑定凭证的游戏，未绑定不勾） */
function openPickModal(group, forced){
  pickGroup = group;
  const accs = window.__accounts || {};
  const boxes = [...document.querySelectorAll('input[name="' + group + '"]')];
  document.getElementById('pick-list').innerHTML = boxes.map(function(b){
    const it = accs[b.value] || {};
    const name = it.name || b.value;
    const bound = it.has_credential === true;
    const known = it.has_credential === true || it.has_credential === false;
    const stateText = bound ? '已绑定凭证' : (known ? '未绑定' : '凭证状态未知');
    /* forced（凭证行点击直达）：只勾指定游戏——动作针对单个条目，
       不沿用「已绑定即预勾」默认，也绝不触碰监控开关 */
    const checked = forced ? (forced.indexOf(b.value) !== -1) : bound;
    return '<label class="pick-row">' +
      '<input type="checkbox" value="' + escHtml(b.value) + '"' + (checked ? ' checked' : '') + '>' +
      selIconHtml(b.value, name) +
      '<span class="pick-name">' + escHtml(name) +
        '<small>' + stateText + '</small></span>' +
      '</label>';
  }).join('');
  document.getElementById('pick-title').textContent =
    '选择要登录的游戏 · ' + (PROVIDER_LABEL[group] || '');
  updatePickNext();
  document.getElementById('pick-modal').hidden = false;
}

/* 取消/关闭：只收起弹窗，此时还没创建任何扫码会话，无副作用 */
function closePick(){
  document.getElementById('pick-modal').hidden = true;
  pickGroup = null;
}

/* 设备伪装：点击入口卡弹配置弹窗（低频专注任务用模态，不再抽屉展开） */
function openDev(){
  document.getElementById('dev-modal').hidden = false;
}
function closeDev(){
  document.getElementById('dev-modal').hidden = true;
}
/* Esc 统一退出所有模态（Nielsen #3/#4：同类组件同一套紧急出口）；
   stage 关闭走 reset()——进行中的扫码会话关闭即中断，语义等同其 ✕ 按钮 */
document.addEventListener('keydown', function(e){
  if (e.key !== 'Escape') return;
  if (!document.getElementById('dev-modal').hidden) { closeDev(); return; }
  if (!document.getElementById('pick-modal').hidden) { closePick(); return; }
  if (!document.getElementById('stage').hidden) reset();
});
/* 遮罩点击关闭：pick/dev/addtime（关闭均无副作用）；stage 例外——
   进行中的扫码会话防误触中断（错误预防优先于一致，故意不一致） */
document.querySelectorAll('.modal-mask').forEach(function(mask){
  if (mask.id === 'stage') return;
  mask.addEventListener('click', function(e){
    if (e.target !== mask) return;
    if (mask.id === 'pick-modal') closePick();
    else if (mask.id === 'dev-modal') closeDev();
    else if (mask.id === 'addtime-modal') rlCloseAddTime();
  });
});

function pickedGames(){
  return [...document.querySelectorAll('#pick-list input:checked')].map(function(i){
    return i.value;
  });
}

function updatePickNext(){
  const btn = document.getElementById('btn-pick-next');
  if (btn) btn.disabled = pickedGames().length === 0;
}

/* 第二步：拿勾选结果去申请二维码（进入原有的扫码浮层流程） */
function confirmPick(){
  const targets = pickedGames();
  if (!targets.length || !pickGroup) return;
  const group = pickGroup;
  closePick();
  start(PROVIDER_OF[group], targets);
}

function startMihoyo(){
  openPickModal('m');
}

function startSkland(){
  openPickModal('s');
}

function showError(msg){
  const stage = document.getElementById('stage');
  stage.hidden = false;
  const badge = document.getElementById('badge');
  badge.textContent = '失败'; badge.className = 'badge err';
  document.getElementById('msg').textContent = msg;
  document.getElementById('qrbox').innerHTML = '<span class="ph">未能创建二维码</span>';
}

/* 扫码轮询的会话代数：reset()/重新 start() 时 +1。在途 fetch 杀不掉——
   响应回来先对代数，旧代的直接作废，否则刚关掉的浮层会被旧响应弹回来、
   新旧两条轮询链还会并存（频率翻倍 + 旧会话覆盖新会话渲染）。 */
let pollEpoch = 0;

function poll(epoch){
  const my = epoch;
  fetch('/api/state?sid=' + encodeURIComponent(sid))
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (my !== pollEpoch) return;   // 旧代在途响应：作废
      render(d);
      if (d.state === 'done' || d.state === 'error') { setBusy(false); loadAccounts(); return; }
      timer = setTimeout(poll, 1000, my);
    })
    .catch(function(){ if (my === pollEpoch) timer = setTimeout(poll, 2000, my); });
}

function reset(){
  pollEpoch += 1;                     // 在途响应全部作废
  if (timer) { clearTimeout(timer); timer = null; }
  sid = null;
  setBusy(false);
  document.getElementById('stage').hidden = true;
  document.getElementById('qrbox').innerHTML = '<span class="ph">二维码准备中…</span>';
  document.getElementById('detail').hidden = true;
}

/* 「关于·检查更新」（2026-11-07，VS Code/Win11 范式：查更新放版本信息旁）：
   点击侧栏版本行 → /api/update（refresh=1 强制联网）→ toast 三态结果 */
let _updateChecking = false;
function checkUpdate(){
  if (_updateChecking) return;              // 防连点
  _updateChecking = true;
  const btn = document.getElementById('ver-line');
  const old = btn ? btn.textContent : '';
  if (btn) btn.textContent = '检查中…';
  fetch('/api/update?refresh=1')
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (!d || !d.ok) {
        toast((d && d.message) || '检查失败：网络不可用', true);
        return;
      }
      if (d.has_update) {
        // 有新版：toast 带版本与下载指引（下载仍走 GitHub/Gitee，双渠道）
        toast('发现新版本 v' + d.latest + '！当前 v' + d.current + '，打开下载页', false);
        if (d.url) window.open(d.url, '_blank');
      } else {
        toast('已是最新版本（v' + d.current + '）');
      }
    })
    .catch(function(e){ toast('检查失败：' + e, true); })
    .finally(function(){
      _updateChecking = false;
      if (btn) btn.textContent = old;
    });
}
document.getElementById('ver-line').addEventListener('click', checkUpdate);

function refreshNotice(){
  fetch('/api/env').then(function(r){ return r.json(); }).then(function(d){
    /* 左下角版本号：以 /api/env 的 version 为准（HTML 里不再写死数字）。
       拿不到就保持"本地运行"，不显示一个可能是错的版本。 */
    const ver = document.getElementById('ver-line');
    if (ver && d.version) ver.textContent = 'v' + d.version + ' · 本地运行';
    /* 演示模式横幅：出厂 mock 配置时点亮，扫码切换真实数据源后自然消失 */
    const wb = document.getElementById('welcome');
    if (wb) wb.hidden = !d.demo_mode || welcomeDismissed;
    const box = document.getElementById('notice');
    if (!box) return;
    if (d.configured) { box.style.display = 'none'; return; }
    box.style.display = 'block';
    box.innerHTML =
      '<b>尚未配置推送通道</b>，手机收不到提醒。去 <a data-goto="account">「账号与通知」</a> 填写。';
  }).catch(function(){});
}

/* 演示模式横幅的「不再显示」（静态元素，只绑一次；持久化，刷新不回弹） */
var welcomeDismissed = false;
try { welcomeDismissed = localStorage.getItem('rl_welcome_dismissed') === '1'; } catch(e) {}
(function(){
  const x = document.getElementById('welcome-x');
  if (x) x.addEventListener('click', function(){
    welcomeDismissed = true;
    try { localStorage.setItem('rl_welcome_dismissed', '1'); } catch(e) {}
    const w = document.getElementById('welcome');
    if (w) w.hidden = true;
  });
})();

/* ---------- 设备伪装 ---------- */
function renderDevice(d){
  if (!d.ok) { toast(d.message || '读取设备档案失败', true); return; }
  const p = d.profile || {};
  /* 2026-11-01：卡头不再显示当前机型徽章（机型详情只在弹窗内展示） */
  /* 档案字段来自用户粘贴的第三方导出文本（设备伪装页的核心工作流），
     落盘持久化后每次启动都回显——此处必须转义，否则是持久化 XSS */
  document.getElementById('dev-current').innerHTML =
    '当前设备：<b>' + escHtml(p.brand || '?') + ' ' + escHtml(p.model || '?') + '</b>' +
    ' · Android ' + escHtml(p.os_version || '?') +
    ' · OAID <code>' + escHtml(p.oaid || '?') + '</code>' +
    ' · 指纹缓存' + (d.cache_exists ? '存在' : '<b>已清空</b>');
  const resetBtn = document.getElementById('btn-dev-reset');
  if (resetBtn) resetBtn.hidden = !d.custom;
  const proxyBox = document.getElementById('query-proxy');
  if (proxyBox) proxyBox.value = d.proxy || '';
  const proxyHint = document.getElementById('proxy-hint');
  if (proxyHint) proxyHint.textContent = d.proxy
    ? '已配置代理出口，米游社查询将经此连接'
    : '未配置，米游社查询直连';

  const apk = d.apk || {};
  const qrBox = document.getElementById('apk-qr');
  const meta = document.getElementById('apk-meta');
  if (apk.available && apk.qr) {
    qrBox.innerHTML = '<img alt="APK 下载二维码">';
    qrBox.querySelector('img').src = 'data:image/png;base64,' + apk.qr;
    let shown = apk.url;
    try { shown = decodeURIComponent(apk.url); } catch (e) {}
    meta.innerHTML = escHtml(apk.name) + '（' + escHtml(apk.size_text) + '）<br>' + escHtml(shown) +
      (apk.warning ? '<br><span class="warn-t">⚠ ' + escHtml(apk.warning) + '</span>' : '');
  } else {
    qrBox.innerHTML =
      '<div style="padding:10px 8px;text-align:center">' +
      '<div style="font-size:12px;color:var(--muted);line-height:1.5">二维码不可用</div>' +
      '<button class="btn sm" id="btn-apk-retry" style="margin-top:6px">生成下载二维码</button>' +
      '</div>';
    meta.innerHTML = '<span class="warn-t">' + escHtml(apk.message || '未找到 APK') + '</span>';
    const btn = document.getElementById('btn-apk-retry');
    if (btn) btn.addEventListener('click', restartApkService);
  }
}

async function restartApkService(){
  const btn = document.getElementById('btn-apk-retry');
  if (btn) { btn.disabled = true; btn.textContent = '重试中…'; }
  try {
    const d = await postJSON('/api/apk/restart', {});
    const box = document.getElementById('apk-qr');
    const meta = document.getElementById('apk-meta');
    if (d.available && d.qr) {
      box.innerHTML = '<img alt="APK 下载二维码">';
      box.querySelector('img').src = 'data:image/png;base64,' + d.qr;
      let shown = d.url;
      try { shown = decodeURIComponent(d.url); } catch (e) {}
      meta.innerHTML = escHtml(d.name) + '（' + escHtml(d.size_text) + '）<br>' + escHtml(shown);
      toast('下载服务已就绪，端口 ' + d.port);
    } else {
      if (btn) { btn.disabled = false; btn.textContent = '生成下载二维码'; }
      meta.innerHTML = '<span class="warn-t">' + escHtml(d.message || '仍然不可用') + '</span>';
      toast('仍然不可用：' + (d.message || ''), true);
    }
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '生成下载二维码'; }
    toast('重启失败：' + e.message, true);
  }
}

function loadDevice(){
  fetch('/api/device').then(function(r){ return r.json(); })
    .then(renderDevice)
    .catch(function(){ toast('无法读取设备档案', true); });
}

/* =========================== 体力溢出阈值 =========================== */
function renderThresholds(d){
  if (!d || !d.ok) { return; }
  const values = d.values || {};
  const defs = d.defaults || {};
  ['genshin','starrail','zzz','arknights','endfield','wuwa'].forEach(function(gid){
    const box = document.getElementById('resin-' + gid);
    if (!box) return;
    box.value = (values[gid] != null) ? values[gid] : '';
    box.placeholder = (defs[gid] != null) ? String(defs[gid]) : '0';
  });

  const enabled = d.enabled !== false;
  const box = document.getElementById('resin-toggle');
  if (box) box.checked = enabled;

  const item = document.getElementById('acc-resource');
  if (item) item.classList.toggle('is-off', !enabled);

  const hint = document.getElementById('resin-hint');
  if (hint) hint.textContent = '留空用默认值 · 0 = 关闭该游戏';
  refreshSetSummaries();
}

function loadThresholds(){
  fetch('/api/settings').then(function(r){ return r.json(); })
    .then(renderThresholds)
    .catch(function(){
      const hint = document.getElementById('resin-hint');
      if (hint) hint.textContent = '读取失败，请刷新重试';
    });
}

function resinPayload(){
  const box = document.getElementById('resin-toggle');
  return {
    genshin:   document.getElementById('resin-genshin').value,
    starrail:  document.getElementById('resin-starrail').value,
    zzz:       document.getElementById('resin-zzz').value,
    arknights: document.getElementById('resin-arknights').value,
    endfield:  document.getElementById('resin-endfield').value,
    wuwa:      document.getElementById('resin-wuwa').value,
    enabled:   box ? box.checked : true
  };
}

async function saveThresholds(){
  const btn = document.getElementById('btn-resin-save');
  if (!btn) return;
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '保存中…';
  try {
    const d = await postJSON('/api/settings', resinPayload());
    if (!d.ok) { toast(d.message || '保存失败', true); return; }
    renderThresholds(d);
    toast(d.message || '已保存');
  } catch (e) {
    toast('保存失败：' + e, true);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

async function toggleResourceAlert(){
  const box = document.getElementById('resin-toggle');
  if (!box) return;
  refreshSetSummaries();          // 开关一动，摘要立即跟随（死命令：同一事件只绑一次）
  const want = box.checked;
  box.disabled = true;
  try {
    // 顺带把输入框里的当前值一起提交，避免开关切换把用户刚改的数字丢掉
    const payload = resinPayload();
    payload.enabled = want;
    const d = await postJSON('/api/settings', payload);
    if (!d.ok) {
      box.checked = !want;
      toast(d.message || '设置失败', true);
      return;
    }
    renderThresholds(d);
    toast(want ? '已开启体力溢出提醒' : '已关闭体力溢出提醒（阈值已保留）');
  } catch (e) {
    box.checked = !want;
    toast('设置失败：' + e, true);
  } finally {
    box.disabled = false;
  }
}

document.getElementById('btn-resin-save').addEventListener('click', saveThresholds);

async function saveDevice(){
  const btn = document.getElementById('btn-dev-save');
  const text = document.getElementById('dev-text').value;
  if (!text.trim()) { toast('请先粘贴设备信息', true); return; }
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '保存中…';
  try {
    const d = await postJSON('/api/device', { text: text });
    if (!d.ok) { toast(d.message || '保存失败', true); return; }
    renderDevice(d);
    document.getElementById('dev-text').value = '';
    toast(d.message || '设备指纹已替换，请重新扫码');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (e) {
    toast('保存失败：' + e, true);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

async function resetDevice(){
  if (!confirm('删除手动粘贴的设备档案，回到随机机型？删掉后需要重新扫码。')) return;
  try {
    const d = await postJSON('/api/device/reset', {});
    if (!d.ok) { toast(d.message || '恢复失败', true); return; }
    renderDevice(d);
    toast(d.message || '已切回随机设备');
  } catch (e) {
    toast('恢复失败：' + e, true);
  }
}

async function shuffleDevice(){
  if (!confirm('换一台设备？会换掉机型和 OAID，旧指纹作废，下次查询用新的。')) return;
  const btn = document.getElementById('btn-dev-shuffle');
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '切换中…';
  try {
    const d = await postJSON('/api/device/shuffle', {});
    if (!d.ok) { toast(d.message || '换设备失败', true); return; }
    renderDevice(d);
    const p = d.profile || {};
    toast('已换到 ' + (p.brand || '') + ' ' + (p.model || '') + (d.label ? '（' + d.label + '）' : ''));
  } catch (e) {
    toast('换设备失败：' + e, true);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

/* =========================== 通知绑定 =========================== */
const CHANNELS = ['serverchan', 'qmsg'];

function hintText(it){
  if (!it.has_key && !it.enabled) return '未配置';
  if (!it.has_key) return '已启用，但没有 Key';
  if (!it.in_channels) return 'Key 已保存，未启用';
  return '已配置并生效';
}

/* 渠道列表名字右侧的状态指示：Key 输入框有值 → 绿点「已配置」，无值 → 灰点「未配置」。
   数据源是输入框本身：输入时实时变化，服务端数据落框后也走这里统一刷新。 */
function updateChState(name){
  const box = document.getElementById(name + '-key');
  const dotEl = document.getElementById('ch-dot-' + name);
  const stateEl = document.getElementById('ch-state-' + name);
  if (!box || !dotEl || !stateEl) return;
  const has = String(box.value || '').trim().length > 0;
  dotEl.className = 'ch-dot' + (has ? ' ok' : '');
  stateEl.textContent = has ? '已配置' : '未配置';
}

function renderPush(d){
  CHANNELS.forEach(function(name){
    const it = (d.items || {})[name] || {};
    const box = document.getElementById(name + '-key');
    const en = document.getElementById(name + '-enabled');
    const hint = document.getElementById(name + '-hint');
    if (!box || !en || !hint) return;
    box.dataset.masked = it.masked || '';
    box.value = it.masked || '';
    box.placeholder = it.has_key ? ''
      : (name === 'serverchan' ? 'SCT 开头的 SendKey' : 'Qmsg 酱的 key');
    en.checked = !!it.enabled;
    hint.innerHTML = hintText(it);
  });
  renderAlwaysNotify(d.always_notify);

  // 左栏渠道列表：名字右侧指示器 = 输入框是否有值（配置明细仍在编辑区 hint 里）
  CHANNELS.forEach(function(name){ updateChState(name); });
  refreshNotice();
}

function loadPush(){
  fetch('/api/push').then(function(r){ return r.json(); }).then(function(d){
    if (!d.ok) { toast(d.message || '读取配置失败', true); return; }
    renderPush(d);
  }).catch(function(){ toast('无法连接本地服务', true); });
}

/* ---------- 三区结构：渠道列表切换 + 最近推送记录 ---------- */
const CHANNEL_NAMES = {
  serverchan: 'Server酱（微信）',
  qmsg: 'Qmsg 酱（QQ）'
};
let selChannel = 'serverchan';

function selectChannel(name){
  if (!CHANNEL_NAMES[name]) return;
  selChannel = name;
  document.querySelectorAll('.ch-item').forEach(function(el){
    el.classList.toggle('on', el.dataset.ch === name);
  });
  document.getElementById('ed-serverchan').hidden = name !== 'serverchan';
  document.getElementById('ed-qmsg').hidden = name !== 'qmsg';
  // 标题固定为「通知绑定」（2026-11-06 用户要求）：当前渠道名由上方分段控件
  // 高亮显示，标题跟随渠道改名属于重复信息，也让卡片标题位置不断跳动。
  document.getElementById('ed-title').innerHTML =
    '通知绑定' +
    ' <span class="q" data-tip="出于安全，已保存的 Key 以掩码显示：不修改输入框即保持原值，清空后保存则删除该 Key"></span>';
}

document.querySelectorAll('.ch-item').forEach(function(el){
  el.addEventListener('click', function(){ selectChannel(this.dataset.ch); });
});

/* 最近推送记录：读 data/push_history.json（notifier 每次真实发送——成功/失败——
   都落一条结构化记录）。不再解析纯文本日志：console 与 daemon 的日志分家
   （desktop.log / dailycheck.log），按文件筛正则永远可能漏。 */
const PUSH_LABELS = { serverchan: 'Server酱', qmsg: 'Qmsg 酱', bark: 'Bark' };

/* 判断哪些格子的内容真的被裁掉了：只有它们才显示展开箭头。
   注意正文用 -webkit-line-clamp 折叠，被裁的是正文元素自身，
   父级 .pv-item 的 scrollHeight 不一定溢出，所以三个来源都要看。 */
function markTruncatedPush(box){
  Array.prototype.forEach.call(box.querySelectorAll('.pv-item'), function(el){
    const b = el.querySelector('.pv-body');
    const m = el.querySelector('.pv-meta');
    const cut = (b && b.scrollHeight > b.clientHeight + 1) ||
                (m && m.scrollWidth > m.clientWidth + 1) ||
                (el.scrollHeight > el.clientHeight + 1);
    if (cut) el.classList.add('has-more');
  });
}

function loadPushRecent(){
  const box = document.getElementById('pv-recent');
  if (!box) return;
  fetch('/api/push_history').then(function(r){ return r.json(); })
    .then(function(d){
      const items = (d && d.items) || [];
      if (!d || d.ok === false || !items.length) {
        box.innerHTML = '<div class="pv-empty">暂无数据。</div>';
        return;
      }
      box.innerHTML = items.map(function(it){
        const ch = PUSH_LABELS[it.channel] || it.channel || '未知渠道';
        const ok = !!it.ok;
        // 主内容显示推送标题+正文预览（老记录两者皆无，回退 message）。
        // 失败行把错误原因带上：有 title 用「标题（原因）」，没有就直接给原因。
        const text = ok ? (it.title || it.message || '推送成功')
                        : (it.title ? it.title + '（' + (it.message || '未知原因') + '）'
                                    : (it.message || '推送失败'));
        const bodyLine = String(it.body || '').replace(/\s+$/, '');
        return '<div class="pv-item">' +
          '<div class="pv-meta"><span class="' + (ok ? 'i-ok' : 'i-no') + '">' +
            (ok ? '✓' : '✗') + '</span> ' +
            escHtml(String(it.ts || '').replace(/^\d{4}-/, '')) + ' · ' +
            escHtml(ch) + ' · ' + escHtml(text) + '</div>' +
          (bodyLine ? '<div class="pv-body">' + escHtml(bodyLine) + '</div>' : '') +
          '<button type="button" class="pv-toggle" aria-expanded="false" title="展开全文"></button>' +
          '</div>';
      }).join('');
      // 折叠高度固定，只有真被裁掉的条目才给展开箭头（最多 8 条，量一次即可）
      requestAnimationFrame(function(){ markTruncatedPush(box); });
    })
    .catch(function(){ box.innerHTML = '<div class="pv-empty">读取推送历史失败，请稍后重试。</div>'; });
}

/* 展开/收起：点格子右上角箭头切换；内容没被裁的条目不会出现箭头 */
(function bindPushToggle(){
  const box = document.getElementById('pv-recent');
  if (!box) return;
  box.addEventListener('click', function(e){
    const btn = e.target.closest ? e.target.closest('.pv-toggle') : null;
    if (!btn) return;
    const item = btn.closest('.pv-item');
    if (!item) return;
    const open = item.classList.toggle('open');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.title = open ? '收起' : '展开全文';
  });
})();

function collectPush(){
  const out = {};
  CHANNELS.forEach(function(name){
    const box = document.getElementById(name + '-key');
    const en = document.getElementById(name + '-enabled');
    out[name] = { enabled: en.checked, key: box.value };
  });
  const an = document.getElementById('always-notify');
  if (an) out.always_notify = an.checked;
  return out;
}

async function savePush(){
  const btn = document.getElementById('btn-save');
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = '保存中…';
  try {
    const d = await postJSON('/api/push', collectPush());
    if (!d.ok) { toast(d.message || '保存失败', true); return; }
    renderPush(d);
    document.getElementById('test-result').hidden = true;
    toast(d.message || '保存成功');
  } catch (e) {
    toast('保存失败：' + e, true);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

/* ---------- 完成也推送（开关已迁到「定时与状态 → 开机与提醒行为」卡
   acc-alwaysnotify；id 保持 always-notify：collectPush/test_push 与 renderPush
   回填零改动）。开关自带即时保存：update_push_config 对缺省通道"完全不动"，
   只带 always_notify 的部分 payload 是官方支持的用法。 ---------- */
function renderAlwaysNotify(v){
  const an = document.getElementById('always-notify');
  if (!an) return;
  an.checked = !!v;
  setSum('alwaysnotify', an.checked ? '已开启' : '未开启',
         an.checked ? '全部完成也会推送一条平安通知' : '仅在存在未完成项时推送');
}

function loadAlwaysNotify(){
  fetch('/api/push').then(function(r){ return r.json(); }).then(function(d){
    if (d.ok) renderAlwaysNotify(d.always_notify);
  }).catch(function(){ /* 静默：/api/push 失败时 loadPush 的 toast 已报错，本摘要等任一次成功渲染跟进 */ });
}

const anSelfSave = document.getElementById('always-notify');
if (anSelfSave) anSelfSave.addEventListener('change', async function(){
  const want = this.checked;
  this.disabled = true;
  try {
    const d = await postJSON('/api/push', { always_notify: want });
    if (!d.ok) { this.checked = !want; toast(d.message || '设置失败', true); return; }
    renderAlwaysNotify(this.checked);
    toast(want ? '已开启：全部完成也推送平安通知' : '已关闭：仅在存在未完成项时推送');
  } catch (e) {
    this.checked = !want;
    toast('设置失败：' + e, true);
  } finally {
    this.disabled = false;
  }
});

/* ---------- 免打扰时段（acc-quiet，定时页开机与提醒卡）：窗口内推送
   顺延到时段结束（触发条件会自然重现，明早自动补推）。开关即时保存，
   时段两个输入框走「保存时段」按钮一起提交。 ---------- */
function renderQuiet(enabled, start, end){
  const box = document.getElementById('quiet-toggle');
  if (!box) return;
  box.checked = !!enabled;
  const s = document.getElementById('quiet-start');
  const e = document.getElementById('quiet-end');
  if (s && document.activeElement !== s) s.value = start || '23:00';
  if (e && document.activeElement !== e) e.value = end || '08:00';
  setSum('quiet', enabled ? ((start || '23:00') + '-' + (end || '08:00')) : '已关闭',
         enabled ? '时段内推送顺延，明早自动补推' : '仅在存在未完成项时推送');
}

function loadQuiet(){
  fetch('/api/push').then(function(r){ return r.json(); }).then(function(d){
    if (d.ok) renderQuiet(d.quiet_enabled, d.quiet_start, d.quiet_end);
  }).catch(function(){ /* 静默：/api/push 失败时其它通道的 toast 已报错 */ });
}

async function toggleQuiet(){
  const box = document.getElementById('quiet-toggle');
  const want = box.checked;
  box.disabled = true;
  try {
    const d = await postJSON('/api/push', { quiet_enabled: want });
    if (!d.ok) { box.checked = !want; toast(d.message || '设置失败', true); return; }
    renderQuiet(d.quiet_enabled, d.quiet_start, d.quiet_end);
    toast(want ? '已开启免打扰：时段内推送顺延' : '已关闭免打扰');
  } catch (e) {
    box.checked = !want;
    toast('设置失败：' + e, true);
  } finally {
    box.disabled = false;
  }
}

async function saveQuiet(){
  const btn = document.getElementById('btn-quiet-save');
  const s = document.getElementById('quiet-start').value;
  const e = document.getElementById('quiet-end').value;
  btn.disabled = true;
  btn.textContent = '保存中…';
  try {
    const d = await postJSON('/api/push', { quiet_start: s, quiet_end: e });
    if (!d.ok) { toast(d.message || '设置失败', true); return; }
    renderQuiet(d.quiet_enabled, d.quiet_start, d.quiet_end);
    toast('免打扰时段已更新：' + d.quiet_start + '-' + d.quiet_end);
  } catch (e) {
    toast('设置失败：' + e, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '保存时段';
  }
}

const quietToggle = document.getElementById('quiet-toggle');
if (quietToggle) quietToggle.addEventListener('change', toggleQuiet);
const quietSave = document.getElementById('btn-quiet-save');
if (quietSave) quietSave.addEventListener('click', saveQuiet);

async function testPush(){
  const btn = document.getElementById('btn-test');
  const box = document.getElementById('test-result');
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = '发送中…';
  box.hidden = true;
  try {
    const d = await postJSON('/api/test_push', collectPush());
    if (!d.ok) { toast(d.message || '发送失败', true); return; }
    const rows = (d.results || []).map(function(x){
      return '<div><span class="' + (x.ok ? 'i-ok' : 'i-no') + '">' +
             (x.ok ? '✓' : '✗') + '</span>' + escHtml(x.label) + ' — ' + escHtml(x.message) + '</div>';
    }).join('');
    box.innerHTML = '<div class="res">' + rows + '</div>';
    box.hidden = false;
    const allOk = (d.results || []).every(function(x){ return x.ok; });
    toast(allOk ? '测试消息已发出' : '部分通道发送失败', !allOk);
  } catch (e) {
    toast('发送失败：' + e, true);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

CHANNELS.forEach(function(name){
  const box = document.getElementById(name + '-key');
  if (!box) return;
  /* 死命令：只允许 addEventListener。Tab 切换只切 display 不重建 DOM，
     这段在页面加载时只执行一次；dataset 闸门兜底，重复调用不会叠加监听器 */
  if (box.dataset.chBound) return;
  box.dataset.chBound = '1';
  box.addEventListener('focus', function(){
    if (box.value && box.value === box.dataset.masked) box.select();
  });
  box.addEventListener('input', function(){ updateChState(name); });
  box.addEventListener('change', function(){ updateChState(name); });
});

document.getElementById('btn-save').addEventListener('click', savePush);
document.getElementById('btn-test').addEventListener('click', testPush);

/* =========================== 定时与状态 =========================== */
/* 重排避让：拖动圆点期间（以及松手后等保存往返的 1.5 秒）跳过轮询重排。
   用时刻而非布尔标志——即使 pointerup 丢失也会在 30 秒内自过期，不会把页面冻死。 */
var rlDragBeginAt = 0;          // >0 = 正在拖动，值为起拖时刻
var rlDropGraceUntil = 0;       // 松手后的短暂避让截止时刻
function rlRenderHeld(){
  if (rlDragBeginAt) {
    if (Date.now() - rlDragBeginAt < 30000) return true;
    rlDragBeginAt = 0;          // 僵死状态，放行
  }
  return Date.now() < rlDropGraceUntil;
}
function renderDaemon(st){
  const dot = document.getElementById('d-dot');
  const badge = document.getElementById('d-state');
  const running = !!st.running;
  /* 心跳点两态：绿点呼吸 = 运行，灰点静止 = 停止（底色交给 .dot/.dot.on，不再写内联） */
  dot.className = 'dot' + (running ? ' on' : '');
  badge.textContent = running ? '运行中' : '已停止';
  badge.className = 'badge' + (running ? '' : ' err');

  hbState.running = running;
  hbState.startedAtRaw = String(st.started_at || '');
  hbState.startedAt = parseStamp(hbState.startedAtRaw);
  hbState.times = Array.isArray(st.check_times) ? st.check_times.slice() : [];

  /* PID 从行内撤下（对用户无意义），悬浮「运行中」徽章可看——排障入口保留 */
  badge.title = st.pid ? 'PID ' + st.pid : '';
  const heart = document.getElementById('d-heart');
  if (heart) {
    heart.textContent = (st.heartbeat_age != null)
      ? Math.round(st.heartbeat_age) + ' 秒前' : '-';
  }

  const last = st.last_run || {};
  document.getElementById('d-last').textContent =
    last.at ? (last.exit === 0 ? '成功' : '退出码 ' + last.exit) + ' · ' + shortStamp(last.at)
            : '还没跑过';
  document.getElementById('btn-daemon-start').disabled = running;
  document.getElementById('btn-daemon-stop').disabled = !running;

  /* 心跳显示统一入口：按状态起/停 timer，并立即刷新一次
     （从暂停恢复为运行时马上出数，不等下一个 30 秒 tick） */
  if (running) startHeartbeat(); else stopHeartbeat();
  tickHeartbeat();
}

function shortStamp(text){
  // "2026-10-27 01:23:43" -> "10-27 01:23"
  const m = String(text || '').match(/^\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return m ? (m[1] + ' ' + m[2]) : String(text || '');
}

function renderSchedule(d, force){
  if (!d.ok) { toast(d.message || '读取失败', true); return; }
  /* 拖动圆点期间不许重排：定时页每 3 秒轮询一次，renderRuler 是整块 innerHTML 重建，
     手里那个圆点节点会被摘掉，新画的圆点用的是服务端还没保存的旧时间 —— 表现为
     「拖一两秒就闪回原位」。这里直接跳过整次渲染；force=true 只给保存成功后那次用。
     用时间戳而非布尔标志：万一没收到 pointerup 也不会把整页冻死（30 秒自过期）。 */
  if (!force && rlRenderHeld()) return;
  const times = d.times || [];
  const box = document.getElementById('d-times');
  if (!times.length) {
    box.innerHTML = '<span class="chip empty">还没有时间点</span>';
  } else {
    box.innerHTML = times.map(function(t){
      return '<span class="chip">' +
        '<span class="chip-time" data-edit="' + t + '" title="点击修改时间">' + t + '</span>' +
        '<button title="删除" data-remove="' + t + '">×</button></span>';
    }).join('');
  }
  /* cron-job.org「next runs」范式：今天还剩哪几轮由服务端算好带来
     （today_next，服务器时钟口径）；字段缺失（后端未换血）时静默隐藏 */
  const tn = document.getElementById('today-next');
  if (tn) {
    if (!d.today_next) {
      tn.hidden = true;
    } else if (d.today_next.length) {
      tn.textContent = '今天还有 ' + d.today_next.length + ' 轮：' + d.today_next.join('、');
      tn.hidden = false;
    } else {
      tn.textContent = '今天的时间点都已跑完';
      tn.hidden = false;
    }
  }
  renderRuler(times);
  document.getElementById('d-count').textContent = times.length + ' 个';
  /* 下次提醒：守护进程停止时与 KPI 保持一致显示「已暂停」；
     时间只保留 月-日 时:分（年份对「下次」没有信息量，纯占宽） */
  document.getElementById('d-next').textContent =
    (d.daemon && d.daemon.running) ? (d.next_run || '-').replace(/^\d{4}-/, '') : '已暂停';

  // 参与检查的账号：只给数量，完整清单走 title（来自后端，不写死）
  const gamesBox = document.getElementById('d-games');
  if (gamesBox) {
    const infos = d.accounts_info || [];
    if (!infos.length) {
      gamesBox.textContent = '无';
      gamesBox.title = 'config.json 里 accounts 的 enabled 全为 false';
    } else {
      gamesBox.textContent = infos.length + ' 个';
      gamesBox.title = infos.map(function(it){
        return (it.name || it.id) + (it.nickname ? '（' + it.nickname + '）' : '');
      }).join('\n');
    }
  }

  renderCatchUp(d);
  renderDaemon(d.daemon || {});
  renderActivitySwitch(d.activity_alert_enabled, d.activity_alert_days);
}

/* 活动结束提醒：config.app.activity_alert_enabled / activity_alert_days */
function renderActivitySwitch(enabled, days){
  const on = enabled !== false;
  const box = document.getElementById('activity-toggle');
  if (box) box.checked = on;
  const item = document.getElementById('acc-activity');
  if (item) item.classList.toggle('is-off', !on);
  const num = parseInt(days, 10);
  const input = document.getElementById('activity-days');
  if (input && document.activeElement !== input && !isNaN(num)) input.value = num;
  refreshSetSummaries();
}

async function toggleActivityAlert(){
  const box = document.getElementById('activity-toggle');
  if (!box) return;
  refreshSetSummaries();          // 开关一动，摘要立即跟随（死命令：同一事件只绑一次）
  const want = box.checked;
  box.disabled = true;
  try {
    // 同 catch_up：开关提交时把天数输入框的当前值带上（非法值留在框里由保存按钮兜）
    const acInput = document.getElementById('activity-days');
    const acParsed = acInput && acInput.value.trim() !== '' ? parseInt(acInput.value, 10) : NaN;
    const acPayload = { enabled: want };
    if (!isNaN(acParsed)) acPayload.days = acParsed;
    const d = await postJSON('/api/activity_alert', acPayload);
    if (!d.ok) { box.checked = !want; toast(d.message || '设置失败', true); return; }
    renderActivitySwitch(d.activity_alert_enabled);
    toast(want ? '已开启活动结束提醒' : '已关闭活动结束提醒');
  } catch (e) {
    box.checked = !want;
    toast('设置失败：' + e, true);
  } finally {
    box.disabled = false;
  }
}

/* 补发：开关 + 窗口数值分别对应 config.app.catch_up_enabled / catch_up_minutes */
let lastCatchUp = { enabled: true, minutes: 180 };

function renderCatchUp(d){
  d = d || {};
  const enabled = d.catch_up_enabled !== false;
  const num = parseInt(d.catch_up_minutes, 10);
  const minutes = isNaN(num) ? 180 : num;
  lastCatchUp = { enabled: enabled, minutes: minutes };

  const input = document.getElementById('catch-up-minutes');
  if (input && document.activeElement !== input) input.value = minutes;

  const box = document.getElementById('catch-up-toggle');
  if (box) box.checked = enabled;

  const item = document.getElementById('acc-catchup');
  if (item) item.classList.toggle('is-off', !enabled);
  refreshSetSummaries();
}

async function toggleCatchUp(){
  const box = document.getElementById('catch-up-toggle');
  if (!box) return;
  refreshSetSummaries();          // 开关一动，摘要立即跟随（死命令：同一事件只绑一次）
  const want = box.checked;
  box.disabled = true;
  try {
    // 顺带把输入框里的当前值一起提交（resin 开关同款口径），别让拨开关吞掉刚输入的数字
    const cuInput = document.getElementById('catch-up-minutes');
    const cuParsed = cuInput && cuInput.value.trim() !== '' ? parseInt(cuInput.value, 10) : NaN;
    const d = await postJSON('/api/catch_up',
      { minutes: isNaN(cuParsed) ? lastCatchUp.minutes : cuParsed, enabled: want });
    if (!d.ok) {
      box.checked = !want;
      toast(d.message || '设置失败', true);
      return;
    }
    renderCatchUp(d);
    toast(want ? '已开启休眠提醒' : '已关闭休眠提醒（窗口数值已保留）');
  } catch (e) {
    box.checked = !want;
    toast('设置失败：' + e, true);
  } finally {
    box.disabled = false;
  }
}

async function saveCatchUp(){
  const btn = document.getElementById('btn-catch-up');
  const input = document.getElementById('catch-up-minutes');
  const raw = parseInt(input.value, 10);
  /* 空值发 null（服务端语义=回默认 180），不翻译成 0——
     0 是「显式不补发」，与「没填」是两种用户意图，混译会静默关闭补发 */
  let minutes = isNaN(raw) ? null : raw;
  if (minutes != null && minutes < 0) minutes = 0;
  if (minutes != null && minutes > 720) minutes = 720;
  btn.disabled = true;
  try {
    const d = await postJSON('/api/catch_up',
      { minutes: minutes, enabled: lastCatchUp.enabled });
    if (!d.ok) { toast(d.message || '保存失败', true); return; }
    renderCatchUp(d);
    toast(d.message || '已保存');
  } catch (e) {
    toast('保存失败：' + e, true);
  } finally {
    btn.disabled = false;
  }
}

function loadSchedule(){
  fetch('/api/schedule').then(function(r){ return r.json(); })
    .then(renderSchedule)
    .catch(function(){
      /* 持续失败给真状态（3 秒轮询自愈成功后由 renderSchedule 正常回写，不 toast 防刷屏） */
      const st = document.getElementById('d-state');
      if (st) { st.textContent = '连接失败'; st.className = 'badge err'; }
      const tn = document.getElementById('today-next');
      if (tn) tn.hidden = true;
    });
}

/* ========================= 开机自启 ========================= */
function renderAutostart(d){
  const box = document.getElementById('autostart-toggle');
  const badge = document.getElementById('autostart-state');
  const hint = document.getElementById('autostart-hint');
  const supported = !!(d && d.supported);

  if (box) {
    box.checked = !!(d && d.enabled);
    box.disabled = !supported;
  }
  if (badge) {
    /* 2026-11-01 起该元素从 badge 徽章改为 set-sum 值列（与其他四行同款右对齐） */
    if (!supported) {
      badge.textContent = '不支持';
      badge.className = 'set-sum err';
    } else if (d.stale) {
      badge.textContent = '路径已失效';
      badge.className = 'set-sum err';
    } else if (d.enabled) {
      badge.textContent = '已开启';
      badge.className = 'set-sum';
    } else {
      badge.textContent = '未开启';
      badge.className = 'set-sum';
    }
  }
  if (hint) {
    if (!supported) {
      hint.hidden = false;
      hint.textContent = (d && d.message) || '仅支持 Windows';
    } else if (d.stale) {
      hint.hidden = false;
      hint.innerHTML = '注册表路径已失效：把开关关掉再打开一次即可刷新。';
    } else {
      /* 正常态无增量信息（副标题已说明），不再占一行 */
      hint.hidden = true;
    }
  }
  refreshSetSummaries();
}

function loadAutostart(){
  fetch('/api/autostart').then(function(r){ return r.json(); })
    .then(renderAutostart)
    .catch(function(){
      const hint = document.getElementById('autostart-hint');
      if (hint) hint.textContent = '读取自启状态失败，刷新页面重试';
    });
}

async function toggleAutostart(){
  const box = document.getElementById('autostart-toggle');
  if (!box) return;
  refreshSetSummaries();          // 开关一动，摘要立即跟随（死命令：同一事件只绑一次）
  const want = box.checked;
  box.disabled = true;
  try {
    const d = await postJSON('/api/autostart', { enable: want });
    if (!d.ok) {
      box.checked = !want;          // 失败就把开关拨回去，别让界面撒谎
      toast(d.message || '设置失败', true);
      renderAutostart(d);
      return;
    }
    renderAutostart(d);
    toast(d.message || (want ? '已开启开机自启' : '已关闭开机自启'));
  } catch (e) {
    box.checked = !want;
    toast('设置失败：' + e, true);
  } finally {
    box.disabled = false;
  }
}

document.getElementById('autostart-toggle')
  .addEventListener('change', toggleAutostart);

/* 抽屉头里的开关：点击只拨开关，不折叠抽屉（2026-11-06）。此前开关嵌在
   <summary> 里，展开阅读时点落到右侧开关区 = 误拨开关并直接写配置/注册表
   ——「开机自启每次打开变关闭」的根因。stopPropagation 后点开关不收抽屉，
   点行其他位置才展开/收起。 */
document.querySelectorAll('.set-head .toggle').forEach(function(label){
  label.addEventListener('click', function(e){ e.stopPropagation(); });
});

document.getElementById('catch-up-toggle')
  .addEventListener('change', toggleCatchUp);

/* 活动提醒提前天数：单独保存，不碰总开关 */
async function saveActivityDays(){
  const input = document.getElementById('activity-days');
  const btn = document.getElementById('btn-activity-days');
  if (!input || !btn) return;
  const raw = input.value;
  const num = parseInt(raw, 10);
  if (isNaN(num) || num < 1 || num > 30) {
    toast('请填 1–30 之间的整数', true);
    return;
  }
  btn.disabled = true;
  try {
    const d = await postJSON('/api/activity_alert', { days: num });
    if (!d.ok) { toast(d.message || '保存失败', true); return; }
    if (document.activeElement !== input) input.value = d.activity_alert_days;
    refreshSetSummaries();
    toast(d.message || ('活动结束前 ' + d.activity_alert_days + ' 天开始提醒'));
  } catch (e) {
    toast('保存失败：' + e, true);
  } finally {
    btn.disabled = false;
  }
}

document.getElementById('btn-activity-days')
  .addEventListener('click', saveActivityDays);
document.getElementById('resin-toggle')
  .addEventListener('change', toggleResourceAlert);
document.getElementById('activity-toggle')
  .addEventListener('change', toggleActivityAlert);

/* ========================= 折叠面板参数摘要 =========================
   每个设置行标题右侧的 11px 摘要，数据永远取当前 DOM（开关 + 输入框）。
   三类时机会刷新：
   ① 用户改动：下方 input/change 监听（一次性绑定，dataset 闸门防叠加）
   ② 后端数据渲染完：renderAutostart / renderThresholds / renderCatchUp /
      renderActivitySwitch 末尾各调一次
   ③ 页面初始化：模块末尾立即调一次，未拿到后端数据时诚实显示「读取中…」 */
function setSum(id, text, full){
  const el = document.getElementById('sum-' + id);
  if (!el) return;
  el.textContent = text;
  el.title = full || text;   // 紧凑文案/截断时，悬停看全量
}

function refreshSetSummaries(){
  /* 2026-11-01 裁决：折叠态值列统一只显示开关状态词（已开启/未开启），
     五行同宽保持整齐；参数摘要退到 title 悬浮，具体数值在抽屉输入框里 */
  const cuBox = document.getElementById('catch-up-toggle');
  const cuMin = document.getElementById('catch-up-minutes');
  const cuLoaded = cuMin && String(cuMin.value).trim() !== '';
  if (!cuLoaded) setSum('catchup', '读取中…');
  else if (cuBox && !cuBox.checked) setSum('catchup', '未开启');
  else {
    const v = parseInt(cuMin.value, 10);
    setSum('catchup', '已开启',
      !isNaN(v) && v > 0 ? '补发窗口 ' + v + ' 分钟' : '不补发');
  }

  const rsBox = document.getElementById('resin-toggle');
  const rsInputs = ['genshin', 'starrail', 'zzz', 'arknights', 'endfield', 'wuwa']
    .map(function(g){ return document.getElementById('resin-' + g); });
  const rsLoaded = rsInputs.some(function(inp){ return inp && String(inp.value).trim() !== ''; });
  if (!rsLoaded) setSum('resin', '读取中…');
  else if (rsBox && !rsBox.checked) setSum('resin', '未开启');
  else {
    const labeled = [];
    [['genshin','原神'],['starrail','星铁'],['zzz','绝区零'],
     ['arknights','方舟'],['endfield','终末地'],['wuwa','鸣潮']].forEach(function(r){
      const inp = document.getElementById('resin-' + r[0]);
      const v = inp ? parseInt(inp.value, 10) : NaN;
      labeled.push(r[1] + ' ' + (isNaN(v) ? '–' : (v === 0 ? '关' : v)));
    });
    setSum('resin', '已开启', labeled.join(' · '));
  }

  const acBox = document.getElementById('activity-toggle');
  const acDays = document.getElementById('activity-days');
  const acLoaded = acDays && String(acDays.value).trim() !== '';
  if (!acLoaded) setSum('activity', '读取中…');
  else if (acBox && !acBox.checked) setSum('activity', '未开启');
  else {
    const v = parseInt(acDays.value, 10);
    setSum('activity', '已开启',
      !isNaN(v) && v > 0 ? '提前 ' + v + ' 天' : '未设置天数');
  }
}

/* 摘要跟随所有改动：7 个数字输入（input+change）。
   4 个开关的 change 已由既有启停处理函数持有（toggleAutostart 等），
   按死命令「同一事件只绑一次」，摘要刷新并入了那些函数的开头，这里不再绑。
   页面 DOM 是静态的、Tab 切换只切 display 不重建，这里只会执行一次；
   dataset 闸门再兜底，重复调用不会叠加监听器 */
(function bindSetSummaries(){
  ['catch-up-minutes', 'activity-days', 'resin-genshin', 'resin-starrail',
   'resin-zzz', 'resin-arknights', 'resin-endfield', 'resin-wuwa'].forEach(function(id){
    const el = document.getElementById(id);
    if (!el || el.dataset.sumBound) return;
    el.dataset.sumBound = '1';
    el.addEventListener('input', refreshSetSummaries);
    el.addEventListener('change', refreshSetSummaries);
  });
  /* 首屏：立即按当前 DOM 画一版（此刻多半是「读取中…」，
     后端数据到手后由 render*() 末尾的刷新接管） */
  refreshSetSummaries();
})();
document.getElementById('btn-live-refresh')
  .addEventListener('click', function(){ loadLive(true); });
document.getElementById('btn-act-refresh')
  .addEventListener('click', refreshActivities);
document.getElementById('btn-push-refresh')
  .addEventListener('click', loadPushRecent);
/* 活动卡片是动态渲染的，删除按钮与抽屉把手用事件委托挂在容器上 */
document.getElementById('act-list').addEventListener('click', function(e){
  if (!e.target.closest) return;
  const handle = e.target.closest('.act-drawer-handle');
  if (handle) {                                   // 「已完成」抽屉：点击展开 / 再点收起
    const drawer = handle.closest('.act-drawer');
    if (drawer) drawer.classList.toggle('open');
    return;
  }
  const btn = e.target.closest('.act-del');
  if (btn) { e.preventDefault(); deleteActivity(btn); }
});

/* 折叠面板标题栏里的开关 / 小按钮：只做自己的事，不连带展开或收起面板。 */
document.querySelectorAll('.set-head .toggle, .set-head .mini, .set-head .btn')
  .forEach(function(el){
    el.addEventListener('click', function(e){ e.stopPropagation(); });
  });

async function saveTimes(times){
  try {
    const d = await postJSON('/api/schedule', { times: times });
    if (!d.ok) { toast(d.message || '保存失败', true); return false; }
    renderSchedule(d, true);      // 保存结果是权威数据，无视拖动避让
    toast(d.message || '已保存');
    return true;
  } catch (e) {
    toast('保存失败：' + e, true);
    return false;
  }
}

async function addTime(){
  const input = document.getElementById('d-new-time');
  const value = (input.value || '').trim();
  if (!value) { toast('请先选择时间', true); return; }
  const current = currentTimes();
  if (current.indexOf(value) >= 0) { toast('这个时间点已经有了', true); return; }
  await saveTimes(current.concat([value]));
}

function currentTimes(){
  return [...document.querySelectorAll('#d-times .chip-time')]
    .map(function(el){ return el.textContent.trim(); })
    .filter(function(t){ return /^\d{1,2}:\d{2}$/.test(t); });
}

document.getElementById('d-times').addEventListener('click', async function(e){
  const edit = e.target.closest('[data-edit]');
  if (edit) { rlOpenAddTime(edit.dataset.edit, edit.dataset.edit); return; }
  const btn = e.target.closest('[data-remove]');
  if (!btn) return;
  const target = btn.dataset.remove;
  const current = currentTimes();
  if (current.length <= 1) { toast('至少要保留一个时间点', true); return; }
  await saveTimes(current.filter(function(t){ return t !== target; }));
});

document.getElementById('btn-add-time').addEventListener('click', addTime);
/* 时间轴点击落点：点击位置换算成分钟（0–1439，四舍五入到整分），
   写入时间输入框后调 addTime() —— 与「添加」按钮共用同一条去重 + 保存链路，
   点在已有圆点上会得到「这个时间点已经有了」提示，不会误删。 */
/* 拖完圆点后浏览器会补一次 click（不当成新增）。这里用「截止时间戳」而不是
   dataset 持久标志：标志一旦留下而那次 click 没来（拖到标尺外松手等），
   会永久吞掉之后所有点击，表现为「点时间轴没反应」——时间戳会自行过期。 */
var rlSuppressClickUntil = 0;
(function bindRulerClick(){
  const ruler = document.getElementById('d-ruler');
  if (!ruler || ruler.dataset.rulerBound) return;
  ruler.dataset.rulerBound = '1';
  ruler.addEventListener('click', function(e){
    if (Date.now() < rlSuppressClickUntil) { rlSuppressClickUntil = 0; return; }
    const dot = e.target.closest('.ruler-dot');
    if (dot) { rlSelect(dot.dataset.t); return; }   // 点圆点 = 选中（交给 ▲▼ / 拖动）
    const rect = ruler.getBoundingClientRect();
    if (!rect.width) return;
    selTime = null; moveFrom = null; rlPaintSel();   // 空白处点击 = 新增，立即清掉选中态
    const mins = Math.round((e.clientX - rect.left) / rect.width * 1440);
    const clamped = Math.min(1439, Math.max(0, mins));
    rlOpenAddTime(rlMins2Str(clamped));   // 先弹确认框（框内可微调），确定后才真正添加
  });
})();

/* ---- 选中 / 拖动改点 / ▲▼ 微调（保存仍走 saveTimes 同一条链路） ---- */
let selTime = null;      // 当前选中的时间点（HH:MM），null = 未选中
let moveFrom = null;     // 本次编辑前该点在列表里的原值（保存成功后跟进）
let rlEditFrom = null;   // 编辑已有时间点时的来源值；null = 纯新增
function rlMins2Str(m){
  const v = ((Math.round(m) % 1440) + 1440) % 1440;   // 跨 0 点回绕
  return String(Math.floor(v / 60)).padStart(2, '0') + ':' + String(v % 60).padStart(2, '0');
}
function rlStr2Mins(s){
  const m = String(s || '').match(/^(\d{1,2}):(\d{2})$/);
  return m ? (+m[1] * 60 + +m[2]) : null;
}
function rlPaintSel(){
  document.querySelectorAll('#d-ruler .ruler-dot').forEach(function(el){
    el.classList.toggle('sel', el.dataset.t === selTime);
  });
}
function rlSelect(t){
  selTime = (selTime === t) ? null : t;
  if (selTime == null) { moveFrom = null; rlPaintSel(); return; }
  moveFrom = t;
  const input = document.getElementById('d-new-time');
  if (input) input.value = t;
  rlPaintSel();
  toast('已选中 ' + t + '：▲▼ 微调 1 分钟，或左右拖动圆点');
}

/* 拖动圆点改时间。24 小时铺在 ~600px 上，1px≈2.4 分钟，不做吸附就会每个像素乱跳，
   这是「手感差」的根因。做法对齐几个成熟实现：
     · noUiSlider 的 step 吸附：值吸附到 5 分钟网格；按住 Shift 切 1 分钟精调
     · vis-timeline 的 tooltipOnItemUpdateTime：拖动中跟出大号时间气泡 + 竖向指示线
     · Premiere / Flick 的修饰键切精度与 Esc 取消
   位置本身连续跟手（拖动中不做取整），只有「值」吸附，眼睛看到的是稳定数字；
   拖到别的已有点上气泡变红标「已有」，松手不落库。 */
(function bindRulerDrag(){
  const ruler = document.getElementById('d-ruler');
  if (!ruler || ruler.dataset.dragBound) return;
  ruler.dataset.dragBound = '1';

  const STEP = 5;        // 默认吸附 5 分钟
  const STEP_FINE = 1;   // Shift 精调
  const DEAD = 3;        // 起拖阈值
  let drag = null;

  function pctOfMins(m){ return (m / 1440 * 100).toFixed(3) + '%'; }
  /* 网格吸附；但吸附不许「反向」——已有的 06:46 / 23:59 往右拖时，
     不能被就近网格拽回到 06:45 / 23:55 */
  function snapMins(m, fine, fromM){
    const step = fine ? STEP_FINE : STEP;
    const max = 1440 - step;                       // 5 分钟档最高 23:55
    const v = Math.min(max, Math.max(0, Math.round(m / step) * step));
    if (fromM != null && (v - fromM) * (m - fromM) < 0) return fromM;
    return v;
  }
  function ghostBox(){
    let g = ruler.querySelector('.ruler-ghost');
    if (!g) {
      g = document.createElement('div');
      g.className = 'ruler-ghost';
      g.innerHTML = '<b><span class="t"></span><em></em></b>';
      ruler.appendChild(g);
    }
    return g;
  }
  /* 气泡贴着标尺边缘时往内推，避免溢出卡片；竖向指示线始终指向真实位置 */
  function placeGhost(g, pct, raw){
    g.style.setProperty('--gx', pct);
    const b = g.querySelector('b');
    const bw = b.offsetWidth || 0;
    if (!bw) return;
    const cx = raw / 1440 * drag.width;
    let over = 0;
    if (cx - bw / 2 < 0) over = bw / 2 - cx;
    else if (cx + bw / 2 > drag.width) over = drag.width - cx - bw / 2;
    b.style.transform = 'translateX(calc(-50% + ' + over.toFixed(1) + 'px))';
  }
  function dropGhost(){
    const g = ruler.querySelector('.ruler-ghost');
    if (g) g.remove();
  }
  /* 收尾：清类名、去气泡。圆点位置不在这里回退——成功落库要停在松手处，
     碰撞/取消两条路径都由随后的 renderRuler(currentTimes()) 归位 */
  function settle(){
    const d = drag; drag = null;
    rlDragBeginAt = 0;                          // 解除重排避让，给保存往返留 1.5 秒缓冲
    rlDropGraceUntil = Date.now() + 1500;
    ruler.classList.remove('ruler--dragging');
    document.body.classList.remove('rl-dragging');
    dropGhost();
    if (d) { d.dot.classList.remove('dragging'); d.dot.classList.remove('bad'); }
    return d;
  }

  ruler.addEventListener('pointerdown', function(e){
    if (e.button !== 0) return;
    const dot = e.target.closest('.ruler-dot');
    if (!dot) return;
    const r = ruler.getBoundingClientRect();
    if (!r.width) return;
    const dr = dot.getBoundingClientRect();
    const others = currentTimes().filter(function(t){ return t !== dot.dataset.t; });
    drag = {
      dot: dot,
      from: dot.dataset.t,
      fromMins: rlStr2Mins(dot.dataset.t),
      x0: e.clientX,
      grabDX: e.clientX - (dr.left + dr.width / 2),   // 抓在圆点边缘也不跳
      left: r.left,
      width: r.width,                                  // 起拖时量一次，后续不再反复读布局
      othersMins: others.map(rlStr2Mins).filter(function(n){ return n != null; }),
      moved: false,
      bad: false,
      lastM: null
    };
    rlDragBeginAt = Date.now();          // 按住即开始避让重排，避免轮询在拖动中把圆点摘掉
    try { ruler.setPointerCapture(e.pointerId); } catch (err) {}
  });

  ruler.addEventListener('pointermove', function(e){
    if (!drag) return;
    if (!drag.moved) {
      if (Math.abs(e.clientX - drag.x0) < DEAD) return;
      drag.moved = true;
      drag.dot.classList.add('dragging');
      ruler.classList.add('ruler--dragging');
      document.body.classList.add('rl-dragging');
    }
    const raw = Math.min(1440, Math.max(0,
      (e.clientX - drag.grabDX - drag.left) / drag.width * 1440));
    const fine = !!e.shiftKey;
    let m = snapMins(raw, fine, drag.fromMins);
    /* 落在别的点旁边（相差不足 5 分钟）算撞车：吸附到那个点的原值并标红，
       这样气泡显示的就是「已有的那个时间」，不会出现 23:55 盖在 23:59 上还以为没事 */
    let bad = false;
    for (let i = 0; i < drag.othersMins.length; i++) {
      if (Math.abs(drag.othersMins[i] - m) < 5) { m = drag.othersMins[i]; bad = true; break; }
    }
    const pct = pctOfMins(raw);          // 位置连续，值才吸附
    drag.dot.style.left = pct;
    const g = ghostBox();
    placeGhost(g, pct, raw);
    if (m !== drag.lastM || bad !== drag.bad) {   // 值没变就不动 DOM，避免无谓重排
      drag.lastM = m; drag.bad = bad;
      const t = rlMins2Str(m);
      g.querySelector('.t').textContent = t;
      g.querySelector('em').textContent = bad ? '已有' : (fine ? '精调 1 分钟' : 'Shift 精调');
      g.classList.toggle('bad', bad);
      drag.dot.classList.toggle('bad', bad);
      drag.dot.dataset.t = t;
      drag.dot.setAttribute('title', t);
      const lab = drag.dot.querySelector('span');
      if (lab) lab.textContent = t;
      const input = document.getElementById('d-new-time');
      if (input) input.value = t;
    }
  });

  ruler.addEventListener('pointerup', function(e){
    if (!drag) return;
    const d = drag;
    try { ruler.releasePointerCapture(e.pointerId); } catch (err) {}
    if (!d.moved) { settle(); return; }        // 没动 → 交给 click 走选中
    const now = d.dot.dataset.t;
    const bad = d.bad;
    settle();
    rlSuppressClickUntil = Date.now() + 600;   // 拖完紧跟的 click 不当成新增
    if (bad) {
      toast('这个时间点已经有了', true);
      renderRuler(currentTimes());
      return;
    }
    if (now === d.from) return;
    selTime = now; moveFrom = d.from; rlPaintSel();
    rlSaveMove(d.from, now);                   // 松手立即落库，不等 450ms 防抖
  });

  ruler.addEventListener('pointercancel', function(){
    if (!drag) return;
    settle();
    renderRuler(currentTimes()); rlPaintSel();
  });

  /* 拖到一半不想改了：Esc 撤回原位，不落库 */
  window.addEventListener('keydown', function(e){
    if (!drag || !drag.moved || e.key !== 'Escape') return;
    settle();
    renderRuler(currentTimes()); rlPaintSel();
    toast('已取消');
  });
})();

/* ▲▼ 微调：有选中点 → 改库；没选中 → 只改输入框 */
let rlMoveTimer = null;
/* 立即落库：拖动松手走这条，0 延迟 */
async function rlSaveMove(from, to){
  const list = currentTimes();
  if (from == null || list.indexOf(from) < 0) {   // 选中态已失效，退回只读
    selTime = null; moveFrom = null; rlPaintSel(); return false;
  }
  if (to === from) return true;
  if (list.indexOf(to) >= 0) {
    toast('这个时间点已经有了', true);
    selTime = from; renderRuler(currentTimes()); rlPaintSel(); return false;
  }
  const next = list.map(function(x){ return x === from ? to : x; })
                   .sort(function(a, b){ return rlStr2Mins(a) - rlStr2Mins(b); });
  const ok = await saveTimes(next);
  if (ok) { selTime = to; moveFrom = to; rlPaintSel(); }
  else { selTime = from; renderRuler(currentTimes()); rlPaintSel(); }
  return ok;
}
/* ▲▼ 连点用 450ms 防抖，只发最后一次 */
function rlMoveTo(t){
  clearTimeout(rlMoveTimer);
  rlMoveTimer = setTimeout(function(){ rlSaveMove(moveFrom, t); }, 450);
}
function rlNudge(delta){
  const input = document.getElementById('d-new-time');
  if (!input) return;
  const base = rlStr2Mins(selTime != null ? selTime : input.value);
  if (base == null) { toast('请先选择时间', true); return; }
  const t = rlMins2Str(base + delta);
  input.value = t;
  if (selTime == null) return;            // 未选中 → 只改输入框，等用户点「添加」
  selTime = t; rlPaintSel(); rlMoveTo(t);
}
document.getElementById('btn-time-up').addEventListener('click', function(){ rlNudge(1); });
document.getElementById('btn-time-down').addEventListener('click', function(){ rlNudge(-1); });

/* ---- 添加时间点：先弹确认框，框内 ▲▼ 微调 1 分钟，确定后才写入 ---- */
/* 弹窗里的时间以输入框为准（可直接键入），▲▼ 只是改这个框的值 */
function rlAddVal(){
  const v = document.getElementById('addtime-val');
  const m = v ? String(v.value || '').match(/^(\d{1,2}):(\d{2})$/) : null;
  return m ? String(+m[1]).padStart(2, '0') + ':' + m[2] : null;
}
function rlOpenAddTime(t, editFrom){
  const box = document.getElementById('addtime-modal');
  const val = document.getElementById('addtime-val');
  const title = document.getElementById('addtime-title');
  const ok = document.getElementById('addtime-ok');
  if (!box || !val) {                  // 兜底：弹窗缺失时退回原来的直接添加
    const input = document.getElementById('d-new-time');
    if (input) input.value = t;
    addTime(); return;
  }
  rlEditFrom = (editFrom != null) ? editFrom : null;
  if (title) title.textContent = rlEditFrom != null ? '修改提醒时间点' : '添加提醒时间点';
  if (ok) ok.textContent = rlEditFrom != null ? '确定修改' : '确定添加';
  val.value = t;
  box.hidden = false;
  rlStepHint(null);
  try { val.focus(); val.select && val.select(); } catch (e) {}
}
function rlCloseAddTime(){
  const box = document.getElementById('addtime-modal');
  if (box) box.hidden = true;
  rlStepHint(null);
  rlEditFrom = null;
  const title = document.getElementById('addtime-title');
  const ok = document.getElementById('addtime-ok');
  if (title) title.textContent = '添加提醒时间点';
  if (ok) ok.textContent = '确定添加';
}
function rlStepHint(step){
  const h = document.getElementById('addtime-hint');
  if (!h) return;
  h.textContent = step
    ? ('松开停止 · 当前步长 ' + (step >= 60 ? '1 小时' : step + ' 分钟'))
    : '可直接输入时间；点 ▲▼ 调 1 分钟，长按连续调整（10 分钟 / 1 小时）';
}
function rlAddStep(delta){
  const cur = rlAddVal();
  if (cur == null) return;
  const m = rlStr2Mins(cur);
  if (m == null) return;
  const val = document.getElementById('addtime-val');
  if (val) val.value = rlMins2Str(m + delta);
}
function rlConfirmAddTime(){
  const t = rlAddVal();
  if (t == null) { toast('时间格式不对，应为 HH:MM', true); return; }
  const from = rlEditFrom;              // 先抓住编辑来源，关闭弹窗会清空它
  const input = document.getElementById('d-new-time');
  if (input) input.value = t;
  rlCloseAddTime();
  if (from != null) {                  // 编辑已有时间点：用新值替换旧值（保持排序、去重）
    const list = currentTimes();
    if (list.indexOf(t) >= 0 && t !== from) { toast('这个时间点已经有了', true); return; }
    const next = list.map(function(x){ return x === from ? t : x; })
                     .sort(function(a, b){ return rlStr2Mins(a) - rlStr2Mins(b); });
    saveTimes(next);
    return;
  }
  addTime();                            // 纯新增：仍走原有的去重 + 保存链路
}
document.getElementById('addtime-up').addEventListener('click', function(){ rlAddStep(1); });
document.getElementById('addtime-down').addEventListener('click', function(){ rlAddStep(-1); });
document.getElementById('addtime-ok').addEventListener('click', rlConfirmAddTime);
document.getElementById('addtime-cancel').addEventListener('click', rlCloseAddTime);
document.getElementById('addtime-x').addEventListener('click', rlCloseAddTime);
document.getElementById('addtime-modal').addEventListener('click', function(e){
  if (e.target === this) rlCloseAddTime();          // 点遮罩空白处关闭
});
document.addEventListener('keydown', function(e){
  if (rlReloadKey(e)) return;   // F5 / Ctrl+R：刷新页面（判定见文件末尾「刷新快捷键」）
  const box = document.getElementById('addtime-modal');
  if (!box || box.hidden) return;
  if (e.key === 'Escape') rlCloseAddTime();
  else if (e.key === 'Enter') { e.preventDefault(); rlConfirmAddTime(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); rlAddStep(1); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); rlAddStep(-1); }
});

/* ▲▼ 长按连发：按住 450ms 后每 220ms 走一步，步长随按住时长升级
   1 分钟 →（>1.2s）10 分钟 →（>2.5s）1 小时，松手停止。
   长按结束时浏览器仍会补一次 click（会多走 ±1 分钟），这里在父级捕获阶段挡掉 */
function rlBindLongPress(id, delta, apply, hint){
  const btn = document.getElementById(id);
  if (!btn) return;
  let arm = null, tick = null, t0 = 0, repeated = false;
  function stop(){ clearTimeout(arm); clearInterval(tick); arm = tick = null; }
  btn.addEventListener('pointerdown', function(e){
    if (e.button !== 0) return;
    repeated = false; t0 = Date.now(); stop();
    arm = setTimeout(function(){
      tick = setInterval(function(){
        const held = Date.now() - t0;
        const step = held > 2500 ? 60 : (held > 1200 ? 10 : 1);
        repeated = true;
        apply(delta * step);
        if (hint) hint(step);
      }, 220);
    }, 450);
  });
  ['pointerup', 'pointerleave', 'pointercancel'].forEach(function(ev){
    btn.addEventListener(ev, function(){ stop(); if (hint) hint(null); });
  });
  const p = btn.parentElement;
  if (p) p.addEventListener('click', function(e){
    if (repeated && e.target === btn) { e.stopPropagation(); repeated = false; }
  }, true);
}
rlBindLongPress('addtime-up', 1, rlAddStep, rlStepHint);
rlBindLongPress('addtime-down', -1, rlAddStep, rlStepHint);
rlBindLongPress('btn-time-up', 1, rlNudge, null);
rlBindLongPress('btn-time-down', -1, rlNudge, null);

document.getElementById('btn-catch-up').addEventListener('click', saveCatchUp);

async function controlDaemon(action){
  const btn = document.getElementById(action === 'start' ? 'btn-daemon-start' : 'btn-daemon-stop');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = action === 'start' ? '启动中…' : '停止中…';
  /* 死命令：点停止 = 前端 heartbeatTimer 立刻停 + 后端 /api/daemon/stop 真停进程，
     两件事必须同时发生；若后端失败，finally 的 loadSchedule 会把状态纠回来 */
  if (action === 'stop') stopHeartbeat();
  try {
    const d = await postJSON('/api/daemon/' + action, {});
    if (d.daemon) renderDaemon(d.daemon);
    const okMsg = action === 'stop'
      ? '已停止本次运行（开机自启设置不变）'
      : (d.message || '已启动守护进程');
    toast(d.ok ? okMsg : (d.message || '操作失败'), !d.ok);
  } catch (e) {
    toast('操作失败：' + e, true);
  } finally {
    btn.textContent = old;
    loadSchedule();
  }
}

document.getElementById('btn-daemon-start').addEventListener('click', function(){ controlDaemon('start'); });
document.getElementById('btn-daemon-stop').addEventListener('click', function(){ controlDaemon('stop'); });
document.getElementById('btn-dev-save').addEventListener('click', saveDevice);
document.getElementById('btn-dev-shuffle').addEventListener('click', shuffleDevice);
document.getElementById('btn-dev-reset').addEventListener('click', resetDevice);

/* 查询代理（设备伪装页）：读取随 /api/device，保存独立端点 */
const proxySave = document.getElementById('btn-proxy-save');
if (proxySave && !proxySave.dataset.bound) {
  proxySave.dataset.bound = '1';
  proxySave.addEventListener('click', async function(){
    const box = document.getElementById('query-proxy');
    const hint = document.getElementById('proxy-hint');
    proxySave.disabled = true;
    try {
      const d = await postJSON('/api/query/proxy', { proxy: box.value });
      if (!d.ok) { toast(d.message || '保存失败', true); return; }
      toast(d.message || '已保存');
      if (hint) hint.textContent = d.proxy
        ? '已配置代理出口，米游社查询将经此连接'
        : '未配置，米游社查询直连';
    } catch (e) {
      toast('保存失败：' + e.message, true);
    } finally {
      proxySave.disabled = false;
    }
  });
}

/* ========================= 信息查询 ========================= */
let liveLoaded = false;    // 本次会话已经发起过实时请求
let liveRendered = false;  // 页面上已有可展示的数据（缓存或实时），占位符不再覆盖
/* 每日报表数据自动轮询（2026-10-31）：守护进程在后台定时写入历史，
   打开着的页面原来永远不知道——概览"一直显示不了"的根因之一。
   页签激活时每 60 秒拉一次（loadHistory 走 GET 不触发巡检；
   loadLive(false) 走 45 秒缓存，两个都轻）。 */
setInterval(function(){
  if (activeTab !== 'history') return;
  loadHistory();
  loadLive(false);
}, 60000);

function loadQuery(){
  loadActivities();
  if (!liveLoaded) loadLive(false);
}

/* 把 /api/live 结果整理成 gid → {finished, tasks, 体力信息} 的映射。
   只有拿到"当日数据"的游戏才进入映射 —— 它的完成状态有"发言权"，
   KPI 与迷你卡以它为准；查询失败、待上线更新、未创建角色的都没有当日记录，
   回落巡检历史。 */
function buildLiveMap(d){
  const map = {};
  (d.games || []).forEach(function(g){
    if (!g.ok || g.stale || g.no_account) return;
    map[g.game] = {
      finished: !!g.finished,
      tasks: (g.tasks || []).map(function(t){
        return { name: t.name, done: !!t.done, progress: t.progress || '' };
      }),
    };
    if (g.resource && g.resource.percent != null) {
      map[g.game].name = g.resource.name;
      map[g.game].current = g.resource.current;
      map[g.game].maximum = g.resource.maximum;
      map[g.game].percent = g.resource.percent;
      map[g.game].recover = g.resource.recover;
    }
  });
  return map;
}

/* 任务明细 → "委托 0/4 · 实训 0/500" 式进度串；无进度语义的任务用 ✓/✗ */
function taskProgressHtml(tasks){
  const parts = (tasks || []).map(function(t){
    const label = escHtml(t.name);
    if (t.progress) {
      const hit = /(\d+)\s*\/\s*(\d+)/.exec(t.progress);
      const txt = hit ? (parseInt(hit[1], 10) + '/' + parseInt(hit[2], 10)) : escHtml(t.progress);
      return '<span class="tp-item' + (t.done ? '' : ' tp-miss') + '">' + label +
             ' <b class="mono">' + txt + '</b></span>';
    }
    return '<span class="tp-item' + (t.done ? '' : ' tp-miss') + '">' + label +
           ' <b>' + (t.done ? '✓' : '✗') + '</b></span>';
  });
  return parts.length ? '<div class="gcard-tasks">' + parts.join('<i class="tp-sep">·</i>') + '</div>' : '';
}

/* ---------- 体力预计满员时间（事前规划） ----------
   数据优先级：API 恢复字段（resource.recover = 后端统一化的距满秒数，
   原神/崩铁/终末地有）> RECOVERY_RATE_PER_MIN 常数表（方舟等接口不给恢复字段的走这里）。
   输出静态文案「预计 22:45 满」：目标时刻由查询数据推出，不引入新 timer（倒计时纪律），
   不写 localStorage；页面常驻时以「信息查询」最新一次结果为准。
   已满 → 「已满」；剩余不足 1 小时 → .eta-soon 橙色；数据缺失 → 空串。 */
const RECOVERY_RATE_PER_MIN = {
  genshin: 8, starrail: 6, zzz: 6, arknights: 6, endfield: 6, wuwa: 10
};
function staminaEtaHtml(gid, resource){
  if (!resource || resource.current == null || resource.maximum == null) return '';
  const cur = Number(resource.current), max = Number(resource.maximum);
  if (!(max > 0)) return '';
  if (cur >= max) return '<span class="eta">已满</span>';
  let seconds = null;
  if (resource.recover != null && Number(resource.recover) > 0) {
    seconds = Number(resource.recover);            // API 真值优先
  } else {
    const rate = RECOVERY_RATE_PER_MIN[gid];
    if (rate > 0) seconds = (max - cur) * rate * 60;
  }
  if (!seconds || seconds <= 0) return '';
  const pad = function(n){ return (n < 10 ? '0' : '') + n; };
  let text;
  if (seconds >= 86400) {
    text = '预计 ' + (Math.round(seconds / 8640) / 10) + ' 天后满';
  } else {
    const target = new Date(Date.now() + seconds * 1000);
    text = '预计 ' + pad(target.getHours()) + ':' + pad(target.getMinutes()) + ' 满';
  }
  return '<span class="eta' + (seconds < 3600 ? ' eta-soon' : '') + '">' + text + '</span>';
}

function renderLive(d){
  const box = document.getElementById('live-grid');
  const hint = document.getElementById('live-hint');
  if (!box) return;
  if (!d || d.ok === false) {
    /* 已有数据在展示时，失败的刷新静默忽略，不把页面打成空态 */
    if (!liveRendered) {
      box.innerHTML = '<div class="emptybox">' + escHtml((d && d.message) || '查询失败，请重试') + '</div>';
      if (hint) hint.textContent = '';
    }
    return;
  }
  liveRendered = true;
  window.__liveMap = Object.assign(window.__liveMap || {}, buildLiveMap(d));
  const games = d.games || [];
  /* 只读曝光原始结果（含失败/待更新/未创建角色），供概览卡判断"查询失败"。
     buildLiveMap 会把这些态过滤掉（KPI/表格的既有语义不动）。
     2026-11-01：失败态原本在概览卡回落成灰字"今日无数据"，把
     "需要动手查凭证"伪装成了"没有信息"，所以必须留一份原文。 */
  window.__liveAll = games;
  if (!games.length) {
    /* 三态文案（2026-11-05 P6-3）：「没有启用」与「没绑凭证」是两回事——
       账号页开关全开但没扫码时，旧文案把用户引去开开关，越开越糊涂 */
    const accs = window.__accounts || {};
    const enabledIds = Object.keys(accs).filter(function(k){
      return accs[k] && accs[k].enabled;
    });
    const boundN = enabledIds.filter(function(k){
      return accs[k] && accs[k].has_credential;
    }).length;
    let msg;
    if (!enabledIds.length) msg = '没有启用的账号。';
    else if (!boundN) msg = '启用的账号都还没绑定凭证——去「账号与通知」绑定后再来。';
    else msg = '暂无实时数据（凭证可能失效或角色未创建），稍后重试或查运行日志。';
    box.innerHTML = '<div class="emptybox">' + msg + '</div>';
  } else {
    /* 每游戏一张便笺卡（HoYoLAB 实时便笺形态）：卡片直接作为网格子项
       （不加包装层——包装 div 会独占一条网格轨道，把卡片挤成竖排） */
    box.innerHTML = games.map(function(g){
      const name = escHtml(g.game_name || g.game);
      const head = '<div class="live-card-head">' + gameIconHtml(g.game) +
        '<b class="live-name">' + name + '</b>';
      /* 风控冷却中（2026-11-01）：命中 1034/10041 的游戏 6 小时内后端不再发起
         实时查询 —— 展示缓存数据 + 冷却徽章，绝不红叉诱使用户再点刷新 */
      if (g.cooled) {
        const left = g.cooldown_left_minutes || 0;
        const leftTxt = left >= 60
          ? (Math.round(left / 6) / 10) + ' 小时'
          : left + ' 分钟';
        let body = '<div class="live-msg dim">实时查询已暂停（' + leftTxt +
          '后自动恢复）：命中风控后不再向米游社发请求，避免加深标记。' +
          '下方为上次缓存数据。</div>';
        if (g.resource && g.resource.current != null) {
          body = '<div class="live-res"><b class="mono">' + g.resource.current +
            ' <span class="dim">/ ' + (g.resource.maximum == null ? '?' : g.resource.maximum) +
            '</span></b><span class="dim">' + escHtml(g.resource.name || '') + '</span></div>' + body;
        }
        /* 结构对照其它分支：head 自身不带闭合标签，紧跟的 </div> 闭合的是
           .live-card-head；body 之后必须再补一个 </div> 才闭合 .live-card。
           2026-11-05 修复：原写法漏了末尾这个（只有 1 个 </div>），导致整张卡
           不闭合、后续游戏卡全被吞进第一张里，网格塌成一列、卡高变 0。 */
        return '<div class="live-card" style="--gc:' + gameDotColor(g.game) + '">' + head + '<span class="st warn">风控冷却中</span></div>' + body + '</div>';
      }
      /* 占位卡：新勾选的游戏缓存里还没查询结果 —— 诚实占位，不伪造数据 */
      if (g.placeholder) {
        return '<div class="live-card" style="--gc:' + gameDotColor(g.game) + '">' + head + '<span class="st">尚未查询</span></div>' +
          '<div class="live-msg dim">点「刷新」查询实时体力，或等下一轮定时巡检。</div></div>';
      }
      if (!g.ok) {
        return '<div class="live-card" style="--gc:' + gameDotColor(g.game) + '">' + head + '<span class="st err">失败</span></div>' +
          '<div class="live-msg err-t">' + escHtml(g.error || '未知错误') + '</div></div>';
      }
      if (g.stale) {
        /* 日常 04:00 已归零 ≠ 失败：等玩家上线，数据自然更新 */
        return '<div class="live-card" style="--gc:' + gameDotColor(g.game) + '">' + head + '<span class="st">待上线更新</span></div>' +
          '<div class="live-msg dim">' + escHtml(g.stale) + '</div></div>';
      }
      if (g.no_account) {
        /* 未创建角色 ≠ 失败：凭证有效，建号后自动生效 */
        return '<div class="live-card" style="--gc:' + gameDotColor(g.game) + '">' + head + '<span class="st">未创建角色</span></div>' +
          '<div class="live-msg dim">' + escHtml(g.no_account) + '</div></div>';
      }
      const st = g.finished ? '<span class="st ok">已完成</span>'
                            : '<span class="st warn">未完成</span>';
      const r = g.resource;
      if (!r || r.current == null) {
        return '<div class="live-card" style="--gc:' + gameDotColor(g.game) + '">' + head + st + '</div>' +
          '<div class="live-msg dim">暂不支持体力查询</div></div>';
      }
      const pct = (r.percent == null) ? 0 : r.percent;
      const max = (r.maximum == null) ? '?' : r.maximum;
      return '<div class="live-card" style="--gc:' + gameDotColor(g.game) + '">' + head + st + '</div>' +
        '<div class="live-res"><b class="mono">' + r.current +
        ' <span class="dim">/ ' + max + '</span></b><span class="dim">' +
        escHtml(r.name) + '</span></div>' +
        '<div class="bar"><i class="' + (pct >= 85 ? 'hot' : '') +
        '" style="width:' + pct + '%"></i><i class="progress-glow' +
        (pct >= 85 ? ' hot' : '') + '" style="left:' + pct + '%"></i></div>' +
        '<div class="live-eta"><span class="dim">' + pct + '%</span>' +
        staminaEtaHtml(g.game, r) + '</div>' +
        '</div>';
    }).join('');
  }
  if (hint) {
    hint.textContent = '查询时间 ' + (d.generated_at || '-') +
      (d.cached ? (d.stale ? '（历史缓存 · 点「刷新」获取实时）' : '（45 秒内缓存）') : '');
  }
  // 迷你卡的体力行用最新数据重绘（报表页当前可见时才有意义）
  const statsBox = document.getElementById('h-stats');
  if (lastHistory && lastHistory.has_data && statsBox && statsBox.querySelector('.gcard')) {
    statsBox.innerHTML = '<div class="gcards">' +
      (lastHistory.games || []).map(gameCardHtml).join('') + '</div>';
  }
  renderKpiResin(d);
  /* 实时结果一到手就重算「今日完成」KPI：live 说已完成的立刻计入，
     不等下一次巡检落盘 —— KPI 与表格永远对得上 */
  if (lastHistory) renderKpiToday(lastHistory);
}

/* ========================= 刷新反馈（转圈按钮） =========================
   2026-11-01 减法裁决：撤掉卡片闪光与整页白闪（Oil UI「每页 1-2 处
   exceptional」）——转圈按钮已是充分反馈，页面唯一的强调动效留给
   体力临满的紧迫度。 */
function btnSpin(btn, promise){
  /* 按钮进入转圈态；promise 落定（无论成败）恢复原文字并让所属卡片闪光 */
  if (!btn) return promise;
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>';
  const done = function(){
    btn.disabled = false;
    btn.innerHTML = orig;
  };
  promise.then(done, done);
  return promise;
}

function loadLive(force){
  const btn = document.getElementById('btn-live-refresh');
  const box = document.getElementById('live-grid');
  /* 打开控制台（首次加载）发 refresh=auto：服务端按磁盘缓存年龄决定
     真查还是秒开（阈值 10 分钟，服务端时钟判定）——
     「每次打开都强刷」实测让 F5 也要白等 ~14 秒，按年龄放行两头兼顾 */
  if (!liveLoaded) force = 'auto';
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spin"></span>'; }
  /* 已有缓存渲染的表格时不再打占位符 —— 静默刷新，数据到了平滑替换 */
  if (box && !liveRendered) box.innerHTML = '<div class="emptybox">正在查询，请稍候…</div>';
  kpiBusy(true);
  return fetch('/api/live' + (force ? '?refresh=' + force : ''))
    .then(function(r){ return r.json(); })
    .then(function(d){
      liveLoaded = true;
      renderLive(d);
      if (d && d.ok !== false) cacheSet('live', d);   // 落缓存，下次秒开
    })
    .catch(function(){
      if (box && !liveRendered) box.innerHTML = '<div class="emptybox">查询失败，请稍后重试。</div>';
    })
    .then(function(){
      kpiBusy(false);
      if (btn) { btn.disabled = false; btn.textContent = '刷新'; }
    });
}

function renderActHint(d){
  const hint = document.getElementById('act-hint');
  if (!hint) return;
  d = d || {};
  const parts = [];
  if (d.last_error) {
    parts.push('上次拉取失败（沿用旧数据）：' + d.last_error);
  } else if (d.fetched_at) {
    parts.push('数据更新于 ' + d.fetched_at);
  }
  if (d.ignored_count > 0) {
    parts.push('已删除 ' + d.ignored_count + ' 个');
  }
  hint.innerHTML = escHtml(parts.join(' · ')) +
    (d.ignored_count > 0 ? ' <a class="act-restore" id="act-restore">恢复全部</a>' : '');
  const restore = document.getElementById('act-restore');
  if (restore) restore.addEventListener('click', restoreActivities);
}

/* 游戏头像：按 game_id 硬绑定官方图标。
   ICON_STAMP 由后端按图片 mtime 注入，换图后 URL 必变，不会被 WebView2 缓存坑。 */
const ICON_STAMP = '__ICON_STAMP__';
const GAME_ICON = {
  genshin: '/static/icons/genshin',
  starrail: '/static/icons/starrail',
  zzz: '/static/icons/zzz',
  arknights: '/static/icons/arknights',
  endfield:  '/static/icons/endfield',
  wuwa:      '/static/icons/wuwa',
  miyoushe:  '/static/icons/miyoushe',
  kuro:      '/static/icons/kuro',    // 库街区 App 官方图标（平台与游戏分开）
  skland:    '/static/icons/skland'
};

/* 图标定义就绪后，把账号绑定页选择卡的静态占位换成官方图标 */
fillSelIcons();

/* 扫码胶囊平台图标：与游戏图标同一资产链路（/static/icons + mtime 戳），
   emoji 仅作水合前的占位，身份层不再依赖 emoji 渲染 */
function fillCapIcons(){
  document.querySelectorAll('.cap-ico[data-plat]').forEach(function(el){
    const tmp = document.createElement('div');
    tmp.innerHTML = selIconHtml(el.getAttribute('data-plat'), '');
    const node = tmp.firstChild;
    if (node) { el.textContent = ''; el.appendChild(node); }
  });
}
fillCapIcons();

function actIconHtml(it){
  const base = GAME_ICON[it.game];
  if (!base) {
    return '<span class="act-icon ph">' +
      escHtml(String(it.game_name || '?').slice(0, 1)) + '</span>';
  }
  const v = ICON_STAMP.indexOf('__') === 0 ? '' : '?v=' + ICON_STAMP;
  return '<img class="act-icon" width="24" height="24" alt="' +
    escHtml(it.game_name || '') + '" src="' + base + '.png' + v +
    '" srcset="' + base + '@2x.png' + v + ' 2x">';
}

/* '2026-10-29 04:00:00' -> '10-29 04:00'（卡片头部精确到分） */
function shortRange(s){
  const m = String(s || '').match(/\d{4}-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/);
  return m ? m[1] + '-' + m[2] + ' ' + m[3] : '';
}

/* 最紧急 1 张的紧迫度标记（只改颜色不改尺寸）：剩余 ≤24h = critical（红边+呼吸）；≤72h（ending_soon）= warn（琥珀数字） */
function actUrgencyAttr(it, isHero){
  if (!isHero || it.not_started) return '';
  const hl = +it.hours_left || 0;
  if (it.ending_soon && hl > 0 && hl <= 24) return ' data-urgency="critical"';
  if (it.ending_soon) return ' data-urgency="warn"';
  return '';
}

/* isHero：最紧急的 1 张 → 2x2 英雄卡（hero class）+ 紧迫感描边；
   heroSlim=true 时只保留描边不占 2x2（英雄卡没内容可展示时不撑大片留白） */
function actCardHtml(it, isHero, heroSlim){
  const id = escHtml(it.id || '');
  const st = shortRange(it.start), en = shortRange(it.end);
  const ended = it.status === '已完成';
  const timeCls = ended ? 't-end' : (it.not_started ? 't-up' : 't-on');
  /* 紧迫度徽章（2026-11-01 方案 A）：≤24h 红 / ≤3 天琥珀 / 其余灰。
     档位只认后端 hours_left，缺值或非正数一律灰——绝不前端估时间 */
  const hl = +it.hours_left || 0;
  const cdCls = ended ? 'ended'
    : (it.not_started ? ''
    : (hl > 0 && hl <= 24 ? 'urgent' : (hl > 0 && hl <= 72 ? 'warn' : '')));
  // reward 为空 = 接口没给奖励明细，被兜底规则保留下来的（别假装它有原石）。
  // 2026-11-06 用户点名：奖励从标题文本里拆出来，放卡片最右端小徽章凸显
  const reward = it.reward
    ? '<span class="act-reward" title="' + escHtml(it.reward) + ' · 抽卡资源">' + escHtml(it.reward) + '</span>'
    : '';
  const prog = actProgHtml(it);
  /* 合并组卡：成员小字清单（这才是「有信息的 2x2」）；× 不渲染——
     data-del 只认单活动 id，成员随窗口结束自然拆回，拆回后单卡再删 */
  const mergedList = (it.merged && it.merged.length > 1)
    ? '<div class="act-merged">' + it.merged.map(function(t){
        return '<div>· ' + escHtml(t) + '</div>';
      }).join('') + '</div>'
    : '';
  const delBtn = it.merged
    ? '<span class="act-merged-tag">' + it.merged.length + ' 项同止</span>'
    : '<button type="button" class="act-del" data-del="' + id +
      '" title="删除，之后不再提醒">×</button>';
  return '<div class="card acard' + (isHero && !heroSlim ? ' hero' : '') + '"' +
    actUrgencyAttr(it, isHero) + ' data-id="' + id + '"' +
    ' style="--gc:' + gameDotColor(it.game) + '">' +
    '<div class="acard-top"><span class="act-game">' + actIconHtml(it) +
      '<span class="act-name">' + escHtml(it.game_name) + '</span></span>' +
      delBtn + '</div>' +
    '<div class="acard-title"><span class="acard-title-txt" title="' + escHtml(it.title) + '">' + escHtml(it.title) + '</span>' + reward + '</div>' +
    '<div class="act-time ' + timeCls + '">始 ' + (st || '时间待定') +
      (en ? ' · 止 ' + en : '') + '</div>' +
    '<div class="act-cd ' + cdCls + '">' + escHtml(it.countdown || '') + '</div>' +
    mergedList +
    prog +
    '</div>';
}

/* 36/36 这类文本进度 -> 进度条；解析不出就不画 */
function actProgHtml(it){
  const m = String(it.progress || '').match(/(\d+)\s*\/\s*(\d+)/);
  if (!m) return '';
  const a = +m[1], b = +m[2];
  if (!b) return '';
  const pct = Math.min(100, Math.round(a / b * 100));
  return '<div class="act-prog"><span class="sbar"><i style="width:' + pct + '%"></i></span>' +
    '<span class="mono">' + a + '/' + b + '</span></div>';
}

/* 找最紧急的 1 张（剩余小时最小且 >0），做 2x2 英雄卡 + data-urgency 标记 */
/* 卡里有没有「可看的内容」：进度条、奖励明细，或合并组卡的成员清单。
   2x2 英雄卡只有内容足才配占（纯文字卡占 2x2 = 大片留白，
   2026-11-05 P6-2 实测空置 241px） */
function actHasContent(it){
  return actProgHtml(it) !== '' || !!it.reward || !!(it.merged && it.merged.length > 1);
}

/* 同游戏同截止的合并组卡（仅「即将结束」窗口内，窗口随用户设置
   app.activity_alert_days 走——后端随 payload 给 window_hours）：
   标题=首活动「等 N 项」，奖励去重拼接；进度不并（各自口径不同，
   硬加会误导）；成员明细由 actCardHtml 的小字列表展示；× 不渲染
   （窗口过了自然拆回，拆回后单卡再删） */
function buildMergedItem(members){
  const first = members[0];
  const rewards = [];
  members.forEach(function(x){
    String(x.reward || '').split(/[、,，;；]/).forEach(function(r){
      const t = r.trim();
      if (t && rewards.indexOf(t) < 0) rewards.push(t);
    });
  });
  return Object.assign({}, first, {
    id: 'merged:' + members.map(function(x){ return x.id || ''; }).join('|'),
    title: (first.title || '') + ' 等 ' + members.length + ' 项',
    reward: rewards.join('、'),
    progress: '',
    merged: members.map(function(x){ return x.title || ''; }),
  });
}


/* 活动视图模式：'time' 按剩余时间（默认）/ 'game' 按游戏分区（方案 A）。
   选择记忆在 localStorage('rl-actview')——与 rl-theme 同机制 */
function actViewMode(){
  return localStorage.getItem('rl-actview') === 'game' ? 'game' : 'time';
}

/* 活动视图切换开关：记忆 rl-actview，切换即免拉取重渲染（数据取 __lastActivities） */
(function bindActView(){
  const seg = document.getElementById('act-view');
  if (!seg) return;
  const paint = function(){
    const mode = actViewMode();
    seg.querySelectorAll('button').forEach(function(b){
      b.classList.toggle('on', b.dataset.view === mode);
    });
  };
  seg.addEventListener('click', function(e){
    const b = e.target.closest ? e.target.closest('button[data-view]') : null;
    if (!b || b.classList.contains('on')) return;   // 防重复触发
    localStorage.setItem('rl-actview', b.dataset.view);
    paint();
    if (window.__lastActivities) renderActivities(window.__lastActivities);
  });
  paint();
})();

function renderActivities(d){
  const list = document.getElementById('act-list');
  const uns = document.getElementById('act-unsupported');
  if (!list) return;
  d = d || {};
  window.__lastActivities = d;   // 视图切换时免拉取重渲染
  const items = d.items || [];
  if (!items.length) {
    list.innerHTML = '<div class="act-empty">' +
      '<div class="act-empty-icon"><svg class="ico"><use href="#' +
      (d.has_data ? 'i-sprout' : 'i-tray') + '"/></svg></div>' +
      '<div>' + (d.has_data
        ? '近期无即将结束的活动，安心长草'
        : '还没有活动数据，点「刷新」拉取。') + '</div>' +
      '</div>';
  } else {
    // 后端已排好序；已完成单独进抽屉。剩余时间只认后端 hours_left（>0），
    // 缺值当 Infinity 沉底，不前端估时间
    const ongoing = items.filter(function(x){ return x.group !== 'upcoming' && x.status !== '已完成'; });
    const upcoming = items.filter(function(x){ return x.group === 'upcoming'; });
    const finished = items.filter(function(x){ return x.status === '已完成'; });
    const hlOf = function(x){ const h = +x.hours_left; return h > 0 ? h : Infinity; };
    /* 显示排序 = 唯一裁决（2026-11-05 英雄卡三改）：hours_left 升序 →
       平局「有内容可展示」的卡排前（英雄给谁一目了然，不再依赖后端返回序）
       → 再平局标题 zh 序。英雄 = 排序后 pool 的第一张，最紧急 = 视觉第一。 */
    const byUrgency = function(a, b){
      const diff = hlOf(a) - hlOf(b);
      if (diff) return diff;
      const ra = actHasContent(a) ? 1 : 0, rb = actHasContent(b) ? 1 : 0;
      if (ra !== rb) return rb - ra;
      return String(a.title || '').localeCompare(String(b.title || ''), 'zh');
    };
    /* 同游戏同截止合并：仅「即将结束」（hours_left ≤ 用户提醒窗口
       window_hours）时才并，平常各是各的卡 */
    const winH = +d.window_hours > 0 ? +d.window_hours : 72;
    const mergeBuckets = {};
    const mergedOngoing = [];
    ongoing.forEach(function(x){
      const bucket = (x.ending_soon && (+x.hours_left || 0) <= winH)
        ? 'm:' + x.game + '|' + String(x.end || '') : null;
      if (!bucket) { mergedOngoing.push(x); return; }
      (mergeBuckets[bucket] = mergeBuckets[bucket] || []).push(x);
    });
    Object.keys(mergeBuckets).forEach(function(key){
      const members = mergeBuckets[key];
      if (members.length < 2) { mergedOngoing.push(members[0]); return; }
      members.sort(byUrgency);
      mergedOngoing.push(buildMergedItem(members));
    });
    const heroPool = mergedOngoing.slice().sort(byUrgency);
    const heroIt = heroPool.length ? heroPool[0] : (upcoming[0] || null);
    const heroSlim = !!heroIt && !actHasContent(heroIt);

    let html = '';
    if (actViewMode() === 'game') {
      /* 模式 A · 按游戏分区（方案 A）：组头 = 色点 + 游戏名 + 数量；
         组内按剩余时间升序；组间按「最近将有活动结束」升序 */
      const groups = [], byGid = {};
      mergedOngoing.concat(upcoming).forEach(function(x){
        const gid = x.game || '';
        if (!byGid[gid]) {
          byGid[gid] = { gid: gid, name: x.game_name || '未知游戏', items: [] };
          groups.push(byGid[gid]);
        }
        byGid[gid].items.push(x);
      });
      groups.forEach(function(g){
        g.items.sort(byUrgency);
        g.mins = hlOf(g.items[0]);   // 组内升序后首项即组内最早结束
      });
      groups.sort(function(a, b){ return a.mins - b.mins; });

      if (groups.length) {
        html = groups.map(function(g){
          return '<div class="act-group">' +
            '<div class="act-group-head">' +
              '<span class="gdot" style="background:' + gameDotColor(g.gid) + '"></span>' +
              '<span class="act-group-name">' + escHtml(g.name) + '</span>' +
              '<span class="act-group-count">' + g.items.length + ' 个活动</span>' +
            '</div>' +
            '<div class="act-grid">' +
              g.items.map(function(x){ return actCardHtml(x, x === heroIt, heroSlim); }).join('') +
            '</div></div>';
        }).join('');
      }
    } else {
      /* 模式 B · 按剩余时间升序，同时间按标题首字母（localeCompare zh）；
         平坦网格无组头，最紧急一张仍是英雄卡 */
      const pool = mergedOngoing.concat(upcoming).sort(byUrgency);
      html = '<div class="act-grid">' +
        pool.map(function(x){ return actCardHtml(x, x === heroIt, heroSlim); }).join('') +
        '</div>';
    }

    if (finished.length) {
      html += '<div class="act-drawer" id="act-drawer">' +
        '<button type="button" class="act-drawer-handle">已完成 · ' + finished.length + '</button>' +
        '<div class="act-drawer-body"><div class="act-grid">' +
        finished.map(function(it){ return actCardHtml(it, false); }).join('') +
        '</div></div></div>';
    }
    list.innerHTML = html;
  }
  renderActHint(d);
  if (uns) {
    const list2 = d.unsupported || [];
    uns.innerHTML = list2.length
      ? '<div class="au-title">暂不支持</div>' +
        list2.map(function(u){
          return '<span class="au-chip" title="' + escHtml(u.reason || '') + '">' +
            escHtml(u.game_name) + '</span>';
        }).join('')
      : '';
  }
}

/* 删除：立刻从界面消失，并写进 data/ignored_activities.json，之后永不提醒 */
async function deleteActivity(btn){
  const id = btn.getAttribute('data-del');
  const row = btn.closest('.acard');
  btn.disabled = true;
  try {
    const d = await postJSON('/api/activities/delete', { id: id });
    if (!d || d.ok === false) {
      toast((d && d.message) || '删除失败', true);
      btn.disabled = false;
      return;
    }
    if (row) row.remove();
    renderActivities(d);
    toast('已删除，之后不会再提醒');
  } catch (e) {
    btn.disabled = false;
    toast('删除失败：' + e, true);
  }
}

async function restoreActivities(){
  try {
    const d = await postJSON('/api/activities/restore', {});
    if (!d || d.ok === false) { toast('恢复失败', true); return; }
    renderActivities(d);
    toast('已恢复全部被删除的活动');
  } catch (e) {
    toast('恢复失败：' + e, true);
  }
}

function loadActivities(){
  fetch('/api/activities').then(function(r){ return r.json(); })
    .then(renderActivities)
    .catch(function(){
      const list = document.getElementById('act-list');
      if (list) list.innerHTML = '<div class="emptybox">读取活动缓存失败，请点「刷新」。</div>';
    });
}

async function refreshActivities(){
  const btn = document.getElementById('btn-act-refresh');
  if (!btn) return;
  const old = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span>';
  try {
    const d = await postJSON('/api/activities/refresh', {});
    if (d.ok === false) { toast(d.message || '刷新失败', true); return; }
    renderActivities(d);
    toast('活动数据已更新（' + (d.items || []).length + ' 个进行中）');
  } catch (e) {
    toast('刷新失败：' + e, true);
  } finally {
    btn.disabled = false; btn.innerHTML = old;
  }
}

/* =========================== 每日报表 =========================== */
function renderHistoryEmpty(message, detail){
  // 空状态：统计表清空，表格位置放一句人话（绝不弹红色错误条）
  const statsBox = document.getElementById('h-stats');
  if (statsBox) statsBox.innerHTML = '';
  const monthBadge = document.getElementById('h-month');
  if (monthBadge) monthBadge.textContent = '-';
  const tableBox = document.getElementById('h-table');
  if (!tableBox) return;
  tableBox.innerHTML =
    '<div class="emptybox">' + message +
    (detail ? '<br><span style="font-size:12px;color:var(--muted)">' + detail + '</span>' : '') +
    '</div>';
}

function gameBaseName(g){
  // 只取游戏基础名：去掉「 · 角色昵称」。
  var raw = String((g && (g.game_name || g.game)) || '');
  var cut = raw.indexOf('·');
  if (cut > 0) raw = raw.slice(0, cut);
  return raw.trim() || String((g && g.game) || '');
}

// 游戏识别色（BetterGI 式图标占位小圆点）
const GAME_DOT = {
  genshin:   '#e0a83c',
  starrail:  '#8f7ff0',
  zzz:       '#f0854b',
  arknights: '#4aa3e8',
  endfield:  '#dcc23c',
  wuwa:      '#c8a96a'
};
function gameDotColor(gid){
  return GAME_DOT[gid] || 'var(--brand)';
}

let lastHistory = null;

/* ---------- 本周汇总（报表页顶部横条） ----------
   数据源 = /api/history?days=7 的 days[].games{gid:{known,finished}}，
   客户端聚合，零新 API；口径与 daemon 周报（history.weekly_digest）一致：
   known = 当天查到数据，「漏」只数查过但没做完的天，整天没查过不算漏。 */
function weeklyStatsOf(d){
  const days = (d && d.days) || [];
  const tracked = (d && d.tracked) || [];
  const nameOf = {};
  (d.games || []).forEach(function(g){ nameOf[g.game] = gameBaseName(g); });
  return tracked.map(function(gid){
    let done = 0, known = 0;
    days.forEach(function(row){
      const c = (row.games || {})[gid];
      if (!c || !c.known) return;
      known++;
      if (c.finished) done++;
    });
    return { gid: gid, name: nameOf[gid] || gid, done: done, known: known, days: days.length };
  });
}

function renderWeeklyCard(d){
  const box = document.getElementById('week-body');
  const badge = document.getElementById('week-badge');
  if (!box) return;
  const stats = weeklyStatsOf(d);
  if (!stats.length || !stats[0].days) {
    box.innerHTML = '<div class="emptybox">暂无数据，等本周巡检跑起来。</div>';
    if (badge) badge.textContent = '-';
    return;
  }
  const totalDays = stats[0].days;
  let totalDone = 0;
  const chips = stats.map(function(s){
    totalDone += s.done;
    const missed = s.known - s.done;             // 查过但没做完（与周报同口径）
    // 身份一致性：同一页里概览卡/最近 7 天都亮官方图标，汇总 chip 不该是
    // 唯一无脸的——复用 gcard-ico 类，小窗紧凑模式（≤1099 隐藏图标）自动同治
    let html = '<span class="week-chip">' + gameIconHtml(s.gid, 'gcard-ico') +
      '<b>' + escHtml(s.name) + '</b> <span class="mono">' +
      s.done + '/' + totalDays + '</span>';
    if (s.done >= totalDays) html += '<span class="dim"> 全勤</span>';
    else if (missed > 0) html += '<span class="miss"> 漏 ' + missed + '</span>';
    return html + '</span>';
  }).join('');
  box.innerHTML = '<div class="week-chips">' + chips + '</div>';
  if (badge) badge.textContent = totalDone + '/' + (stats.length * totalDays);
}

/* ---------- 周完成率徽章（本周 vs 上周） ----------
   /api/history?days=14 独立旁路：loadHistory 主链仍拉 7 天喂报表/KPI，
   这里另拉 14 天只喂徽章，两路互不影响、失败互不传染。
   /api/history 的 days 数组实为「最旧在前」——2026-11-01 真机实测修正：
   days[0].date=最旧日、days[last]=今天（最近 7 天表格列序同序可证；
   _summary_unsafe 的「今天在最上面」注释指 UI 呈现观感，与数组序相反），
   故 [0..6] 为上周、[7..13] 为本周。
   口径与 weeklyStatsOf / history.weekly_digest 一致：known 天才算分母，
   整天没查过不算漏；上周没数据（如全新安装）时只显示本周，不编造对比。 */
function weekRateOf(dayRows, tracked){
  let done = 0, known = 0;
  (dayRows || []).forEach(function(row){
    const games = (row && row.games) || {};
    (tracked || []).forEach(function(gid){
      const c = games[gid];
      if (!c || !c.known) return;
      known++;
      if (c.finished) done++;
    });
  });
  return known > 0 ? Math.round(done / known * 100) : null;
}

function loadWeekTrend(){
  fetch('/api/history?days=14').then(function(r){ return r.json(); })
    .then(function(d){
      const badge = document.getElementById('week-badge');
      if (!badge) return;
      const rows = (d && d.days) || [];
      const tracked = (d && d.tracked) || [];
      /* days 数组实为「最旧在前」（_summary_unsafe 按 offset 递减 append；
         最近 7 天表格的列序 10/26→11/1 同序可证）——故本周 = 末尾 7 格 */
      const cur = weekRateOf(rows.slice(7, 14), tracked);
      if (cur === null) { badge.textContent = '-'; return; }
      const prev = weekRateOf(rows.slice(0, 7), tracked);
      let html = '本周 ' + cur + '%';
      if (prev !== null) {
        html += ' · 上周 ' + prev + '%';
        const diff = cur - prev;
        if (diff >= 1) html += ' <b class="wk-up">↑</b>';
        else if (diff <= -1) html += ' <b class="wk-dn">↓</b>';
      }
      badge.innerHTML = html;
    })
    .catch(function(){ /* 静默：徽章保留主链已画的 done/total 口径 */ });
}

/* 热力图格子状态判定（最近 7 天与月报下钻共用，2026-11-06 抽出）。
   返回 {cls, mark, tip}：live 实时合并只对「今天的行」生效，历史月不受影响。 */
function hmCellState(c, row, lv){
  let cls = 'na', mark = '', tip;
  if (lv && lv.finished != null) {
    cls = lv.finished ? 'ok' : 'na wait';
    mark = lv.finished ? '✓' : '⋯';
    tip = row.label + ' · ' + (lv.finished ? '已完成（实时）' : '进行中（今日未完成）');
  } else if (c && c.known) {
    cls = c.finished ? 'ok' : 'err';
    mark = c.finished ? '✓' : '✗';
    tip = row.label + ' · ' + (c.finished ? '已完成'
        : '未完成 ' + (c.unfinished_tasks || 0) + ' 项');
  } else if (row.is_today) {
    /* 今日列三态（P2）：检查失败/快照过期不再伪装成「等待巡检」 */
    if (c && (c.checks || 0) > 0 && c.err) {
      cls = 'na';
      mark = '✗';
      tip = row.label + ' · 检查失败：' + c.err;
    } else if (c && (c.checks || 0) > 0 && (c.stale || c.noacc)) {
      cls = 'na';
      mark = '⋯';
      tip = row.label + ' · ' + (c.stale || '账号下未创建角色');
    } else {
      cls = 'na wait';
      mark = '⋯';
      tip = row.label + ' · 等待今日巡检';
    }
  } else if (c && (c.checks || 0) > 0 && c.err) {
    tip = row.label + ' · 检查失败：' + c.err;
  } else {
    tip = row.label + ' · 无数据';
  }
  return { cls: cls, mark: mark, tip: tip };
}

function renderHistory(d){
  d = d || {};
  lastHistory = d;
  renderWeeklyCard(d);   // 顶部本周汇总横条（空数据时自己画空态）
  const monthBadge = document.getElementById('h-month');
  if (monthBadge) monthBadge.textContent = d.month ? (d.month + ' 本月') : '-';

  if (!d.has_data) {
    /* 没有历史数据时也要把界面画出来：列 = 最近 7 天，行 = 已启用游戏，格子全灰；
       游戏概览同样画满每款启用游戏的迷你卡（今日状态显示"今日无数据"）——
       用户要看的是"界面长什么样、缺的只是数据"，不是一块空白。 */
    if ((d.tracked || []).length && (d.days || []).length) {
      const statsBox = document.getElementById('h-stats');
      if (statsBox) {
        // 概览卡用与正常态同一套迷你卡骨架：streak/本月统计以 0 起步，
        // 今日状态显示"今日无数据"，第一次巡检写入后原样刷新成真实数据
        statsBox.innerHTML = '<div class="gcards">' +
          d.games.map(gameCardHtml).join('') + '</div>';
      }
      renderHeatmap(d);
      const box = document.getElementById('h-table');
      if (box) {
        box.insertAdjacentHTML('beforeend',
          '<div class="hint">暂无历史记录，下一次定时检查写入后会自动填上。</div>');
      }
      return;
    }
    renderHistoryEmpty('暂无历史记录，等待下一次定时检查写入。');
    return;
  }
  const games = d.games || [];
  if (!games.length) {
    renderHistoryEmpty('没有启用任何游戏账号。');
    return;
  }

  // --- 游戏概览：每款一张迷你卡 ---
  const statsBox = document.getElementById('h-stats');
  if (statsBox) {
    statsBox.innerHTML = '<div class="gcards">' +
      games.map(gameCardHtml).join('') + '</div>';
  }
  renderHeatmap(d);
}

function gameIconHtml(gid, cls){
  const klass = cls || 'gcard-ico';
  const base = GAME_ICON[gid];
  if (!base) {
    return '<span class="' + klass + ' ph">' + escHtml(String(gid).slice(0, 1).toUpperCase()) + '</span>';
  }
  const v = ICON_STAMP.indexOf('__') === 0 ? '' : '?v=' + ICON_STAMP;
  return '<img class="' + klass + '" width="40" height="40" alt="" src="' + base + '.png' + v +
         '" srcset="' + base + '@2x.png' + v + ' 2x">';
}

function todayCellFor(d, gid){
  const days = (d && d.days) || [];
  for (let i = days.length - 1; i >= 0; i--) {
    if (days[i].is_today) return (days[i].games || {})[gid] || null;
  }
  return null;
}

function gameCardHtml(g){
  const gid = g.game;
  const today = todayCellFor(lastHistory, gid);
  const live = (window.__liveMap || {})[gid];
  const raw = (window.__liveAll || []).filter(function(x){
    return x && x.game === gid && x.placeholder !== true;
  })[0];

  /* 今日状态（一眼层）：今天要不要动手。
     优先级：查询失败（红，需要行动）> 待上线更新/未创建角色（灰，非失败）
             > 实时判定 > 巡检历史 > 无数据。
     彩色徽章只给异常，已完成/待更新/无数据一律灰字（项目色彩纪律）。 */
  let badge, why = '';
  if (raw && raw.ok === false) {
    badge = '<span class="badge err">查询失败</span>';
    why = String(raw.error || '查询失败');
  } else if (raw && raw.stale) {
    badge = '<span class="dim">待上线更新</span>';
    why = String(raw.stale);
  } else if (raw && raw.no_account) {
    badge = '<span class="dim">未创建角色</span>';
    why = String(raw.no_account);
  } else if (live && live.finished != null) {
    const miss = (live.tasks || []).filter(function(t){ return !t.done; }).length;
    badge = live.finished ? '<span class="dim">✔ 今日已完成</span>'
                          : '<span class="badge warn">未完成 ' + miss + ' 项</span>';
  } else if (!today || !today.known) {
    badge = '<span class="dim">今日无数据</span>';
  } else if (today.finished) {
    badge = '<span class="dim">✔ 今日已完成</span>';
  } else {
    badge = '<span class="badge warn">未完成 ' + (today.unfinished_tasks || 0) + ' 项</span>';
  }

  /* 体力（一眼层）：数值/进度条/预计回满，实时化成果原样保留。
     三态全用真实值（2026-11-07 用户终裁）：未满 N/N、满 N/N、
     道具突破上限后 N/N（current 原样透传，服务器给多少显示多少）——
     「已满」只出现在进度条悬浮提示里，不替代数字 */
  let stamina;
  if (live && live.percent != null) {
    const full = live.current >= live.maximum;
    const over = live.current > live.maximum;
    const hot = live.percent >= 85;
    const tip = over ? '超出上限 ' + (live.current - live.maximum) + '（道具/效果临时提升）'
      : full ? '体力已满' : '';
    stamina = '<div class="gcard-stam"><span class="dim">' + escHtml(live.name || '体力') +
      '</span><span class="sbar"><i class="' + (hot ? 'hot' : '') +
      '" style="width:' + live.percent + '%"></i><i class="progress-glow' +
      (hot ? ' hot' : '') + '" style="left:' + live.percent + '%"></i></span>' +
      '<span class="mono"' + (tip ? ' title="' + escHtml(tip) + '"' : '') + '>' +
      live.current + '/' + live.maximum + '</span>' +
      staminaEtaHtml(gid, live) + '</div>';
  } else {
    stamina = '<div class="gcard-stam"><span class="dim">体力</span>' +
      '<span class="dim">到「信息查询」查询后显示</span></div>';
  }

  /* 回顾信息（悬浮层）：上次检查｜每日任务明细｜本月统计。
     顺序固定，一项都不删；#tip 是 textContent，换成「｜」分隔让它自然折行。 */
  let taskTip;
  if (why) {
    taskTip = why;                       // 失败/待更新/未建号的原因（长文只进 tip）
  } else if (live && (live.tasks || []).length) {
    taskTip = live.tasks.map(function(t){
      const hit = /(\d+)\s*\/\s*(\d+)/.exec(t.progress || '');
      const val = hit ? (parseInt(hit[1], 10) + '/' + parseInt(hit[2], 10))
                      : (t.progress ? String(t.progress) : (t.done ? '✓' : '✗'));
      return t.name + ' ' + val;
    }).join(' · ');
  } else if (today && today.known) {
    taskTip = today.finished ? '今日已完成'
      : '无实时明细 · 历史未完成 ' + (today.unfinished_tasks || 0) + ' 项';
  } else {
    taskTip = '无实时明细';
  }
  const missed = Number(g.month_missed || 0);
  const tip = '上次检查 ' + (today && today.last ? shortStamp(today.last) : '—') +
    ' ｜ 每日任务：' + taskTip +
    ' ｜ 本月完成 ' + Number(g.month_done || 0) + ' 天' + (missed > 0 ? ' · 漏 ' + missed : '');

  /* 连续天数保护话术：streak > 0 且今天还没做完时，用损失厌恶框架提醒
     「别断」——琥珀不用红（对比是信息不是告警，色彩纪律） */
  const streakAtRisk = Number(g.streak || 0) > 0 &&
    ((live && live.finished === false) || (today && today.known && !today.finished));

  return '<div class="card gcard" style="--gc:' + gameDotColor(gid) + '" data-tip="' + escHtml(tip) + '">' +
    '<div class="gcard-head">' + gameIconHtml(gid) +
      '<div class="gcard-name"><b>' + escHtml(gameBaseName(g)) + '</b></div>' +
      '<span class="gcard-today" style="flex:none">' + badge + '</span>' +
      '<div class="gcard-streak"><b class="value">' + Number(g.streak || 0) + '</b><small>连续天数' +
        (streakAtRisk ? ' · <span style="color:var(--warn)">今天别断</span>' : '') + '</small></div>' +
    '</div>' +
    stamina +
    '</div>';
}

/* GitHub 式热力图：行 = 游戏，列 = 日期；绿=完成 红=未完成 灰=无数据 */
function renderHeatmap(d){
  const box = document.getElementById('h-table');
  if (!box) return;
  const days = d.days || [];
  const tracked = d.tracked || [];
  if (!tracked.length) {
    box.innerHTML = '<div class="emptybox">没有启用任何游戏账号。</div>';
    return;
  }
  const nameOf = {};
  (d.games || []).forEach(function(g){ nameOf[g.game] = g.game_name || g.game; });

  let html = '<div class="heatmap">';
  html += '<div class="hm-row hm-head"><div class="hm-label"></div>' +
    days.map(function(row){
      return '<div class="hm-cell' + (row.is_today ? ' today' : '') + '">' +
        escHtml(String(row.label || '').replace(/^\d{4}-/, '')) + '</div>';
    }).join('') + '</div>';

  tracked.forEach(function(gid){
    html += '<div class="hm-row"><div class="hm-label">' +
      '<span class="gdot" style="background:' + gameDotColor(gid) + '"></span>' +
      escHtml(gameBaseName({ game: gid, game_name: nameOf[gid] })) + '</div>';
    days.forEach(function(row){
      const c = (row.games || {})[gid];
      /* 今日列合并实时查询（与游戏卡/KPI 同一「live 说了算」纪律）：
         live 报告已完成 → 今日格立即变绿，不等下一轮巡检；
         live 未覆盖/未完成 → 维持巡检事实或"等待中"，绝不提前画红叉 */
      const lv = (row.is_today && window.__liveMap) ? window.__liveMap[gid] : null;
      const st = hmCellState(c, row, lv);
      html += '<div class="hm-cell ' + st.cls + (row.is_today ? ' today' : '') +
        '" title="' + escHtml(st.tip) + '">' + st.mark + '</div>';
    });
    html += '</div>';
  });

  html += '</div>' +
    '<div class="hm-legend">' +
    '<span class="hm-cell ok">✓</span>完成' +
    '<span class="hm-cell err">✗</span>未完成' +
    '<span class="hm-cell na">⋯</span>等待中/无数据' +
    '</div>';
  box.innerHTML = html;
}

function loadHistory(){
  kpiBusy(true);
  return fetch('/api/history?days=7')
    .then(function(r){
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function(d){
      try {
        cacheSet('history', d);   // 落缓存，下次秒开
        renderHistory(d);
        renderKpiToday(d);
        loadWeekTrend();          // 周对比徽章旁路：独立 fetch，失败不影响主链
        /* 报表刷新时顺带更新月报——两个视图读同一份 history，
           用户点了刷新就该看到全部同步，而不是只更新一半。 */
        const mmask = document.getElementById('month-modal');
        if (mmask && !mmask.hidden) loadMonthGrid(monthView, true);
      } catch (e) {
        console.error('渲染历史报表失败', e);
        renderHistoryEmpty('报表渲染出错，请点「刷新」重试。');
      }
    })
    .catch(function(err){
      console.error('读取历史记录失败', err);
      /* 有缓存时静默失败：上次的数据还在页面上，不打断用户 */
      if (!lastHistory) {
        renderHistoryEmpty('暂无历史记录，等待下一次定时检查写入。',
                           err && err.message ? '（' + err.message + '）' : '');
      }
    })
    .then(function(){ kpiBusy(false); });
}

/* 报表页刷新已合并：概览卡的旧「刷新」按钮（2026-11-01 减法裁决移除）与本按钮
   功能九成重叠，现只保留本周卡这一个入口，行为 = 实时查询（写历史）→ 重拉报表。
   失败静默保留旧数据，与 loadHistory 同一口径 */
document.getElementById('btn-hist-refresh').addEventListener('click', function(){
  const btn = this;
  if (!btn || btn.disabled) return;              // 防连点
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span>';
  Promise.resolve(loadLive(true))                // 实时查询（写历史）→ 再拉报表
    .then(function(){ return loadHistory(); })
    .catch(function(err){ console.error('刷新报表失败', err); })
    .then(function(){ btn.disabled = false; btn.textContent = '刷新'; });
});

/* 游戏概览卡的刷新（2026-11-06）：只重拉报表并重绘本卡，不触发游戏查询。
   2026-11-09 用户裁决：刷新不查询就是假刷新——概览刷新与信息查询页
   同款 loadLive(true) 真查一遍（写历史、受风控冷却保护），完成后把
   报表一起重绘。查询耗时约 5~15 秒，转圈反馈。 */
document.getElementById('btn-stats-refresh').addEventListener('click', function(){
  const btn = this;
  if (!btn || btn.disabled) return;              // 防连点
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span>';
  loadLive(true)
    .then(function(){ return loadHistory(); })
    .catch(function(err){
      console.error('刷新游戏概览失败', err);
      toast('刷新失败：' + ((err && err.message) || '无法连接本地服务'), true);
    })
    .then(function(){ btn.disabled = false; btn.textContent = '刷新'; });
});

/* 立即巡检（2026-11-05 P2）：让 daemon 跑一轮并写历史，绑号当天不用等到
   明天第一个时间点才见报表。成功后 12 秒自动重拉一次报表（daemon 0.5s 内
   响应 .run_now 标记，一轮并行查询约 6-15s）。 */
document.getElementById('btn-run-now').addEventListener('click', function(){
  const btn = this;
  if (!btn || btn.disabled) return;              // 防连点
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span>';
  fetch('/api/daemon/run-now', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  })
    .then(function(r){ return r.json(); })
    .then(function(res){
      btn.disabled = false; btn.textContent = '立即巡检';
      if (res && res.ok) {
        kpiSet('k-next-sub', '已触发巡检，约十几秒后自动更新报表');
        setTimeout(function(){
          Promise.resolve(loadHistory()).catch(function(){});
        }, 12000);
      } else {
        kpiSet('k-next-sub', (res && res.message) || '触发失败，请查看运行日志');
      }
    })
    .catch(function(err){
      btn.disabled = false; btn.textContent = '立即巡检';
      console.error('立即巡检失败', err);
      kpiSet('k-next-sub', '触发失败：' + err);
    });
});

/* ========================= 月报下钻（2026-11-06，抽屉选单版） ========================= */
/* 徽章/每月报表按钮点击 → 整月月历弹窗。视图单一入口 = 抽屉按钮：
   按钮上显示当前视图名（总表 / 游戏名），点开下拉选单（总表 + 各游戏），
   选中谁渲染谁；旁边小三角 ◀（收纳）/ ▼（展开）跟随状态。
   总表 = GitHub 贡献图范式（深浅 = 当日完成委托的游戏数）；
   游戏视图 = 该游戏整月历（固定色 ✓绿/✗红/⋯灰）。
   格子悬浮复用 data-tip；hmCellState 供两视图共用。 */
let monthView = null;                          // 当前查看的月份（YYYY-MM）
let monthNav = { prev: null, next: null };
let monthGame = null;                          // null = 总表；否则 = 游戏 id
let monthMenuOpen = false;
let monthTimer = null;                         // 弹窗打开期间的自动刷新定时器
let monthSig = '';                             // 上次渲染的数据指纹，无变化则不重绘
const MONTH_REFRESH_MS = 30000;                 // 与 loadToasts / loadConsoleLast 同节奏

function monthGameMark(c, row){
  if (c.future) return '';
  if (c.known) return c.finished ? '✓' : '✗';
  if ((c.checks || 0) > 0 && c.err) return '失败';
  if ((c.checks || 0) > 0 && (c.stale || c.noacc)) return '无数据';
  return '⋯';
}

function openMonthModal(){
  const mask = document.getElementById('month-modal');
  if (!mask) return;
  mask.hidden = false;
  if (!monthView) {
    const n = new Date();   // 月报视图按自然月（month_grid 同口径）
    monthView = n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0');
  }
  loadMonthGrid(monthView);
  startMonthTimer();
}

function closeMonthModal(){
  const mask = document.getElementById('month-modal');
  if (mask) mask.hidden = true;
  stopMonthTimer();
  closeMonthMenu();
}

/* 弹窗开着的时候数据会变（巡检随时写 history），必须自己跟上——
   否则「当日完成」永远停在打开弹窗那一刻，得关掉重开才更新。
   只在弹窗可见时轮询，关掉即停，不占资源。 */
function startMonthTimer(){
  stopMonthTimer();
  monthTimer = setInterval(function(){
    const mask = document.getElementById('month-modal');
    if (!mask || mask.hidden) { stopMonthTimer(); return; }
    if (document.hidden) return;          // 窗口最小化/切走时不刷
    loadMonthGrid(monthView, true);       // silent：不闪「读取中」
  }, MONTH_REFRESH_MS);
}

function stopMonthTimer(){
  if (monthTimer) { clearInterval(monthTimer); monthTimer = null; }
}

function closeMonthMenu(){
  monthMenuOpen = false;
  const menu = document.getElementById('month-drawer-menu');
  if (menu) menu.hidden = true;
  const arrow = document.getElementById('month-arrow');
  if (arrow) arrow.classList.remove('open');
}

function toggleMonthMenu(){
  monthMenuOpen = !monthMenuOpen;
  const menu = document.getElementById('month-drawer-menu');
  if (menu) menu.hidden = !monthMenuOpen;
  const arrow = document.getElementById('month-arrow');
  if (arrow) arrow.classList.toggle('open', monthMenuOpen);
}

function monthViewLabel(){
  if (monthGame) {
    const g = ((window.__lastMonth && window.__lastMonth.games) || [])
      .filter(function(x){ return x.game === monthGame; })[0];
    return g ? g.game_name : monthGame;
  }
  return '总表';
}

function loadMonthGrid(month, silent){
  const body = document.getElementById('month-body');
  if (!body) return;
  if (!silent) body.innerHTML = '<div class="emptybox">读取中…</div>';
  if (!silent) closeMonthMenu();
  fetch('/api/history?month=' + encodeURIComponent(month || ''), { cache: 'no-store' })
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (!d || d.ok === false) {
        if (!silent) body.innerHTML = '<div class="emptybox">' +
          escHtml((d && d.message) || '读取失败') + '</div>';
        return;
      }
      monthView = d.month;
      monthNav = { prev: d.prev || null, next: d.next || null };
      window.__lastMonth = d;
      /* 自动刷新：数据没变就不重绘（重绘会把用户滚动位置与
         悬浮细节冲掉，白白打断浏览）。只看「日期+每游戏完成态」。 */
      const sig = monthDataSig(d);
      if (silent && sig === monthSig) return;
      monthSig = sig;
      renderMonthModal(d);
    })
    .catch(function(e){
      if (!silent) body.innerHTML = '<div class="emptybox">读取失败：' + escHtml(String(e)) + '</div>';
    });
}

/* 渲染相关指纹：日期集合 + 每格每游戏的 finished/known/checks。
   不含统计数与 label——那些由前者推导，变了必然一起变。 */
function monthDataSig(d){
  const parts = [];
  (d.days || []).forEach(function(row){
    const cells = [];
    const gs = row.games || {};
    Object.keys(gs).sort().forEach(function(k){
      const c = gs[k] || {};
      cells.push(k + ':' + (c.finished ? 1 : 0) + (c.known ? 1 : 0) + (c.checks || 0));
    });
    parts.push(row.date + '|' + (row.future ? 'F' : '') + '|' + cells.join(','));
  });
  (d.games || []).forEach(function(g){ parts.push(g.game + '=' + g.done + '/' + g.missed); });
  return parts.join(';');
}

function renderMonthModal(d){
  document.getElementById('month-title').textContent = d.label + ' · 月报';
  document.getElementById('month-prev').disabled = !d.prev;
  document.getElementById('month-next').disabled = !d.next;
  /* 抽屉按钮：显示当前视图名 + 三角朝向（▼ 展开 / ◀ 收纳） */
  document.getElementById('month-view-label').textContent = monthViewLabel();
  document.getElementById('month-arrow').classList.toggle('open', monthMenuOpen);
  /* 选单内容：总表 + 各游戏（当前视图高亮） */
  const menuEl = document.getElementById('month-drawer-menu');
  if (menuEl) menuEl.innerHTML =
    '<button type="button" data-view="heat"' + (monthGame ? '' : ' class="cur"') +
    '>总表</button>' +
    d.games.map(function(g){
      return '<button type="button" data-game="' + escHtml(g.game) + '"' +
        (g.game === monthGame ? ' class="cur"' : '') + '>' +
        '<span class="gdot" style="background:' + gameDotColor(g.game) + '"></span>' +
        escHtml(g.game_name) + '</button>';
    }).join('');

  const statLines = d.games.map(function(g){
    let t = escHtml(g.game_name) + ' 完成 ' + g.done + ' 天';
    if (g.missed > 0) t += ' · 漏 ' + g.missed;
    if (g.nodata > 0) t += ' · 查无数据 ' + g.nodata;
    return '· ' + t;
  }).join('<br>');
  const legend = '<div class="hm-legend">' +
    '<span class="hm-cell ok">✓</span>完成' +
    '<span class="hm-cell err">✗</span>未完成' +
    '<span class="hm-cell na">⋯</span>等待中/无数据' +
    '</div>';

  let body;
  if (monthGame) {
    /* 单游戏：该游戏整月历——固定颜色（✓绿/✗红/⋯灰），日数字 + 悬浮明细 */
    const cur = d.games.filter(function(g){ return g.game === monthGame; })[0] || d.games[0];
    let html = '<div class="mc-grid">';
    const wdMap = { '周一': 0, '周二': 1, '周三': 2, '周四': 3, '周五': 4, '周六': 5, '周日': 6 };
    const lead = wdMap[d.days[0].weekday] || 0;
    for (let i = 0; i < lead; i++) html += '<div class="mc-day blank"></div>';
    d.days.forEach(function(row){
      if (row.future) {
        html += '<div class="mc-day future" title="还没到"></div>';
        return;
      }
      const c = (row.games || {})[cur.game] || {};
      const mk = monthGameMark(c, row);
      const cls = mk === '✓' ? ' done' : mk === '✗' ? ' miss' : '';
      const tip = (c.known
        ? (c.finished ? '已完成' : '未完成 ' + (c.unfinished_tasks || 0) + ' 项')
        : ((c.checks || 0) > 0 && c.err) ? '检查失败：' + c.err
        : ((c.checks || 0) > 0) ? '查无数据（当日无有效记录）' : '当日未巡检');
      html += '<div class="mc-day g' + cls + (row.is_today ? ' today' : '') +
        '" data-tip="' + escHtml(row.label + '｜' + tip) + '"><span>' + Number(String(row.date).slice(-2)) + '</span></div>';
    });
    for (let i = lead + d.days.length; i % 7 !== 0; i++) html += '<div class="mc-day blank"></div>';
    html += '</div>';
    const sum = '当月：完成 ' + cur.done + ' 天' +
      (cur.missed > 0 ? ' · 漏 ' + cur.missed : '') +
      (cur.nodata > 0 ? ' · 查无数据 ' + cur.nodata : '');
    body = '<div class="month-scroll">' + html + '</div>' +
      '<div class="month-stats">' + escHtml(sum) + '</div>';
  } else {
    /* 总表：GitHub 贡献图范式 —— 周一~周日 7 列按周分行，
       颜色深浅 = 当日完成委托的游戏数，悬浮给出当天逐游戏明细 */
    const wdMap = { '周一': 0, '周二': 1, '周三': 2, '周四': 3, '周五': 4, '周六': 5, '周日': 6 };
    const lead = wdMap[d.days[0].weekday] || 0;
    const total = d.games.length || 1;
    let html = '<div class="mc-grid">';
    ['一', '二', '三', '四', '五', '六', '日'].forEach(function(w){
      html += '<div class="mc-wd">' + w + '</div>';
    });
    for (let i = 0; i < lead; i++) html += '<div class="mc-day blank"></div>';
    d.days.forEach(function(row){
      if (row.future) {
        html += '<div class="mc-day future" title="还没到"></div>';
        return;
      }
      let done = 0;
      const parts = d.games.map(function(g){
        const c = (row.games || {})[g.game] || {};
        if (c.known && c.finished) done += 1;
        return g.game_name + ' ' + monthGameMark(c, row);
      });
      const lv = done === 0 ? 0 : Math.max(1, Math.ceil(done / total * 4));
      const tip = '完成 ' + done + '/' + total + '｜' + parts.join(' · ');
      html += '<div class="mc-day mc-l' + lv + (row.is_today ? ' today' : '') +
        '" data-tip="' + escHtml(row.label + '｜' + tip) + '"><span>' + Number(String(row.date).slice(-2)) + '</span></div>';
    });
    for (let i = lead + d.days.length; i % 7 !== 0; i++) html += '<div class="mc-day blank"></div>';
    html += '</div>';
    body = '<div class="month-scroll">' + html + '</div>' +
      '<div class="mc-legend">少' +
      '<span class="mc-legend-cell mc-l0"></span>' +
      '<span class="mc-legend-cell mc-l1"></span>' +
      '<span class="mc-legend-cell mc-l2"></span>' +
      '<span class="mc-legend-cell mc-l3"></span>' +
      '<span class="mc-legend-cell mc-l4"></span>' +
      '多</div>';
  }
  document.getElementById('month-body').innerHTML = body;
}

const hMonthEntry = document.getElementById('h-month-entry');
if (hMonthEntry) {
  hMonthEntry.addEventListener('click', openMonthModal);
}
document.getElementById('month-view-btn').addEventListener('click', function(e){
  e.stopPropagation();   // 防止冒泡到遮罩的空白关闭
  toggleMonthMenu();
});
document.getElementById('month-drawer-menu').addEventListener('click', function(e){
  const b = e.target.closest ? e.target.closest('button[data-view], button[data-game]') : null;
  if (!b) return;
  monthGame = b.dataset.view === 'heat' ? null : (b.dataset.game || null);
  if (window.__lastMonth) renderMonthModal(window.__lastMonth);
  closeMonthMenu();
});
document.addEventListener('click', function(e){
  if (!monthMenuOpen) return;
  const drawer = e.target.closest ? e.target.closest('.month-drawer') : null;
  if (!drawer) closeMonthMenu();   // 点抽屉外任意处收起选单
});
document.getElementById('month-prev').addEventListener('click', function(){
  if (monthNav.prev) loadMonthGrid(monthNav.prev);
});
document.getElementById('month-next').addEventListener('click', function(){
  if (monthNav.next) loadMonthGrid(monthNav.next);
});
document.getElementById('month-close').addEventListener('click', closeMonthModal);
document.getElementById('month-modal').addEventListener('click', function(e){
  if (e.target === this) closeMonthModal();   // 点遮罩空白处关闭
});


/* ========================= 主题（三态：跟随系统 / 深色 / 浅色） ========================= */
/* 默认跟随 Windows 系统深浅；点侧栏底部按钮可强制深色或浅色，选择写进
   localStorage 的 rl-theme，下次启动沿用。
   注意：exe 壳原本在每次文档创建时 removeItem('rl-theme')（为保证页面与壳同口径），
   那句已同步从 shell/MainForm.cs 移除 —— 否则这里存的值每次启动都会被抹掉。
   auto 模式下仍然实时跟随系统；手动模式下系统切换不影响用户的选择。 */
const systemDark = window.matchMedia('(prefers-color-scheme: dark)');
const THEME_MODES = ['auto', 'dark', 'light'];
const THEME_META = {
  auto:  ['<svg class="ico"><use href="#i-monitor"/></svg>', '跟随系统'],
  dark:  ['<svg class="ico"><use href="#i-moon"/></svg>', '深色'],
  light: ['<svg class="ico"><use href="#i-sun"/></svg>', '浅色'],
};

function readThemeMode(){
  try {
    const v = localStorage.getItem('rl-theme');
    return THEME_MODES.indexOf(v) >= 0 ? v : 'auto';
  } catch (e) { return 'auto'; }
}

function applyTheme(){
  const mode = readThemeMode();
  const dark = mode === 'auto' ? systemDark.matches : (mode === 'dark');
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const meta = THEME_META[mode] || THEME_META.auto;
  const ico = document.getElementById('theme-ico');
  const txt = document.getElementById('theme-txt');
  const btn = document.getElementById('btn-theme');
  if (ico) ico.innerHTML = meta[0];
  if (txt) txt.textContent = meta[1];
  if (btn) btn.dataset.mode = mode;
}

applyTheme();
if (systemDark.addEventListener) {
  systemDark.addEventListener('change', applyTheme);
}

const themeBtn = document.getElementById('btn-theme');
if (themeBtn) {
  themeBtn.addEventListener('click', function () {
    const cur = themeBtn.dataset.mode || 'auto';
    const next = THEME_MODES[(THEME_MODES.indexOf(cur) + 1) % THEME_MODES.length];
    try { localStorage.setItem('rl-theme', next); } catch (e) {}
    applyTheme();
  });
}

/* ========================= 推送预览 ========================= */
/* 文案与手机实发同源：标题/正文一律来自 GET /api/push_preview
   （后端 core/analyzer.py 的 to_push_payload 同一条组装路径），
   前端只渲染、绝不自拼 —— tests/rl_test_push_preview.py 守卫这条纪律。 */
function renderPushPreview(j){
  const titleEl = document.getElementById('pv-title');
  const bodyEl = document.getElementById('pv-body');
  const chEl = document.getElementById('pv-channel');
  if (!titleEl || !bodyEl) return;

  const channels = [];
  const sc = document.getElementById('serverchan-enabled');
  const qm = document.getElementById('qmsg-enabled');
  if (sc && sc.checked) channels.push('Server酱');
  if (qm && qm.checked) channels.push('Qmsg 酱');
  if (chEl) chEl.textContent = channels.length
    ? ('推送到 ' + channels.join(' + ')) : '未启用推送渠道';

  titleEl.textContent = (j && j.title) || '';
  bodyEl.textContent = (j && j.body) || '';
}

function loadPushPreview(){
  fetch('/api/push_preview').then(function(r){ return r.json(); })
    .then(renderPushPreview)
    .catch(function(){
      const t = document.getElementById('pv-title');
      if (t) t.textContent = '预览暂不可用';
      const b = document.getElementById('pv-body');
      if (b) b.textContent = '读取推送预览失败，请稍后重试。';
    });
}

/* ========================= 今日巡检动态 ========================= */
function renderRunLog(h){
  const box = document.getElementById('run-log');
  if (!box) return;
  const days = (h && h.days) || [];
  const nameOf = {};
  ((h && h.games) || []).forEach(function(g){ nameOf[g.game] = g.game_name || g.game; });
  const tracked = (h && h.tracked) || [];
  const rows = days.filter(function(row){
    const cells = row.games || {};
    return Object.keys(cells).some(function(k){ return cells[k] && cells[k].known; });
  }).slice(-14).reverse();

  if (!rows.length) {
    box.innerHTML = '<div class="emptybox">还没有巡检记录，等第一次定时检查跑完后这里会有动态。</div>';
    return;
  }
  /* 三列时间轴：时间列 / 节点列 / 内容列；节点色 = 本次检查整体状态
     绿 = 参与检查的游戏全部完成 · 琥珀 = 仍有未完成 · 灰 = 无有效记录 */
  box.innerHTML = '<div class="tl">' + rows.map(function(row){
    const cells = row.games || {};
    const done = [], miss = [];
    let lastTime = '';
    tracked.forEach(function(gid){
      const c = cells[gid];
      if (!c || !c.known) return;
      if (c.last && c.last > lastTime) lastTime = c.last;
      if (c.finished) done.push(nameOf[gid] || gid);
      else miss.push(nameOf[gid] || gid);
    });
    const state = (!done.length && !miss.length) ? 'none' : (miss.length ? 'warn' : 'ok');
    const sumText = state === 'none' ? '无有效记录'
      : (miss.length ? miss.length + ' 款未完成' : '全部完成');
    const detail = miss.length
      ? '<div class="tl-desc">未完成：' + escHtml(miss.join('、')) + '</div>'
      : (done.length ? '<div class="tl-desc">已完成：' + escHtml(done.join('、')) + '</div>' : '');
    const when = lastTime ? '<span class="tl-when">' + escHtml(lastTime) + '</span>' : '';
    return '<div class="tl-item" data-state="' + state + '">' +
      '<div class="tl-time"><b>' + escHtml(row.label || '') + '</b>' +
        escHtml((row.weekday || '') + (row.is_today ? ' · 今天' : '')) + '</div>' +
      '<div class="tl-node"></div>' +
      '<div class="tl-body">' +
        '<div class="tl-title"><span class="tl-sum' +
          (miss.length ? ' warn' : '') + '">' + escHtml(sumText) + '</span>' + when + '</div>' +
        detail +
      '</div>' +
      '</div>';
  }).join('') + '</div>';
}

function loadRunLog(){
  fetch('/api/history?days=7').then(function(r){ return r.json(); })
    .then(renderRunLog)
    .catch(function(){
      const box = document.getElementById('run-log');
      if (box) box.innerHTML = '<div class="emptybox">读取巡检记录失败，请点「刷新」重试。</div>';
    });
}

document.getElementById('btn-log-refresh')
  .addEventListener('click', loadRunLog);

/* ========================= 全局 Tooltip ========================= */
const tipBox = document.createElement('div');
tipBox.id = 'tip';
document.body.appendChild(tipBox);

document.addEventListener('mouseover', function(e){
  const el = e.target.closest ? e.target.closest('[data-tip]') : null;
  if (!el) return;
  const text = el.getAttribute('data-tip') || '';
  if (!text) return;
  tipBox.textContent = text;
  tipBox.classList.add('on');          // 先显示才能量出气泡尺寸
  const r = el.getBoundingClientRect();
  const tw = tipBox.offsetWidth, th = tipBox.offsetHeight;
  let x = r.left + r.width / 2 - tw / 2;
  x = Math.max(8, Math.min(x, window.innerWidth - tw - 8));
  let y = r.top - th - 8;              // 默认在图标上方，贴顶时翻到下方
  if (y < 8) y = r.bottom + 8;
  tipBox.style.left = Math.round(x) + 'px';
  tipBox.style.top = Math.round(y) + 'px';
});
document.addEventListener('mouseout', function(e){
  if (e.target.closest && e.target.closest('[data-tip]')) tipBox.classList.remove('on');
});
window.addEventListener('scroll', function(){ tipBox.classList.remove('on'); }, true);

/* ========================= KPI 摘要条 ========================= */
function kpiSet(id, text){
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

/* 紧迫感系统：把 warn/critical 写到 KPI 卡片的 data-urgency 上 */
function setUrgency(id, level){
  const el = document.getElementById(id);
  if (!el) return;
  const card = el.closest('.kpi');
  if (card) card.dataset.urgency = level;
}

/* KPI 智能吸顶：滚动容器是 .main（不是 window） */
const kpiBarEl = document.getElementById('kpi-bar');
const kpiMainEl = document.querySelector('.main');

function kpiUrgencyScore(card){
  const u = card.dataset.urgency;
  return u === 'critical' ? 2 : (u === 'warn' ? 1 : 0);
}

/* compact 态只保留最紧急的 2 个信号（不足 2 个紧急时用普通卡补位） */
function refreshKpiKeep(){
  if (!kpiBarEl) return;
  const cards = Array.prototype.slice.call(kpiBarEl.querySelectorAll('.kpi'));
  const sorted = cards.slice().sort(function(a, b){
    return kpiUrgencyScore(b) - kpiUrgencyScore(a);
  });
  sorted.forEach(function(c, i){ c.classList.toggle('keep', i < 2); });
}

/* KPI 吸顶收缩：进入 >进档线、退出 <40px —— 两个阈值必须分开（滞回），不能共用一个。
   为什么共用会出事：compact 会把 KPI 条压矮，浏览器为保持视觉位置不动（滚动锚定）
   会同步把 scrollTop 下调同样的落差。若退出阈值也是进档线，则
     scrollTop 刚过线 → 压缩 → 被锚定推回线下 → 还原 → 又推回线上 → 再压缩 → ……
   形成无限振荡，每次都对 min-height 跑一遍 250ms 过渡，画面就是「滚动时图标反复
   重叠 / 抖重影」「一向下滚就被弹回顶部」。所有 tab 都中招，因为它挂在全局唯一的
   #kpi-bar 上。
   落差不是常数：宽幅 ~24px（80→56），≤1000px 双列两行卡实测 133px（914×600：
   213→80）——固定 40px 间隙在窄幅照样振荡（压缩后 scrollTop 被锚定推到 40 以下）。
   进档线因此按两档实测高度差自适应：max(80, 40+落差+8)，保证压缩后 scrollTop
   仍 ≥48，永远跌不破退档线 40。落差随窗口/主题漂移在每次滚动事件里重测自愈。 */
const KPI_COMPACT_ENTER = 80;   /* 进档线；宽幅下即最终进档线，窄幅被自适应抬高 */
const KPI_COMPACT_EXIT  = 40;   /* 退档线；压缩后 scrollTop ≥ 48，留 8px 余量 */
let kpiCompact = false;
const kpiBarH = { normal: 0, compact: 0 };   /* 两档实高缓存，落差 = normal - compact */

function updateKpiCompact(){
  if (!kpiBarEl || !kpiMainEl) return;
  const on = kpiBarEl.classList.contains('compact');
  kpiBarH[on ? 'compact' : 'normal'] = kpiBarEl.offsetHeight;
  const delta = (kpiBarH.normal > 0 && kpiBarH.compact > 0)
    ? kpiBarH.normal - kpiBarH.compact : 24;
  const enter = Math.max(KPI_COMPACT_ENTER, KPI_COMPACT_EXIT + delta + 8);
  const top = kpiMainEl.scrollTop;
  if (!kpiCompact && top > enter) kpiCompact = true;
  else if (kpiCompact && top < KPI_COMPACT_EXIT) kpiCompact = false;
  kpiBarEl.classList.toggle('compact', kpiCompact);
}
if (kpiMainEl) kpiMainEl.addEventListener('scroll', updateKpiCompact, {passive:true});
if (kpiBarEl) {
  /* 预热两档实高：否则首次进档只能用 24px 兜底落差，窄幅第一滚仍会闪一轮 */
  const wasCompact = kpiBarEl.classList.contains('compact');
  kpiBarEl.classList.add('compact');
  kpiBarH.compact = kpiBarEl.offsetHeight;
  if (!wasCompact) kpiBarEl.classList.remove('compact');
  kpiBarH.normal = kpiBarEl.offsetHeight;
}
updateKpiCompact();

/* 今日完成：巡检历史 + 实时查询合并统计。
   数据源纪律：/api/live 查询成功的游戏以 live 为准（现场状态最权威）；
   live 没查到/查询失败的回落 /api/history 的粘性完成。两路永远不会打架。 */
function renderKpiToday(h){
  const el = document.getElementById('k-today');
  if (!el) return;
  const days = (h && h.days) || [];
  let row = null;
  for (let i = days.length - 1; i >= 0; i--) {
    if (days[i].is_today) { row = days[i]; break; }
  }
  const tracked = (h && h.tracked) || [];
  const cells = (row && row.games) || {};
  const liveMap = window.__liveMap || {};
  let done = 0, known = 0;
  tracked.forEach(function(gid){
    const lv = liveMap[gid];
    if (lv && lv.finished != null) {      // live 说了算
      known++;
      if (lv.finished) done++;
      return;
    }
    const c = cells[gid];
    if (!c || !c.known) return;           // 两路都没有 → 不计入
    known++;
    if (c.finished) done++;
  });
  const total = tracked.length;
  if (!total) {
    el.textContent = '–';
    el.className = 'kpi-value value';
    setUrgency('k-today', '');
    kpiSet('k-today-sub', '没有启用任何游戏');
    return;
  }
  if (!known) {
    /* 无任何有效数据：不假装进度（0/5 会被读成「玩了但没做」） */
    el.textContent = '–';
    el.className = 'kpi-value value';
    setUrgency('k-today', '');
    refreshKpiKeep();
    /* 三态区分（2026-11-05 P2）：检查过但失败 ≠ 从未检查。
       /api/history 现为 known=false 的格子保留 checks/last 与
       err/stale/noacc 尾巴，这里据此分流 */
    let checkedN = 0, failedN = 0, staleN = 0;
    tracked.forEach(function(gid){
      const c = cells[gid];
      if (c && (c.checks || 0) > 0) {
        checkedN++;
        if (c.err) failedN++;
        else if (c.stale || c.noacc) staleN++;
      }
    });
    if (checkedN === 0) {
      kpiSet('k-today-sub', '今日还没检查过，查询中…');
    } else {
      const parts = [];
      if (failedN) parts.push(failedN + ' 款检查失败');
      if (staleN) parts.push(staleN + ' 款未进游戏/未建号');
      const other = checkedN - failedN - staleN;
      if (other > 0) parts.push(other + ' 款暂无当日数据');
      kpiSet('k-today-sub', parts.length
        ? parts.join('，') + '——账号数据未就绪'
        : '今日还没检查过，查询中…');
    }
    return;
  }
  el.textContent = done + '/' + total;
  el.className = 'kpi-value value' + (done !== total ? ' warn' : '');
  /* 0 完成 = critical；部分完成 = warn；全部完成 = 无紧迫（紧迫度只看合并结果，不碰统计） */
  setUrgency('k-today', done === total ? '' : (done > 0 ? 'warn' : 'critical'));
  refreshKpiKeep();
  kpiSet('k-today-sub', done === total ? '今日全部完成' : (total - done) + ' 款未完成');
}

/* 凭证健康：已绑定凭证的账号 / 启用账号（口径=启用，与今日完成 KPI 同分母；
   未勾选游戏不计入 —— payload 自带 enabled 字段，此前两处消费都无视了它） */
function renderKpiCred(d){
  const el = document.getElementById('k-cred');
  if (!el) return;
  const items = ((d && d.items) || []).filter(function(i){ return i.enabled; });
  if (!items.length) {
    el.textContent = '–';
    el.className = 'kpi-value value';
    setUrgency('k-cred', '');
    kpiSet('k-cred-sub', '没有账号');
    return;
  }
  const bound = items.filter(function(i){ return i.has_credential; }).length;
  el.textContent = bound + '/' + items.length;
  el.className = 'kpi-value value' + (bound === items.length ? '' : ' warn');
  setUrgency('k-cred', bound === items.length ? '' : 'warn');
  refreshKpiKeep();
  kpiSet('k-cred-sub', bound === items.length ? '全部已绑定'
    : (items.length - bound) + ' 个未绑定');
}

/* =========================== 心跳：已运行 + 下次巡查 ===========================
   全局唯一的 heartbeatTimer（30 秒一拍），「已运行」和「下次巡查」都从这里刷。
   计算一律 target - Date.now()：起点只认 Python 端给的数据（pid 文件 started_at /
   config 的巡查时刻），严禁拿页面打开时刻之类伪造守护进程的数据。 */
let heartbeatTimer = null;
const PAGE_BOOT = Date.now();   // 仅用于「本次会话」兜底显示，与守护进程数据无关
const hbState = { running: false, startedAtRaw: '', startedAt: null, times: [] };

function parseStamp(text){
  const m = String(text || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : null;
}

function fmtUptime(ms){
  const min = Math.floor(ms / 60000);
  if (min < 1) return '不到 1 分钟';
  const d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), m = min % 60;
  if (d > 0) return d + ' 天 ' + h + ' 小时';
  if (h > 0) return h + ' 小时 ' + m + ' 分';
  return m + ' 分钟';
}

/* 最近的下一个巡查点：每次 tick 都重算；已过今日时刻自动指向明日同一时刻，
   返回值永远晚于 now（归零后下一拍自动切到下一个点，永不出现负数） */
function computeNextRun(now){
  let best = null;
  hbState.times.forEach(function(t){
    const m = String(t).trim().match(/^(\d{1,2}):(\d{1,2})$/);
    if (!m) return;
    const cand = new Date(now.getFullYear(), now.getMonth(), now.getDate(), +m[1], +m[2]);
    if (cand.getTime() <= now.getTime()) cand.setDate(cand.getDate() + 1);
    if (!best || cand.getTime() < best.getTime()) best = cand;
  });
  return best;
}

function renderUptime(){
  const el = document.getElementById('d-since');
  if (!el) return;
  if (!hbState.running) { el.textContent = '-'; el.title = ''; return; }
  if (hbState.startedAt) {
    /* 状态条本身已有「运行」标签，这里只给时长，不再重复「已运行」 */
    el.textContent = fmtUptime(Date.now() - hbState.startedAt.getTime());
    el.title = '启动于 ' + hbState.startedAtRaw;
  } else {
    /* started_at 拿不到（老版 pid 文件只有 pid）：老实标注是控制台会话时长 */
    el.textContent = fmtUptime(Date.now() - PAGE_BOOT) + '（会话）';
    el.title = '守护进程未上报启动时间，此处显示本控制台页面的打开时长';
  }
}

function renderNextRun(){
  const el = document.getElementById('k-next');
  const sub = document.getElementById('k-next-sub');
  if (!el || !sub) return;
  if (!hbState.running) {
    el.textContent = '已暂停';
    sub.textContent = '守护进程未运行';
    return;
  }
  const next = computeNextRun(new Date());
  if (!next) { el.textContent = '–'; sub.textContent = '未设置巡查时间点'; return; }
  const pad = function(n){ return (n < 10 ? '0' : '') + n; };
  el.textContent = pad(next.getHours()) + ':' + pad(next.getMinutes());
  const min = Math.round(Math.max(0, next.getTime() - Date.now()) / 60000);
  const h = Math.floor(min / 60), m = min % 60;
  sub.textContent = min === 0 ? '即将执行'
    : (h > 0 ? h + ' 小时 ' + m + ' 分后' : m + ' 分钟后');
}

function tickHeartbeat(){ renderUptime(); renderNextRun(); }

function startHeartbeat(){
  if (!heartbeatTimer) heartbeatTimer = setInterval(tickHeartbeat, 30000);
}
function stopHeartbeat(){
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
}

/* 体力临满：最近一次「信息查询」结果里 ≥85% 的游戏数 */
function renderKpiResin(d){
  const el = document.getElementById('k-resin');
  if (!el) return;
  if (!d || d.ok === false) return;
  const games = d.games || [];
  let hot = 0, withRes = 0;
  games.forEach(function(g){
    if (!g.ok || !g.resource || g.resource.percent == null) return;
    withRes++;
    if (g.resource.percent >= 85) hot++;
  });
  el.textContent = withRes ? String(hot) : '–';
  el.className = 'kpi-value value' + (hot ? ' warn' : '');
  setUrgency('k-resin', hot > 0 ? 'warn' : '');
  refreshKpiKeep();
  kpiSet('k-resin-sub', withRes
    ? (hot ? hot + ' 款即将溢出' : '体力都还充裕')
    : '到「信息查询」查询后显示');
}

/* ========================= 24 小时标尺 ========================= */
function renderRuler(times){
  const box = document.getElementById('d-ruler');
  if (!box) return;
  let html = '<div class="ruler-track"></div>';
  [0, 6, 12, 18, 24].forEach(function(h){
    html += '<div class="ruler-tick" style="left:' + (h / 24 * 100) + '%">' +
      (h < 10 ? '0' + h : h) + ':00</div>';
  });
  if (!times.length) {
    html += '<div class="ruler-empty">还没有时间点，点击时间轴任意位置即可添加</div>';
  }
  times.forEach(function(t){
    const m = String(t).match(/^(\d{1,2}):(\d{2})$/);
    if (!m) return;
    const pct = ((+m[1] * 60 + +m[2]) / 1440 * 100).toFixed(3);
    html += '<div class="ruler-dot' + (t === selTime ? ' sel' : '') +
            '" data-t="' + t + '" style="left:' + pct + '%" title="' + t + '">' +
            '<span>' + t + '</span></div>';
  });
  box.innerHTML = html;
}

/* ========================= 底部日志控制台 ========================= */
let consoleLoaded = false;
let consoleShowAll = false;   // 展开视图：false=用户视图（隐藏系统消息）

function renderConsole(d){
  const body = document.getElementById('console-body');
  if (!body) return;
  if (!d || d.ok === false) {
    body.innerHTML = '<span class="console-empty">' +
      escHtml((d && d.message) || '读取失败，请点「刷新」重试。') + '</span>';
    return;
  }
  /* 事件流：后端分类好 level/noise；旧响应无 events 时全部按 info 兜底 */
  const events = (d.events && d.events.length)
    ? d.events
    : (d.lines || []).map(function(t){ return {level: 'info', noise: false, text: t}; });
  if (!events.length) {
    body.innerHTML = '<span class="console-empty">还没有日志。</span>';
    return;
  }
  /* 用户视图：噪音块折叠成一条汇总，「显示」可切原始流 */
  const visible = consoleShowAll ? events
    : events.filter(function(e){ return !e.noise; });
  const hidden = events.length - visible.length;
  let html = '';
  if (hidden > 0 || consoleShowAll) {
    html += '<div class="console-fold">' +
      (consoleShowAll ? '正在显示全部'
                      : '已隐藏 ' + hidden + ' 条系统消息 · <a id="console-toggle">显示</a>') +
      (consoleShowAll ? ' · <a id="console-toggle">只看关键</a>' : '') + '</div>';
  }
  html += visible.map(function(e){
    let cls = 'cline';
    if (e.level === 'error') cls += ' c-err';
    else if (e.level === 'warn') cls += ' c-warn';
    else if (/推送成功/.test(e.text)) cls += ' c-ok';
    const marked = escHtml(e.text).replace(
      /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/, '<span class="c-time">$1</span>')
      .replace(/\n/g, '<br>');   /* 后端按块合并：traceback 等多行块保留换行 */
    return '<div class="' + cls + '">' + marked + '</div>';
  }).join('');
  body.innerHTML = html;
  body.scrollTop = body.scrollHeight;
}

function loadConsole(){
  fetch('/api/logtail?n=200').then(function(r){ return r.json(); })
    .then(function(d){ consoleLoaded = true; renderConsole(d); })
    .catch(function(){
      const body = document.getElementById('console-body');
      if (body) body.innerHTML = '<span class="console-empty">读取日志失败，请点「刷新」。</span>';
    });
}

/* 折叠条：取最新一条「非噪音」行——收起条的价值在一眼看到最近发生的有用的事，
   而最新任意行大概率是守护进程生命周期噪音（实测占比 ~75-80%）。
   读失败静默——底部条不是关键路径。 */
function loadConsoleLast(){
  fetch('/api/logtail?n=60&useful=1').then(function(r){ return r.json(); })
    .then(function(d){
      if (!d || d.ok === false) return;
      const lines = d.lines || [];
      const last = document.getElementById('console-last');
      if (last && lines.length) {
        last.textContent = lines[lines.length - 1].split('\n')[0].replace(/^\S+\s+/, '');
      }
    }).catch(function(){ /* 静默 */ });
}
loadConsoleLast();
setInterval(loadConsoleLast, 30000);

/* 「显示系统消息 / 只看关键」切换：console-body 是静态元素，委托只绑一次 */
document.getElementById('console-body').addEventListener('click', function(e){
  if (e.target && e.target.id === 'console-toggle') {
    consoleShowAll = !consoleShowAll;
    loadConsole();
  }
});

document.getElementById('log-console').addEventListener('toggle', function(){
  if (this.open && !consoleLoaded) loadConsole();
});
document.getElementById('btn-console-refresh').addEventListener('click', function(e){
  // 阻止 summary 的默认展开/收起行为，只刷新
  e.preventDefault();
  e.stopPropagation();
  loadConsole();
});

/* =========================== 启动 =========================== */
/* 扫码选择弹窗：勾选变化时同步「下一步」按钮可用性（至少勾一款） */
const pickList = document.getElementById('pick-list');
if (pickList) pickList.addEventListener('change', updatePickNext);
/* 秒开：先把 localStorage 里的上次数据画出来（KPI/报表/实时面板），
   下面的 switchTab 会发起真实请求做后台静默刷新，数据到手平滑替换 */
renderCachedData();
const hash = (location.hash || '').replace('#', '');
switchTab(PANEL_NAMES.indexOf(hash) >= 0 ? hash : 'history');
refreshNotice();
loadDevice();
loadAccounts();      // 用 config 里的真实巡检开关覆盖页面默认勾选
loadThresholds();
loadSchedule();      // 状态先拉一次，展开时不会闪空
loadAutostart();

/* =========================== 刷新快捷键 ===========================
   pywebview 把 WebView2 的浏览器快捷键关掉了（非 debug 时
   AreBrowserAcceleratorKeysEnabled=False），F5/Ctrl+R 原本什么都不做。
   页面自己接管：阻止默认行为后主动 reload，配合下面的整页闪烁即为刷新反馈。

   绑定点只在文件上方那一个 document keydown（addtime 弹窗那段，先调 rlReloadKey）；
   这里只定义判定与动作，不再单独 addEventListener——document 上多挂一个 keydown
   会让 tests/rl_deep_test.js 的 T4.3「每个(元素,事件)对恰好绑定 1 次」失败。 */

function rlReloadKey(e){
  var k = (e.key || '').toLowerCase();
  if (e.key === 'F5' || (e.ctrlKey && (k === 'r' || k === 'f5'))){
    e.preventDefault();
    location.reload();
    return true;
  }
  return false;
}

/* 刷新反馈：整页白闪已随 2026-11-01 减法裁决移除（转圈按钮承载反馈） */

/* ===================== 库街区绑定（鸣潮，2026-11-09 D2b）=====================
   双模：短信登录（主，前端嵌极验 v4 人肉过验证）+ Token 粘贴（兜底）。
   短信走 /api/kuro/sms/send + /sms/login；token 走 /api/kuro/verify + /save。
   gt4.js 加载失败只影响短信模式（toast 引导切 token），不挡绑定。 */

let kuroCaptcha = null;          // 极验 captchaObj（懒初始化，弹窗内复用）
let kuroCaptchaBroken = false;   // gt4.js 加载失败 → 短信模式置灰引导 token
let kuroSmsTimer = null;
let kuroSmsLeft = 0;

function startKuro(){
  document.getElementById('kuro-stage').hidden = false;
  kuroSwitchMode('sms');
  kuroStatus('');
  kuroTokenStatus('');
}

function closeKuro(){
  document.getElementById('kuro-stage').hidden = true;
}

function kuroSwitchMode(mode){
  const sms = mode !== 'token';
  document.getElementById('kuro-mode-sms').classList.toggle('on', sms);
  document.getElementById('kuro-mode-token').classList.toggle('on', !sms);
  document.getElementById('kuro-sms-pane').hidden = !sms;
  document.getElementById('kuro-token-pane').hidden = sms;
}

function kuroStatus(text, cls){
  const el = document.getElementById('kuro-status');
  el.textContent = text || '';
  el.className = 'kuro-status' + (cls ? ' ' + cls : '');
}

function kuroTokenStatus(text, cls){
  const el = document.getElementById('kuro-token-status');
  el.textContent = text || '';
  el.className = 'kuro-status' + (cls ? ' ' + cls : '');
}

function kuroPhone(){
  return document.getElementById('kuro-phone').value.trim();
}

/* 极验 v4 懒初始化：gt4.js 在 head 里 defer 加载；失败置 broken 引导 token 模式。
   关键参数对齐 Arietta（WebView2 实测可弹的参考实现）：product='bind' +
   showBox() 弹窗、protocol 强制 https——http 页面下 geetest 自动探测出
   http:// 端点，showCaptcha() 全程静默不弹（2026-11-09 真机取证）。 */
function kuroEnsureCaptcha(){
  return new Promise(function(resolve, reject){
    if (kuroCaptcha) { resolve(kuroCaptcha); return; }
    if (typeof window.initGeetest4 !== 'function') {
      kuroCaptchaBroken = true;
      reject(new Error('人机验证组件未加载（需要联网访问极验）'));
      return;
    }
    let settled = false;
    const done = function(fn, arg){ if (!settled) { settled = true; fn(arg); } };
    const fallback = setTimeout(function(){
      done(reject, new Error('人机验证初始化超时（极验接口连不上），可改用「Token 粘贴」模式'));
    }, 10000);
    try {
      window.initGeetest4({
        captchaId: 'ec4aa4174277d822d73f2442a165a2cd',
        product: 'bind',
        language: 'zho',
        protocol: 'https://',
      }, function(captchaObj){
        kuroCaptcha = captchaObj;
        // bind 模式必须先 appendTo 挂载再 showCaptcha，否则弹窗静默不出现
        //（真机取证：缺这步时 onBoxShow 永不触发、display 恒 none）
        try { captchaObj.appendTo(document.getElementById('kuro-captcha')); } catch (e) {}
        captchaObj.onSuccess(function(){
          const v = captchaObj.getValidate();
          if (!v) return;
          kuroDoSendSms(JSON.stringify(v));
          captchaObj.reset();
        });
        captchaObj.onError(function(){
          kuroStatus('人机验证出错，请重试', 'err');
          try { captchaObj.reset(); } catch (e) {}
        });
        captchaObj.onClose(function(){
          kuroStatus('人机验证已关闭，点「发送验证码」重新开始');
        });
        captchaObj.onReady(function(){
          clearTimeout(fallback);
          done(resolve, captchaObj);
        });
      });
    } catch (e) {
      clearTimeout(fallback);
      done(reject, new Error('人机验证初始化异常：' + e.message));
    }
  });
}

/* bind 模式弹窗：showBox 与 showCaptcha 双保险（v4 版本间 API 命名有漂移），
   并给「弹不出来」一个明确的失败反馈，绝不无限卡在「正在唤起」 */
function kuroShowCaptcha(captcha){
  try { if (typeof captcha.showBox === 'function') captcha.showBox(); } catch (e) {}
  try { if (typeof captcha.showCaptcha === 'function') captcha.showCaptcha(); } catch (e) {}
  setTimeout(function(){
    // 可见性看 .geetest_box（真身 340x386）；box_wrap 是定位壳，恒 0 高
    const box = document.querySelector('[class*="geetest_box_wrap"] .geetest_box');
    const visible = box && getComputedStyle(box).display !== 'none'
      && box.getBoundingClientRect().height > 0;
    if (!visible) {
      kuroStatus('验证窗口未能弹出（网络或组件异常），请重试或改用「Token 粘贴」模式', 'err');
    } else {
      kuroStatus('请完成滑块验证，通过后自动发送短信');
    }
  }, 1500);
}

function kuroSendSms(){
  if (!/^1[3-9]\d{9}$/.test(kuroPhone())) {
    kuroStatus('请先填写正确的手机号（11 位国内号段）', 'err');
    return;
  }
  if (kuroSmsLeft > 0) return;
  const btn = document.getElementById('btn-kuro-send');
  btn.disabled = true;
  kuroStatus('正在唤起人机验证…');
  kuroEnsureCaptcha().then(function(captcha){
    btn.disabled = false;
    kuroShowCaptcha(captcha);       // 用户手动过验证，成功回调里真正发码
  }).catch(function(err){
    btn.disabled = false;
    kuroStatus(err.message + '。可改用「Token 粘贴」模式绑定', 'err');
  });
}

function kuroDoSendSms(geeTestData){
  const btn = document.getElementById('btn-kuro-send');
  kuroStatus('正在发送验证码…');
  postJSON('/api/kuro/sms/send', { mobile: kuroPhone(), gee_test_data: geeTestData })
    .then(function(d){
      if (!d || !d.ok) { kuroStatus((d && d.error) || '发送失败', 'err'); return; }
      kuroStatus(d.message || '验证码已发送', 'ok');
      kuroSmsLeft = 60;
      btn.disabled = true;
      if (kuroSmsTimer) clearInterval(kuroSmsTimer);
      kuroSmsTimer = setInterval(function(){
        kuroSmsLeft -= 1;
        if (kuroSmsLeft <= 0) { clearInterval(kuroSmsTimer); kuroSmsTimer = null; btn.textContent = '发送验证码'; btn.disabled = false; return; }
        btn.textContent = kuroSmsLeft + 's';
      }, 1000);
      btn.textContent = '60s';
      document.getElementById('kuro-code').focus();
    })
    .catch(function(){ kuroStatus('无法连接本地服务', 'err'); });
}

function kuroLogin(){
  const code = document.getElementById('kuro-code').value.trim();
  if (!/^1[3-9]\d{9}$/.test(kuroPhone())) { kuroStatus('手机号格式不对', 'err'); return; }
  if (!/^\d{6}$/.test(code)) { kuroStatus('请输入 6 位短信验证码', 'err'); return; }
  const btn = document.getElementById('btn-kuro-login');
  btn.disabled = true;
  kuroStatus('正在登录…');
  postJSON('/api/kuro/sms/login', { mobile: kuroPhone(), code: code })
    .then(function(d){
      btn.disabled = false;
      if (!d || !d.ok) { kuroStatus((d && d.error) || '登录失败', 'err'); return; }
      toast(d.message || '绑定成功');
      closeKuro();
      loadAccounts();
    })
    .catch(function(){ btn.disabled = false; kuroStatus('无法连接本地服务', 'err'); });
}

function kuroSaveToken(){
  const token = document.getElementById('kuro-token').value.trim();
  if (!token) { kuroTokenStatus('请先粘贴 Token', 'err'); return; }
  const btn = document.getElementById('btn-kuro-save');
  btn.disabled = true;
  kuroTokenStatus('正在校验…');
  postJSON('/api/kuro/save', { token: token })
    .then(function(d){
      btn.disabled = false;
      if (!d || !d.ok) { kuroTokenStatus((d && d.error) || '校验未通过', 'err'); return; }
      toast(d.message || '绑定成功');
      closeKuro();
      loadAccounts();
    })
    .catch(function(){ btn.disabled = false; kuroTokenStatus('无法连接本地服务', 'err'); });
}
