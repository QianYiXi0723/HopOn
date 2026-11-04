
/* =========================== Tab 切换 =========================== */
const PANEL_NAMES = ['history', 'query', 'account', 'notify', 'schedule'];
let schedTimer = null;

function switchTab(name){
  PANEL_NAMES.forEach(function(k){
    const panel = document.getElementById('tab-' + k);
    if (panel) {
      panel.classList.toggle('on', k === name);
    }
    const btn = document.querySelector('.nav button[data-tab="' + k + '"]');
    if (btn) btn.classList.toggle('on', k === name);
  });

  if (name === 'query') loadQuery();
  if (name === 'notify') { loadPush(); loadPushPreview(); loadPushRecent(); }
  if (name === 'schedule') { loadSchedule(); startSchedPoll(); loadThresholds(); loadAutostart(); loadRunLog(); } else { stopSchedPoll(); }
  if (name === 'account') { loadDevice(); loadAccounts(); refreshNotice(); }
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
  const link = e.target.closest('[data-goto]');
  if (!link) return;
  e.preventDefault();
  switchTab(link.dataset.goto);
});

/* =========================== 通用提示 =========================== */
function toast(text, isErr){
  const el = document.getElementById('toast');
  el.textContent = text;
  el.className = 'show' + (isErr ? ' err' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(function(){ el.className = ''; }, 2800);
}

async function postJSON(url, payload){
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {})
  });
  return r.json();
}

function escHtml(v){
  return String(v == null ? '' : v).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

/* =========================== 账号绑定 =========================== */
let sid = null, timer = null;

function toggleAll(group, checked){
  document.querySelectorAll('input[name="' + group + '"]').forEach(function(i){
    i.checked = checked;
  });
  refreshBadges(group);
  updateLoginButtons();
  pushEnabled(group,
    [...document.querySelectorAll('input[name="' + group + '"]')].map(function(i){ return i.value; }),
    checked);
}

function selected(group){
  return [...document.querySelectorAll('input[name="' + group + '"]:checked')]
           .map(function(i){ return i.value; });
}

/* 勾选框 = 「参与每日巡检并提醒」的开关，直接写 config.accounts[].enabled */
function refreshBadges(group){
  document.querySelectorAll('input[name="' + group + '"]').forEach(function(i){
    const rem = document.querySelector('[data-rem="' + group + ':' + i.value + '"]');
    if (rem) rem.classList.toggle('on', i.checked);
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

/* 按钮文案随勾选动态变化：1 款=更新该游戏凭证，多款=更新全部/所选凭证，未勾=禁用 */
function updateLoginButtons(){
  ['m', 's'].forEach(function(g){
    const boxes = [...document.querySelectorAll('input[name="' + g + '"]')];
    const btn = document.getElementById('btn-' + g);
    if (!btn) return;
    const on = boxes.filter(function(b){ return b.checked; });
    const allBox = document.getElementById(g + '-all');
    if (allBox) {
      allBox.checked = on.length === boxes.length;
      allBox.indeterminate = on.length > 0 && on.length < boxes.length;
    }
    if (!on.length) {
      btn.disabled = true;
      btn.textContent = '请先勾选需要登录的游戏';
      return;
    }
    btn.disabled = false;
    if (on.length === 1) {
      btn.textContent = '更新该游戏凭证';
    } else {
      btn.textContent = (on.length === boxes.length) ? '更新全部凭证' : '更新所选凭证';
    }
  });
}

/* 从后端拉取真实的巡检开关与凭证状态，覆盖页面默认值 */
function loadAccounts(){
  fetch('/api/accounts').then(function(r){ return r.json(); }).then(function(d){
    if (!d || !d.ok) return;
    (d.items || []).forEach(function(it){
      const box = document.querySelector(
        'input[name="m"][value="' + it.id + '"],input[name="s"][value="' + it.id + '"]');
      if (!box) return;
      box.checked = !!it.enabled;
      const group = box.name;
      const rem = document.querySelector('[data-rem="' + group + ':' + it.id + '"]');
      if (rem) rem.classList.toggle('on', !!it.enabled);
      const nc = document.querySelector('[data-nocred="' + group + ':' + it.id + '"]');
      if (nc) nc.classList.toggle('on', !it.has_credential);
    });
    renderCredHealth(d);
    renderKpiCred(d);
    updateLoginButtons();
  }).catch(function(){});
}

/* 凭证健康状态：巡检开关 / 凭证绑定 / 最近一次扫码更新时间
   色彩纪律：正常 = 灰字，异常（未绑定）= 橙色徽章 */
function shortTime(text){
  // "2026-10-27 22:00:33" -> "10-27 22:00"
  const m = String(text || '').match(/^\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return m ? (m[1] + ' ' + m[2]) : String(text || '');
}

function renderCredHealth(d){
  const box = document.getElementById('cred-health');
  const badge = document.getElementById('cred-health-badge');
  if (!box) return;
  const items = (d && d.items) || [];
  if (!items.length) {
    box.innerHTML = '<div class="emptybox">config.json 里没有账号节点。</div>';
    if (badge) badge.textContent = '-';
    return;
  }
  const bound = items.filter(function(i){ return i.has_credential; }).length;
  if (badge) {
    badge.textContent = bound + '/' + items.length + ' 已绑定';
    badge.className = 'badge' + (bound === items.length ? '' : ' warn');
  }
  box.innerHTML = '<table class="tbl"><thead><tr>' +
    '<th>游戏</th><th class="num">巡检</th><th class="num">凭证</th><th>更新时间</th>' +
    '</tr></thead><tbody>' +
    items.map(function(it){
      return '<tr>' +
        '<td class="wrap"><span class="gdot" style="background:' + gameDotColor(it.id) +
          '"></span>' + escHtml(it.name || it.id) +
          (it.nickname ? ' <span class="dim">' + escHtml(it.nickname) + '</span>' : '') + '</td>' +
        '<td class="num dim">' + (it.enabled ? '开启' : '关闭') + '</td>' +
        '<td class="num">' + (it.has_credential
          ? '<span class="dim">已绑定</span>'
          : '<span class="badge warn">未绑定</span>') + '</td>' +
        '<td class="mono wrap">' +
          escHtml(it.credential_updated_at ? shortTime(it.credential_updated_at) : '早期绑定') + '</td>' +
        '</tr>';
    }).join('') + '</tbody></table>';
}

document.querySelectorAll('input[name="m"], input[name="s"]').forEach(function(box){
  box.addEventListener('change', function(){
    refreshBadges(box.name);
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
  document.getElementById('btn-m').disabled = busy;
  document.getElementById('btn-s').disabled = busy;
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
    box.innerHTML = '<span class="ph"><span class="spin"></span>请稍候…</span>';
  }

  const kv = document.getElementById('detail');
  const items = [];
  if (d.detail) {
    if (d.detail.ltuid) items.push('米游社 ID：<code>' + d.detail.ltuid + '</code>');
    if (d.detail.games) items.push('已更新：' + d.detail.games.join('、'));
    if (d.detail.note) items.push(d.detail.note);
  }
  if (d.results && d.results.length && (d.results.length > 1 || d.state === 'error')) {
    const rows = d.results.map(function(r){
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
  const url = '/api/start?provider=' + encodeURIComponent(provider)
            + '&targets=' + encodeURIComponent(targets.join(','));
  try {
    const r = await fetch(url);
    const d = await r.json();
    if (!d.ok) { showError(d.message); setBusy(false); return; }
    sid = d.sid;
    poll();
  } catch (e) {
    showError('无法连接本地服务：' + e);
    setBusy(false);
  }
}

function startMihoyo(){
  const list = selected('m');
  if (!list.length) { toast('请先勾选需要登录的游戏', true); return; }
  start('mihoyo', list);
}

function startSkland(){
  const list = selected('s');
  if (!list.length) { toast('请先勾选需要登录的游戏', true); return; }
  start('skland', list);
}

function showError(msg){
  const stage = document.getElementById('stage');
  stage.hidden = false;
  const badge = document.getElementById('badge');
  badge.textContent = '失败'; badge.className = 'badge err';
  document.getElementById('msg').textContent = msg;
  document.getElementById('qrbox').innerHTML = '<span class="ph">未能创建二维码</span>';
}

function poll(){
  fetch('/api/state?sid=' + encodeURIComponent(sid))
    .then(function(r){ return r.json(); })
    .then(function(d){
      render(d);
      if (d.state === 'done' || d.state === 'error') { setBusy(false); loadAccounts(); return; }
      timer = setTimeout(poll, 1000);
    })
    .catch(function(){ timer = setTimeout(poll, 2000); });
}

function reset(){
  if (timer) { clearTimeout(timer); timer = null; }
  sid = null;
  setBusy(false);
  document.getElementById('stage').hidden = true;
  document.getElementById('qrbox').innerHTML = '<span class="ph">二维码准备中…</span>';
  document.getElementById('detail').hidden = true;
}

function refreshNotice(){
  fetch('/api/env').then(function(r){ return r.json(); }).then(function(d){
    const box = document.getElementById('notice');
    if (!box) return;
    if (d.configured) { box.style.display = 'none'; return; }
    box.style.display = 'block';
    box.innerHTML =
      '<b>尚未配置推送通道</b>，手机收不到提醒。去 <a data-goto="notify">「通知绑定」</a> 填写。';
  }).catch(function(){});
}

/* ---------- 设备伪装 ---------- */
function renderDevice(d){
  if (!d.ok) { toast(d.message || '读取设备档案失败', true); return; }
  const p = d.profile || {};
  document.getElementById('dev-badge').textContent = d.custom ? '自定义档案' : '默认档案';
  document.getElementById('dev-badge').className = 'badge ' + (d.custom ? 'ok' : '');
  document.getElementById('dev-current').innerHTML =
    '当前设备：<b>' + (p.brand || '?') + ' ' + (p.model || '?') + '</b>' +
    ' · Android ' + (p.os_version || '?') +
    ' · OAID <code>' + (p.oaid || '?') + '</code>' +
    ' · 指纹缓存' + (d.cache_exists ? '存在' : '<b>已清空</b>');

  const apk = d.apk || {};
  const qrBox = document.getElementById('apk-qr');
  const meta = document.getElementById('apk-meta');
  if (apk.available && apk.qr) {
    qrBox.innerHTML = '<img alt="APK 下载二维码">';
    qrBox.querySelector('img').src = 'data:image/png;base64,' + apk.qr;
    let shown = apk.url;
    try { shown = decodeURIComponent(apk.url); } catch (e) {}
    meta.innerHTML = apk.name + '（' + apk.size_text + '）<br>' + shown +
      (apk.warning ? '<br><span style="color:var(--warn)">⚠ ' + apk.warning + '</span>' : '');
  } else {
    qrBox.innerHTML =
      '<div style="padding:10px 8px;text-align:center">' +
      '<div style="font-size:11px;color:var(--muted);line-height:1.5">二维码不可用</div>' +
      '<button class="btn sm" id="btn-apk-retry" style="margin-top:6px">换个端口重试</button>' +
      '</div>';
    meta.innerHTML = '<span style="color:var(--warn)">' + (apk.message || '未找到 APK') + '</span>';
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
      meta.innerHTML = d.name + '（' + d.size_text + '）<br>' + shown;
      toast('下载服务已重启，端口 ' + d.port);
    } else {
      if (btn) { btn.disabled = false; btn.textContent = '换个端口重试'; }
      meta.innerHTML = '<span style="color:var(--warn)">' + (d.message || '仍然不可用') + '</span>';
      toast('仍然不可用：' + (d.message || ''), true);
    }
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '换个端口重试'; }
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
  ['genshin','starrail','zzz','arknights','endfield'].forEach(function(gid){
    const box = document.getElementById('resin-' + gid);
    if (!box) return;
    box.value = (values[gid] != null) ? values[gid] : '';
    box.placeholder = (defs[gid] != null) ? String(defs[gid]) : '0';
  });

  const enabled = d.enabled !== false;
  const box = document.getElementById('resin-toggle');
  if (box) box.checked = enabled;

  const state = document.getElementById('resin-state');
  if (state) {
    const off = Object.keys(values).filter(function(k){ return values[k] === 0; });
    state.textContent = !enabled ? '已关闭'
      : (off.length ? off.length + ' 项关闭' : '已启用');
  }
  const item = document.getElementById('acc-resource');
  if (item) item.classList.toggle('is-off', !enabled);

  const hint = document.getElementById('resin-hint');
  if (hint) hint.textContent = '留空用默认值 · 0 = 关闭该游戏';
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
  if (!confirm('恢复内置默认设备档案？恢复后需要重新扫码。')) return;
  try {
    const d = await postJSON('/api/device/reset', {});
    if (!d.ok) { toast(d.message || '恢复失败', true); return; }
    renderDevice(d);
    toast(d.message || '已恢复默认');
  } catch (e) {
    toast('恢复失败：' + e, true);
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
  const an = document.getElementById('always-notify');
  if (an) an.checked = !!d.always_notify;

  // 左栏渠道列表：点 = 配置健康度（异常=橙），文字 = 状态说明
  CHANNELS.forEach(function(name){
    const it = (d.items || {})[name] || {};
    const stateEl = document.getElementById('ch-state-' + name);
    const dotEl = document.getElementById('ch-dot-' + name);
    const healthy = !!(it.has_key && it.enabled && it.in_channels);
    if (stateEl) stateEl.textContent = hintText(it);
    if (dotEl) dotEl.className = 'ch-dot' + (healthy ? '' : ' bad');
  });
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
  serverchan: '💬 Server酱（微信）',
  qmsg: '🐧 Qmsg 酱（QQ）'
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
  // 重建标题 + (?) 提示（textContent 会把图标一起清掉）
  document.getElementById('ed-title').innerHTML =
    escHtml(CHANNEL_NAMES[name]) +
    ' <span class="q" data-tip="Key 由后端打码显示：不修改输入框 = 保持原值；清空后保存 = 删除该 Key"></span>';
}

document.querySelectorAll('.ch-item').forEach(function(el){
  el.addEventListener('click', function(){ selectChannel(this.dataset.ch); });
});

/* 最近推送记录：从运行日志里筛推送相关行（真实记录，最多 8 条） */
function loadPushRecent(){
  const box = document.getElementById('pv-recent');
  if (!box) return;
  fetch('/api/logtail?n=300').then(function(r){ return r.json(); })
    .then(function(d){
      if (!d || d.ok === false || !(d.lines || []).length) {
        box.innerHTML = '<div>还没有推送记录。</div>';
        return;
      }
      const rows = d.lines.filter(function(l){ return /推送|should_push|推送决策/.test(l); })
        .slice(-8).reverse();
      if (!rows.length) {
        box.innerHTML = '<div>最近没有推送记录。</div>';
        return;
      }
      box.innerHTML = rows.map(function(l){
        return '<div>' + escHtml(l.replace(/^\d{4}-\d{2}-\d{2} /, '')) + '</div>';
      }).join('');
    })
    .catch(function(){ box.innerHTML = '<div>读取日志失败，请稍后重试。</div>'; });
}

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
             (x.ok ? '✓' : '✗') + '</span>' + x.label + ' — ' + x.message + '</div>';
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
  box.addEventListener('focus', function(){
    if (box.value && box.value === box.dataset.masked) box.select();
  });
});

document.getElementById('btn-save').addEventListener('click', savePush);
document.getElementById('btn-test').addEventListener('click', testPush);

/* =========================== 定时与状态 =========================== */
function renderDaemon(st){
  const dot = document.getElementById('d-dot');
  const badge = document.getElementById('d-state');
  const running = !!st.running;
  /* 色彩纪律：运行中 = 灰点灰字，停止才是红色 */
  dot.style.background = running ? 'var(--muted)' : 'var(--err)';
  badge.textContent = running ? '运行中' : '已停止';
  badge.className = 'badge' + (running ? '' : ' err');

  document.getElementById('d-pid').textContent = st.pid || '-';
  document.getElementById('d-since').textContent = st.started_at || '-';
  const heart = document.getElementById('d-heart');
  if (heart) {
    heart.textContent = (st.heartbeat_age != null)
      ? Math.round(st.heartbeat_age) + ' 秒前' : '-';
  }

  const last = st.last_run || {};
  document.getElementById('d-last').textContent =
    last.at ? (last.exit === 0 ? '成功' : '退出码 ' + last.exit) + ' (' + shortStamp(last.at) + ')'
            : '还没跑过';
  document.getElementById('btn-daemon-start').disabled = running;
  document.getElementById('btn-daemon-stop').disabled = !running;
}

function shortStamp(text){
  // "2026-10-27 01:23:43" -> "10-27 01:23"
  const m = String(text || '').match(/^\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return m ? (m[1] + ' ' + m[2]) : String(text || '');
}

function renderSchedule(d){
  if (!d.ok) { toast(d.message || '读取失败', true); return; }
  const times = d.times || [];
  const box = document.getElementById('d-times');
  if (!times.length) {
    box.innerHTML = '<span class="chip empty">还没有时间点</span>';
  } else {
    box.innerHTML = times.map(function(t){
      return '<span class="chip">' + t +
             '<button title="删除" data-remove="' + t + '">×</button></span>';
    }).join('');
  }
  renderRuler(times);
  document.getElementById('d-count').textContent = times.length + ' 个';
  document.getElementById('d-next').textContent = d.next_run || '-';
  renderKpiNext(d);

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
  const note = document.getElementById('act-state');
  if (note) note.textContent = on ? '已开启' : '已关闭';
  const item = document.getElementById('acc-activity');
  if (item) item.classList.toggle('is-off', !on);
  const num = parseInt(days, 10);
  const input = document.getElementById('activity-days');
  if (input && document.activeElement !== input && !isNaN(num)) input.value = num;
}

async function toggleActivityAlert(){
  const box = document.getElementById('activity-toggle');
  if (!box) return;
  const want = box.checked;
  box.disabled = true;
  try {
    const d = await postJSON('/api/activity_alert', { enabled: want });
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

  const badge = document.getElementById('cu-state');
  if (badge) {
    if (!enabled) badge.textContent = '已关闭';
    else if (minutes > 0) badge.textContent = minutes + ' 分钟内补发';
    else badge.textContent = '窗口为 0';
  }
  const item = document.getElementById('acc-catchup');
  if (item) item.classList.toggle('is-off', !enabled);
}

async function toggleCatchUp(){
  const box = document.getElementById('catch-up-toggle');
  if (!box) return;
  const want = box.checked;
  box.disabled = true;
  try {
    const d = await postJSON('/api/catch_up',
      { minutes: lastCatchUp.minutes, enabled: want });
    if (!d.ok) {
      box.checked = !want;
      toast(d.message || '设置失败', true);
      return;
    }
    renderCatchUp(d);
    toast(want ? '已开启睡过头补发' : '已关闭睡过头补发（窗口数值已保留）');
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
  let minutes = isNaN(raw) ? 0 : raw;
  if (minutes < 0) minutes = 0;
  if (minutes > 720) minutes = 720;
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
    .catch(function(){});
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
    if (!supported) {
      badge.textContent = '不支持';
      badge.className = 'badge err';
    } else if (d.stale) {
      badge.textContent = '路径已失效';
      badge.className = 'badge err';
    } else if (d.enabled) {
      badge.textContent = '已开启';
      badge.className = 'badge';
    } else {
      badge.textContent = '未开启';
      badge.className = 'badge';
    }
  }
  if (hint) {
    if (!supported) {
      hint.textContent = (d && d.message) || '仅支持 Windows';
    } else if (d.stale) {
      hint.innerHTML = '注册表路径已失效：把开关关掉再打开一次即可刷新。';
    } else if (d.enabled) {
      hint.innerHTML = '下次登录 Windows 自动在托盘启动。';
    } else {
      hint.innerHTML = '勾选后下次登录 Windows 自动在托盘启动。';
    }
  }
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
document.getElementById('btn-live-refresh')
  .addEventListener('click', function(){ loadLive(true); });
document.getElementById('btn-act-refresh')
  .addEventListener('click', refreshActivities);
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
    renderSchedule(d);
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
  return [...document.querySelectorAll('#d-times .chip')]
    .map(function(el){ return el.textContent.replace('×','').trim(); })
    .filter(function(t){ return /^\d{1,2}:\d{2}$/.test(t); });
}

document.getElementById('d-times').addEventListener('click', async function(e){
  const btn = e.target.closest('[data-remove]');
  if (!btn) return;
  const target = btn.dataset.remove;
  const current = currentTimes();
  if (current.length <= 1) { toast('至少要保留一个时间点', true); return; }
  await saveTimes(current.filter(function(t){ return t !== target; }));
});

document.getElementById('btn-add-time').addEventListener('click', addTime);
document.getElementById('btn-catch-up').addEventListener('click', saveCatchUp);

async function controlDaemon(action){
  const btn = document.getElementById(action === 'start' ? 'btn-daemon-start' : 'btn-daemon-stop');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = action === 'start' ? '启动中…' : '停止中…';
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
document.getElementById('btn-dev-reset').addEventListener('click', resetDevice);


/* ========================= 信息查询 ========================= */
let liveLoaded = false;

function loadQuery(){
  loadActivities();
  if (!liveLoaded) loadLive(false);
}

/* 把 /api/live 结果整理成 gid → {finished, tasks, 体力信息} 的映射。
   只有查询成功（g.ok）的游戏才进入映射 —— 它的完成状态有"发言权"，
   KPI 与迷你卡以它为准；查询失败的没有记录，回落到巡检历史。 */
function buildLiveMap(d){
  const map = {};
  (d.games || []).forEach(function(g){
    if (!g.ok) return;
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

function renderLive(d){
  const box = document.getElementById('live-grid');
  const hint = document.getElementById('live-hint');
  if (!box) return;
  if (!d || d.ok === false) {
    box.innerHTML = '<div class="emptybox">' + escHtml((d && d.message) || '查询失败，请重试') + '</div>';
    if (hint) hint.textContent = '';
    return;
  }
  window.__liveMap = Object.assign(window.__liveMap || {}, buildLiveMap(d));
  const games = d.games || [];
  if (!games.length) {
    box.innerHTML = '<div class="emptybox">没有启用的账号。</div>';
  } else {
    box.innerHTML = '<table class="tbl"><thead><tr>' +
      '<th>游戏</th><th>日常</th><th>体力</th><th>进度</th></tr></thead><tbody>' +
      games.map(function(g){
        const name = escHtml(g.game_name || g.game);
        if (!g.ok) {
          return '<tr><td>' + name + '</td>' +
            '<td><span class="badge err">失败</span></td>' +
            '<td class="wrap err-t">' + escHtml(g.error || '未知错误') + '</td>' +
            '<td>—</td></tr>';
        }
        const r = g.resource;
        const st = g.finished
          ? '<span class="dim">已完成</span>'
          : '<span class="badge warn">未完成</span>';
        if (!r || r.current == null) {
          return '<tr><td>' + name + '</td><td>' + st + '</td>' +
            '<td class="dim">—</td><td class="dim">暂不支持</td></tr>';
        }
        const pct = (r.percent == null) ? 0 : r.percent;
        const max = (r.maximum == null) ? '?' : r.maximum;
        return '<tr><td>' + name + '</td><td>' + st + '</td>' +
          '<td class="mono">' + r.current + ' <span class="dim">/ ' + max +
          ' · ' + escHtml(r.name) + '</span></td>' +
          '<td><span class="bar"><i class="' + (pct >= 85 ? 'hot' : '') +
          '" style="width:' + pct + '%"></i><i class="progress-glow' +
          (pct >= 85 ? ' hot' : '') + '" style="left:' + pct + '%"></i></span><span class="dim">' + pct + '%</span></td></tr>';
      }).join('') + '</tbody></table>';
  }
  if (hint) {
    hint.textContent = '查询时间 ' + (d.generated_at || '-') + (d.cached ? '（45 秒内缓存）' : '');
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

function loadLive(force){
  const btn = document.getElementById('btn-live-refresh');
  const box = document.getElementById('live-grid');
  if (btn) { btn.disabled = true; btn.textContent = '查询中…'; }
  if (box) box.innerHTML = '<div class="emptybox">正在查询，请稍候…</div>';
  fetch('/api/live' + (force ? '?refresh=1' : ''))
    .then(function(r){ return r.json(); })
    .then(function(d){ liveLoaded = true; renderLive(d); })
    .catch(function(){
      if (box) box.innerHTML = '<div class="emptybox">查询失败，请稍后重试。</div>';
    })
    .then(function(){
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
  arknights: '/static/icons/arknights'
};

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

function actCardHtml(it, isHero){
  const id = escHtml(it.id || '');
  const st = shortRange(it.start), en = shortRange(it.end);
  const ended = it.status === '已完成';
  const timeCls = ended ? 't-end' : (it.not_started ? 't-up' : 't-on');
  const soon = !!it.ending_soon && !it.not_started;
  // reward 为空 = 接口没给奖励明细，被兜底规则保留下来的（别假装它有原石）
  const reward = it.reward
    ? '<span class="act-reward" title="这个活动给抽卡资源">' + escHtml(it.reward) + '</span>'
    : '';
  const cdCls = ended ? 'ended' : (soon ? 'soon' : '');
  const prog = actProgHtml(it);
  return '<div class="card acard' + (isHero ? ' hero' : '') + '" data-id="' + id + '">' +
    '<div class="acard-top"><span class="act-game">' + actIconHtml(it) +
      escHtml(it.game_name) + '</span>' +
      '<button type="button" class="act-del" data-del="' + id +
      '" title="删除，之后不再提醒">×</button></div>' +
    '<div class="acard-title" title="' + escHtml(it.title) + '">' + escHtml(it.title) + reward + '</div>' +
    '<div class="act-time ' + timeCls + '">始 ' + (st || '时间待定') +
      (en ? ' · 止 ' + en : '') + '</div>' +
    '<div class="act-cd ' + cdCls + '">' + escHtml(it.countdown || '') + '</div>' +
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

/* 最紧急的 1 张做 2x2 英雄卡：进行中按剩余小时取最小（>0），没有进行中就取未开启的第一张 */
function pickHeroIndex(pool){
  if (!pool.length) return -1;
  let best = 0;
  for (let i = 1; i < pool.length; i++) {
    const a = +pool[i].hours_left || 0, b = +pool[best].hours_left || 0;
    if (a > 0 && (b <= 0 || a < b)) best = i;
  }
  return best;
}

function renderActivities(d){
  const list = document.getElementById('act-list');
  const uns = document.getElementById('act-unsupported');
  if (!list) return;
  d = d || {};
  const items = d.items || [];
  if (!items.length) {
    list.innerHTML = '<div class="act-empty">' +
      '<div class="act-empty-icon">' + (d.has_data ? '🌱' : '📭') + '</div>' +
      '<div>' + (d.has_data
        ? '近期无即将结束的活动，安心长草'
        : '还没有活动数据，点「刷新」拉取。') + '</div>' +
      '</div>';
  } else {
    // 后端已排好序；这里拆成 进行中 / 未开启 / 已完成（进抽屉）三段
    const ongoing = items.filter(function(x){ return x.group !== 'upcoming' && x.status !== '已完成'; });
    const upcoming = items.filter(function(x){ return x.group === 'upcoming'; });
    const finished = items.filter(function(x){ return x.status === '已完成'; });

    let html = '<div class="act-grid">';
    if (ongoing.length) {
      const hero = pickHeroIndex(ongoing);
      html += ongoing.map(function(it, i){ return actCardHtml(it, i === hero); }).join('');
    }
    if (upcoming.length) {
      html += upcoming.map(function(it){ return actCardHtml(it, !ongoing.length && it === upcoming[0]); }).join('');
    }
    html += '</div>';

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
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '刷新中…';
  try {
    const d = await postJSON('/api/activities/refresh', {});
    if (d.ok === false) { toast(d.message || '刷新失败', true); return; }
    renderActivities(d);
    toast('活动数据已更新（' + (d.items || []).length + ' 个进行中）');
  } catch (e) {
    toast('刷新失败：' + e, true);
  } finally {
    btn.disabled = false; btn.textContent = old;
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
    (detail ? '<br><span style="font-size:11px;color:var(--muted)">' + detail + '</span>' : '') +
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
  endfield:  '#dcc23c'
};
function gameDotColor(gid){
  return GAME_DOT[gid] || 'var(--brand)';
}

let lastHistory = null;

function renderHistory(d){
  d = d || {};
  lastHistory = d;
  const monthBadge = document.getElementById('h-month');
  if (monthBadge) monthBadge.textContent = d.month ? (d.month + ' 本月') : '-';

  if (!d.has_data) {
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

function gameIconHtml(gid){
  const base = GAME_ICON[gid];
  if (!base) {
    return '<span class="gcard-ico ph">' + escHtml(String(gid).slice(0, 1).toUpperCase()) + '</span>';
  }
  const v = ICON_STAMP.indexOf('__') === 0 ? '' : '?v=' + ICON_STAMP;
  return '<img class="gcard-ico" width="40" height="40" alt="" src="' + base + '.png' + v +
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

  // 今日状态：live 查询成功以 live 为准（与 KPI/表格同一套判定），
  // 没有实时数据时回落巡检历史。正常 = 灰字，异常 = 橙色徽章（色彩纪律）
  let todayHtml;
  if (live && live.finished != null) {
    todayHtml = live.finished ? '<span class="dim">✔ 今日已完成</span>'
                              : '<span class="badge warn">未完成</span>';
  } else if (!today || !today.known) {
    todayHtml = '<span class="dim">今日无数据</span>';
  } else if (today.finished) {
    todayHtml = '<span class="dim">✔ 今日已完成</span>';
  } else {
    todayHtml = '<span class="badge warn">未完成 ' + (today.unfinished_tasks || 0) + ' 项</span>';
  }

  // 体力：来自「信息查询」最近一次结果（未查过则提示）
  let stamina;
  if (live && live.percent != null) {
    const hot = live.percent >= 85;
    stamina = '<div class="gcard-stam"><span class="dim">' + escHtml(live.name || '体力') +
      '</span><span class="sbar"><i class="' + (hot ? 'hot' : '') +
      '" style="width:' + live.percent + '%"></i><i class="progress-glow' +
      (hot ? ' hot' : '') + '" style="left:' + live.percent + '%"></i></span>' +
      '<span class="mono">' + live.current + '/' + live.maximum + '</span></div>';
  } else {
    stamina = '<div class="gcard-stam"><span class="dim">体力</span>' +
      '<span class="dim">到「信息查询」查询后显示</span></div>';
  }

  const missed = Number(g.month_missed || 0);
  return '<div class="card gcard">' +
    '<div class="gcard-head">' + gameIconHtml(gid) +
      '<div class="gcard-name"><b>' + escHtml(gameBaseName(g)) + '</b>' +
        '<small>' + (today && today.last ? escHtml(shortStamp(today.last)) : '—') + '</small></div>' +
      '<div class="gcard-streak"><b class="value">' + Number(g.streak || 0) + '</b><small>连续天数</small></div>' +
    '</div>' +
    '<div class="gcard-today">' + todayHtml + '</div>' +
    /* 进度透明化：直接展示每条判定任务的进度（live 优先） */
    taskProgressHtml(live && live.tasks) +
    stamina +
    '<div class="gcard-foot">本月完成 <b>' + Number(g.month_done || 0) + '</b> 天' +
      (missed > 0 ? ' · <span class="err-t">漏 ' + missed + '</span>' : '') +
    '</div></div>';
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
      let cls = 'na', mark = '';
      if (c && c.known) {
        cls = c.finished ? 'ok' : 'err';
        mark = c.finished ? '✓' : '✗';
      } else if (row.is_today) {
        /* 今天的定时巡检还没到：画"等待中"灰点，绝不提前画红叉 */
        cls = 'na wait';
        mark = '⋯';
      }
      const tip = (c && c.known)
        ? (row.label + ' · ' + (c.finished ? '已完成'
            : '未完成 ' + (c.unfinished_tasks || 0) + ' 项'))
        : (row.is_today ? (row.label + ' · 等待今日巡检')
                        : (row.label + ' · 无数据'));
      html += '<div class="hm-cell ' + cls + (row.is_today ? ' today' : '') +
        '" title="' + escHtml(tip) + '">' + mark + '</div>';
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
  fetch('/api/history?days=7')
    .then(function(r){
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function(d){
      try {
        renderHistory(d);
        renderKpiToday(d);
      } catch (e) {
        console.error('渲染历史报表失败', e);
        renderHistoryEmpty('报表渲染出错，请点「刷新」重试。');
      }
    })
    .catch(function(err){
      console.error('读取历史记录失败', err);
      renderHistoryEmpty('暂无历史记录，等待下一次定时检查写入。',
                         err && err.message ? '（' + err.message + '）' : '');
    });
}

document.getElementById('btn-history-refresh').addEventListener('click', loadHistory);

/* ========================= 主题切换 ========================= */
/* auto = 跟随系统，light / dark = 手动固定；选择存在 localStorage，重启后保留。
   CSS 变量只认 html[data-theme="dark"|"light"] 两组 Token；
   auto 在这里解析成系统实际值后同样落到 data-theme 上，杜绝两边逻辑分叉。 */
const THEME_ORDER = ['auto', 'light', 'dark'];
const THEME_NAMES = { auto: '跟随系统', light: '浅色', dark: '深色' };
let themeCur = 'auto';
try { themeCur = localStorage.getItem('rl-theme') || 'auto'; } catch (e) {}
if (THEME_ORDER.indexOf(themeCur) < 0) themeCur = 'auto';

const systemDark = window.matchMedia('(prefers-color-scheme: dark)');

function applyTheme(){
  const mode = themeCur === 'auto' ? (systemDark.matches ? 'dark' : 'light') : themeCur;
  document.documentElement.dataset.theme = mode;
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = '主题：' + THEME_NAMES[themeCur];
}

applyTheme();
document.getElementById('theme-btn').addEventListener('click', function(){
  themeCur = THEME_ORDER[(THEME_ORDER.indexOf(themeCur) + 1) % THEME_ORDER.length];
  try { localStorage.setItem('rl-theme', themeCur); } catch (e) {}
  applyTheme();
});
/* 跟随系统时，系统深浅变化实时切换 */
if (systemDark.addEventListener) {
  systemDark.addEventListener('change', function(){ if (themeCur === 'auto') applyTheme(); });
}

/* ========================= 推送预览 ========================= */
function renderPushPreview(h){
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

  const days = (h && h.days) || [];
  let row = null;
  for (let i = days.length - 1; i >= 0; i--) {
    if (days[i].is_today) { row = days[i]; break; }
  }
  if (!row && days.length) row = days[days.length - 1];

  if (!row) {
    titleEl.textContent = '游戏日常：等待第一次巡检';
    bodyEl.textContent = '还没有巡检数据。定时检查跑过一轮后，这里会按真实结果显示推送样式。';
    return;
  }

  const cells = row.games || {};
  const nameOf = {};
  ((h && h.games) || []).forEach(function(g){ nameOf[g.game] = g.game_name || g.game; });
  const tracked = (h && h.tracked) || [];
  const done = [], miss = [];
  let lastTime = '';
  tracked.forEach(function(gid){
    const c = cells[gid];
    if (!c || !c.known) return;
    if (c.last && c.last > lastTime) lastTime = c.last;
    if (c.finished) done.push(nameOf[gid] || gid);
    else miss.push((nameOf[gid] || gid) +
      (c.unfinished_tasks ? '（漏 ' + c.unfinished_tasks + ' 项）' : ''));
  });
  const total = done.length + miss.length;
  const allDone = total > 0 && !miss.length;
  titleEl.textContent = allDone
    ? ('游戏日常：' + total + '/' + total + ' 全部完成')
    : ('游戏日常：' + miss.length + '/' + (total || tracked.length) + ' 个未完成');

  const lines = [];
  lines.push((row.label || '') + (row.weekday ? ' ' + row.weekday : '') +
             (lastTime ? ' · ' + lastTime : ''));
  miss.forEach(function(m){ lines.push('[未完成] ' + m); });
  if (done.length) lines.push('[已完成] ' + done.join('、'));
  lines.push(allDone ? '今天的日常全部完成，可以放心摆烂了。'
                     : '别拖到 23:59，记得把日常清了。');
  bodyEl.textContent = lines.join('\n');
}

function loadPushPreview(){
  fetch('/api/history?days=1').then(function(r){ return r.json(); })
    .then(renderPushPreview)
    .catch(function(){
      const t = document.getElementById('pv-title');
      if (t) t.textContent = '游戏日常：预览不可用';
      const b = document.getElementById('pv-body');
      if (b) b.textContent = '读取巡检数据失败，请稍后重试。';
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
  }).slice(-3).reverse();

  if (!rows.length) {
    box.innerHTML = '<div class="emptybox">还没有巡检记录，等第一次定时检查跑完后这里会有动态。</div>';
    return;
  }
  box.innerHTML = rows.map(function(row){
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
    const summary = miss.length
      ? '<span class="pill no">' + miss.length + ' 款未完成</span>'
      : '<span class="dim">全部完成</span>';
    const detail = miss.length
      ? '<span class="log-detail">未完成：' + escHtml(miss.join('、')) + '</span>' : '';
    return '<div class="log-row">' +
      '<span class="log-date">' + escHtml(row.label || '') +
        ' <span class="dim">' + escHtml(row.weekday || '') +
        (row.is_today ? ' · 今天' : '') + '</span></span>' +
      '<span class="log-time">' + escHtml(lastTime || '--:--') + '</span>' +
      summary + detail +
      '</div>';
  }).join('');
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

function updateKpiCompact(){
  if (!kpiBarEl || !kpiMainEl) return;
  kpiBarEl.classList.toggle('compact', kpiMainEl.scrollTop > 80);
}
if (kpiMainEl) kpiMainEl.addEventListener('scroll', updateKpiCompact, {passive:true});
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
  el.textContent = done + '/' + total;
  el.className = 'kpi-value value' + (known && done !== total ? ' warn' : '');
  /* 0 完成 = critical；部分完成 = warn；全部完成 = 无紧迫（紧迫度只看合并结果，不碰统计） */
  setUrgency('k-today', !known ? '' : (done === total ? '' : (done > 0 ? 'warn' : 'critical')));
  refreshKpiKeep();
  kpiSet('k-today-sub', !known ? '今日还没检查过，查询中…'
    : (done === total ? '今日全部完成' : (total - done) + ' 款未完成'));
}

/* 凭证健康：已绑定凭证的账号 / 启用账号 */
function renderKpiCred(d){
  const el = document.getElementById('k-cred');
  if (!el) return;
  const items = (d && d.items) || [];
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

/* 下次巡查：取 schedule 的 next_run，展示时刻 + 每 30 秒刷新倒计时 */
let kpiNext = null;

function renderKpiNext(d){
  const el = document.getElementById('k-next');
  if (!el) return;
  const raw = String((d && d.next_run) || '');
  const m = raw.match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/);
  if (m) {
    kpiNext = new Date(+m[1], +m[2] - 1, +m[3], +m[4].slice(0, 2), +m[4].slice(3, 5));
    el.textContent = m[4];
    updateKpiCountdown();
  } else {
        kpiNext = null;
    el.textContent = raw || '–';
    el.className = 'kpi-value value';
    kpiSet('k-next-sub', raw ? '' : '守护进程未运行');  }
}

function updateKpiCountdown(){
  const sub = document.getElementById('k-next-sub');
  if (!sub) return;
  if (!kpiNext) { sub.textContent = '守护进程未运行'; return; }
  const diff = Math.max(0, kpiNext.getTime() - Date.now());
  const min = Math.floor(diff / 60000);
  const h = Math.floor(min / 60), m = min % 60;
  sub.textContent = h > 0 ? (h + ' 小时 ' + m + ' 分后')
    : (m > 0 ? m + ' 分钟后' : '1 分钟内');
}
setInterval(updateKpiCountdown, 30000);

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
    html += '<div class="ruler-empty">还没有时间点，在下面添加</div>';
  }
  times.forEach(function(t){
    const m = String(t).match(/^(\d{1,2}):(\d{2})$/);
    if (!m) return;
    const pct = ((+m[1] * 60 + +m[2]) / 1440 * 100).toFixed(3);
    html += '<div class="ruler-dot" style="left:' + pct + '%" title="' + t + '">' +
            '<span>' + t + '</span></div>';
  });
  box.innerHTML = html;
}

/* ========================= 底部日志控制台 ========================= */
let consoleLoaded = false;

function renderConsole(d){
  const body = document.getElementById('console-body');
  const last = document.getElementById('console-last');
  if (!body) return;
  if (!d || d.ok === false) {
    body.innerHTML = '<span class="console-empty">' +
      escHtml((d && d.message) || '读取失败，请点「刷新」重试。') + '</span>';
    return;
  }
  const lines = d.lines || [];
  if (!lines.length) {
    body.innerHTML = '<span class="console-empty">还没有日志。</span>';
    if (last) last.textContent = '暂无日志';
    return;
  }
  body.innerHTML = lines.map(function(line){
    let cls = 'cline';
    if (/ERROR|CRITICAL/.test(line)) cls += ' c-err';
    else if (/WARNING/.test(line)) cls += ' c-warn';
    else if (/推送成功/.test(line)) cls += ' c-ok';
    const marked = escHtml(line).replace(
      /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/, '<span class="c-time">$1</span>');
    return '<div class="' + cls + '">' + marked + '</div>';
  }).join('');
  body.scrollTop = body.scrollHeight;
  if (last) last.textContent = lines[lines.length - 1].replace(/^\S+\s+/, '');
}

function loadConsole(){
  fetch('/api/logtail?n=200').then(function(r){ return r.json(); })
    .then(function(d){ consoleLoaded = true; renderConsole(d); })
    .catch(function(){
      const body = document.getElementById('console-body');
      if (body) body.innerHTML = '<span class="console-empty">读取日志失败，请点「刷新」。</span>';
    });
}

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
const hash = (location.hash || '').replace('#', '');
switchTab(PANEL_NAMES.indexOf(hash) >= 0 ? hash : 'history');
refreshNotice();
loadDevice();
loadAccounts();      // 用 config 里的真实巡检开关覆盖页面默认勾选
loadThresholds();
loadSchedule();      // 状态先拉一次，展开时不会闪空
loadAutostart();
